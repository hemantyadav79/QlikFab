# Detailed Design Document

**Project:** QlikFab — Autonomous Qlik Sense to Microsoft Fabric Migration Platform
**Prepared by:** SegueIT — https://segueit.com/
**Document ID:** SEG-QF-DDD-001
**Version:** 1.0
**Date:** 13 August 2026
**Status:** Issued for technical review
**Classification:** Client Confidential

---

## Document Control

| Version | Date | Author | Change summary |
| --- | --- | --- | --- |
| 0.5 | 07 Aug 2026 | SegueIT Engineering | Initial architecture |
| 0.8 | 11 Aug 2026 | SegueIT Engineering | Added Direct Lake / OneLake staging design |
| 1.0 | 13 Aug 2026 | SegueIT Engineering | Issued for technical review |

---

## 1. Introduction

### 1.1 Purpose

This document describes the internal design of the QlikFab platform: its
components, data flows, algorithms, and the reasoning behind decisions that are
not self-evident from the code. It is written for engineers who will maintain or
extend the platform.

### 1.2 Design principles

Five principles govern the design. They are stated first because most of the
non-obvious decisions in later sections follow from them.

| Ref | Principle | Consequence |
| --- | --- | --- |
| DP-01 | **Never fabricate** | No sample rows, no invented values, no type guessed from a column name |
| DP-02 | **The artefact is the authority** | Where two sources disagree about a fact, the generated file wins over the intention |
| DP-03 | **Fail visibly and early** | An incomplete migration is refused or reported, never published as if complete |
| DP-04 | **Name the cause, not the symptom** | Errors identify the specific artefact and reason, because the platforms downstream do not |
| DP-05 | **Report every gap** | Anything approximated, trimmed, or omitted appears in the audit report |

> **On DP-01.** An earlier iteration generated placeholder company data
> (`Contoso`, `Office Supplies`) to make reports look populated. This produced
> reports that appeared finished and were meaningless. The verifier now carries
> an explicit regression guard against those tokens.

---

## 2. Architecture Overview

### 2.1 Logical architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│  Operator browser  —  index.html / script.js / styles.css            │
│  Connection setup · migration control · live log stream · downloads  │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ HTTP (localhost)
┌───────────────────────────────▼──────────────────────────────────────┐
│  dev_server.py — local control plane                                 │
│  Static hosting · Qlik proxy · Entra token exchange · run API        │
│  Stale-module guard                                                  │
└───────────────┬──────────────────────────────┬───────────────────────┘
                │                              │
┌───────────────▼──────────────┐  ┌────────────▼─────────────────────┐
│ engine_runner.py             │  │ fabric_publisher.py              │
│ Run lifecycle · subprocess   │  │ Preflight · Lakehouse staging    │
│ Disk guards · log capture    │  │ Placeholder resolution · publish  │
└───────────────┬──────────────┘  └────────────┬─────────────────────┘
                │ subprocess                    │
┌───────────────▼──────────────────────────┐   │  ┌──────────────────┐
│ cli/ai_qvf_to_powerbi.py  (the engine)   │   └─▶│ onelake_client.py│
│  ├─ qvf_extractor.py                     │      │ Lakehouse · DFS  │
│  ├─ qlik_script_parser.py                │      └──────────────────┘
│  ├─ qlik_data_reader.py   (QIX)          │
│  ├─ powerquery_builder.py (M + types)    │
│  ├─ parquet_writer.py     (staging)      │
│  └─ verify_pbip.py        (verification) │
└──────────────────────────────────────────┘
                │
                ▼
      PBIP project on disk  ──▶  Microsoft Fabric workspace
```

### 2.2 Migration phases

The platform presents four phases to the operator. These are reporting
groupings, not separate processes.

| Phase | Responsibility |
| --- | --- |
| **Assessment** | Ingest the application; read metadata and, where available, real rows |
| **Parsing** | Parse the load script; resolve tables, fields, and types |
| **Mapping** | Translate expressions to DAX; map chart types; bind visuals |
| **Report Generation** | Emit the PBIP project; verify; publish |

### 2.3 Deployment topology

Single-host. The operator's browser talks only to `localhost`. Application data
travels **tenant → migration host → Fabric** and is never routed through the
browser — an early design carried the `.qvf` out to the page and back, and
transfers of tens of megabytes failed repeatedly.

---

## 3. Component Design

### 3.1 `dev_server.py` — control plane

A `http.server`-based local server. Responsibilities:

| Endpoint | Purpose |
| --- | --- |
| `/` and static assets | Serves the operator interface |
| `/qlik-proxy` | Proxies Qlik Cloud REST calls, avoiding browser CORS restrictions |
| `/fabric-proxy` | Proxies Fabric REST calls |
| `/fabric-token` | Server-side Entra client-credentials exchange |
| `/api/runs` | Creates and starts a migration run |
| `/api/runs/{id}` | Polls run status and log lines |
| `/api/runs/{id}/publish` | Publishes a completed run |
| `/api/runs/{id}/download` | Returns the generated archive |

**Why the token exchange is server-side.** Entra rejects a confidential-client
secret presented from a browser origin, and would not return the necessary CORS
headers in any case. The secret is used for one request and discarded.

**The stale-module guard.** `engine_runner` and `fabric_publisher` are imported
into the server process and frozen at their state when it started. The engine
itself runs as a subprocess, so `cli/*.py` is re-read every run — but edits to
those two modules do not take effect until restart. The server records their
modification times at import and compares before each run:

- For a **migration**, a stale module produces a warning in the run's own log.
- For a **publish**, it is **refused**. Publishing with an out-of-date publisher
  produces workspace items that look successful and do not work; the failure is
  only discovered by opening the report, after several minutes of data reading.
  Refusing costs one restart. Not refusing costs the whole run.

### 3.2 `engine_runner.py` — run lifecycle

Owns a `MigrationRun` per migration: a working directory, captured output,
status, and the generated artefact.

**Disk guards.** A minimum free-space check runs before anything is written, and
free space is re-checked periodically during the run. Orphaned working
directories from earlier server processes are swept before a new run begins —
the in-memory run store cannot reach them, so nothing else would ever reclaim
them.

**Credential handling.** The Qlik credential is passed to the engine subprocess
**by environment variable, never on the command line.** Process arguments are
readable by any process on the host, and the command line is echoed to the
operator's log.

**Path selection.** A tenant-sourced run receives `--qlik-tenant` and
`--qlik-app-id`, and by default also `--stage-lakehouse`. Setting
`QLIKFAB_EMBED_ROWS=1` forces inline embedding instead, which is useful for a
small application because it removes the need for a Storage-audience token
entirely.

### 3.3 `cli/qvf_extractor.py` — application metadata

A `.qvf` is a container. This module extracts the load script, data-model
metadata, sheets, and chart definitions, decoding base64-encoded entries where
present.

**Master items.** Charts frequently reference dimensions and measures by
`qLibraryId` rather than inline definition. Resolving these against the
application's library is required, or a chart appears to have no fields at all.

### 3.4 `cli/qlik_script_parser.py` — load script

Parses the Qlik load script into `QlikTable`, `QlikField`, and `QlikSource`
structures: table names, field lists, source declarations (delimited file, Excel,
resident, inline, QVD, SQL), and connection references.

Mapping tables and hidden tables are excluded from the migrated model.

**Derived fields.** Fields computed in the script (`ApplyMap`, `If`, date
arithmetic) have no counterpart in the physical source. Selecting them would
produce an all-null column, so they are dropped from the query and **reported**
— some are join keys built by concatenation, so losing one also loses a
relationship.

### 3.5 `cli/qlik_data_reader.py` — QIX engine reader

Reads real rows from a live Qlik Cloud application over a JSON-RPC websocket at
`wss://{tenant}/app/{appId}`.

**Why `GetTableData` and not a hypercube.** A hypercube over N dimensions returns
*distinct combinations*. Two identical records in the source would silently
collapse into one — a fidelity defect that looks like a smaller dataset rather
than an error.

**Column names.** `GetTableData` returns values only, with no header row. Column
names and their order come from `GetTablesAndKeys`, called first. `qSyntheticMode`
is false so the engine reports the source tables the load script created, rather
than its internal representation with synthetic keys spliced in.

**Paging.** Rows are requested in pages of 10,000. Raising the page size from
2,000 changed throughput not at all — measured at roughly 1,600 rows/sec either
way — because the cost is per *cell*, not per call. A short page is **not** the
end of the table: the engine limits a response by cell count, so a wide table
returns fewer rows than requested and still has more to give. Only a genuinely
empty page terminates the read. Treating "short" as "final" silently truncated
every table wider than a few columns.

**Row alignment.** A row whose length does not match the table's field count is
refused outright rather than padded. Shifting values into the wrong columns
produces a report that renders perfectly and means nothing.

**Partial reads.** If the engine reports N rows and stops returning data before
N, the table is rejected with the reason recorded — a partial table would look
complete in the report.

#### 3.5.1 Known limitation — formatted numerics

`_cell_value` prefers `qText`, which is Qlik's *formatted* representation. A
numeric field formatted `#,##0` arrives as `"1,013"`. Downstream, conservative
parsing then classifies the whole column as text.

This is deliberate over the alternative — coercing unparseable values to null
turns a visible wrong value into invisible missing data — but it means an
application with formatted numerics will lose aggregation on those columns.

**Status: open.** The fix is to prefer `qNumber` where `qIsNumeric` is set.
It is not yet applied because Qlik dates are also numeric, and `qNumber` returns
a Qlik serial rather than a date, so the change requires a format-aware decision
per field. The degradation is currently detected and reported per column in the
audit report.

### 3.6 `cli/powerquery_builder.py` — types and M generation

#### 3.6.1 Type resolution

`TypeResolver` resolves a column's type from the strongest available evidence, in
strict precedence:

1. Field tags from the `.qvf` data model (`$numeric`, `$integer`, `$date`, …)
2. The data model's `qis_numeric` flag
3. Tagged clauses in the load script
4. Text

**Column names are never consulted.** An earlier implementation matched
substrings such as `id` and `count`, which typed `show_id` and `country` as
numeric and generated `SUM` measures that fail in Power BI.

#### 3.6.2 M string escaping

M has exactly one escape mechanism inside quoted text: `#(...)`. Doubling the
quote character is therefore not sufficient on its own:

- `#(` opens an escape sequence, so a literal `#` followed by `(` must be written
  `#(#)`. A Qlik field named `Revenue #(000s)` otherwise terminates the literal
  early.
- A raw CR, LF, or TAB splits the token across lines, with the same result.

The `#(` substitution runs **first**, so it cannot re-escape the `#(cr)`,
`#(lf)`, `#(tab)` sequences introduced immediately after.

Getting this wrong produces a Fabric error reading only `Token ',' expected`,
naming neither the table nor the column.

#### 3.6.3 Embedded row expressions

`build_embedded_expression` emits a `#table(...)` literal carrying the rows
themselves. The declared type literal is derived from what the values actually
support, not from the resolver directly: a column typed numeric that holds one
unparseable value is written as text **for its whole length**, and the
disagreement is reported. Declaring `Int64.Type` over a value written as text
fails the entire mashup document.

### 3.7 `cli/parquet_writer.py` — Lakehouse staging format

Parquet rather than CSV, because a Delta table carries real types and a CSV round
trip would make every column text — discarding the type resolution the platform
works hard to get right.

**Arrow types are pinned, never inferred.** An empty table, or a column whose
every value is null, infers Arrow `null` — which Delta has no equivalent for, and
which the type-reporting function would then report as `string`, telling the
model to declare a type the file does not hold. Both failures are silent. Since
the Direct Lake path deliberately writes empty Parquet files for tables whose
source could not be read, this case is common rather than exotic.

`written_types()` reports what each column was **actually** written as, and the
model declares that. This is DP-02 in practice.

### 3.8 `cli/ai_qvf_to_powerbi.py` — the engine

The largest component. Three collaborating generators:

| Class | Responsibility |
| --- | --- |
| `UniversalModelGenerator` | Builds `model.bim`: tables, columns, types, measures, relationships |
| `UniversalVisualGenerator` | Builds individual visuals and their field bindings |
| `UniversalPBIPGenerator` | Orchestrates; writes the PBIP folder structure, `.pbit`, audit report, archive |

#### 3.8.1 Expression translation

`AIConverterBrain.translate_expression_to_dax` returns `(measure_name, dax)`.
Deterministic pattern matching handles the common cases first — `Count`,
`Count(DISTINCT …)`, single-field aggregations, ratios of two aggregations —
falling back to an LLM tier chain (Groq → Gemini → Ollama) and finally to a
`[Needs Review]` stub carrying the original Qlik text.

Results are cached on `(table_name, expression)`.

**Cross-table rebinding.** A Qlik expression freely mixes fields from across the
associative model, and Qlik needs no join. The translator is told one table, so
it qualifies every column with that table — producing references like
`'FactTable'[Fiscal Year]` for a column that lives elsewhere. `_rebind_columns`
corrects references to the owning table and flags the measure as needing a
relationship; where a column exists nowhere, the whole measure becomes a
reviewable stub rather than DAX that cannot evaluate.

#### 3.8.2 The measure-inventory guard

A measure name is derived **twice** from the same Qlik expression: once when the
measure is built, and again when a visual binds to it. The two derivations
resolve column casing against different column lists and can disagree — an
all-lowercase Qlik field such as `listed_in` becomes the measure `Listed_In Count`
through title-casing, and a different column list yields a different name.

Power BI answers a name that does not exist with `Missing_References`, and the
whole visual renders as an error box.

`UniversalModelGenerator.built_measures` records `table → {measure names}` as
measures are created. `_resolve_measure` then pins every projection to that
inventory:

1. If the measure exists on the named table, use it.
2. If it exists on another table, repoint to that table.
3. Otherwise fall back to the table's row-count measure and **record the
   substitution** in the audit report.

#### 3.8.3 The two report formats

A generated project contains **two** report definitions:

| Artefact | Consumer | Built by |
| --- | --- | --- |
| `definition/` (PBIR) | Microsoft Fabric | `UniversalVisualGenerator` |
| root `report.json` (legacy) | Power BI Desktop | `_build_legacy_vc` |

The legacy file exists so the `.pbip` opens in Desktop without preview features.
It has a structural constraint the PBIR format does not: its `prototypeQuery` has
a **single** `From` entry, so every property is read against one table alias. A
column that exists in the model but on a *different* table is therefore just as
broken as one that does not exist — Power BI answers both with
`Missing_References`.

The candidate column list is accordingly narrowed to columns the bound table
actually owns, and measures resolved to another owner fall back to the row count.

**The legacy file is withheld from publication.** `SKIP_AT_ITEM_ROOT` excludes
it, matched on the path relative to the item folder — matching on the bare
filename would also discard `definition/report.json`, which is the real report.

#### 3.8.4 Data carriage: two paths

```
                    rows available?
                          │
              ┌───────────┴───────────┐
              no                     yes
              │                       │
     schema-only table        stage_dir set?
     (real schema,          ┌────────┴────────┐
      zero rows,            no               yes
      reported)             │                 │
                     size within         Parquet →
                     embed budget?       Lakehouse →
                      ┌────┴────┐        Direct Lake
                     yes        no
                      │          │
                  embedded    trimmed +
                  #table      reported
```

**Import path.** Rows are embedded as an M `#table` literal. Bounded by the
Fabric item-creation request size; the budget is applied to *generated M text*
rather than row count, because 50,000 rows of a two-column lookup is trivial and
50,000 rows of a twenty-column fact table is 30 MB. Tables are filled smallest
first, so one wide fact table cannot crowd out every lookup around it.

**Direct Lake path.** Rows are written to Parquet, staged into a Lakehouse, and
loaded as Delta tables the model reads in place. No size ceiling. Every table
goes to the lakehouse, including ones that could not be read — mixing Direct Lake
and import partitions in one model is a compatibility minefield, and a uniform
model is worth an empty Parquet file.

#### 3.8.5 Placeholder substitution

A Direct Lake model must name the workspace and lakehouse GUIDs in its source
URL. **Neither exists when the engine runs** — the lakehouse is created at
publish time. The engine emits placeholders:

```
AzureStorage.DataLake(
  "https://onelake.dfs.fabric.microsoft.com/{{QLIKFAB_WORKSPACE_ID}}/{{QLIKFAB_LAKEHOUSE_ID}}",
  [HierarchicalNavigation = true])
```

Substitution happens **in flight** at publish time, not on disk, so the artefact
the user downloads keeps its placeholders and stays publishable to any workspace.

`AzureStorage.DataLake` identifies *Direct Lake on OneLake*, which runs under the
workspace identity. The alternative shape, pointing at the SQL analytics
endpoint, is Direct Lake on SQL and requires a stored credential — no gateway and
no secret to bind after publishing was a deliberate choice.

**`schemaName` is deliberately omitted** by the engine. Whether Delta tables live
at `Tables/<name>` or `Tables/<schema>/<name>` is a property of the lakehouse,
which does not exist yet. The publisher sets or removes it once Fabric answers.
Naming a schema that does not exist does not error — the partition simply
resolves to nothing, and every measure returns BLANK over a table that visibly
has data. That is the least debuggable failure of the set.

### 3.9 `cli/verify_pbip.py` — pre-publish verification

Checks a generated project for internal consistency. Returns `(fails, warns)`.

| Check | Severity |
| --- | --- |
| Fabricated-data tokens in any partition | Fail |
| Unescaped `#(`, raw control characters, empty quoted identifiers in M | Fail |
| Declared columns match those the partition emits | Fail |
| DAX measures reference columns that exist on the named table | Fail |
| Direct Lake partition names an entity | Fail |
| Direct Lake partition's shared expression exists in the model | Fail |
| Declared column type matches the staged Parquet type | Fail |
| Active relationships do not form an ambiguous loop | Fail |
| Every PBIR visual projection resolves | Fail |
| Legacy root `report.json` references resolve | **Warn** |
| Table with no columns | Warn |

The legacy check is a warning rather than a failure because that file is not
published — a defect there breaks the downloadable `.pbip`, not the published
report. Its wording deliberately avoids the phrases the publisher blocks on.

> **Design note.** The CLI entry point is guarded by `if __name__ == '__main__'`.
> Left at module level it executed on import and called `sys.exit()`, which would
> terminate the publisher process that imports this module to verify before
> uploading.

### 3.10 `onelake_client.py` — Lakehouse and OneLake

**Two different tokens.** This is the detail that catches people. The Fabric
Items API (`api.fabric.microsoft.com`) takes a token for the Fabric audience.
OneLake's DFS endpoint takes one for the **Storage** audience and nothing else —
the documentation is explicit. Passing the Fabric token to OneLake returns 401
with no useful explanation. Both are required and kept separate throughout.

**Addressing.** `https://onelake.dfs.fabric.microsoft.com/<workspaceGuid>/<itemGuid>/<path>`
— GUIDs for both, because names need an item-type suffix, break on spaces, and
change when someone renames the workspace.

**Upload protocol.** ADLS Gen2 three-step:

| Step | Call |
| --- | --- |
| Create (truncating) | `PUT …?resource=file` |
| Stage bytes | `PATCH …?action=append&position=N` |
| Commit | `PATCH …?action=flush&position=T` |

Append only *stages*. Without the final flush the file exists at zero length —
which reads back as a successful upload of an empty table.

**Load Table.** A raw file under `Files/` is not queryable as a table; the Load
Table operation registers it under `Tables/`. It is long-running and is followed
to completion rather than assumed.

### 3.11 `fabric_publisher.py` — publication

Publishing order matters: the **semantic model** is created first, then the
**report**, whose `definition.pbir` is rewritten from an on-disk relative path
(meaningless in a workspace) to a `byConnection` reference naming the model id
Fabric just assigned.

Sequence:

1. **Preflight** — run the verifier; refuse on any structural failure.
2. **Stage** — if `lakehouse/manifest.json` exists, ensure the lakehouse, upload
   each Parquet file, and load each as a Delta table.
3. **Resolve** — substitute placeholders and apply the lakehouse's actual schema
   convention.
4. **Guard** — refuse if any placeholder survives.
5. **Create** the semantic model, then the report.

#### 3.11.1 The unresolved-placeholder guard

`_resolve_placeholders` raises on leftovers, but it only runs when there was
something to stage against. When staging is skipped — no manifest, an empty
manifest, or a publisher loaded into memory before staging existed — the model
would be uploaded verbatim, with the literal text `{{QLIKFAB_WORKSPACE_ID}}` in
its source URL.

Fabric **accepts** that model. The semantic model and report appear in the
workspace, the publish looks successful, and then every visual on every page
renders *"Something's wrong with one or more fields"* — because no table
resolves, so nothing it declares exists. Nothing in that error names the cause.

`_refuse_unresolved_placeholders` matches the placeholder text directly and
refuses, naming both the symptom and the likely cause.

#### 3.11.2 Long-running operation resilience

Item creation returns `202 Accepted` with a `Location` header to poll.

**A failed poll is not a failed operation.** The work runs on Fabric's side,
unaffected by whether the host can reach it at that moment. A dropped connection
is retried until the deadline. Aborting on the first blip surfaced as a bare
`<urlopen error [WinError 10060]>` while the semantic model was in fact being
created — leaving an item in the workspace the run never reported.

OneLake's hostname resolves to a rotating pool of addresses, so a single
unhealthy endpoint produces exactly this intermittent timeout while an immediate
retry succeeds. One-shot polling is the wrong strategy against such a pool.

Where contact is lost for the full window, the error states that the item may
exist and warns that publishing again creates a duplicate rather than replacing
the first.

**Chunked uploads are deliberately not retried.** An ADLS append is positional;
blindly re-appending after a partially successful write would misalign the file.
A corrupted table is worse than a failed run.

### 3.12 Operator interface

`index.html`, `script.js`, `styles.css`. Single page, no build step, no external
runtime dependencies.

Migration logs are streamed by polling the run API and classified by phase, with
filters for each phase and for failures. Connection status messages support
`error`, `warning`, `success`, and `info` states.

**The Storage token is obtained at connection time**, in the same exchange as the
Fabric token, because the client secret is only in hand during that exchange —
it is never retained and cannot be minted later at publish time. If it cannot be
obtained, this is reported **immediately** as a warning rather than after a
migration that may run for minutes.

---

## 4. Data Flow

### 4.1 Tenant-sourced migration

```
Qlik Cloud tenant
   │ 1. REST: list apps                     (dev_server /qlik-proxy)
   │ 2. REST: export app  ─────────────────▶ .qvf on migration host
   │ 3. QIX websocket: GetTablesAndKeys
   │ 4. QIX websocket: GetTableData (paged) ─▶ rows in memory
   ▼
Engine
   │ 5. Parse script, resolve types
   │ 6. Translate expressions → DAX
   │ 7. Write Parquet  ────────────────────▶ out/lakehouse/*.parquet + manifest.json
   │ 8. Emit PBIP (model.bim with placeholders, PBIR report, .pbit)
   ▼
Publisher
   │ 9.  Verify  → refuse on structural failure
   │ 10. Upload Parquet → OneLake  (Storage-audience token)
   │ 11. Load Table → Delta        (Fabric-audience token)
   │ 12. Substitute placeholders, apply schema convention
   │ 13. Create semantic model, then report
   ▼
Microsoft Fabric workspace
```

### 4.2 Credential flow

| Credential | Obtained | Used for | Persisted |
| --- | --- | --- | --- |
| Qlik API key | Operator input | REST export, QIX session | No — passed to engine by environment variable |
| Entra client secret | Operator input | Token exchange only | No — discarded with the request |
| Fabric-audience token | `/fabric-token` | Fabric Items API, Load Table | No — held in page memory for the session |
| Storage-audience token | `/fabric-token` (same exchange) | OneLake DFS upload | No — held in page memory for the session |

---

## 5. Key Data Structures

### 5.1 Staging manifest — `out/lakehouse/manifest.json`

The publisher reads the manifest rather than globbing the directory, so a
partially written stage cannot be mistaken for a complete one.

```json
{
  "workspacePlaceholder": "{{QLIKFAB_WORKSPACE_ID}}",
  "lakehousePlaceholder": "{{QLIKFAB_LAKEHOUSE_ID}}",
  "tables": [
    {
      "table": "netflix_titles",
      "file": "netflix_titles.parquet",
      "rows": 8807,
      "columns": ["show_id", "title", "..."],
      "bytes": 184320,
      "types": { "show_id": "string", "release_year": "string" }
    }
  ]
}
```

`types` records what the Parquet file genuinely holds, and the model declares
exactly that.

### 5.2 Direct Lake partition

```json
{
  "name": "netflix_titles",
  "mode": "directLake",
  "source": {
    "type": "entity",
    "entityName": "netflix_titles",
    "expressionSource": "DatabaseQuery"
  }
}
```

`schemaName` is absent by design and added by the publisher only if the lakehouse
is schema-enabled.

---

## 6. Error Handling Design

Errors follow DP-04: name the artefact and the cause, because the downstream
platforms do not.

| Condition | Behaviour |
| --- | --- |
| Malformed M | Caught by verifier before upload; Fabric would report only `Token ',' expected` |
| Visual references a missing field | Rebound to a valid field and reported; Fabric would render an error box per visual |
| Unresolved Direct Lake placeholder | Publish refused, cause named |
| Missing Storage token | Reported at connection time, before the migration runs |
| Transient network failure while polling | Retried within the operation window |
| Publish outcome unconfirmed | Reported as possibly-created, with a duplicate warning |
| Stale publisher module | Publish refused with restart instructions |
| Missing Python dependency | Error names the **exact interpreter path** and the precise install command |
| Table read partially | Table rejected, reason recorded |
| Insufficient disk | Run refused before anything is written |

> **On the dependency message.** The engine is a subprocess of the server and
> inherits *its* interpreter. A host with both a python.org install and a
> Microsoft Store build can have a package in one and not the other; generic
> advice to `pip install X` then installs into the interpreter that already had
> it, and the error returns unchanged. Naming the path is the difference between
> a one-minute fix and an hour.

---

## 7. Security Design

| Control | Implementation |
| --- | --- |
| No credential persistence | Credentials used per-request and discarded |
| No secrets in process arguments | Qlik credential passed by environment variable |
| Server-side secret exchange | Entra client-credentials flow never runs in the browser |
| Least privilege | Service principal needs Contributor on the target workspace only |
| Audience separation | Fabric and Storage tokens obtained and used separately |
| Transport | TLS throughout; certificate verification required on the QIX websocket |
| Tenant id validation | Rejected unless alphanumeric with `-._`, as it lands in a URL path |
| Local-only control plane | The server binds locally; the browser never receives application data |
| Body size limits | Request bodies capped to prevent resource exhaustion |

**Not addressed by design:** Qlik section access is not translated. Migrated
reports carry no row-level security and must not be released to a wider audience
until equivalent Power BI RLS is applied. See BRD risk RI-09.

---

## 8. Verification and Test Approach

| Level | Approach |
| --- | --- |
| Structural | `verify_pbip.py` across every generated project |
| Regression | Four reference `.qvf` applications, both data paths, expected zero failures |
| Fault injection | Deliberately corrupted projects confirm each verifier check fires rather than passing vacuously |
| Reference reproduction | A known source application rebuilt from tenant metadata and compared field-for-field |
| Resilience | Simulated network failures confirm retry behaviour |
| Escaping | Adversarial field names (`"`, `#(`, control characters) round-tripped through M generation |

> **On fault injection.** A verifier that reports zero failures proves nothing
> until you have watched it report a failure you planted. One early check
> appeared to pass while examining nothing at all.

---

## 9. Known Limitations

| Ref | Limitation | Impact | Status |
| --- | --- | --- | --- |
| KL-01 | Formatted numerics arrive as text from QIX | Aggregations unavailable on affected columns | Open; detected and reported per column |
| KL-02 | Qlik section access not translated | No RLS on migrated reports | Out of scope; client action required |
| KL-03 | Visual layout approximated | Reports differ cosmetically from source | Accepted; reported in audit |
| KL-04 | Complex set analysis may not translate | Reviewable stub instead of a measure | Accepted by design (DP-01) |
| KL-05 | Qlik extensions have no equivalent | Those visuals are not migrated | Out of scope |
| KL-06 | Relationships inferred, not authoritative | May need manual adjustment | Reported in audit |
| KL-07 | `.qvf`-only migration carries no data | Schema-only tables | Inherent; use a tenant connection |
| KL-08 | Runs are held in memory | Migration history lost on server restart | Accepted; artefacts remain on disk |

---

## 10. Environment Dependencies

| Dependency | Purpose | Absence behaviour |
| --- | --- | --- |
| Python 3.12+ | Runtime | — |
| `websocket-client` | QIX engine session | Migration completes with every table empty; error names the interpreter |
| `pyarrow` | Parquet staging | Direct Lake path unavailable; error names the interpreter |
| Standard library | Everything else | — |

Declared in `requirements.txt`. They must be installed into the **same
interpreter that runs `dev_server.py`**.

---

*Prepared by SegueIT — https://segueit.com/*
*This document is confidential and intended solely for the named client engagement.*
