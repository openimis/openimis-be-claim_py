| :bomb: Disclaimer |
| --- |
| This repository currently only contains bootsrapping material for the modularized openIMIS. Don't use it (or even connect it) to a production database. |

# openIMIS Backend Claim reference module
This repository holds the files of the openIMIS Backend Claim reference module.

Current version provides the following ORM mapping:
* tblClaimAdmin > ClaimAdmin
* tblICDCodes > ClaimDiagnosisCode
* tblFeedback > Feedback
* tblClaim  > Claim (missing fk to tblBatchrun)
* tblClaimItems > ClaimItem (missing fk to tblProduct)
* tblClaimServices > ClaimService (missing fk to tblProduct)
