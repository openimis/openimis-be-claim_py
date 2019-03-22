from django.db import models
from core import fields

class Claim(models.Model):
    id = models.AutoField(db_column='ClaimID', primary_key=True)  
    category = models.CharField(db_column='ClaimCategory', max_length=1, blank=True, null=True)  
    code = models.CharField(db_column='ClaimCode', max_length=8)  
    date_from = fields.DateField(db_column='DateFrom')  
    date_to = fields.DateField(db_column='DateTo', blank=True, null=True)  
    claimed = models.DecimalField(db_column='Claimed', max_digits=18, decimal_places=2, blank=True, null=True)  
    approved = models.DecimalField(db_column='Approved', max_digits=18, decimal_places=2, blank=True, null=True)  
    reinsured = models.DecimalField(db_column='Reinsured', max_digits=18, decimal_places=2, blank=True, null=True)  
    valuated = models.DecimalField(db_column='Valuated', max_digits=18, decimal_places=2, blank=True, null=True)  
    date_claimed = fields.DateField(db_column='DateClaimed')  
    date_processed = models.DateTimeField(db_column='DateProcessed', blank=True, null=True)

    def __str__(self):
        return "[%s]" % self.id
    
    class Meta:
        managed = False
        db_table = 'tblClaim'
