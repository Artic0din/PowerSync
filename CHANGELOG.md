# Changelog

All notable changes to PowerSync are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- Added daily, keyless Copilot Repo Assist automation, shared agent instructions, and Cursor Cloud workflow rules.

### Changed

- Repo Assist GitHub MCP access is restricted to this repository.
- Repo Assist can close previous monthly activity issues, weights unlabelled PRs into Task 1, opens proposal issues for scheduled improvements, and requires `GH_AW_GITHUB_TOKEN` so created PRs trigger validation.
- Repo Assist now reserves pull-request capacity, stops fallback cycles, validates repository PR requirements, and gives Copilot Cloud an executable gh-aw upgrade route.
- Graphite CI Optimizations now gate pull-request HACS and Hassfest validation
  while failing open to normal CI when the optimizer is unavailable.

### Removed

- Removed the disposable support-workflow regression fixture after release
  testing.

### Fixed

- Recovered the fork-only v2.12.921 release cleanup after its repository
  notification step failed.
