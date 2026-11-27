"""get_queryset honours the OP-2029 validity contract (epic OP-3317, phase 1).

Explicit validity values return the expected rows; a caller asking for
nothing gets what it got before the contract existed.
"""

from django.test import TestCase

from core.test_helpers import assert_row_filters, create_test_interactive_user, mark_deleted
from claim.models import Claim
from claim.test_helpers import create_test_claim


class ClaimRowFiltersTest(TestCase):
    def test_claim(self):
        user = create_test_interactive_user(username="claimrowfilters")
        live = create_test_claim()
        deleted = mark_deleted(create_test_claim())
        assert_row_filters(
            self, Claim, Claim.objects.filter(id__in=[live.id, deleted.id]), user,
            live.id, deleted.id, default_hides_deleted=True,
        )
