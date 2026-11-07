# NGA governance release gate — 2026-11-01

## Status

**Release blocked by three governance/content decisions.** The gate is now
machine-readable and read-only; it does not change answers or sign anything on
the authorities' behalf.

```bash
.venv/bin/python scripts/nga_governance.py --check
# exit 1: BLOCKED
```

Register: `content/dspmu/governance.json`. Only the named authority may change
a decision from `unsigned` to `signed`; record `signed_by` and `signed_at`.

## Findings

| Surface | Finding | Gate result |
|---|---|---|
| D4 — PG/PhD programme scope | `NGA_PG_PROGRAMME_LISTS_ANSWERABLE` defaults **true** and is live; D4 is **Awaiting PO sign-off** | **BLOCKER** `d4_pg_lists_unsigned` |
| Programme catalogue | **76/76 rows** have no `reviewed_by`: 43 `draft`, 33 `page_derived`; catalogue and draft inclusion are enabled | **BLOCKER** `catalogue_rows_unreviewed` |
| Legacy old-site nodes | **12** `legacy.*` nodes are `status=ok`, groundable and chunked; `oldsite.dspmuranchi.ac.in` is **not** in `NGA_ALLOWED_HOSTS` and the legacy allowlist is empty | **BLOCKER** `legacy_nodes_unapproved` |
| D6 — image/JS shell strategy | Still unsigned | warning `d6_shell_strategy_unsigned` |
| D7 — lead capture placement | Still unsigned | warning `d7_lead_capture_unsigned` |

### Legacy nodes

| Node | Page | Chunks |
|---|---|---:|
| `legacy.06b66b2698b8578c` | Assistant Registrar | 1 |
| `legacy.396b1dddb35a85a3` | Anti-Ragging Policy | 3 |
| `legacy.3ad4577bbf0417eb` | Controller of Examinations | 1 |
| `legacy.4ddee8d7d8e5638f` | AISHE | 1 |
| `legacy.66974401095105ab` | Training & Placement Cell | 4 |
| `legacy.6cf3ffa9f4eec0e2` | Grievance Redressal / SGRC | 6 |
| `legacy.8731a585ff97cacf` | Registrar Office | 1 |
| `legacy.8eaea2664851d67f` | Major Research Projects | 1 |
| `legacy.b33ff0a950a1e800` | Dean Office | 1 |
| `legacy.de70c68cdd75205b` | Vice-Chancellor Office | 1 |
| `legacy.e64462eb00a47f24` | DSW Office | 1 |
| `legacy.e69eaa1246b3c421` | Convocation Application | 2 |

These are official old-site pages, but they are not in the fetch allowlist, so
they cannot be refreshed; serving them without an approval entry is the gap.

## Decisions required

| Decision | Authority | Options | Engineering recommendation |
|---|---|---|---|
| D4 PG/PhD scope | PO | Sign the recommended scope (public programme names/departments/links; fees/seats out), or keep the flag off | Sign; the catalogue already separates names from fee facts, and PG fee/seat queries remain deterministic refusals |
| Catalogue review | Data/Content + PO | Name a reviewer for all 76 rows, or stop serving draft rows (`NGA_CATALOGUE_INCLUDE_DRAFT=false`) | Reviewer batch; rows already carry `status` and answers label draft/page-derived |
| Legacy allowlist | Content owner + Ops | Approve the 12 URLs/node IDs and add `oldsite.dspmuranchi.ac.in` to the allowlist with tests, or exclude legacy nodes from grounding | Explicit allowlist after content confirms the pages are authoritative; do not silently keep them live |

D6 and D7 remain warnings because current shell hand-offs and lead-capture
placement do not depend on a signed choice for this release, but they should be
recorded before the next product change.

## How to clear a blocker

1. D4: in `content/dspmu/governance.json`, set
   `decisions.D4.status="signed"` plus `signed_by`/`signed_at`.
2. Catalogue: add a non-empty `reviewed_by` (and reviewer date) to every row in
   `content/dspmu/programmes.json`; or disable draft inclusion.
3. Legacy: add each approved node ID or URL to `legacy_allowlist` in the
   governance register; separately add the host to `NGA_ALLOWED_HOSTS` if the
   refresh worker may fetch it.
4. Re-run:

```bash
.venv/bin/python scripts/nga_governance.py --check
.venv/bin/python -m pytest -q tests/test_governance.py
```

The gate is intentionally not wired into `/health/nga`: readiness describes
runtime availability, while release readiness is a sign-off state. It belongs
in the release checklist, not in the serving health probe.

## Scope

- No product behavior changed: PG lists remain enabled, draft/page-derived
  catalogue rows are still served with their labels, and legacy nodes remain
  groundable.
- Read-only report: `scripts/nga_governance.py`.
- Machine-readable register: `content/dspmu/governance.json`.
- Tests: `tests/test_governance.py`.
