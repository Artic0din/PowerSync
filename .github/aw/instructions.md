# Copilot Cloud gh-aw instructions

The repository Copilot setup installs the pinned `gh aw` CLI before an agent starts.

In Copilot Cloud, use that local CLI for workflow upgrades, fixes, and compilation.
Run `gh aw upgrade`, `gh aw fix --write`, and `gh aw compile` as appropriate.
Do not call `upgrade` or `fix` MCP tools; gh-aw v0.83.4 does not expose them.

Inspect all generated changes and run `gh aw validate`.
Follow the repository pull-request workflow before publishing an upgrade.

Repo Assist pull-request outputs require repository secret `GH_AW_GITHUB_TOKEN`
(a PAT or GitHub App installation token with contents and pull-requests write).
Optionally set `GH_AW_CI_TRIGGER_TOKEN` for the empty-commit CI trigger path.
Without one of these credentials, created Repo Assist PRs will not run `validate.yml`.
