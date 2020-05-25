from claim.models import Claim, ClaimService, ClaimItem, ClaimDedRem
from claim.validations import get_claim_category, approved_amount
from medical.test_helpers import get_item_of_type, get_service_of_category


def create_test_claim(custom_props={}):
    from core import datetime
    return Claim.objects.create(
        **{
            "health_facility_id": 18,
            "icd_id": 116,
            "date_from": datetime.datetime(2019, 6, 1),
            "date_claimed": datetime.datetime(2019, 6, 1),
            "date_to": datetime.datetime(2019, 6, 1),
            "audit_user_id": 1,
            "insuree_id": 2,
            "status": 2,
            "validity_from": datetime.datetime(2019, 6, 1),
            **custom_props
        }
    )


def create_test_claimitem(claim, item_type, valid=True, custom_props={}):
    return ClaimItem.objects.create(
        **{
            "claim": claim,
            "qty_provided": 7,
            "price_asked": 11,
            "item_id": get_item_of_type(item_type).id if item_type else 23,  # Atropine
            "status": 1,
            "availability": True,
            "validity_from": "2019-06-01",
            "validity_to": None if valid else "2019-06-01",
            "audit_user_id": -1,
            **custom_props
           }
    )


def create_test_claimservice(claim, category=None, valid=True, custom_props={}):
    return ClaimService.objects.create(
        **{
            "claim": claim,
            "qty_provided": 7,
            "price_asked": 11,
            "service_id": get_service_of_category(category).id if category else 23,  # Skin graft, no cat
            "status": 1,
            "validity_from": "2019-06-01",
            "validity_to": None if valid else "2019-06-01",
            "audit_user_id": -1,
            **custom_props
        }
    )


def mark_test_claim_as_processed(claim, status=Claim.STATUS_CHECKED, audit_user_id=-1):
    claim.approved = approved_amount(claim)
    claim.status = status
    claim.audit_user_id_submit = audit_user_id
    from core.utils import TimeUtils
    claim.submit_stamp = TimeUtils.now()
    claim.category = get_claim_category(claim)
    claim.save()


def delete_claim_with_itemsvc_dedrem_and_history(claim):
    # first delete old versions of the claim
    ClaimDedRem.objects.filter(claim=claim).delete()
    old_claims = Claim.objects.filter(legacy_id=claim.id)
    ClaimItem.objects.filter(claim__in=old_claims).delete()
    ClaimService.objects.filter(claim__in=old_claims).delete()
    old_claims.delete()
    claim.items.all().delete()
    claim.services.all().delete()
    claim.delete()
