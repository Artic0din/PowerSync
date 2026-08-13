# GitHub support workflow

PowerSync support issues use GitHub as the system of record.
The workflows run on GitHub-hosted runners and do not require PowerBot, Discord, Docker, a server, or persistent watermark files.

GitHub Agentic Workflows is currently in public preview.
Keep the fork trial isolated from production systems until each transition has been validated.

## Intake and triage

Bug and feature forms apply `needs triage` when submitted.
The Copilot triage workflow checks the issue and existing comments, then applies one of these states:

- `needs information` means the reporter must supply a specific missing item.
- `needs investigation` means the evidence gates passed and repository investigation is appropriate.
- No workflow state label means a complete feature request or a support question awaiting maintainer handling.

The triage workflow checks the installed version first, then the system profile, problem and reproduction, monitoring mode, and relevant log window.
It reads the full comment history before asking for evidence so it does not repeat a previous request.

## Investigation and fixes

Only a maintainer with write access can start automated investigation by adding `agent ready`.
The investigation workflow classifies the issue and establishes a concrete repository root cause before editing code.

If the evidence is insufficient, it removes `agent ready` and comments with the exact blocker.
If a repository defect is confirmed, it reproduces the defect in a test, implements the smallest fix, runs relevant validation, and opens a ready-for-review pull request using `Refs #123`.

The workflow cannot merge, release, deploy, close an issue, or access Discord, PowerSync Cloud, production data, or customer systems.

## Delivery and solved confirmation

A maintainer records a delivered fix with one exact issue comment:

```text
/powersync delivered https://github.com/OWNER/REPOSITORY/pull/123 https://github.com/OWNER/REPOSITORY/releases/tag/VERSION
```

The deterministic workflow verifies that:

1. The command author has repository write access.
2. The pull request belongs to an allowed PowerSync repository and is merged.
3. Its latest check runs completed successfully.
4. The release is published, is not a draft, and was published after the merge.

When validation passes, the issue receives `awaiting confirmation` and an audit comment containing the pull request and release links.

The issue author or a maintainer then confirms the result with one exact comment:

```text
/powersync solved
```

Only that exact command closes an issue in `awaiting confirmation`.
The workflow adds `solved`, preserves the complete issue history, records who confirmed the result, and closes the issue as completed.

## Required repository secrets

Configure these Actions secrets before enabling the agentic workflows:

- `COPILOT_GITHUB_TOKEN`: an Artic0din-owned fine-grained token with only the account-level `Copilot Requests: read` permission.
- `GH_AW_CI_TRIGGER_TOKEN`: a Plaintext-Lab-owned fine-grained token restricted to this fork with `Contents: read and write` and `Pull requests: read and write`.

The first token charges inference to Artic0din's Copilot entitlement.
The second lets pull requests created by the workflow trigger normal GitHub checks; it does not provide model access.
Do not reuse Ryan's broad GitHub CLI login token for either secret.

## Trial sequence

1. Submit an intentionally incomplete throwaway bug and verify one consolidated evidence request.
2. Edit it with the missing evidence and verify the triage state changes without repeating the first request.
3. Add `agent ready` to a harmless issue with no concrete root cause and verify that no pull request is created.
4. Validate an agent-created fix through review, checks, merge, and a harmless fork release.
5. Run the delivered and solved commands and verify the issue closes only after the final confirmation.
