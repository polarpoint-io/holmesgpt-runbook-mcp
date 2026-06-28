"""Shared test fixtures."""

import pytest


@pytest.fixture
def sample_investigation_log():
    return """
HolmesGPT investigation — 2026-06-28 03:12 UTC
Alert: KubePodOOMKilled
Namespace: payments
Pod: payments-api-7d8f9b-xk2p4

No matching runbook found in Confluence for 'payments OOMKill'.

Investigation steps:
1. kubectl describe pod payments-api-7d8f9b-xk2p4 -n payments
   → Last State: Terminated (OOMKilled, exit 137)
   → Memory limit: 256Mi, working set at termination: 254Mi

2. kubectl top pod -n payments
   → payments-api-7d8f9b-xk2p4: CPU 120m / Memory 254Mi (at limit)

3. Checked Grafana — memory climbed linearly from 180Mi to 254Mi over 40 minutes.
   No corresponding traffic spike (RPS flat at ~200/s).

4. Checked recent deploys — payments-api v2.3.1 deployed 2h before OOMKill.

Resolution applied:
   helm rollback payments 3  (rolled back to v2.3.0)
   Memory stabilised at ~190Mi within 5 minutes.

Likely cause: memory leak introduced in v2.3.1. Root cause ticket: PLAT-2901.
"""


@pytest.fixture
def sample_gap_classification():
    return {
        "has_gap": True,
        "confidence": "high",
        "gap_description": "No runbook exists for payments-api OOMKill caused by memory leak after deploy",
        "service": "payments-api",
        "namespace": "payments",
        "failure_mode": "OOMKill",
        "alert_name": "KubePodOOMKilled",
        "resolution": "helm rollback payments 3",
        "severity": "P1",
    }
