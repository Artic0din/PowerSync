from pathlib import Path


WORKFLOWS = Path(".github/workflows")


def workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def test_triage_routes_only_the_triggering_issue() -> None:
    source = workflow("issue-triage.md")

    assert "dispatch-workflow:" not in source
    assert "SUPPORT_ISSUE_NUMBER: ${{ github.event.inputs.issue_number }}" in source
    assert '-f issue_number="$SUPPORT_ISSUE_NUMBER"' in source
    assert '-f evidence_revision="$SUPPORT_EVIDENCE_REVISION"' in source
    assert "id: refresh_evidence" in source
    assert (
        "SUPPORT_EVIDENCE_REVISION: "
        "${{ steps.refresh_evidence.outputs.evidence_revision }}" in source
    )
    assert "options: [issue-investigation, feature-assessment]" in source
    assert "needs: [agent, detection, safe_outputs]" in source
    assert "needs.safe_outputs.result == 'success'" in source
    assert "SUPPORT_EXPECTED_ROUTE: ${{ steps.route.outputs.destination }}" in source


def test_triage_applies_destination_classification_before_routing() -> None:
    source = workflow("issue-triage.md")

    assert "Add `bug` and `needs investigation`." in source
    assert (
        "add `enhancement`, remove `feature assessed`, `bug`, `question`, "
        "`needs triage`, `needs information`, and `needs investigation`" in source
    )


def test_every_route_refresh_requires_the_expected_label_state() -> None:
    triage = workflow("issue-triage.md")
    investigation = workflow("issue-investigation.md")
    assessment = workflow("feature-assessment.md")

    assert "SUPPORT_EXPECTED_ROUTE: ${{ steps.route.outputs.destination }}" in triage
    assert 'SUPPORT_EXPECTED_ROUTE: "feature-assessment"' in investigation
    assert 'SUPPORT_EXPECTED_ROUTE: "issue-investigation"' in assessment


def test_models_cannot_read_other_issue_content() -> None:
    for name in (
        "issue-triage.md",
        "issue-investigation.md",
        "feature-assessment.md",
    ):
        source = workflow(name)
        assert (
            "blocked: [github.com, api.github.com, raw.githubusercontent.com]"
            in source
        )
        assert "toolsets: [repos]" in source
        assert "min-integrity: none" not in source

        compiled = workflow(name.replace(".md", ".lock.yml"))
        assert '"GITHUB_TOOLSETS": "repos"' in compiled
        blocked_domains = (
            r'\"blockDomains\":[\"api.github.com\",\"github.com\",'
            r'\"raw.githubusercontent.com\"]'
        )
        assert blocked_domains in compiled
        assert (
            '"GITHUB_TOOLSETS": "context,repos,issues,pull_requests"'
            not in compiled
        )
        assert '"repos": "all"' not in compiled


def test_every_issue_mutation_and_snapshot_is_bound_to_the_dispatch_input() -> None:
    target = "target: ${{ github.event.inputs.issue_number }}"
    safe_output_path = (
        "GH_AW_SAFE_OUTPUTS: "
        "${{ steps.set-runtime-paths.outputs.GH_AW_SAFE_OUTPUTS }}"
    )
    for name in (
        "issue-triage.md",
        "feature-assessment.md",
        "issue-investigation.md",
    ):
        source = workflow(name)
        assert source.count(target) == 3
        assert safe_output_path in source
        assert (
            "SUPPORT_EVIDENCE_REVISION: ${{ github.event.inputs.evidence_revision }}"
            in source
        )
        assert "python -m scripts.revalidate_support_snapshot" in source


def test_every_agent_workflow_requires_an_evidence_revision() -> None:
    for name in (
        "issue-triage.md",
        "feature-assessment.md",
        "issue-investigation.md",
    ):
        source = workflow(name)
        assert "evidence_revision:" in source
        assert "required: true" in source


def test_workflow_contract_tests_run_in_ci() -> None:
    source = workflow("validate.yml")

    assert "tests/test_support_workflows.py" in source
    assert "version: v0.85.4" in source
    assert "gh aw compile issue-triage issue-investigation feature-assessment" in source
    assert "git diff --exit-code" in source


def test_feature_assessment_has_a_fail_closed_missing_evidence_branch() -> None:
    source = workflow("feature-assessment.md")

    assert "- needs information" in source
    assert "remove only `feature assessed`" in source
    assert "Do not assess the request" in source
    assert (
        "remove only `feature assessed`, `needs triage`, and `needs investigation`"
        in source
    )


def test_retriage_clears_stale_feature_assessment_state() -> None:
    source = workflow("issue-triage.md")
    add_labels, remove_labels = source.split("  remove-labels:", 1)

    assert "- feature assessed" not in add_labels.rsplit("  add-labels:", 1)[1]
    assert "- feature assessed" in remove_labels.split("  add-comment:", 1)[0]
    assert (
        "remove `feature assessed`, `needs triage`, and `needs investigation`"
        in source
    )
    assert (
        "Remove `feature assessed`, `needs triage`, and `needs information`"
        in source
    )
    assert (
        "request is complete, add `enhancement`, remove `feature assessed`, "
        "`bug`, `question`, `needs triage`, `needs information`, and "
        "`needs investigation`"
        in source
    )


def test_investigation_routes_reclassified_features_after_state_mutations() -> None:
    source = workflow("issue-investigation.md")

    assert "route-feature-assessment:" in source
    assert "needs: [agent, detection, safe_outputs]" in source
    assert "needs.safe_outputs.result == 'success'" in source
    assert "call `route_feature_assessment` once" in source
    assert "github-token: ${{ secrets.GH_AW_CI_TRIGGER_TOKEN }}" in source
    assert "id: refresh_evidence" in source
    assert (
        "SUPPORT_EVIDENCE_REVISION: "
        "${{ steps.refresh_evidence.outputs.evidence_revision }}" in source
    )
    assert "max: 3" in source.split("  remove-labels:", 1)[1].split(
        "  add-comment:", 1
    )[0]


def test_terminal_non_repository_conclusions_clear_stale_bug_state() -> None:
    source = workflow("issue-investigation.md")

    assert (
        "For unsupported or outdated versions, confirmed configuration issues, "
        "expected behaviour, third-party integration or hardware, and PowerSync "
        "Cloud or worker-side conclusions, remove both `bug` and "
        "`needs investigation`" in source
    )


def test_unanswered_support_questions_keep_needs_information() -> None:
    source = workflow("feature-assessment.md")

    assert (
        "If the available evidence is sufficient to answer, add `question`, "
        "remove `enhancement`, `feature assessed`, `bug`, `needs triage`, "
        "`needs information`, and `needs investigation`" in source
    )
    assert (
        "If a specific missing item prevents an answer, add `question` and "
        "`needs information`" in source
    )


def test_investigation_clears_state_only_after_pull_request_creation() -> None:
    source = workflow("issue-investigation.md")

    assert "finalize-created-fix:" in source
    assert "needs.safe_outputs.outputs.created_pr_url != ''" in source
    assert "call `finalize_created_fix` once" in source
    assert (
        "Do not request label removal for a repository defect; the deterministic "
        "finalizer clears those labels only after pull request creation succeeds."
        in source
    )
    finalizer = source.split("    finalize-created-fix:", 1)[1].split(
        "  create-pull-request:", 1
    )[0]
    assert "python -m scripts.revalidate_support_snapshot" in finalizer
    assert (
        "SUPPORT_EVIDENCE_REVISION: ${{ github.event.inputs.evidence_revision }}"
        in finalizer
    )
    assert finalizer.index("revalidate_support_snapshot") < finalizer.index(
        "gh issue edit"
    )


def test_feature_assessment_reclassifies_and_routes_misrouted_issues() -> None:
    source = workflow("feature-assessment.md")
    add_labels, remove_labels = source.split("  remove-labels:", 1)

    assert "route-issue-investigation:" in source
    assert "gh workflow run issue-investigation.lock.yml" in source
    assert "call `route_issue_investigation` once" in source
    assert "classify it independently as a feature request, bug, or support question" in source
    assert "- bug" in add_labels.rsplit("  add-labels:", 1)[1]
    assert "- question" in add_labels.rsplit("  add-labels:", 1)[1]
    assert "- needs investigation" in add_labels.rsplit("  add-labels:", 1)[1]
    assert "- enhancement" in remove_labels.split("  add-comment:", 1)[0]


def test_cross_classification_routing_has_a_single_hop_guard() -> None:
    for name, route_job in (
        ("issue-investigation.md", "route-feature-assessment:"),
        ("feature-assessment.md", "route-issue-investigation:"),
    ):
        source = workflow(name)
        route = source.split(f"    {route_job}", 1)[1].split(
            "  create-pull-request:" if name == "issue-investigation.md" else "  add-labels:",
            1,
        )[0]

        assert "routing_hops:" in source
        assert 'default: "0"' in source
        assert "github.event.inputs.routing_hops == '0'" in route
        assert "-f routing_hops=1" in route
        assert "If `routing_hops` is not `0`, do not call a cross-classification route" in source


def test_investigation_installs_manifest_runtime_requirements() -> None:
    source = workflow("issue-investigation.md")

    assert 'manifest.get("requirements")' in source
    assert 'subprocess.run(' in source
    assert '"pip", "install"' in source
    assert 'requirement.startswith("-")' in source
