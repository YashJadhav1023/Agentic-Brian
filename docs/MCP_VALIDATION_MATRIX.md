# MCP Validation Matrix (Part 5)

Verified in-process on 2026-09-08 via `get_mcp_registry()` + `MCPToolCatalog.sync_with_registry()`. Health = registry-reported status at validation time.

| MCP | Discovered | Health | Enabled | Tools (catalog) | Schema/risk classification | Live invocation |
|---|---|---|---|---|---|---|
| brain | yes | healthy | yes | 3 | yes | NOT VERIFIED |
| firecrawl | yes | healthy | yes | 3 | yes | NOT VERIFIED |
| azure | yes | healthy | yes | 8 | yes (incl. destructive-classified) | NOT VERIFIED |
| render | yes | healthy | yes | 4 | yes | NOT VERIFIED |
| cloudflare | yes | healthy | yes | 4 | yes | NOT VERIFIED |
| bitdefender | yes | healthy | yes | 4 | yes | NOT VERIFIED |
| betterclaw | yes | healthy | yes | 0 | n/a | NOT VERIFIED |
| omniroute | yes | healthy | yes | 0 | n/a | NOT VERIFIED |
| kirocrew-core | yes | unknown | yes | 0 | n/a | NOT VERIFIED |
| kirocrew-cron | yes | unknown | yes | 0 | n/a | NOT VERIFIED |
| kirocrew-computer | yes | unknown | yes | 0 | n/a | NOT VERIFIED |

Totals: 11 servers, 8 healthy, 3 unknown, 0 offline; 26 tools (15 read-only, 2 destructive-classified, gated at the approval layer).

Per operator mandate, no live MCP tool calls were executed against external services during validation; invocation columns are explicitly NOT VERIFIED rather than assumed PASS.
