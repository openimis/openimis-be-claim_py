from math import isclose


REALISTIC_COUNTRY_PRESETS = {
    "nepal": {
        "name": "Nepal",
        "families": 50000,
        "family_size_weights": {
            1: 0.05,
            2: 0.15,
            3: 0.20,
            4: 0.30,
            5: 0.20,
            6: 0.10,
        },
        "gender_weights": {"M": 0.49, "F": 0.51},
        "location_weighting": "population",
        "claims_per_insuree": 1.8,
        "round_robin_products": True,
        "realistic_claim_outcomes": True,
    },
}

CLAIM_OUTCOME_WEIGHTS = {
    "APPROVED": 0.70,
    "REJECTED": 0.10,
    "PARTIAL": 0.20,
}
ITEM_REJECTION_RATE = 0.30
SERVICE_REJECTION_RATE = 0.25
PRICE_REDUCTION_RANGE = (0.50, 0.90)


def validate_weights(weights, name, required_keys=None):
    if not isinstance(weights, dict) or not weights:
        raise ValueError(f"{name} must be a non-empty object.")
    if required_keys and set(weights) != set(required_keys):
        required = ", ".join(map(str, required_keys))
        raise ValueError(f"{name} must contain exactly these keys: {required}.")
    if any(not isinstance(weight, (int, float)) or weight <= 0 for weight in weights.values()):
        raise ValueError(f"All {name} weights must be positive numbers.")
    if not isclose(sum(weights.values()), 1.0, abs_tol=1e-9):
        raise ValueError(f"{name} weights must sum to 1.0.")
    return weights


def validate_realistic_config(config):
    family_weights = config.get("family_size_weights")
    if family_weights:
        if any(not isinstance(size, int) or size < 1 for size in family_weights):
            raise ValueError("Family-size keys must be positive integers.")
        validate_weights(family_weights, "family-size")

    gender_weights = config.get("gender_weights")
    if gender_weights:
        validate_weights(gender_weights, "gender", required_keys=("M", "F"))

    claims_per_insuree = config.get("claims_per_insuree")
    if claims_per_insuree is not None and claims_per_insuree <= 0:
        raise ValueError("Claims per insuree must be greater than zero.")

    if config.get("location_weighting") not in (None, "uniform", "population"):
        raise ValueError("Location weighting must be 'uniform' or 'population'.")

    return config
