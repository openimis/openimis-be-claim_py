from django.db import connection
import xml.etree.ElementTree as ET
from django.core.exceptions import PermissionDenied


class ClaimElementSubmit(object):
    def __init__(self, type, code, quantity, price=None):
        self.type = type
        self.code = code
        self.price = price
        self.quantity = quantity

    def add_to_xmlelt(self, xmlelt):
        item = ET.SubElement(xmlelt, self.type)
        ET.SubElement(item, "%sCode" % self.type).text = "%s" % self.code
        if self.price:
            ET.SubElement(item, "%sPrice" % self.type).text = "%s" % self.price
        ET.SubElement(item, "%sQuantity" %
                      self.type).text = "%s" % self.quantity


class ClaimItemSubmit(ClaimElementSubmit):
    def __init__(self, code, quantity, price=None):
        super().__init__(type='Item',
                         code=code,
                         price=price,
                         quantity=quantity)


class ClaimServiceSubmit(ClaimElementSubmit):
    def __init__(self, code, quantity, price=None):
        super().__init__(type='Service',
                         code=code,
                         price=price,
                         quantity=quantity)


class ClaimSubmit(object):
    def __init__(self, date, code, icd_code, total, start_date,
                 insuree_chf_id, health_facility_code,
                 claim_admin_code,
                 item_submits=None, service_submits=None,
                 end_date=None,
                 icd_code_1=None, icd_code_2=None, icd_code_3=None, icd_code_4=None,
                 visit_type=None, guarantee_no=None,
                 comment=None,
                 ):
        self.date = date
        self.code = code
        self.icd_code = icd_code
        self.total = total
        self.start_date = start_date
        self.insuree_chf_id = insuree_chf_id
        self.health_facility_code = health_facility_code
        self.end_date = end_date
        self.icd_code_1 = icd_code_1
        self.icd_code_2 = icd_code_2
        self.icd_code_3 = icd_code_3
        self.icd_code_4 = icd_code_4
        self.claim_admin_code = claim_admin_code
        self.visit_type = visit_type
        self.guarantee_no = guarantee_no
        self.comment = comment
        self.items = item_submits
        self.services = service_submits

    def _details_to_xmlelt(self, xmlelt):
        ET.SubElement(xmlelt, 'ClaimDate').text = self.date.to_ad_date().strftime(
            "%d/%m/%Y")
        ET.SubElement(
            xmlelt, 'HFCode').text = "%s" % self.health_facility_code
        if self.claim_admin_code:
            ET.SubElement(
                xmlelt, 'ClaimAdmin').text = "%s" % self.claim_admin_code
        ET.SubElement(xmlelt, 'ClaimCode').text = "%s" % self.code
        ET.SubElement(xmlelt, 'CHFID').text = "%s" % self.insuree_chf_id
        ET.SubElement(
            xmlelt, 'StartDate').text = self.start_date.to_ad_date().strftime("%d/%m/%Y")
        if self.end_date:
            ET.SubElement(xmlelt, 'EndDate').text = self.end_date.to_ad_date().strftime(
                "%d/%m/%Y")
        ET.SubElement(xmlelt, 'ICDCode').text = "%s" % self.icd_code
        if self.comment:
            ET.SubElement(xmlelt, 'Comment').text = "%s" % self.comment
        ET.SubElement(xmlelt, 'Total').text = "%s" % self.total
        if self.icd_code_1:
            ET.SubElement(xmlelt, 'ICDCode1').text = "%s" % self.icd_code_1
        if self.icd_code_2:
            ET.SubElement(xmlelt, 'ICDCode1').text = "%s" % self.icd_code_2
        if self.icd_code_3:
            ET.SubElement(xmlelt, 'ICDCode1').text = "%s" % self.icd_code_3
        if self.icd_code_4:
            ET.SubElement(xmlelt, 'ICDCode1').text = "%s" % self.icd_code_4
        if self.visit_type:
            ET.SubElement(xmlelt, 'VisitType').text = "%s" % self.visit_type
        if self.guarantee_no:
            ET.SubElement(
                xmlelt, 'GuaranteeNo').text = "%s" % self.guarantee_no

    def add_to_xmlelt(self, xmlelt):
        details = ET.SubElement(xmlelt, 'Details')
        self._details_to_xmlelt(details)

        if self.items and len(self.items) > 0:
            items = ET.SubElement(xmlelt, 'Items')
            for item in self.items:
                item.add_to_xmlelt(items)

        if self.services and len(self.services) > 0:
            services = ET.SubElement(xmlelt, 'Services')
            for service in self.services:
                service.add_to_xmlelt(services)

    def to_xml(self):
        claim_xml = ET.Element('Claim')
        self.add_to_xmlelt(claim_xml)
        return ET.tostring(claim_xml, encoding='utf-8', method='xml').decode()


class ClaimSubmitError(Exception):
    ERROR_CODES = {
        -1: "Fatal Error",
        1: "Invalid HF Code",
        2: "Duplicate Claim Code",
        3: "Invalid Insuree CHFID",
        4: "End date is smaller than start date",
        5: "Invalid ICDCode",
        6: "Claimed amount is 0",
        7: "Invalid ItemCode",
        8: "Invalid ServiceCode",
        9: "Invalid Claim Admin",
    }

    def __init__(self, code, msg=None):
        self.code = code
        self.msg = ClaimSubmitError.ERROR_CODES.get(
            self.code, msg or "Unknown exception")

    def __str__(self):
        return "ClaimSubmitError %s: %s" % (self.code, self.msg)


class ClaimSubmitService(object):

    def __init__(self, user):
        self.user = user

    def submit(self, claim_submit):
        if self.user.is_anonymous or not self.user.has_perm('claim.can_add'):
            raise PermissionDenied
        with connection.cursor() as cur:
            sql = """\
                DECLARE @ret int;
                EXEC @ret = [dbo].[uspUpdateClaimFromPhone] @XML = %s;
                SELECT @ret;
            """

            cur.execute(sql, (claim_submit.to_xml(),))
            cur.nextset()  # skip 'DECLARE...' (non) result
            cur.nextset()  # skip 'EXEC...' (non) result
            if cur.description is None:  # 0 is considered as 'no result' by pyodbc
                return
            res = cur.fetchone()[0]  # FETCH 'SELECT @res' returned value
            raise ClaimSubmitError(res)
