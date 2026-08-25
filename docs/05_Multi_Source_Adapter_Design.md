# Multi-Source Adapter Design — Qlik / Tableau → Fabric

**Status:** Proposed — awaiting approval
**Phase:** 2 (source pluralisation)
**Supersedes:** nothing. Extends `03_Detailed_Design_Document.md`, which is updated
per-component as this lands.

---

## 1. Goal

Today the platform is `Qlik → Fabric`. This phase makes it
`Qlik / Tableau → Fabric`: the user picks the source platform from a dropdown, and
everything downstream — semantic model, report, Fabric publication — is unchanged.

Full parity is in scope for Tableau: data model, calculated fields → DAX, and
worksheets/dashboards → report pages. Both input paths are in scope: a live
Tableau Server / Tableau Cloud REST connection, and direct `.twb` / `.twbx` upload.

## 2. The seam

The pipeline already has a natural cut point, and it is not a new invention — it is
the `extraction_result.json` contract that `cli/ai_qvf_to_powerbi.py` accepts today
via `--input`. Everything above the cut is source-specific; everything below is
source-agnostic and already written.

```
  SOURCE-SPECIFIC (per adapter)          │  SOURCE-AGNOSTIC (exists, unchanged)
  ─────────────────────────────────────  │  ─────────────────────────────────────
  connect / auth / list content          │
  download artefact  ──────────────┐     │
  parse artefact                   │     │
  read rows                        ▼     │
                          ╔══════════════════════╗
                          ║  Canonical IR        ║   ← THE SEAM
                          ║  (extraction dict)   ║
                          ╚══════════════════════╝
                                         │  UniversalModelGenerator
                                         │  UniversalVisualGenerator
                                         │  UniversalPBIPGenerator
                                         │  powerquery_builder / parquet_writer
                                         │  verify_pbip → fabric_publisher
```

### 2.1 Canonical IR

The existing dict, with source-neutral semantics. Keys are unchanged so the Qlik
path keeps working byte-for-byte; two keys are added.

| Key | Meaning | Qlik fills from | Tableau fills from |
|---|---|---|---|
| `source_platform` | **new** — `"qlik"` \| `"tableau"` | constant | constant |
| `file` | artefact provenance | `.qvf` stat | `.twbx` stat |
| `app_properties` | `{title, description, …}` | app props stream | `<workbook>` / repository-name |
| `load_script` | Qlik LOAD text | script stream | **empty** — see 2.2 |
| `data_model.tables` | table list | script parse | `<datasource>` / `<relation>` |
| `data_model.fields` | flat field list w/ types | data-model stream | `<column>` datatype/role |
| `data_model.relationships` | **new** — explicit joins | (absent; Qlik associates by name) | `<relation join=…>` |
| `sheets[]` | `{name, charts[]}` | sheet streams | dashboards (+ orphan worksheets) |
| `sheets[].charts[]` | `{id,type,visualization,title,dimensions[],measures[],settings{}}` | `qHyperCubeDef` | `<worksheet>` shelves |
| `variables` | named expressions | variable stream | parameters |

`relationships` is additive: the Qlik path emits nothing and behaves exactly as
today. Tableau genuinely has explicit joins, and discarding them would produce a
wrong model, so the IR has to carry them.

### 2.2 The `load_script` asymmetry

`UniversalModelGenerator._discover_tables()` ([ai_qvf_to_powerbi.py:505](../cli/ai_qvf_to_powerbi.py))
prefers `parse_load_script(load_script)` and falls back to a **single** table built
from `data_model.fields`. That fallback collapses a multi-table Tableau workbook
into one table — unacceptable.

**Change:** `_discover_tables()` gains a third branch, tried before the
single-table fallback — build one `QlikTable` per entry in `data_model.tables`
when those entries carry their own field lists. This is a strict widening: the
script branch still wins when a script exists, and the single-table fallback still
catches the empty case. The Qlik path is unaffected.

The `QlikTable` / `QlikField` / `QlikSource` dataclasses move to a new
`cli/source_model.py` as `SourceTable` / `SourceField` / `TableOrigin`, joined by
a new `SourceRelation` for explicit joins. The old names stay as aliases,
re-exported from `qlik_script_parser`, so nothing else has to move at once.
(`QlikSource` describes where rows come from, not a join — hence `TableOrigin`
rather than `SourceRelation`, which is a genuinely new structure.)

## 3. Component plan

### 3.1 New — `cli/tableau_extractor.py`

Mirrors `qvf_extractor.py`'s role and output shape.

- `.twbx` is a zip; the workbook is the single `.twb` inside it, which is plain
  XML. No zlib-stream archaeology needed — this extractor is *simpler* than the
  Qlik one.
- Parses `<datasources>` → tables, columns, datatypes, roles (dimension/measure),
  and `<relation>` joins.
- Parses `<worksheet>` → rows/cols shelves, marks, encodings → the same
  `dimensions[]` / `measures[]` chart shape.
- Parses `<dashboard>` → `sheets[]`, with `<zone>` geometry preserved so report
  pages can reproduce the layout rather than re-flowing it.
- Calculated fields (`<column caption=… ><calculation formula=…/>`) are collected
  and handed to the AI brain as expressions.
- Worksheets not placed on any dashboard become their own single-visual page, so
  nothing is silently dropped.

**Per the no-fabricated-output rule:** anything unparseable is reported as a gap in
the audit report. Datatypes come from the XML's declared `datatype`; they are never
guessed from field names.

### 3.2 New — `cli/tableau_calc_translator.py` (prompt layer)

`AIConverterBrain` stays one class; it gains a `dialect` parameter (`"qlik"` |
`"tableau"`) that selects the system prompt and the deterministic fallback rules.
Tableau calc language differs enough from Qlik's that a shared prompt would degrade
both:

| Concern | Qlik | Tableau |
|---|---|---|
| Aggregation scope | set analysis `{<Year={2024}>}` | `LOD {FIXED [A] : SUM([B])}` |
| Conditionals | `if()` | `IF/ELSEIF/END`, `IIF` |
| Table calcs | — | `WINDOW_SUM`, `INDEX()`, `RANK()` |

LOD expressions map to `CALCULATE(… , ALLEXCEPT(…))` patterns; table calcs map to
window DAX where a mapping exists and are **reported as manual-review items where
one does not**, rather than approximated into something that silently returns wrong
numbers.

### 3.3 New — `cli/tableau_data_reader.py`

Parity with `qlik_data_reader.py`. Rows come from either the extracts bundled
inside the `.twbx` (`.hyper`, read via `tableauhyperapi`) or, for live
connections, the Tableau REST *query view data* endpoint. If neither is available
the run proceeds in offline mode and says so — same behaviour as the Qlik path
when QIX is unreachable.

`tableauhyperapi` becomes an **optional** dependency: absent, extract-backed
workbooks fall back to offline mode with a clear note. It is not made mandatory
for users who only migrate live-connection workbooks.

### 3.4 New — `cli/tableau_visual_map.py`

`UniversalVisualGenerator`'s `qlik_to_pbi_map` / `approximated_types` become
dialect-selected tables. Tableau's mark types (`bar`, `line`, `square`, `circle`,
`shape`, `text`, `map`, `gantt`, `polygon`) plus shelf shape determine the Power BI
visual. The existing "no known equivalent → column chart + fidelity note" backstop
is kept verbatim — it is the right behaviour for both dialects.

### 3.5 Changed — `dev_server.py`

| Route | Change |
|---|---|
| `POST /api/runs` | gains `?source=qlik\|tableau`, default `qlik` (back-compatible) |
| `POST /api/runs/from-qlik` | kept as a deprecated alias of the next row |
| `POST /api/runs/from-source` | **new** — `?source=…&server=…&contentId=…` |
| `POST/GET /api/proxy` | `ALLOWED_HOST_SUFFIXES` gains Tableau Cloud/Server hosts |

The proxy allowlist is the security boundary here and stays a **strict suffix
allowlist** — Tableau Server is customer-hosted on arbitrary hostnames, so a
Server URL must be operator-configured via env var rather than free-form from the
browser. Tableau Cloud (`online.tableau.com`) is allowlisted by default.

### 3.6 Changed — `engine_runner.py`

- `MigrationRun` gains `source_platform`; `safe_stem()` accepts `.twb`/`.twbx`.
- `download_qlik_app` generalises to `download_source_artifact(adapter, …)`.
- Tableau REST auth is a **two-step** flow (POST `/api/3.x/auth/signin` with PAT →
  credentials token + site id → subsequent calls), unlike Qlik's single bearer
  header. The adapter owns that; `engine_runner` only sees "get me the artefact".
- The four phase labels are already source-neutral in wording except
  `"Reads the .qvf binary"` — those detail strings become per-adapter.

### 3.7 Changed — front end (`index.html`, `script.js`, `styles.css`)

- A `<select id="source-platform">` in the source card header. Changing it swaps
  the card body between `#mode-pane-qlik` and a new `#mode-pane-tableau`; the
  Fabric card and run bar are untouched. Choice persists in `localStorage`
  alongside the existing `qlikfab-theme` key.
- Tableau card fields: **Server URL**, **Site (content URL)**, **PAT name**,
  **PAT secret** — plus the same *Test Connection* → *select workbooks* dropdown,
  reusing the existing `app-dropdown` component and its filter/select-all
  behaviour rather than cloning it.
- Header/title become `Qlik / Tableau → Fabric`.
- The existing Qlik-specific copy in the Summary and Agents tabs becomes
  source-conditional.

## 4. Phasing

Each step leaves the repo working and the Qlik path green.

Reordered after the first review: the live connection was built before the
parser, because the user's own Tableau environment — not sample files — is where
real workbooks come from, and the REST download is what supplies them.

| # | Step | Ships | Status |
|---|---|---|---|
| 1 | `cli/source_model.py`, widen `_discover_tables()`, add `source_platform` + `relationships` to the IR | No user-visible change | **Done** — Qlik regression identical across all 4 samples |
| 2 | `tableau_client.py` — REST sign-in, list, download | Live Tableau Cloud/Server source | **Done** — verified against a real tenant |
| 3 | Server routes + `engine_runner` dispatch | Runs start from a Tableau workbook | **Done** |
| 4 | UI dropdown, Tableau card, copy | The feature as specified | **Done** — verified in-browser |
| 5 | `cli/tableau_extractor.py` | `.twb`/`.twbx` → IR | **Done** |
| 6 | Dialect-aware `AIConverterBrain`, mark-class visual map, `_build_relationships()` | `.twbx` → valid PBIP end-to-end | **Done** — `verify_pbip` 0/0 |
| 7 | `cli/tableau_data_reader.py` (.hyper extracts) | Data carriage parity | **Done** — degradation paths verified; real-extract read pending a `tableauhyperapi` install |
| 8 | Docs — `03_Detailed_Design_Document.md` §3, `01`, `02`, `README` | Documentation parity | Outstanding |

## 4a. Findings that changed the design

Three things surfaced during implementation that the plan did not anticipate.

**Joins were extracted but never modelled.** `model.bim` had no `relationships`
key at all, for either platform. For Qlik that is correct — it states no joins —
but for Tableau it meant two tables with no filter propagation, which produces
*wrong totals rather than an error*. `_build_relationships()` now emits them,
and refuses any join it cannot faithfully express (non-equi, missing column,
self-join) rather than emitting a broken model.

**Join clauses name the remote column, metadata records name the local one.**
Tableau's `[Returns].[Order ID]` becomes `[Order ID (Returns)]` once the
metadata record disambiguates it against `[Orders].[Order ID]`. Every join in a
Superstore-shaped workbook was being discarded for naming a column that "did not
exist". Resolved by a remote→local index applied as a post-pass, after all
datasources are read.

**`main()`'s return value was discarded.** A run that bailed out still exited 0,
so `engine_runner` would report a failed migration as a success that happened to
produce nothing. Now `sys.exit(main() or 0)`.

## 5. Risks

| Risk | Handling |
|---|---|
| Tableau table calcs have no faithful DAX equivalent | Reported as manual-review items in the audit report, never approximated silently |
| LOD semantics (FIXED/INCLUDE/EXCLUDE) mistranslate | Deterministic templates for the common forms; AI only for the residue; every LOD flagged in the report for verification |
| `.twbx` with live DB connections has no extractable rows | Offline mode + explicit note, same as the Qlik no-QIX path |
| Tableau Server hostnames are arbitrary → proxy allowlist bypass | Operator-configured env allowlist; no free-form host from the browser |
| Scope creep into Tableau Prep flows / published datasources | Out of scope this phase; stated explicitly |

## 6. Out of scope

Tableau Prep flows, published/certified datasource migration as first-class Fabric
items, Tableau permissions/row-level-security translation, and Tableau
subscriptions or alerts.
