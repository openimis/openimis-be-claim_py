# Deductibles and ceilings (DedRem)

`tblClaimDedRem` (`ClaimDedRem`) holds, per claim and per policy, the amount the
claim had deducted (`ded_*`) and remunerated (`rem_*`). A row stores this claim's
own amounts only; what the previous claims already consumed is summed from their
own rows each time a claim is computed.

## A working table

A claim has at most one live row per policy. The row is deleted and rewritten
each time the claim's DedRem is computed, and deleted when the claim is rejected,
invalid or deleted. Rows are never archived (earlier releases set `validity_to`;
such rows are ignored everywhere).

## When the DedRem is computed

| Event | Status of the row | Amounts written on the claim |
|---|---|---|
| Submission (the claim becomes checked) | `CHECKED` | no |
| Review delivery | `REVIEWED` | no |
| Processing | `PROCESSED` | yes: deducted, exceeding ceiling and remunerated amounts |

`ClaimDedRem.status` is ordered `CHECKED < REVIEWED < PROCESSED`.

Review delivery and processing first stamp the claim (`validity_from_review` or
`process_stamp`, and `validity_from`); a claim update sets `validity_from`. A
claim's own recompute is skipped when its rows are newer than the latest of
`validity_from`, `validity_from_review`, `submit_stamp` and `process_stamp`,
because nothing changed since they were written.

## Which previous rows count

| Claim being computed | Previous rows counted |
|---|---|
| checked | checked, reviewed, processed |
| reviewed | reviewed, processed |
| processed | processed |

Only the rows in the limit's scope count:

| Product limit level (`ded_*`, `max_*`) | Previous rows |
|---|---|
| `<prefix>_treatment` | none: the limit applies per claim |
| `<prefix>_insuree` | the same insuree's rows on the policy |
| `<prefix>_policy` | every row on the policy |

The prefix is `ded` or `max` (general, type G); when the product sets no general
limit, `ded_ip`/`max_ip` apply to a hospital visit and `ded_op`/`max_op` to any
other claim. The first level the product sets wins (treatment, then insuree,
then policy).

## Recalculating the later claims

After a claim's DedRem is computed or deleted, the claims that counted it are
recomputed, once each, in reference date order (`date_to`, else `date_from`),
without cascading further. They are the claims whose rows:

- are in the scope above (the union of the deductible and ceiling levels:
  none for treatment-level limits only);
- have a status lower than or equal to the changed claim's, and are never
  processed (a processed or valuated claim is never changed);
- belong to a claim with a later reference date.

Submit, review delivery and process batches handle their claims in reference
date order, so an earlier claim is settled before a later one counts it.

Concurrent submissions on the same policy are not locked against each other.

## GraphQL

Both queries require the claim read right and return only the rows the user may
see (`ClaimDedRem.row_scope = ParentScope("policy")`).

```graphql
query {
  claimDedRems(policyUuid: "...", insureeUuid: "...", status: [CHECKED, REVIEWED]) {
    edges { node { claim { code } status dedG remG remIp remOp } }
  }
  claimDedRemSum(policyUuid: "...", insureeUuid: "...", status: [PROCESSED], dateTo: "2026-12-31") {
    dedG dedIp dedOp remG remIp remOp remConsult remSurgery remDelivery remHospitalization remAntenatal
  }
}
```

`claimDedRems` takes at least one of `claimUuid`, `policyUuid`, `insureeUuid`.
`claimDedRemSum` requires `policyUuid`; `dateTo` keeps the claims whose reference
date is on or before it.
