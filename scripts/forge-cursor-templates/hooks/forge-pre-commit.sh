#!/bin/bash
#
# Forge pre-commit hook for Cursor Agent.
#
# Only activates for work-order commits (message contains [TASK-] or [WO-]).
# Regular commits pass through unblocked.
#
# Protocol: receives JSON on stdin, writes JSON to stdout.
# Exit 0 = use returned JSON; exit 2 = hard deny.
#
# Coordination with .git/hooks/commit-msg:
#   This hook ONLY peeks at the marker — it never deletes it. The native
#   commit-msg hook is the canonical authority and consumes the marker after
#   the commit message is validated. If we deleted it here, the commit-msg
#   hook would then block the commit because the marker would be gone by the
#   time git invokes it.

input=$(cat)

if ! echo "$input" | grep -qE '\[(TASK|WO)-'; then
  echo '{"permission": "allow"}'
  exit 0
fi

PROJECT_DIR="${CURSOR_PROJECT_DIR:-.}"
MARKER="$PROJECT_DIR/.forge-commit-ready"

if [ -f "$MARKER" ]; then
  echo '{"permission": "allow"}'
  exit 0
fi

cat << 'DENY'
{
  "permission": "deny",
  "user_message": "Forge pre-commit checklist must be completed before committing.",
  "agent_message": "FORGE PRE-COMMIT CHECKLIST — You must complete ALL steps below before the commit can proceed.\n\n## Step 1 — Unit Tests\n1. Run `git diff --cached --name-only` to get the list of staged files.\n2. For each staged source file, determine whether a corresponding unit test file exists (e.g., `foo.ts` → `foo.test.ts` or `foo.spec.ts`).\n3. If a test file is MISSING, auto-generate a unit test file for it.\n4. Run the full test suite for all affected test files.\n5. If any test FAILS:\n   a. Attempt to auto-fix the test.\n   b. If the fix requires changes to business/production logic (not just test code), you MUST ask the user for consent before making the change.\n   c. Re-run the tests to confirm they pass.\n\n## Step 2 — Acceptance Criteria Validation\n1. Call the Forge MCP `get_work_order` tool to retrieve the full work order, including its acceptance criteria.\n2. For each acceptance criterion, reason through whether your implementation satisfies it.\n   Use your knowledge of the changes you just made — you do NOT need to run `git diff` for this.\n3. For each criterion, determine: pass / partial / fail, with a one-sentence rationale.\n4. If any criterion is NOT fully satisfied:\n   a. State which criteria are unmet and why.\n   b. Ask the user how to proceed before continuing.\n5. Once all criteria are satisfied, proceed to Step 3.\n   Keep your validation summary — you will include it in the `commit_summary` field in Step 3.\n\n## Step 3 — Report to Forge\nUse the Forge MCP `update_work_order` tool to update the work order with:\n- `work_order_id`: the current work order ID\n- `commit_summary`: a brief description of the changes being committed\n- `test_summary`: summary of test execution (how many tests ran, passed, failed, auto-generated)\n\nYou MUST also include these structured fields for activity tracking:\n- `repo_url`: the remote origin URL (run `git remote get-url origin`)\n- `repo_name`: owner/repo extracted from the URL\n- `branch_name`: current branch (run `git branch --show-current`)\n- `commit_hash`: the full SHA of the commit being made (use `git rev-parse HEAD` after committing, or the staged commit SHA)\n- `commit_message`: the commit message text\n- `commit_author`: author name (run `git config user.name`)\n- `files_changed`: number of files changed (from `git diff --cached --stat`)\n- `lines_added`: total lines added\n- `lines_removed`: total lines removed\n- `changed_files`: array of objects with `path`, `additions`, and `deletions` per file (from `git diff --cached --numstat`)\n- `tests_total`: total number of tests run\n- `tests_passed`: number of tests that passed\n- `tests_failed`: number of tests that failed\n- `tests_skipped`: number of tests skipped (0 if none)\n- `test_coverage`: code coverage percentage if available (0-100), omit if not available\n\n## Step 4 — Mark Ready\nAfter ALL steps above are complete, create a file called `.forge-commit-ready` in the project root (write the text 'ready' to it), then retry the exact same `git commit` command."
}
DENY
exit 0
