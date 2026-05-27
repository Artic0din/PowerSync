@AGENTS.md
@ENGINEERING_CONSTITUTION.md

# Claude Code — PowerSync fork specific

## Commit format (Ryan's branches only — NOT enforced on sync/*)

`{type}({scope}): {description}`

Types: `feat`, `fix`, `test`, `refactor`, `perf`, `docs`, `style`, `chore`
Scopes: `optimizer`, `battery`, `ev`, `pricing`, `services`, `views`, `sensors`, `config-flow`, `ci`, `docs`, `tests`, `audit`

## Branch rules

- Never commit to `main` directly
- Never rebase `main` — fast-forward from upstream only
- For Ryan's work: branch from `main`, name `{type}/{description}`
- For audit work: branch from `main`, name `audit/phase-{N}-{topic}`

## Upstream sync workflow

When pulling from upstream:
1. `git fetch upstream main`
2. `git checkout -b sync/upstream-$(date +%Y%m%d)`
3. `git merge upstream/main`
4. Resolve conflicts (rare — fork shouldn't diverge much)
5. Push, open PR to `main`
6. Advisory CI runs; pass = merge, fail = investigate but typically still merge
7. After merge, rebase Ryan's open feature branches onto new `main`

## PR workflow (Ryan's work)

1. Open as draft
2. Run `/self-review` locally
3. Flip to ready when CI green locally
4. Codex auto-reviews on push
5. Address P0/P1 only via `/fix-review`
6. Cap fix loop at 3 rounds
7. Manual merge (NO auto-merge on this repo)

## Upstream contribution flow

When a fork PR is ready to propose upstream:
1. Confirm PR is small, focused, single-concern
2. Confirm commit history is clean (rebase if needed)
3. Open PR `bolagnaise/PowerSync` from `Artic0din/PowerSync:feature-branch`
4. Use upstream's PR template (NOT this fork's)
5. Strip any references to fork-specific tooling, CI, or process
6. Be patient — upstream maintainer has their own cadence

## Reply formats (per review thread)

- Fix applied: `Fixed in <sha>. <rationale>`
- Pushing back: `Disagree: <reason>. Leaving as-is.`
- Known debt: `Acknowledged — tracked in audit Phase N.`

## HA safety

- Never edit `/config/.storage/*.json` on a live HA instance
- Never run background processes via SSH
- Verify entity names via `/api/states` before code references

## Secrets

- Run `gitleaks detect` before every push
- Never commit `.env`, tokens, API keys
- Token/API key in log = P0 fix immediately
