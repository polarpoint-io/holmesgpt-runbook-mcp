"""Runbook markdown template — matches the Polarpoint AI-optimised runbook format."""

from __future__ import annotations

from datetime import date


def render_runbook_template(
    *,
    title: str,
    service: str,
    failure_mode: str,
    alert_name: str,
    severity: str = "P1-P2",
    mttr: str = "TBD",
    owner: str = "platform-team",
    namespace: str = "TBD",
    prometheus_query: str = "TBD",
    symptom_signals: list[str] | None = None,
    quick_summary: str = "",
    what_description: str = "",
    impact: list[str] | None = None,
    why_causes: list[tuple[str, str]] | None = None,
    prerequisites: list[str] | None = None,
    diagnosis_steps: list[dict] | None = None,
    resolution_paths: list[dict] | None = None,
    escalation: list[dict] | None = None,
    references: list[str] | None = None,
) -> str:
    """
    Render a complete runbook in the Polarpoint AI-optimised format.

    The output includes:
    - confluence_properties frontmatter (CQL-queryable via Page Properties macro)
    - Alert Match table (for agent matching before full read)
    - Quick Summary (surfaced directly in incident Slack thread)
    - WHAT / WHY / HOW sections
    - Decision-tree diagnosis
    - Named resolution paths
    - Escalation table
    """
    today = date.today().isoformat()
    symptom_signals = symptom_signals or ["TBD"]
    impact = impact or ["TBD"]
    why_causes = why_causes or [("Unknown cause", "Investigate further")]
    prerequisites = prerequisites or ["kubectl access to the namespace", "Grafana access"]
    diagnosis_steps = diagnosis_steps or []
    resolution_paths = resolution_paths or []
    escalation = escalation or [
        {
            "severity": "P1 — service down",
            "contact": "@platform-oncall",
            "channel": "#incidents-production",
        },
        {"severity": "P2 — degraded", "contact": "@platform-team", "channel": "#platform-alerts"},
    ]
    references = references or []

    slug = service.lower().replace(" ", "-")
    rb_slug = failure_mode.lower().replace(" ", "-")

    # Build symptoms list
    symptoms_md = "\n".join(f"- {s}" for s in symptom_signals)

    # Build impact list
    impact_md = "\n".join(f"- {i}" for i in impact)

    # Build WHY causes
    why_md = "\n".join(f"- **{cause}** — {detail}" for cause, detail in why_causes)

    # Build prerequisites
    prereq_md = "\n".join(f"- {p}" for p in prerequisites)

    # Build diagnosis decision tree
    diag_md = ""
    for i, step in enumerate(diagnosis_steps, 1):
        diag_md += f"\n**Check {chr(64+i)} — {step.get('question', 'TBD')}**\n\n"
        if step.get("command"):
            diag_md += f"```bash\n{step['command']}\n```\n\n"
        for branch in step.get("branches", []):
            diag_md += f"- If `{branch['condition']}` → {branch['action']}\n"
        diag_md += "\n"

    # Build resolution paths
    res_md = ""
    for rp in resolution_paths:
        path_id = rp.get("id", "resolution")
        res_md += f"\n#### {rp.get('name', 'Resolution')} {{#{path_id}}}\n\n"
        if rp.get("use_when"):
            res_md += f"Use when: {rp['use_when']}\n\n"
        if rp.get("commands"):
            res_md += f"```bash\n{rp['commands']}\n```\n\n"
        if rp.get("verify"):
            res_md += f"**Verify:** {rp['verify']}\n\n"

    if not res_md:
        res_md = "\n#### Resolution path 1: TBD {#resolution-path-1}\n\n```bash\n# Commands TBD — fill in from investigation logs\n```\n"

    # Build escalation table
    esc_rows = "\n".join(
        f"| {e['severity']} | `{e['contact']}` | `{e['channel']}` |" for e in escalation
    )

    # Build references
    refs_md = "\n".join(f"- {r}" for r in references) if references else "- TBD"

    return f"""---
title: 'RB - {service} {failure_mode}'
tags:
  - guide
  - runbook
  - {slug}
toc: true
confluence_properties:
  Service: {service}
  Failure-Mode: {failure_mode}
  Alert-Name: {alert_name}
  Severity: {severity}
  MTTR: {mttr}
  Owner: {owner}
  Status: Draft
  Last-Tested: {today}
labels: [runbook, {slug}, {rb_slug}]
---

# RB — {service}: {failure_mode}

---

## Alert Match

| Field | Value |
|-------|-------|
| Alert name | `{alert_name}` |
| Prometheus query | `{prometheus_query}` |
| Namespace | `{namespace}` |
| Affected resource | `deployment/{slug}` |

**Symptom signals:**

{symptoms_md}

---

## Quick Summary

{quick_summary or f"`{service}` has triggered `{alert_name}`. Review the HOW section for diagnosis steps. MTTR: {mttr}."}

---

## WHAT

___

{what_description or f"`{alert_name}` has fired for `{service}`. Review the affected pods and recent events."}

**Impact:**

{impact_md}

---

## WHY

___

{why_md}

---

## HOW

___

### Prerequisites

{prereq_md}

### 1. Confirm the alert

```bash
kubectl get pods -n {namespace} | grep {slug}
kubectl describe pod -n {namespace} -l app={slug} | grep -A8 "Last State"
```

### 2. Diagnose
{diag_md if diag_md else chr(10) + "TBD — fill in from investigation logs." + chr(10)}

### 3. Resolution paths
{res_md}

### 4. Post-incident

- [ ] Pod/service is healthy for 5 minutes
- [ ] Alert resolved in Alertmanager
- [ ] Root cause noted in incident channel thread
- [ ] Ticket opened if recurring

---

## Escalation

| Severity | Contact | Channel |
|----------|---------|---------|
{esc_rows}

---

## References

{refs_md}

---

!!! note "Generated by holmesgpt-runbook-mcp"
    Drafted from HolmesGPT investigation logs. Reviewed and approved before publishing.
    See [holmesgpt-runbook-mcp](https://github.com/polarpoint-io/holmesgpt-runbook-mcp).
"""
