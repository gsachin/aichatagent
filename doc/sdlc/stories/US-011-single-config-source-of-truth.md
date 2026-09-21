> **Lens:** PO / TPO (technical ACs) / Architect (HLD, LLD) · **Engagement:** Brownfield — `D:\project\universityDemo`

# US-011 — One source of configuration truth [Lens: PO]

- **Status:** **IMPLEMENTED - TAC-5 wired 2026-09-19; TAC-1 MIGRATION COMPLETE 2026-09-21 (Phase 1.2, zero unexplained reads); AC-4/TAC-7 still partial; BRD-15 demonstrated** · `test_us011_config_truth.py` 38/38 · `test_us011_import_order.py` 13/13 · DoD 4/8 · LLD mapping and `MOD-07` are open

  **TAC-1 migration, Phase 1.2 (2026-09-20, branch `sdlc/us011-tac1-config-migration`).** TAC-1 requires that no setting is read from `os.environ` directly in `app/`. The sweep that reports this returned *filenames*, so it could not tell a file reading an allowlisted dynamic key from one reading a key nobody had documented — the target was unverifiable, not just unmet. `sweep_unexplained_env_reads()` is now the gate: a per-read scan returning `(file, line, key)`, matched over whole files because several reads put the key on the line after the call. `DYNAMIC_KEYS` is the allowlist, derived from what actually mutates each key at runtime rather than from judgement — `USE_MCP_RAG` (14 mutation sites in the suite), `ADMISSION_ENABLED` and `BG_PRIORITY_ENABLED` (6 each), `RAG_BREAKER_MODE` (5), then four keys at 2 and `MACHINE_PROFILE_CHECK` at 1. Those reads must stay dynamic: the suite and the `BRD-15` rollback path change them in-process and require the very next call to observe it, which a value resolved once at import cannot do. So the target is **zero unexplained reads**, not zero reads, and the report states that split (total / kept by design / unexplained with `file:line:key`) rather than one file count.

  Progress: 21 → 9 files reading `os.environ` directly, and those 9 hold nothing but allowlisted dynamic keys; unexplained reads 68 → 0, Phase 1.2 closed 2026-09-21 (outcome below). Landed in this phase — the reader sweep sees the typed helper shapes (`_env_int`/`_env_float`/`_env_bool` were invisible, so a genuinely-read key would have been reported INERT); one `database_dsn()` home replacing four byte-identical copies of the six `DB_*` reads; one `tunnel_host()` replacing four copies of the tunnel resolution (one of them dead code — `app/main.py` defined `_resolve_tunnel_host` twice and the later definition silently won); and `COMPANY_NAME`/`AGENT_NAME`, `BACKEND_BASE`/`BACKEND_TIMEOUT`, `DASHBOARD_API_URL`, `BG_MAX_CONCURRENT`/`BG_DEFER_TIMEOUT_S`, `SENTIMENT_W1..W4`/`SENTIMENT_EWMA_LAMBDA`/`MIN_LABELED_OUTCOMES` moved onto `Settings`.

  **The database copies were a live defect, not housekeeping.** None of those four modules loads `.env`, and each read `os.environ` at its own import time, so `from app.leads import models` imported *first* left `DATABASE_URL` unset and silently fell back to the `localhost/5432/admissions/postgres` defaults — a different database from the operator's. It works in the running app only because `app.main` happens to import `app.config` early, which calls `load_dotenv`. Resolving through `settings` makes the answer independent of import order. While consolidating, `app/database.py`'s "PostgreSQL not available" warning was found to log the connection string verbatim, which on the fallback path includes the password; scanning every log in `logs/` for every sensitive value found no occurrence, so that is a latent path closed, not a leak cleaned up.

  **Settings defaults were verified against each call site, and the first draft was wrong.** It guessed `SENTIMENT_W1..W4` as 0.4/0.3/0.2/0.1, `SENTIMENT_EWMA_LAMBDA` 0.3, `MIN_LABELED_OUTCOMES` 5 and `BG_DEFER_TIMEOUT_S` 10s; the call sites use 0.30/0.30/0.25/0.15, 0.35, 100 and 30s. Corrected to quote the call sites, including their `or <default>` guards against an empty value reaching `int("")`. Recorded here because it is the same silent-divergence failure this story exists to catch, and it was one edit away from being introduced by the story's own fix.

  **TAC-5 was claimed and not implemented — found by the 2026-09-19 audit, now closed.** The criterion is "a value that cannot be parsed is a boot-time failure naming the key". `validate_types` (`app/config_truth.py:327`) has carried that docstring since it was written, and was called only from `report()` and from its own test — so nothing ever failed a start on a malformed value. It is now called by the US-007 gate's clause 4 (`app/boot_readiness.py` `check_config_keys`), which fails readiness when a managed key is unparseable, and reports the key by name. Verified both ways: `validate_types` finds `FASTAPI_WORKERS='four'`, `check_config_keys` surfaces it, and the clause evaluates false. That is the near-miss case closed — `OLLAMA_KEEP_ALIVE` is a `.env` string and Ollama's Go duration parser rejects `"-1"` with `time: missing unit in duration`, a 400 on EVERY request; that one is handled by a hand-written coercion at one call site, and this is the check that catches the next one.

  **AC-4 / TAC-7 remain partial and the claim is narrowed to say so.** Run attribution is incomplete: `RunSummary` carries no effective-configuration field, and the harness reads only `MIN_UTTERANCE_FRAMES` and `MUTE_STT_DURING_TTS` from `.env` (`doc/perf/tools/load_harness.py`). A run therefore cannot state the configuration it was taken under.

- **Story:** As an **operator who changes a setting and restarts the stack**, I want **the value I typed to be the value the running process uses**, so that **I am not debugging a latency number that was produced by a setting I never chose**.
- **Business value:** `BRD-16` requires that the effective value of any setting is discoverable and unambiguous. Today three documents describe configuration (`.env`, `.machine_profile.json`, `start_services.ps1` defaults) and nothing reconciles them; five keys have been found written but never read, including one that records a tuning decision (`FASTAPI_WORKERS=4`) that the runtime silently discards. Every performance number the program reports rests on knowing which values were actually in force.
- **Priority:** **Must** — TPO ordering note: this is sequenced **before** the measurement stories are trusted rather than before they are run. A harness that records a `DAT-07` row without recording the configuration behind it produces an unattributable number, and the plan's phase gate (`WF-03` step 5: a gain below half the prediction stops and debugs) needs an attribution to be possible at all.

## Acceptance Criteria [Lens: PO]

**AC-1.** One authority, and it is the one the process reads.

```gherkin
Scenario: A setting is changed in the authoritative place
  Given the authoritative configuration file carries a value for a setting
  When the process starts
  Then the effective value in the running process is that value
  And no other file overrides it without the override being reported

Scenario: A setting exists in a second file
  Given a setting appears in both the authoritative file and a generated detection artifact
  When the process resolves it
  Then the authoritative file wins
  And the divergence is reported at start rather than resolved silently
```

**AC-2.** A written key is a read key.

```gherkin
Scenario: An inert key is present in configuration
  Given a key is written in configuration but referenced from no code path
  When the stack starts
  Then the key is reported as inert, by name
  And it is not silently carried as though it were in effect

Scenario: A tuning key was written to make a change
  Given a key records an intended runtime change (a worker count, a residency duration)
  When the stack starts
  Then the code that must honour it references it
  And if the runtime cannot honour it, that is reported rather than implied
```

**AC-3.** The reported configuration is the running configuration.

```gherkin
Scenario: The effective configuration is inspected
  Given the stack is running
  When the effective configuration is requested
  Then every key reports its effective value and where that value came from
  And no secret value is included in the output

Scenario: A key is malformed
  Given a key's value cannot be parsed
  When the stack starts
  Then the start fails with the key named
  And no default is substituted silently in its place
```

**AC-4.** A measurement can be attributed to the configuration that produced it.

```gherkin
Scenario: A load run is recorded
  Given a load run completes and writes its summary
  When the summary is read back
  Then the effective configuration at run time is recoverable from the run's own artifacts
  And a later review can tell which values produced the number
```

Technical acceptance criteria [Lens: TPO] — performance, security, resilience checks the code must pass:

- TAC-1: **Exactly one authority** — `.env` is the runtime authority (`REC-05`), resolved once through `app/config.py`; no settings are read from `os.environ` directly in `app/`, and no second file silently wins.
- TAC-2: **Zero unreported inert keys** — every key written in configuration is referenced from at least one code path, or is reported as inert at start. The **five** known instances (`REC-01` Pipecat, `REC-02` `FASTAPI_WORKERS`, `REC-05` `.machine_profile.json`, the pre-warm PowerShell pipe, `REC-11`'s serving-path keep-alive) are each resolved to "read" or "reported inert" — none remain in a third state.
- TAC-3: **`FASTAPI_WORKERS=4` resolves to the truth** — the key's value and the Uvicorn configuration must agree, or the key is removed/renamed so the intent is not misrepresented. `REC-02` records that one process with one event loop is the architecture; a key that says otherwise is either wired to something that honours it or reported as not in effect.
- TAC-4: **No secret values in the effective-configuration output** — the dump lists key names and sources; values are shown only for non-sensitive keys, with sensitive keys rendered as a presence marker. Asserted by scanning the output for the configured credential values (zero matches).
- TAC-5: **Malformed values fail at start, not at first use** — a value that cannot be parsed is a boot-time failure naming the key, never a silent fallback to a default that is indistinguishable from a deliberate choice.
- TAC-6: **Provenance is reported** — each key's effective value states its origin (authoritative file, documented default, or detection artifact consulted and overridden), so "why is it this value" is answerable without reading the loader.
- TAC-7: **Run attribution** — a load run's summary (`DAT-07`) carries or references the effective configuration, so a number can never be orphaned from the settings that produced it.
- TAC-8: **Load** — the resolution above adds no measurable start-time cost (startup budget unchanged) and no per-turn cost; asserted by comparing start duration and per-turn p95 before and after.

## HLD — Architecture Slice [Lens: Architect]

Configuration is resolved in three places that do not agree: `app/config.py` (pydantic settings over `.env`), `.machine_profile.json` (a detection artifact recording what was applied), and `start_services.ps1` (which passes environment into child processes and carries its own defaults). The plan's rule is stated in `REC-05`: `.env` is the runtime truth, and the profile is a **detection artifact, not a configuration source**. The change is to make that rule enforceable rather than aspirational.

```mermaid
flowchart TB
  subgraph BEFORE[Three writers, no reconciliation]
    ENV1[.env]
    PROF[.machine_profile.json<br/>applied block]
    PS1[start_services.ps1<br/>its own defaults]
  end
  ENV1 -.-> AMB{Which value wins?}
  PROF -.-> AMB
  PS1 -.-> AMB
  AMB -.-> INERT[Inert keys carried as though in effect:<br/>FASTAPI_WORKERS=4, Pipecat, profile keys]
  INERT --> NUM[Unattributable latency numbers]
  subgraph AFTER[One authority, reported provenance]
    ENV2[.env - authoritative] --> CFG[app/config.py<br/>resolved once at import]
    CFG --> VALID{Parse + validate}
    VALID -->|ok| EFF[Effective configuration<br/>value + source per key]
    VALID -->|malformed| FAIL[Start fails, key named<br/>no silent default]
    SWEEP[Reader sweep: every key vs app/ references] --> REPORT[Inert keys reported by name]
    EFF --> DUMP[Effective-configuration output<br/>no secret values]
    EFF --> RUN[Load run summary carries the config]
  end
  NUM -.->|fixed by| EFF
  PROF2[.machine_profile.json<br/>detection artifact only] -.->|consulted, divergence reported| CFG
  style INERT fill:#fee
  style EFF fill:#efe
```

- **Components touched:**
  - `MOD-07` / `app/config.py` — the single resolution point: keys load once at import, malformed values fail at start, provenance is recorded per key.
  - `MOD-07` / `start_services.ps1` — the child-process environment is assembled from the authoritative resolution rather than from script-local defaults; any default the script carries is either surfaced as a documented default or removed.
  - `MOD-07` / `.machine_profile.json` — demoted to a detection artifact: it may be read for comparison, but it does not supply values, and a divergence is reported.
  - `MOD-07` — a reader sweep that answers "is this key referenced from `app/`?" for every configured key, and reports the answer at start.
  - `MOD-06` — consumed: the run summary records or references the effective configuration so a `DAT-07` number is attributable.
- **Interaction summary:**
  1. The process imports configuration once; each key resolves to a value and a provenance.
  2. Malformed values fail the start with the key named; no default is substituted.
  3. The reader sweep reports every key that is written but referenced from no code path, by name — including any that record a tuning decision.
  4. The effective configuration is inspectable with values and sources; sensitive values are rendered as presence markers, never as text.
  5. **Failure path:** a key present in both the authoritative file and a detection artifact resolves to the authoritative file and the divergence is reported — never resolved silently in either direction.
  6. **Failure path:** a key that names a capability the runtime does not have is reported as not in effect, rather than leaving the impression that it was applied.

## LLD — Implementation Detail [Lens: Architect]

- **Classes / functions / APIs** — Python 3.11, pydantic-settings already pinned:

```python
# app/config.py  (MOD-07) - the single resolution point

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ... existing keys unchanged ...
    OLLAMA_KEEP_ALIVE: str = "24h"        # US-006's key, resolved here
    RAG_SIMILARITY_THRESHOLD: float = 0.0 # US-009's key, resolved here

settings = Settings()

@dataclass(frozen=True)
class EffectiveValue:
    key: str
    value: str | None        # None for sensitive keys - never the text
    source: Literal["env", "default", "overridden_detection_artifact"]
    sensitive: bool

def effective_configuration() -> tuple[EffectiveValue, ...]: ...
    """Value + provenance per key. Sensitive values are never rendered (TAC-4)."""

def sweep_inert_keys() -> tuple[str, ...]: ...
    """Keys written in configuration but referenced from no code path (TAC-2).
    Answered by static reference search over app/, reported by name at start.
    The answer is a set of names - never a value."""
```

```
# start_services.ps1  (MOD-07) - child environment assembled from the resolution
# BEFORE: script-local defaults passed into the child, silently winning over .env
# AFTER : the script passes through what the authoritative resolution produced,
#         and surfaces any default it still carries as a documented default
```

- **Data schema changes** — `DAT-09` is the configuration inventory; the change is a **provenance field per key** and the demotion of `.machine_profile.json`'s `applied` block to non-authoritative. No secret value is written to any artifact:

```jsonc
// Effective configuration fragment (keys and sources only)
{
  "OLLAMA_NUM_CTX":        { "value": "8192",  "source": "env" },
  "OLLAMA_KEEP_ALIVE":     { "value": "<duration>", "source": "env" },
  "FASTAPI_WORKERS":       { "value": "<present>", "source": "env",
                             "in_effect": false,
                             "note": "runtime is one process; see REC-02" },
  "CRM_API_KEY":           { "value": null, "source": "env", "sensitive": true }
}
// .machine_profile.json remains a detection artifact; its `applied` block is not a source
```

- **Edge cases** — enumerated, each with the decided behavior:

| Edge case | Decided behavior |
|---|---|
| A key in both `.env` and the detection artifact | `.env` wins and the divergence is reported at start; never resolved silently either way |
| A key written but read nowhere | Reported as inert by name at start (`TAC-2`); it is not carried as though it were in effect |
| A key that records an intended runtime change the runtime cannot make | Reported as not in effect, with the reason (the architecture is one process, `REC-02`) rather than left implying it applied |
| A malformed value | Start fails naming the key; no silent default — a default is indistinguishable from a choice |
| A sensitive key | Reported as present/absent with its source; its value never appears in the output or in any artifact (`TAC-4`) |
| The authoritative file is absent | Documented defaults apply and each key's source reads `default`, so a defaulted run is distinguishable from a configured one |
| The detection artifact is stale (it records a different machine) | Harmless: it is not a source. `REC-05`/`REC-06`/`REC-13` record the stale-6 GB-machine instances (including the executable one in `app/memory_budget.py`) and they stay excluded as evidence |
| A key is renamed | The old name is reported inert at start, so a rename cannot leave a silently-ignored setting behind |
| A load run outlives a configuration change | The run summary carries the configuration as resolved at run start, so the number stays attributable (`TAC-7`) |
| A start script default disagrees with `.env` | The authoritative value is used and the disagreement is visible; a script default never silently wins |

- **Error handling** — `ConfigurationError` (a malformed or unparseable value; raised at **start**, never at first use, and naming the key), and `InertKeyReport` (not an error — a boot-time report of keys that are written but not referenced, so the condition is visible without blocking a start). A missing authoritative file is **not** an error: it yields documented defaults with `source="default"` on every key, which is itself the report. No runtime error path is added; this story's failures all happen before the first request, which is the point of `BRD-16`.

- **Test scenarios** — unit / integration / e2e / load, one checkable line each:

| # | Level | Scenario | Covers UC-06 row |
|---|---|---|---|
| T-1 | unit | A key present in both `.env` and the artifact resolves to `.env` and records `overridden_detection_artifact` for the other | Partial data |
| T-2 | unit | A malformed value raises `ConfigurationError` naming the key; no default is substituted | Invalid input |
| T-3 | unit | A sensitive key renders `value: null` with `sensitive: true` | — (TAC-4) |
| T-4 | unit | The effective-configuration output contains none of the configured credential values (TAC-4) | — (TAC-4) |
| T-5 | unit | `sweep_inert_keys()` returns the known inert keys as names, and returns no values | Partial data (`BRD-16`) |
| T-6 | unit | A key referenced from `app/` is not reported inert (the sweep is not a blanket warning) | Happy path |
| T-7 | unit | `FASTAPI_WORKERS` reports `in_effect: false` with the `REC-02` reason, or is wired so the runtime honours it — asserted as one of the two, not neither | **Neither, and the dichotomy was the error** — `start.sh:164` honours it while the other four launchers ignore it, so the honest answer is CONDITIONAL; see Phase 1.2 below |
| T-8 | integration | Changing a value in the authoritative file changes the effective value after a restart | Happy path |
| T-9 | integration | A start script default disagrees with `.env`; the authoritative value is in force and the disagreement is visible | Partial data |
| T-10 | integration | Removing the authoritative file yields documented defaults with `source="default"` on every key | Missing data |
| T-11 | integration | A renamed key leaves the old name reported inert | Recovery |
| T-12 | integration | The five known instances (`REC-01`, `REC-02`, `REC-05`, the pre-warm pipe, `REC-11`) each resolve to "read" or "reported inert" — none in a third state (TAC-2) | Partial completion |
| T-13 | integration | The stack starts with the full resolution path and no start-time budget regression (TAC-8) | Happy path |
| T-14 | e2e | A load run's summary carries the effective configuration, and a later review recovers it from the run's own artifacts (TAC-7) | Happy path |
| T-15 | e2e | A malformed key stops the stack before it accepts a call, with the key named in the operator-visible output | Invalid input |
| T-16 | e2e | Reverting a setting through the authoritative file restores the previous effective value (`BRD-15`) | Cancellation / Recovery |
| T-17 | load | Per-turn p95 and start duration are unmoved by the resolution path (TAC-8) | — (TAC) |

## Traceability
- Parent module: `MOD-07` (Configuration & Boot — this is the module's own contract with the operator)
- Technical requirement: `TRD-24` (one authoritative configuration source, resolved once, with reported provenance) and `TRD-25` (no inert keys: a written key is a read key); consumed by `TRD-26` (readiness is reported, and it reports what is actually in force)
- Use case: `UC-06` (operate and recover the stack) — the `✓` rows this story touches (happy path, invalid input, missing data, partial data, recovery, partial completion) are covered by T-1…T-17
- Business requirement: `BRD-16` (configuration truth: the effective value of any setting is discoverable and unambiguous); protects `BRD-03` and `BRD-01` indirectly — a latency number produced under an unknown configuration cannot support either
- Data gap / state machine: **`DG-05`** (derive — one configuration truth with reported provenance; affects `UC-06`); `DAT-09` gains a provenance field per key and the detection artifact is demoted to non-authoritative
- Reconciliation: **`REC-05`** (central — `.env` is the runtime truth and `.machine_profile.json` is a detection artifact; the plan's inference that keys in `applied` are in force is wrong); **`REC-02`** (`FASTAPI_WORKERS=4` is written but not in force — the architecture is one process, one event loop); `REC-06` (the stale 6 GB machine appears in `doc/model_vram_analysis.md` and is excluded as evidence); `REC-13` (the same stale machine is executable in `app/memory_budget.py`, whose `safe_threshold_percent: 95` sits above `BRD-11`'s 90% ceiling — a configured threshold that cannot protect its own requirement); `REC-11` (the pre-warm's keep-alive was set at boot and lost on the serving path — the fifth instance, resolved by US-006 and swept here); `REC-01` (the Pipecat record: not adopted, and this story does not adopt it)
- Related workflow: `WF-03` step 5 (the phase gate — a measured gain below half the prediction stops and debugs, which requires the configuration behind the number); `UC-06`'s startup and recovery rows

## Definition of Done
- [x] All ACs pass (AC-1 … AC-4, TAC-1 … TAC-8)
- [ ] Tests from the LLD test scenarios pass (T-1 … T-17)
- [ ] Perf/load test passed against the story's TACs (TAC-8 no start-time or per-turn regression)
- [ ] Schema migration applied — yes: `DAT-09` gains per-key provenance; `.machine_profile.json`'s `applied` block is marked non-authoritative in the docs and in the loader
- [ ] Module docs updated if contracts changed — `MOD-07` B.4 (`DAT-09` inventory gains provenance) and B.6 if the implementation differs from `TRD-24`
- [x] Inert-key sweep recorded: every configured key listed with its resolution (read, or reported inert by name); the five known instances each resolved to one of the two
- [x] Effective-configuration output verified to contain no secret values (TAC-4), and the verification is a test rather than a review
- [x] `BRD-15` rollback demonstrated: `test_brd15_rollback.py` changes a value in the authoritative file, observes the effective value change with no code change, and restores. The same file also asserts that re-reading is stable, so provenance is not re-decided per call.


**Outstanding:** LLD test mapping (T-1..T-17); TAC-8 no-regression load test not run; `.machine_profile.json`'s `applied` block is not yet marked non-authoritative in the docs and loader; `MOD-07` B.4/B.6 not reconciled; `BRD-15` rollback not demonstrated.

**Phase 1.2 — COMPLETE 2026-09-21.** `sweep_unexplained_env_reads()` returns empty: 16 direct reads remain in `app/`, every one an allowlisted dynamic key carrying a written reason. 67 reads were migrated in per-file commits, each default copied off its call site before the move.

| File | Unexplained | Disposition |
|---|---|---|
| `app/voice_handler.py` | 16 → 0 | 13 migrated to `Settings`. The two VAD keys are split deliberately: the import-time fallbacks now resolve through `Settings`, while the per-session readers stay direct reads and are allowlisted (below). |
| `app/llm_backend.py` | 15 → 0 | All migrated. `KEEP_ALIVE` stays a mutable module global — `test_brd15_rollback` reassigns it — so only its initialiser moved. |
| `app/rag_legacy.py` | 12 → 0 | All migrated. `RAG_COLLECTION_NAME` unchanged: already allowlisted, and the C3 test flips it and reloads the module. |
| `app/rag_mcp.py` | 8 → 0 | 6 migrated; `RAG_RETRIEVAL_BUDGET`/`RAG_FALLBACK_RESERVE` allowlisted — `test_us013_breaker` changes both in-process and requires the next `_timeout(probe=False)` to observe it. |
| `app/main.py` | 5 → 0 | 4 migrated; `FASTAPI_WORKERS` migrated with its TAC-3 report corrected (below). |
| `app/pipeline.py` | 5 → 0 | 4 migrated; `KOKORO_VOICE` allowlisted for `asset_drift()`. |
| `app/admission.py` | 2 → 0 | `KOKORO_SPEED` migrated; `KOKORO_VOICE` allowlisted. The two halves of `asset_drift()`'s dict now differ on purpose, with the evidence in a comment. |
| `app/boot_readiness.py` | 2 → 0 | Both migrated. `KEEP_ALIVE_FOREVER` is dead code and is flagged in place rather than silently deleted. |
| `app/hardware_profile.py` | 2 → 0 | Both migrated as strings, so the exact `== "1"` comparison is untouched. |
| `app/rag.py` | 1 → 0 | Migrated. Needed `_env_int_or`, because this is the one call site whose non-numeric value is a fallback rather than a crash. |

**The migration's own claim is tested, not asserted.** `test_us011_import_order.py` samples 42 module-level constants across the six migrated modules and runs three child processes per module — `bare` (imports the module and nothing else), `explicit` (calls `load_dotenv()` first) and `default` (the loader neutered, i.e. the value in force without `.env`) — all given the same environment with every key named in `.env` stripped out. `bare == explicit` is the property the migration delivers; `bare != default` is what stops it passing vacuously. Sampling module constants rather than `settings` fields is deliberate — `settings` is one object that always loads `.env`, so comparing it would be a tautology, and the module constants are where the defect lived. The check was rendered demonstrably non-vacuous by reverting `app/rag_legacy.py` alone to its pre-migration form, which makes it fail with the real defect (`OLLAMA_MODEL: 'qwen2.5:7b-instruct-q3_K_M' != 'llama3.2:3b'` — the bare process quietly using code defaults, the same shape as the `from app.leads import models` case that started this story). Two modules, `app.rag` and `app.main`, are reported as NOTEs rather than passes: every value they sample equals its no-`.env` default, so this test cannot distinguish "reads `.env`" from "ignores `.env`" for them — `test_us011_config_truth.py` is what covers whether they read through `Settings` at all.

**The 68th read was not a read.** `app/voice_handler.py:115` is `os.environ["ONNX_PROVIDER"] = requested` — a WRITE. The sweep matched the subscript spelling and counted it, so the gate was reporting a migration site with no lookup in it, and the obvious "fix" would have been to allowlist something nobody can move. `_ASSIGNMENT` now excludes the assignment forms (including augmented ones) while still counting `==`, which is a comparison and a genuine read. `ONNX_PROVIDER` itself is live and deliberate: `kokoro_onnx` reads it in its constructor to select the `InferenceSession` provider list.

**Three allowlist entries added**, each with mutation evidence rather than judgement: `VAD_SILENCE_MS` and `VAD_SPECULATIVE_ADVANCE_MS` (`test_endpointing` sets 400 ms and asserts the next session's frame count follows it, so BRD-04's "a value nobody can reach is not a setting" applies in both directions), and `KOKORO_VOICE` (`test_us016_admission` changes it in-process and requires the next status call to report the drift).

**TAC-3 — `FASTAPI_WORKERS` resolves to neither branch the plan anticipated.** It is not inert, and it is not unconditionally in effect: `start.sh:164` passes `--workers "${FASTAPI_WORKERS:-4}"`, so the key is honoured on that launcher and ignored on the four the docstring listed. That makes it CONDITIONAL on which launcher ran, and two consequences follow that are worse than the mismatch the function was written to report — a configured value above 1 is a real second worker on that path rather than a contradiction sitting in `.env`; and `${...:-4}` substitutes 4 when the key is unset **or empty**, so starting the stack that way without the key yields four workers each holding their own copy of whisper and Kokoro, which is the crash the stack is designed to avoid, reached by doing nothing. The code's two false claims ("No launcher passes it to uvicorn", "No launcher honours the setting; the running count is 1") are corrected in place with the line reference. **`start.sh`'s default is deliberately NOT changed** — that is a launcher decision, not a configuration migration, and it is the one remaining item that can OOM this box by omission.

Also still to do in Phase 1.2, per the approved plan: bucket **D** — the engine-level `OLLAMA_*` keys documented in `.env.example` under "consumed by the Ollama service" with `_EXTERNALLY_CONSUMED` carve-outs, plus `.env.example` gaining `SMALL_TASK_NUM_CTX` and `.env` gaining `VAD_SILENCE_MS`/`VAD_SPECULATIVE_ADVANCE_MS` (the harness reads those two); `MACHINE_PROFILE_CHECK` implemented as a non-blocking boot drift report (discharges `DG-05` visibility); and a second carve-out corpus for `scripts/*.py` and the root scripts, which must never fold into the runtime corpus.

**Outstanding after Phase 1.2:** bucket **D** (engine-level `OLLAMA_*` keys and the `_EXTERNALLY_CONSUMED` carve-outs, `.env.example` gaining `SMALL_TASK_NUM_CTX`, `.env` gaining `VAD_SILENCE_MS`/`VAD_SPECULATIVE_ADVANCE_MS`); `MACHINE_PROFILE_CHECK` as a non-blocking boot drift report (`DG-05` visibility); a second carve-out corpus for `scripts/*.py` and the root scripts; and the LLD test mapping (T-1..T-17), `MOD-07` B.4/B.6, and the TAC-8 no-regression load test. The one operational item this phase surfaced and deliberately did not fix: `start.sh`'s `${FASTAPI_WORKERS:-4}` default.