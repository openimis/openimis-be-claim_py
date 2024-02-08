from django.apps import AppConfig

MODULE_NAME = "claim"

DEFAULT_CFG = {
    "default_validations_disabled": False,
    "gql_query_claims_perms": ["111001"],
    "gql_query_claim_admins_perms": [],
    "gql_query_claim_officers_perms": [],
    "gql_query_claim_diagnosis_variance_only_on_existing": True,
    "gql_mutation_create_claims_perms": ["111002"],
    "gql_mutation_update_claims_perms": ["111010"],
    "gql_mutation_load_claims_perms": ["111005"],
    "gql_mutation_submit_claims_perms": ["111007"],
    "gql_mutation_select_claim_feedback_perms": ["111010"],
    "gql_mutation_bypass_claim_feedback_perms": ["111010"],
    "gql_mutation_skip_claim_feedback_perms": ["111010"],
    "gql_mutation_deliver_claim_feedback_perms": ["111009"],
    "gql_mutation_select_claim_review_perms": ["111010"],
    "gql_mutation_bypass_claim_review_perms": ["111010"],
    "gql_mutation_skip_claim_review_perms": ["111010"],
    "gql_mutation_deliver_claim_review_perms": ["111008"],
    "gql_mutation_process_claims_perms": ["111011"],
    "gql_mutation_restore_claims_perms": ["111012"],
    "gql_mutation_delete_claims_perms": ["111004"],
    "claim_print_perms": ["111006"],
    "claim_attachments_root_path": None,
    "claim_uspUpdateClaimFromPhone_intermediate_sets": 2,
    "autogenerated_claim_code_config": {'code_length': 8},
    "max_claim_length": 20,
    "claim_validation_multiple_services_explanation_required": True,
    "autogenerate_func": 'claim.utils.autogenerate_nepali_claim_code',
    "additional_diagnosis_number_allowed": 4,
    "claim_max_restore": None,
    "allowed_domains_attachments": [],
    "native_code_for_services": True
}


class ClaimConfig(AppConfig):
    name = MODULE_NAME

    default_validations_disabled = None
    gql_query_claims_perms = []
    gql_query_claim_admins_perms = []
    gql_query_claim_officers_perms = []
    gql_query_claim_diagnosis_variance_only_on_existing: None
    gql_mutation_create_claims_perms = []
    gql_mutation_update_claims_perms = []
    gql_mutation_load_claims_perms = []
    gql_mutation_submit_claims_perms = []
    gql_mutation_select_claim_feedback_perms = []
    gql_mutation_bypass_claim_feedback_perms = []
    gql_mutation_skip_claim_feedback_perms = []
    gql_mutation_deliver_claim_feedback_perms = []
    gql_mutation_select_claim_review_perms = []
    gql_mutation_bypass_claim_review_perms = []
    gql_mutation_skip_claim_review_perms = []
    gql_mutation_deliver_claim_review_perms = []
    gql_mutation_process_claims_perms = []
    gql_mutation_restore_claims_perms = []
    gql_mutation_delete_claims_perms = []
    claim_print_perms = []
    claim_attachments_root_path = None
    claim_uspUpdateClaimFromPhone_intermediate_sets = None
    autogenerated_claim_code_config = {}
    native_code_for_services = True
    # cannot be set in the model, since migration has to be able to handle all implementations
    max_claim_length = None
    claim_max_restore = None
    claim_validation_multiple_services_explanation_required = None
    # Provide absolute path to autogenerating function for claim code
    autogenerate_func = None
    additional_diagnosis_number_allowed = None  # Currently code supports 4 diagnoses maximum, going above will not work
    allowed_domains_attachments = None

    def __load_config(self, cfg):
        for field in cfg:
            if hasattr(ClaimConfig, field):
                setattr(ClaimConfig, field, cfg[field])

    def ready(self):
        from core.models import ModuleConfiguration
        cfg = ModuleConfiguration.get_or_default(MODULE_NAME, DEFAULT_CFG)
        self.__load_config(cfg)
