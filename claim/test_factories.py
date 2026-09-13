import factory

from claim.models import ClaimItem, ClaimService


class ClaimItemFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ClaimItem

    qty_provided = 7
    price_asked = 11
    status = 1
    availability = True
    validity_from = "2019-06-01"
    audit_user_id = -1


class ClaimServiceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ClaimService

    qty_provided = 7
    price_asked = 11
    status = 1
    validity_from = "2019-06-01"
    audit_user_id = -1
