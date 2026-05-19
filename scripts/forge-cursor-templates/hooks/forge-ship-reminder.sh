#!/bin/bash
# After agent stop: nudge ship flow when on a WO branch with unpushed commits or no PR yet.
# Never blocks; only adds context when conditions match.

input=$(cat)
BRANCH="$(git branch --show-current 2>/dev/null || true)"

if ! echo "$BRANCH" | grep -qE '^wo/WO-[0-9]+'; then
  exit 0
fi

WO="$(echo "$BRANCH" | sed -E 's#^wo/(WO-[0-9]+).*#\1#')"
NEEDS=""

if git rev-parse '@{u}' >/dev/null 2>&1; then
  AHEAD="$(git rev-list --count '@{u}..HEAD' 2>/dev/null || echo 0)"
  [[ "$AHEAD" != "0" ]] && NEEDS="push"
else
  NEEDS="push"
fi

if ! command -v gh >/dev/null 2>&1 || ! gh pr view >/dev/null 2>&1; then
  NEEDS="${NEEDS:+$NEEDS, }open PR"
fi

[[ -n "$NEEDS" ]] || exit 0

cat <<EOF
{"followup_message":"WO ship reminder ($WO): run \`./scripts/forge-wo-ship.sh $WO <work_order_uuid>\` then Forge MCP \`create_pull_request\` + \`update_work_order\` (status in_review). See docs/FORGE_WO_WORKFLOW.md."}
EOF
exit 0
