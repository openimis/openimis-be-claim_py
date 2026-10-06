"""Claim reads and claim workflow mutations stay in the user's districts.

Audit C6 (`claim`, `claimAttachments`, attachment download/delete), H05 (the
right is required whatever ROW_SECURITY says), H06 (`claimHistory`) and H07
(workflow mutations acting on claim uuids).
"""

import json

from django.conf import settings
from django.core.cache import cache
from django.test import override_settings

from claim.gql_mutations import (
    DeleteAttachmentMutation,
    DeleteClaimsMutation,
    ProcessClaimsMutation,
    SaveClaimReviewMutation,
    SelectClaimsForReviewMutation,
)
from claim.models import Claim, ClaimAttachment
from claim.services import update_or_create_claim
from claim.test_helpers import create_test_claim, create_test_claim_attachment
from core.models import MutationLog
from core.models.openimis_graphql_test_case import openIMISGraphQLTestCase, BaseTestContext
from core.test_helpers import create_right_only_user
from location.models import Location
from location.test_helpers import create_basic_test_locations, create_test_health_facility

CLAIM_QUERY = 'query { claim(uuid: "%s") { code } }'
CLAIMS_QUERY = 'query { claims(code_Istartswith: "C6-") { edges { node { code } } } }'
HISTORY_QUERY = 'query { claimHistory(claimUuid: "%s") { edges { node { code } } } }'
ATTACHMENTS_QUERY = 'query { claimAttachments%s { edges { node { title } } } }'


@override_settings(ROW_SECURITY=True)
class ClaimRowSecurityTests(openIMISGraphQLTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        create_basic_test_locations()
        district_a = Location.objects.get(code="R1D1", validity_to__isnull=True)
        district_b = Location.objects.get(code="R2D1", validity_to__isnull=True)
        hf_a = create_test_health_facility(code="C6HFA", location_id=district_a.id)
        hf_b = create_test_health_facility(code="C6HFB", location_id=district_b.id)
        cls.claim_a = create_test_claim(custom_props={"code": "C6-A", "health_facility": hf_a})
        cls.claim_b = create_test_claim(custom_props={"code": "C6-B", "health_facility": hf_b})
        cls.attachment_a = create_test_claim_attachment(cls.claim_a, {"title": "C6-ATT-A"})
        cls.attachment_b = create_test_claim_attachment(cls.claim_b, {"title": "C6-ATT-B"})
        # One superseded version each, for claimHistory.
        cls.claim_a.save_history()
        cls.claim_b.save_history()

        cls.user_a = create_right_only_user(
            "c6usera",
            [
                "gql_query_claims_perms",
                "gql_mutation_update_claims_perms",
                "gql_mutation_delete_claims_perms",
                "gql_mutation_process_claims_perms",
                "gql_mutation_select_claim_review_perms",
                "gql_mutation_deliver_claim_review_perms",
            ],
            district_codes=["R1D1"],
        )
        cls.no_right = create_right_only_user("c6noright", [], district_codes=["R1D1"])
        cache.clear()

    def _gql(self, user, query):
        token = BaseTestContext(user=user).get_jwt()
        response = self.query(query, headers={"HTTP_AUTHORIZATION": f"Bearer {token}"})
        return json.loads(response.content)

    def _claim_code(self, user, claim):
        content = self._gql(user, CLAIM_QUERY % claim.uuid)
        self.assertIsNone(content.get("errors"))
        claim_data = content["data"]["claim"]
        return claim_data["code"] if claim_data else None

    def _attachment_titles(self, user, arguments=""):
        content = self._gql(user, ATTACHMENTS_QUERY % arguments)
        self.assertIsNone(content.get("errors"))
        edges = content["data"]["claimAttachments"]["edges"]
        return {e["node"]["title"] for e in edges} & {"C6-ATT-A", "C6-ATT-B"}

    def test_claim_by_uuid_keeps_own_district(self):
        self.assertEqual(self._claim_code(self.user_a, self.claim_a), "C6-A")
        # Out of scope reads as "not found", not as an error naming the claim.
        self.assertIsNone(self._claim_code(self.user_a, self.claim_b))

    def test_claim_by_id_keeps_own_district(self):
        content = self._gql(self.user_a, 'query { claim(id: %d) { code } }' % self.claim_b.id)
        self.assertIsNone(content.get("errors"))
        self.assertIsNone(content["data"]["claim"])

    @override_settings(ROW_SECURITY=False)
    def test_claim_requires_right_without_row_security(self):
        content = self._gql(self.no_right, CLAIM_QUERY % self.claim_a.uuid)
        self.assertTrue(content.get("errors"))

    def test_attachments_keep_own_district(self):
        self.assertEqual(self._attachment_titles(self.user_a), {"C6-ATT-A"})

    def test_attachments_filtered_on_foreign_claim_return_nothing(self):
        arguments = '(claim_Uuid: "%s")' % self.claim_b.uuid
        self.assertEqual(self._attachment_titles(self.user_a, arguments), set())

    def test_model_scope_follows_the_claim(self):
        titles = set(
            ClaimAttachment.get_queryset(ClaimAttachment.objects.all(), self.user_a)
            .filter(title__startswith="C6-ATT-")
            .values_list("title", flat=True)
        )
        self.assertEqual(titles, {"C6-ATT-A"})

    def _download(self, attachment):
        token = BaseTestContext(user=self.user_a).get_jwt()
        return self.client.get(
            f"/{settings.SITE_ROOT()}claim/attach/",
            {"id": str(attachment.id)},
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    def test_download_keeps_own_district(self):
        self.assertEqual(self._download(self.attachment_a).status_code, 200)
        self.assertEqual(self._download(self.attachment_b).status_code, 403)

    def test_delete_refuses_foreign_attachment(self):
        errors = DeleteAttachmentMutation.async_mutate(self.user_a, id=str(self.attachment_b.id))
        self.assertTrue(errors)
        self.assertTrue(
            ClaimAttachment.objects.filter(id=self.attachment_b.id, validity_to__isnull=True).exists()
        )

    @override_settings(ROW_SECURITY=False)
    def test_claims_and_history_require_right_without_row_security(self):
        for query in (CLAIMS_QUERY, HISTORY_QUERY % self.claim_a.uuid):
            self.assertTrue(self._gql(self.no_right, query).get("errors"))

    def test_claims_keep_own_district(self):
        content = self._gql(self.user_a, CLAIMS_QUERY)
        self.assertIsNone(content.get("errors"))
        codes = {e["node"]["code"] for e in content["data"]["claims"]["edges"]}
        self.assertEqual(codes, {"C6-A"})

    def _history_codes(self, claim):
        content = self._gql(self.user_a, HISTORY_QUERY % claim.uuid)
        self.assertIsNone(content.get("errors"))
        return [e["node"]["code"] for e in content["data"]["claimHistory"]["edges"]]

    def test_history_keeps_own_district(self):
        self.assertEqual(self._history_codes(self.claim_a), ["C6-A"])
        self.assertEqual(self._history_codes(self.claim_b), [])

    def _reload_b(self):
        return Claim.objects.get(id=self.claim_b.id)

    def test_delete_refuses_foreign_claim(self):
        errors = DeleteClaimsMutation.async_mutate(self.user_a, uuids=[self.claim_b.uuid])
        self.assertIn(self.claim_b.uuid, errors[0]["message"])
        self.assertIsNone(self._reload_b().validity_to)

    def test_process_refuses_foreign_claim(self):
        status = self._reload_b().status
        MutationLog.objects.create(json_content="{}", client_mutation_id="c6-process")
        errors = ProcessClaimsMutation.async_mutate(
            self.user_a, uuids=[self.claim_b.uuid], client_mutation_id="c6-process"
        )
        self.assertTrue(errors)
        self.assertEqual(self._reload_b().status, status)

    def test_status_change_refuses_foreign_claim(self):
        review_status = self._reload_b().review_status
        errors = SelectClaimsForReviewMutation.async_mutate(self.user_a, uuids=[self.claim_b.uuid])
        self.assertTrue(errors)
        self.assertEqual(self._reload_b().review_status, review_status)

    def test_save_review_refuses_foreign_claim(self):
        errors = SaveClaimReviewMutation.async_mutate(
            self.user_a, claim_uuid=self.claim_b.uuid, items=[], services=[]
        )
        self.assertTrue(errors)

    def test_update_refuses_foreign_claim(self):
        with self.assertRaises(Claim.DoesNotExist):
            update_or_create_claim({"uuid": self.claim_b.uuid, "code": "C6-NEW"}, self.user_a)
