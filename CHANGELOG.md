# Changelog

All notable changes to PowerSync are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- Added daily, keyless Copilot Repo Assist automation, shared agent instructions, and Cursor Cloud workflow rules.

### Changed

- Repo Assist now reserves pull-request capacity, stops fallback cycles, validates repository PR requirements, and gives Copilot Cloud an executable gh-aw upgrade route.
- Graphite CI Optimizations now gate pull-request HACS and Hassfest validation
  while failing open to normal CI when the optimizer is unavailable.
