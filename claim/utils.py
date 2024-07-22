from claim.models import ClaimItem, ClaimService, ClaimDetail


def process_child_relation(user, data_children, claim_id, children, create_hook):
    claimed = 0
    from core.utils import TimeUtils
    for data_elt in data_children:
        claimed += data_elt['qty_provided'] * data_elt['price_asked']
        elt_id = data_elt.pop('id') if 'id' in data_elt else None
        if elt_id:
            # elt has been historized along with claim historization
            elt = children.get(id=elt_id)
            [setattr(elt, k, v) for k, v in data_elt.items()]
            elt.validity_from = TimeUtils.now()
            elt.audit_user_id = user.id_for_audit
            elt.claim_id = claim_id
            elt.validity_to = None
            elt.save()
        else:
            data_elt['validity_from'] = TimeUtils.now()
            data_elt['audit_user_id'] = user.id_for_audit
            # Ensure claim id from func argument will be assigned
            data_elt.pop('claim_id', None)
            # Should entered claim items/services have status passed assigned?
            # Status is mandatory field, and it doesn't have default value in model
            data_elt['status'] = ClaimDetail.STATUS_PASSED
            create_hook(claim_id, data_elt)

    return claimed


def item_create_hook(claim_id, item):
    # TODO: investigate 'availability' is mandatory,
    # but not in UI > always true?
    item['availability'] = True
    ClaimItem.objects.create(claim_id=claim_id, **item)


def service_create_hook(claim_id, service):
    ClaimService.objects.create(claim_id=claim_id, **service)


def process_items_relations(user, claim, items):
    return process_child_relation(user, items, claim.id, claim.items, item_create_hook)


def process_services_relations(user, claim, services):
    return process_child_relation(user, services, claim.id, claim.services, service_create_hook)


def convert_date_to_datetime(date):
    from core import datetime
    time = datetime.datetime.min.time()
    new_datetime = datetime.datetime.combine(date, time)
    return new_datetime


CLAIM_ELEMENTS_DECIMAL_FIELDS = [
    "qty_provided",
    "qty_approved",
    "price_asked",
    "price_adjusted",
    "price_approved",
    "price_valuated",
    "limitation_value",
    "remunerated_amount",
    "deductable_amount",
    "exceed_ceiling_amount",
    "exceed_ceiling_amount_category",
]


def clean_review_decimals(data: list):
    # Since the frontend sometimes sends "nan" as a value for decimal fields, and this cannot be stored in MSSQL
    # This will clean input data and remove all Decimal("NaN") values to avoid any DB error
    for claim_element in data:
        for field in CLAIM_ELEMENTS_DECIMAL_FIELDS:
            if field in claim_element and claim_element[field] and claim_element[field].is_nan():
                claim_element.pop(field)
