"""Tests for Confluence client — CQL building and response parsing."""

from holmesgpt_runbook_mcp.confluence import build_cql


def test_build_cql_service_only():
    cql = build_cql(service="payments-api")
    assert 'property["Service"]="payments-api"' in cql
    assert "type=page" in cql
    assert 'label="runbook"' in cql


def test_build_cql_full():
    cql = build_cql(
        service="payments-api",
        failure_mode="OOMKill",
        alert_name="KubePodOOMKilled",
        status="Approved",
    )
    assert 'property["Service"]="payments-api"' in cql
    assert 'property["Failure-Mode"]="OOMKill"' in cql
    assert 'property["Alert-Name"]="KubePodOOMKilled"' in cql
    assert 'property["Status"]="Approved"' in cql


def test_build_cql_no_filters():
    cql = build_cql()
    assert "type=page" in cql
    assert 'label="runbook"' in cql
    assert "property" not in cql


def test_build_cql_space_override():
    cql = build_cql(space="PLATFORM", service="ingestion")
    assert 'space="PLATFORM"' in cql
