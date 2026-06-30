"""Confluence client — runbook search and retrieval via CQL Page Properties."""

from __future__ import annotations

import os
import logging
from typing import Optional
from atlassian import Confluence

logger = logging.getLogger(__name__)

# Read lazily so the module can be imported without Confluence credentials set
# (e.g. in CI unit tests that only test build_cql / templates).
_SPACE_DEFAULT = "RUNBOOKS"


def _conf_url() -> str:
    return os.environ["CONFLUENCE_URL"]


def _conf_space() -> str:
    return os.environ.get("CONFLUENCE_RUNBOOK_SPACE", _SPACE_DEFAULT)


def _client() -> Confluence:
    return Confluence(
        url=os.environ["CONFLUENCE_URL"],
        username=os.environ["CONFLUENCE_USERNAME"],
        password=os.environ["CONFLUENCE_API_TOKEN"],
        cloud=True,
    )


def build_cql(
    *,
    service: Optional[str] = None,
    failure_mode: Optional[str] = None,
    alert_name: Optional[str] = None,
    status: Optional[str] = None,
    space: Optional[str] = None,
) -> str:
    """Build a CQL query using Confluence Page Properties set by the MkDocs plugin."""
    effective_space = space or os.environ.get("CONFLUENCE_RUNBOOK_SPACE", _SPACE_DEFAULT)
    clauses = [
        "type=page",
        f'space="{effective_space}"',
        'label="runbook"',
    ]
    if service:
        clauses.append(f'property["Service"]="{service}"')
    if failure_mode:
        clauses.append(f'property["Failure-Mode"]="{failure_mode}"')
    if alert_name:
        clauses.append(f'property["Alert-Name"]="{alert_name}"')
    if status:
        clauses.append(f'property["Status"]="{status}"')
    return " AND ".join(clauses)


def search_runbooks(
    *,
    service: Optional[str] = None,
    failure_mode: Optional[str] = None,
    alert_name: Optional[str] = None,
    status: Optional[str] = "Approved",
    space: Optional[str] = None,
    limit: int = 10,
) -> list[dict]:
    """
    Search Confluence for runbooks using Page Properties CQL.

    Returns a list of dicts with: id, title, url, properties (Service,
    Failure-Mode, Alert-Name, Severity, MTTR, Owner, Status).
    """
    cql = build_cql(
        service=service,
        failure_mode=failure_mode,
        alert_name=alert_name,
        status=status,
        space=space,
    )
    logger.info("Confluence CQL: %s", cql)

    effective_space = space or _conf_space()
    client = _client()
    try:
        results = client.cql(
            cql,
            limit=limit,
            expand="metadata.properties,version",
        )
    except Exception as exc:
        logger.error("Confluence CQL failed: %s", exc)
        raise

    pages = []
    for r in results.get("results", []):
        content = r.get("content", {})
        page_id = content.get("id", "")
        title = content.get("title", "")
        url = f"{_conf_url()}/wiki/spaces/{effective_space}/pages/{page_id}"

        props: dict = {}
        for prop in content.get("metadata", {}).get("properties", {}).get("results", []):
            props[prop.get("key", "")] = prop.get("value", "")

        pages.append(
            {
                "id": page_id,
                "title": title,
                "url": url,
                "service": props.get("Service", ""),
                "failure_mode": props.get("Failure-Mode", ""),
                "alert_name": props.get("Alert-Name", ""),
                "severity": props.get("Severity", ""),
                "mttr": props.get("MTTR", ""),
                "owner": props.get("Owner", ""),
                "status": props.get("Status", ""),
            }
        )

    return pages


def get_runbook_content(page_id: str) -> dict:
    """
    Retrieve the full body of a Confluence page by ID.

    Returns: {id, title, url, body_markdown, properties}
    """
    client = _client()
    try:
        page = client.get_page_by_id(
            page_id,
            expand="body.storage,metadata.properties,version",
        )
    except Exception as exc:
        logger.error("Failed to fetch page %s: %s", page_id, exc)
        raise

    title = page.get("title", "")
    url = f"{_conf_url()}/wiki/spaces/{_conf_space()}/pages/{page_id}"

    body = page.get("body", {}).get("storage", {}).get("value", "")

    props: dict = {}
    for prop in page.get("metadata", {}).get("properties", {}).get("results", []):
        props[prop.get("key", "")] = prop.get("value", "")

    return {
        "id": page_id,
        "title": title,
        "url": url,
        "body": body,
        "properties": props,
    }


def find_page_by_title(title: str, space: Optional[str] = None) -> Optional[dict]:
    """Look up a Confluence page by exact title. Returns None if not found."""
    client = _client()
    try:
        page = client.get_page_by_title(
            space=space or _conf_space(),
            title=title,
            expand="metadata.properties",
        )
        if not page:
            return None
        return get_runbook_content(page["id"])
    except Exception as exc:
        logger.warning("Page lookup by title failed: %s", exc)
        return None
