@AGENTS.md
@ENGINEERING_CONSTITUTION.md

# Claude Code — PowerSync fork specific

## When implementing

Apply the constitution. When stuck between approaches, use Priority Rules:
- Correctness over speed
- Systemic fix over local fix
- Maintainability over convenience

Before pushing, self-check against constitution principles 11 (Define Done), 12 (Root-Cause First), 13 (No Regression by Design).

## PR size

Target ≤200 lines, ceiling 400, split above 400. See AGENTS.md "PR size discipline" for the empirical basis. If your work naturally exceeds 400 lines, split into stacked PRs — one logical change per PR.

## Commit format (Ryan's branches only — NOT enforced on sync/*)

`{type}({scope}): {description}`

Valid types: `feat`, `fix`, `test`, `refactor`, `perf`, `docs`, `style`, `chore`

Valid scopes: `optimizer`, `battery`, `ev`, `pricing`, `services`, `views`, `sensors`, `config-flow`, `mobile-api`, `curtailment`, `providers`, `ci`, `docs`, `tests`, `audit`

## Branch rules

- Never commit to `main` directly
- Never rebase `main` — fast-forward from upstream only
- For Ryan's work: branch from `main`, name `{type}/{description}`
- For audit work: branch from `main`, name `audit/phase-{N}-{topic}`
- For upstream sync: branch from `main`, name `sync/upstream-YYYYMMDD`

## Upstream sync workflow

When pulling from `bolagnaise/PowerSync`:

```bash
git fetch upstream main
git checkout -b sync/upstream-$(date +%Y%m%d)
git merge upstream/main
# Resolve conflicts (rare — fork shouldn't diverge much)
git push origin HEAD
gh pr create --base main --title "sync: upstream $(date +%Y-%m-%d)" --body "Routine upstream sync."
```

Advisory CI runs only. Pass = merge. Fail = investigate, but typically still merge (upstream is the source of truth).

After merge, rebase Ryan's open feature branches onto new `main`.

## PR workflow (Ryan's work)

1. Open as draft
2. Run `/self-review` locally
3. Flip to ready when CI green locally
4. Codex auto-reviews on push (only fires on non-`sync/*` branches)
5. Address P0/P1 only via `/fix-review`
6. Cap fix loop at 3 rounds
7. **Manual merge** (NO auto-merge on this repo)

## Upstream contribution flow

When a fork PR is ready to propose upstream:

1. Confirm PR is small (< 400 lines), focused, single-concern
2. Confirm commit history clean (rebase autosquash if needed)
3. Strip any references to fork-specific tooling, CI, AGENTS.md, or Constitution
4. Read upstream's PR template at `bolagnaise/PowerSync/.github/PULL_REQUEST_TEMPLATE.md` (if exists)
5. Open PR:
   ```bash
   gh pr create --repo bolagnaise/PowerSync --base main --head Artic0din:<branch>
   ```
6. Use upstream's voice and conventions
7. Be patient — upstream maintainer has their own cadence
8. Log the upstream PR in `docs/audits/upstream-prs.md`

## Reply formats (per review thread)

- Fix applied: `Fixed in <sha>. <one-line rationale>`
- Pushing back: `Disagree: <reason>. Leaving as-is.` (do not resolve unilaterally)
- Known debt: `Acknowledged — tracked in audit Phase N.`
- P2/P3: `Acknowledged — tracked for later.`

## HA safety

- Never edit `/config/.storage/*.json` on a live HA instance
- Never run background processes via SSH to live HA
- Verify entity names via `/api/states` before referencing in code or tests
- Never write to Modbus registers in tests against real hardware

## Secrets

- Run `gitleaks detect --source . --no-git` before every push
- Never commit `.env`, tokens, API keys, credentials
- Token/API key in log = P0, fix immediately
- Long-lived access tokens used by mobile app — never log, never embed in responses

## Dependency & supply-chain hygiene

- **Before adopting any new PyPI dependency**, run `uvx guarddog pypi scan <package>` (malware/typosquat heuristics). Treat the result as one signal, not a verdict — GuardDog has been bypassed in research, so also sanity-check the package's provenance (repo, maintainers, release history). This is a pre-install gate, not CI.
- `pip-audit` (CI, advisory) scans the `manifest.json` runtime requirements against OSV on every PR. Triage advisories; promote to a blocking gate once the baseline is clean.
- `zizmor` (CI, gates on high severity) audits fork-owned workflows. Upstream-owned workflows (`release.yml`, `validate.yml`, etc.) are intentionally excluded to avoid sync divergence.
- **Pin every GitHub Action to a full commit SHA** with a trailing `# vX.Y.Z` comment. Dependabot's `github-actions` ecosystem (3-day cooldown) proposes SHA bumps.
- A CycloneDX SBOM is generated and attached on each published release (`sbom.yml`).

## Mobile app API contract

- Endpoints under `views/` are consumed by iOS/Android apps
- Breaking changes require coordinated mobile release
- Always preserve backwards compatibility on existing endpoints
- Version new contracts via path (`/v1/`, `/v2/`) not query params

## CI failure debugging

Read the actual job log before proposing a fix:

```bash
gh run view <run-id> --log-failed
```

Codex reasons from static analysis. CI failures need runtime evidence. Don't assume the fix from the finding description.

## Codex review signals

- 👀 reaction = reviewing
- 💬 comment = P0/P1 findings to address
- 👍 reaction = LGTM, no actionable findings
- No notification fires on 👍 — must poll

```bash
gh api repos/Artic0din/PowerSync/issues/{pr}/reactions \
  --jq '.[] | select(.user.login | test("codex|chatgpt"; "i")) | .content'
```

## Slash commands

- `/plan` — explore issue, propose design, no code
- `/implement` — execute against PLAN.md in fresh context
- `/self-review` — local lint, tests, gitleaks, P0/P1 check
- `/fix-review` — fetch Codex comments, apply P0/P1, push, reply
- `/ship` — final pre-merge steps (rebase autosquash, flip to ready)
- `/upstream-propose` — prepare fork PR for upstream submission
- `/sync-upstream` — pull from `bolagnaise/PowerSync`, open advisory-CI PR
