---
title: 'RB - [Service] [Failure Mode]'
tags:
  - guide
  - runbook
  - template
toc: true
confluence_properties:
  Service: replace-with-service-name
  Failure-Mode: replace-with-failure-mode
  Alert-Name: replace-with-exact-alert-name
  Severity: P1
  MTTR: 15m
  Owner: platform-team
  Status: Draft
  Last-Tested: YYYY-MM-DD
labels: [runbook, template]
---

# RB — [Service]: [Failure Mode]

!!! note "AI Agent Runbook Format"
    This runbook follows the Polarpoint AI-optimised format. The `confluence_properties` frontmatter above
    is published as a Confluence Page Properties macro, enabling CQL queries like:
    `property["Service"]="payments-api" AND property["Failure-Mode"]="OOMKill"`.
    HolmesGPT (and any Confluence MCP client) can find this runbook precisely without full-text search.

---

## Alert Match

<!-- AI agents use this section to identify which runbook applies. Keep values exact — copy from Alertmanager/Prometheus. -->

| Field | Value |
|-------|-------|
| Alert name | `AlertNameHere` |
| Prometheus query | `metric_name{label="value"} > threshold` |
| Namespace | `target-namespace` |
| Affected resource | `deployment/service-name` |

**Symptom signals** (what an agent or on-call engineer will see):

- Pod termination reason: `OOMKilled` / `CrashLoopBackOff` / `Error` (pick the right one)
- Alert fires in: `#alerts-platform` / `#alerts-production`
- Grafana panel: link or panel name

---

## Quick Summary

<!-- Holmes surfaces this paragraph directly in the incident channel. One paragraph, no filler. -->

`[service-name]` has triggered `[AlertName]` because `[one-sentence root cause]`. The most common cause is `[cause]`. Typical resolution is `[resolution]` in under `[MTTR]`. If `[condition]`, escalate immediately.

---

## WHAT

___

`[AlertName]` has fired for `[service-name]`. This means `[plain English description of what is wrong]`.

**Impact:**

- `[customer-facing impact or none]`
- `[internal systems affected]`
- Data loss risk: `none / low / high`

---

## WHY

___

Reasons this alert fires:

- **[Cause 1]** — `[explanation]`
- **[Cause 2]** — `[explanation]`
- **[Cause 3]** — `[explanation]`

---

## HOW

___

### Prerequisites

- Access to: `[tool/console/dashboard]`
- Permissions: `[role required]`
- Related dashboards: `[link or name]`

### 1. Confirm the alert

```bash
kubectl get pods -n <namespace> | grep <service>
kubectl describe pod <pod-name> -n <namespace> | grep -A5 "Last State"
```

Expected output confirming the alert is real (not a flap):

```
Last State: Terminated
  Reason: OOMKilled   # or CrashLoopBackOff, Error, etc.
```

### 2. Diagnose

> **Decision tree** — run through these checks in order. Stop at the first one that matches.

**Check A — [First diagnostic question]**

```bash
kubectl top pod <pod-name> -n <namespace>
```

- If `[condition]` → go to [Resolution path 1](#resolution-path-1-name)
- If `[other condition]` → go to [Resolution path 2](#resolution-path-2-name)
- If neither → continue to Check B

**Check B — [Second diagnostic question]**

```bash
kubectl logs <pod-name> -n <namespace> --previous | tail -50
```

- If logs show `[pattern]` → go to [Resolution path 3](#resolution-path-3-name)
- If logs show `[other pattern]` → escalate (see [Escalation](#escalation))

### 3. Resolution paths

#### Resolution path 1: [Name] {#resolution-path-1-name}

```bash
# step 1
kubectl ...

# step 2
kubectl ...
```

**Verify resolution:**

```bash
kubectl get pods -n <namespace> -w
# wait for Running status, no restarts
```

#### Resolution path 2: [Name] {#resolution-path-2-name}

```bash
# steps
```

#### Resolution path 3: [Name] {#resolution-path-3-name}

```bash
# steps
```

### 4. Post-incident

- [ ] Confirm pod is `Running` with 0 restarts for 5 minutes
- [ ] Alert has resolved in Alertmanager
- [ ] Note root cause in incident channel thread
- [ ] If this recurs more than twice: open a ticket to address root cause

---

## Escalation

| Severity | Contact | Channel |
|----------|---------|---------|
| P1 — service down | `@[team]-oncall` | `#incidents-production` |
| P2 — degraded | `@platform-team` | `#platform-alerts` |
| Vendor issue | `[vendor support URL]` | — |

---

## References

- [Service repo](https://github.com/your-org/service-name)
- [Runbook for related alert](./rb_related_runbook.md)
- [Architecture decision](../../architecture_design_records/adr-XXX.md)
