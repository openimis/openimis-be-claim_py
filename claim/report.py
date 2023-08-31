from claim.reports import claim_percentage_referrals, claims_overview, claim_history
from claim.reports.claim_history import claim_history_query
from claim.reports.claim_percentage_referrals import claim_percentage_referrals_query
from claim.reports.claims_overview import claims_overview_query

report_definitions = [
    {
        "name": "claim_percentage_referrals",
        "engine": 0,
        "default_report": claim_percentage_referrals.template,
        "description": "Percentage of referrals in claims",
        "module": "claim",
        "python_query": claim_percentage_referrals_query,
        "permission": ["131214"],
    },
    {
        "name": "claims_overview",
        "engine": 0,
        "default_report": claims_overview.template,
        "description": "Overview of the processing of claims",
        "module": "claim",
        "python_query": claims_overview_query,
        "permission": ["131213"],
    },
    {
        "name": "claim_history",
        "engine": 0,
        "default_report": claim_history.template,
        "description": "Claim history",
        "module": "claim",
        "python_query": claim_history_query,
        "permission": ["131223"],
    },
]
