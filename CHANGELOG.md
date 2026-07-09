# Changelog

All notable changes to this repository are documented in this file.

## Unreleased

### Changed

- CI workflow now skips Hassfest when a run is cancelled while still executing Hassfest after earlier step failures.

### Added

- Repository governance and community files:
  - `CONTRIBUTING.md`
  - `CODE_OF_CONDUCT.md`
  - `SECURITY.md`
  - `.github/CODEOWNERS`
  - `.github/pull_request_template.md`
  - `.github/ISSUE_TEMPLATE/*`
  - `.github/dependabot.yml`
  - `.github/labeler.yml`
  - `AGENTS.md`
  - `.github/copilot-instructions.md`
- Automation workflows:
  - `.github/workflows/ci.yml`
  - `.github/workflows/pr-automation.yml`
  - `.github/workflows/labeler.yml`
