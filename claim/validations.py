import itertools
import logging
from collections import namedtuple

from claim.models import ClaimItem, Claim, ClaimService, ClaimDedRem, ClaimDetail, ClaimServiceService, ClaimServiceItem

from core import utils
from datetime import datetime
from core.datetimes.shared import datetimedelta
from core.utils import filter_validity
from django.db import connection
from django.db.models import Sum, Q, ExpressionWrapper, DecimalField
from django.db.models.functions import Coalesce
from django.utils.translation import gettext as _
from insuree.models import InsureePolicy
from medical.models import Service, ServiceService, ServiceItem
from medical_pricelist.models import ItemsPricelistDetail, ServicesPricelistDetail
from policy.models import Policy
from product.models import Product, ProductItem, ProductService, ProductItemOrService

from .apps import ClaimConfig
from .utils import get_queryset_valid_at_date, get_valid_policies_qs, get_claim_target_date, approved_amount
logger = logging.getLogger(__name__)

REJECTION_REASON_INVALID_ITEM_OR_SERVICE = 1
REJECTION_REASON_NOT_IN_PRICE_LIST = 2
REJECTION_REASON_NO_PRODUCT_FOUND = 3
REJECTION_REASON_CATEGORY_LIMITATION = 4
REJECTION_REASON_FREQUENCY_FAILURE = 5
# REJECTION_REASON_DUPLICATED = 6
REJECTION_REASON_FAMILY = 7
# REJECTION_REASON_ICD_NOT_IN_LIST = 8
REJECTION_REASON_TARGET_DATE = 9
REJECTION_REASON_CARE_TYPE = 10
REJECTION_REASON_MAX_HOSPITAL_ADMISSIONS = 11
REJECTION_REASON_MAX_VISITS = 12
REJECTION_REASON_MAX_CONSULTATIONS = 13
REJECTION_REASON_MAX_SURGERIES = 14
REJECTION_REASON_MAX_DELIVERIES = 15
REJECTION_REASON_QTY_OVER_LIMIT = 16
REJECTION_REASON_WAITING_PERIOD_FAIL = 17
REJECTION_REASON_MAX_ANTENATAL = 19
REJECTION_REASON_INVALID_CLAIM = 20
REJECTION_REASON_NO_COVERAGE = 21


def validate_claim(claim, check_max, policies=None):
    """
    Based on the legacy validation, this method returns standard codes along with details
    :param claim: claim to be verified
    :param check_max: max amount to validate. Everything above will be rejected
    :return: (result_code, error_details)
    """
    logger.debug(f"Validating claim {claim.uuid}")
    if ClaimConfig.default_validations_disabled:
        return []
    errors = []
    detail_errors = []
    errors += validate_target_date(claim)
    if len(errors) == 0:
        errors += validate_insuree(claim, claim.insuree, policies)
    if len(errors) == 0:
        detail_errors += validate_claimitems(claim)
        detail_errors += validate_claimservices(claim)

    if len(errors) == 0 and check_max:
        # we went over the maximum for a category, all items and services in the claim are rejected
        over_category_errors = [
            x for x in detail_errors if x['code'] in [REJECTION_REASON_MAX_HOSPITAL_ADMISSIONS,
                                                      REJECTION_REASON_MAX_VISITS,
                                                      REJECTION_REASON_MAX_CONSULTATIONS,
                                                      REJECTION_REASON_MAX_SURGERIES,
                                                      REJECTION_REASON_MAX_DELIVERIES,
                                                      REJECTION_REASON_MAX_ANTENATAL]]
        if len(over_category_errors) > 0:
            rtn_items_rejected = claim.items.filter(validity_to__isnull=True) \
                .update(status=ClaimItem.STATUS_REJECTED,
                        qty_approved=0,
                        rejection_reason=over_category_errors[0]['code'])
            rtn_services_rejected = claim.services.filter(validity_to__isnull=True) \
                .update(status=ClaimService.STATUS_REJECTED,
                        qty_approved=0,
                        rejection_reason=over_category_errors[0]['code'])
        else:
            rtn_items_rejected = claim.items.filter(validity_to__isnull=True) \
                .exclude(rejection_reason=0).exclude(rejection_reason__isnull=True) \
                .update(status=ClaimItem.STATUS_REJECTED, qty_approved=0)
            rtn_services_rejected = claim.services.filter(validity_to__isnull=True) \
                .exclude(rejection_reason=0).exclude(rejection_reason__isnull=True) \
                .update(status=ClaimService.STATUS_REJECTED, qty_approved=0)
        if rtn_items_rejected or rtn_services_rejected:
            logger.debug(f"Marked {rtn_items_rejected} items as rejected and {rtn_services_rejected} services")

    rtn_items_passed = claim.items.filter(validity_to__isnull=True) \
        .exclude(status=ClaimItem.STATUS_REJECTED) \
        .update(status=ClaimItem.STATUS_PASSED)
    rtn_services_passed = claim.services.filter(validity_to__isnull=True) \
        .exclude(status=ClaimService.STATUS_REJECTED) \
        .update(status=ClaimService.STATUS_PASSED)

    if rtn_items_passed + rtn_services_passed == 0:
        errors += [{'code': REJECTION_REASON_INVALID_ITEM_OR_SERVICE,
                    'message': _("claim.validation.all_items_and_services_rejected") % {
                        'code': claim.code},
                    'detail': claim.uuid}]
        if len(detail_errors)>0:
            errors += detail_errors
        claim.status = Claim.STATUS_REJECTED
        claim.rejection_reason = REJECTION_REASON_INVALID_ITEM_OR_SERVICE
        claim.save()
    logger.debug(f"Validation found {len(errors)} error(s)")
    return errors


def validate_claimitems(claim, save=True):
    errors = []
    target_date = claim.date_from if claim.date_from else claim.date_to
    for claimitem in claim.items.all():
        if not claimitem.rejection_reason:
            errors += validate_claimitem_validity(claim, claimitem)
            if not claimitem.rejection_reason:
                errors += validate_claimitem_in_price_list(claim, claimitem)
            if not claimitem.rejection_reason:
                errors += validate_claimdetail_care_type(claim, claimitem)
            if not claimitem.rejection_reason:
                errors += validate_claimdetail_limitation_fail(claim, claimitem)
            if not claimitem.rejection_reason:
                errors += validate_claimitem_frequency(claim, claimitem)
            if not claimitem.rejection_reason:
                errors += validate_item_product_family(
                    claimitem=claimitem,
                    target_date=target_date,
                    item=claimitem.item,
                    insuree_id=claim.insuree_id,
                    adult=claim.insuree.is_adult(target_date)
                )
            if claimitem.rejection_reason:
                claimitem.status = ClaimItem.STATUS_REJECTED
            else:
                claimitem.rejection_reason = 0
                claimitem.status = ClaimItem.STATUS_PASSED
            if save:
                claimitem.save()
    if errors:
        pass
    return errors


def validate_claimservices(claim, save=True):
    errors = []
    target_date = get_claim_target_date(claim)
    base_category = get_claim_category(claim)
    
    for claimservice in claim.services.all():
        if not claimservice.rejection_reason:
            errors += validate_claimservice_validity(claim, claimservice)
            if not claimservice.rejection_reason:
                errors += validate_claimservice_in_price_list(claim, claimservice)
            if not claimservice.rejection_reason:
                errors += validate_claimdetail_care_type(claim, claimservice)
            if not claimservice.rejection_reason:
                errors += validate_claimservice_frequency(claim, claimservice)
            if not claimservice.rejection_reason:
                errors += validate_claimdetail_limitation_fail(claim, claimservice)
            if not claimservice.rejection_reason:
                errors += validate_service_product_family(
                    claimservice=claimservice,
                    target_date=target_date,
                    service=claimservice.service,
                    insuree_id=claim.insuree_id,
                    adult=claim.insuree.is_adult(target_date),
                    base_category=base_category,
                    claim=claim,
                )
            if claimservice.rejection_reason:
                claimservice.status = ClaimService.STATUS_REJECTED
            else:
                claimservice.rejection_reason = 0
                claimservice.status = ClaimService.STATUS_PASSED
            if save:
                claimservice.save()
    return errors


def validate_claimitem_validity(claim, claimitem):
    # In the stored procedure, this check used a complex query to get the latest item but the latest item seems to
    # always be updated.
    # select *
    # from tblClaimItems tCI inner join tblItems tI on tCI.ItemID = tI.ItemID
    # where ti.ValidityTo is not null and tI.LegacyID is not null;
    # gives no result, so no claimitem is pointing to an old item and the complex query always fetched the last one.
    # Here, claimitem.item.legacy_id is always None
    errors = []
    if claimitem.validity_to is None and claimitem.item.validity_to is not None:
        claimitem.rejection_reason = REJECTION_REASON_INVALID_ITEM_OR_SERVICE
        errors += [{'code': REJECTION_REASON_INVALID_ITEM_OR_SERVICE,
                    'message': _("claim.validation.claimitem_validity") % {
                        'code': claim.code
                    },
                    'detail': claim.uuid}]
    return errors


def validate_claimservice_validity(claim, claimservice):
    # See note in validate_claimitem_validity
    errors = []
    if claimservice.validity_to is None and claimservice.service.validity_to is not None:
        claimservice.rejection_reason = REJECTION_REASON_INVALID_ITEM_OR_SERVICE
        errors += [{'code': REJECTION_REASON_INVALID_ITEM_OR_SERVICE,
                    'message': _("claim.validation.claimservice_validity") % {
                        'code': claim.code
                    },
                    'detail': claim.uuid}]
    return errors




def validate_claimitem_in_price_list(claim, claimitem):
    errors = []
    target_date = get_claim_target_date(claim)
    pricelist_detail_qs = ItemsPricelistDetail.objects \
        .filter(item_id=claimitem.item_id,
                validity_to__isnull=True,
                items_pricelist=claim.health_facility.items_pricelist,
                items_pricelist__validity_to__isnull=True
                )
    pricelist_detail = get_queryset_valid_at_date(pricelist_detail_qs, target_date).first()
    if not pricelist_detail:
        claimitem.rejection_reason = REJECTION_REASON_NOT_IN_PRICE_LIST
        errors += [{'code': REJECTION_REASON_NOT_IN_PRICE_LIST,
                    'message': _("claim.validation.claimitem_in_price_list_validity") % {
                        'code': claim.code
                    },
                    'detail': claim.uuid}]
    return errors


def validate_claimservice_in_price_list(claim, claimservice):
    errors = []
    target_date = get_claim_target_date(claim)
    pricelist_detail_qs = ServicesPricelistDetail.objects \
        .filter(service_id=claimservice.service_id,
                services_pricelist=claim.health_facility.services_pricelist,
                services_pricelist__validity_to__isnull=True
                )
    pricelist_detail = get_queryset_valid_at_date(pricelist_detail_qs, target_date).first()
    if not pricelist_detail:
        claimservice.rejection_reason = REJECTION_REASON_NOT_IN_PRICE_LIST
        errors += [{'code': REJECTION_REASON_NOT_IN_PRICE_LIST,
                    'message': _("claim.validation.claimservice_in_price_list_validity") % {
                        'code': claim.code
                    },
                    'detail': claim.uuid}]
    return errors


def validate_claimdetail_care_type(claim, claimdetail):
    errors = []
    care_type = claimdetail.itemsvc.care_type
    hf_care_type = claim.health_facility.care_type if claim.health_facility.care_type else 'B'
    target_date = get_claim_target_date(claim)
    # itm should work
    # in a B facility : inpatient / out patient and all
    # in a O facility : out patient O
    # in a I facility : inpatient I
    inpatient = target_date != claim.date_from
    
    if (
        (hf_care_type == 'O' and inpatient) or
        (hf_care_type == 'O' and care_type == 'I') or
        (hf_care_type == 'I' and care_type == 'O')
    ):
        claimdetail.rejection_reason = REJECTION_REASON_CARE_TYPE
        errors += [{'code': REJECTION_REASON_CARE_TYPE,
                    'message': _("claim.validation.claimdetail_care_type_validity") % {
                        'code': claim.code
                    },
                    'detail': claim.uuid}]
    return errors


def validate_claimdetail_limitation_fail(claim, claimdetail):
    # if the mask is empty, it should be valid for everyone
    if claimdetail.itemsvc.patient_category == 0:
        return []
    errors = []
    target_date = get_claim_target_date(claim)
    patient_category_mask = utils.patient_category_mask(
        claim.insuree, target_date)
    
    if claimdetail.itemsvc.patient_category & patient_category_mask != patient_category_mask:
        claimdetail.rejection_reason = REJECTION_REASON_CATEGORY_LIMITATION
        errors += [{'code': REJECTION_REASON_CATEGORY_LIMITATION,
                    'message': _("claim.validation.claimdetail_limitation_validity") % {
                        'code': claim.code
                    },
                    'detail': claim.uuid}]
    return errors


def frequency_check(qs, claim, elt):
    td = claim.date_from if not claim.date_to else claim.date_to
    delta = datetimedelta(days=elt.frequency)
    return qs \
        .annotate(target_date=Coalesce("claim__date_to", "claim__date_from")) \
        .filter(Q(rejection_reason=0) | Q(rejection_reason__isnull=True),
                validity_to__isnull=True,
                target_date__gte=td - delta,
                status=ClaimDetail.STATUS_PASSED,
                claim__insuree_id=claim.insuree_id,
                claim__status__gt=Claim.STATUS_ENTERED
                ) \
        .exclude(claim__uuid=claim.uuid) \
        .exists()


def validate_claimitem_frequency(claim, claimitem):
    errors = []
    if claimitem.item.frequency and \
            frequency_check(ClaimItem.objects.filter(item=claimitem.item), claim, claimitem.item):
        claimitem.rejection_reason = REJECTION_REASON_FREQUENCY_FAILURE
        errors += [{'code': REJECTION_REASON_FREQUENCY_FAILURE,
                    'message': _("claim.validation.claimitem_frequency_validity") % {
                        'code': claim.code
                    },
                    'detail': claim.uuid}]
    return errors


def validate_claimservice_frequency(claim, claimservice):
    errors = []
    if claimservice.service.frequency and \
            frequency_check(ClaimService.objects.filter(service=claimservice.service), claim, claimservice.service):
        claimservice.rejection_reason = REJECTION_REASON_FREQUENCY_FAILURE
        errors += [{'code': REJECTION_REASON_FREQUENCY_FAILURE,
                    'message': _("claim.validation.claimservice_frequency_validity") % {
                        'code': claim.code
                    },
                    'detail': claim.uuid}]
    return errors


def validate_target_date(claim):
    errors = []
    if (claim.date_from is None and claim.date_to is None) \
            or claim.date_claimed < claim.date_from:
        claim.reject(REJECTION_REASON_TARGET_DATE)
        errors += [{'code': REJECTION_REASON_TARGET_DATE,
                    'message': _("claim.validation.target_date") % {
                        'code': claim.code
                    },
                    'detail': claim.uuid}]
    return errors

# policies param is used to avoid too much query the database
def validate_insuree(claim, insuree, policies=None):
    errors = []
    if insuree.validity_to is not None:
        errors += [{'code': REJECTION_REASON_FAMILY,
                    'message': _("claim.validation.family.insuree_validity") % {
                        'code': claim.code,
                        'insuree': str(insuree)},
                    'detail': claim.uuid}]
    if not policies and not InsureePolicy.objects.filter(
        insuree=insuree,
        effective_date__lte=claim.date_from,
        expiry_date__gte=claim.date_to or claim.date_from,
        *filter_validity()):
        errors += [{'code': REJECTION_REASON_NO_COVERAGE,
                    'message': _("claim.validation.family.no_policy") % {
                        'code': claim.code,
                        'insuree': str(insuree)},
                    'detail': claim.uuid}]
    if len(errors) > 0:
        claim.reject(REJECTION_REASON_FAMILY)
    return errors


def validate_item_product_family(claimitem, target_date, item, insuree_id, adult):
    errors = []
    found = False
    with get_products(target_date, item.id, insuree_id, adult, 'Item') as cursor:
        for (product_id, product_item_id, insuree_policy_effective_date, policy_effective_date, expiry_date,
             policy_stage) in cursor.fetchall():
            found = True
            core = __import__("core")
            insuree_policy_effective_date = core.datetime.date.from_ad_date(
                insuree_policy_effective_date)
            expiry_date = core.datetime.date.from_ad_date(expiry_date)
            product_item = ProductItem.objects.get(pk=product_item_id)
            # START CHECK 17 --> Item/Service waiting period violation (17)
            errors = check_service_item_waiting_period(policy_stage, policy_effective_date,
                                                       insuree_policy_effective_date,
                                                       item, adult, product_item, target_date, claimitem)

            # **** START CHECK 16 --> Item/Service Maximum provision (16)*****
            errors += check_service_item_max_provision(adult, product_item, item, insuree_policy_effective_date,
                                                       expiry_date, insuree_id, claimitem)
        if not found:
            claimitem.rejection_reason = REJECTION_REASON_NO_PRODUCT_FOUND
            errors += [{'code': REJECTION_REASON_NO_PRODUCT_FOUND,
                        'message': _("claim.validation.product_family.no_product_found") % {
                            'code': claimitem.claim.code,
                            'element': str(item)},
                        'detail': claimitem.claim.uuid}]

    return errors


# noinspection DuplicatedCode
def validate_service_product_family(claimservice, target_date, service, insuree_id, adult, base_category, claim):
    errors = []
    found = False
    with get_products(target_date, service.id, insuree_id, adult, 'Service') as cursor:
        for (product_id, product_service_id, insuree_policy_effective_date, policy_effective_date, expiry_date,
             policy_stage) in cursor.fetchall():
            found = True
            core = __import__("core")
            insuree_policy_effective_date = core.datetime.date.from_ad_date(
                insuree_policy_effective_date)
            expiry_date = core.datetime.date.from_ad_date(expiry_date)
            product_service = ProductService.objects.get(pk=product_service_id)

            # START CHECK 17 --> Item/Service waiting period violation (17)
            errors += check_service_item_waiting_period(policy_stage, policy_effective_date,
                                                        insuree_policy_effective_date, service, adult,
                                                        product_service, target_date, claimservice)

            # **** START CHECK 16 --> Item/Service Maximum provision (16)*****
            errors += check_service_item_max_provision(adult, product_service, service, insuree_policy_effective_date,
                                                       expiry_date, insuree_id, claimservice)

            # Each violation is meant to interrupt the validation
            error_len = len(errors)
            product = Product.objects.filter(pk=product_id).first()
            if base_category != 'O':
                errors += check_claim_max_no_category(base_category, product, expiry_date, insuree_id,
                                                      insuree_policy_effective_date, claim, claimservice)
                if error_len != len(errors):
                    break

        if not found:
            claimservice.rejection_reason = REJECTION_REASON_NO_PRODUCT_FOUND
            errors += [{'code': REJECTION_REASON_NO_PRODUCT_FOUND,
                        'message': _("claim.validation.product_family.no_product_found") % {
                            'code': claimservice.claim.code,
                            'element': str(service)},
                        'detail': claimservice.claim.uuid}]

    return errors

def check_service_item_waiting_period(policy_stage, policy_effective_date, insuree_policy_effective_date, service_or_item,
                                 adult, product_service_item, target_date, claim_service_item):
    errors = []
    waiting_period = None
    if policy_stage == 'N' or policy_effective_date < insuree_policy_effective_date:
        if adult:
            waiting_period = product_service_item.waiting_period_adult
        else:
            waiting_period = product_service_item.waiting_period_child
    if waiting_period and target_date < \
            (insuree_policy_effective_date.to_datetime() + datetimedelta(months=waiting_period)):
        claim_service_item.rejection_reason = REJECTION_REASON_WAITING_PERIOD_FAIL
        errors += [{'code': REJECTION_REASON_WAITING_PERIOD_FAIL,
                    'message': _("claim.validation.product_family.waiting_period") % {
                        'code': claim_service_item.claim.code,
                        'element': str(service_or_item)},
                    'detail': claim_service_item.claim.uuid}]
    return errors


def check_service_item_max_provision(adult, product_service_item, service_or_item, insuree_policy_effective_date,
                                     expiry_date, insuree_id, claim_service_item):
    errors = []
    if adult:
        limit_no = product_service_item.limit_no_adult
    else:
        limit_no = product_service_item.limit_no_child
    if limit_no is not None and limit_no >= 0:
        # count qty provided
        total_qty_provided = _get_total_qty_provided(claim_service_item, service_or_item, insuree_policy_effective_date,
                                                     expiry_date, insuree_id)
        qty = total_qty_provided + claim_service_item.qty_provided if claim_service_item.qty_approved is None \
                                                                   else claim_service_item.qty_approved
        if qty > limit_no:
            # it would be good to add a warning msg, here is a related ticket: OTC-943
            if total_qty_provided < limit_no:
                remaining_qty = limit_no - total_qty_provided
                if claim_service_item.qty_approved is None:
                    claim_service_item.qty_provided = remaining_qty
                else:
                    claim_service_item.qty_approved = remaining_qty
                claim_service_item.save()
            else:
                claim_service_item.rejection_reason = REJECTION_REASON_QTY_OVER_LIMIT
                errors += [{'code': REJECTION_REASON_QTY_OVER_LIMIT,
                            'message': _("claim.validation.product_family.max_nb_allowed") % {
                                'code': claim_service_item.claim.code,
                                'element': str(service_or_item),
                                'provided': total_qty_provided,
                                'max': limit_no},
                            'detail': claim_service_item.claim.uuid}]

    return errors


def _get_total_qty_provided(claim_service_item, service_or_item, insuree_policy_effective_date,
                            expiry_date, insuree_id):
    return claim_service_item.__class__.objects \
            .annotate(target_date=Coalesce("claim__date_to", "claim__date_from")) \
            .filter(Q(rejection_reason=0) | Q(rejection_reason__isnull=True),
                    validity_to__isnull=True,
                    **{
                        f"{'service' if isinstance(service_or_item, Service) else 'item'}_id": service_or_item.id},
                    policy__validity_to__isnull=True,
                    target_date__gte=insuree_policy_effective_date,
                    target_date__lte=expiry_date,
                    claim__insuree_id=insuree_id,
                    claim__status__gt=Claim.STATUS_ENTERED,
                    claim__validity_to__isnull=True
                    ) \
            .aggregate(total_qty_provided=Sum(Coalesce("qty_approved", "qty_provided"))) \
            .get("total_qty_provided") or 0

def check_claim_max_no_category(base_category, product, expiry_date, insuree_id,
                                insuree_policy_effective_date, claim, claimservice):
    errors = []
    category_dict = {
        'C': {'max': product.max_no_consultation,
              "reason": REJECTION_REASON_MAX_CONSULTATIONS,
              "message": "claim.validation.product_family.max_nb_consultation"},
        'S': {"max": product.max_no_surgery,
              "reason": REJECTION_REASON_MAX_SURGERIES,
              "message": "claim.validation.product_family.max_nb_surgeries"},
        'D': {"max": product.max_no_delivery,
              "reason": REJECTION_REASON_MAX_DELIVERIES,
              "message": "claim.validation.product_family.max_nb_deliveries"},
        'A': {"max": product.max_no_antenatal,
              "reason": REJECTION_REASON_MAX_ANTENATAL,
              "message": "claim.validation.product_family.max_nb_antenatal"},
        'H': {"max": product.max_no_hospitalization,
              "reason": REJECTION_REASON_MAX_HOSPITAL_ADMISSIONS,
              "message": "claim.validation.product_family.max_nb_hospitalizations"},
        'V': {"max": product.max_no_visits,
              "reason": REJECTION_REASON_MAX_VISITS,
              "message": "claim.validation.product_family.max_nb_visits"},
    }.get(base_category)

    if category_dict['max'] is not None and category_dict['max'] >= 0:
        count = get_claim_queryset_by_category(expiry_date, insuree_id, insuree_policy_effective_date, base_category, claim) \
            .count()
        if count and count >= category_dict['max']:
            claimservice.rejection_reason = category_dict['reason']
            errors += [{'code': category_dict['reason'],
                        'message': _(category_dict['message']) % {
                            'code': claimservice.claim.code,
                            'count': count,
                            'max': category_dict['max']},
                        'detail': claimservice.claim.uuid}]
    return errors


def get_claim_queryset_by_category(expiry_date, insuree_id, insuree_policy_effective_date, category, claim=None):
    queryset = Claim.objects \
        .annotate(target_date=Coalesce("date_to", "date_from")) \
        .filter(insuree_id=insuree_id,
                validity_to__isnull=True,
                status__gt=Claim.STATUS_ENTERED,
                target_date__gte=insuree_policy_effective_date,
                target_date__lte=expiry_date)
    if claim:
        queryset = queryset.exclude(uuid=claim.uuid)
    if category == 'V':
        queryset = queryset.filter(
            Q(category=category) | Q(category__isnull=True))
    else:
        queryset = queryset.filter(category=category)
    return queryset


def get_products(target_date, elt_id, insuree_id, adult, item_or_service):
    cursor = connection.cursor()
    waiting_period = "WaitingPeriodAdult" if adult else "WaitingPeriodChild"
    # about deductions and ceilings...
    # tblProduct.DedInsuree, tblProduct.DedOPInsuree, tblProduct.DedIPInsuree,
    # tblProduct.MaxInsuree, tblProduct.MaxOPInsuree, tblProduct.MaxIPInsuree,
    # tblProduct.DedTreatment, tblProduct.DedOPTreatment, tblProduct.DedIPTreatment,
    # tblProduct.MaxTreatment, tblProduct.MaxOPTreatment, tblProduct.MaxIPTreatment,
    # tblProduct.DedPolicy, tblProduct.DedOPPolicy, tblProduct.DedIPPolicy,
    # tblProduct.MaxPolicy, tblProduct.MaxOPPolicy, tblProduct.MaxIPPolicy
    if connection.vendor == "postgresql":
        sql = f"""
                    SELECT 
                        "tblProduct"."ProdID", "tblProduct{item_or_service}s"."Prod{item_or_service}ID",            
                        "tblInsureePolicy"."EffectiveDate",
                        "tblPolicy"."EffectiveDate",
                        CASE
                            WHEN "tblPolicy"."ExpiryDate" < "tblInsureePolicy"."ExpiryDate" THEN "tblPolicy"."ExpiryDate"
                            ELSE "tblInsureePolicy"."ExpiryDate"
                        END AS "ExpiryDate",
                        "tblPolicy"."PolicyStage"
                    FROM "tblInsuree" 
                        INNER JOIN "tblInsureePolicy" ON "tblInsureePolicy"."InsureeID" = "tblInsuree"."InsureeID"
                        LEFT OUTER JOIN "tblPolicy"
                        LEFT OUTER JOIN "tblProduct" ON "tblPolicy"."ProdID" = "tblProduct"."ProdID"
                        INNER JOIN "tblProduct{item_or_service}s" 
                            ON "tblProduct"."ProdID" = "tblProduct{item_or_service}s"."ProdID"            
                        RIGHT OUTER JOIN "tblFamilies" ON "tblPolicy"."FamilyID" = "tblFamilies"."FamilyID"
                        ON "tblInsuree"."FamilyID" = "tblFamilies"."FamilyID"
                    WHERE ("tblInsuree"."ValidityTo" IS NULL) AND ("tblInsuree"."InsureeID" = %s)
                        AND ("tblInsureePolicy"."PolicyId" = "tblPolicy"."PolicyID")
                        AND ("tblPolicy"."ValidityTo" IS NULL)
                        AND ("tblPolicy"."EffectiveDate" <= %s) AND ("tblPolicy"."ExpiryDate" >= %s)
                        AND ("tblInsureePolicy"."ValidityTo" IS NULL)
                        AND ("tblInsureePolicy"."EffectiveDate" <= %s) AND ("tblInsureePolicy"."ExpiryDate" >= %s)
                        AND ("tblPolicy"."PolicyStatus" in ({Policy.STATUS_ACTIVE}, {Policy.STATUS_EXPIRED}))
                        AND ("tblProduct{item_or_service}s"."ValidityTo" IS NULL) 
                            AND ("tblProduct{item_or_service}s"."{item_or_service}ID" = %s)
                    ORDER BY "tblPolicy"."EffectiveDate" + coalesce("tblProduct{item_or_service}s"."{waiting_period}", 0) 
                        * INTERVAL '1 MONTH'            
                """
    else:
        sql = f"""
            SELECT 
                tblProduct.ProdID, tblProduct{item_or_service}s.Prod{item_or_service}ID,            
                tblInsureePolicy.EffectiveDate,
                tblPolicy.EffectiveDate,
                CASE
                    WHEN tblPolicy.ExpiryDate < tblInsureePolicy.ExpiryDate THEN tblPolicy.ExpiryDate
                    ELSE tblInsureePolicy.ExpiryDate
                END AS ExpiryDate,
                tblPolicy.PolicyStage
            FROM tblInsuree 
                INNER JOIN tblInsureePolicy ON tblInsureePolicy.InsureeID = tblInsuree.InsureeID
                LEFT OUTER JOIN tblPolicy
                LEFT OUTER JOIN  tblProduct ON tblPolicy.ProdID = tblProduct.ProdID
                INNER JOIN tblProduct{item_or_service}s ON tblProduct.ProdID = tblProduct{item_or_service}s.ProdID            
                RIGHT OUTER JOIN tblFamilies ON tblPolicy.FamilyID = tblFamilies.FamilyID
                ON tblInsuree.FamilyID = tblFamilies.FamilyID
            WHERE (tblInsuree.ValidityTo IS NULL) AND (tblInsuree.InsureeId = %s)
                AND (tblInsureePolicy.PolicyId = tblPolicy.PolicyID)
                AND (tblPolicy.ValidityTo IS NULL)
                AND (tblPolicy.EffectiveDate <= %s) AND (tblPolicy.ExpiryDate >= %s)
                AND (tblInsureePolicy.ValidityTo IS NULL)
                AND (tblInsureePolicy.EffectiveDate <= %s) AND (tblInsureePolicy.ExpiryDate >= %s)
                AND (tblPolicy.PolicyStatus in ({Policy.STATUS_ACTIVE}, {Policy.STATUS_EXPIRED}))
                AND (tblProduct{item_or_service}s.ValidityTo IS NULL) AND (tblProduct{item_or_service}s.{item_or_service}ID = %s)
            ORDER BY DATEADD(m,ISNULL(tblProduct{item_or_service}s.{waiting_period}, 0),
                tblPolicy.EffectiveDate)            
        """
    cursor.execute(sql,
                   [insuree_id, target_date, target_date, target_date, target_date, elt_id])
    return cursor


def get_claim_category(claim):
    """
    Determine the claim category based on its services:
    S = Surgery
    D = Delivery
    A = Antenatal care
    H = Hospitalization
    C = Consultation
    O = Other
    V = Visit
    :param claim: claim for which category should be determined
    :return: category if a service is defined, None if not service at all
    """

    service_categories = [
        Service.CATEGORY_SURGERY,
        Service.CATEGORY_DELIVERY,
        Service.CATEGORY_ANTENATAL,
        Service.CATEGORY_HOSPITALIZATION,
        Service.CATEGORY_CONSULTATION,
        Service.CATEGORY_OTHER,
        Service.CATEGORY_VISIT,
    ]
    target_date = get_claim_target_date(claim)
    services = claim.services \
        .filter(validity_to__isnull=True, service__validity_to__isnull=True) \
        .values("service__category").distinct()
    claim_service_categories = [
        service["service__category"]
        for service in services
    ]
    if claim.date_from != target_date:
        claim_service_categories.append(Service.CATEGORY_HOSPITALIZATION)
    for category in service_categories:
        if category in claim_service_categories:
            claim_category = category
            break
    else:
        # One might expect "O" here but the legacy code uses "V"
        claim_category = Service.CATEGORY_VISIT

    return claim_category


def find_best_product_etl(product_elt_c, product_elt_f, fixed_limit,
    claim_price, co_sharing_percent ):
    if product_elt_c and product_elt_f:
        if fixed_limit == 0 or fixed_limit > claim_price:
            product_elt = product_elt_f
            product_elt_c = None  # used in condition below
        else:
            if 100 - co_sharing_percent > 0:
                product_amount_own_f = claim_price - fixed_limit
                product_amount_own_c = (1 - co_sharing_percent / 100) * claim_price
                if product_amount_own_c > product_amount_own_f:
                    product_elt = product_elt_f
                    product_elt_c = None  # used in condition below
                else:
                    product_elt = product_elt_c
            else:
                product_elt = product_elt_c
    else:
        if product_elt_c:
            product_elt = product_elt_c
        else:
            product_elt = product_elt_f
            product_elt_c = None
    
    return product_elt

def validate_assign_prod_elt(claim, elt, elt_ref, elt_qs, target_date, policies=None):
    """
    This method checks the limits for the family and the insuree, child or adult for their limits
    between the copay percentage and fixed limit.
    :return: List of errors, only if no product could be found for now
    """
    visit_type_field = {
        "O": ("limitation_type", "limit_adult", "limit_child"),
        "E": ("limitation_type_e", "limit_adult_e", "limit_child_e"),
        "R": ("limitation_type_r", "limit_adult_r", "limit_child_r"),
    }
    logger.debug("[claim: %s] Assigning product for %s %s", claim.uuid, type(elt), elt.id)
    visit_type = claim.visit_type if claim.visit_type and claim.visit_type in visit_type_field else "O"
    adult = claim.insuree.is_adult(target_date)
    (limitation_type_field, limit_adult, limit_child) = visit_type_field[visit_type]
    claim_price = elt.price_approved or elt.price_adjusted or elt.price_asked or 0
    logger.debug("[claim: %s] claim_price: %s", claim.uuid, claim_price)
    logger.debug("[claim: %s] Checking product itemsvc limit at date %s  with field %s C for adult: %s",
                 claim.uuid, target_date, limitation_type_field, adult)
    product_elt_c = _query_product_item_service_limit(
        target_date, elt_qs, limitation_type_field, "C",
        limit_adult if adult else limit_child
    )

    product_elt_f = _query_product_item_service_limit(
        target_date, elt_qs, limitation_type_field, "F",
        limit_adult if adult else limit_child
    )
    
    if not product_elt_c and not product_elt_f:
        elt.rejection_reason = REJECTION_REASON_NO_PRODUCT_FOUND
        elt.save()
        return [{'code': REJECTION_REASON_NO_PRODUCT_FOUND,
                 'message': _("claim.validation.assign_prod.elt.no_product_code") % {
                     'code': claim.code,
                     'elt': str(elt_ref)},
                 'detail': claim.uuid}]

    if product_elt_f:
        fixed_limit = getattr(
            product_elt_f, limit_adult if adult else limit_child)
        logger.debug("[claim: %s] fixed_limit: %s", claim.uuid, fixed_limit)
    else:
        fixed_limit = None

    if product_elt_c:
        co_sharing_percent = getattr(
            product_elt_c, limit_adult if adult else limit_child)
        logger.debug("[claim: %s] co_sharing_percent: %s", claim.uuid, co_sharing_percent)
    else:
        co_sharing_percent = None

    # if both products exist, find the best one to use
    product_elt = find_best_product_etl(
        product_elt_c,
        product_elt_f,
        fixed_limit,
        claim_price,
        co_sharing_percent
    )

    if product_elt is None:
        logger.warning(f"Could not find a suitable product from {type(elt)} {elt.id}")
    if product_elt.product_id is None:
        logger.warning(f"Found a productItem/Service for {type(elt)} {elt.id} but it does not have a product")
    logger.debug("[claim: %s] product_id found: %s", claim.uuid, product_elt.product_id)
    elt.product = product_elt.product
    logger.debug("[claim: %s] fetching policy for family %s", claim.uuid, claim.insuree.family_id)
    elt.policy = next(iter([p for p in policies if p.product == product_elt.product]), None)
    if elt.policy is None:
        logger.warning(f"{type(elt)} id {elt.id} doesn't seem to have a valid policy with product"
                       f" {product_elt.product_id}")
    logger.debug("[claim: %s] setting policy %s", claim.uuid, elt.policy.id if elt.policy else None)
    elt.price_origin = product_elt.price_origin
    # The original code also sets claimservice.price_adjusted but it also always NULL
    if product_elt_c:
        elt.limitation = "C"
        elt.limitation_value = co_sharing_percent
    else:
        elt.limitation = "F"
        elt.limitation_value = fixed_limit
    logger.debug("[claim: %s] setting limitation %s to %s", claim.uuid, elt.limitation, elt.limitation_value)
    elt.save()
    return []


def validate_assign_prod_to_claimitems_and_services(claim, policies=None, services=None, items=None):
    errors = []
    target_date = get_claim_target_date(claim)
    if not policies:
        policies = get_valid_policies_qs(claim.insuree.id, target_date)
    logger.debug("[claim: %s] validate_assign_prod_to_claimitems_and_services", claim.uuid)
    if items is None:
        items = list(
            claim.items.filter(validity_to__isnull=True) 
            .filter(Q(rejection_reason=0) | Q(rejection_reason__isnull=True))
        )
    if services is None:
        services = list(
            claim.services.filter(validity_to__isnull=True) 
            .filter(Q(rejection_reason=0) | Q(rejection_reason__isnull=True))
        )
    for claimitem in [i for i in items if not i.rejection_reason]:
        logger.debug("[claim: %s] validating item %s", claim.uuid, claimitem.id)
        errors += validate_assign_prod_elt(
            claim, claimitem, claimitem.item,
            ProductItem.objects.filter(
                item_id=claimitem.item_id, 
                product__in=[p.product for p in policies]
            ),
            target_date=target_date,
            policies=policies)

    for claimservice in [s for s in services if not s.rejection_reason]:
        logger.debug("[claim: %s] validating service %s", claim.uuid, claimservice.id)
        errors += validate_assign_prod_elt(
            claim, claimservice, claimservice.service,
            ProductService.objects.filter(
                service_id=claimservice.service_id, 
                product__in=[p.product for p in policies]
            ),
            target_date=target_date,
            policies=policies
        )

    logger.debug("[claim: %s] validate_assign_prod_to_claimitems_and_services nb of errors %s", claim.uuid, len(errors))
    return errors



def _query_product_item_service_limit(target_date, elt_qs,
                                      limitation_field, limitation_type,
                                      limit_ordering):
    pdt_elt = elt_qs \
        .filter(validity_to__isnull=True,
                product__validity_to__isnull=True,
                **{limitation_field: limitation_type}
                ) \
        .order_by("-" + limit_ordering) \
        .first()
    logger.debug("product found: %s, checking product itemsvc limit at date %s  "
                "with field %s (%s)",  pdt_elt is not None, target_date,
                limitation_field, limitation_type)
    return pdt_elt


Deductible = namedtuple('Deductible', ['amount', 'type', 'prev'])


def _get_dedrem(prefix, dedrem_type, field, product, insuree, demrems):
    if getattr(product, prefix + "_treatment", None):
        return Deductible(
            getattr(product, prefix + "_treatment", None),
            dedrem_type,
            0
        )
    if getattr(product, prefix + "_insuree", None):
        prev = sum([getattr(dr, field, 0)\
            for dr in demrems if dr.insuree_id == insuree.id])
        return Deductible(
            getattr(product, prefix + "_insuree", None),
            dedrem_type,
            prev if prev else 0
        )
    if getattr(product, prefix + "_policy", None):
        prev = sum([getattr(dr, field, 0) for dr in demrems])
        return Deductible(
            getattr(product, prefix + "_policy", None),
            dedrem_type,
            prev if prev else 0
        )
    return None


# This method is replicating the step 2 of the stored procedure mostly as-is. It will be refactored in several steps.
# The overall process is:
# - Check each product associated with the claim, compute ceilings and maxes
# - Go through each item and deduce
# - Go through each service and deduce
def process_dedrem(claim, audit_user_id=-1, is_process=False, policies=None, items=None, services=None):
    errors = []
    logger.debug(f"processing dedrem for claim {claim.uuid}")
    target_date = get_claim_target_date(claim)
    category = get_claim_category(claim)
    if claim.date_from != target_date:
        hospitalization = True
    else:
        hospitalization = False
    hf_level = claim.health_facility.level
    deducted = 0
    remunerated = 0
    # archiving old demrem
    ClaimDedRem.objects.filter(claim_id=claim.id, *filter_validity()).update(validity_to=datetime.now())

    # TODO: it is not clear in the original code which policy_id was actually used, the latest one apparently...
    if not policies:
        policies = get_valid_policies_qs(claim.insuree.id, target_date)

    # The original code has a pretty complex query here called product_loop that refers to policies while it is
    # actually looping on ClaimItem and ClaimService.
    if items is None:
        items = list(claim.items.filter(
            item__isnull=False,
            product__isnull=False,
            validity_to__isnull=True,
        ).filter(
            Q(Q(rejection_reason=0) | Q(rejection_reason__isnull=True))
        ))
    if services is None:
        services = list(claim.services.filter(
            service__isnull=False,
            product__isnull=False,
            validity_to__isnull=True,
        ).filter(
            Q(Q(rejection_reason=0) | Q(rejection_reason__isnull=True))
        ))
    
    policies_id = list(set((*[s.policy_id for s in services if s.policy_id is not None],
                      *[i.policy_id for i in items if i.policy_id is not None],)))
    products_id = list(set(p.product_id for p in policies if p.id in policies_id))
    products = list(Product.objects.filter(*filter_validity(validity=target_date),
                                   Q(Q(id__in=products_id) | Q(legacy_id__in=products_id))))
    for policy_id in policies_id:
        policy_members = InsureePolicy.objects.filter(
            policy_id=policy_id,
            effective_date__isnull=False,
            effective_date__lte=target_date,
            expiry_date__gte=target_date,
            validity_to__isnull=True
        ).count()
    
        policy = next(iter([p for p in policies if p.id == policy_id]), None)
        product = next(iter([p for p in products if p.id == policy.product_id]), None)
        

        hospital_visit = ( 
            product.ceiling_interpretation == Product.CEILING_INTERPRETATION_IN_PATIENT 
            and hospitalization == 1
        ) or (
            product.ceiling_interpretation == Product.CEILING_INTERPRETATION_HOSPITAL 
            and hf_level == "H"
        )
        deductible = None
        ceiling = None
        # In previous stored procedure, some commented code fetched the amounts from sum(RemDelivery) from
        # tblClaimDedRem where policy_id, insuree_id & claim_id <> this one
        prev_deductible = None
        prev_remunerated = 0
        prev_remunerated_consult = 0
        prev_remunerated_surgery = 0
        prev_remunerated_hospitalization = 0
        prev_remunerated_delivery = 0
        prev_remunerated_antenatal = 0
        remunerated_consultation = 0
        remunerated_surgery = 0
        remunerated_hospitalization = 0
        remunerated_delivery = 0
        remunerated_antenatal = 0
        relative_prices = False

        demrems = list(
            ClaimDedRem.objects.filter(
                policy_id=policy_id
            ).exclude(
                claim_id=claim.id
            )
        )
        

        ded_g = _get_dedrem("ded", "G", "ded_g", product, claim.insuree, demrems)
        if ded_g:
            deductible = ded_g
            prev_deductible = deductible.prev

        rem_g = _get_dedrem("max", "G", "rem_g", product, claim.insuree, demrems)
        if rem_g:
            ceiling = rem_g
            prev_remunerated = rem_g.prev
        if product.max_policy:
            if policy_members > product.threshold:  # Threshold is NOT NULL
                if product.max_policy_extra_member:
                    ceiling = Deductible(
                        product.max_policy + (policy_members - product.threshold) * product.max_policy_extra_member,
                        ceiling.type,
                        ceiling.prev)
                if product.max_ceiling_policy and ceiling.amount > product.max_ceiling_policy:
                    ceiling = Deductible(
                        product.max_ceiling_policy,
                        ceiling.type,
                        ceiling.prev)
            else:
                ceiling = Deductible(
                    product.max_policy,
                    ceiling.type,
                    ceiling.prev)

        # Then check IP deductibles
        if not deductible:
            if hospital_visit:
                # Hospital IP
                ded_ip = _get_dedrem("ded_ip", "I", "ded_ip", product, claim.insuree, demrems)
                if ded_ip:
                    deductible = ded_ip
                    prev_deductible = ded_ip.prev
            else:
                #  OP
                ded_op = _get_dedrem("ded_op", "O", "ded_op", product, claim.insuree, demrems)
                if ded_op:
                    deductible = ded_op
                    prev_deductible = ded_op.prev

        if not ceiling:
            if hospital_visit:
                max_ip = _get_dedrem("max_ip", "I", "rem_ip", product, claim.insuree, demrems)
                if max_ip:
                    ceiling = max_ip
                    prev_remunerated = max_ip.prev
                if product.max_ip_policy:
                    if policy_members > product.threshold:  # Threshold is NOT NULL
                        if product.max_policy_extra_member_ip:
                            ceiling = Deductible(
                                product.max_ip_policy + (
                                        policy_members - product.threshold) * product.max_policy_extra_member_ip,
                                ceiling.type,
                                ceiling.prev
                            )
                        if product.max_ceiling_policy_ip and ceiling.amount > product.max_ceiling_policy_ip:
                            ceiling = Deductible(
                                product.max_ceiling_policy_ip,
                                ceiling.type,
                                ceiling.prev
                            )
                    else:
                        ceiling = Deductible(
                            product.max_ip_policy,
                            ceiling.type,
                            ceiling.prev
                        )
            else:
                max_op = _get_dedrem("max_op", "O", "rem_op", product, claim.insuree, demrems)
                if max_op:
                    ceiling = max_op
                    prev_remunerated = max_op.prev
                if product.max_op_policy:
                    if product.threshold and policy_members > product.threshold:
                        if product.max_policy_extra_member_op:
                            ceiling = Deductible(
                                product.max_op_policy + (
                                        policy_members - product.threshold) * product.max_policy_extra_member_op,
                                ceiling.type,
                                ceiling.prev
                            )
                        if product.max_ceiling_policy_op and ceiling.amount > product.max_ceiling_policy_op:
                            ceiling = Deductible(
                                product.max_ceiling_policy_op,
                                ceiling.type,
                                ceiling.prev
                            )
                    else:
                        ceiling = Deductible(
                            product.max_op_policy,
                            ceiling.type,
                            ceiling.prev
                        )
                        
        

        # Loop through items
        deducted = 0
        remunerated = 0
        for claim_detail in [
            *[i for i in items if i.status==ClaimItem.STATUS_PASSED],
            *[s for s in services if s.status==ClaimService.STATUS_PASSED],
        ]:


            detail_is_item = isinstance(claim_detail, ClaimItem)
            itemsvc_quantity = claim_detail.qty_approved or claim_detail.qty_provided
            set_price_deducted = 0
            exceed_ceiling_amount = 0
            exceed_ceiling_amount_category = 0
            # TODO make sure that this does not return more than one row ?
            itemsvc_pricelist_detail_qs = (ItemsPricelistDetail if detail_is_item else ServicesPricelistDetail).objects \
                .filter(itemsvcs_pricelist=claim.health_facility.items_pricelist
            if detail_is_item else claim.health_facility.services_pricelist,
                        itemsvc=claim_detail.itemsvc,
                        itemsvcs_pricelist__validity_to__isnull=True,
                        )
            itemsvc_pricelist_detail = get_queryset_valid_at_date(itemsvc_pricelist_detail_qs, target_date).first()
            product_itemsvc = None

            if detail_is_item:
                product_itemsvc = ProductItem.objects.filter(
                    product_id=claim_detail.product_id,
                    item_id=claim_detail.item_id,
                    validity_to__isnull=True
                ).first()
                if product_itemsvc is None:
                    raise ValueError("Product Item not found")
            else:
                product_itemsvc = ProductService.objects.filter(
                    product=claim_detail.product,
                    service_id=claim_detail.service_id,
                    validity_to__isnull=True
                ).first()
                if product_itemsvc is None:
                    raise ValueError("Product Service not found")

            pl_price = itemsvc_pricelist_detail.price_overrule if itemsvc_pricelist_detail.price_overrule \
                else claim_detail.itemsvc.price

            if claim_detail.price_approved is not None:
                
                set_price_adjusted = claim_detail.price_approved
            if claim_detail.price_origin == ProductItemOrService.ORIGIN_CLAIM:
                set_price_adjusted = claim_detail.price_asked
                if ClaimConfig.native_code_for_services == False:
                    if not detail_is_item:
                        service_price = None
                        if claim_detail.service.packagetype == 'F':
                            service_price = claim_detail.service.price
                        if service_price and (claim_detail.price_adjusted or claim_detail.price_asked) > service_price:
                            set_price_adjusted = service_price
            else:
                set_price_adjusted = pl_price
                if ClaimConfig.native_code_for_services == False:
                    if not detail_is_item:
                        contunue_service_check = True
                        if claim_detail.service.packagetype == 'P':
                            service_services = ServiceService.objects.filter(parent=claim_detail.service.id).all()
                            claim_service_services = ClaimServiceService.objects.filter(claim_service=claim_detail.id).all()
                            if len(service_services) == len(claim_service_services):
                                for servservice in service_services:
                                    for claimserviceservice in claim_service_services:
                                        if servservice.service.id == claimserviceservice.service.id:
                                            value_to_compare = claimserviceservice.qty_displayed
                                            if claimserviceservice.qty_adjusted is not None:
                                                value_to_compare = claimserviceservice.qty_adjusted
                                            logger.debug(f"comparing serviceservice qty {servservice.qty_provided}\
                                                and claimserviceservice qty {value_to_compare}")
                                            if servservice.qty_provided != value_to_compare:
                                                set_price_adjusted = 0
                                                contunue_service_check = False
                                                break
                                    if contunue_service_check == False:
                                        break
                            else:
                                # user misconfiguration !
                                set_price_adjusted = 0
                                contunue_service_check = False
                            logger.debug(f"set_price_adjusted after service check {set_price_adjusted}")
                            if contunue_service_check:
                                contunue_item_check = True
                                service_items = ServiceItem.objects.filter(parent=claim_detail.service.id).all()
                                claim_service_items = ClaimServiceItem.objects.filter(claim_service=claim_detail.id).all()
                                logger.debug(f"service_items: {service_items}")
                                logger.debug(f"claim_service_items: {claim_service_items}")
                                if len(service_items) == len(claim_service_items):
                                    for serviceitem in service_items:
                                        for claimservicesitem in claim_service_items:
                                            if serviceitem.item.id == claimservicesitem.item.id:
                                                value_to_compare = claimservicesitem.qty_displayed
                                                if claimservicesitem.qty_adjusted is not None:
                                                    value_to_compare = claimservicesitem.qty_adjusted
                                                logger.debug(f"comparing serviceitem qty {serviceitem.qty_provided}\
                                                 and claimservicesitem qty {value_to_compare}")
                                                if serviceitem.qty_provided != value_to_compare:
                                                    set_price_adjusted = 0
                                                    contunue_item_check = False
                                                    break
                                        if contunue_item_check == False:
                                            break
                                else:
                                    # user misconfiguration !
                                    set_price_adjusted = 0
                                logger.debug(f"set_price_adjusted after items check {set_price_adjusted}")

            work_value = int(itemsvc_quantity * set_price_adjusted)
            set_unit_price_adjusted  = set_price_adjusted
            if claim_detail.limitation == ProductItemOrService.LIMIT_FIXED_AMOUNT \
                    and claim_detail.limitation_value \
                    and (itemsvc_quantity * claim_detail.limitation_value) < work_value:
                work_value = itemsvc_quantity * claim_detail.limitation_value

            if deductible and deductible.amount - prev_deductible - deducted > 0:
                if deductible.amount - deductible.prev - deducted >= work_value:
                    set_price_deducted = work_value
                    deducted += work_value
                    # remunerated += 0 # why ?
                    set_price_valuated = 0
                    set_price_remunerated = 0
                else:
                    # partial coverage
                    set_price_deducted = deductible.amount - deductible.prev - deducted
                    work_value -= set_price_deducted
                    deducted += deductible.amount - deductible.prev - deducted

            if claim_detail.limitation == ProductItemOrService.LIMIT_CO_INSURANCE and claim_detail.limitation_value:
                work_value = claim_detail.limitation_value / 100 * work_value

            if category != Service.CATEGORY_VISIT:
                if product.max_amount_surgery and category == Service.CATEGORY_SURGERY:
                    if work_value + prev_remunerated_surgery + remunerated_surgery <= product.max_amount_surgery:
                        remunerated_surgery += work_value
                    else:
                        if prev_remunerated_surgery + remunerated_surgery >= product.max_amount_surgery:
                            exceed_ceiling_amount_category = work_value
                            # remunerated_surgery += 0
                            work_value = 0
                        else:
                            exceed_ceiling_amount_category = work_value + prev_remunerated_surgery \
                                                             + remunerated_surgery - product.max_amount_surgery
                            work_value -= exceed_ceiling_amount_category
                            remunerated_surgery += work_value
                if product.max_amount_delivery and category == Service.CATEGORY_DELIVERY:
                    if work_value + prev_remunerated_delivery + remunerated_delivery <= product.max_amount_delivery:
                        remunerated_delivery += work_value
                    else:
                        if prev_remunerated_delivery + remunerated_delivery >= product.max_amount_delivery:
                            exceed_ceiling_amount_category = work_value
                            # remunerated_delivery += 0
                            work_value = 0
                        else:
                            exceed_ceiling_amount_category = work_value + prev_remunerated_delivery \
                                                             + remunerated_delivery - product.max_amount_delivery
                            work_value -= exceed_ceiling_amount_category
                            remunerated_delivery += work_value
                if product.max_amount_antenatal and category == Service.CATEGORY_ANTENATAL:
                    if work_value + prev_remunerated_antenatal + remunerated_antenatal <= product.max_amount_antenatal:
                        remunerated_antenatal += work_value
                    else:
                        if prev_remunerated_antenatal + remunerated_antenatal >= product.max_amount_antenatal:
                            exceed_ceiling_amount_category = work_value
                            # remunerated_antenatal += 0
                            work_value = 0
                        else:
                            exceed_ceiling_amount_category = work_value + prev_remunerated_antenatal \
                                                             + remunerated_antenatal - product.max_amount_antenatal
                            work_value -= exceed_ceiling_amount_category
                            remunerated_antenatal += work_value
                if product.max_amount_hospitalization and category == Service.CATEGORY_HOSPITALIZATION:
                    if work_value + prev_remunerated_hospitalization + remunerated_hospitalization \
                            <= product.max_amount_hospitalization:
                        remunerated_hospitalization += work_value
                    else:
                        if prev_remunerated_hospitalization + remunerated_hospitalization \
                                >= product.max_amount_hospitalization:
                            exceed_ceiling_amount_category = work_value
                            # remunerated_hospitalization += 0
                            work_value = 0
                        else:
                            exceed_ceiling_amount_category = work_value + prev_remunerated_hospitalization \
                                                             + remunerated_hospitalization \
                                                             - product.max_amount_hospitalization
                            work_value -= exceed_ceiling_amount_category
                            remunerated_hospitalization += work_value
                if product.max_amount_consultation and category == Service.CATEGORY_CONSULTATION:
                    if work_value + prev_remunerated_consult + remunerated_consultation \
                            <= product.max_amount_consultation:
                        remunerated_consultation += work_value
                    else:
                        if prev_remunerated_consult + remunerated_consultation >= product.max_amount_consultation:
                            exceed_ceiling_amount_category = work_value
                            # remunerated_consult += 0
                            work_value = 0
                        else:
                            exceed_ceiling_amount_category = work_value + prev_remunerated_consult \
                                                             + remunerated_consultation - product.max_amount_consultation
                            work_value -= exceed_ceiling_amount_category
                            remunerated_consultation += work_value

            # TODO big rework of this condition is needed. putting the ceiling_exclusion_? into a variable as first step

                
 
            if product_itemsvc is not None and ((
                  claim.insuree.is_adult and hospital_visit    
                  and product_itemsvc.ceiling_exclusion_adult in ("B", "H")
            ) or (claim.insuree.is_adult and not hospital_visit
                  and product_itemsvc.ceiling_exclusion_adult in ("B", "N")
            ) or (not claim.insuree.is_adult and hospital_visit
                  and product_itemsvc.ceiling_exclusion_child in ("B", "H")
            ) or (not claim.insuree.is_adult and not hospital_visit
                  and product_itemsvc.ceiling_exclusion_child in ("B", "N")
            )):
                # NO CEILING WILL BE AFFECTED
                exceed_ceiling_amount = 0
                # remunerated += 0
                # here in this case we do not add the amount to be added to the
                # ceiling --> so exclude from the actual value to be entered against the insert into tblClaimDedRem
                # in the end of the prod loop
                set_price_valuated = work_value
                set_price_remunerated = work_value
            else:
                if ceiling and ceiling.amount > 0:
                    if ceiling.amount - prev_remunerated - remunerated > 0:
                        if ceiling.amount - prev_remunerated - remunerated >= work_value:
                            exceed_ceiling_amount = 0
                            set_price_valuated = work_value
                            set_price_remunerated = work_value
                            remunerated += work_value
                        else:
                            total = ceiling.amount - prev_remunerated - remunerated
                            exceed_ceiling_amount = work_value - total
                            set_price_valuated = total
                            set_price_remunerated = total
                            remunerated += total
                    else:
                        exceed_ceiling_amount = work_value
                        # remunerated += 0
                        set_price_valuated = 0
                        set_price_remunerated = 0
                else:
                    exceed_ceiling_amount = 0
                    remunerated += work_value
                    set_price_valuated = work_value
                    set_price_remunerated = work_value
            # if price adjusted not using approved price in its calculation
            if claim_detail.price_approved is None:
                claim_detail.price_adjusted = set_unit_price_adjusted
            # TODO here was NextItem target. Some "goto nextitem" above might have been replaced with a continue instead
            if is_process:
                if claim_detail.price_origin == ProductItemOrService.ORIGIN_RELATIVE:
                    claim_detail.price_valuated = None
                    claim_detail.deductable_amount = set_price_deducted
                    claim_detail.exceed_ceiling_amount = exceed_ceiling_amount
                    # TODO ExceedCeilingAmountCategory = ExceedCeilingAmountCategory ???
                    relative_prices = True
                else:
                    claim_detail.price_valuated = set_price_valuated
                    claim_detail.deductable_amount = set_price_deducted
                    claim_detail.exceed_ceiling_amount = exceed_ceiling_amount
                    # TODO ExceedCeilingAmountCategory = ExceedCeilingAmountCategory ???
                    claim_detail.remunerated_amount = set_price_remunerated
                    # Don't touch relative_prices
            claim_detail.save()

        now = datetime.now()
        claim_ded_rem_to_create = {
            "policy": policy,
            "insuree": claim.insuree,
            "claim": claim,
            "ded_g": deducted,
            "rem_g": remunerated,
            "rem_consult": remunerated_consultation,
            "rem_hospitalization": remunerated_hospitalization,
            "rem_delivery": remunerated_delivery,
            "rem_antenatal": remunerated_antenatal,
            "rem_surgery": remunerated_surgery,
            "audit_user_id": audit_user_id,
            "validity_from": now
        }
        if hospital_visit:
            claim_ded_rem_to_create["ded_ip"] = deducted
            claim_ded_rem_to_create["rem_ip"] = remunerated
        else:
            claim_ded_rem_to_create["ded_op"] = deducted
            claim_ded_rem_to_create["rem_op"] = remunerated

        ClaimDedRem.objects.create(**claim_ded_rem_to_create)

        if is_process:
            claim.valuated = remunerated
            if relative_prices:
                claim.status = Claim.STATUS_PROCESSED
            else:
                claim.status = Claim.STATUS_VALUATED
                claim.remunerated = remunerated
            claim.audit_user_id_process = audit_user_id
            claim.process_stamp = now
            claim.date_processed = now
            if claim.feedback_status == Claim.FEEDBACK_SELECTED:
                claim.feedback_status = Claim.FEEDBACK_BYPASSED
            if claim.review_status == Claim.REVIEW_SELECTED:
                claim.review_status = Claim.REVIEW_BYPASSED

            
 
    if not products:
        logger.warning(f"claim {claim.uuid} did not have any item or service to valuate.")
        claim.status = Claim.STATUS_REJECTED
        errors += [{'code': REJECTION_REASON_NO_PRODUCT_FOUND,
                            'message': _("claim.validation.product_family.no_product_found") % {
                            'code': claim.code,
                            'element': 'all'},
                        'detail': claim.uuid}]

        # amount is 'locked' from the submit
        # ... so re-creating the ClaimDedRem according to adjusted/valuated price
    claim.save()    
    return errors  # process_dedrem will never put the claim in error status (beside technical error and until it changes)
