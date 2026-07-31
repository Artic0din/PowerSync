# Copilot Cloud gh-aw instructions

The repository Copilot setup installs the pinned `gh aw` CLI before an agent starts.

In Copilot Cloud, use that local CLI for workflow upgrades, fixes, and compilation.
Run `gh aw upgrade`, `gh aw fix --write`, and `gh aw compile` as appropriate.
Do not call `upgrade` or `fix` MCP tools; gh-aw v0.83.4 does not expose them.

Inspect all generated changes, run `gh aw validate`, and follow the repository pull-request workflow before publishing an upgrade.
