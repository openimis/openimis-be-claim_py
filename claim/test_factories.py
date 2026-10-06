import factory

from claim.models import ClaimAttachment

# A complete payload: the attachment download decodes it and serves the bytes.
base64_text_attachment = "Q2xhaW0gYXR0YWNobWVudCB0ZXN0IGRvY3VtZW50Lg=="


class ClaimAttachmentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ClaimAttachment

    general_type = "FILE"
    title = "Test attachment"
    filename = "attachment.txt"
    mime = "text/plain"
    module = "claim"
    document = base64_text_attachment
    validity_from = "2019-01-01"
