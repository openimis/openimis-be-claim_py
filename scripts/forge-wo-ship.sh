#!/usr/bin/env bash
# Push branch, create/update GitHub PR, and print Forge MCP payloads for in_review + PR registration.
# Usage:
#   ./scripts/forge-wo-ship.sh WO-001 [work_order_uuid]
#   ./scripts/forge-wo-ship.sh --wo-uuid <uuid> [--label WO-001]
#
# Env: FORGE_PROJECT_ID or .forge/project-id; FORGE_API_TOKEN or FORGE_TOKEN (optional REST);
#      FORGE_API_URL (default http://127.0.0.1:3001); GH_BASE (default: origin default branch).

set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || { echo "Not inside a git repository." >&2; exit 1; }
cd "$ROOT"

die() { echo "forge-wo-ship: $*" >&2; exit 1; }

WO_LABEL=""
WO_UUID="${FORGE_WORK_ORDER_ID:-}"
GH_BASE="${GH_BASE:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --wo-uuid) WO_UUID="${2:?}"; shift 2 ;;
    --label) WO_LABEL="${2:?}"; shift 2 ;;
    --base) GH_BASE="${2:?}"; shift 2 ;;
    -h|--help)
      sed -n '2,8p' "$0"
      exit 0
      ;;
    WO-*|wo:WO-*)
      WO_LABEL="${1#wo:}"
      shift
      ;;
    *)
      if [[ -z "$WO_UUID" && "$1" =~ ^[0-9a-f-]{36}$ ]]; then
        WO_UUID="$1"
      elif [[ -z "$WO_LABEL" && "$1" =~ ^WO-[0-9]+$ ]]; then
        WO_LABEL="$1"
      else
        die "Unknown argument: $1"
      fi
      shift
      ;;
  esac
done

BRANCH="$(git branch --show-current)"
if [[ -z "$WO_LABEL" && "$BRANCH" =~ ^wo/(WO-[0-9]+) ]]; then
  WO_LABEL="${BASH_REMATCH[1]}"
fi
[[ -n "$WO_LABEL" ]] || die "Work order label required (e.g. WO-001) or use branch wo/WO-NNN-*"

PROJECT_ID="${FORGE_PROJECT_ID:-}"
if [[ -f "$ROOT/.forge/project-id" ]]; then
  PROJECT_ID="$(head -n 1 "$ROOT/.forge/project-id" | tr -d '\r\n')"
fi
[[ -n "$PROJECT_ID" ]] || die "Set FORGE_PROJECT_ID or create .forge/project-id"

command -v gh >/dev/null 2>&1 || die "gh CLI is required"
command -v jq >/dev/null 2>&1 || die "jq is required"

REPO_URL="$(git remote get-url origin 2>/dev/null || true)"
REPO_NAME="$(echo "$REPO_URL" | sed -E 's#.*github\.com[:/]([^/]+/[^/.]+).*#\1#; s#\.git$##')"
COMMIT_HASH="$(git rev-parse HEAD)"
COMMIT_MSG="$(git log -1 --pretty=%B)"
COMMIT_AUTHOR="$(git config user.name 2>/dev/null || true)"

if git rev-parse HEAD~1 >/dev/null 2>&1; then
  DIFF_RANGE="HEAD~1"
else
  DIFF_RANGE="HEAD"
fi

STAT_LINE="$(git diff "$DIFF_RANGE" --stat 2>/dev/null | tail -1 || true)"
FILES_CHANGED="$(echo "$STAT_LINE" | awk '{print $1}' | grep -E '^[0-9]+$' || echo 0)"
[[ "$FILES_CHANGED" != "0" ]] || FILES_CHANGED="$(git diff "$DIFF_RANGE" --name-only 2>/dev/null | wc -l | tr -d ' ')"

LINES_ADDED=0
LINES_REMOVED=0
CHANGED_FILES_JSON="[]"
if git rev-parse HEAD~1 >/dev/null 2>&1; then
  while IFS=$'\t' read -r add del path; do
    [[ -n "$path" ]] || continue
    LINES_ADDED=$((LINES_ADDED + add))
    LINES_REMOVED=$((LINES_REMOVED + del))
    CHANGED_FILES_JSON="$(echo "$CHANGED_FILES_JSON" | jq --arg p "$path" --argjson a "$add" --argjson d "$del" \
      '. + [{path:$p, additions:$a, deletions:$d}]')"
  done < <(git diff HEAD~1 --numstat)
fi

if [[ -z "$GH_BASE" ]]; then
  GH_BASE="$(gh repo view "$REPO_NAME" --json defaultBranchRef -q .defaultBranchRef.name 2>/dev/null || echo develop)"
fi

echo "==> Pushing $BRANCH to origin"
git push -u origin "$BRANCH"

PR_URL=""
PR_NUMBER=""
if PR_JSON="$(gh pr view --json url,number 2>/dev/null)"; then
  PR_URL="$(echo "$PR_JSON" | jq -r .url)"
  PR_NUMBER="$(echo "$PR_JSON" | jq -r .number)"
  echo "==> Existing PR #$PR_NUMBER: $PR_URL"
else
  TITLE="[$WO_LABEL] $(git log -1 --pretty=%s)"
  BODY_FILE="$(mktemp)"
  cat >"$BODY_FILE" <<EOF
## Summary
Ship work order **$WO_LABEL** from branch \`$BRANCH\`.

## Test plan
- [ ] CI checks pass on this PR
- [ ] Acceptance criteria for $WO_LABEL verified in Forge

---
_Forge: ClaimsAdjudication ($PROJECT_ID)_
EOF
  echo "==> Creating PR (base: $GH_BASE)"
  PR_URL="$(gh pr create --base "$GH_BASE" --head "$BRANCH" --title "$TITLE" --body-file "$BODY_FILE")"
  rm -f "$BODY_FILE"
  PR_NUMBER="$(gh pr view --json number -q .number)"
  echo "==> Created PR #$PR_NUMBER: $PR_URL"
fi

# Optional Forge REST (local Forge server); MCP is the canonical path when REST is unavailable.
API_URL="${FORGE_API_URL:-http://127.0.0.1:3001}"
TOKEN="${FORGE_API_TOKEN:-${FORGE_TOKEN:-}}"
if [[ -n "$TOKEN" && -n "$WO_UUID" ]]; then
  PAYLOAD="$(jq -n \
    --arg status "in_review" \
    --arg branch "$BRANCH" \
    --arg repo_url "$REPO_URL" \
    --arg repo_name "$REPO_NAME" \
    --arg commit_hash "$COMMIT_HASH" \
    --arg commit_message "$COMMIT_MSG" \
    --arg commit_author "$COMMIT_AUTHOR" \
    --arg pr_url "$PR_URL" \
    --argjson pr_number "${PR_NUMBER:-null}" \
    --argjson files_changed "${FILES_CHANGED:-0}" \
    --argjson lines_added "$LINES_ADDED" \
    --argjson lines_removed "$LINES_REMOVED" \
    '{status:$status, branch_name:$branch, repo_url:$repo_url, repo_name:$repo_name,
      commit_hash:$commit_hash, commit_message:$commit_message, commit_author:$commit_author,
      pr_url:$pr_url, pr_number:$pr_number, files_changed:$files_changed,
      lines_added:$lines_added, lines_removed:$lines_removed}')"
  if curl -sf -X PATCH "$API_URL/projects/$PROJECT_ID/work-orders/$WO_UUID" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD" >/dev/null 2>&1; then
    echo "==> Forge REST: work order set to in_review"
  else
    echo "==> Forge REST unavailable (use MCP below)"
  fi
fi

MCP_FILE="$ROOT/.forge/last-ship-mcp.json"
mkdir -p "$ROOT/.forge"
jq -n \
  --arg wo_label "$WO_LABEL" \
  --arg wo_uuid "$WO_UUID" \
  --arg project_id "$PROJECT_ID" \
  --arg branch "$BRANCH" \
  --arg repo_url "$REPO_URL" \
  --arg repo_name "$REPO_NAME" \
  --arg pr_url "$PR_URL" \
  --argjson pr_number "${PR_NUMBER:-null}" \
  --arg commit_hash "$COMMIT_HASH" \
  --arg commit_message "$COMMIT_MSG" \
  --arg commit_author "$COMMIT_AUTHOR" \
  --argjson files_changed "${FILES_CHANGED:-0}" \
  --argjson lines_added "$LINES_ADDED" \
  --argjson lines_removed "$LINES_REMOVED" \
  --argjson changed_files "$CHANGED_FILES_JSON" \
  '{
    instructions: "Run these Forge MCP tools in order (set_project if needed):",
    set_project: {project_id: $project_id},
    create_pull_request: {
      work_order_id: (if $wo_uuid != "" then $wo_uuid else "LOOKUP_VIA_get_work_order"),
      branch_name: $branch,
      repo_url: $repo_url,
      repo_name: $repo_name,
      pr_url: $pr_url,
      pr_number: $pr_number,
      changes_summary: ("Shipped " + $wo_label)
    },
    update_work_order: {
      work_order_id: (if $wo_uuid != "" then $wo_uuid else "LOOKUP_VIA_get_work_order"),
      status: "in_review",
      branch_name: $branch,
      repo_url: $repo_url,
      repo_name: $repo_name,
      commit_hash: $commit_hash,
      commit_message: $commit_message,
      commit_author: $commit_author,
      pr_url: $pr_url,
      pr_number: $pr_number,
      files_changed: $files_changed,
      lines_added: $lines_added,
      lines_removed: $lines_removed,
      changed_files: $changed_files
    },
    wo_label: $wo_label
  }' >"$MCP_FILE"

echo ""
echo "==> GitHub PR: $PR_URL"
echo "==> Forge MCP payload written to .forge/last-ship-mcp.json"
echo "    Agent: call create_pull_request then update_work_order (status in_review) using that file."
cat "$MCP_FILE"
