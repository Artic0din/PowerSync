# GitHub support workflow

PowerSync support issues use GitHub as the system of record.
The workflows run on GitHub-hosted runners and do not require PowerBot, Discord, a server, self-hosted Docker, or persistent watermark files.

GitHub Agentic Workflows is currently in public preview.
Keep the fork trial isolated from production systems until every transition has been validated.

## Safe evidence intake

GitHub uploads an attachment as soon as it is added to an issue editor, before the issue or comment is submitted.
Do not attach raw logs, credentials, archives, screenshots, or other binary evidence.

Use the [PowerSync support-bundle tool](https://plaintext-lab.github.io/PowerSync/) locally in the browser.
It removes credentials and consistently pseudonymises identifiers without sending the source log to a service.
Its output starts with `PowerSync sanitised support bundle v1`.

The deterministic intake workflow runs on public issue creation, edits, reopening, and new or edited replies.
It scans the complete current issue, comments, and linked GitHub attachments before Copilot is dispatched.
It accepts at most five UTF-8 text attachments, 512 KB each and 1 MB combined, in the documented text formats.
It rejects binary content, archives, unsupported formats, excessive structured-data nesting, missing bundle markers, oversized evidence, and credential patterns.

Unsafe evidence receives `unsafe evidence` and never reaches a model.
The warning explains how to replace it and instructs the reporter to revoke or rotate any credential that may already have been uploaded.
Removing an attachment link is not a confidentiality guarantee.

Evidence that passes receives `safe evidence`, and the intake workflow dispatches triage with the issue number.
Triage and investigation read a linked bundle only through the bounded support-bundle reader, which rechecks the GitHub attachment URL, 512 KB limit, UTF-8 text, marker, binary content, and credential patterns before returning evidence to the agent.
The workflow firewall permits only GitHub's domains and the exact GitHub production user-attachment bucket required by the download redirect.

## Automated triage and routing

The Copilot triage workflow reads the complete current issue and verifies `safe evidence` independently.
It checks the installed version first, then the system profile, problem and reproduction, monitoring mode, and sanitised log window.
It reads existing replies before asking for evidence so it does not repeat an earlier request.
Dispatched triage, assessment, and investigation runs use per-issue concurrency groups so separate reports cannot replace one another in the pending queue.

- Missing evidence receives one consolidated request and `needs information`.
- A complete bug receives `needs investigation` and is dispatched directly to issue investigation.
- A complete feature request is dispatched directly to feature assessment.
- Duplicate, spam, and off-topic classifications clear triage-state labels.

Feature assessment inspects current repository capabilities, overlap, dependencies, compatibility, and risk.
It posts a repository-aware recommendation and applies `feature assessed`, but does not make a roadmap commitment or create code.
All issue comments and labels use the workflow's short-lived `GITHUB_TOKEN`, so agent output cannot retrigger intake; the pull-request output alone uses the dedicated CI-trigger token.

## Investigation, review, and release

Issue investigation independently rechecks intake and every bug evidence gate before editing.
No concrete repository root cause means no patch or pull request.

For a confirmed defect, the workflow:

1. Adds and runs a failing regression test for the established cause.
2. Implements the smallest root-cause fix.
3. Increments the patch version and writes version-matched release notes.
4. Runs the focused and relevant repository validation.
5. Opens one ready-for-review pull request using `Refs #123`.

The workflow cannot directly merge, release, deploy, close an issue, or access Discord, PowerSync Cloud, production data, or customer systems.

Agent-created pull requests receive `automation`.
A deterministic workflow adds `merge-queue` only to same-repository, non-draft pull requests with that trusted label.
Graphite and required repository checks decide when the pull request can merge.
The existing version-bump workflow creates the release after merge.

## Delivery and solved confirmation

The release workflow dispatches deterministic support reconciliation because ordinary events created with `GITHUB_TOKEN` do not start another workflow.
Manual published releases also trigger reconciliation directly.

Reconciliation compares the new release with the previous published release, discovers associated merged pull requests, and accepts only explicit same-repository `Refs #123` links.
It verifies every page of the pull request head's latest check runs and requires the release publication time to be strictly after the merge time.
Valid linked issues receive `awaiting confirmation` and an audit comment containing the pull request and release links.

The issue author or a write-level maintainer confirms the released result with one exact comment:

```text
/powersync solved
```

Only that exact command closes an issue in `awaiting confirmation`.
The transition rereads live issue state after entering its concurrency queue and remains retryable after partial GitHub API failures.
It adds `solved`, records the confirming user, closes the issue, removes `awaiting confirmation` last, and retains the complete issue history.

The normal human steps are limited to supplying missing evidence and confirming the released result.

## Required repository secrets

Configure these Actions secrets before enabling the agentic workflows:

- `COPILOT_GITHUB_TOKEN`: an Artic0din-owned fine-grained token with only the account-level `Copilot Requests: read` permission.
- `GH_AW_CI_TRIGGER_TOKEN`: a Plaintext-Lab-owned fine-grained token restricted to this fork with `Contents: read and write` and `Pull requests: read and write`.

The first token charges inference to Artic0din's Copilot entitlement.
The second lets pull requests created by the workflow trigger normal GitHub checks and does not provide model access.
Do not reuse Ryan's broad GitHub CLI login token for either secret.

## Trial sequence

1. Try an unmarked raw log and verify `unsafe evidence` blocks every Copilot workflow.
2. Create a sanitised bundle, replace the evidence, and verify deterministic intake dispatches triage.
3. Submit an incomplete bug, supply the requested evidence in a reply, and verify the request is not repeated.
4. Exercise the no-root-cause path and verify no pull request is created.
5. Validate an agent-created fix through CI, automated review, Graphite merge, and a harmless fork release.
6. Verify the release moves the issue to `awaiting confirmation` automatically.
7. Verify conversational solved wording does nothing and `/powersync solved` closes the issue.
