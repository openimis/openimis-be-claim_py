# Claims module baseline benchmark (WO-001)

**Project:** ClaimsAdjudication (REF-2091)  
**Work order:** WO-001 — Baseline current behavior  
**Module:** `openimis-be-claim_py` (this repo is a pluggable module of [openimis-be_py](https://github.com/openimis/openimis-be_py))  
**Purpose:** Establish repeatable measurements of *current* behavior before modernization. Fill in the results tables after each run; do not change application code as part of recording baselines.

## PRD measurement dimensions

| Dimension | What to capture | Used in scenarios |
|-----------|-----------------|-------------------|
| Submission latency | Wall-clock time for submit path (GraphQL `submit_claims` or service `ClaimSubmitService.submit_claim`) | S1 |
| Query latency | Wall-clock time for GraphQL resolver round-trip | S2, S3 |
| Query count | Number of SQL queries per operation (`django.db.connection.queries` or `assertNumQueries` in tests) | S2, S3 |
| Report runtime | Wall-clock time to generate claim report output | S4 |
| Batch throughput | Claims processed per unit time via `process_claims` (and related batch run if applicable) | S5 |

Record environment metadata on every run (see [Run metadata](#run-metadata)).

---

## Environment setup (non-production only)

Run all steps against a **dedicated non-prod** openIMIS backend. Do not point benchmarks at production data or credentials.

### Prerequisites

1. **Backend assembly:** Clone and configure [openimis-be_py](https://github.com/openimis/openimis-be_py) with this claim module on the `develop` branch (or the same commit under test).
2. **Database:** SQL Server (or the DB stack your openIMIS instance uses) with a **restorable snapshot** or disposable database. Restore from a anonymized/staging backup rather than connecting to production.
3. **Dependencies:** Install module requirements from `setup.py` / the parent backend’s lockfile. Module CI is defined in `.github/workflows/ci.yml` (reusable workflow `openimis/openimis-be_py/.github/workflows/ci_module.yml@develop`).
4. **Auth:** GraphQL calls need a user with claim permissions (see README configuration keys, e.g. `gql_mutation_submit_claims_perms`, `gql_query_claims_perms`).
5. **Optional — synthetic volume:** Use the management command below to seed claims for list/query/batch scenarios.

### Seed representative test data

From the **parent backend** project root (where `manage.py` lives):

```bash
# Example: 500 claims, 3 services and 2 items each (adjust counts for your DB size)
python manage.py generateclaims <nb_claims> <nb_services> <nb_items> --verbose
```

- Command implementation: `claim/management/commands/generateclaims.py`
- Helpers: `claim/test_helpers.py` (`create_test_claim`, items, services)
- Related: `claim/management/commands/generateclaimadmins.py`

Ensure seed data includes claims in states needed for **submit** (entered/saved) and **process** (checked/submitted per your workflow).

### Enable query counting (for S2/S3)

In Django settings for the benchmark run only:

```python
DEBUG = True  # required for connection.queries; never in production
```

Or use Django’s `CaptureQueriesContext` / test `assertNumQueries` patterns in a one-off script under the parent backend.

### GraphQL endpoint

Use the parent backend GraphQL URL (typically `/api/graphql`). Authenticate with the same mechanism your environment uses (JWT, basic, etc.).

---

## Benchmark scenarios

Each scenario uses **current** code paths in this module. Code references are for orientation when writing scripts or manual steps.

| ID | Scenario | Primary interface | Code path (reference) |
|----|----------|-------------------|------------------------|
| **S1** | Claim submission | GraphQL `submit_claims` | `claim/gql_mutations.py` (`SubmitClaimsMutation`), `claim/services.py` (`ClaimSubmitService.submit_claim`) |
| **S2** | Claim list query | GraphQL `claims` | `claim/schema.py` (`resolve_claims`), `claim/gql_queries.py` |
| **S3** | Single claim lookup | GraphQL `claim` | `claim/schema.py` (`resolve_claim`) |
| **S4** | Report generation | Report `claim_claim` / print | `claim/reports/claim.py`, `claim/report.py`, `ClaimReportService` (README) |
| **S5** | Batch processing | GraphQL `process_claims` | `claim/gql_mutations.py` (`ProcessClaimsMutation`), `claim/services.py` (`processing_claim`) |

### S1 — Claim submission

**Procedure (outline):**

1. Create or select N claims in submittable state (use test helpers or UI).
2. Warm up once, then measure M iterations of `submit_claims` for a fixed claim UUID set.
3. Record end-to-end latency (client → response) and optionally server-side timing from logs.

**Example GraphQL shape (adjust to your schema):**

```graphql
mutation SubmitClaims($input: SubmitClaimsMutationInput!) {
  submitClaims(input: $input) {
    clientMutationId
    internalId
    internalCode
  }
}
```

### S2 — `claims` query (list)

**Procedure:**

1. Fix filter parameters (date range, HF, status) representative of production-like list views.
2. Run the `claims` query with `first`/pagination matching a typical UI page size.
3. Record latency and **query count** for one page.

```graphql
query Claims($first: Int, $after: String) {
  claims(first: $first, after: $after) {
  }
}
```

### S3 — `claim` query (single)

**Procedure:**

1. Pick claim UUIDs that exist in seed data (with items/services attached).
2. Run `claim(id: …)` or `claim(uuid: …)` repeatedly; record latency and query count.

```graphql
query Claim($uuid: String!) {
  claim(uuid: $uuid) {
    uuid
    code
    status
  }
}
```

### S4 — Report generation

**Procedure:**

1. Select a claim with items/services suitable for the `claim_claim` report template.
2. Trigger report generation via the report module API or REST print endpoint (`claim/urls.py`, README `claim_print_perms`).
3. Record wall-clock runtime until PDF/output is available.

### S5 — `process_claims` execution (batch throughput)

**Procedure:**

1. Prepare B claims in **checked** (or the status your environment requires before processing).
2. Invoke `process_claims` with batch size B; record total wall time and compute **claims per second**.
3. If your deployment uses `claim_batch` batch runs for valuation, note that separately (out of scope for pure mutation timing unless required for your baseline definition).

```graphql
mutation ProcessClaims($input: ProcessClaimsMutationInput!) {
  processClaims(input: $input) {
    clientMutationId
    internalId
  }
}
```

---

## Results — run 1

> Copy this section for each benchmark run (e.g. before/after upgrades).

### Run metadata

| Field | Value |
|-------|--------|
| Date (UTC) | _TBD_ |
| Git commit (`openimis-be-claim_py`) | _TBD_ |
| Parent backend (`openimis-be_py`) commit | _TBD_ |
| DB size (approx. claims count) | _TBD_ |
| Hardware / hosting | _TBD_ |
| Runner (manual / script name) | _TBD_ |

### Results table

| Scenario | Submission latency (ms) | Query latency (ms) | Query count | Report runtime (ms) | Batch throughput (claims/s) | Notes |
|----------|-------------------------|--------------------|-------------|---------------------|----------------------------|-------|
| S1 Submit | | — | — | — | — | |
| S2 `claims` | — | | | — | — | Pagination: _first=N_ |
| S3 `claim` | — | | | — | — | UUID: _…_ |
| S4 Report | — | — | — | | — | Report: `claim_claim` |
| S5 `process_claims` | — | — | — | — | | Batch size B=_ |

### PRD target comparison (fill when PRD targets are known)

| Dimension | Baseline (this run) | PRD target | Meets target? |
|-----------|---------------------|------------|---------------|
| Submission latency | | | |
| Query latency | | | |
| Query count | | | |
| Report runtime | | | |
| Batch throughput | | | |

---

## Existing test and CI tooling

| Asset | Location | Relevance |
|-------|----------|-----------|
| Unit / validation tests | `claim/tests/` | Behavior reference; not performance benchmarks |
| Test helpers | `claim/test_helpers.py` | Programmatic claim creation |
| CI | `.github/workflows/ci.yml` | Module test + Sonar on push/PR |
| Data generator | `claim/management/commands/generateclaims.py` | Volume for S2–S5 |

Module tests are intended to run from the **parent** backend test harness, not in isolation in this repo.

---

## Acceptance criteria checklist (WO-001)

- [ ] Repeatable benchmark document covers S1–S5 (this file).
- [ ] Results tables include all PRD dimensions (even if targets are missed).
- [ ] Procedure is executable in non-prod without modifying production data.

---

## Next steps (after scaffold)

1. Execute each scenario in your non-prod stack and fill **Results — run 1**.
2. Optionally add a small benchmark script in the parent backend repo (kept out of this module until agreed).
3. When results are complete, commit on branch `wo/WO-001-baseline` with message containing `[WO-001]` per Forge workflow.
