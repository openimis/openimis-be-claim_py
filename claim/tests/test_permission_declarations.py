"""
Guard rails on claim's rights declaration.

Same structure as `core`: `DJANGO_PERMS` by entity then by action, `_PERM_CFG`
deriving the config keys from it, and `Claim.get_rights` which is only an access
point. What this shape gains over a flat list is that identifier sharing becomes
visible: 111010 appears once as "update" and turns up named on every action that is,
in fact, a modification of the claim.

What is locked down here is the entity/action pair, not only the values:
  * an identifier in one place only (DJANGO_PERMS), hence no drift between the
    DEFAULT_CFG and the check;
  * a config key with no class attribute is never loaded by `__load_config` and
    reading it raises AttributeError - the right becomes unenforceable;
  * `has_perms([])` returns True, so an empty list grants to everybody.
"""

from django.test import TestCase

from claim.apps import (
    DJANGO_PERMS,
    ClaimConfig,
    _PERM_CFG,
    configured_perms,
    django_perms,
    perms,
)
from claim.models import Claim

# The identifiers as deployed. Changing one is incompatible with the existing roles:
# this test has to be updated *and* the new right granted.
EXPECTED_RIGHTS = {
    "gql_query_claims_perms": ["111001"],
    "gql_query_claim_officers_perms": ["111001"],
    "gql_mutation_create_claims_perms": ["111002"],
    "gql_mutation_update_claims_perms": ["111010"],
    "gql_mutation_load_claims_perms": ["111005"],
    "gql_mutation_submit_claims_perms": ["111007"],
    "gql_mutation_select_claim_feedback_perms": ["111010"],
    "gql_mutation_bypass_claim_feedback_perms": ["111010"],
    "gql_mutation_skip_claim_feedback_perms": ["111010"],
    "gql_mutation_deliver_claim_feedback_perms": ["111009"],
    "gql_mutation_select_claim_review_perms": ["111010"],
    "gql_mutation_bypass_claim_review_perms": ["111010"],
    "gql_mutation_skip_claim_review_perms": ["111010"],
    "gql_mutation_deliver_claim_review_perms": ["111008"],
    "gql_mutation_process_claims_perms": ["111011"],
    "gql_mutation_restore_claims_perms": ["111012"],
    "gql_mutation_delete_claims_perms": ["111004"],
    "claim_print_perms": ["111006"],
}

# Actions that deliberately share another action's right.
INTENTIONALLY_SHARED = {
    ("claim", "selectFeedback"): ("claim", "update"),
    ("claim", "bypassFeedback"): ("claim", "update"),
    ("claim", "skipFeedback"): ("claim", "update"),
    ("claim", "selectReview"): ("claim", "update"),
    ("claim", "bypassReview"): ("claim", "update"),
    ("claim", "skipReview"): ("claim", "update"),
    ("claimOfficer", "query"): ("claim", "query"),
}


class ClaimPermissionDeclarationTestCase(TestCase):
    def test_right_ids_unchanged(self):
        self.assertEqual(
            {key: getattr(ClaimConfig, key) for key in EXPECTED_RIGHTS}, EXPECTED_RIGHTS
        )

    def test_perm_cfg_covers_every_declared_action(self):
        declared = {
            (entity, action)
            for entity, actions in DJANGO_PERMS.items()
            for action in actions
        }
        self.assertEqual(set(_PERM_CFG.values()), declared)

    def test_perm_cfg_matches_config_attributes(self):
        """`__load_config` ignores the keys with no class attribute."""
        missing = [key for key in _PERM_CFG if not hasattr(ClaimConfig, key)]
        self.assertEqual(missing, [])

    def test_no_right_list_is_empty(self):
        empty = [key for key in _PERM_CFG if not getattr(ClaimConfig, key)]
        self.assertEqual(empty, [])

    def test_attributes_carry_the_declared_right(self):
        """
        The rights are constants set from DJANGO_PERMS: the attribute must equal the
        declaration, without going through the config.
        """
        for key, (entity, action) in _PERM_CFG.items():
            with self.subTest(key=key):
                self.assertEqual(getattr(ClaimConfig, key), perms(entity, action))

    def test_shared_right_ids_are_only_the_intended_ones(self):
        seen = {}
        for entity, actions in DJANGO_PERMS.items():
            for action, (_, right_id) in actions.items():
                seen.setdefault(right_id, []).append((entity, action))
        for right_id, holders in seen.items():
            if len(holders) == 1:
                continue
            for holder in holders:
                with self.subTest(right=right_id, holder=holder):
                    target = INTENTIONALLY_SHARED.get(holder)
                    self.assertTrue(
                        target is None or target in holders,
                        f"{holder} shares right {right_id} without that being intended",
                    )

    def test_django_permission_names_are_unique(self):
        """Even the actions sharing an identifier keep a distinct django name."""
        seen = {}
        for entity, actions in DJANGO_PERMS.items():
            for action, (name, _) in actions.items():
                seen.setdefault(name, []).append(f"{entity}.{action}")
        shared = {name: who for name, who in seen.items() if len(who) > 1}
        self.assertEqual(shared, {})

    def test_unknown_entity_or_action_raises(self):
        with self.assertRaises(KeyError):
            perms("nosuchentity", "query")
        with self.assertRaises(KeyError):
            perms("claim", "nosuchaction")
        with self.assertRaises(KeyError):
            django_perms("claim", "nosuchaction")

    # --- the access point through the model -------------------------------
    def test_model_exposes_every_action_of_its_entity(self):
        for action in DJANGO_PERMS["claim"]:
            with self.subTest(action=action):
                self.assertEqual(
                    Claim.get_rights(action), configured_perms("claim", action)
                )
                self.assertTrue(Claim.get_rights(action))

    def test_model_returns_none_for_an_undeclared_action(self):
        """None means "no rule": the caller must fail closed."""
        self.assertIsNone(Claim.get_rights("nosuchaction"))

    def test_model_reads_the_configured_value_not_the_declared_default(self):
        """
        ModuleConfiguration may override a right; the check must read the configured
        value, where `perms()` returns the declared default.
        """
        original = ClaimConfig.gql_query_claims_perms
        try:
            ClaimConfig.gql_query_claims_perms = ["999999"]
            self.assertEqual(Claim.get_rights("query"), ["999999"])
            self.assertEqual(perms("claim", "query"), ["111001"])
        finally:
            ClaimConfig.gql_query_claims_perms = original
