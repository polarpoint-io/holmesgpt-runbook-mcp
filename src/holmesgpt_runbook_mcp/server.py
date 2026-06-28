"""
HolmesGPT Runbook MCP Server.

Provides five tools for HolmesGPT (and any Confluence MCP client) to:
  1. Search runbooks by service / failure mode / alert name (CQL, not fuzzy text)
  2. Retrieve full runbook content from Confluence
  3. Classify an investigation log as a runbook gap or not
  4. Draft a new runbook from investigation logs and open a GitHub PR
  5. Analyse root cause using the runbook knowledge base + live incident data

Transport: streamable HTTP (port 8080) — deploy as a K8s pod alongside HolmesGPT.

Environment variables required:
  ANTHROPIC_API_KEY        — Claude API key
  CONFLUENCE_URL           — e.g. https://your-org.atlassian.net/wiki
  CONFLUENCE_USERNAME      — Atlassian email
  CONFLUENCE_API_TOKEN     — Atlassian API token
  GITHUB_TOKEN             — GitHub PAT with repo write access
  CONFLUENCE_RUNBOOK_SPACE — Confluence space key (default: RUNBOOKS)
  GITHUB_RUNBOOK_REPO      — GitHub repo (default: polarpoint-io/markdown-pol-docs)
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from . import confluence as cf
from . import claude_client as ai
from . import github as gh
from .templates import render_runbook_template

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP(
    "holmesgpt_runbook_mcp",
    instructions=(
        "Runbook search, gap detection, and root cause analysis for HolmesGPT. "
        "Use runbook_search first to find an existing runbook before calling "
        "root_cause_analyse or runbook_draft."
    ),
)


# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------

class RunbookSearchInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    service: Optional[str] = Field(
        default=None,
        description="Service name exactly as stored in confluence_properties (e.g. 'payments-api')",
    )
    failure_mode: Optional[str] = Field(
        default=None,
        description="Failure class exactly as stored (e.g. 'OOMKill', 'CrashLoop', 'HighLatency')",
    )
    alert_name: Optional[str] = Field(
        default=None,
        description="Exact Prometheus/Alertmanager alert name (e.g. 'KubePodOOMKilled')",
    )
    status: Optional[str] = Field(
        default="Approved",
        description="Runbook status filter: 'Approved', 'Draft', or None for all",
    )
    space: Optional[str] = Field(
        default=None,
        description="Confluence space key override (uses CONFLUENCE_RUNBOOK_SPACE env var by default)",
    )
    limit: int = Field(
        default=5,
        description="Maximum runbooks to return",
        ge=1,
        le=20,
    )


class RunbookGetInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    page_id: Optional[str] = Field(
        default=None,
        description="Confluence page ID (preferred — faster than title lookup)",
    )
    title: Optional[str] = Field(
        default=None,
        description="Exact Confluence page title (used if page_id not provided)",
    )


class InvestigationClassifyInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    investigation_log: str = Field(
        ...,
        description="Full text of the HolmesGPT investigation log or Slack message",
        min_length=20,
    )
    alert_name: Optional[str] = Field(
        default=None,
        description="Alert name if already known (improves classification accuracy)",
    )
    namespace: Optional[str] = Field(
        default=None,
        description="Kubernetes namespace if already known",
    )


class RunbookDraftInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    service: str = Field(
        ...,
        description="Service name (e.g. 'payments-api')",
        min_length=1,
    )
    failure_mode: str = Field(
        ...,
        description="Failure class (e.g. 'OOMKill')",
        min_length=1,
    )
    alert_name: str = Field(
        ...,
        description="Exact alert name (e.g. 'KubePodOOMKilled')",
        min_length=1,
    )
    investigation_logs: list[str] = Field(
        ...,
        description="List of HolmesGPT investigation logs for this failure mode",
        min_length=1,
    )
    namespace: Optional[str] = Field(
        default=None,
        description="Kubernetes namespace (improves command accuracy in draft)",
    )
    priority: str = Field(
        default="medium",
        description="Draft priority: 'high' (3+ incidents), 'medium', or 'low'",
    )


class RootCauseInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    alert_name: str = Field(
        ...,
        description="Prometheus/Alertmanager alert name",
        min_length=1,
    )
    namespace: str = Field(
        ...,
        description="Kubernetes namespace of the affected workload",
        min_length=1,
    )
    service: Optional[str] = Field(
        default=None,
        description="Service name — used to pre-fetch matching runbooks from Confluence",
    )
    pod_logs: Optional[str] = Field(
        default=None,
        description="Recent pod logs (tail -100 or similar)",
    )
    k8s_events: Optional[str] = Field(
        default=None,
        description="kubectl get events output for the namespace",
    )
    metrics_summary: Optional[str] = Field(
        default=None,
        description="Key metric values at time of alert (e.g. memory_working_set: 254Mi / 256Mi limit)",
    )


# ---------------------------------------------------------------------------
# Tool 1: runbook_search
# ---------------------------------------------------------------------------

@mcp.tool(
    name="runbook_search",
    annotations={
        "title": "Search Runbooks by Service / Failure Mode / Alert",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def runbook_search(params: RunbookSearchInput) -> str:
    """
    Search the Confluence runbook space using Page Properties CQL.

    Performs a structured CQL query rather than full-text search — uses the
    confluence_properties frontmatter published by the MkDocs plugin. This
    returns the exact runbook for a given service + failure mode instead of
    a fuzzy ranked list.

    Call this FIRST during incident response before trying root_cause_analyse
    or runbook_draft. If a matching runbook is found, use runbook_get to
    retrieve the full content.

    Args:
        params (RunbookSearchInput):
            - service: service name as in confluence_properties (e.g. 'payments-api')
            - failure_mode: failure class (e.g. 'OOMKill', 'CrashLoop')
            - alert_name: exact Prometheus alert name (e.g. 'KubePodOOMKilled')
            - status: 'Approved' (default), 'Draft', or None for all
            - space: Confluence space key override
            - limit: max results (1–20, default 5)

    Returns:
        str: JSON array of matching runbooks, each with:
             {id, title, url, service, failure_mode, alert_name,
              severity, mttr, owner, status}
             or a message if no runbooks found.

    Examples:
        - "Find the OOMKill runbook for payments-api"
          → params: service="payments-api", failure_mode="OOMKill"
        - "What runbook covers KubePodOOMKilled?"
          → params: alert_name="KubePodOOMKilled"
        - "Are there any draft runbooks for the ingestion service?"
          → params: service="ingestion", status="Draft"
    """
    try:
        results = cf.search_runbooks(
            service=params.service,
            failure_mode=params.failure_mode,
            alert_name=params.alert_name,
            status=params.status,
            space=params.space,
            limit=params.limit,
        )
        if not results:
            query_parts = [
                p for p in [
                    f"service={params.service}" if params.service else None,
                    f"failure_mode={params.failure_mode}" if params.failure_mode else None,
                    f"alert_name={params.alert_name}" if params.alert_name else None,
                ]
                if p
            ]
            return json.dumps({
                "found": 0,
                "message": f"No approved runbooks found for {', '.join(query_parts) or 'the given criteria'}. "
                           "Consider calling runbook_draft to create one.",
                "runbooks": [],
            })

        return json.dumps({
            "found": len(results),
            "runbooks": results,
        }, indent=2)

    except Exception as exc:
        logger.exception("runbook_search failed")
        return json.dumps({"error": f"Confluence search failed: {exc}"})


# ---------------------------------------------------------------------------
# Tool 2: runbook_get
# ---------------------------------------------------------------------------

@mcp.tool(
    name="runbook_get",
    annotations={
        "title": "Get Full Runbook Content from Confluence",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def runbook_get(params: RunbookGetInput) -> str:
    """
    Retrieve the full content of a Confluence runbook page.

    Use after runbook_search returns a matching page — pass the page id from
    the search result for fastest retrieval. Falls back to title lookup if
    only a title is provided.

    Args:
        params (RunbookGetInput):
            - page_id: Confluence page ID (preferred)
            - title: Exact page title (used if page_id not provided)

    Returns:
        str: JSON object with:
             {id, title, url, body, properties}
             body is in Confluence storage format (XML-like) — Holmes reads it natively.

    Examples:
        - "Get the full OOMKill runbook" (after search returned page_id "12345")
          → params: page_id="12345"
        - "Get the payments-api runbook by name"
          → params: title="RB - payments-api OOMKill"
    """
    if not params.page_id and not params.title:
        return json.dumps({"error": "Provide either page_id or title"})

    try:
        if params.page_id:
            content = cf.get_runbook_content(params.page_id)
        else:
            content = cf.find_page_by_title(params.title)
            if not content:
                return json.dumps({"error": f"No page found with title: {params.title}"})
        return json.dumps(content, indent=2)
    except Exception as exc:
        logger.exception("runbook_get failed")
        return json.dumps({"error": f"Failed to retrieve page: {exc}"})


# ---------------------------------------------------------------------------
# Tool 3: investigation_classify
# ---------------------------------------------------------------------------

@mcp.tool(
    name="investigation_classify",
    annotations={
        "title": "Classify Investigation Log as Runbook Gap",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def investigation_classify(params: InvestigationClassifyInput) -> str:
    """
    Classify a HolmesGPT investigation log to detect runbook gaps.

    Calls Claude Haiku to determine whether Holmes improvised without a
    matching runbook, and extracts structured gap metadata. Call this on
    every completed investigation to build up the gap store.

    Args:
        params (InvestigationClassifyInput):
            - investigation_log: full text of the investigation log or Slack message
            - alert_name: alert name if already known (improves accuracy)
            - namespace: Kubernetes namespace if already known

    Returns:
        str: JSON object with:
             {has_gap, confidence, gap_description, service, namespace,
              failure_mode, alert_name, resolution, severity}

    Examples:
        - Classify a Slack message from the #holmes-investigations channel
          → params: investigation_log="<slack message text>"
        - Classify with known context
          → params: investigation_log="...", alert_name="KubePodOOMKilled", namespace="payments"

    Error Handling:
        - Returns has_gap=false with confidence=low if classification fails
        - Check confidence field — only act on high/medium confidence gaps
    """
    try:
        result = ai.classify_investigation(
            params.investigation_log,
            alert_name=params.alert_name,
            namespace=params.namespace,
        )
        return json.dumps(result, indent=2)
    except Exception as exc:
        logger.exception("investigation_classify failed")
        return json.dumps({
            "has_gap": False,
            "confidence": "low",
            "error": f"Classification failed: {exc}",
        })


# ---------------------------------------------------------------------------
# Tool 4: runbook_draft
# ---------------------------------------------------------------------------

@mcp.tool(
    name="runbook_draft",
    annotations={
        "title": "Draft Runbook from Investigation Logs and Open GitHub PR",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def runbook_draft(params: RunbookDraftInput) -> str:
    """
    Draft a new runbook from HolmesGPT investigation logs and open a GitHub PR.

    Uses Claude Sonnet to extract diagnosis steps, resolution paths, and
    escalation details from real investigation logs. The draft is written
    in the Polarpoint AI-optimised runbook format with confluence_properties
    frontmatter, then opened as a draft GitHub PR for human review.

    The PR starts with Status: Draft. A platform engineer must review and
    change Status to Approved before the runbook is published to Confluence
    (via the MkDocs CI build).

    Call this when:
    - investigation_classify returns has_gap=true with confidence >= medium
    - The same gap has appeared 2+ times (high priority)
    - An engineer explicitly requests a runbook be drafted

    Args:
        params (RunbookDraftInput):
            - service: service name (e.g. 'payments-api')
            - failure_mode: failure class (e.g. 'OOMKill')
            - alert_name: exact alert name (e.g. 'KubePodOOMKilled')
            - investigation_logs: list of investigation log texts
            - namespace: Kubernetes namespace (improves command accuracy)
            - priority: 'high', 'medium', or 'low'

    Returns:
        str: JSON object with:
             {pr_url, pr_number, branch_name, file_path, runbook_title, draft_preview}
             draft_preview is the first 500 chars of the generated markdown.

    Examples:
        - "Draft a runbook for the payments OOMKill gap"
          → params: service="payments-api", failure_mode="OOMKill",
                    alert_name="KubePodOOMKilled",
                    investigation_logs=["<log1>", "<log2>"]

    Error Handling:
        - If Sonnet drafting fails, a minimal stub runbook is still opened as a PR
        - GitHub API errors are returned in the error field
    """
    try:
        # Check for an existing runbook to use as cross-link context
        existing = cf.search_runbooks(service=params.service, failure_mode=params.failure_mode, limit=1)
        existing_context = None
        if existing:
            try:
                page = cf.get_runbook_content(existing[0]["id"])
                existing_context = page.get("body", "")[:2000]
            except Exception:
                pass

        # Draft with Claude Sonnet
        draft_data = ai.draft_runbook(
            service=params.service,
            failure_mode=params.failure_mode,
            alert_name=params.alert_name,
            investigation_logs=params.investigation_logs,
            namespace=params.namespace,
            existing_runbook_context=existing_context,
        )

        # Render to the Polarpoint template format
        runbook_md = render_runbook_template(
            title=draft_data.get("title", f"RB - {params.service} {params.failure_mode}"),
            service=params.service,
            failure_mode=params.failure_mode,
            alert_name=params.alert_name,
            severity=draft_data.get("severity", "P1-P2"),
            mttr=draft_data.get("mttr", "TBD"),
            namespace=params.namespace or "TBD",
            prometheus_query=draft_data.get("prometheus_query", "TBD"),
            symptom_signals=draft_data.get("symptom_signals"),
            quick_summary=draft_data.get("quick_summary", ""),
            what_description=draft_data.get("what_description", ""),
            impact=draft_data.get("impact"),
            why_causes=draft_data.get("why_causes"),
            prerequisites=draft_data.get("prerequisites"),
            diagnosis_steps=draft_data.get("diagnosis_steps"),
            resolution_paths=draft_data.get("resolution_paths"),
            escalation=draft_data.get("escalation"),
            references=draft_data.get("references"),
        )

        # Open GitHub PR
        pr_result = gh.open_runbook_pr(
            title=draft_data.get("title", f"RB - {params.service} {params.failure_mode}"),
            service=params.service,
            failure_mode=params.failure_mode,
            runbook_markdown=runbook_md,
            source_count=len(params.investigation_logs),
            priority=params.priority,
        )

        return json.dumps({
            "pr_url": pr_result["pr_url"],
            "pr_number": pr_result["pr_number"],
            "branch_name": pr_result["branch_name"],
            "file_path": pr_result["file_path"],
            "runbook_title": draft_data.get("title"),
            "draft_preview": runbook_md[:500] + "...",
        }, indent=2)

    except Exception as exc:
        logger.exception("runbook_draft failed")
        return json.dumps({"error": f"Draft failed: {exc}"})


# ---------------------------------------------------------------------------
# Tool 5: root_cause_analyse
# ---------------------------------------------------------------------------

@mcp.tool(
    name="root_cause_analyse",
    annotations={
        "title": "Analyse Root Cause Using Runbook Knowledge Base",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def root_cause_analyse(params: RootCauseInput) -> str:
    """
    Analyse root cause of an active incident using the runbook knowledge base.

    First searches Confluence for runbooks matching the alert and service
    (using Page Properties CQL), then uses Claude Sonnet to reason over the
    runbook content + live incident data to identify the most likely root cause
    and recommend an immediate next step.

    Call this during an active incident when you have pod logs or events and
    want to determine root cause before deciding whether to escalate.

    Args:
        params (RootCauseInput):
            - alert_name: Prometheus/Alertmanager alert name (required)
            - namespace: Kubernetes namespace (required)
            - service: service name for runbook lookup (optional but improves accuracy)
            - pod_logs: recent pod logs (optional, tail -100 is sufficient)
            - k8s_events: kubectl get events output (optional)
            - metrics_summary: key metric values at alert time (optional)

    Returns:
        str: JSON object with:
             {most_likely_cause, confidence, reasoning, recommended_action,
              escalate_immediately, matching_runbooks}
             escalate_immediately=true means P1 — page the on-call engineer now.

    Examples:
        - "What's causing the OOMKill on payments-api?"
          → params: alert_name="KubePodOOMKilled", namespace="payments",
                    service="payments-api", pod_logs="<logs>",
                    metrics_summary="memory_working_set: 254Mi / 256Mi limit"
        - "Diagnose the high latency alert on ingestion"
          → params: alert_name="HighP99Latency", namespace="ingestion",
                    k8s_events="<events>", metrics_summary="p99: 4200ms"

    Error Handling:
        - Returns confidence=low if Sonnet analysis fails
        - Runbook context fetch failures are non-fatal (analysis proceeds without KB)
    """
    try:
        # Pre-fetch matching runbooks for knowledge base context
        runbook_context: list[dict] = []
        if params.service or params.alert_name:
            matching = cf.search_runbooks(
                service=params.service,
                alert_name=params.alert_name,
                status="Approved",
                limit=3,
            )
            for rb in matching:
                try:
                    page = cf.get_runbook_content(rb["id"])
                    runbook_context.append(page)
                except Exception:
                    pass

        result = ai.analyse_root_cause(
            alert_name=params.alert_name,
            namespace=params.namespace,
            pod_logs=params.pod_logs,
            k8s_events=params.k8s_events,
            metrics_summary=params.metrics_summary,
            runbook_context=runbook_context if runbook_context else None,
        )

        # Enrich matching_runbooks with URLs from our Confluence search
        if runbook_context:
            result["matching_runbooks"] = [
                {"title": rb["title"], "url": rb["url"]}
                for rb in runbook_context
            ]

        return json.dumps(result, indent=2)

    except Exception as exc:
        logger.exception("root_cause_analyse failed")
        return json.dumps({
            "most_likely_cause": "Analysis failed — check logs manually",
            "confidence": "low",
            "reasoning": str(exc),
            "recommended_action": "kubectl describe pod -n <namespace> <pod-name>",
            "escalate_immediately": False,
            "matching_runbooks": [],
        })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="streamable_http", port=8080)
