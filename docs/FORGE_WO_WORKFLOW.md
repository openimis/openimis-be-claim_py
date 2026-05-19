# Forge work order ship workflow

Standing process for **every** Forge work order (WO) in ClaimsAdjudication.

| Item | Value |
|------|--------|
| Forge project | ClaimsAdjudication `5fc679ab-b55c-441c-848d-2b5435a7462d` |
| GitHub fork | [alekhyaakkiraju-droid/ClaimsAdjudication](https://github.com/alekhyaakkiraju-droid/ClaimsAdjudication) |
| Default PR base | `develop` |
| Branch pattern | `wo/WO-XXX-short-description` |

## One-command ship (after commit)

```bash
# WO label from branch wo/WO-001-baseline → auto-detects WO-001
./scripts/forge-wo-ship.sh WO-001 9e41bfef-ff94-45d1-8f10-65e6d47240ca
```

What the script does:

1. `git push -u origin <current-branch>`
2. `gh pr create` or reuse existing PR for the branch
3. Writes `.forge/last-ship-mcp.json` with payloads for Forge MCP

Then the agent (or you) **must** still call Forge MCP in the same session:

- `create_pull_request` — record PR in Forge  
- `update_work_order` — `status: in_review` + dev-activity fields  

## Full WO completion flow (agents)

1. Implement WO; validate acceptance criteria  
2. `prepare_commit` (Forge MCP)  
3. `git commit -m "[WO-XXX] …"` (pre-commit hook runs checklist)  
4. `./scripts/forge-wo-ship.sh WO-XXX <uuid>`  
5. `create_pull_request` + `update_work_order` (status `in_review`)  
6. Report PR URL to user  

## Setup (once per clone)

```bash
# Forge session
# MCP: set_project({ project_id: "5fc679ab-b55c-441c-848d-2b5435a7462d" })
# MCP: configure_repo({ ide: "cursor" })

./scripts/install-forge-cursor.sh
mkdir -p .forge
echo "5fc679ab-b55c-441c-848d-2b5435a7462d" > .forge/project-id
```

Optional env for local Forge REST (MCP remains canonical):

- `FORGE_API_TOKEN` or `FORGE_TOKEN`  
- `FORGE_API_URL` (default `http://127.0.0.1:3001`)  

## Cursor integration

| Artifact | Location | Committed? |
|----------|----------|------------|
| Agent rule | `.cursor/rules/forge-workflow.mdc` | No (install from template) |
| Pre-commit hook | `.cursor/hooks/forge-pre-commit.sh` | No |
| Ship reminder | `.cursor/hooks/forge-ship-reminder.sh` | No (stop hook, WO branches only) |
| Templates | `scripts/forge-cursor-templates/` | Yes |
| Ship script | `scripts/forge-wo-ship.sh` | Yes |

Install local hooks/rules:

```bash
./scripts/install-forge-cursor.sh
```

## WO UUID lookup

```bash
# Forge MCP
list_work_orders → find label wo:WO-XXX → copy id
```

Example: WO-001 → `9e41bfef-ff94-45d1-8f10-65e6d47240ca`

## Rules

- One commit per WO; message contains `[WO-XXX]`  
- Never force-push  
- Push + PR + Forge `in_review` happen together on completion — not optional  
