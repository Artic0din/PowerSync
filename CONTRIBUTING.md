# Contributing to PowerSync

Thanks for contributing to PowerSync.

## Before you open a pull request

1. Open an issue (bug/feature) for larger changes so scope is clear.
2. Keep changes focused and avoid unrelated refactors.
3. Reuse existing scripts/tooling in the repository.

## Local development

PowerSync is a Python-based Home Assistant custom integration.

Common local validation command:

```bash
python3 -m pytest
```

If you change integration metadata or release behavior, ensure related docs are updated in at least one of:

- `README.md`
- `docs/`
- `CHANGELOG.md`

## Pull request expectations

- Use the repository pull request template.
- Select exactly one **Type of change** checkbox.
- Include command output under **Validation**.
- Do not move or relocate application code unless explicitly requested.
