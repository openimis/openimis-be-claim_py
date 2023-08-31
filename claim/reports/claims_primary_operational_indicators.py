from django.db.models import Q

from location.models import Location, HealthFacility
from product.models import Product

template = """"""


CATEGORY_TOTAL = "T"
CATEGORY_PAID = "P"
CATEGORY_REJECTED = "R"

NO_LOCATION_CODE = "XXXX"
LOCATION_REGIONAL = "Regional"

Q1 = 1
Q2 = 2
Q3 = 3
Q4 = 4
AVAILABLE_QUARTERS = [Q1, Q2, Q3, Q4]

DEFAULT_YEAR = 0
ALL_MONTHS = -1
ALL_QUARTERS = -2
DEFAULT_REGION = -3
ALL_DISTRICTS = -4
ALL_PRODUCTS = -5
ALL_HFS = -6



def generate_subtotal():
    return {
        CATEGORY_TOTAL: 0,
        CATEGORY_PAID: 0,
        CATEGORY_REJECTED: 0,
    }


def claims_primary_operational_indicators_query(user,
                                                requested_month=ALL_MONTHS,
                                                requested_quarter=ALL_QUARTERS,
                                                requested_year=DEFAULT_YEAR,
                                                requested_region_id=DEFAULT_REGION,
                                                requested_district_id=ALL_DISTRICTS,
                                                requested_product_id=ALL_PRODUCTS,
                                                requested_hf_id=ALL_HFS,
                                                **kwargs):
    # Checking the parameters received and returning an error if anything is wrong
    validated_parameters = {}
    month = int(requested_month)
    if month not in range(1, 13) and month != ALL_MONTHS:
        return {"error": "Error - the selected month is invalid"}
    quarter = int(requested_quarter)
    if month not in range(1, 5) and quarter != ALL_QUARTERS:
        return {"error": "Error - the selected quarter is invalid"}
    year = int(requested_year)
    if year not in range(2010, 2100):
        return {"error": "Error - the selected year is invalid"}
    product_id = int(requested_product_id)
    if product_id != ALL_PRODUCTS:
        product = Product.objects.filter(validity_to=None, id=product_id).first()
        if not product:
            return {"error": "Error - the requested product does not exist"}
        validated_parameters["product"] = product
    region_id = int(requested_region_id)
    region = Location.objects.filter(validity_to=None, type='R', id=region_id).first()
    if not region:
        return {"error": "Error - the requested region does not exist"}
    validated_parameters["region"] = region
    district_id = int(requested_district_id)
    if district_id != ALL_DISTRICTS:
        district_filters = Q(validity_to__isnull=True) & Q(type="D") & Q(id=district_id) & Q(parent_id=region_id)
        district = Location.objects.filter(district_filters).first()
        if not district:
            return {"error": "Error - the requested district does not exist"}
        validated_parameters["district"] = district
    hf_id = int(requested_hf_id)
    if hf_id != ALL_HFS:
        hf = HealthFacility.objects.filter(validity_to=None, id=hf_id).first()
        if not hf:
            return {"error": "Error - the requested health facility does not exist"}
        validated_parameters["hf"] = hf



    return {}