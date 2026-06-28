# holmesgpt-runbook-mcp

[![CI](https://github.com/polarpoint-io/holmesgpt-runbook-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/polarpoint-io/holmesgpt-runbook-mcp/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/polarpoint-io/holmesgpt-runbook-mcp)](LICENSE)

MCP server for [HolmesGPT](https://github.com/robusta-dev/holmesgpt) — runbook search, gap detection, AI-assisted drafting, and root cause analysis.

## What it does

Five tools that give HolmesGPT (and any Confluence MCP client) a structured runbook layer:

| Tool | What it does |
|------|-------------|
| `runbook_search` | CQL search by `service`, `failure_mode`, `alert_name` via Confluence Page Properties — exact match, not fuzzy |
| `runbook_get` | Full runbook content by page ID or title |
| `investigation_classify` | Claude Haiku classifies an investigation log as a runbook gap |
| `runbook_draft` | Claude Sonnet drafts a runbook from investigation logs → opens GitHub PR |
| `root_cause_analyse` | Pulls matching runbooks + Claude Sonnet reasons over live incident data |

## Why CQL beats full-text search

Runbooks published via [python-mkdocs-to-confluence](https://github.com/polarpoint-io/python-mkdocs-to-confluence) include a `confluence_properties:` frontmatter block that renders as a Confluence Page Properties macro. This server queries those properties directly:

```
property["Service"]="payments-api" AND property["Failure-Mode"]="OOMKill"
```

Holmes finds the exact runbook on the first query instead of ranking 40 pages that mention both terms.

## Runbook format

Runbooks must use the [Polarpoint AI-optimised runbook format](https://github.com/polarpoint-io/markdown-pol-docs/blob/main/docs/technical-practices/monitoring-observability/runbooks/rb_template_ai_optimized.md) with `confluence_properties` frontmatter. `runbook_draft` generates this format automatically.

## Prerequisites

- HolmesGPT running in your cluster
- Confluence space for runbooks (published via the MkDocs plugin)
- GitHub repo containing runbook markdown sources
- Anthropic API key

## Quickstart

### 1. Deploy to Kubernetes

```bash
# Create secrets (or use ExternalSecrets — see deploy/externalsecret.yaml)
kubectl create secret generic holmesgpt-runbook-mcp-secrets \
  --namespace platform-tools \
  --from-literal=anthropic-api-key=$ANTHROPIC_API_KEY \
  --from-literal=confluence-url=$CONFLUENCE_URL \
  --from-literal=confluence-username=$CONFLUENCE_USERNAME \
  --from-literal=confluence-api-token=$CONFLUENCE_API_TOKEN \
  --from-literal=github-token=$GITHUB_TOKEN

kubectl apply -f deploy/
```

### 2. Wire into HolmesGPT

```yaml
# holmes-config.yaml
mcpServers:
  - name: holmesgpt-runbook-mcp
    url: http://holmesgpt-runbook-mcp.platform-tools.svc.cluster.local:8080/mcp
```

### 3. Test the connection

```bash
# From inside the cluster
curl http://holmesgpt-runbook-mcp.platform-tools.svc.cluster.local:8080/health
```

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | ✅ | Claude API key |
| `CONFLUENCE_URL` | ✅ | e.g. `https://your-org.atlassian.net/wiki` |
| `CONFLUENCE_USERNAME` | ✅ | Atlassian email |
| `CONFLUENCE_API_TOKEN` | ✅ | Atlassian API token |
| `GITHUB_TOKEN` | ✅ | GitHub PAT with `repo` write scope |
| `CONFLUENCE_RUNBOOK_SPACE` | | Confluence space key (default: `RUNBOOKS`) |
| `GITHUB_RUNBOOK_REPO` | | Repo for PR creation (default: `polarpoint-io/markdown-pol-docs`) |
| `GITHUB_BASE_BRANCH` | | Base branch (default: `main`) |
| `RUNBOOK_PATH_PREFIX` | | Path prefix for runbook files in repo (default: `docs/technical-practices/monitoring-observability/runbooks`) |

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/

# Lint
ruff check src/ tests/
black --check src/ tests/

# Run locally (stdio transport for MCP Inspector)
python -m holmesgpt_runbook_mcp.server
```

## How runbook_draft works

1. Holmes calls `investigation_classify` during an active investigation — Haiku classifies the log and returns `has_gap=true` with service/failure_mode/resolution extracted
2. Holmes accumulates classified gaps (in memory or a simple store)
3. When gap count for a (service, failure_mode) pair hits threshold, Holmes calls `runbook_draft` with the accumulated logs
4. Sonnet drafts the runbook in the AI-optimised format, extracting real commands from the investigation logs
5. `runbook_draft` opens a GitHub PR with `Status: Draft` — a platform engineer reviews and merges
6. The MkDocs CI build publishes the merged runbook to Confluence
7. Next time the same alert fires, `runbook_search` finds it on the first CQL query

## Related

- [HolmesGPT](https://github.com/robusta-dev/holmesgpt) — the AI troubleshooting assistant this server extends
- [python-mkdocs-to-confluence](https://github.com/polarpoint-io/python-mkdocs-to-confluence) — MkDocs plugin that publishes the Page Properties this server queries
- [Blog: HolmesGPT Knows What Your Runbooks Are Missing](https://polarpoint.io/blog/holmesgpt-runbook-improvement-loop/) — the post this repo was built from

## License

Apache 2.0
