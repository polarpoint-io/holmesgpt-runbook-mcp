"""
LLM client — provider-agnostic interface for classification and generation.

Supported providers (set via LLM_PROVIDER env var):
  anthropic         — Anthropic Claude (default)
  openai            — OpenAI API
  azure             — Azure OpenAI Service
  openai-compatible — Any OpenAI-compatible endpoint (Ollama, vLLM, Together, etc.)

Required environment variables (per provider):
  All providers:
    LLM_PROVIDER          — provider name (default: anthropic)
    LLM_FAST_MODEL        — model for cheap/fast calls (classification)
    LLM_CAPABLE_MODEL     — model for deep reasoning (drafting, RCA)

  anthropic:
    LLM_API_KEY or ANTHROPIC_API_KEY

  openai:
    LLM_API_KEY or OPENAI_API_KEY

  azure:
    LLM_API_KEY or AZURE_OPENAI_API_KEY
    LLM_BASE_URL          — e.g. https://<resource>.openai.azure.com/
    LLM_AZURE_API_VERSION — API version (default: 2024-02-01)

  openai-compatible:
    LLM_API_KEY           — token for the endpoint (use 'none' if not required)
    LLM_BASE_URL          — full base URL, e.g. http://localhost:11434/v1
"""

from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Provider detection and defaults
# ---------------------------------------------------------------------------

PROVIDER = os.environ.get("LLM_PROVIDER", "anthropic").lower().strip()

_PROVIDER_DEFAULTS: dict[str, dict[str, str]] = {
    "anthropic": {
        "fast": "claude-haiku-4-5-20251001",
        "capable": "claude-sonnet-4-6",
    },
    "openai": {
        "fast": "gpt-4o-mini",
        "capable": "gpt-4o",
    },
    "azure": {
        "fast": "gpt-4o-mini",
        "capable": "gpt-4o",
    },
    "openai-compatible": {
        "fast": "llama3",
        "capable": "llama3",
    },
}

_defaults = _PROVIDER_DEFAULTS.get(PROVIDER, _PROVIDER_DEFAULTS["openai"])

FAST_MODEL = os.environ.get("LLM_FAST_MODEL", _defaults["fast"])
CAPABLE_MODEL = os.environ.get("LLM_CAPABLE_MODEL", _defaults["capable"])


def _api_key(env_candidates: list[str]) -> str:
    """Return first non-empty env var from the list, or raise."""
    for name in env_candidates:
        val = os.environ.get(name, "").strip()
        if val:
            return val
    raise RuntimeError(
        f"No API key found. Set one of: {', '.join(env_candidates)}"
    )


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class _LLMClient(ABC):
    """Minimal interface used throughout this package."""

    @abstractmethod
    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int,
        model: str,
    ) -> str:
        """Send a system + user message and return the response text."""


# ---------------------------------------------------------------------------
# Anthropic implementation
# ---------------------------------------------------------------------------

class _AnthropicClient(_LLMClient):
    def __init__(self) -> None:
        import anthropic  # noqa: PLC0415  (lazy import)
        key = _api_key(["LLM_API_KEY", "ANTHROPIC_API_KEY"])
        self._client = anthropic.Anthropic(api_key=key)

    def complete(self, *, system: str, user: str, max_tokens: int, model: str) -> str:
        response = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return response.content[0].text.strip()


# ---------------------------------------------------------------------------
# OpenAI implementation (also covers openai-compatible and azure)
# ---------------------------------------------------------------------------

class _OpenAIClient(_LLMClient):
    def __init__(self, *, base_url: Optional[str] = None, api_version: Optional[str] = None) -> None:
        import openai  # noqa: PLC0415

        key = _api_key(["LLM_API_KEY", "OPENAI_API_KEY", "AZURE_OPENAI_API_KEY"])

        if PROVIDER == "azure":
            self._client = openai.AzureOpenAI(
                api_key=key,
                azure_endpoint=base_url or os.environ["LLM_BASE_URL"],
                api_version=api_version or os.environ.get("LLM_AZURE_API_VERSION", "2024-02-01"),
            )
        else:
            kwargs: dict = {"api_key": key}
            if base_url:
                kwargs["base_url"] = base_url
            self._client = openai.OpenAI(**kwargs)

    def complete(self, *, system: str, user: str, max_tokens: int, model: str) -> str:
        response = self._client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return (response.choices[0].message.content or "").strip()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_client_instance: Optional[_LLMClient] = None


def _get_client() -> _LLMClient:
    global _client_instance
    if _client_instance is not None:
        return _client_instance

    if PROVIDER == "anthropic":
        _client_instance = _AnthropicClient()
    elif PROVIDER in ("openai", "azure", "openai-compatible"):
        base_url = os.environ.get("LLM_BASE_URL")
        _client_instance = _OpenAIClient(base_url=base_url)
    else:
        raise ValueError(
            f"Unknown LLM_PROVIDER '{PROVIDER}'. "
            "Choose: anthropic | openai | azure | openai-compatible"
        )

    logger.info("LLM provider: %s | fast=%s | capable=%s", PROVIDER, FAST_MODEL, CAPABLE_MODEL)
    return _client_instance


# ---------------------------------------------------------------------------
# Prompt constants
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


# ---------------------------------------------------------------------------
# Public API — same signatures as the old claude_client module
# ---------------------------------------------------------------------------

def classify_investigation(
    investigation_log: str,
    alert_name: Optional[str] = None,
    namespace: Optional[str] = None,
) -> dict:
    """
    Classify a HolmesGPT investigation log.

    Uses the fast/cheap model (LLM_FAST_MODEL). Returns a structured dict
    indicating whether a runbook gap exists and what it covers.
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
    raw = client.complete(system=CLASSIFY_SYSTEM, user=prompt, max_tokens=600, model=FAST_MODEL)

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.error("classify_investigation: model returned non-JSON: %s", raw[:200])
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

    Uses the capable model (LLM_CAPABLE_MODEL).
    """
    sections = [f"**Alert:** {alert_name}", f"**Namespace:** {namespace}"]

    if metrics_summary:
        sections.append(f"**Metrics:**\n{metrics_summary}")
    if k8s_events:
        sections.append(f"**Kubernetes events:**\n```\n{k8s_events}\n```")
    if pod_logs:
        sections.append(f"**Pod logs (tail):**\n```\n{pod_logs[-3000:]}\n```")

    if runbook_context:
        rb_text = "\n\n---\n\n".join(
            f"**{rb['title']}** ({rb.get('url', '')})\n{rb.get('body', '')[:1500]}"
            for rb in runbook_context
        )
        sections.append(f"**Relevant runbooks from knowledge base:**\n\n{rb_text}")

    prompt = "\n\n".join(sections)
    prompt += f"\n\nReturn JSON matching this schema exactly:\n{RCA_SCHEMA}"

    client = _get_client()
    raw = client.complete(system=RCA_SYSTEM, user=prompt, max_tokens=1200, model=CAPABLE_MODEL)

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.error("analyse_root_cause: model returned non-JSON: %s", raw[:200])
        return {
            "most_likely_cause": "Analysis failed — review logs manually",
            "confidence": "low",
            "reasoning": raw[:500],
            "recommended_action": "Check pod logs and events directly",
            "escalate_immediately": False,
            "matching_runbooks": [],
        }


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

    Uses the capable model (LLM_CAPABLE_MODEL).
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

Investigation logs (what the AI troubleshooter actually did to resolve this):
{logs_text[:4000]}

The runbook must be based on what ACTUALLY HAPPENED in the logs above —
not hypothetical procedures. Extract real commands, real metrics, real
thresholds from the logs.

Return JSON matching this schema exactly:
{DRAFT_SCHEMA}"""

    client = _get_client()
    raw = client.complete(system=DRAFT_SYSTEM, user=prompt, max_tokens=3000, model=CAPABLE_MODEL)

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.error("draft_runbook: model returned non-JSON: %s", raw[:200])
        return {
            "title": f"RB - {service} {failure_mode}",
            "quick_summary": (
                f"`{service}` triggered `{alert_name}`. "
                f"Drafted from {len(investigation_logs)} investigation log(s). "
                "Review and complete before publishing."
            ),
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
