from datetime import date, timedelta
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase

from claim.management.commands.seed_synthetic_health_data import (
    AUTOMATIC_REJECTION_RATE, CARE_TYPE_WEIGHTS, CLAIM_PROGRESS_WEIGHTS,
    MEDICAL_OFFICER_REJECTION_RATE, MEDICAL_OFFICER_REJECTION_REASON,
    PROCESS_DELAY_RANGE_DAYS, SUBMIT_DELAY_RANGE_DAYS,
    SUBMIT_TO_VALUATED_RANGE_DAYS, BulkInsureeGenerator,
)
from claim.models import Claim, ClaimDetail, ClaimItem, ClaimService
from core.test_helpers import create_test_officer
from insuree.test_helpers import create_test_insuree
from location.test_helpers import create_test_health_facility, create_test_village
from medical.models import Diagnosis
from medical.test_helpers import create_test_item, create_test_service
from policy.test_helpers import create_test_policy2
from product.test_helpers import create_test_product


class GetClaimDateRangeTest(TestCase):
    """Rule: a claim date must never precede the insuree's policy, and must
    not be in the future even if the policy is still active.

    _get_claim_date_range only reads policy.effective_date/expiry_date and
    insuree.family_id, so a lightweight double is enough here
    """

    def setUp(self):
        self.generator = BulkInsureeGenerator()
        self.insuree = create_test_insuree()

    def test_active_policy_caps_latest_date_to_today(self):
        effective_date = date.today() - timedelta(days=400)
        policy = SimpleNamespace(effective_date=effective_date, expiry_date=date.today() + timedelta(days=100))
        policy_by_family = {self.insuree.family_id: policy}

        earliest, latest = self.generator._get_claim_date_range(self.insuree, policy_by_family)

        self.assertEqual(earliest, effective_date)
        self.assertEqual(latest, date.today())

    def test_expired_policy_caps_latest_date_to_expiry(self):
        effective_date = date.today() - timedelta(days=400)
        expiry = date.today() - timedelta(days=10)
        policy = SimpleNamespace(effective_date=effective_date, expiry_date=expiry)
        policy_by_family = {self.insuree.family_id: policy}

        earliest, latest = self.generator._get_claim_date_range(self.insuree, policy_by_family)

        self.assertEqual(earliest, effective_date)
        self.assertEqual(latest, expiry)

    def test_no_policy_falls_back_to_two_year_window_ending_today(self):
        earliest, latest = self.generator._get_claim_date_range(self.insuree, {})

        self.assertEqual(latest, date.today())
        self.assertEqual(earliest, date.today() - timedelta(days=730))


class GenerateClaimsDateCoherenceTest(TestCase):
    """Claims generated in bulk must fall within the insuree's policy period."""

    def setUp(self):
        self.generator = BulkInsureeGenerator(batch_size=10)
        village = create_test_village()
        district = village.parent.parent
        health_facility = create_test_health_facility("SEED1", district.id, valid=True)
        self.insuree = create_test_insuree(
            custom_props={"chf_id": "seedclaimtest", "health_facility": health_facility}
        )
        product = create_test_product("SEEDT02")
        self.policy, _ = create_test_policy2(
            product, self.insuree, link=False,
            custom_props={
                "effective_date": date.today() - timedelta(days=500),
                "expiry_date": date.today() - timedelta(days=50),
            },
        )
        self.generator.diagnoses = [Diagnosis.objects.create(code="ICDSEED", name="seed diag", audit_user_id=-1)]
        self.generator.items = [create_test_item("D")]
        self.generator.services = [create_test_service("V")]
        self.generator.health_facilities = [health_facility]

    def test_claim_dates_stay_within_policy_period(self):
        policy_by_family = {self.insuree.family_id: self.policy}

        self.generator._generate_claims([self.insuree], 5, policy_by_family)

        claims = Claim.objects.filter(insuree=self.insuree)
        self.assertEqual(claims.count(), 5)
        for claim in claims:
            self.assertGreaterEqual(claim.date_from, self.policy.effective_date)
            self.assertLessEqual(claim.date_from, self.policy.expiry_date)

    def test_care_type_drives_date_to(self):
        """IPD claims must carry a date_to 2-5 days after date_from; OPD claims must not."""
        policy_by_family = {self.insuree.family_id: self.policy}

        # Generate enough claims that both IPD and OPD are very likely to appear.
        self.generator._generate_claims([self.insuree], 40, policy_by_family)

        claims = Claim.objects.filter(insuree=self.insuree)
        self.assertEqual(claims.count(), 40)

        care_types_seen = set()
        for claim in claims:
            self.assertIn(claim.care_type, ["IPD", "OPD"])
            care_types_seen.add(claim.care_type)
            if claim.care_type == "IPD":
                self.assertIsNotNone(claim.date_to)
                stay_length = (claim.date_to - claim.date_from).days
                self.assertGreaterEqual(stay_length, 2)
                self.assertLessEqual(stay_length, 5)
            else:
                self.assertIsNone(claim.date_to)

        # With 40 draws, both care types should show up (sanity check on randomization).
        self.assertEqual(care_types_seen, {"IPD", "OPD"})

    def test_care_type_distribution_matches_configured_weights(self):
        """IPD share should track CARE_TYPE_WEIGHTS (~15-20%), not a 50/50 split."""
        policy_by_family = {self.insuree.family_id: self.policy}
        sample_size = 2000

        self.generator._generate_claims([self.insuree], sample_size, policy_by_family)

        claims = Claim.objects.filter(insuree=self.insuree)
        ipd_count = claims.filter(care_type="IPD").count()
        ipd_ratio = ipd_count / sample_size

        expected_ratio = CARE_TYPE_WEIGHTS["IPD"] / sum(CARE_TYPE_WEIGHTS.values())
        # Allow a statistical tolerance around the expected ratio for a sample of 2000.
        self.assertAlmostEqual(ipd_ratio, expected_ratio, delta=0.05)

    def test_visit_type_is_randomized_among_valid_values(self):
        policy_by_family = {self.insuree.family_id: self.policy}

        # Generate enough claims that all three visit types are very likely to appear.
        self.generator._generate_claims([self.insuree], 60, policy_by_family)

        claims = Claim.objects.filter(insuree=self.insuree)
        self.assertEqual(claims.count(), 60)

        visit_types_seen = {claim.visit_type for claim in claims}
        for visit_type in visit_types_seen:
            self.assertIn(visit_type, ["O", "E", "R"])

        # With 60 draws, all three visit types should show up (sanity check on randomization).
        self.assertEqual(visit_types_seen, {"O", "E", "R"})


class GenerateClaimsStatusDistributionTest(TestCase):
    """Claim status must follow the configured automatic / Medical Officer
    rejection rates, and rejections must cascade to items and services."""

    def setUp(self):
        self.generator = BulkInsureeGenerator(batch_size=50)
        village = create_test_village()
        district = village.parent.parent
        health_facility = create_test_health_facility("SEED2", district.id, valid=True)
        self.insuree = create_test_insuree(
            custom_props={"chf_id": "seedstatustest", "health_facility": health_facility}
        )
        product = create_test_product("SEEDT03")
        self.policy, _ = create_test_policy2(
            product, self.insuree, link=False,
            custom_props={
                "effective_date": date.today() - timedelta(days=500),
                "expiry_date": date.today() - timedelta(days=50),
            },
        )
        self.generator.diagnoses = [Diagnosis.objects.create(code="ICDSEED2", name="seed diag 2", audit_user_id=-1)]
        self.generator.items = [create_test_item("D")]
        self.generator.services = [create_test_service("V")]
        self.generator.health_facilities = [health_facility]

    def test_status_distribution_matches_configured_rates(self):
        policy_by_family = {self.insuree.family_id: self.policy}
        sample_size = 3000

        self.generator._generate_claims([self.insuree], sample_size, policy_by_family)

        claims = Claim.objects.filter(insuree=self.insuree)
        self.assertEqual(claims.count(), sample_size)

        auto_rejected = claims.filter(status=Claim.STATUS_REJECTED, review_status=Claim.REVIEW_IDLE)
        mo_rejected = claims.filter(status=Claim.STATUS_REJECTED, review_status=Claim.REVIEW_DELIVERED)
        # Non-rejected claims are spread across Entered/Submit/Processed/Valuated
        # by CLAIM_PROGRESS_WEIGHTS (see GenerateClaimsProgressDistributionTest),
        # so "normal" here means "not rejected", not "still Entered".
        normal = claims.exclude(status=Claim.STATUS_REJECTED)

        self.assertAlmostEqual(auto_rejected.count() / sample_size, AUTOMATIC_REJECTION_RATE, delta=0.03)
        self.assertAlmostEqual(mo_rejected.count() / sample_size, MEDICAL_OFFICER_REJECTION_RATE, delta=0.02)
        self.assertAlmostEqual(normal.count() / sample_size, 1 - AUTOMATIC_REJECTION_RATE - MEDICAL_OFFICER_REJECTION_RATE, delta=0.03)

        # Every Medical Officer rejection carries the manual reason and a reviewer audit id.
        for claim in mo_rejected:
            self.assertEqual(claim.rejection_reason, MEDICAL_OFFICER_REJECTION_REASON)
            self.assertIsNotNone(claim.audit_user_id_review)

        # Normal (non-rejected) claims must not carry a rejection reason.
        for claim in normal:
            self.assertEqual(claim.rejection_reason, 0)

    def test_rejected_claims_cascade_status_to_items_and_services(self):
        policy_by_family = {self.insuree.family_id: self.policy}
        sample_size = 500

        self.generator._generate_claims([self.insuree], sample_size, policy_by_family)

        rejected_claims = Claim.objects.filter(insuree=self.insuree, status=Claim.STATUS_REJECTED)
        passed_claims = Claim.objects.filter(insuree=self.insuree, status=Claim.STATUS_ENTERED)
        self.assertGreater(rejected_claims.count(), 0)
        self.assertGreater(passed_claims.count(), 0)

        for claim in rejected_claims:
            for item in ClaimItem.objects.filter(claim=claim):
                self.assertEqual(item.status, ClaimDetail.STATUS_REJECTED)
                self.assertEqual(item.rejection_reason, claim.rejection_reason)
                self.assertEqual(item.qty_approved, 0)
            for service in ClaimService.objects.filter(claim=claim):
                self.assertEqual(service.status, ClaimDetail.STATUS_REJECTED)
                self.assertEqual(service.rejection_reason, claim.rejection_reason)
                self.assertEqual(service.qty_approved, 0)

        for claim in passed_claims:
            for item in ClaimItem.objects.filter(claim=claim):
                self.assertEqual(item.status, ClaimDetail.STATUS_PASSED)
                self.assertIsNone(item.rejection_reason)
            for service in ClaimService.objects.filter(claim=claim):
                self.assertEqual(service.status, ClaimDetail.STATUS_PASSED)
                self.assertIsNone(service.rejection_reason)


class GetClaimProgressFieldsTest(TestCase):
    """Rule: non-rejected claims progress Entered -> Submit -> Processed ->
    Valuated with sequential, random-gap dates."""

    def setUp(self):
        self.generator = BulkInsureeGenerator()
        self.claim_date = date.today() - timedelta(days=60)

    def test_entered_has_no_stamps(self):
        with self._force_progress("ENTERED"):
            status, submit_stamp, process_stamp, date_processed, audit_submit, audit_process = (
                self.generator._get_claim_progress_fields(self.claim_date)
            )

        self.assertEqual(status, Claim.STATUS_ENTERED)
        self.assertIsNone(submit_stamp)
        self.assertIsNone(process_stamp)
        self.assertIsNone(date_processed)
        self.assertIsNone(audit_submit)
        self.assertIsNone(audit_process)

    def test_submit_sets_only_submit_stamp_after_claim_date(self):
        with self._force_progress("SUBMIT"):
            status, submit_stamp, process_stamp, date_processed, audit_submit, audit_process = (
                self.generator._get_claim_progress_fields(self.claim_date)
            )

        self.assertEqual(status, Claim.STATUS_CHECKED)
        self.assertIsNotNone(submit_stamp)
        self.assertGreaterEqual(submit_stamp.date(), self.claim_date)
        self.assertIsNone(process_stamp)
        self.assertIsNone(date_processed)
        self.assertEqual(audit_submit, 1)
        self.assertIsNone(audit_process)

    def test_processed_sets_sequential_submit_and_process_stamps(self):
        with self._force_progress("PROCESSED"):
            status, submit_stamp, process_stamp, date_processed, audit_submit, audit_process = (
                self.generator._get_claim_progress_fields(self.claim_date)
            )

        self.assertEqual(status, Claim.STATUS_PROCESSED)
        self.assertGreaterEqual(submit_stamp.date(), self.claim_date)
        self.assertGreater(process_stamp, submit_stamp)
        self.assertEqual(date_processed, process_stamp.date())
        self.assertEqual(audit_submit, 1)
        self.assertEqual(audit_process, 1)

    def test_valuated_reuses_process_stamp_within_one_month_of_submit(self):
        with self._force_progress("VALUATED"):
            status, submit_stamp, process_stamp, date_processed, audit_submit, audit_process = (
                self.generator._get_claim_progress_fields(self.claim_date)
            )

        self.assertEqual(status, Claim.STATUS_VALUATED)
        self.assertGreaterEqual(submit_stamp.date(), self.claim_date)
        self.assertGreater(process_stamp, submit_stamp)
        gap_days = (process_stamp.date() - submit_stamp.date()).days
        self.assertGreaterEqual(gap_days, SUBMIT_TO_VALUATED_RANGE_DAYS[0])
        self.assertLessEqual(gap_days, SUBMIT_TO_VALUATED_RANGE_DAYS[1])
        # No dedicated date_valuated column exists - process_stamp is reused instead.
        self.assertIsNone(date_processed)
        self.assertEqual(audit_submit, 1)
        self.assertEqual(audit_process, 1)

    def _force_progress(self, progress_key):
        return mock.patch("random.choices", return_value=[progress_key])


class GenerateClaimsProgressDistributionTest(TestCase):
    """Non-rejected claims must follow CLAIM_PROGRESS_WEIGHTS and carry
    sequential submit/process dates; rejected claims must not."""

    def setUp(self):
        self.generator = BulkInsureeGenerator(batch_size=50)
        village = create_test_village()
        district = village.parent.parent
        health_facility = create_test_health_facility("SEED3", district.id, valid=True)
        self.insuree = create_test_insuree(
            custom_props={"chf_id": "seedprogresstest", "health_facility": health_facility}
        )
        product = create_test_product("SEEDT04")
        self.policy, _ = create_test_policy2(
            product, self.insuree, link=False,
            custom_props={
                "effective_date": date.today() - timedelta(days=500),
                "expiry_date": date.today() - timedelta(days=50),
            },
        )
        self.generator.diagnoses = [Diagnosis.objects.create(code="ICDSEED3", name="seed diag 3", audit_user_id=-1)]
        self.generator.items = [create_test_item("D")]
        self.generator.services = [create_test_service("V")]
        self.generator.health_facilities = [health_facility]

    def test_progress_distribution_matches_configured_weights(self):
        policy_by_family = {self.insuree.family_id: self.policy}
        sample_size = 3000

        self.generator._generate_claims([self.insuree], sample_size, policy_by_family)

        non_rejected = Claim.objects.filter(insuree=self.insuree).exclude(status=Claim.STATUS_REJECTED)
        total_non_rejected = non_rejected.count()
        total_weight = sum(CLAIM_PROGRESS_WEIGHTS.values())

        status_by_progress = {
            "ENTERED": Claim.STATUS_ENTERED, "SUBMIT": Claim.STATUS_CHECKED,
            "PROCESSED": Claim.STATUS_PROCESSED, "VALUATED": Claim.STATUS_VALUATED,
        }
        for progress, status in status_by_progress.items():
            expected_ratio = CLAIM_PROGRESS_WEIGHTS[progress] / total_weight
            actual_ratio = non_rejected.filter(status=status).count() / total_non_rejected
            self.assertAlmostEqual(actual_ratio, expected_ratio, delta=0.04)

    def test_dates_are_sequential_and_stamps_match_status(self):
        policy_by_family = {self.insuree.family_id: self.policy}

        self.generator._generate_claims([self.insuree], 500, policy_by_family)

        claims = Claim.objects.filter(insuree=self.insuree)
        for claim in claims:
            if claim.status == Claim.STATUS_REJECTED:
                self.assertIsNone(claim.submit_stamp)
                self.assertIsNone(claim.process_stamp)
                self.assertIsNone(claim.date_processed)
            elif claim.status == Claim.STATUS_ENTERED:
                self.assertIsNone(claim.submit_stamp)
                self.assertIsNone(claim.process_stamp)
            elif claim.status == Claim.STATUS_CHECKED:
                self.assertIsNotNone(claim.submit_stamp)
                self.assertGreaterEqual(claim.submit_stamp.date(), claim.date_from)
                self.assertIsNone(claim.process_stamp)
                self.assertEqual(claim.audit_user_id_submit, 1)
            elif claim.status == Claim.STATUS_PROCESSED:
                self.assertGreaterEqual(claim.submit_stamp.date(), claim.date_from)
                self.assertGreater(claim.process_stamp, claim.submit_stamp)
                self.assertEqual(claim.date_processed, claim.process_stamp.date())
                self.assertEqual(claim.audit_user_id_submit, 1)
                self.assertEqual(claim.audit_user_id_process, 1)
            elif claim.status == Claim.STATUS_VALUATED:
                self.assertGreaterEqual(claim.submit_stamp.date(), claim.date_from)
                self.assertGreater(claim.process_stamp, claim.submit_stamp)
                gap_days = (claim.process_stamp.date() - claim.submit_stamp.date()).days
                self.assertGreaterEqual(gap_days, SUBMIT_TO_VALUATED_RANGE_DAYS[0])
                self.assertLessEqual(gap_days, SUBMIT_TO_VALUATED_RANGE_DAYS[1])
                self.assertIsNone(claim.date_processed)
                self.assertEqual(claim.audit_user_id_submit, 1)
                self.assertEqual(claim.audit_user_id_process, 1)


class SetupReferenceDataValidityTest(TestCase):
    """Rule: reference data used to build claims (health facilities, products,
    officers, diagnoses, items, services) must be currently valid
    (validity_to IS NULL) - expired/superseded rows must never be picked."""

    def setUp(self):
        self.generator = BulkInsureeGenerator()
        self.village = create_test_village()
        self.district = self.village.parent.parent

    def test_expired_health_facility_is_excluded(self):
        valid_hf = create_test_health_facility("VALIDHF", self.district.id, valid=True)
        create_test_health_facility("EXPHF", self.district.id, valid=False)

        self.generator.setup_reference_data(generate_claims=False)

        hf_codes = {hf.code for hf in self.generator.health_facilities}
        self.assertIn(valid_hf.code, hf_codes)
        self.assertNotIn("EXPHF", hf_codes)

    def test_expired_officer_is_excluded(self):
        valid_officer = create_test_officer(valid=True, custom_props={"code": "VALIDOFF"})
        create_test_officer(valid=False, custom_props={"code": "EXPOFF"})

        self.generator.setup_reference_data(generate_claims=False)

        officer_codes = {o.code for o in self.generator.officers}
        self.assertIn(valid_officer.code, officer_codes)
        self.assertNotIn("EXPOFF", officer_codes)

    def test_expired_product_is_excluded(self):
        valid_product = create_test_product("VALIDPR", valid=True)
        create_test_product("EXPIRPR", valid=False)

        self.generator.setup_reference_data(generate_claims=False)

        product_codes = {p.code for p in self.generator.products}
        self.assertIn(valid_product.code, product_codes)
        self.assertNotIn("EXPIRPR", product_codes)

    def test_expired_diagnosis_item_and_service_are_excluded(self):
        create_test_health_facility("VALIDHF2", self.district.id, valid=True)
        create_test_officer(valid=True)
        create_test_product("VALIDPR2", valid=True)

        # Diagnosis.code and Item.code are limited to 6 characters in the DB schema.
        valid_diag = Diagnosis.objects.create(code="VALDIC", name="valid diag", audit_user_id=-1)
        Diagnosis.objects.create(code="EXPDIC", name="expired diag", audit_user_id=-1, validity_to=date.today())
        valid_item = create_test_item("D", valid=True, custom_props={"code": "VALIT"})
        create_test_item("D", valid=False, custom_props={"code": "EXPIT"})
        valid_service = create_test_service("V", valid=True, custom_props={"code": "VALIDSVC"})
        create_test_service("V", valid=False, custom_props={"code": "EXPSVC"})

        self.generator.setup_reference_data(generate_claims=True)

        self.assertIn(valid_diag.code, {d.code for d in self.generator.diagnoses})
        self.assertNotIn("EXPDIC", {d.code for d in self.generator.diagnoses})
        self.assertIn(valid_item.code, {i.code for i in self.generator.items})
        self.assertNotIn("EXPIT", {i.code for i in self.generator.items})
        self.assertIn(valid_service.code, {s.code for s in self.generator.services})
        self.assertNotIn("EXPSVC", {s.code for s in self.generator.services})
