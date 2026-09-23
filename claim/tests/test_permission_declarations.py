"""
Garde-fous sur la declaration des droits de claim.

Meme structure que `core` : `DJANGO_PERMS` par entite puis par action, `_PERM_CFG` qui
en derive les cles de config, et `Claim.get_rights` qui n'est qu'un point d'acces.
L'interet de cette forme sur une liste a plat est que le partage d'identifiant devient
visible : 111010 apparait une fois comme "update" et se retrouve nomme sur chaque action
qui est, de fait, une modification de la reclamation.

Ce qui est verrouille ici, c'est le couple entite/action, pas seulement les valeurs :
  * un identifiant a un seul endroit (DJANGO_PERMS), donc pas de derive entre le
    DEFAULT_CFG et le controle ;
  * une cle de config sans attribut de classe n'est jamais chargee par `__load_config`
    et sa lecture leve AttributeError - le droit devient inapplicable ;
  * `has_perms([])` renvoie True, donc une liste vide accorde a tous.
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

# Les identifiants tels que deployes. En changer un est incompatible avec les roles
# existants : il faut mettre ce test a jour *et* accorder le nouveau droit.
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

# Actions qui partagent volontairement le droit d'une autre.
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
        """`__load_config` ignore les cles sans attribut de classe."""
        missing = [key for key in _PERM_CFG if not hasattr(ClaimConfig, key)]
        self.assertEqual(missing, [])

    def test_no_right_list_is_empty(self):
        empty = [key for key in _PERM_CFG if not getattr(ClaimConfig, key)]
        self.assertEqual(empty, [])

    def test_attributes_carry_the_declared_right(self):
        """
        Les droits sont des constantes posées depuis DJANGO_PERMS : l'attribut doit
        valoir la déclaration, sans passer par la config.
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
                        f"{holder} partage le droit {right_id} sans que ce soit prevu",
                    )

    def test_django_permission_names_are_unique(self):
        """Meme les actions qui partagent un identifiant gardent un nom django distinct."""
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

    # --- le point d'acces par le modele -----------------------------------
    def test_model_exposes_every_action_of_its_entity(self):
        for action in DJANGO_PERMS["claim"]:
            with self.subTest(action=action):
                self.assertEqual(
                    Claim.get_rights(action), configured_perms("claim", action)
                )
                self.assertTrue(Claim.get_rights(action))

    def test_model_returns_none_for_an_undeclared_action(self):
        """None signifie "aucune regle" : l'appelant doit echouer ferme."""
        self.assertIsNone(Claim.get_rights("nosuchaction"))

    def test_model_reads_the_configured_value_not_the_declared_default(self):
        """
        ModuleConfiguration peut surcharger un droit ; le controle doit lire la valeur
        configuree, la ou `perms()` renvoie le defaut declare.
        """
        original = ClaimConfig.gql_query_claims_perms
        try:
            ClaimConfig.gql_query_claims_perms = ["999999"]
            self.assertEqual(Claim.get_rights("query"), ["999999"])
            self.assertEqual(perms("claim", "query"), ["111001"])
        finally:
            ClaimConfig.gql_query_claims_perms = original
