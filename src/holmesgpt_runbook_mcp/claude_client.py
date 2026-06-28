"""Claude API client — classification (Haiku) and runbook drafting (Sonnet)."""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

import anthropic

logger = logging.getLogger(__name__)

_client: Optional[anthropic.Anthropic] = None

HAIKU_MODEL = "claude-haiku-4-5-20251001"
SONNET_MODEL = "claude-sonnet-4-6"


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


# ---------------------------------------------------------------------------
# Investigation classifier (Haiku — fast, cheap)
# ---------------------------------------------------------------------------

CLASSIFY_SYSTEM = """\
You are a platform engineering assistant. Analyse a HolmesGPT investigation
log and determine whether it represents a runbook gap (i.e. Holmes had to
improvise because no matching runbook existed in the knowledge base).

Return ONLY valid JSON — no prose, no markdown fences.
"""

CLASSIFY_SCHEMA = """\
{
  "has_gap": true,           // bool: true if Holmes improvised without a runbook
  "confidence": "high",      // high | medium | low
  "gap_description": "...",  // one sentence: what knowledge was missing
  "service": "payments-api", // service/component involved, or null
  "namespace": "payments",   // Kubernetes namespace, or null
  "failure_mode": "OOMKill", // failure class (OOMKill, CrashLoop, HighLatency, etc.), or null
  "alert_name": "KubePodOOMKilled", // exact Prometheus alert name if identifiable, or null
  "resolution": "...",       // what Holmes did to resolve, or null — becomes runbook content
  "severity": "P1"           // P1 | P2 | P3, or null
}
"""


def classify_investigation(
    investigation_log: str,
    alert_name: Optional[str] = None,
    namespace: Optional[str] = None,
) -> dict:
    """
    Classify a HolmesGPT investigation log.

    Uses Claude Haiku for speed and cost-efficiency. Returns a structured
    dict indicating whether a runbook gap exists and what it covers.
    """
    extra = ""
    if alert_name:
        extra += f"\nKnown alert name: {alert_name}"
    if namespace:
        extra += f"\nKnown namespace: {namespace}"

    prompt = f"""Classify this HolmesGPT investigation log.{extra}

Investigation log:
{investigation_log}

Return JSON matching this schema exactly:
{CLASSIFY_SCHEMA}"""

    client = _get_client()
    response = client.messages.create(
        model=HAIKU_MODEL,
        max_tokens=600,
        system=CLASSIFY_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.error("Haiku returned non-JSON: %s", raw)
        return {
            "has_gap": False,
            "confidence": "low",
            "gap_description": "Failed to parse classification response",
            "service": None,
            "namespace": namespace,
            "failure_mode": None,
            "alert_name": alert_name,
            "resolution": None,
            "severity": None,
        }


# ---------------------------------------------------------------------------
# Root cause analyser (Sonnet — deeper reasoning with runbook context)
# ---------------------------------------------------------------------------

RCA_SYSTEM = """\
You are an expert site reliability engineer performing root cause analysis.
You have access to Kubernetes events, pod logs, and relevant runbooks from
the knowledge base. Your job is to identify the most likely root cause,
explain your reasoning, and recommend the next diagnostic or remediation step.

Be specific. Reference exact metrics, error strings, or events where possible.
Return ONLY valid JSON — no prose, no markdown fences.
"""

RCA_SCHEMA = """\
{
  "most_likely_cause": "...",     // one sentence, specific
  "confidence": "high",           // high | medium | low
  "reasoning": "...",             // 2-4 sentences explaining the conclusion
  "recommended_action": "...",    // immediate next step (kubectl command or action)
  "escalate_immediately": false,  // bool: true if P1 and human should be paged now
  "matching_runbooks": [          // runbooks that match this failure mode
    {"title": "...", "url": "..."}
  ]
}
"""


def analyse_root_cause(
    *,
    alert_name: str,
    namespace: str,
    pod_logs: Optional[str] = None,
    k8s_events: Optional[str] = None,
    metrics_summary: Optional[str] = None,
    runbook_context: Optional[list[dict]] = None,
) -> dict:
    """
    Analyse root cause using investigation data + runbook knowledge base.

    Uses Claude Sonnet for deeper reasoning. Runbook context should be
    pre-fetched via confluence.search_runbooks() for the relevant service
    and failure mode.
    """
    sections = [f"**Alert:** {alert_name}", f"**Namespace:** {namespace}"]

    if metrics_summary:
        sections.append(f"**Metrics:**\n{metrics_summary}")
    if k8s_events:
        sections.append(f"**Kubernetes events:**\n```\n{k8s_events}\n```")
    if pod_logs:
        sections.append(f"**Pod logs (tail):**\n```\n{pod_logs[-3000:]}\n```")  # truncate

    if runbook_context:
        rb_text = "\n\n---\n\n".join(
            f"**{rb['title']}** ({rb.get('url', '')})\n{rb.get('body', '')[:1500]}"
            for rb in runbook_context
        )
        sections.append(f"**Relevant runbooks from knowledge base:**\n\n{rb_text}")

    prompt = "\n\n".join(sections)
    prompt += f"\n\nReturn JSON matching this schema exactly:\n{RCA_SCHEMA}"

    client = _get_client()
    response = client.messages.create(
        model=SONNET_MODEL,
        max_tokens=1200,
        system=RCA_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.error("Sonnet RCA returned non-JSON: %s", raw)
        return {
            "most_likely_cause": "Analysis failed — review logs manually",
            "confidence": "low",
            "reasoning": raw[:500],
            "recommended_action": "Check pod logs and events directly",
            "escalate_immediately": False,
            "matching_runbooks": [],
        }


# ---------------------------------------------------------------------------
# Runbook drafter (Sonnet — high-quality structured output)
# ---------------------------------------------------------------------------

DRAFT_SYSTEM = """\
You are a senior platform engineer writing a runbook for your team. Your output
will be parsed by a structured template — return ONLY valid JSON as specified.

The runbook must be practical: include real kubectl commands where identifiable
from the investigation logs, name specific metrics and thresholds, and provide
a decision tree that tells a junior engineer exactly what to check and in what
order. Write for someone who is on-call at 2am and has never seen this failure
before.
"""

DRAFT_SCHEMA = """\
{
  "title": "RB - <service> <failure_mode>",
  "quick_summary": "One paragraph. Service, alert, most common cause, typical resolution, MTTR.",
  "what_description": "One paragraph. What the alert means in plain English.",
  "impact": ["bullet 1", "bullet 2"],
  "why_causes": [
    ["Cause name", "Explanation of why this causes the alert"],
    ...
  ],
  "prerequisites": ["Access requirement 1", "Access requirement 2"],
  "prometheus_query": "the Prometheus/PromQL query that drives this alert, if identifiable",
  "symptom_signals": ["what the on-call engineer sees in Grafana/Alertmanager/kubectl"],
  "diagnosis_steps": [
    {
      "question": "Is this recurring?",
      "command": "kubectl get events -n <namespace> --field-selector reason=OOMKilling",
      "branches": [
        {"condition": "more than twice in 24h", "action": "go to resolution path 2"},
        {"condition": "first occurrence", "action": "go to resolution path 1"}
      ]
    }
  ],
  "resolution_paths": [
    {
      "id": "resolution-path-1-name",
      "name": "Resolution path 1: Rolling restart",
      "use_when": "first occurrence or traffic spike",
      "commands": "kubectl rollout restart deployment/<service> -n <namespace>",
      "verify": "kubectl get pods -n <namespace> -w"
    }
  ],
  "escalation": [
    {"severity": "P1 — service down", "contact": "@oncall-handle", "channel": "#incidents-production"},
    {"severity": "P2 — degraded", "contact": "@platform-team", "channel": "#platform-alerts"}
  ],
  "references": ["Link or runbook title 1", "Link or runbook title 2"],
  "mttr": "15m",
  "severity": "P1-P2"
}
"""


def draft_runbook(
    *,
    service: str,
    failure_mode: str,
    alert_name: str,
    investigation_logs: list[str],
    namespace: Optional[str] = None,
    existing_runbook_context: Optional[str] = None,
) -> dict:
    """
    Draft a structured runbook from HolmesGPT investigation logs.

    Uses Claude Sonnet. The output dict is passed directly to
    templates.render_runbook_template().
    """
    logs_text = "\n\n---\n\n".join(investigation_logs)
    extra = ""
    if namespace:
        extra += f"\nNamespace: {namespace}"
    if existing_runbook_context:
        extra += f"\n\nRelated existing runbook (for context/cross-links):\n{existing_runbook_context[:2000]}"

    prompt = f"""Write a runbook for this failure mode.

Service: {service}
Failure mode: {failure_mode}
Alert name: {alert_name}{extra}

Investigation logs (what Holmes actually did to resolve this):
{logs_text[:4000]}

The runbook must be based on what ACTUALLY HAPPENED in the logs above —
not hypothetical procedures. Extract real commands, real metrics, real
thresholds from the logs.

Return JSON matching this schema exactly:
{DRAFT_SCHEMA}"""

    client = _get_client()
    response = client.messages.create(
        model=SONNET_MODEL,
        max_tokens=3000,
        system=DRAFT_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.error("Sonnet draft returned non-JSON: %s", raw)
        # Return a minimal stub so the tool can still open a PR
        return {
            "title": f"RB - {service} {failure_mode}",
            "quick_summary": f"`{service}` triggered `{alert_name}`. Drafted from {len(investigation_logs)} investigation log(s). Review and complete before publishing.",
            "what_description": f"{alert_name} fired for {service}.",
            "impact": ["TBD"],
            "why_causes": [("Unknown", "Review investigation logs")],
            "prerequisites": ["kubectl access", "Grafana access"],
            "prometheus_query": "TBD",
            "symptom_signals": [f"{alert_name} in Alertmanager"],
            "diagnosis_steps": [],
            "resolution_paths": [],
            "escalation": [{"severity": "P1", "contact": "@platform-team", "channel": "#platform-alerts"}],
            "references": [],
            "mttr": "TBD",
            "severity": "P1-P2",
        }
