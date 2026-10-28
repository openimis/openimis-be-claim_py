from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from claim.schema import on_claim_after_mutation
from claim.services import handle_claim_subscription_notification


class ClaimSubscriptionNotificationTest(SimpleTestCase):
    @patch("api_fhir_r4.signals.notify_subscribers")
    @patch(
        "api_fhir_r4.converters.claimResponseConverter.ClaimResponseConverter"
    )
    @patch(
        "api_fhir_r4.configurations.R4ClaimConfig."
        "R4ClaimConfig.get_subscribe_claim_signal",
        return_value=True,
    )
    @patch("claim.services.Claim.objects.filter")
    def test_notifies_for_created_claim_when_enabled(
        self,
        mock_filter,
        _mock_get_subscribe_claim_signal,
        mock_converter,
        mock_notify_subscribers,
    ):
        claim = MagicMock()
        user = MagicMock(username="testadmin")
        converter = mock_converter.return_value
        mock_filter.return_value.first.return_value = claim

        handle_claim_subscription_notification(
            None,
            data={"code": "CLAIM-001"},
            user=user,
        )

        mock_filter.assert_called_once_with(
            code="CLAIM-001",
            validity_to__isnull=True,
        )
        mock_converter.assert_called_once_with(user=user)
        mock_notify_subscribers.assert_called_once_with(
            claim,
            converter,
            "Claim",
            None,
        )

    @patch("api_fhir_r4.signals.notify_subscribers")
    @patch(
        "api_fhir_r4.configurations.R4ClaimConfig."
        "R4ClaimConfig.get_subscribe_claim_signal",
        return_value=False,
    )
    @patch("claim.services.Claim.objects.filter")
    def test_does_not_notify_when_disabled(
        self,
        mock_filter,
        _mock_get_subscribe_claim_signal,
        mock_notify_subscribers,
    ):
        handle_claim_subscription_notification(
            None,
            data={"code": "CLAIM-001"},
            user=MagicMock(),
        )

        mock_filter.assert_not_called()
        mock_notify_subscribers.assert_not_called()


class ClaimMutationSubscriptionTest(SimpleTestCase):
    @patch("claim.schema.handle_claim_subscription_notification")
    def test_successful_create_triggers_subscription(self, mock_handle):
        kwargs = {
            "mutation_class": "CreateClaimMutation",
            "error_messages": None,
            "data": {"code": "CLAIM-001", "autogenerate": False},
            "user": MagicMock(),
        }

        self.assertEqual(on_claim_after_mutation(None, **kwargs), [])

        mock_handle.assert_called_once_with(None, **kwargs)

    @patch("claim.schema.handle_claim_subscription_notification")
    def test_failed_create_does_not_trigger_subscription(self, mock_handle):
        result = on_claim_after_mutation(
            None,
            mutation_class="CreateClaimMutation",
            error_messages=[{"message": "failed"}],
            data={"code": "CLAIM-001"},
            user=MagicMock(),
        )

        self.assertEqual(result, [])
        mock_handle.assert_not_called()

    @patch("claim.schema.handle_claim_subscription_notification")
    def test_other_mutation_does_not_trigger_subscription(self, mock_handle):
        result = on_claim_after_mutation(
            None,
            mutation_class="UpdateClaimMutation",
            error_messages=None,
            data={"code": "CLAIM-001"},
            user=MagicMock(),
        )

        self.assertEqual(result, [])
        mock_handle.assert_not_called()
