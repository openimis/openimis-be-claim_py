"""
DISCLAIMER:

This generates SYNTHETIC TEST DATA ONLY
All data (names, addresses, IDs, claims) is randomly generated
It does NOT represent real individuals or actual medical cases
Intended for development, testing, and performance evaluation
Should NOT be used in production with real patient data
Django Management Command for Bulk Synthetic Data Generation


TODO: to generate this data more realistic and diversion, based on HF, location, Claim, Family that looks more realistic approach
Usage:
    # Generate 1000 families with 4 members each, and 2 claims per insuree
    python manage.py seed_synthetic_health_data --families 1000 --members 4 --claims 2

    # Generate 50,000 families with 5 members each, no claims
    python manage.py seed_synthetic_health_data --families 50000 --members 5

    # Use a preset for medium size data generation and skip the confirmation prompt
    python manage.py seed_synthetic_health_data --preset medium --claims 1 --no-confirm
    #preset contains large, medium and small
"""

import gc
import random
import time
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone
from faker import Faker
# Local app imports
from claim.models import Claim, ClaimDetail, ClaimItem, ClaimService
from claim.validations import (
    REJECTION_REASON_CARE_TYPE, REJECTION_REASON_CATEGORY_LIMITATION,
    REJECTION_REASON_FAMILY, REJECTION_REASON_FREQUENCY_FAILURE,
    REJECTION_REASON_INVALID_CLAIM, REJECTION_REASON_INVALID_ITEM_OR_SERVICE,
    REJECTION_REASON_NOT_IN_PRICE_LIST, REJECTION_REASON_NO_COVERAGE,
    REJECTION_REASON_NO_PRODUCT_FOUND, REJECTION_REASON_QTY_OVER_LIMIT,
    REJECTION_REASON_TARGET_DATE, REJECTION_REASON_WAITING_PERIOD_FAIL,
)
from core.models import Officer
from insuree.models import (
    Family,
    FamilyType,
    Gender,
    Insuree,
    InsureePolicy,
    InsureeStatus,
)
from location.models import HealthFacility, Location
from medical.models import Diagnosis, Item, Service
from policy.models import Policy
from product.models import Product


# ---------------------------------------------------------------------------
# Synthetic data distribution parameters
#
# Tune these to make generated data match real-world proportions instead of
# a naive uniform random.choice(). Weights are relative, not percentages -
# random.choices() normalizes them, but the values below are chosen so the
# CARE_TYPE_WEIGHTS ratio already reads as 15-20% IPD / 80-85% OPD.
# ---------------------------------------------------------------------------
CARE_TYPE_WEIGHTS = {
    "IPD": 17,  # in-patient claims: ~15-20% of all claims
    "OPD": 83,  # out-patient claims: the remainder
}

# Claim status distribution. The remainder (1 - AUTOMATIC_REJECTION_RATE -
# MEDICAL_OFFICER_REJECTION_RATE) stays in the normal STATUS_ENTERED flow.
AUTOMATIC_REJECTION_RATE = 0.10
MEDICAL_OFFICER_REJECTION_RATE = 0.03

# Codes a real automatic validation run (see claim.validations.validate_claim)
# can produce before a claim ever reaches human review.
AUTOMATIC_REJECTION_REASONS = [
    REJECTION_REASON_INVALID_ITEM_OR_SERVICE, REJECTION_REASON_NOT_IN_PRICE_LIST,
    REJECTION_REASON_NO_PRODUCT_FOUND, REJECTION_REASON_CATEGORY_LIMITATION,
    REJECTION_REASON_FREQUENCY_FAILURE, REJECTION_REASON_FAMILY,
    REJECTION_REASON_TARGET_DATE, REJECTION_REASON_CARE_TYPE,
    REJECTION_REASON_QTY_OVER_LIMIT, REJECTION_REASON_WAITING_PERIOD_FAIL,
    REJECTION_REASON_NO_COVERAGE,
]
# REJECTION_REASON_INVALID_CLAIM (20) is the generic reason used when a
# Medical Officer manually rejects a claim during review.
MEDICAL_OFFICER_REJECTION_REASON = REJECTION_REASON_INVALID_CLAIM

# Processing-stage distribution, applied only to claims that are NOT rejected
# (see AUTOMATIC_REJECTION_RATE / MEDICAL_OFFICER_REJECTION_RATE above).
CLAIM_PROGRESS_WEIGHTS = {
    "VALUATED": 40,
    "PROCESSED": 30,
    "SUBMIT": 20,
    "ENTERED": 10,
}
# Random gaps (in days) between each stage's timestamp - dates are always
# sequential: date_from -> submit_stamp -> process_stamp.
SUBMIT_DELAY_RANGE_DAYS = (1, 15)  # date_from -> submit_stamp
PROCESS_DELAY_RANGE_DAYS = (1, 15)  # submit_stamp -> process_stamp, for claims stopping at PROCESSED
SUBMIT_TO_VALUATED_RANGE_DAYS = (1, 30)  # submit_stamp -> process_stamp, for claims reaching VALUATED


class PostgreSQLOptimizer:
    """Apply optional, session-level PostgreSQL bulk insert settings."""

    @staticmethod
    def optimize_for_bulk_insert():
        if connection.vendor != "postgresql":
            return False

        with connection.cursor() as cursor:
            cursor.execute("SET work_mem = '256MB'")
            cursor.execute("SET synchronous_commit = OFF")
        return True

    @staticmethod
    def reset_optimizations():
        if connection.vendor != "postgresql":
            return

        with connection.cursor() as cursor:
            cursor.execute("RESET work_mem")
            cursor.execute("RESET synchronous_commit")


class DataGenerator:
    """Generate realistic test data using Faker library"""

    def __init__(self, rng=None, seed=None):
        self.random = rng or random.Random(seed)
        self.faker = Faker()
        if seed is not None:
            self.faker.seed_instance(seed)
        self.chf_id_counter = self.random.randint(100000000, 200000000)

    def get_first_name(self):
        return self.faker.first_name()

    def get_last_name(self):
        return self.faker.last_name()

    def get_address(self):
        return self.faker.street_address() + ", " + self.faker.city()

    def get_unique_chf_id(self):
        self.chf_id_counter += 1
        return f"CHF{self.chf_id_counter:09d}"


class BulkInsureeGenerator:
    def __init__(
        self,
        batch_size=2000,
        stdout=None,
        seed=None,
        audit_user_id=-1,
        optimize_database=True,
    ):
        self.batch_size = batch_size
        self.random = random.Random(seed)
        self.data_gen = DataGenerator(rng=self.random, seed=seed)
        self.stdout = stdout
        self.audit_user_id = audit_user_id
        self.optimize_database = optimize_database

    def _uuid4(self):
        """Return a random UUID controlled by the command seed."""
        return str(uuid.UUID(int=self.random.getrandbits(128), version=4))

    def write(self, message):
        if self.stdout:
            self.stdout.write(message)
        else:
            print(message)

    def setup_reference_data(self, generate_claims=False):
        """Cache reference data to avoid repeated DB queries"""
        self.write("Loading and setting up reference data...")
        # validity_to__isnull=True selects only currently-valid rows (openIMIS VersionedModel convention):
        # a non-null validity_to means the row was superseded/deactivated and must not be reused for new claims.
        self.genders = list(
            Gender.objects.filter(validity_to__isnull=True).order_by("pk")
        ) or list(Gender.objects.bulk_create([Gender(code="M"), Gender(code="F")]))
        self.family_types = list(
            FamilyType.objects.filter(validity_to__isnull=True).order_by("pk")
        ) or list(
            FamilyType.objects.bulk_create([FamilyType(code="N", type="Nuclear")])
        )
        self.locations = list(
            Location.objects.filter(validity_to__isnull=True).order_by("pk")[:200]
        )
        self.health_facilities = list(
            HealthFacility.objects.filter(
                validity_to__isnull=True,
                status=HealthFacility.HealthFacilityStatus.ACTIVE,
            ).order_by("pk")[:100]
        )
        self.products = list(
            Product.objects.filter(validity_to__isnull=True).order_by("pk")[:20]
        )
        # TODO: Should we filter officers by district/region? Currently random assignment
        self.officers = list(
            Officer.objects.filter(validity_to__isnull=True).order_by("pk")[:50]
        )

        required_data = {
            "locations": self.locations, "health facilities": self.health_facilities,
            "products": self.products, "officers": self.officers
        }
        for name, data_list in required_data.items():
            if not data_list:
                raise CommandError(
                    f"No valid data found for {name}. Please populate reference data."
                )

        if generate_claims:
            self.diagnoses = list(
                Diagnosis.objects.filter(validity_to__isnull=True).order_by("pk")[:500]
            )
            self.items = list(
                Item.objects.filter(validity_to__isnull=True).order_by("pk")[:1000]
            )
            self.services = list(
                Service.objects.filter(validity_to__isnull=True).order_by("pk")[:500]
            )
            required_claim_data = {
                "diagnoses": self.diagnoses,
                "medical items": self.items,
                "medical services": self.services,
            }
            for name, data_list in required_claim_data.items():
                if not data_list:
                    raise CommandError(
                        "To generate claims, please populate valid reference data "
                        f"for {name}."
                    )
        self.write("Reference data loaded.")

    def _bulk_create_with_progress(self, model_class, objects, description):
        total = len(objects)
        if total == 0:
            return []
        self.write(f"Creating {total:,} {description}...")
        created_objects = []
        for i in range(0, total, self.batch_size):
            batch = objects[i:i + self.batch_size]
            with transaction.atomic():
                created_batch = model_class.objects.bulk_create(batch, batch_size=self.batch_size)
                created_objects.extend(created_batch)
            progress = (len(created_objects) / total) * 100
            self.write(f"  Progress: {len(created_objects):,}/{total:,} ({progress:.1f}%) {description}")
            gc.collect()
        return created_objects

    def _generate_insurees(self, total_insuree_count):
        self.write(f"\n=== Generating {total_insuree_count:,} Insurees (unassigned) ===")
        insurees_to_create = []
        for _ in range(total_insuree_count):
            insuree = Insuree(
                uuid=self._uuid4(), chf_id=self.data_gen.get_unique_chf_id(),
                last_name=self.data_gen.get_last_name(), other_names=self.data_gen.get_first_name(),
                gender=self.random.choice(self.genders),
                dob=date.today() - timedelta(days=self.random.randint(1, 30000)),
                head=False,
                card_issued=self.random.choice([True, False]),
                audit_user_id=self.audit_user_id,
                validity_from=timezone.now(),
                status=InsureeStatus.ACTIVE,
                health_facility=self.random.choice(self.health_facilities),
            )
            insurees_to_create.append(insuree)
        return self._bulk_create_with_progress(Insuree, insurees_to_create, "insurees")

    def _generate_families_and_link_members(self, all_insurees, num_families, num_members):
        self.write(f"\n=== Generating {num_families:,} Families and linking {num_members} members to each ===")
        if len(all_insurees) < num_families * num_members:
            raise CommandError("Not enough insurees to form the requested families.")

        insurees_pool = list(all_insurees)
        self.random.shuffle(insurees_pool)

        heads_pool, members_pool = insurees_pool[:num_families], insurees_pool[num_families:]
        families_to_create = [
            Family(
                uuid=self._uuid4(),
                head_insuree=head,
                location=self.random.choice(self.locations),
                family_type=self.random.choice(self.family_types),
                address=self.data_gen.get_address(),
                audit_user_id=self.audit_user_id,
                validity_from=timezone.now(),
            )
            for head in heads_pool
        ]
        created_families = self._bulk_create_with_progress(Family, families_to_create, "families")

        self.write("Linking family members...")
        insurees_to_update = []
        for family in created_families:
            head = family.head_insuree
            head.family, head.head = family, True
            insurees_to_update.append(head)
            for _ in range(num_members - 1):
                if not members_pool:
                    break
                member = members_pool.pop()
                member.family = family
                insurees_to_update.append(member)

        self.write(f"Bulk updating {len(insurees_to_update):,} insurees with family links...")
        Insuree.objects.bulk_update(insurees_to_update, ['family', 'head'], batch_size=self.batch_size)
        return created_families

    def _generate_policies(self, families):
        self.write(f"\n=== Generating policies for {len(families):,} families ===")
        policies_to_create = []
        for f in families:
            enroll_date = date.today() - timedelta(days=self.random.randint(90, 730))
            # TODO: Add support for different policy stages (not just 'N')
            policy = Policy(
                uuid=self._uuid4(), stage='N', status=Policy.STATUS_ACTIVE,
                value=Decimal(self.random.randint(10000, 200000)) / 100,
                family=f, enroll_date=enroll_date, start_date=enroll_date, effective_date=enroll_date,
                expiry_date=enroll_date + timedelta(days=365),
                product=self.random.choice(self.products),
                officer=self.random.choice(self.officers),
                audit_user_id=self.audit_user_id,
            )
            policies_to_create.append(policy)
        return self._bulk_create_with_progress(Policy, policies_to_create, "policies")

    def _generate_insuree_policies(self, policies):
        self.write("\n=== Generating InsureePolicy relationships ===")
        family_ids = [p.family_id for p in policies]
        insurees = Insuree.objects.filter(family_id__in=family_ids)
        insurees_by_family = {fid: [] for fid in family_ids}
        for insuree in insurees:
            insurees_by_family[insuree.family_id].append(insuree)

        insuree_policies = [
            InsureePolicy(
                insuree=insuree,
                policy=policy,
                enrollment_date=policy.enroll_date,
                start_date=policy.start_date,
                effective_date=policy.effective_date,
                expiry_date=policy.expiry_date,
                audit_user_id=self.audit_user_id,
            )
            for policy in policies
            for insuree in insurees_by_family.get(policy.family_id, [])
        ]
        self._bulk_create_with_progress(InsureePolicy, insuree_policies, "InsureePolicy relationships")
        return len(insuree_policies)

    def _get_claim_date_range(self, insuree, policy_by_family):
        """Return (earliest, latest) valid dates for a claim on this insuree.

        Claims must fall within the insuree's policy coverage period so that
        we never claim before a policy exists. If no policy is found (e.g. a
        fraudulent claim attempt with no active policy), fall back to a
        2-year window ending today - such claims are expected to be rejected
        downstream.
        """
        policy = policy_by_family.get(insuree.family_id)
        if policy is None:
            return date.today() - timedelta(days=730), date.today()
        earliest = policy.effective_date
        latest = min(policy.expiry_date, date.today())
        if earliest > latest:
            raise CommandError(
                f"Policy {policy.pk} has no coverage period on or before today."
            )
        return earliest, latest

    def _get_claim_status_fields(self):
        """Randomly assign a claim's terminal status per configured rejection rates.

        Returns (status, review_status, rejection_reason, audit_user_id_review).
        """
        roll = self.random.random()
        if roll < AUTOMATIC_REJECTION_RATE:
            # Rejected before ever reaching a human reviewer.
            return (
                Claim.STATUS_REJECTED,
                Claim.REVIEW_IDLE,
                self.random.choice(AUTOMATIC_REJECTION_REASONS),
                None,
            )
        if roll < AUTOMATIC_REJECTION_RATE + MEDICAL_OFFICER_REJECTION_RATE:
            # Reviewed and rejected by a Medical Officer.
            return (
                Claim.STATUS_REJECTED,
                Claim.REVIEW_DELIVERED,
                MEDICAL_OFFICER_REJECTION_REASON,
                self.audit_user_id,
            )
        return Claim.STATUS_ENTERED, Claim.REVIEW_IDLE, 0, None

    def _select_claim_health_facility(self, insuree):
        """Return a currently active health facility for a generated claim."""
        if not self.health_facilities:
            raise CommandError(
                "Cannot generate claims without an active health facility."
            )

        insuree_health_facility = getattr(insuree, "health_facility", None)
        active_health_facility_ids = {
            health_facility.pk for health_facility in self.health_facilities
        }
        if (
            insuree_health_facility is not None
            and insuree_health_facility.pk in active_health_facility_ids
        ):
            return insuree_health_facility

        return self.random.choice(self.health_facilities)

    def _get_claim_progress_fields(self, claim_date, as_of_date=None):
        """Randomly advance a non-rejected claim through Submit -> Processed -> Valuated.

        Dates are sequential and start from claim_date (date_from). There is no
        dedicated "date valuated" column on Claim, so - mirroring
        claim.services.set_claim_processed_or_valuated - process_stamp is reused
        as the valuation timestamp when a claim reaches VALUATED.

        Returns (status, submit_stamp, process_stamp, date_processed,
        audit_user_id_submit, audit_user_id_process).
        """
        progress = self.random.choices(
            list(CLAIM_PROGRESS_WEIGHTS.keys()), weights=list(CLAIM_PROGRESS_WEIGHTS.values())
        )[0]
        as_of_date = as_of_date or timezone.now().date()
        available_days = (as_of_date - claim_date).days
        if progress == "ENTERED" or available_days < SUBMIT_DELAY_RANGE_DAYS[0]:
            return Claim.STATUS_ENTERED, None, None, None, None, None

        submit_delay = self.random.randint(
            SUBMIT_DELAY_RANGE_DAYS[0],
            min(SUBMIT_DELAY_RANGE_DAYS[1], available_days),
        )
        submit_stamp = self._date_to_timestamp(claim_date + timedelta(days=submit_delay))
        if progress == "SUBMIT":
            return (
                Claim.STATUS_CHECKED,
                submit_stamp,
                None,
                None,
                self.audit_user_id,
                None,
            )

        remaining_days = (as_of_date - submit_stamp.date()).days
        if remaining_days < 1:
            return (
                Claim.STATUS_CHECKED,
                submit_stamp,
                None,
                None,
                self.audit_user_id,
                None,
            )

        if progress == "PROCESSED":
            process_delay = self.random.randint(
                PROCESS_DELAY_RANGE_DAYS[0],
                min(PROCESS_DELAY_RANGE_DAYS[1], remaining_days),
            )
            process_stamp = submit_stamp + timedelta(days=process_delay)
            return (
                Claim.STATUS_PROCESSED,
                submit_stamp,
                process_stamp,
                process_stamp.date(),
                self.audit_user_id,
                self.audit_user_id,
            )

        # VALUATED
        process_delay = self.random.randint(
            SUBMIT_TO_VALUATED_RANGE_DAYS[0],
            min(SUBMIT_TO_VALUATED_RANGE_DAYS[1], remaining_days),
        )
        process_stamp = submit_stamp + timedelta(days=process_delay)
        return (
            Claim.STATUS_VALUATED,
            submit_stamp,
            process_stamp,
            None,
            self.audit_user_id,
            self.audit_user_id,
        )

    @staticmethod
    def _date_to_timestamp(value):
        stamp = datetime.combine(value, datetime.min.time())
        if timezone.is_aware(timezone.now()):
            return timezone.make_aware(stamp, timezone.get_current_timezone())
        return stamp

    def _generate_claims(self, all_insurees, num_claims_per_insuree, policy_by_family):
        self.write(f"\n=== Generating {num_claims_per_insuree} claims for each of {len(all_insurees):,} insurees ===")
        claims_to_create = []
        for insuree in all_insurees:
            earliest, latest = self._get_claim_date_range(insuree, policy_by_family)
            health_facility = self._select_claim_health_facility(insuree)
            for _ in range(num_claims_per_insuree):
                # IPD (in-patient) claims require a stay, so date_to is a few days after date_from.
                # OPD (out-patient) claims are same-day and leave date_to unset.
                care_type = self.random.choices(
                    list(CARE_TYPE_WEIGHTS.keys()), weights=list(CARE_TYPE_WEIGHTS.values())
                )[0]
                span_days = (latest - earliest).days
                if care_type == "IPD" and span_days >= 2:
                    claim_date = earliest + timedelta(
                        days=self.random.randint(0, span_days - 2)
                    )
                    max_stay = min(5, (latest - claim_date).days)
                    date_to = claim_date + timedelta(
                        days=self.random.randint(2, max_stay)
                    )
                else:
                    care_type = "OPD"
                    claim_date = earliest + timedelta(
                        days=self.random.randint(0, span_days)
                    )
                    date_to = None

                date_claimed = date_to or claim_date
                # Visit type: "O" ordinary, "E" emergency, "R" referral (see validations.visit_type_field).
                visit_type = self.random.choice(["O", "E", "R"])
                status, review_status, rejection_reason, audit_user_id_review = self._get_claim_status_fields()
                submit_stamp = process_stamp = date_processed = None
                audit_user_id_submit = audit_user_id_process = None
                if status != Claim.STATUS_REJECTED:
                    (status, submit_stamp, process_stamp, date_processed,
                     audit_user_id_submit, audit_user_id_process) = self._get_claim_progress_fields(date_claimed)
                # TODO: we need to find data diversity of claims
                claim = Claim(
                    uuid=self._uuid4(), insuree=insuree, code=f"BULK-{self._uuid4()}",
                    date_from=claim_date, date_to=date_to, care_type=care_type, visit_type=visit_type,
                    date_claimed=date_claimed, status=status, review_status=review_status,
                    rejection_reason=rejection_reason, audit_user_id_review=audit_user_id_review,
                    submit_stamp=submit_stamp, process_stamp=process_stamp, date_processed=date_processed,
                    audit_user_id_submit=audit_user_id_submit, audit_user_id_process=audit_user_id_process,
                    health_facility=health_facility,
                    icd=self.random.choice(self.diagnoses),
                    audit_user_id=self.audit_user_id,
                    claimed=0,
                )
                claims_to_create.append(claim)

        created_claims = self._bulk_create_with_progress(Claim, claims_to_create, "claims")

        claim_items_to_create, claim_services_to_create = [], []
        claim_totals = {c.id: Decimal(0) for c in created_claims}

        for claim in created_claims:
            # Rejected claims (automatic or Medical Officer) must have their items/services
            # marked as rejected too, with qty_approved cleared - mirrors claim.validations
            # automatic checks (see validate_assign_prod_elt: qty_approved=0 on rejection).
            is_rejected = claim.status == Claim.STATUS_REJECTED
            detail_status = ClaimDetail.STATUS_REJECTED if is_rejected else ClaimDetail.STATUS_PASSED
            detail_rejection_reason = claim.rejection_reason if is_rejected else None
            detail_qty_approved = Decimal(0) if is_rejected else None

            # TODO: Make items/services count configurable instead of random, make it more realistic in TODO:
            for _ in range(self.random.randint(1, 4)):  # 1-4 items per claim
                price = Decimal(self.random.randint(500, 15000)) / 100
                qty = Decimal(self.random.randint(1, 5))
                claim_items_to_create.append(ClaimItem(
                    claim=claim, item=self.random.choice(self.items), status=detail_status, qty_provided=qty,
                    qty_approved=detail_qty_approved, rejection_reason=detail_rejection_reason,
                    price_asked=price, audit_user_id=self.audit_user_id, availability=True
                ))
                claim_totals[claim.id] += price * qty

            for _ in range(self.random.randint(1, 3)):  # 1-3 services per claim
                price = Decimal(self.random.randint(5000, 50000)) / 100
                qty = 1
                claim_services_to_create.append(ClaimService(
                    claim=claim, service=self.random.choice(self.services), status=detail_status, qty_provided=qty,
                    qty_approved=detail_qty_approved, rejection_reason=detail_rejection_reason,
                    price_asked=price, audit_user_id=self.audit_user_id
                ))
                claim_totals[claim.id] += price * qty

        self._bulk_create_with_progress(ClaimItem, claim_items_to_create, "claim items")
        self._bulk_create_with_progress(ClaimService, claim_services_to_create, "claim services")

        self.write(f"Bulk updating {len(created_claims):,} claims with calculated totals...")
        for claim in created_claims:
            claim.claimed = claim_totals[claim.id]
        Claim.objects.bulk_update(created_claims, ['claimed'], batch_size=self.batch_size)

        return len(created_claims), len(claim_items_to_create), len(claim_services_to_create)

    def generate_bulk_data(self, num_families, num_members, num_claims):
        start_time = time.time()
        self.setup_reference_data(generate_claims=num_claims > 0)

        self.write("=" * 80 + f"\nBULK DATA GENERATION STARTED\nFamilies: {num_families:,}, Members/Family: {num_members}, Claims/Insuree: {num_claims}\n" + "=" * 80)
        optimizations_applied = False
        if self.optimize_database:
            optimizations_applied = PostgreSQLOptimizer.optimize_for_bulk_insert()

        results = {}
        try:
            total_insuree_count = num_families * num_members
            all_insurees = self._generate_insurees(total_insuree_count)
            families = self._generate_families_and_link_members(all_insurees, num_families, num_members)
            policies = self._generate_policies(families)
            insuree_policies = self._generate_insuree_policies(policies)

            results = {'families': len(families), 'insurees': len(all_insurees),
                       'policies': len(policies), 'insuree_policies': insuree_policies}

            if num_claims > 0:
                policy_by_family = {p.family_id: p for p in policies}
                claims, items, services = self._generate_claims(all_insurees, num_claims, policy_by_family)
                results.update({'claims': claims, 'claim_items': items, 'claim_services': services})

        finally:
            if optimizations_applied:
                PostgreSQLOptimizer.reset_optimizations()
                self.write("\nDatabase optimizations have been reset.")

        duration = time.time() - start_time
        results['duration'] = duration

        self.write("\n" + "=" * 80 + "\nBULK SYNTHETIC DATA GENERATION COMPLETED SUCCESSFULLY!\n" + "=" * 80)
        for key, value in results.items():
            self.write(f"{key.replace('_', ' ').title():<25} {value:>12,.2f}" if isinstance(value, float) else f"{key.replace('_', ' ').title():<25} {value:>12,}")
        return results


class Command(BaseCommand):
    help = 'Generate bulk insuree, policy, and claim test data for the project.'

    DEFAULT_CONFIG = {"families": 1000, "members": 4, "claims": 0}
    PRESETS = {
        "small": {"families": 1000, "members": 3, "claims": 1},
        "medium": {"families": 10000, "members": 4, "claims": 2},
        "large": {"families": 50000, "members": 5, "claims": 2},
    }

    def add_arguments(self, parser):
        parser.add_argument('--preset', choices=['small', 'medium', 'large'], help='Use preset configuration.')
        parser.add_argument('--families', type=int, help='Number of families to generate.')
        parser.add_argument('--members', type=int, help='Fixed number of members per family.')
        parser.add_argument('--claims', type=int, help='Number of claims to generate per insuree. If 0, no claims are created.')
        parser.add_argument('--batch-size', type=int, default=2000, help='Batch size for bulk operations.')
        parser.add_argument(
            '--seed',
            type=int,
            help='Seed for repeatable random and Faker values.',
        )
        parser.add_argument(
            '--audit-user-id',
            type=int,
            default=-1,
            help='Audit user id stored on generated records (default: -1).',
        )
        parser.add_argument(
            '--no-db-optimization',
            action='store_true',
            help='Do not apply PostgreSQL session settings for bulk inserts.',
        )
        parser.add_argument('--no-confirm', action='store_true', help='Skip confirmation prompt.')

    @classmethod
    def resolve_config(cls, options):
        config = dict(cls.DEFAULT_CONFIG)
        if options.get('preset'):
            config.update(cls.PRESETS[options['preset']])

        for key in ('families', 'members', 'claims'):
            if options.get(key) is not None:
                config[key] = options[key]
        return config

    def handle(self, *args, **options):
        config = self.resolve_config(options)
        if options['preset']:
            self.stdout.write(self.style.SUCCESS(f"Using preset '{options['preset']}' configuration"))

        if config['families'] < 1:
            raise CommandError("Number of families must be at least 1.")
        if config['members'] < 1:
            raise CommandError("Number of members must be at least 1.")
        if config['claims'] < 0:
            raise CommandError("Number of claims cannot be negative.")

        batch_size = options['batch_size']
        if batch_size < 1:
            raise CommandError("Batch size must be at least 1.")
        if config['families'] > 10000 and batch_size < 4000:
            batch_size = 5000
            self.stdout.write(self.style.WARNING(f"Auto-adjusted batch size to {batch_size} for large dataset"))

        total_insurees = config['families'] * config['members']
        total_claims = total_insurees * config['claims']

        self.stdout.write(
            "\n"
            + "=" * 60
            + "\n"
            + self.style.SUCCESS("DJANGO BULK SYNTHETIC DATA GENERATOR - OPENIMIS")
            + "\n"
            + "=" * 60
        )
        self.stdout.write(f"Families to create: {config['families']:,}\nMembers per family: {config['members']}\nTotal insurees: {total_insurees:,}")
        if config['claims'] > 0:
            self.stdout.write(
                f"Claims per insuree: {config['claims']}\nTotal claims: {total_claims:,}"
            )

        if not options['no_confirm']:
            if input(f"\n{self.style.WARNING('WARNING:')} This will create a large amount of test data. Continue? (yes/no): ").lower() not in ['yes', 'y']:
                self.stdout.write(self.style.ERROR("Operation cancelled."))
                return

        with transaction.atomic():
            generator = BulkInsureeGenerator(
                batch_size=batch_size,
                stdout=self.stdout,
                seed=options['seed'],
                audit_user_id=options['audit_user_id'],
                optimize_database=not options['no_db_optimization'],
            )
            results = generator.generate_bulk_data(config['families'], config['members'], config['claims'])

        self.stdout.write(self.style.SUCCESS(f"\n Successfully generated synthetic data in {results['duration']:.2f} seconds!"))
