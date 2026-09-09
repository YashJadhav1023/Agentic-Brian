# Phase 15 MCP Integration & Tool Catalog

## 1. Overview
The Mission Control Model Context Protocol (MCP) subsystem discovers, catalogs, and governs external tool providers without requiring manual configuration by human operators.

---

## 2. Discovered MCP Servers

During system discovery, 11 MCP servers were identified across approved configuration directories:

| Server ID | Transport | Source | Command | Detected Capabilities | Health |
|---|---|---|---|---|---|
| `azure` | `stdio` | Kiro | `/home/setoo/.local/bin/npx` | `azure`, `cloud`, `infrastructure`, `deployment` | `healthy` |
| `render` | `stdio` | Kiro | `/home/setoo/.local/bin/npx` | `render`, `cloud`, `deployment`, `web-services` | `healthy` |
| `cloudflare` | `stdio` | Kiro | `/home/setoo/.local/bin/npx` | `cloudflare`, `dns`, `workers` | `healthy` |
| `firecrawl` | `stdio` | Kiro | `/home/setoo/.local/bin/npx` | `web-scraping`, `web-crawling`, `web-search` | `healthy` |
| `brain` | `stdio` | Cline | `/home/setoo/.local/bin/basic-memory` | `memory`, `knowledge-graph`, `semantic-notes` | `healthy` |
| `bitdefender` | `stdio` | Kiro | `node` | `security`, `endpoint-protection`, `vulnerability-scan` | `healthy` |
| `betterclaw` | `stdio` | Kiro | `npx` | `coordination`, `tool-orchestration`, `agentic-tasks` | `healthy` |
| `omniroute` | `stdio` | Kiro | `npx` | `api-routing`, `proxy`, `gateway` | `healthy` |
| `kirocrew-core` | `stdio` | Kiro | `kirocrew` | `agent-coordination`, `workflow` | `offline` |
| `kirocrew-cron` | `stdio` | Kiro | `kirocrew` | `scheduling`, `cron`, `automation` | `offline` |
| `kirocrew-computer`| `stdio` | Kiro | `kirocrew` | `local-automation`, `desktop-control` | `offline` |

---

## 3. Discovered MCP Tools Catalog

A catalog of 26 core MCP tools was extracted and classified:

### Azure MCP
- `azure.list_resources` (`READ_ONLY`): List Azure resources in a subscription or group.
- `azure.get_resource` (`READ_ONLY`): Inspect resource details.
- `azure.list_resource_groups` (`READ_ONLY`): List resource groups.
- `azure.deploy_template` (`HIGH_RISK_WRITE`): Deploy ARM or Bicep templates.
- `azure.delete_resource` (`DESTRUCTIVE`): Terminate Azure resource.
- `azure.aks_get_credentials` (`READ_ONLY`): Retrieve cluster kubeconfig.
- `azure.storage_list_blobs` (`READ_ONLY`): List files in blob container.
- `azure.storage_upload_blob` (`HIGH_RISK_WRITE`): Upload file to blob container.

### Render MCP
- `render.list_services` (`READ_ONLY`): List web services and databases.
- `render.get_service` (`READ_ONLY`): View service metrics and config.
- `render.trigger_deploy` (`HIGH_RISK_WRITE`): Initiate redeployment.
- `render.list_logs` (`READ_ONLY`): Retrieve service logs.

### Cloudflare MCP
- `cloudflare.list_zones` (`READ_ONLY`): List DNS zones.
- `cloudflare.list_dns_records` (`READ_ONLY`): Query DNS records.
- `cloudflare.create_dns_record` (`HIGH_RISK_WRITE`): Add DNS record.
- `cloudflare.delete_dns_record` (`DESTRUCTIVE`): Remove DNS record.

### Firecrawl MCP
- `firecrawl.search` (`READ_ONLY`): Real-time web search.
- `firecrawl.scrape` (`UNKNOWN`): Scrape web page content.
- `firecrawl.crawl` (`UNKNOWN`): Multi-page spider crawl.

### Shared Brain MCP
- `brain.read_note` (`READ_ONLY`): Read shared memory notes.
- `brain.search_notes` (`READ_ONLY`): Keyword and semantic search.
- `brain.write_note` (`HIGH_RISK_WRITE`): Persist note to shared memory.

### Bitdefender MCP
- `bitdefender.get_endpoints` (`READ_ONLY`): List protected endpoints.
- `bitdefender.get_incidents` (`READ_ONLY`): View security alerts.
- `bitdefender.create_scan_task` (`HIGH_RISK_WRITE`): Trigger malware scan.
- `bitdefender.isolate_endpoint` (`HIGH_RISK_WRITE`): Quarantine host.

---

## 4. MCP CLI Operations

Operators can inspect and manage MCP servers using `scripts/brain.py`:

```bash
# Discover all configured MCP servers
python3 scripts/brain.py mcp discover

# List registered servers and their status
python3 scripts/brain.py mcp list

# Check command availability and health status
python3 scripts/brain.py mcp health

# Inspect detailed server metadata
python3 scripts/brain.py mcp inspect azure
```
