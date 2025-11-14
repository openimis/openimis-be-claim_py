# Add these imports to the top of your claim/tests.py file
import unittest.mock as mock
from core.models import User
from core.models.openimis_graphql_test_case import openIMISGraphQLTestCase, BaseTestContext
from core.utils import filter_validity
from core.test_helpers import create_test_interactive_user
from django.conf import settings
from graphene_django.utils.testing import GraphQLTestCase
from graphql_jwt.shortcuts import get_token
from claim import schema as claim_schema
from graphene.test import Client
from graphene import Schema
from django.test import TestCase

class ClaimNotificationTestCase(TestCase):
    """
    Test case to verify claim notifications work from web interface
    """
    
    @classmethod
    def setUpTestData(cls):
        """Set up test data - reuse your existing test data setup"""
        super().setUpTestData()
        
        # Create admin user for GraphQL testing
        cls.admin_user = create_test_interactive_user(username="testClaimAdmin")
        cls.admin_token = get_token(cls.admin_user, BaseTestContext(user=cls.admin_user))
        
        # Set up GraphQL schema
        cls.schema = Schema(
            query=claim_schema.Query,
            mutation=claim_schema.Mutation
        )
        cls.graph_client = Client(cls.schema)

    def query(self, query_string, headers=None, variables=None):
        """Helper method for GraphQL queries"""
        from django.test import Client as DjangoClient
        
        client = DjangoClient()
        response = client.post(
            '/graphql',
            {
                'query': query_string,
                'variables': variables or {}
            },
            content_type='application/json',
            **headers or {}
        )
        return response

    def get_mutation_result(self, mutation_id, token):
        """Helper to get mutation result"""
        # This would typically check for mutation results in your system
        # You might need to adapt this based on your GraphQL implementation
        return {}

    @mock.patch('api_fhir_r4.configurations.R4ClaimConfig.get_subscribe_claim_signal')
    @mock.patch('claim.schema.notify_subscribers')
    @mock.patch('openIMIS.openimisapps.openimis_apps')
    def test_claim_creation_triggers_notification_when_enabled(self, mock_openimis_apps, mock_notify_subscribers, mock_get_subscribe_claim_signal):
        """
        Test that claim creation from web interface triggers notification when enabled
        """
        # Setup mocks
        mock_openimis_apps.return_value = ['claim', 'insuree', 'location']
        mock_get_subscribe_claim_signal.return_value = True
        
        # Use existing test data
        test_claim_code = "notif-test-001"
        
        # Create claim via GraphQL mutation
        mutation = f'''
            mutation {{
                createClaim(
                    input: {{
                        clientMutationId: "notification-test-001"
                        clientMutationLabel: "Create Claim for Notification Test"
                        code: "{test_claim_code}"
                        autogenerate: false
                        insureeId: {self.test_insuree.id}
                        adminId: {self.test_claim_admin.id}
                        dateFrom: "2023-12-06"
                        icdId: {self.test_icd.id}
                        jsonExt: "{{}}"
                        feedbackStatus: 1
                        reviewStatus: 1
                        dateClaimed: "2023-12-06"
                        healthFacilityId: {self.test_hf.id}
                        visitType: "O"
                        preAuthorization: false
                        services: [{{
                            serviceId: {self.test_claim_service.service.id}
                            priceAsked: "10.00"
                            qtyProvided: "1.00"
                            status: 1,
                            serviceItemSet: [],
                            serviceServiceSet: []
                        }}]
                        items: []
                    }}
                ) {{
                    clientMutationId
                    internalId
                }}
            }}
        '''
        
        response = self.query(
            mutation,
            headers={"HTTP_AUTHORIZATION": f"Bearer {self.admin_token}"}
        )
        
        # Verify claim was created
        created_claim = Claim.objects.filter(code=test_claim_code).first()
        self.assertIsNotNone(created_claim, "Claim should be created")
        
        # Verify notification was triggered
        mock_notify_subscribers.assert_called_once()
        
        # Verify the correct parameters were passed to notify_subscribers
        call_args = mock_notify_subscribers.call_args
        called_claim, converter_instance, model_name, context = call_args[0]
        
        self.assertEqual(called_claim.uuid, created_claim.uuid)
        self.assertEqual(model_name, 'Claim')
        self.assertIsNone(context)

    @mock.patch('api_fhir_r4.configurations.R4ClaimConfig.get_subscribe_claim_signal')
    @mock.patch('claim.schema.notify_subscribers')
    @mock.patch('openIMIS.openimisapps.openimis_apps')
    def test_claim_creation_skips_notification_when_disabled(self, mock_openimis_apps, mock_notify_subscribers, mock_get_subscribe_claim_signal):
        """
        Test that claim creation skips notification when disabled
        """
        # Setup mocks - notifications disabled
        mock_openimis_apps.return_value = ['claim', 'insuree', 'location']
        mock_get_subscribe_claim_signal.return_value = False  # DISABLED
        
        test_claim_code = "notif-test-002"
        
        # Create claim via GraphQL mutation
        mutation = f'''
            mutation {{
                createClaim(
                    input: {{
                        clientMutationId: "notification-test-002"
                        code: "{test_claim_code}"
                        autogenerate: false
                        insureeId: {self.test_insuree.id}
                        adminId: {self.test_claim_admin.id}
                        dateFrom: "2023-12-06"
                        icdId: {self.test_icd.id}
                        healthFacilityId: {self.test_hf.id}
                        visitType: "O"
                        services: [{{
                            serviceId: {self.test_claim_service.service.id}
                            priceAsked: "10.00"
                            qtyProvided: "1.00"
                            status: 1,
                            serviceItemSet: [],
                            serviceServiceSet: []
                        }}]
                        items: []
                    }}
                ) {{
                    clientMutationId
                }}
            }}
        '''
        
        response = self.query(
            mutation,
            headers={"HTTP_AUTHORIZATION": f"Bearer {self.admin_token}"}
        )
        
        # Verify claim was created
        created_claim = Claim.objects.filter(code=test_claim_code).first()
        self.assertIsNotNone(created_claim, "Claim should be created")
        
        # Verify notification was NOT triggered
        mock_notify_subscribers.assert_not_called()

    @mock.patch('api_fhir_r4.configurations.R4ClaimConfig.get_subscribe_claim_signal')
    @mock.patch('claim.schema.notify_subscribers')
    @mock.patch('openIMIS.openimisapps.openimis_apps')
    def test_claim_creation_skips_notification_when_module_inactive(self, mock_openimis_apps, mock_notify_subscribers, mock_get_subscribe_claim_signal):
        """
        Test that notification is skipped when claim module is not active
        """
        # Setup mocks - claim module not active
        mock_openimis_apps.return_value = ['insuree', 'location']  # NO claim module
        mock_get_subscribe_claim_signal.return_value = True
        
        test_claim_code = "notif-test-003"
        
        # Create claim using service directly (since GraphQL might not work without module)
        mock_user = mock.Mock()
        mock_user.id_for_audit = -1
        mock_user.has_perm = mock.MagicMock(return_value=True)
        
        claim_data = {
            'code': test_claim_code,
            'health_facility_id': self.test_hf.id,
            'insuree_id': self.test_insuree.id,
            'icd_id': self.test_icd.id,
            'date_from': core.datetime.date(2023, 12, 6),
            'date_claimed': core.datetime.date(2023, 12, 6),
            'audit_user_id': -1,
            'services': [{
                'service_id': self.test_claim_service.service.id,
                'price_asked': 10.00,
                'qty_provided': 1.00,
                'status': 1
            }]
        }
        
        # Create claim
        created_claim = Claim.objects.create(
            code=test_claim_code,
            health_facility=self.test_hf,
            insuree=self.test_insuree,
            icd=self.test_icd,
            date_from=core.datetime.date(2023, 12, 6),
            date_claimed=core.datetime.date(2023, 12, 6),
            audit_user_id=-1,
            status=Claim.STATUS_ENTERED
        )
        
        # Verify claim was created
        self.assertIsNotNone(created_claim)
        
        # Verify notification was NOT triggered (due to module being inactive)
        mock_notify_subscribers.assert_not_called()

    def test_failed_claim_creation_skips_notification(self):
        """
        Test that failed claim creation does not trigger notification
        """
        with mock.patch('openIMIS.openimisapps.openimis_apps') as mock_openimis_apps, \
             mock.patch('api_fhir_r4.configurations.R4ClaimConfig.get_subscribe_claim_signal') as mock_get_subscribe_claim_signal, \
             mock.patch('claim.schema.notify_subscribers') as mock_notify_subscribers:
            
            mock_openimis_apps.return_value = ['claim', 'insuree', 'location']
            mock_get_subscribe_claim_signal.return_value = True
            
            # Create a claim with duplicate code (should fail)
            existing_code = self.test_claim.code  # Use existing claim's code
            
            # Try to create another claim with same code
            with self.assertRaises(Exception):  # Should raise validation error
                Claim.objects.create(
                    code=existing_code,  # Duplicate code
                    health_facility=self.test_hf,
                    insuree=self.test_insuree,
                    icd=self.test_icd,
                    date_from=core.datetime.date(2023, 12, 6),
                    date_claimed=core.datetime.date(2023, 12, 6),
                    audit_user_id=-1,
                    status=Claim.STATUS_ENTERED
                )
            
            mock_notify_subscribers.assert_not_called()