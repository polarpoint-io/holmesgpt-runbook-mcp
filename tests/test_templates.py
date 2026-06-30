"""Tests for runbook template rendering."""

from holmesgpt_runbook_mcp.templates import render_runbook_template


def test_render_minimal():
    md = render_runbook_template(
        title="RB - payments-api OOMKill",
        service="payments-api",
        failure_mode="OOMKill",
        alert_name="KubePodOOMKilled",
    )
    assert "confluence_properties:" in md
    assert "Service: payments-api" in md
    assert "Failure-Mode: OOMKill" in md
    assert "Alert-Name: KubePodOOMKilled" in md
    assert "Status: Draft" in md
    assert "## Alert Match" in md
    assert "## Quick Summary" in md
    assert "## WHAT" in md
    assert "## WHY" in md
    assert "## HOW" in md
    assert "## Escalation" in md


def test_render_with_resolution_paths():
    md = render_runbook_template(
        title="RB - payments-api OOMKill",
        service="payments-api",
        failure_mode="OOMKill",
        alert_name="KubePodOOMKilled",
        resolution_paths=[
            {
                "id": "rolling-restart",
                "name": "Resolution path 1: Rolling restart",
                "use_when": "first occurrence",
                "commands": "kubectl rollout restart deployment/payments-api -n payments",
                "verify": "kubectl get pods -n payments -w",
            }
        ],
    )
    assert "Rolling restart" in md
    assert "kubectl rollout restart" in md
    assert "kubectl get pods" in md


def test_render_labels():
    md = render_runbook_template(
        title="test",
        service="ingestion-service",
        failure_mode="CrashLoop",
        alert_name="KubePodCrashLooping",
    )
    assert "labels: [runbook, ingestion-service, crashloop]" in md


def test_render_generated_by_note():
    md = render_runbook_template(
        title="test",
        service="s",
        failure_mode="f",
        alert_name="a",
    )
    assert "holmesgpt-runbook-mcp" in md
