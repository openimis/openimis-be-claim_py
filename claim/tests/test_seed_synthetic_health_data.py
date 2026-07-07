from datetime import date, timedelta

from django.test import TestCase

from claim.management.commands.seed_synthetic_health_data import CARE_TYPE_WEIGHTS, BulkInsureeGenerator
from claim.models import Claim
from insuree.test_helpers import create_test_insuree
from location.test_helpers import create_test_health_facility, create_test_village
from medical.models import Diagnosis
from medical.test_helpers import create_test_item, create_test_service
from policy.test_helpers import create_test_policy2
from product.test_helpers import create_test_product


class GetClaimDateRangeTest(TestCase):
    """Rule: a claim date must never precede the insuree's policy, and must
    not be in the future even if the policy is still active."""

    def setUp(self):
        self.generator = BulkInsureeGenerator()
        self.insuree = create_test_insuree()
        self.product = create_test_product("SEEDT01")

    def test_active_policy_caps_latest_date_to_today(self):
        policy, _ = create_test_policy2(
            self.product, self.insuree, link=False,
            custom_props={
                "effective_date": date.today() - timedelta(days=400),
                "expiry_date": date.today() + timedelta(days=100),
            },
        )
        policy_by_family = {self.insuree.family_id: policy}

        earliest, latest = self.generator._get_claim_date_range(self.insuree, policy_by_family)

        self.assertEqual(earliest, policy.effective_date)
        self.assertEqual(latest, date.today())

    def test_expired_policy_caps_latest_date_to_expiry(self):
        expiry = date.today() - timedelta(days=10)
        policy, _ = create_test_policy2(
            self.product, self.insuree, link=False,
            custom_props={
                "effective_date": date.today() - timedelta(days=400),
                "expiry_date": expiry,
            },
        )
        policy_by_family = {self.insuree.family_id: policy}

        earliest, latest = self.generator._get_claim_date_range(self.insuree, policy_by_family)

        self.assertEqual(earliest, policy.effective_date)
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
