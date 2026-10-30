import random
from datetime import date, timedelta
from types import SimpleNamespace
from unittest import mock

from django.core.management.base import CommandError
from django.test import SimpleTestCase

from claim.management.commands.seed_synthetic_health_data import (
    CARE_TYPE_WEIGHTS,
    BulkInsureeGenerator,
    Command,
    DataGenerator,
    PostgreSQLOptimizer,
)
from claim.models import Claim, ClaimDetail, ClaimItem, ClaimService
from insuree.models import Insuree
from location.models import HealthFacility
from medical.models import Diagnosis, Item, Service


class CommandConfigTest(SimpleTestCase):
    def test_defaults_are_used_without_a_preset(self):
        config = Command.resolve_config(
            {"preset": None, "families": None, "members": None, "claims": None}
        )

        self.assertEqual(config, {"families": 1000, "members": 4, "claims": 0})

    def test_explicit_values_override_a_preset(self):
        config = Command.resolve_config(
            {"preset": "medium", "families": 12, "members": None, "claims": 1}
        )

        self.assertEqual(config, {"families": 12, "members": 4, "claims": 1})


class DataGeneratorTest(SimpleTestCase):
    def test_seed_reproduces_faker_and_chf_values(self):
        first = DataGenerator(seed=77)
        second = DataGenerator(seed=77)

        first_values = (
            first.get_first_name(),
            first.get_last_name(),
            first.get_address(),
            first.get_unique_chf_id(),
        )
        second_values = (
            second.get_first_name(),
            second.get_last_name(),
            second.get_address(),
            second.get_unique_chf_id(),
        )

        self.assertEqual(first_values, second_values)


class PostgreSQLOptimizerTest(SimpleTestCase):
    @mock.patch(
        "claim.management.commands.seed_synthetic_health_data.connection",
        SimpleNamespace(vendor="sqlite"),
    )
    def test_non_postgresql_database_is_ignored(self):
        self.assertFalse(PostgreSQLOptimizer.optimize_for_bulk_insert())


class GetClaimDateRangeTest(SimpleTestCase):
    def setUp(self):
        self.generator = BulkInsureeGenerator(seed=101)
        self.insuree = SimpleNamespace(family_id=42)

    def test_active_policy_caps_latest_date_to_today(self):
        effective_date = date.today() - timedelta(days=400)
        policy = SimpleNamespace(
            pk=1,
            effective_date=effective_date,
            expiry_date=date.today() + timedelta(days=100),
        )

        earliest, latest = self.generator._get_claim_date_range(
            self.insuree, {42: policy}
        )

        self.assertEqual(earliest, effective_date)
        self.assertEqual(latest, date.today())

    def test_expired_policy_caps_latest_date_to_expiry(self):
        effective_date = date.today() - timedelta(days=400)
        expiry = date.today() - timedelta(days=10)
        policy = SimpleNamespace(
            pk=1, effective_date=effective_date, expiry_date=expiry
        )

        earliest, latest = self.generator._get_claim_date_range(
            self.insuree, {42: policy}
        )

        self.assertEqual(earliest, effective_date)
        self.assertEqual(latest, expiry)

    def test_no_policy_uses_two_year_window_ending_today(self):
        earliest, latest = self.generator._get_claim_date_range(self.insuree, {})

        self.assertEqual(latest, date.today())
        self.assertEqual(earliest, date.today() - timedelta(days=730))

    def test_future_policy_has_no_valid_claim_range(self):
        policy = SimpleNamespace(
            pk=1,
            effective_date=date.today() + timedelta(days=1),
            expiry_date=date.today() + timedelta(days=366),
        )

        with self.assertRaises(CommandError):
            self.generator._get_claim_date_range(self.insuree, {42: policy})


class GetClaimProgressFieldsTest(SimpleTestCase):
    def setUp(self):
        self.generator = BulkInsureeGenerator(seed=104, audit_user_id=12)
        self.claim_date = date.today() - timedelta(days=60)

    def test_entered_has_no_stamps(self):
        result = self._get_progress("ENTERED")

        self.assertEqual(
            result, (Claim.STATUS_ENTERED, None, None, None, None, None)
        )

    def test_submitted_claim_has_only_a_submit_stamp(self):
        status, submit, process, processed_date, audit_submit, audit_process = (
            self._get_progress("SUBMIT")
        )

        self.assertEqual(status, Claim.STATUS_CHECKED)
        self.assertGreater(submit.date(), self.claim_date)
        self.assertLessEqual(submit.date(), date.today())
        self.assertIsNone(process)
        self.assertIsNone(processed_date)
        self.assertEqual(audit_submit, 12)
        self.assertIsNone(audit_process)

    def test_processed_claim_has_sequential_stamps(self):
        status, submit, process, processed_date, audit_submit, audit_process = (
            self._get_progress("PROCESSED")
        )

        self.assertEqual(status, Claim.STATUS_PROCESSED)
        self.assertGreater(process, submit)
        self.assertLessEqual(process.date(), date.today())
        self.assertEqual(processed_date, process.date())
        self.assertEqual((audit_submit, audit_process), (12, 12))

    def test_valuated_claim_has_sequential_stamps(self):
        status, submit, process, processed_date, audit_submit, audit_process = (
            self._get_progress("VALUATED")
        )

        self.assertEqual(status, Claim.STATUS_VALUATED)
        self.assertGreater(process, submit)
        self.assertLessEqual(process.date(), date.today())
        self.assertIsNone(processed_date)
        self.assertEqual((audit_submit, audit_process), (12, 12))

    def test_claim_from_today_cannot_have_future_workflow_stamps(self):
        result = self._get_progress("VALUATED", claim_date=date.today())

        self.assertEqual(
            result, (Claim.STATUS_ENTERED, None, None, None, None, None)
        )

    def _get_progress(self, progress, claim_date=None):
        seeded_random = mock.Mock(wraps=random.Random(104))
        seeded_random.choices.return_value = [progress]
        self.generator.random = seeded_random
        return self.generator._get_claim_progress_fields(
            claim_date or self.claim_date
        )


class GenerateClaimsTest(SimpleTestCase):
    def setUp(self):
        self.generator = BulkInsureeGenerator(
            batch_size=20, seed=202, audit_user_id=7
        )
        self.health_facility = HealthFacility(id=1)
        self.insuree = Insuree(
            id=1,
            family_id=10,
            health_facility=self.health_facility,
        )
        self.policy = SimpleNamespace(
            pk=1,
            effective_date=date.today() - timedelta(days=500),
            expiry_date=date.today() - timedelta(days=50),
        )
        self.generator.health_facilities = [self.health_facility]
        self.generator.diagnoses = [Diagnosis(id=1)]
        self.generator.items = [Item(id=1)]
        self.generator.services = [Service(id=1)]
        self.created = {}

    def test_claims_are_coherent(self):
        results = self._generate(250)
        claims = self.created[Claim]
        items = self.created[ClaimItem]
        services = self.created[ClaimService]

        self.assertEqual(results, (250, len(items), len(services)))
        self.assertEqual(len(claims), 250)
        self.assertEqual({claim.care_type for claim in claims}, {"IPD", "OPD"})
        self.assertEqual({claim.visit_type for claim in claims}, {"O", "E", "R"})

        expected_ipd_ratio = CARE_TYPE_WEIGHTS["IPD"] / sum(
            CARE_TYPE_WEIGHTS.values()
        )
        actual_ipd_ratio = sum(c.care_type == "IPD" for c in claims) / len(claims)
        self.assertAlmostEqual(actual_ipd_ratio, expected_ipd_ratio, delta=0.06)

        rejected = [c for c in claims if c.status == Claim.STATUS_REJECTED]
        self.assertGreater(len(rejected), 0)
        self.assertGreater(len(claims) - len(rejected), 0)

        details_by_claim = {claim.id: [] for claim in claims}
        for detail in items + services:
            details_by_claim[detail.claim_id].append(detail)

        for claim in claims:
            self.assertIsNotNone(claim.health_facility_id)
            self.assertIn(claim.health_facility, self.generator.health_facilities)
            self.assertGreaterEqual(claim.date_from, self.policy.effective_date)
            self.assertLessEqual(claim.date_from, self.policy.expiry_date)
            self.assertEqual(claim.audit_user_id, 7)
            if claim.care_type == "IPD":
                self.assertEqual(claim.date_claimed, claim.date_to)
                self.assertGreaterEqual((claim.date_to - claim.date_from).days, 2)
                self.assertLessEqual((claim.date_to - claim.date_from).days, 5)
                self.assertLessEqual(claim.date_to, self.policy.expiry_date)
            else:
                self.assertIsNone(claim.date_to)
                self.assertEqual(claim.date_claimed, claim.date_from)

            if claim.submit_stamp:
                self.assertLessEqual(claim.submit_stamp.date(), date.today())
            if claim.process_stamp:
                self.assertLessEqual(claim.process_stamp.date(), date.today())

            expected_total = sum(
                detail.price_asked * detail.qty_provided
                for detail in details_by_claim[claim.id]
            )
            self.assertEqual(claim.claimed, expected_total)
            for detail in details_by_claim[claim.id]:
                self.assertEqual(detail.audit_user_id, 7)
                if claim.status == Claim.STATUS_REJECTED:
                    self.assertEqual(detail.status, ClaimDetail.STATUS_REJECTED)
                    self.assertEqual(detail.rejection_reason, claim.rejection_reason)
                    self.assertEqual(detail.qty_approved, 0)
                else:
                    self.assertEqual(detail.status, ClaimDetail.STATUS_PASSED)
                    self.assertIsNone(detail.rejection_reason)

    def test_missing_insuree_health_facility_uses_active_fallback(self):
        self.insuree.health_facility = None

        self._generate(3)

        for claim in self.created[Claim]:
            self.assertEqual(claim.health_facility, self.health_facility)
            self.assertIsNotNone(claim.health_facility_id)

    def test_inactive_insuree_health_facility_uses_active_fallback(self):
        inactive_health_facility = HealthFacility(id=2)
        self.insuree.health_facility = inactive_health_facility

        self._generate(3)

        for claim in self.created[Claim]:
            self.assertEqual(claim.health_facility, self.health_facility)

    def test_claim_generation_requires_an_active_health_facility(self):
        self.generator.health_facilities = []

        with self.assertRaisesMessage(
            CommandError,
            "Cannot generate claims without an active health facility.",
        ):
            self._generate(1)

    def _generate(self, count):
        next_ids = {Claim: 1, ClaimItem: 1, ClaimService: 1}

        def capture(model_class, objects, description):
            del description
            self.created.setdefault(model_class, []).extend(objects)
            for obj in objects:
                obj.id = next_ids[model_class]
                next_ids[model_class] += 1
            return objects

        with mock.patch.object(
            self.generator, "_bulk_create_with_progress", side_effect=capture
        ), mock.patch.object(Claim.objects, "bulk_update"):
            return self.generator._generate_claims(
                [self.insuree], count, {self.insuree.family_id: self.policy}
            )
