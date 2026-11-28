"""DedRem (deductibles and ceilings) stage counting and recalculation, OP-3355.

The scenarios use a product whose ceiling is 1000, one item priced 100 and one
policy. A claim's remunerated amount (``rem_g`` of its DedRem row) shows which
other claims were counted as previous: with a claim of 700 counted, the next
claim of 700 is capped at 300.
"""

import datetime
import json
from decimal import Decimal

from django.db.models import Sum
from unittest import mock

from django.test import TestCase, override_settings

from claim.gql_mutations import ProcessClaimsMutation, set_claim_deleted
from claim.models import Claim, ClaimDedRem, ClaimItem
from claim.services import processing_claim, set_claims_status, update_claims_dedrems
from claim.test_helpers import (
    create_test_claim,
    create_test_claimitem,
    mark_test_claim_as_processed,
)
from claim.validations import validate_claim
from core.models.openimis_graphql_test_case import BaseTestContext, openIMISGraphQLTestCase
from core.test_helpers import create_test_interactive_user
from insuree.test_helpers import create_test_insuree
from location.test_helpers import create_test_health_facility
from medical.test_helpers import create_test_item
from medical_pricelist.test_helpers import add_item_to_hf_pricelist
from policy.test_helpers import create_test_policy2
from product.test_helpers import create_test_product, create_test_product_item

CEILING = 1000


def days_ago(days):
    return datetime.date.today() - datetime.timedelta(days=days)


class DedRemScenarioMixin:
    """Builds claims on one policy and walks them through the claim stages."""

    # A threshold is required by calculate_deductibles_and_ceilings for a
    # policy-level ceiling; the family is below it, so the ceiling is max_policy.
    product_props = {"max_policy": CEILING, "threshold": 10}

    def setUp(self):
        super().setUp()
        self.user = create_test_interactive_user()
        self.insuree = create_test_insuree()
        self.product = create_test_product("DRM", custom_props=dict(self.product_props))
        self.policy, _ = create_test_policy2(
            self.product, self.insuree, link=True, custom_props=self._policy_dates()
        )
        self.item = create_test_item("D")
        create_test_product_item(self.product, self.item)
        self.health_facility = None

    @staticmethod
    def _policy_dates():
        # Wide enough for every reference date used, whatever the day of the year.
        return {
            "enroll_date": days_ago(365),
            "start_date": days_ago(365),
            "effective_date": days_ago(365),
            "expiry_date": days_ago(-365),
        }

    def add_insuree_to_policy(self):
        insuree = create_test_insuree(
            with_family=False, custom_props={"family": self.insuree.family}
        )
        create_test_policy2(self.product, insuree, link=True, custom_props=self._policy_dates())
        return insuree

    def make_claim(self, days, qty=7, insuree=None):
        """An entered claim whose reference date is `days` days ago, for qty x 100."""
        props = {
            "insuree_id": (insuree or self.insuree).id,
            "date_from": days_ago(days),
            "date_to": None,
        }
        if self.health_facility:
            props["health_facility"] = self.health_facility
        claim = create_test_claim(props)
        if not self.health_facility:
            self.health_facility = claim.health_facility
            self.health_facility.care_type = self.health_facility.CARE_TYPE_BOTH
            self.health_facility.save()
            add_item_to_hf_pricelist(self.item, self.health_facility)
        create_test_claimitem(
            claim, "D", custom_props={"item_id": self.item.id, "qty_provided": qty}
        )
        return claim

    def submit(self, claim):
        """What ClaimSubmitService.submit_claim does: validate, then mark checked."""
        errors = processing_claim(claim, self.user, False)
        self.assertEqual(errors, [])
        mark_test_claim_as_processed(claim)
        return claim

    def deliver_review(self, claim):
        """What DeliverClaimsReviewMutation does on an unchanged claim."""
        set_claims_status([claim.uuid], "review_status", Claim.REVIEW_DELIVERED)
        errors = update_claims_dedrems([claim.uuid], self.user)
        self.assertEqual(errors, [])
        claim.refresh_from_db()

    def reject_at_review(self, claim):
        """What SaveClaimReviewMutation does when the review rejects every detail."""
        for detail in claim.items.filter(validity_to__isnull=True):
            detail.status = ClaimItem.STATUS_REJECTED
            detail.rejection_reason = -1
            detail.qty_approved = 0
            detail.price_approved = 0
            detail.save()
        claim.status = Claim.STATUS_REJECTED
        claim.review_status = Claim.REVIEW_DELIVERED
        claim.save()
        update_claims_dedrems(None, self.user, [claim])

    def process(self, claim):
        """What ProcessClaimsMutation does on a checked claim."""
        claim.refresh_from_db()
        claim.save_history()
        errors = processing_claim(claim, self.user, True)
        self.assertEqual(errors, [])
        claim.save()
        claim.refresh_from_db()

    def row(self, claim):
        rows = ClaimDedRem.objects.filter(claim=claim, *ClaimDedRem.filter_validity())
        self.assertEqual(rows.count(), 1, f"claim {claim.code} must have one DedRem row")
        return rows.first()


class DedRemStageTest(DedRemScenarioMixin, TestCase):
    def test_processing_does_not_count_checked_claims(self):
        claim_a = self.submit(self.make_claim(10))
        claim_b = self.submit(self.make_claim(8))
        # Both checked: B counts A, so B is capped at 300 for now.
        self.assertEqual(self.row(claim_b).rem_g, 300)

        self.process(claim_b)

        # Processed, B counts processed claims only: A (checked) is left out.
        self.assertEqual(self.row(claim_b).rem_g, 700)
        item_b = claim_b.items.get(validity_to__isnull=True)
        self.assertEqual(item_b.remunerated_amount, 700)
        self.assertEqual(claim_b.status, Claim.STATUS_VALUATED)
        self.assertEqual(self.row(claim_b).status, ClaimDedRem.STATUS_PROCESSED)
        self.assertEqual(self.row(claim_a).rem_g, 700)

    def test_review_rejection_recalculates_later_claim(self):
        claim_a = self.submit(self.make_claim(10))
        claim_b = self.submit(self.make_claim(8))
        self.assertEqual(self.row(claim_b).rem_g, 300)

        self.reject_at_review(claim_a)

        self.assertFalse(ClaimDedRem.objects.filter(claim=claim_a).exists())
        self.assertEqual(self.row(claim_b).rem_g, 700)

    def test_deleted_claim_loses_its_dedrem_and_later_claim_is_recalculated(self):
        claim_a = self.submit(self.make_claim(10))
        claim_b = self.submit(self.make_claim(8))
        self.assertEqual(self.row(claim_b).rem_g, 300)

        self.assertEqual(set_claim_deleted(claim_a), [])

        self.assertFalse(ClaimDedRem.objects.filter(claim=claim_a).exists())
        self.assertEqual(self.row(claim_b).rem_g, 700)

    def test_reprocessing_keeps_one_row_per_claim(self):
        claim_a = self.make_claim(10)
        self.assertEqual(validate_claim(claim_a, True), [])
        self.assertEqual(validate_claim(claim_a, True), [])
        mark_test_claim_as_processed(claim_a)

        # Archived rows included: the table holds one row per claim.
        self.assertEqual(ClaimDedRem.objects.filter(claim=claim_a).count(), 1)

        claim_b = self.submit(self.make_claim(8))
        # B counts A once: 1000 - 700.
        self.assertEqual(self.row(claim_b).rem_g, 300)

        self.process(claim_a)
        self.assertEqual(ClaimDedRem.objects.filter(claim=claim_a).count(), 1)
        policy_total = ClaimDedRem.objects.filter(
            policy=self.policy, *ClaimDedRem.filter_validity()
        ).aggregate(total=Sum("rem_g"))["total"]
        self.assertEqual(policy_total, 700 + 300)

    def test_each_status_counts_only_its_stages(self):
        # K stays checked (100), R is reviewed (200), P is processed (400).
        claim_k = self.submit(self.make_claim(20, qty=1))
        claim_r = self.submit(self.make_claim(19, qty=2))
        claim_p = self.submit(self.make_claim(18, qty=4))
        self.deliver_review(claim_r)
        self.process(claim_p)

        claim_x = self.make_claim(17, qty=10)
        # Checked counts checked, reviewed and processed: 1000 - 700.
        self.submit(claim_x)
        self.assertEqual(self.row(claim_x).rem_g, 300)
        # Reviewed counts reviewed and processed: 1000 - 600.
        self.deliver_review(claim_x)
        self.assertEqual(self.row(claim_x).rem_g, 400)
        self.assertEqual(self.row(claim_x).status, ClaimDedRem.STATUS_REVIEWED)
        # Processed counts processed only: 1000 - 400.
        self.process(claim_x)
        self.assertEqual(self.row(claim_x).rem_g, 600)
        self.assertEqual(self.row(claim_x).status, ClaimDedRem.STATUS_PROCESSED)
        self.assertEqual(self.row(claim_k).status, ClaimDedRem.STATUS_CHECKED)
        self.assertEqual(self.row(claim_r).status, ClaimDedRem.STATUS_REVIEWED)
        self.assertEqual(self.row(claim_p).status, ClaimDedRem.STATUS_PROCESSED)


class DedRemCascadeTest(DedRemScenarioMixin, TestCase):
    def test_processed_claim_is_never_recalculated(self):
        claim_a = self.submit(self.make_claim(10))
        claim_p = self.submit(self.make_claim(8))
        self.process(claim_p)
        processed_row = self.row(claim_p)
        processed_item = claim_p.items.get(validity_to__isnull=True)
        claim_c = self.submit(self.make_claim(6))
        # C counts A (checked) and P (processed): 1000 - 1400 leaves nothing.
        self.assertEqual(self.row(claim_c).rem_g, 0)

        self.reject_at_review(claim_a)

        self.assertEqual(self.row(claim_c).rem_g, 300)
        self.assertEqual(self.row(claim_p).id, processed_row.id)
        self.assertEqual(self.row(claim_p).rem_g, 700)
        claim_p.refresh_from_db()
        self.assertEqual(claim_p.status, Claim.STATUS_VALUATED)
        item = claim_p.items.get(validity_to__isnull=True)
        self.assertEqual(item.remunerated_amount, processed_item.remunerated_amount)

    def test_claim_with_earlier_reference_date_is_not_recalculated(self):
        claim_e = self.submit(self.make_claim(12))
        claim_a = self.submit(self.make_claim(10))
        claim_c = self.submit(self.make_claim(8))
        earlier_row = self.row(claim_e)
        self.assertEqual(self.row(claim_c).rem_g, 0)

        self.reject_at_review(claim_a)

        self.assertEqual(self.row(claim_c).rem_g, 300)
        self.assertEqual(self.row(claim_e).id, earlier_row.id)

    def test_claim_of_another_insuree_is_not_recalculated_under_insuree_limit(self):
        self.product.max_policy = None
        self.product.max_insuree = CEILING
        self.product.save()
        other_insuree = self.add_insuree_to_policy()
        claim_a = self.submit(self.make_claim(10))
        claim_o = self.submit(self.make_claim(8, insuree=other_insuree))
        claim_c = self.submit(self.make_claim(6))
        other_row = self.row(claim_o)
        self.assertEqual(other_row.rem_g, 700)
        self.assertEqual(self.row(claim_c).rem_g, 300)

        self.reject_at_review(claim_a)

        self.assertEqual(self.row(claim_c).rem_g, 700)
        self.assertEqual(self.row(claim_o).id, other_row.id)

    def test_nothing_is_recalculated_under_treatment_limit(self):
        self.product.max_policy = None
        self.product.max_treatment = 500
        self.product.save()
        claim_a = self.submit(self.make_claim(10))
        claim_c = self.submit(self.make_claim(8))
        later_row = self.row(claim_c)
        self.assertEqual(later_row.rem_g, 500)

        self.assertEqual(set_claim_deleted(claim_a), [])

        self.assertFalse(ClaimDedRem.objects.filter(claim=claim_a).exists())
        self.assertEqual(self.row(claim_c).id, later_row.id)

    def test_cascade_recalculates_a_claim_already_recalculated_by_a_cascade(self):
        claim_a = self.submit(self.make_claim(10))
        claim_b = self.submit(self.make_claim(9))
        claim_c = self.submit(self.make_claim(8))
        self.assertEqual(self.row(claim_c).rem_g, 0)

        self.reject_at_review(claim_a)
        # B no longer counts A (700), and C counts B only.
        self.assertEqual(self.row(claim_b).rem_g, 700)
        self.assertEqual(self.row(claim_c).rem_g, 300)

        # C's row is now newer than every stamp of C, and is recalculated anyway.
        self.assertEqual(set_claim_deleted(claim_b), [])
        self.assertEqual(self.row(claim_c).rem_g, 700)

    def test_own_recompute_is_skipped_when_dedrem_is_newer_than_the_claim(self):
        claim_a = self.submit(self.make_claim(10))
        claim_c = self.submit(self.make_claim(8))
        self.reject_at_review(claim_a)
        cascaded_row = self.row(claim_c)
        self.assertEqual(cascaded_row.rem_g, 700)

        # Nothing changed on C since the cascade wrote its row: no recompute.
        self.assertEqual(processing_claim(claim_c, self.user, False), [])
        self.assertEqual(self.row(claim_c).id, cascaded_row.id)

        # A review delivery is a stage change: it always recomputes.
        self.deliver_review(claim_c)
        self.assertNotEqual(self.row(claim_c).id, cascaded_row.id)
        self.assertEqual(self.row(claim_c).status, ClaimDedRem.STATUS_REVIEWED)


@override_settings(ROW_SECURITY=False)
class DedRemBatchOrderTest(DedRemScenarioMixin, TestCase):
    def _submit_later_claim_first(self):
        # B is created and submitted before A, so a batch in id order handles B first.
        claim_b = self.submit(self.make_claim(8))
        claim_a = self.submit(self.make_claim(10))
        return claim_a, claim_b

    def test_process_batch_handles_claims_by_reference_date(self):
        claim_a, claim_b = self._submit_later_claim_first()

        # The submission statistics need a MutationLog row; they are not under test.
        with mock.patch.object(ProcessClaimsMutation, "add_submission_stats_to_mutation_log"):
            errors = ProcessClaimsMutation.async_mutate(
                self.user, uuids=[claim_b.uuid, claim_a.uuid]
            )

        self.assertEqual(errors, [])
        # A (earlier) is settled first; B then counts A as processed.
        self.assertEqual(self.row(claim_a).rem_g, 700)
        self.assertEqual(self.row(claim_b).rem_g, 300)

    def test_review_delivery_batch_handles_claims_by_reference_date(self):
        claim_a, claim_b = self._submit_later_claim_first()
        set_claims_status(
            [claim_b.uuid, claim_a.uuid], "review_status", Claim.REVIEW_DELIVERED
        )

        errors = update_claims_dedrems([claim_b.uuid, claim_a.uuid], self.user)

        self.assertEqual(errors, [])
        self.assertEqual(self.row(claim_a).rem_g, 700)
        self.assertEqual(self.row(claim_b).rem_g, 300)


DEDREM_FIELDS = (
    "dedG dedIp dedOp remG remIp remOp remConsult remSurgery remDelivery "
    "remHospitalization remAntenatal"
)


@override_settings(ROW_SECURITY=False)
class DedRemGraphQLTest(DedRemScenarioMixin, openIMISGraphQLTestCase):
    def setUp(self):
        super().setUp()
        other_insuree = self.add_insuree_to_policy()
        self.submit(self.make_claim(10))
        self.process(self.submit(self.make_claim(9)))
        self.submit(self.make_claim(8, insuree=other_insuree))
        self.other_insuree = other_insuree

    def _gql(self, query):
        token = BaseTestContext(user=self.user).get_jwt()
        response = self.query(query, headers={"HTTP_AUTHORIZATION": f"Bearer {token}"})
        content = json.loads(response.content)
        self.assertIsNone(content.get("errors"), content.get("errors"))
        return content["data"]

    def _rows_and_sum(self, arguments):
        data = self._gql(
            "query { claimDedRems(%s) { edges { node { %s } } } "
            "claimDedRemSum(%s) { %s } }"
            % (arguments, DEDREM_FIELDS, arguments, DEDREM_FIELDS)
        )
        rows = [edge["node"] for edge in data["claimDedRems"]["edges"]]
        return rows, data["claimDedRemSum"]

    def assert_sum_matches_rows(self, arguments, expected_count):
        rows, total = self._rows_and_sum(arguments)
        self.assertEqual(len(rows), expected_count)
        for field in DEDREM_FIELDS.split():
            expected = sum(Decimal(str(row[field] or 0)) for row in rows)
            self.assertEqual(Decimal(str(total[field] or 0)), expected, field)

    def test_sum_equals_rows_under_the_same_filters(self):
        policy = 'policyUuid: "%s"' % self.policy.uuid
        insuree = 'insureeUuid: "%s"' % self.insuree.uuid
        self.assert_sum_matches_rows(policy, 3)
        self.assert_sum_matches_rows(f"{policy}, {insuree}", 2)
        self.assert_sum_matches_rows(f"{policy}, status: [PROCESSED]", 1)
        self.assert_sum_matches_rows(f"{policy}, {insuree}, status: [CHECKED]", 1)
        self.assert_sum_matches_rows(f"{policy}, status: [CHECKED, REVIEWED]", 2)

    def test_sum_stops_at_date_to(self):
        policy = 'policyUuid: "%s"' % self.policy.uuid
        _, total = self._rows_and_sum(policy)
        data = self._gql(
            'query { claimDedRemSum(%s, dateTo: "%s") { remG } }'
            % (policy, days_ago(10).isoformat())
        )
        # Only the claim of 10 days ago (700) is counted, not the processed one of
        # 9 days ago (700) nor the one of 8 days ago (0, the ceiling being reached).
        self.assertEqual(Decimal(str(total["remG"])), 1400)
        self.assertEqual(Decimal(str(data["claimDedRemSum"]["remG"])), 700)

    def test_rows_by_claim(self):
        claim = Claim.objects.filter(insuree=self.other_insuree).first()
        data = self._gql(
            'query { claimDedRems(claimUuid: "%s") { edges { node { remG status } } } }'
            % claim.uuid
        )
        edges = data["claimDedRems"]["edges"]
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["node"]["status"], "CHECKED")
