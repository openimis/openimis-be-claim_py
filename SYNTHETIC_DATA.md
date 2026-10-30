# Synthetic health data generator

The `seed_synthetic_health_data` Django command creates test families,
insurees, policies, and optional claims. It has two modes.

## Original fixed mode

When no realistic distribution option is supplied, the command keeps the
original behavior: every family has the same number of members and every
insuree has the same number of claims.

```shell
python manage.py seed_synthetic_health_data \
  --families 1000 \
  --members 4 \
  --claims 2 \
  --no-confirm
```

The `small`, `medium`, and `large` presets also use fixed mode.

```shell
python manage.py seed_synthetic_health_data --preset small --no-confirm
```

## Realistic mode

The Nepal country preset enables weighted family sizes and genders,
population-weighted locations, an average of 1.8 claims per insuree,
round-robin products, and realistic claim outcomes.

```shell
python manage.py seed_synthetic_health_data \
  --country nepal \
  --families 1000 \
  --claim-chunk-size 2000 \
  --seed 2026 \
  --no-confirm
```

The country preset defines:

- Family sizes: 1 (5%), 2 (15%), 3 (20%), 4 (30%), 5 (20%), 6 (10%).
- Genders: M (49%) and F (51%).
- Locations: weighted by male and female population, then recorded family
  count when population is unavailable.
- Claim utilization: a Poisson distribution with mean 1.8. Individual
  insurees may have zero, one, or several claims.
- Policies: products rotate across families after the active product list is
  shuffled using the configured random seed.
- Outcomes: 70% fully approved, 10% rejected, and 20% partially approved.
  A partial claim contains both accepted and rejected details. Accepted
  partial details receive 50% to 90% of their requested unit price.

Use the individual options to build another scenario:

```shell
python manage.py seed_synthetic_health_data \
  --families 2000 \
  --family-size-weights '{"1": 0.10, "2": 0.20, "3": 0.30, "4": 0.25, "5": 0.15}' \
  --gender-weights '{"M": 0.48, "F": 0.52}' \
  --location-weighting population \
  --claims-per-insuree 2.3 \
  --round-robin-products \
  --realistic-claim-outcomes \
  --seed 2026 \
  --no-confirm
```

All weight objects must contain positive values that sum to `1.0`. Passing
`--members` overrides weighted family sizes. Passing `--claims` overrides the
average claim count. `--seed` makes Faker and all distributions repeatable.

## Large datasets

Claims are generated in bounded insuree chunks. Each chunk creates its claims,
items, and services and then releases those Python objects before continuing.
The default `--claim-chunk-size` is 2,000 insurees. Reduce it when memory is
limited; increase it cautiously when the database and application have enough
memory.

```shell
python manage.py seed_synthetic_health_data \
  --country nepal \
  --families 50000 \
  --batch-size 5000 \
  --claim-chunk-size 1000 \
  --seed 2026 \
  --no-confirm
```

Every claim chunk is transactional, so a failure cannot leave half of that
chunk in the database. Completed chunks and earlier family, policy, and
insuree stages remain committed. `--single-transaction` restores full-command
rollback for smaller datasets, but it creates a long-running database
transaction and is not recommended for the 50,000-family scenario.

The generator intentionally uses sequential chunks. Python threads would make
Django connection management and seeded output more complex, while concurrent
inserts can contend for the same PostgreSQL tables and indexes.

The command requires active locations, health facilities, products, officers,
diagnoses, medical items, and medical services. Every generated claim receives
an active health facility. Claim dates remain inside their family's policy
coverage period.

## Verification queries

`tblClaim` does not contain `PolicyID` or `ProdID`. Those references are on
`tblClaimItems` and `tblClaimServices`. Product verification must therefore
join through the two claim detail tables.

Product and policy distribution for generated claims:

```sql
WITH claim_policy_products AS (
    SELECT DISTINCT "ClaimID", "PolicyID", "ProdID"
    FROM "tblClaimItems"
    WHERE "ValidityTo" IS NULL
    UNION
    SELECT DISTINCT "ClaimID", "PolicyID", "ProdID"
    FROM "tblClaimServices"
    WHERE "ValidityTo" IS NULL
)
SELECT
    product."ProductCode",
    product."ProductName",
    COUNT(DISTINCT details."PolicyID") AS policies,
    COUNT(DISTINCT details."ClaimID") AS claims
FROM claim_policy_products details
JOIN "tblClaim" claim ON claim."ClaimID" = details."ClaimID"
JOIN "tblProduct" product ON product."ProdID" = details."ProdID"
WHERE claim."ClaimCode" LIKE 'BULK-%'
  AND claim."ValidityTo" IS NULL
GROUP BY product."ProdID", product."ProductCode", product."ProductName"
ORDER BY product."ProductCode";
```

Claim outcome distribution:

```sql
SELECT
    CASE
        WHEN "ClaimStatus" = 1 THEN 'rejected'
        WHEN "Approved" = "Claimed" THEN 'approved'
        WHEN "Approved" > 0 AND "Approved" < "Claimed" THEN 'partial'
        ELSE 'other'
    END AS outcome,
    COUNT(*) AS claims,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS percentage
FROM "tblClaim"
WHERE "ClaimCode" LIKE 'BULK-%'
  AND "ValidityTo" IS NULL
GROUP BY outcome
ORDER BY outcome;
```

Requested and approved amounts by product:

```sql
WITH detail_amounts AS (
    SELECT
        "ClaimID",
        "ProdID",
        "PriceAsked" * "QtyProvided" AS requested,
        COALESCE("PriceApproved", 0) * COALESCE("QtyApproved", 0) AS approved
    FROM "tblClaimItems"
    WHERE "ValidityTo" IS NULL
    UNION ALL
    SELECT
        "ClaimID",
        "ProdID",
        "PriceAsked" * "QtyProvided" AS requested,
        COALESCE("PriceApproved", 0) * COALESCE("QtyApproved", 0) AS approved
    FROM "tblClaimServices"
    WHERE "ValidityTo" IS NULL
)
SELECT
    product."ProductCode",
    product."ProductName",
    SUM(details.requested) AS requested,
    SUM(details.approved) AS approved
FROM detail_amounts details
JOIN "tblClaim" claim ON claim."ClaimID" = details."ClaimID"
JOIN "tblProduct" product ON product."ProdID" = details."ProdID"
WHERE claim."ClaimCode" LIKE 'BULK-%'
  AND claim."ValidityTo" IS NULL
GROUP BY product."ProdID", product."ProductCode", product."ProductName"
ORDER BY product."ProductCode";
```

This command creates synthetic test data only. Do not run it against a
production database containing real patient or insurance data.
