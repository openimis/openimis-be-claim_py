# openIMIS Backend Claim reference module
This repository holds the files of the openIMIS Backend Claim reference module.
It is dedicated to be deployed as a module of [openimis-be_py](https://github.com/openimis/openimis-be_py).

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

## Code climate (develop branch)

[![Maintainability](https://img.shields.io/codeclimate/maintainability/openimis/openimis-be-claim_py.svg)](https://codeclimate.com/github/openimis/openimis-be-claim_py/maintainability)
[![Test Coverage](https://img.shields.io/codeclimate/coverage/openimis/openimis-be-claim_py.svg)](https://codeclimate.com/github/openimis/openimis-be-claim_py)

## ORM mapping:
* tblClaimAdmin > ClaimAdmin
* tblFeedback > Feedback
* tblClaim  > Claim
* tblClaimItems > ClaimItem
* tblClaimServices > ClaimService
* claim_ClaimAttachment > ClaimAttachment

## Listened Django Signals
* `signal_mutation_module_validate["claim"]`: handles ClaimMutation

## Services
* *DEPRECATED* ClaimSubmitService.submit, mapped to uspUpdateClaimFromPhone Stored Proc (used by api_fhir reference implementation: needs replacement, with signals)
* ClaimReportService, loading the necessary data for the Claim printing
## New Defined Statuses for claim
* STATUS_RETURNED_FROM_FACILITY = 17
* STATUS_RETURNED_FROM_BRANCH = 18
* STATUS_SUBMITTED_TO_HEAD = 19
* STATUS_RESUBMITTED_TO_HEAD = 20
* STATUS_RESUBMITTED_TO_BRANCH = 21
* STATUS_FLAGGED = 22

## Reports (template can be overloaded via report.ReportDefinition)
* claim_claims (Claim printing)

## GraphQL Queries
* claims
* claim_admins
* claim_admins_str
* claim_officers
* claim_attachments

## GraphQL Mutations - each mutation emits default signals and return standard error lists (cfr. openimis-be-core_py)
* create_claim
* update_claim
* submit_claims
* select_claims_for_feedback
* deliver_claim_feedback
* bypass_claims_feedback
* skip_claims_feedback
* select_claims_for_review
* deliver_claims_review
* bypass_claims_review
* skip_claims_review
* save_claims_review
* process_claims
* delete_claims
* add_claim_attachment
* update_attachment
* delete_claim_attachment

## Additional Endpoints
* print: generating Claim PDF

## Reports
* `claim_claim`: Claim summary

## Configuration options (can be changed via core.ModuleConfiguration)
* default_validations_disabled: bypass (defaul) claim validations in Submit and Process mutations (default: False)
* gql_query_claims_perms: required rights to call claims GraphQL Query
  (default: `["111001"]`)
* gql_query_claim_officers_perms: required rights to call claim_officers GraphQL Query (default: `[]`)
* gql_query_claims_rejected_perms: view REJECTED claims (default: `["111060"]`)
* gql_query_claims_entered_perms: view ENTERED claims (default: `["111061"]`)
* gql_query_claims_checked_perms: view CHECKED claims (default: `["111062"]`)
* gql_query_claims_processed_perms: view PROCESSED claims (default: `["111063"]`)
* gql_query_claims_valuated_perms: view VALUATED claims (default: `["111064"]`)
* gql_query_claims_returned_facility_perms: view RETURNED FROM FACILITY claims (default: `["111065"]`)
* gql_query_claims_returned_branch_perms: view RETURNED FROM BRANCH claims (default: `["111066"]`)
* gql_query_claims_submitted_head_perms: view SUBMITTED TO HEAD claims (default: `["111067"]`)
* gql_query_claims_resubmitted_head_perms: view RESUBMITTED TO HEAD claims (default: `["111068"]`)
* gql_query_claims_resubmitted_branch_perms: view RESUBMITTED TO BRANCH claims (default: `["111069"]`)
* gql_query_claims_flagged_perms: view FLAGGED claims (default: `["111070"]`)
* gql_mutation_create_claims_perms: required rights to call create_claim GraphQL Mutation (default: `["111002"]`)
* gql_mutation_update_claims_perms: required rights to call update_claim GraphQL Mutation (default: `["111010"]`)
* gql_mutation_submit_claims_perms: required rights to call submit_claim GraphQL Mutation (default: `["111007"]`)
* gql_mutation_select_claim_feedback_perms: required rights to call select_claim_feedback GraphQL Mutation (default: `["111010"]`)
* gql_mutation_bypass_claim_feedback_perms: required rights to call bypass_claim_feedback GraphQL Mutation (default: `["111010"]`)
* gql_mutation_skip_claim_feedback_perms: required rights to call skip_claim_feedback GraphQL Mutation (default: `["111010"]`)
* gql_mutation_deliver_claim_feedback_perms: required rights to call deliver_claim_feedback GraphQL Mutation (default: `["111009"]`)
* gql_mutation_select_claim_review_perms: required rights to call select_claim_review GraphQL Mutation (default: `["111010"]`)
* gql_mutation_bypass_claim_review_perms: required rights to call bypass_claim_review GraphQL Mutation (default: `["111010"]`)
* gql_mutation_skip_claim_review_perms: required rights to call skip_claim_review GraphQL Mutation (default: `["111010"]`)
* gql_mutation_deliver_claim_review_perms: required rights to call deliver_claim_review GraphQL Mutation (default: `["111010"]`)
* gql_mutation_process_claims_perms: required rights to call process_claims GraphQL Mutation (default: `["111011"]`)
* gql_mutation_delete_claims_perms: required rights to call delete_claims GraphQL Mutation (default: `["111004"]`)
* claim_print_perms: required rights to call print endpoint (default: `["111006"]`)
* gql_mutation_submit_claims_to_head_perms: required rights to call submit_claims_to_head GraphQL Mutation (default: `["111014"]`)
* gql_mutation_resubmit_claims_to_head_perms: required rights to call resubmit_claims_to_head GraphQL Mutation (default: `["111015"]`)
* gql_mutation_resubmit_claims_to_branch_perms: required rights to call resubmit_claims_to_branch GraphQL Mutation (default: `["111016"]`)
* gql_mutation_flag_claims_perms: required rights to call flag_claims GraphQL Mutation (default: `["111017"]`)
* gql_mutation_approve_claims_perms: required rights to call approve_claims GraphQL Mutation (default: `["111018"]`)
* gql_mutation_reject_claims_perms: required rights to call reject_claims GraphQL Mutation (default: `["111019"]`)
* claim_attachments_root_path: using os standard file system, root path for the claim attachments (default: None ... documents B64 in database)

  WARNINGS:
  * attachments in input are NOT streamed (posted in a GraphQL query and fully read when serving), gateway must be configure to limit request payload size
  * attachments in output are served from the python (django) os process, generating load (memory consumption) on the application server, customisation of the view to output a redirect to a static file server is recommended
  * in an attempt to prevent encoding problems, files are written as binaries on the filesystem, please ensure the mounted file system supports python binary access (wb flag)
* verify_quantities: Defines whether the system must check the service & sub-services prices or not. 
  * If the packagetype of the service is flat fee bundle, the system checks if claim_detail.price_adjusted or claim_detail.price_asked is higher than the price of the service. If it's the case, the final price of the claim becomes the price of the service.
  * On the other hand, if the service type is Fee-for-service bundle then the system checks all the sub-services of the claim to find out if the qty_provided of the sub-service is indeed equal to the qty_displayed of the claimserviceservice or if the qty_provided of the sub-item is indeed equal to the qty_displayed of the claimserviceitem. If all are not equal then the final price of the claim will be 0.

## openIMIS Modules Dependencies
* core.models.VersionedModel
* core.models.InteractiveUser
* claim_batch.models.BatchRun
* insuree.models.Insuree
* location.models.HealthFacility
* medical.models.Diagnosis
* medical.models.Item
* medical.models.Service
* policy.models.Policy
* product.models.Product
* report.services.ReportService


## price per stage

remunerated* is not a real claim status
CHECKED and reviewed is not a different status that CHECKED

| claim Stage | elm latest qty | elm latest price | claim value |
|---|---|---|---|
| SAVED | qty_provided | price_asked | x |
| CHECKED | qty_provided | price_adjusted | claimed |
| CHECKED and reviewed | qty_approved | price_approved | approved | 
| PROCESSED | qty_approved | price_valuated | approved |
| VALUATED | qty_approved | price_valuated | valuated |
| remunerated* | x | remunerated_amount | remunerated |


### process steps
SAVED --Submit--> CHECKED --process--> PROCESSED --batch run--> VALUATED --payment--> remunerated*