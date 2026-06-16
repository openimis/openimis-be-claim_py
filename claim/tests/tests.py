import json
from core.models.openimis_graphql_test_case import (
    openIMISGraphQLTestCase,
    BaseTestContext,
)
from core.test_helpers import create_test_interactive_user
from graphql_jwt.shortcuts import get_token

# credits https://docs.graphene-python.org/projects/django/en/latest/testing/
from claim import schema as claim_schema
from graphene.test import Client
from graphene import Schema

from claim.models import Claim
from claim.services import processing_claim

import datetime
from django.core.cache import caches
from django.test import TestCase
from core.utils import clear_current_user, clear_history_context
from core.test_helpers import create_medical_officer_role
from location.test_helpers import (
    create_test_location,
    assign_user_districts,
    create_test_health_facility,
)
from claim.gql_queries import ClaimGQLType
from claim.gql_mutations import SubmitClaimsMutation
from core.gql.gql_mutations.mutation_by_filter import mutation_on_queryset_from_filter
from policy.models import Policy
from policy.test_helpers import create_test_policy2
from product.test_helpers import create_test_product, create_test_product_service
from core.test_helpers import create_test_officer
from insuree.test_helpers import create_test_insuree
from medical.test_helpers import create_test_service, create_test_diagnosis
from medical_pricelist.test_helpers import add_service_to_hf_pricelist
from claim.test_helpers import create_test_claim, create_test_claim_admin


def _make_async_mutate_spy():
    """Return a simple async_mutate replacement that records the data it receives."""
    calls = []

    def _spy(cls, user, **data):
        calls.append({"cls": cls, "user": user, "data": dict(data)})
        return "ok"

    return _spy, calls


class ClaimGraphQLTestCase(openIMISGraphQLTestCase):
    # This is required by some version of graphene but is never used. It should be set to the schema but the import
    # is shown as an error in the IDE, so leaving it as True.
    GRAPHQL_SCHEMA = True
    admin_user = None
    graph_client = None
    schema = None
    officer = None
    insuree = None
    product = None
    service = None
    product_service = None
    claim_admin = None
    icd = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin_user = create_test_interactive_user(username="testLocationAdmin")
        cls.admin_token = get_token(
            cls.admin_user, BaseTestContext(user=cls.admin_user)
        )
        cls.schema = Schema(query=claim_schema.Query, mutation=claim_schema.Mutation)
        cls.graph_client = Client(cls.schema)

        cls.officer = create_test_officer(custom_props={"code": "TSTSIMP1"})
        cls.insuree = create_test_insuree(custom_props={"chf_id": "paysimp"})
        cls.product = create_test_product("ELI1")
        (policy, insuree_policy) = create_test_policy2(
            cls.product,
            cls.insuree,
            custom_props={"value": 1000, "status": Policy.STATUS_ACTIVE},
        )
        cls.service = create_test_service("A")
        cls.claim_admin = create_test_claim_admin()
        cls.icd = create_test_diagnosis()
        cls.svc_pl_detail = add_service_to_hf_pricelist(
            cls.service, cls.claim_admin.health_facility
        )
        cls.product_service = create_test_product_service(
            cls.product, cls.service, custom_props={"limit_no_adult": 20})
        cls.claim = create_test_claim(custom_props={"insuree_id": cls.insuree.id})

    def test_claims_query(self):

        response = self.query(
            """
            query {
                claims
                {
                    totalCount
                    pageInfo { hasNextPage, hasPreviousPage, startCursor, endCursor}
                    edges
                    {
                        node
                        {
                            uuid,
                            code,
                            jsonExt,
                            dateClaimed,
                            dateProcessed,
                            feedbackStatus,
                            reviewStatus,
                            claimed,
                            approved,
                            status,
                            restoreId,
                            healthFacility { id uuid name code },
                            insuree{id, uuid, chfId, lastName, otherNames, dob},
                            attachmentsCount,
                            preAuthorization,
                            patientCondition,
                            referralCode
                        }
                    }
                }
            }
            """,
            headers={"HTTP_AUTHORIZATION": f"Bearer {self.admin_token}"},
        )

        json.loads(response.content)

        # This validates the status code and if you get errors
        self.assertResponseNoErrors(response)

        # Add some more asserts if you like
        ...

    def test_query_with_variables(self):
        response = self.query(
            """
            query claims($status: Int!, $first:  Int! ) {
                claims(status: $status,orderBy: ["-dateClaimed"],first: $first)
                {
                    totalCount
                    pageInfo { hasNextPage, hasPreviousPage, startCursor, endCursor}
                    edges
                    {
                        node
                        {
                            uuid,
                            code,
                            jsonExt,
                            dateClaimed,
                            dateProcessed,
                            feedbackStatus,
                            reviewStatus,
                            claimed,
                            approved,
                            status,
                            restoreId,
                            healthFacility { id uuid name code },
                            insuree{
                                id,
                                uuid,
                                chfId,
                                lastName,
                                otherNames,
                                dob
                            },
                            attachmentsCount,
                            preAuthorization,
                            patientCondition,
                            referralCode
                        }
                    }
                }
            }
            """,
            headers={"HTTP_AUTHORIZATION": f"Bearer {self.admin_token}"},
            variables={"status": 2, "first": 10},
        )

        json.loads(response.content)

        # This validates the status code and if you get errors
        self.assertResponseNoErrors(response)

    def execute_mutation(self, mutation):
        mutation_result = self.graph_client.execute(
            mutation, context=BaseTestContext(user=self.admin_user)
        )
        return mutation_result

    def test_mutation_create_claim(self):

        response = self.query(
            f"""
            mutation {{
                createClaim(
                    input: {{
                    clientMutationId: "3a90436a-d5ea-48e7-bde4-0bcff0240260"
                    clientMutationLabel: "Create Claim - m-c-claim"
                    code: "m-c-claim"
                autogenerate: false
                insureeId: {self.insuree.id}
                adminId: {self.claim_admin.id}
                dateFrom: "2023-12-06"
                icdId: {self.icd.id}
                jsonExt: "{{}}"
                feedbackStatus: 1
                reviewStatus: 1
                dateClaimed: "2023-12-06"
                healthFacilityId: {self.claim_admin.health_facility.id}
                visitType: "O"
                preAuthorization: false
                patientCondition: "R"
                referralCode: "REF1"
                services: [
                {{

                serviceId: {self.service.id}
                priceAsked: "10.00"
                qtyProvided: "1.00"
                status: 1,
                serviceItemSet: [],
                serviceServiceSet: []
            }}
                ]
                items: [
                ]
                    }}
                ) {{
                    clientMutationId
                    internalId
                }}
            }}
                """,
            headers={"HTTP_AUTHORIZATION": f"Bearer {self.admin_token}"},
        )
        self.get_mutation_result(
            "3a90436a-d5ea-48e7-bde4-0bcff0240260", self.admin_token
        )
        claim = Claim.objects.filter(code="m-c-claim").first()
        date_from = datetime.date.today() - datetime.timedelta(days=3)
        self.assertIsNotNone(claim)
        self.assertEqual(claim.status, Claim.STATUS_ENTERED)
        response = self.query(
            f"""
            mutation {{
                updateClaim(
                    input: {{
                    clientMutationId: "3a90436b-d5ea-48e7-bde4-0bcff0240260"
                    clientMutationLabel: "Update Claim - m-c-claim"
                    code: "m-c-claim"
                autogenerate: false
                uuid: "{str(claim.uuid)}"
                insureeId: {self.insuree.id}
                adminId: {self.claim_admin.id}
                dateFrom: "{str(date_from)}"
                icdId: {self.icd.id}
                jsonExt: "{{}}"
                feedbackStatus: 1
                reviewStatus: 1
                dateClaimed: "{str(date_from)}"
                healthFacilityId: {self.claim_admin.health_facility.id}
                visitType: "O"
                preAuthorization: false
                patientCondition: "R"
                referralCode: "REF1"
                services: [
                {{

                serviceId: {self.service.id}
                priceAsked: "10.00"
                explanation: "why not"
                qtyProvided: "2.00"
                status: 1,
                serviceServiceSet: []
            }}
                ]
                items: [
                ]
                    }}
                ) {{
                    clientMutationId
                    internalId
                }}
            }}
                """,
            headers={"HTTP_AUTHORIZATION": f"Bearer {self.admin_token}"},
        )
        response = self.get_mutation_result(
            "3a90436b-d5ea-48e7-bde4-0bcff0240260", self.admin_token
        )

        # submit claim
        response = self.query(
            f"""
            mutation {{
            submitClaims(
                input: {{
                clientMutationId: "d02fff0a-dd95-4413-a2f4-4cf2189dc0d6"
                clientMutationLabel: "Submit claim erterwtw"

                uuids: ["{claim.uuid}"]
                }}
            ) {{
                clientMutationId
                internalId
            }}
            }}
            """,
            headers={"HTTP_AUTHORIZATION": f"Bearer {self.admin_token}"},
        )
        self.assertResponseNoErrors(response)
        self.get_mutation_result(
            "d02fff0a-dd95-4413-a2f4-4cf2189dc0d6", self.admin_token
        )
        # select for feeback
        claim.refresh_from_db()
        create_test_officer(villages=[claim.insuree.family.location])
        self.assertEqual(claim.status, Claim.STATUS_CHECKED)
        response = self.query(
            f"""
            mutation {{
            selectClaimsForFeedback(
                input: {{
                clientMutationId: "f0585e2b-d72d-4001-915a-1cf10e9f1722"
                clientMutationLabel: "Select claim sadddfas for feedback"

                uuids: ["{claim.uuid}"]
                }}
            ) {{
                clientMutationId
                internalId
            }}
            }}
        """,
            headers={"HTTP_AUTHORIZATION": f"Bearer {self.admin_token}"},
        )
        self.assertResponseNoErrors(response)
        # check the mutation answer
        claim.refresh_from_db()
        self.get_mutation_result(
            "f0585e2b-d72d-4001-915a-1cf10e9f1722", self.admin_token
        )
        # deliver review
        claim_service = claim.services.first()
        response = self.query(
            f"""
    mutation {{
      saveClaimReview(
        input: {{
          clientMutationId: "d44f5fd2-1f8d-4748-a7c2-7dea38bfde05"
          clientMutationLabel: "Deliver claim review"
          services: [
                {{
                id: {claim_service.id},
                serviceId: {claim_service.service_id}
                priceApproved: "5.00"
                qtyApproved: "1.00"
                status: 1
                serviceServiceSet: [ ]
                serviceItemSet: [ ]
            }}]
          claimUuid: "{claim.uuid}"
          submitReview : false
          adjustment: "test review"
        }}
      ) {{
        clientMutationId
        internalId
      }}
    }}
        """,
            headers={"HTTP_AUTHORIZATION": f"Bearer {self.admin_token}"},
        )
        self.get_mutation_result(
            "d44f5fd2-1f8d-4748-a7c2-7dea38bfde05", self.admin_token
        )

        claim.refresh_from_db()

        response = self.query(
            f"""
    mutation {{
      saveClaimReview(
        input: {{
          clientMutationId: "d44f5fd2-1f8d-4748-a7c2-7dea38bfde06"
          clientMutationLabel: "Deliver claim review"
          services: [
                {{
                id: {claim_service.id}
                serviceId: {claim_service.service_id}
                priceApproved: "5.00"
                qtyApproved: "1.00"
                status: 1
                serviceServiceSet: [ ]
                serviceItemSet: [ ]
            }}]
          claimUuid: "{claim.uuid}"
          submitReview : true
          adjustment: "test review"
        }}
      ) {{
        clientMutationId
        internalId
      }}
    }}
        """,
            headers={"HTTP_AUTHORIZATION": f"Bearer {self.admin_token}"},
        )

        self.get_mutation_result(
            "d44f5fd2-1f8d-4748-a7c2-7dea38bfde06", self.admin_token
        )

        claim.refresh_from_db()
        self.assertEqual(claim.feedback_status, Claim.FEEDBACK_SELECTED)
        self.assertEqual(claim.review_status, Claim.REVIEW_DELIVERED)

    def test_claim_history_query(self):

        self.claim.status = Claim.STATUS_ENTERED
        self.claim.save_history()
        self.claim.save()

        self.claim.status = Claim.STATUS_CHECKED
        self.claim.save_history()
        self.claim.save()

        processing_claim(self.claim, self.admin_user, True)

        response = self.query(
            '''
            query {
                claimHistory(claimUuid: "%s") {
                    totalCount
                    edges {
                        node {
                            uuid
                            code
                            validityTo
                            status
                        }
                    }
                }
            }
            ''' % str(self.claim.uuid),
            headers={"HTTP_AUTHORIZATION": f"Bearer {self.admin_token}"}
        )
        content = json.loads(response.content)
        self.assertResponseNoErrors(response)
        self.assertEqual(content['data']['claimHistory']['totalCount'], 2)
        edges = content['data']['claimHistory']['edges']
        self.assertEqual(edges[0]['node']['code'], self.claim.code)
        self.assertIsNotNone(edges[0]['node']['validityTo'])
        self.assertEqual(edges[0]['node']['status'], Claim.STATUS_ENTERED)
        self.assertEqual(edges[1]['node']['code'], self.claim.code)
        self.assertIsNotNone(edges[1]['node']['validityTo'])
        self.assertEqual(edges[1]['node']['status'], Claim.STATUS_CHECKED)


class SubmitClaimsWithFilterDecoratorRowSecurityTest(TestCase):
    """
    Tests that the @mutation_on_queryset_from_filter decorator used on
    SubmitClaimsMutation correctly honors Claim.get_queryset row security
    (HF or district restrictions) when additional_filters (e.g. status=CHECKED)
    is used instead of explicit uuids.
    Only claims the submitting user has rights to (via HF for claim-admins or
    district via UserDistrict) must end up in the resulting queryset.
    """

    def setUp(self):
        clear_current_user()
        clear_history_context()
        caches["default"].clear()

    def tearDown(self):
        clear_current_user()
        clear_history_context()
        caches["default"].clear()

    def test_submit_claims_filter_on_checked_state_only_returns_user_accessible_claims_district(self):
        """
        District-restricted user (via UserDistrict) + filter status=CHECKED:
        - Create claims in allowed district and foreign district, all with CHECKED status.
        - additional_filters selects status CHECKED (would match all without row sec).
        - Resulting queryset from decorator must contain only the claims from the user's district.
        """
        # Locations: two districts (codes <=8 chars per model). D must have parent for UserDistrict queries in get_user_districts.
        region_allowed = create_test_location(
            "R", custom_props={"code": "RA1", "name": "Allowed Region"}
        )
        district_allowed = create_test_location(
            "D", custom_props={"code": "DA1", "name": "Allowed District for submit filter", "parent": region_allowed}
        )
        region_forbidden = create_test_location(
            "R", custom_props={"code": "RF2", "name": "Forbidden Region"}
        )
        district_forbidden = create_test_location(
            "D", custom_props={"code": "DF2", "name": "Forbidden District for submit filter", "parent": region_forbidden}
        )

        hf_allowed = create_test_health_facility(
            code="HA1", location_id=district_allowed.id
        )
        hf_forbidden = create_test_health_facility(
            code="HF2", location_id=district_forbidden.id
        )

        # Limited non-super user with submit perms, restricted to one district
        med_officer_role = create_medical_officer_role()
        limited_user = create_test_interactive_user(
            username="submitter_district_only",
            roles=[med_officer_role.id],
            custom_props={"is_superuser": False},
        )
        assign_user_districts(limited_user, [district_allowed.code])

        # Create claims matching the filter (CHECKED) in both locations
        claim_allowed1 = create_test_claim(
            custom_props={
                "health_facility": hf_allowed,
                "status": Claim.STATUS_CHECKED,
                "code": "SUBMIT-ALLOW-1",
            }
        )
        claim_allowed2 = create_test_claim(
            custom_props={
                "health_facility": hf_allowed,
                "status": Claim.STATUS_CHECKED,
                "code": "SUBMIT-ALLOW-2",
            }
        )
        # A non-matching status in allowed location (should be excluded by the status filter too)
        claim_allowed_entered = create_test_claim(
            custom_props={
                "health_facility": hf_allowed,
                "status": Claim.STATUS_ENTERED,
                "code": "SUBMIT-ALLOW-ENTERED",
            }
        )

        claim_forbidden1 = create_test_claim(
            custom_props={
                "health_facility": hf_forbidden,
                "status": Claim.STATUS_CHECKED,
                "code": "SUBMIT-FORBID-1",
            }
        )
        claim_forbidden2 = create_test_claim(
            custom_props={
                "health_facility": hf_forbidden,
                "status": Claim.STATUS_CHECKED,
                "code": "SUBMIT-FORBID-2",
            }
        )

        spy, calls = _make_async_mutate_spy()

        # Re-apply the exact same decorator config used by SubmitClaimsMutation
        # (including its explicit filter handlers for services/items)
        handlers = getattr(
            SubmitClaimsMutation, "_SubmitClaimsMutation__filter_handlers", {}
        )
        decorated = mutation_on_queryset_from_filter(
            Claim,
            ClaimGQLType,
            "additional_filters",
            handlers,
        )(spy)

        # Use additional_filters for the status=CHECKED (the filter requested in the scenario)
        # Note: key "status" maps to the exact filter.
        filters = {"status": Claim.STATUS_CHECKED}
        data = {"additional_filters": json.dumps(filters)}

        # Call without uuids and without pre-supplied queryset -> triggers get_queryset + filter
        decorated(SubmitClaimsMutation, limited_user, **data)

        self.assertEqual(len(calls), 1)
        received_data = calls[0]["data"]
        self.assertIn("queryset", received_data)
        final_qs = received_data["queryset"]

        final_uuids = set(final_qs.values_list("uuid", flat=True))

        # Only claims from the allowed location + matching filter should be present
        self.assertIn(claim_allowed1.uuid, final_uuids)
        self.assertIn(claim_allowed2.uuid, final_uuids)
        self.assertNotIn(claim_allowed_entered.uuid, final_uuids)  # filtered out by status
        self.assertNotIn(claim_forbidden1.uuid, final_uuids)
        self.assertNotIn(claim_forbidden2.uuid, final_uuids)

        # All returned must be CHECKED (the filter) and from allowed hf
        for c in final_qs:
            self.assertEqual(c.status, Claim.STATUS_CHECKED)
            self.assertEqual(c.health_facility_id, hf_allowed.id)

    def test_submit_claims_filter_on_checked_state_only_returns_user_accessible_claims_hf(self):
        """
        HF-restricted user (health_facility_id on i_user, as for claimAdmins):
        Same logic: filter status=CHECKED must only yield claims under the user's HF.
        """
        region = create_test_location(
            "R", custom_props={"code": "RH1", "name": "HF only region"}
        )
        district = create_test_location(
            "D", custom_props={"code": "DH1", "name": "HF only district", "parent": region}
        )
        hf_allowed = create_test_health_facility(
            code="HFA1", location_id=district.id
        )
        hf_forbidden = create_test_health_facility(
            code="HFF2", location_id=district.id  # same district, different hf -> still restricted by hf_id
        )

        med_officer_role = create_medical_officer_role()
        limited_user = create_test_interactive_user(
            username="submitter_hf_only",
            roles=[med_officer_role.id],
            custom_props={"is_superuser": False},
        )
        # Simulate claim admin style restriction
        limited_user.i_user.health_facility_id = hf_allowed.id
        limited_user.i_user.save()

        claim_ok = create_test_claim(
            custom_props={
                "health_facility": hf_allowed,
                "status": Claim.STATUS_CHECKED,
                "code": "HFONLY-OK",
            }
        )
        claim_bad = create_test_claim(
            custom_props={
                "health_facility": hf_forbidden,
                "status": Claim.STATUS_CHECKED,
                "code": "HFONLY-BAD",
            }
        )

        spy, calls = _make_async_mutate_spy()

        decorated = mutation_on_queryset_from_filter(
            Claim,
            ClaimGQLType,
            "additional_filters",
            getattr(SubmitClaimsMutation, "_SubmitClaimsMutation__filter_handlers", {}),
        )(spy)

        data = {"additional_filters": json.dumps({"status": Claim.STATUS_CHECKED})}

        decorated(SubmitClaimsMutation, limited_user, **data)

        final_qs = calls[0]["data"]["queryset"]
        final_uuids = set(final_qs.values_list("uuid", flat=True))

        self.assertIn(claim_ok.uuid, final_uuids)
        self.assertNotIn(claim_bad.uuid, final_uuids)

        for c in final_qs:
            self.assertEqual(c.health_facility_id, hf_allowed.id)
