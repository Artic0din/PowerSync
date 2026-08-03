# Changelog

All notable changes to PowerSync are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Fixed

- Recovered the fork-only v2.12.921 release cleanup after its repository
  notification step failed.

### Removed

- Removed the disposable support-workflow regression fixture after release
  testing.

### Changed

- Graphite CI Optimizations now gate pull-request HACS and Hassfest validation
  while failing open to normal CI when the optimizer is unavailable.
