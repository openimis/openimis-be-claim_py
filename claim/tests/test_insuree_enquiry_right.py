"""
`insureeNameByChfid` is a scope escape and takes the right that names it.

The resolver queries `Insuree` by CHFID with no location filter, so any CHFID resolves
to that insuree's name wherever in the country they live. It used to be reachable with
`gql_mutation_create_claims_perms` or `gql_mutation_update_claims_perms`, which every
claim clerk holds - so the whole insuree register was readable, one CHFID at a time,
by anyone who could enter a claim.

It now requires `InsureeConfig.gql_query_insuree_inquire_perms` (101105), the enquiry
right: resolving one identifier, anywhere, without the register that 101101 opens. An
id that already exists and is already seeded on the standard roles beats minting a new
one - it had simply never been checked anywhere, which is why leaving the caller's
districts cost nothing. The right lives in insuree, not claim, because the data
exposed is insuree data.

The nationwide reach itself is unchanged and intentional - the right is what
authorises it, not a filter.
"""

import ast
import inspect
import re
import textwrap

from django.test import TestCase

from claim import schema as claim_schema
from claim.apps import ClaimConfig
from insuree.apps import InsureeConfig

PERM_REF = re.compile(r"(?:ClaimConfig|InsureeConfig)\.(\w*perms\w*)")


def _perms_in_resolver(name):
    # dedent: getsource on a method returns it at class indentation, which
    # ast.parse rejects on its own
    source = textwrap.dedent(inspect.getsource(getattr(claim_schema.Query, name)))
    return set(PERM_REF.findall(ast.unparse(ast.parse(source))))


class InsureeEnquiryRightTestCase(TestCase):
    def test_resolver_checks_the_enquiry_right(self):
        self.assertEqual(
            _perms_in_resolver("resolve_insuree_name_by_chfid"),
            {"gql_query_insuree_inquire_perms"},
        )

    def test_resolver_no_longer_checks_the_claim_write_rights(self):
        referenced = _perms_in_resolver("resolve_insuree_name_by_chfid")
        self.assertNotIn("gql_mutation_create_claims_perms", referenced)
        self.assertNotIn("gql_mutation_update_claims_perms", referenced)

    def test_the_enquiry_right_is_readable_and_has_its_own_id(self):
        """
        It was declared in DEFAULT_CFG with no matching attribute, so `__load_config`
        skipped it and reading it raised AttributeError - which is why nothing checked
        it. It is now a declared right, readable and checked.
        """
        self.assertEqual(InsureeConfig.gql_query_insuree_inquire_perms, ["101105"])

    def test_the_id_is_not_shared_with_an_insuree_or_claim_write_right(self):
        """
        The point of using an id of its own: granting it must not drag the register
        or a claim mutation along, and revoking it must not take them away.
        """
        enquiry = set(InsureeConfig.gql_query_insuree_inquire_perms)
        for config in (InsureeConfig, ClaimConfig):
            for attr in dir(config):
                if not attr.endswith("_perms") or attr == "gql_query_insuree_inquire_perms":
                    continue
                value = getattr(config, attr, None)
                if not isinstance(value, (list, tuple)):
                    continue
                with self.subTest(other=f"{config.__name__}.{attr}"):
                    self.assertEqual(enquiry & set(value), set())
