# Enterprise Solutions

## Moved

The Enterprise RAG/MCP Core Engine design blueprint and its implementation
have moved to their own repository:

→ **https://github.com/gsachin/enterprise-rag-core**

| What | Where (in that repo) |
|---|---|
| Blueprint TRD/LLD v2026.5 | `docs/TRD_ENTERPRISE_RAG_MCP_CORE.md` |
| Solution status / pending work | `docs/ent-solution-status.md` |
| Project context / handover | `docs/ent-chat-context.md` |
| Package + tests | `enterprise_rag/`, `tests/` |

## In this repo

| What | Where |
|---|---|
| **Decoupling & integration TRD** (this repo's app → the MCP service, data migration, parity gates, rollback) | [`TRD_ERC_DECOUPLING_INTEGRATION.md`](TRD_ERC_DECOUPLING_INTEGRATION.md) |
| MCP client adapter + dispatcher | `app/rag_mcp.py`, `app/rag.py` (legacy fallback: `app/rag_legacy.py`) |
| Migration + parity script | `scripts/migrate_rag_to_erc.py` |
| Branch carrying the integration | `enterprise-rag-core-for-universityDemo` (both repos) |
