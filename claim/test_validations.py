from claim.models import Claim, ClaimDedRem
from claim.test_helpers import create_test_claim, create_test_claimservice, create_test_claimitem, \
    mark_test_claim_as_processed
from claim.validations import get_claim_category, validate_claim, validate_assign_prod_to_claimitems_and_services, \
    process_dedrem
from django.test import TestCase
from insuree.models import Family, Insuree
from insuree.test_helpers import create_test_insuree
from location.models import HealthFacility
from medical.test_helpers import create_test_service, create_test_item
from medical_pricelist.test_helpers import add_service_to_hf_pricelist, add_item_to_hf_pricelist
from policy.test_helpers import create_test_policy

# default arguments should not pass a list or a dict because they're mutable but we don't risk mutating them here:
# noinspection PyDefaultArgument,DuplicatedCode
from product.test_helpers import create_test_product, create_test_product_service, create_test_product_item


class ValidationTest(TestCase):
    service_H = None
    service_O = None
    service_D = None
    service_A = None
    service_A_invalid = None

    def setUp(self) -> None:
        self.service_H = create_test_service("H")
        self.service_O = create_test_service("O")
        self.service_D = create_test_service("D")
        self.service_A = create_test_service("A")
        self.service_A_invalid = create_test_service("A", False)

        self.item_1 = create_test_item("D")

    def tearDown(self) -> None:
        self.service_H.delete()
        self.service_O.delete()
        self.service_D.delete()
        self.service_A.delete()
        self.service_A_invalid.delete()

        self.item_1.delete()

    def test_get_claim_category_S(self):
        # Given
        claim = create_test_claim()
        service1 = create_test_claimservice(claim, "S")

        # when
        category = get_claim_category(claim)

        # then
        self.assertIsNotNone(category)
        self.assertEquals(category, "S")

        # tearDown
        service1.delete()
        claim.delete()

    def test_get_claim_category_D(self):
        # Given
        claim = create_test_claim()
        service1 = create_test_claimservice(claim, "D")

        # when
        category = get_claim_category(claim)

        # then
        self.assertIsNotNone(category)
        self.assertEquals(category, "D")

        # tearDown
        service1.delete()
        claim.delete()

    def test_get_claim_category_mix(self):
        # Given
        claim = create_test_claim()
        services = [
            create_test_claimservice(claim, "H"),
            create_test_claimservice(claim, "O"),
            create_test_claimservice(claim, None),
            create_test_claimservice(claim, "A"),
            create_test_claimservice(claim, "S"),
        ]

        # when
        category = get_claim_category(claim)

        # then
        self.assertIsNotNone(category)
        self.assertEquals(category, "S")

        # tearDown
        for service in services:
            service.delete()
        claim.delete()

    def test_get_claim_category_some_invalid(self):
        # Given
        claim = create_test_claim()
        services = [
            create_test_claimservice(claim, "H", False),
            create_test_claimservice(claim, "A", False),
            create_test_claimservice(claim, "S"),
        ]

        # when
        category = get_claim_category(claim)

        # then
        self.assertIsNotNone(category)
        self.assertEquals(category, "S")

        # tearDown
        for service in services:
            service.delete()
        claim.delete()

    def test_get_claim_category_null(self):
        # Given
        claim = create_test_claim()
        service1 = create_test_claimservice(claim, None)

        # when
        category = get_claim_category(claim)

        # then
        self.assertIsNotNone(category)
        self.assertEquals(category, "V")

        # tearDown
        service1.delete()
        claim.delete()

    # This test requires a valid policy...
    # def test_validate_claim_valid(self):
    #     # Given
    #     claim = create_test_claim()
    #     service1 = create_test_claimservice(claim, "S")
    #     service2 = create_test_claimservice(claim, "S")
    #     item1 = create_test_claimitem(claim, "D")
    #     item2 = create_test_claimitem(claim, "D")

    #     # When
    #     errors = validate_claim(claim, True)
    #     # Then
    #     self.assertEquals(len(errors), 0, "The claim should be fully valid")

    #     # tearDown
    #     service1.delete()
    #     service2.delete()
    #     item1.delete()
    #     item2.delete()
    #     claim.delete()

    # This test cannot be performed because the database constraints don't allow a null date_from.
    # def test_validate_claim_target_date(self):
    #     # Given
    #     claim = create_test_claim(custom_props={"date_from": None, "date_to": None})
    #     service1 = create_test_claimservice(claim, "S")
    #
    #     # When
    #     errors = validate_claim(claim)
    #
    #     # Then
    #     self.assertEquals(len(errors), 1, "The claim should fail the target date validation")
    #     self.assertEquals(errors[0].code, 9, "The claim should fail the target date validation with code 9")
    #     self.assertTrue("date" in errors[0].message.lower())
    #
    #     # tearDown
    #     service1.delete()
    #     claim.delete()

    def test_validate_pricelist_hf1(self):
        # When the claimitem points to a pricelist that doesn't correspond to the claim HF
        hf_without_pricelist = HealthFacility.objects.filter(items_pricelist__id__isnull=True).first()
        self.assertIsNotNone(hf_without_pricelist, "This test requires a health facility without a price list item")
        # Given
        claim = create_test_claim({"health_facility_id": hf_without_pricelist.id})
        service1 = create_test_claimservice(claim, "S")
        item1 = create_test_claimitem(claim, "D")

        # When
        errors = validate_claim(claim, True)

        # Then
        claim.refresh_from_db()
        service1.refresh_from_db()
        item1.refresh_from_db()
        self.assertGreaterEqual(len(errors), 1, "Should raise at least one error")
        error1 = [e for e in errors if e['code'] == 1]  # all services rejected
        self.assertGreaterEqual(len(error1), 1, "There should be an error code 1")
        self.assertEquals(item1.rejection_reason, 2, "Database was updated with rejection reason")

        # tearDown
        service1.delete()
        item1.delete()
        claim.delete()

    def test_validate_family(self):
        # When the insuree family is invalid
        # Given
        invalid_insuree = Insuree.objects.filter(family__in=Family.objects.filter(validity_to__isnull=False)).first()
        self.assertIsNotNone(invalid_insuree)
        claim = create_test_claim({"insuree_id": invalid_insuree.id})
        service1 = create_test_claimservice(claim, "S")
        item1 = create_test_claimitem(claim, "D")

        # When
        errors = validate_claim(claim, True)

        # Then
        claim.refresh_from_db()
        item1.refresh_from_db()
        self.assertGreaterEqual(len(errors), 1, "Should raise at least one error")
        error7 = [e for e in errors if e['code'] == 7]
        self.assertGreaterEqual(len(error7), 1, "There should be 1 error code 7: invalid insuree")
        self.assertEquals(item1.rejection_reason, 7, "Database was updated with rejection reason")

        # tearDown
        service1.delete()
        item1.delete()
        claim.delete()

    def test_validate_max_visits(self):
        # When the insuree already reaches his limit of visits
        # Given
        insuree = create_test_insuree()
        self.assertIsNotNone(insuree)
        product = create_test_product("VISIT", custom_props={"max_no_visits": 1})
        policy = create_test_policy(product, insuree, link=True)
        service = create_test_service("V")
        product_service = create_test_product_service(product, service)
        pricelist_detail = add_service_to_hf_pricelist(service)

        # A first claim for a visit should be accepted
        claim1 = create_test_claim({"insuree_id": insuree.id})
        service1 = create_test_claimservice(claim1, custom_props={"service_id": service.id})
        errors = validate_claim(claim1, True)
        self.assertEquals(len(errors), 0, "The first visit should be accepted")

        # a second visit should be denied
        claim2 = create_test_claim({"insuree_id": insuree.id})
        service2 = create_test_claimservice(claim2, "V")
        errors = validate_claim(claim2, True)
        # TODO Temporarily disabled
        # self.assertGreater(len(errors), 0, "The second visit should be refused")

        # Then
        claim1.refresh_from_db()
        claim2.refresh_from_db()

        # tearDown
        service2.delete()
        claim2.delete()
        service1.delete()
        claim1.delete()
        policy.insuree_policies.first().delete()
        policy.delete()
        product_service.delete()
        pricelist_detail.delete()
        service.delete()
        product.delete()

    def test_validate_patient_category(self):
        # When the insuree already reaches his limit of visits
        # Given
        insuree = create_test_insuree()
        self.assertIsNotNone(insuree)
        product = create_test_product("VISIT", custom_props={"max_no_visits": 1})
        policy = create_test_policy(product, insuree, link=True)
        service = create_test_service("V", custom_props={"patient_category": 1})
        product_service = create_test_product_service(product, service)
        pricelist_detail = add_service_to_hf_pricelist(service)

        # The insuree has a patient_category of 6, not matching the service category
        claim1 = create_test_claim({"insuree_id": insuree.id})
        service1 = create_test_claimservice(claim1, custom_props={"service_id": service.id})
        errors = validate_claim(claim1, True)

        # Then
        claim1.refresh_from_db()
        self.assertEquals(len(errors), 1)
        self.assertEquals(errors[0]['code'], 1)  # claimed rejected because all services are rejected
        self.assertEquals(claim1.services.first().rejection_reason, 4)  # reason is wrong insuree mask

        # tearDown
        service1.delete()
        claim1.delete()
        policy.insuree_policies.first().delete()
        policy.delete()
        product_service.delete()
        pricelist_detail.delete()
        service.delete()
        product.delete()

    def test_frequency(self):
        # When the insuree already reaches his limit of visits
        # Given
        insuree = create_test_insuree()
        self.assertIsNotNone(insuree)
        product = create_test_product("CSECT")
        policy = create_test_policy(product, insuree, link=True)
        service = create_test_service("C", custom_props={"code": "G34B", "frequency": 180})
        product_service = create_test_product_service(product, service)
        pricelist_detail = add_service_to_hf_pricelist(service)

        # A first claim for a visit should be accepted
        claim1 = create_test_claim({"insuree_id": insuree.id})
        service1 = create_test_claimservice(claim1, custom_props={"service_id": service.id})
        errors = validate_claim(claim1, True)
        mark_test_claim_as_processed(claim1)

        self.assertEquals(len(errors), 0, "The first visit should be accepted")

        # a second visit should be denied
        claim2 = create_test_claim({"insuree_id": insuree.id})
        service2 = create_test_claimservice(claim2, custom_props={"service_id": service.id})
        errors = validate_claim(claim2, True)
        self.assertGreater(len(errors), 0, "The second service should be refused as it is withing 180 days")

        # Then
        claim1.refresh_from_db()
        claim2.refresh_from_db()

        # tearDown
        service2.delete()
        claim2.delete()
        service1.delete()
        claim1.delete()
        policy.insuree_policies.first().delete()
        policy.delete()
        product_service.delete()
        pricelist_detail.delete()
        service.delete()
        product.delete()

    def test_submit_claim_dedrem(self):
        # When the insuree already reaches his limit of visits
        # Given
        insuree = create_test_insuree()
        self.assertIsNotNone(insuree)
        product = create_test_product("VISIT", custom_props={})
        policy = create_test_policy(product, insuree, link=True)
        service = create_test_service("V", custom_props={})
        item = create_test_item("D", custom_props={})
        #product_service = create_test_product_service(product, service)
        product_item = create_test_product_item(product, item)
        #pricelist_detail1 = add_service_to_hf_pricelist(service)
        pricelist_detail2 = add_item_to_hf_pricelist(item)

        # The insuree has a patient_category of 6, not matching the service category
        claim1 = create_test_claim({"insuree_id": insuree.id})
        #service1 = create_test_claimservice(claim1, custom_props={"service_id": service.id})
        item1 = create_test_claimitem(claim1, "D", custom_props={"item_id": item.id, "product": product, "policy": policy})
        errors = validate_claim(claim1, True)
        errors += validate_assign_prod_to_claimitems_and_services(claim1)
        foo = process_dedrem(claim1, -1, True)

        # Then
        claim1.refresh_from_db()
        item1.refresh_from_db()
        self.assertEqual(len(errors), 0)
        self.assertEqual(item1.price_adjusted, 100)
        self.assertEqual(item1.price_valuated, 700)
        self.assertEqual(item1.deductable_amount, 0)
        self.assertEqual(item1.exceed_ceiling_amount, 0)
        self.assertIsNone(item1.exceed_ceiling_amount_category)
        self.assertEqual(item1.remunerated_amount, 700)
        self.assertEqual(claim1.status, Claim.STATUS_VALUATED)
        self.assertEqual(claim1.audit_user_id_process, -1)
        self.assertIsNotNone(claim1.process_stamp)
        self.assertIsNotNone(claim1.date_processed)

        dedrem_qs = ClaimDedRem.objects.filter(claim=claim1)
        self.assertEqual(dedrem_qs.count(), 1)
        dedrem1 = dedrem_qs.first()
        self.assertEqual(dedrem1.policy_id, item1.policy_id)
        self.assertEqual(dedrem1.insuree_id, claim1.insuree_id)
        self.assertEqual(dedrem1.audit_user_id, -1)
        self.assertEqual(dedrem1.ded_g, 0)
        self.assertEqual(dedrem1.rem_g, 700)
        self.assertIsNotNone(claim1.validity_from)
        self.assertIsNone(claim1.validity_to)

        # tearDown
        dedrem_qs.delete()
        #service1.delete()
        item1.delete()
        claim1.delete()
        policy.insuree_policies.first().delete()
        policy.delete()
        product_item.delete()
        #product_service.delete()
        #pricelist_detail1.delete()
        pricelist_detail2.delete()
        service.delete()
        item.delete()
        product.delete()

