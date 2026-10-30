"""
DISCLAIMER:

This generates SYNTHETIC TEST DATA ONLY
All data (names, addresses, IDs, claims) is randomly generated
It does NOT represent real individuals or actual medical cases
Intended for development, testing, and performance evaluation
Should NOT be used in production with real patient data
Django Management Command for Bulk Synthetic Data Generation


Usage:
    # Generate 1000 families with 4 members each, and 2 claims per insuree
    python manage.py seed_synthetic_health_data --families 1000 --members 4 --claims 2

    # Generate 50,000 families with 5 members each, no claims
    python manage.py seed_synthetic_health_data --families 50000 --members 5

    # Use a preset for medium size data generation and skip the confirmation prompt
    python manage.py seed_synthetic_health_data --preset medium --claims 1 --no-confirm

    # Use weighted distributions and realistic claim outcomes for Nepal
    python manage.py seed_synthetic_health_data --country nepal --no-confirm
"""

import gc
import json
import math
import random
import time
import uuid
from contextlib import nullcontext
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone
from faker import Faker
# Local app imports
from claim.models import Claim, ClaimDetail, ClaimItem, ClaimService
from claim.management.commands.synthetic_data_config import (
    CLAIM_OUTCOME_WEIGHTS,
    ITEM_REJECTION_RATE,
    PRICE_REDUCTION_RANGE,
    REALISTIC_COUNTRY_PRESETS,
    SERVICE_REJECTION_RATE,
    validate_realistic_config,
)
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
        claim_chunk_size=2000,
    ):
        self.batch_size = batch_size
        self.claim_chunk_size = claim_chunk_size
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

    def setup_reference_data(
        self, generate_claims=False, population_weighted_locations=False
    ):
        """Cache reference data to avoid repeated DB queries"""
        self.write("Loading and setting up reference data...")
        self.genders = list(
            Gender.objects.order_by("pk")
        ) or list(Gender.objects.bulk_create([Gender(code="M"), Gender(code="F")]))
        self.family_types = list(
            FamilyType.objects.order_by("pk")
        ) or list(
            FamilyType.objects.bulk_create([FamilyType(code="N", type="Nuclear")])
        )
        # A non-null validity_to means a versioned row was superseded or
        # deactivated and must not be reused for newly generated data.
        locations = Location.objects.filter(validity_to__isnull=True).order_by("pk")
        if population_weighted_locations:
            self.locations = list(locations.filter(type="V"))
            if not self.locations:
                self.locations = list(locations[:200])
        else:
            self.locations = list(locations[:200])
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
        return created_objects

    def _generate_family_sizes(self, num_families, num_members, family_size_weights):
        if not family_size_weights:
            return [num_members] * num_families

        family_sizes = list(family_size_weights)
        return self.random.choices(
            family_sizes,
            weights=[family_size_weights[size] for size in family_sizes],
            k=num_families,
        )

    def _select_gender(self, gender_weights):
        if not gender_weights:
            return self.random.choice(self.genders)

        genders_by_code = {gender.code: gender for gender in self.genders}
        missing_codes = set(gender_weights) - set(genders_by_code)
        if missing_codes:
            raise CommandError(
                "Missing active gender reference data for: "
                + ", ".join(sorted(missing_codes))
            )
        codes = list(gender_weights)
        code = self.random.choices(
            codes, weights=[gender_weights[value] for value in codes]
        )[0]
        return genders_by_code[code]

    def _select_family_location(self, location_weighting):
        if location_weighting != "population":
            return self.random.choice(self.locations)

        # Families are normally attached to the lowest configured location
        # level. Fall back to all locations for installations without villages.
        candidates = [location for location in self.locations if location.type == "V"]
        candidates = candidates or self.locations
        weights = []
        for location in candidates:
            population = (location.male_population or 0) + (
                location.female_population or 0
            ) + (location.other_population or 0)
            weights.append(population or location.families or 1)
        return self.random.choices(candidates, weights=weights)[0]

    def _generate_insurees(self, total_insuree_count, gender_weights=None):
        self.write(f"\n=== Generating {total_insuree_count:,} Insurees (unassigned) ===")
        insurees_to_create = []
        for _ in range(total_insuree_count):
            insuree = Insuree(
                uuid=self._uuid4(), chf_id=self.data_gen.get_unique_chf_id(),
                last_name=self.data_gen.get_last_name(), other_names=self.data_gen.get_first_name(),
                gender=self._select_gender(gender_weights),
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

    def _generate_families_and_link_members(
        self,
        all_insurees,
        family_sizes,
        location_weighting=None,
    ):
        num_families = len(family_sizes)
        self.write(
            f"\n=== Generating {num_families:,} Families and linking "
            f"{sum(family_sizes):,} members ==="
        )
        if len(all_insurees) < sum(family_sizes):
            raise CommandError("Not enough insurees to form the requested families.")

        insurees_pool = list(all_insurees)
        self.random.shuffle(insurees_pool)

        heads_pool, members_pool = insurees_pool[:num_families], insurees_pool[num_families:]
        families_to_create = [
            Family(
                uuid=self._uuid4(),
                head_insuree=head,
                location=self._select_family_location(location_weighting),
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
        for family, family_size in zip(created_families, family_sizes):
            head = family.head_insuree
            head.family, head.head = family, True
            insurees_to_update.append(head)
            for _ in range(family_size - 1):
                if not members_pool:
                    break
                member = members_pool.pop()
                member.family = family
                insurees_to_update.append(member)

        self.write(f"Bulk updating {len(insurees_to_update):,} insurees with family links...")
        Insuree.objects.bulk_update(insurees_to_update, ['family', 'head'], batch_size=self.batch_size)
        return created_families

    def _generate_policies(self, families, round_robin_products=False):
        self.write(f"\n=== Generating policies for {len(families):,} families ===")
        products = list(self.products)
        if round_robin_products:
            self.random.shuffle(products)
        policies_to_create = []
        for index, f in enumerate(families):
            enroll_date = date.today() - timedelta(days=self.random.randint(90, 730))
            # TODO: Add support for different policy stages (not just 'N')
            policy = Policy(
                uuid=self._uuid4(), stage='N', status=Policy.STATUS_ACTIVE,
                value=Decimal(self.random.randint(10000, 200000)) / 100,
                family=f, enroll_date=enroll_date, start_date=enroll_date, effective_date=enroll_date,
                expiry_date=enroll_date + timedelta(days=365),
                product=(
                    products[index % len(products)]
                    if round_robin_products
                    else self.random.choice(products)
                ),
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

    def _get_claim_progress_fields(self, claim_date, as_of_date=None, progress=None):
        """Randomly advance a non-rejected claim through Submit -> Processed -> Valuated.

        Dates are sequential and start from claim_date (date_from). There is no
        dedicated "date valuated" column on Claim, so - mirroring
        claim.services.set_claim_processed_or_valuated - process_stamp is reused
        as the valuation timestamp when a claim reaches VALUATED.

        Returns (status, submit_stamp, process_stamp, date_processed,
        audit_user_id_submit, audit_user_id_process).
        """
        if progress is None:
            progress = self.random.choices(
                list(CLAIM_PROGRESS_WEIGHTS.keys()),
                weights=list(CLAIM_PROGRESS_WEIGHTS.values()),
            )[0]
        as_of_date = as_of_date or timezone.now().date()
        available_days = (as_of_date - claim_date).days
        if progress == "ENTERED" or available_days < SUBMIT_DELAY_RANGE_DAYS[0]:
            return Claim.STATUS_ENTERED, None, None, None, None, None

        max_submit_delay = min(SUBMIT_DELAY_RANGE_DAYS[1], available_days)
        if progress in ("PROCESSED", "VALUATED") and available_days >= 2:
            max_submit_delay = min(max_submit_delay, available_days - 1)
        submit_delay = self.random.randint(
            SUBMIT_DELAY_RANGE_DAYS[0],
            max_submit_delay,
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

    def _get_claim_count(self, fixed_count, average_count):
        if average_count is None:
            return fixed_count

        # Knuth's algorithm produces a Poisson-distributed utilization count.
        threshold = math.exp(-average_count)
        count = 0
        probability = 1.0
        while probability > threshold:
            count += 1
            probability *= self.random.random()
        return count - 1

    def _get_realistic_claim_outcome(self):
        outcomes = list(CLAIM_OUTCOME_WEIGHTS)
        return self.random.choices(
            outcomes,
            weights=[CLAIM_OUTCOME_WEIGHTS[outcome] for outcome in outcomes],
        )[0]

    def _get_realistic_detail_fields(
        self,
        outcome,
        price,
        quantity,
        rejection_rate,
        force_rejected=False,
        force_approved=False,
        claim_rejection_reason=None,
    ):
        rejected = outcome == "REJECTED"
        if outcome == "PARTIAL":
            rejected = force_rejected or (
                not force_approved and self.random.random() < rejection_rate
            )

        if rejected:
            return {
                "status": ClaimDetail.STATUS_REJECTED,
                "qty_approved": Decimal(0),
                "price_adjusted": Decimal(0),
                "price_approved": Decimal(0),
                "price_valuated": Decimal(0),
                "rejection_reason": (
                    claim_rejection_reason
                    or self.random.choice(AUTOMATIC_REJECTION_REASONS)
                ),
                "approved_amount": Decimal(0),
            }

        approved_price = price
        if outcome == "PARTIAL":
            reduction_min = round(PRICE_REDUCTION_RANGE[0] * 100)
            reduction_max = round(PRICE_REDUCTION_RANGE[1] * 100)
            approved_price = (
                price * Decimal(self.random.randint(reduction_min, reduction_max)) / 100
            ).quantize(Decimal("0.01"))

        return {
            "status": ClaimDetail.STATUS_PASSED,
            "qty_approved": quantity,
            "price_adjusted": approved_price,
            "price_approved": approved_price,
            "price_valuated": approved_price,
            "rejection_reason": None,
            "approved_amount": approved_price * quantity,
        }

    def _build_claim_batch(
        self,
        insurees,
        num_claims_per_insuree,
        policy_by_family,
        claims_per_insuree=None,
        realistic_claim_outcomes=False,
    ):
        claims_to_create = []
        claim_items_to_create = []
        claim_services_to_create = []
        for insuree in insurees:
            earliest, latest = self._get_claim_date_range(insuree, policy_by_family)
            health_facility = self._select_claim_health_facility(insuree)
            claim_count = self._get_claim_count(
                num_claims_per_insuree, claims_per_insuree
            )
            for _ in range(claim_count):
                claim_period_end = latest
                if realistic_claim_outcomes:
                    terminal_cutoff = date.today() - timedelta(days=2)
                    if earliest <= terminal_cutoff:
                        claim_period_end = min(latest, terminal_cutoff)
                # IPD (in-patient) claims require a stay, so date_to is a few days after date_from.
                # OPD (out-patient) claims are same-day and leave date_to unset.
                care_type = self.random.choices(
                    list(CARE_TYPE_WEIGHTS.keys()), weights=list(CARE_TYPE_WEIGHTS.values())
                )[0]
                span_days = (claim_period_end - earliest).days
                if care_type == "IPD" and span_days >= 2:
                    claim_date = earliest + timedelta(
                        days=self.random.randint(0, span_days - 2)
                    )
                    max_stay = min(5, (claim_period_end - claim_date).days)
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
                synthetic_outcome = None
                if realistic_claim_outcomes:
                    synthetic_outcome = self._get_realistic_claim_outcome()
                    if synthetic_outcome == "REJECTED":
                        status = Claim.STATUS_REJECTED
                        review_status = Claim.REVIEW_DELIVERED
                        rejection_reason = MEDICAL_OFFICER_REJECTION_REASON
                        audit_user_id_review = self.audit_user_id
                    else:
                        status = Claim.STATUS_ENTERED
                        review_status = Claim.REVIEW_DELIVERED
                        rejection_reason = 0
                        audit_user_id_review = self.audit_user_id
                else:
                    (
                        status,
                        review_status,
                        rejection_reason,
                        audit_user_id_review,
                    ) = self._get_claim_status_fields()
                submit_stamp = process_stamp = date_processed = None
                audit_user_id_submit = audit_user_id_process = None
                if status != Claim.STATUS_REJECTED:
                    progress = "VALUATED" if realistic_claim_outcomes else None
                    (
                        status,
                        submit_stamp,
                        process_stamp,
                        date_processed,
                        audit_user_id_submit,
                        audit_user_id_process,
                    ) = self._get_claim_progress_fields(
                        date_claimed, progress=progress
                    )
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
                claim._synthetic_outcome = synthetic_outcome
                item_details, service_details = self._build_claim_details(
                    claim,
                    policy_by_family,
                    realistic_claim_outcomes,
                )
                claims_to_create.append(claim)
                claim_items_to_create.extend(item_details)
                claim_services_to_create.extend(service_details)

        return claims_to_create, claim_items_to_create, claim_services_to_create

    def _build_claim_details(
        self,
        claim,
        policy_by_family,
        realistic_claim_outcomes,
    ):
        policy = policy_by_family.get(claim.insuree.family_id)
        product = policy.product if policy else None
        synthetic_outcome = getattr(claim, "_synthetic_outcome", None)
        is_rejected = claim.status == Claim.STATUS_REJECTED
        default_status = (
            ClaimDetail.STATUS_REJECTED
            if is_rejected
            else ClaimDetail.STATUS_PASSED
        )
        default_rejection_reason = claim.rejection_reason if is_rejected else None
        default_qty_approved = Decimal(0) if is_rejected else None
        claimed_total = Decimal(0)
        approved_total = Decimal(0)
        item_details = []
        service_details = []

        for item_index in range(self.random.randint(1, 4)):
            price = Decimal(self.random.randint(500, 15000)) / 100
            quantity = Decimal(self.random.randint(1, 5))
            detail_fields = self._get_claim_detail_fields(
                synthetic_outcome,
                price,
                quantity,
                ITEM_REJECTION_RATE,
                default_status,
                default_qty_approved,
                default_rejection_reason,
                force_rejected=(
                    synthetic_outcome == "PARTIAL" and item_index == 0
                ),
            )
            item_details.append(
                ClaimItem(
                    claim=claim,
                    item=self.random.choice(self.items),
                    product=product,
                    policy=policy,
                    qty_provided=quantity,
                    price_asked=price,
                    audit_user_id=self.audit_user_id,
                    availability=True,
                    **detail_fields["model_fields"],
                )
            )
            claimed_total += price * quantity
            approved_total += detail_fields["approved_amount"]

        for service_index in range(self.random.randint(1, 3)):
            price = Decimal(self.random.randint(5000, 50000)) / 100
            quantity = Decimal(1)
            detail_fields = self._get_claim_detail_fields(
                synthetic_outcome,
                price,
                quantity,
                SERVICE_REJECTION_RATE,
                default_status,
                default_qty_approved,
                default_rejection_reason,
                force_approved=(
                    synthetic_outcome == "PARTIAL" and service_index == 0
                ),
            )
            service_details.append(
                ClaimService(
                    claim=claim,
                    service=self.random.choice(self.services),
                    product=product,
                    policy=policy,
                    qty_provided=quantity,
                    price_asked=price,
                    audit_user_id=self.audit_user_id,
                    **detail_fields["model_fields"],
                )
            )
            claimed_total += price * quantity
            approved_total += detail_fields["approved_amount"]

        claim.claimed = claimed_total
        if realistic_claim_outcomes:
            claim.approved = approved_total
            claim.valuated = approved_total
        return item_details, service_details

    def _get_claim_detail_fields(
        self,
        synthetic_outcome,
        price,
        quantity,
        rejection_rate,
        default_status,
        default_qty_approved,
        default_rejection_reason,
        force_rejected=False,
        force_approved=False,
    ):
        if synthetic_outcome:
            fields = self._get_realistic_detail_fields(
                synthetic_outcome,
                price,
                quantity,
                rejection_rate,
                force_rejected=force_rejected,
                force_approved=force_approved,
                claim_rejection_reason=(
                    default_rejection_reason
                    if synthetic_outcome == "REJECTED"
                    else None
                ),
            )
            approved_amount = fields.pop("approved_amount")
            return {"model_fields": fields, "approved_amount": approved_amount}

        return {
            "model_fields": {
                "status": default_status,
                "qty_approved": default_qty_approved,
                "rejection_reason": default_rejection_reason,
                "price_adjusted": None,
                "price_approved": None,
                "price_valuated": None,
            },
            "approved_amount": Decimal(0),
        }

    def _generate_claims(
        self,
        all_insurees,
        num_claims_per_insuree,
        policy_by_family,
        claims_per_insuree=None,
        realistic_claim_outcomes=False,
    ):
        if claims_per_insuree is None:
            description = f"{num_claims_per_insuree} claims for each"
        else:
            description = f"an average of {claims_per_insuree:g} claims for each"
        total_insurees = len(all_insurees)
        total_chunks = math.ceil(total_insurees / self.claim_chunk_size)
        self.write(
            f"\n=== Generating {description} of {total_insurees:,} insurees "
            f"in {total_chunks:,} chunks ==="
        )

        claim_count = item_count = service_count = 0
        for start in range(0, total_insurees, self.claim_chunk_size):
            insuree_chunk = all_insurees[start:start + self.claim_chunk_size]
            chunk_number = start // self.claim_chunk_size + 1
            with transaction.atomic():
                claims, items, services = self._build_claim_batch(
                    insuree_chunk,
                    num_claims_per_insuree,
                    policy_by_family,
                    claims_per_insuree=claims_per_insuree,
                    realistic_claim_outcomes=realistic_claim_outcomes,
                )
                created_claims = self._bulk_create_with_progress(
                    Claim, claims, f"claims (chunk {chunk_number}/{total_chunks})"
                )
                for detail in items + services:
                    detail.claim_id = detail.claim.id
                self._bulk_create_with_progress(
                    ClaimItem,
                    items,
                    f"claim items (chunk {chunk_number}/{total_chunks})",
                )
                self._bulk_create_with_progress(
                    ClaimService,
                    services,
                    f"claim services (chunk {chunk_number}/{total_chunks})",
                )

            claim_count += len(created_claims)
            item_count += len(items)
            service_count += len(services)
            processed = min(start + len(insuree_chunk), total_insurees)
            self.write(
                f"Claim chunks: {chunk_number:,}/{total_chunks:,}; "
                f"insurees: {processed:,}/{total_insurees:,}; "
                f"claims: {claim_count:,}"
            )
            del claims, created_claims, items, services
            gc.collect()

        return claim_count, item_count, service_count

    def generate_bulk_data(
        self,
        num_families,
        num_members,
        num_claims,
        family_size_weights=None,
        gender_weights=None,
        location_weighting=None,
        claims_per_insuree=None,
        round_robin_products=False,
        realistic_claim_outcomes=False,
    ):
        start_time = time.time()
        generate_claims = num_claims > 0 or claims_per_insuree is not None
        self.setup_reference_data(
            generate_claims=generate_claims,
            population_weighted_locations=location_weighting == "population",
        )

        family_sizes = self._generate_family_sizes(
            num_families, num_members, family_size_weights
        )
        total_insuree_count = sum(family_sizes)
        claims_description = (
            f"average {claims_per_insuree:g}"
            if claims_per_insuree is not None
            else str(num_claims)
        )
        self.write(
            "=" * 80
            + f"\nBULK DATA GENERATION STARTED\nFamilies: {num_families:,}, "
            f"Insurees: {total_insuree_count:,}, Claims/Insuree: "
            f"{claims_description}\n"
            + "=" * 80
        )
        optimizations_applied = False
        if self.optimize_database:
            optimizations_applied = PostgreSQLOptimizer.optimize_for_bulk_insert()

        results = {}
        try:
            all_insurees = self._generate_insurees(
                total_insuree_count, gender_weights=gender_weights
            )
            families = self._generate_families_and_link_members(
                all_insurees,
                family_sizes,
                location_weighting=location_weighting,
            )
            policies = self._generate_policies(
                families, round_robin_products=round_robin_products
            )
            insuree_policies = self._generate_insuree_policies(policies)

            results = {'families': len(families), 'insurees': len(all_insurees),
                       'policies': len(policies), 'insuree_policies': insuree_policies}

            if generate_claims:
                policy_by_family = {p.family_id: p for p in policies}
                claims, items, services = self._generate_claims(
                    all_insurees,
                    num_claims,
                    policy_by_family,
                    claims_per_insuree=claims_per_insuree,
                    realistic_claim_outcomes=realistic_claim_outcomes,
                )
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

    DEFAULT_CONFIG = {
        "families": 1000,
        "members": 4,
        "claims": 0,
        "family_size_weights": None,
        "gender_weights": None,
        "location_weighting": None,
        "claims_per_insuree": None,
        "round_robin_products": False,
        "realistic_claim_outcomes": False,
        "country": None,
    }
    PRESETS = {
        "small": {"families": 1000, "members": 3, "claims": 1},
        "medium": {"families": 10000, "members": 4, "claims": 2},
        "large": {"families": 50000, "members": 5, "claims": 2},
    }

    def add_arguments(self, parser):
        scenario = parser.add_mutually_exclusive_group()
        scenario.add_argument(
            '--preset',
            choices=['small', 'medium', 'large'],
            help='Use the original fixed-size preset configuration.',
        )
        scenario.add_argument(
            '--country',
            choices=sorted(REALISTIC_COUNTRY_PRESETS),
            help='Use a realistic country distribution preset.',
        )
        parser.add_argument('--families', type=int, help='Number of families to generate.')
        parser.add_argument('--members', type=int, help='Fixed number of members per family.')
        parser.add_argument('--claims', type=int, help='Number of claims to generate per insuree. If 0, no claims are created.')
        parser.add_argument(
            '--family-size-weights',
            help='JSON object of family sizes and probability weights.',
        )
        parser.add_argument(
            '--gender-weights',
            help='JSON object with M and F probability weights.',
        )
        parser.add_argument(
            '--location-weighting',
            choices=['uniform', 'population'],
            help='Choose family locations uniformly or by recorded population.',
        )
        parser.add_argument(
            '--claims-per-insuree',
            type=float,
            help='Average claims per insuree using a Poisson distribution.',
        )
        parser.add_argument(
            '--round-robin-products',
            action='store_true',
            default=None,
            help='Distribute family policies across active products in rotation.',
        )
        parser.add_argument(
            '--realistic-claim-outcomes',
            action='store_true',
            default=None,
            help='Generate 70%% approved, 10%% rejected and 20%% partial claims.',
        )
        parser.add_argument('--batch-size', type=int, default=2000, help='Batch size for bulk operations.')
        parser.add_argument(
            '--claim-chunk-size',
            type=int,
            default=2000,
            help='Number of insurees whose claims are held in memory at once.',
        )
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
        parser.add_argument(
            '--single-transaction',
            action='store_true',
            help='Wrap the entire generation in one transaction. Avoid for large datasets.',
        )
        parser.add_argument('--no-confirm', action='store_true', help='Skip confirmation prompt.')

    @classmethod
    def resolve_config(cls, options):
        config = dict(cls.DEFAULT_CONFIG)
        if options.get('preset'):
            config.update(cls.PRESETS[options['preset']])
        if options.get('country'):
            config.update(REALISTIC_COUNTRY_PRESETS[options['country']])
            config['country'] = options['country']

        for key in ('families', 'members', 'claims'):
            if options.get(key) is not None:
                config[key] = options[key]

        if options.get('members') is not None:
            config['family_size_weights'] = None
        if options.get('claims') is not None:
            config['claims_per_insuree'] = None

        if options.get('family_size_weights'):
            config['family_size_weights'] = cls.parse_weights(
                options['family_size_weights'], int, 'family-size'
            )
        if options.get('gender_weights'):
            config['gender_weights'] = cls.parse_weights(
                options['gender_weights'], str, 'gender'
            )
        for key in (
            'location_weighting',
            'claims_per_insuree',
            'round_robin_products',
            'realistic_claim_outcomes',
        ):
            if options.get(key) is not None:
                config[key] = options[key]

        try:
            validate_realistic_config(config)
        except ValueError as error:
            raise CommandError(str(error)) from error
        return config

    @staticmethod
    def parse_weights(raw_value, key_type, name):
        try:
            values = json.loads(raw_value)
            return {key_type(key): value for key, value in values.items()}
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise CommandError(
                f"Invalid {name} weights. Provide a JSON object."
            ) from error

    def handle(self, *args, **options):
        config = self.resolve_config(options)
        if options['preset']:
            self.stdout.write(self.style.SUCCESS(f"Using preset '{options['preset']}' configuration"))
        if options['country']:
            country_name = REALISTIC_COUNTRY_PRESETS[options['country']]['name']
            self.stdout.write(
                self.style.SUCCESS(
                    f"Using realistic country configuration: {country_name}"
                )
            )

        if config['families'] < 1:
            raise CommandError("Number of families must be at least 1.")
        if config['members'] < 1:
            raise CommandError("Number of members must be at least 1.")
        if config['claims'] < 0:
            raise CommandError("Number of claims cannot be negative.")

        batch_size = options['batch_size']
        if batch_size < 1:
            raise CommandError("Batch size must be at least 1.")
        claim_chunk_size = options['claim_chunk_size']
        if claim_chunk_size < 1:
            raise CommandError("Claim chunk size must be at least 1.")
        if config['families'] > 10000 and batch_size < 4000:
            batch_size = 5000
            self.stdout.write(self.style.WARNING(f"Auto-adjusted batch size to {batch_size} for large dataset"))

        if config['family_size_weights']:
            average_family_size = sum(
                size * weight
                for size, weight in config['family_size_weights'].items()
            )
        else:
            average_family_size = config['members']
        total_insurees = round(config['families'] * average_family_size)
        claims_per_insuree = (
            config['claims_per_insuree']
            if config['claims_per_insuree'] is not None
            else config['claims']
        )
        total_claims = round(total_insurees * claims_per_insuree)

        self.stdout.write(
            "\n"
            + "=" * 60
            + "\n"
            + self.style.SUCCESS("DJANGO BULK SYNTHETIC DATA GENERATOR - OPENIMIS")
            + "\n"
            + "=" * 60
        )
        self.stdout.write(
            f"Families to create: {config['families']:,}\n"
            f"Average members per family: {average_family_size:.2f}\n"
            f"Estimated total insurees: {total_insurees:,}"
        )
        if claims_per_insuree > 0:
            self.stdout.write(
                f"Claims per insuree: {claims_per_insuree:g}\n"
                f"Estimated total claims: {total_claims:,}\n"
                f"Claim chunk size: {claim_chunk_size:,} insurees"
            )

        if not options['no_confirm']:
            if input(f"\n{self.style.WARNING('WARNING:')} This will create a large amount of test data. Continue? (yes/no): ").lower() not in ['yes', 'y']:
                self.stdout.write(self.style.ERROR("Operation cancelled."))
                return

        generation_context = (
            transaction.atomic()
            if options['single_transaction']
            else nullcontext()
        )
        with generation_context:
            generator = BulkInsureeGenerator(
                batch_size=batch_size,
                stdout=self.stdout,
                seed=options['seed'],
                audit_user_id=options['audit_user_id'],
                optimize_database=not options['no_db_optimization'],
                claim_chunk_size=claim_chunk_size,
            )
            results = generator.generate_bulk_data(
                config['families'],
                config['members'],
                config['claims'],
                family_size_weights=config['family_size_weights'],
                gender_weights=config['gender_weights'],
                location_weighting=config['location_weighting'],
                claims_per_insuree=config['claims_per_insuree'],
                round_robin_products=config['round_robin_products'],
                realistic_claim_outcomes=config['realistic_claim_outcomes'],
            )

        self.stdout.write(self.style.SUCCESS(f"\n Successfully generated synthetic data in {results['duration']:.2f} seconds!"))
