# Business Requirement Document

**Project:** QlikFab — Autonomous Qlik Sense to Microsoft Fabric Migration Platform
**Prepared by:** SegueIT — https://segueit.com/
**Document ID:** SEG-QF-BRD-001
**Version:** 1.0
**Date:** 13 August 2026
**Status:** Issued for review
**Classification:** Client Confidential

---

## Document Control

| Version | Date | Author | Change summary |
| --- | --- | --- | --- |
| 0.1 | 04 Aug 2026 | SegueIT Delivery | Initial draft, requirements capture |
| 0.9 | 11 Aug 2026 | SegueIT Delivery | Added Direct Lake requirements after volume testing |
| 1.0 | 13 Aug 2026 | SegueIT Delivery | Issued for client review |

### Reviewers and approvers

| Role | Name | Responsibility | Sign-off |
| --- | --- | --- | --- |
| Client Business Sponsor | *TBC* | Accepts business requirements | ☐ |
| Client BI Lead | *TBC* | Confirms reporting fidelity criteria | ☐ |
| Client Platform / Security | *TBC* | Confirms identity and data-residency terms | ☐ |
| SegueIT Engagement Lead | *TBC* | Confirms deliverability | ☐ |

---

## 1. Executive Summary

Organisations moving from Qlik Sense to Microsoft Fabric face a migration that is
mechanical in volume but expert in nature. Each Qlik application carries a load
script, a data model, a set of sheets, and dozens of chart expressions written in
Qlik's own expression language. Reproducing these by hand in Power BI is slow,
inconsistent between analysts, and difficult to audit after the fact.

QlikFab automates the repeatable majority of that work. It reads a Qlik Sense
application, derives its data model and visual layer, translates chart
expressions into DAX, generates a complete Power BI Project (PBIP), and publishes
the result into a Microsoft Fabric workspace as a semantic model and report.

The platform is built on one governing principle, which shapes every requirement
in this document:

> **The platform never invents data, and never guesses a type or a value it
> cannot derive from the source.** Where information is unavailable, the output
> states the gap explicitly rather than filling it with plausible-looking
> content.

This matters commercially. A migration tool that silently fabricates a column
type, a row of sample data, or a measure definition produces reports that look
finished and are wrong — a defect that surfaces only when a business user acts on
a bad number. QlikFab is designed so that an incomplete migration is *visibly*
incomplete.

---

## 2. Business Context and Problem Statement

### 2.1 Current state

| Aspect | Current position |
| --- | --- |
| Reporting platform | Qlik Sense (Qlik Cloud tenant) |
| Target platform | Microsoft Fabric (Power BI semantic models and reports) |
| Migration method | Manual rebuild by BI analysts |
| Effort per application | Days to weeks, depending on sheet and expression count |
| Consistency | Varies by analyst; naming and modelling conventions drift |
| Auditability | Limited — no systematic record of what was and was not carried over |

### 2.2 Problems to be solved

1. **Manual effort does not scale.** A portfolio of Qlik applications cannot be
   migrated within a sensible window at manual rates.
2. **Expression translation is specialist work.** Qlik set analysis and
   aggregation syntax has no one-to-one DAX equivalent; translation quality
   depends heavily on the individual analyst.
3. **Data does not travel with the application.** A Qlik `.qvf` file contains the
   application's structure but not its rows. Source files referenced by the load
   script sit on infrastructure the Fabric service cannot reach, so a naively
   migrated model publishes with every table empty.
4. **Silent incompleteness.** Manual migrations rarely produce a defensible
   record of what was approximated, dropped, or left for later.
5. **Volume ceilings.** Large fact tables cannot be carried inline inside a
   published semantic model; a different mechanism is required above a
   threshold.

### 2.3 Opportunity

An automated, auditable migration pipeline reduces per-application effort from
days to minutes for the mechanical portion, concentrates analyst time on the
genuinely ambiguous residue, and produces a written audit trail for every run.

---

## 3. Business Objectives and Success Criteria

| Ref | Objective | Success criterion | Measure |
| --- | --- | --- | --- |
| BO-01 | Reduce migration effort | Mechanical translation of an application completes without manual intervention | Elapsed engine time per application |
| BO-02 | Preserve reporting fidelity | Migrated visuals reference the same fields and aggregations as the source | Field- and measure-level comparison against source app |
| BO-03 | Carry real data to Fabric | Published reports render populated visuals, not empty axes | Row counts in the published semantic model |
| BO-04 | Guarantee auditability | Every run produces a written record of gaps and approximations | Migration audit report generated per run |
| BO-05 | Eliminate fabricated output | No placeholder or invented values reach a published report | Automated verifier check, zero tolerance |
| BO-06 | Support enterprise volumes | Applications with large fact tables migrate without truncation | Successful migration of tables beyond the inline size ceiling |
| BO-07 | Fail visibly, not silently | A migration that cannot complete says so before publishing | Pre-publish verification blocks defective output |

---

## 4. Stakeholders

| Stakeholder | Interest | Involvement |
| --- | --- | --- |
| Business Sponsor | Cost and timeline of platform migration | Approves scope and budget |
| BI / Analytics Lead | Fidelity of migrated reports | Defines acceptance criteria; reviews audit reports |
| BI Analysts / Report Authors | Day-to-day tool users | Operate the platform; complete flagged residue |
| Data Platform Team | Fabric workspace, capacity, lakehouse governance | Provisions workspace and service principal |
| Information Security | Credential handling, data movement | Reviews identity model and data flow |
| Qlik Administrators | Source tenant access | Issues API keys; confirms app access |
| SegueIT Delivery Team | Build and handover | Delivers platform and documentation |

---

## 5. Scope

### 5.1 In scope

| Ref | Capability |
| --- | --- |
| SC-01 | Ingest a Qlik Sense application, either as an uploaded `.qvf` file or directly from a Qlik Cloud tenant |
| SC-02 | Extract application metadata: load script, data model, fields, sheets, charts, dimensions and measures |
| SC-03 | Read real row data from a live Qlik Cloud tenant via the QIX engine |
| SC-04 | Derive a Power BI tabular data model with types resolved from source metadata |
| SC-05 | Translate Qlik chart expressions to DAX measures |
| SC-06 | Generate a complete PBIP project: semantic model, report definition, `.pbit` template, and packaged archive |
| SC-07 | Publish the semantic model and report into a Microsoft Fabric workspace |
| SC-08 | Stage large tables into a Fabric Lakehouse and bind the model to them via Direct Lake |
| SC-09 | Verify generated output before publishing and block defective projects |
| SC-10 | Produce a per-run migration audit report enumerating all gaps and approximations |
| SC-11 | Provide a browser-based operator interface with live migration logs |

### 5.2 Out of scope

| Ref | Exclusion | Rationale |
| --- | --- | --- |
| OS-01 | Migration of Qlik NPrinting, Alerting, or Automation artefacts | Different product surface, no Fabric equivalent |
| OS-02 | Row-level security and section access translation | Requires per-tenant security review; handled as a separate engagement |
| OS-03 | Qlik extensions and custom visuals | No general mapping to Power BI custom visuals exists |
| OS-04 | Pixel-accurate replication of Qlik sheet layout | Power BI's layout model differs; approximation is stated in the audit report |
| OS-05 | Rebuilding source data pipelines feeding Qlik | Upstream of this platform |
| OS-06 | Ongoing dual-run reconciliation between Qlik and Fabric | Operational activity, may be scoped separately |
| OS-07 | Translation of Qlik variables used for dynamic expression construction | Requires runtime evaluation; flagged for manual review |
| OS-08 | Fabric capacity sizing and cost optimisation | Client platform responsibility |

---

## 6. Business Requirements

| Ref | Requirement | Priority |
| --- | --- | --- |
| BR-01 | The platform shall migrate a Qlik Sense application into Fabric without manual code authoring for the mechanical portion of the work | Must |
| BR-02 | The platform shall never generate placeholder, sample, or otherwise fabricated data values | Must |
| BR-03 | The platform shall never infer a column's data type from its name; types shall derive only from source metadata | Must |
| BR-04 | Every migration shall produce a written audit report stating what was migrated, approximated, and omitted, with reasons | Must |
| BR-05 | The platform shall carry the application's real data into Fabric so published reports render populated visuals | Must |
| BR-06 | The platform shall support applications whose data volume exceeds the inline publishing ceiling | Must |
| BR-07 | The platform shall detect defective output before publishing and refuse to publish it | Must |
| BR-08 | Credentials shall not be persisted by the platform | Must |
| BR-09 | Operators shall see live progress during a migration | Should |
| BR-10 | Generated projects shall be downloadable for offline use in Power BI Desktop | Should |
| BR-11 | The platform shall be operable by a BI analyst without developer assistance | Should |
| BR-12 | Migration history shall be retrievable within an operating session | Could |

---

## 7. Functional Requirements

### 7.1 Ingestion

| Ref | Requirement | Priority |
| --- | --- | --- |
| FR-01 | Accept a `.qvf` file uploaded through the operator interface | Must |
| FR-02 | Connect to a Qlik Cloud tenant using an API key and list available applications | Must |
| FR-03 | Export a selected application from the tenant to the local host for processing | Must |
| FR-04 | Where the source is a tenant, read the application's real rows via the QIX engine | Must |
| FR-05 | Where the source is an uploaded `.qvf` alone, proceed with structure only and state in the audit report that no data was available | Must |

### 7.2 Extraction and modelling

| Ref | Requirement | Priority |
| --- | --- | --- |
| FR-06 | Parse the load script to identify tables, fields, and source declarations | Must |
| FR-07 | Where the load script cannot be parsed, fall back to the data-model metadata and record that fallback | Must |
| FR-08 | Resolve each column's data type from field tags and data-model metadata, in a defined precedence order | Must |
| FR-09 | Identify script-computed fields that have no counterpart in the physical source and report them as not carried over | Must |
| FR-10 | Establish table relationships where the source model implies them | Should |
| FR-11 | Accept tables reported by the engine that static script parsing did not produce, and record them as such | Must |

### 7.3 Expression translation

| Ref | Requirement | Priority |
| --- | --- | --- |
| FR-12 | Translate single-field Qlik aggregations (`Sum`, `Count`, `Avg`, `Min`, `Max`, `DISTINCT` variants) to equivalent DAX | Must |
| FR-13 | Translate ratio expressions composed of two aggregations | Should |
| FR-14 | Where an expression cannot be translated deterministically, produce a reviewable stub carrying the original Qlik text rather than incorrect DAX | Must |
| FR-15 | Repoint column references in generated DAX to the table that actually holds each column | Must |
| FR-16 | Flag measures whose references span tables, as these require a modelled relationship | Must |

### 7.4 Report generation

| Ref | Requirement | Priority |
| --- | --- | --- |
| FR-17 | Map Qlik chart types to the nearest Power BI visual type, recording any approximation | Must |
| FR-18 | Bind each visual only to columns and measures the generated model actually defines | Must |
| FR-19 | Where a chart's field cannot be bound, substitute a valid field and record the substitution | Must |
| FR-20 | Generate both a modern PBIR report definition and a Desktop-compatible legacy layout | Should |
| FR-21 | Produce a `.pbit` template and a packaged archive of the project | Should |

### 7.5 Data carriage

| Ref | Requirement | Priority |
| --- | --- | --- |
| FR-22 | For volumes within the inline budget, embed rows directly in the semantic model | Must |
| FR-23 | For volumes above the budget, write rows to Parquet and stage them into a Fabric Lakehouse | Must |
| FR-24 | Bind the semantic model to staged Lakehouse tables using Direct Lake | Must |
| FR-25 | Where rows must be trimmed to fit a budget, trim deterministically and record the reduction | Must |
| FR-26 | Declare each column in the model as the type its staged file genuinely holds, not the type hoped for | Must |

### 7.6 Verification and publishing

| Ref | Requirement | Priority |
| --- | --- | --- |
| FR-27 | Verify generated output for internal consistency before publishing | Must |
| FR-28 | Block publishing where verification finds a structural defect | Must |
| FR-29 | Create the semantic model in the target workspace, then the report bound to it | Must |
| FR-30 | Withhold from publication any artefact intended only for Power BI Desktop | Must |
| FR-31 | Refuse to publish a Direct Lake model whose lakehouse binding is unresolved | Must |
| FR-32 | Tolerate transient network failure during long-running publish operations without abandoning the operation | Must |
| FR-33 | Where a publish cannot be confirmed, state that the item may exist and warn against duplicate publishing | Must |

### 7.7 Operator interface

| Ref | Requirement | Priority |
| --- | --- | --- |
| FR-34 | Present connection setup for both Qlik Cloud and Microsoft Fabric | Must |
| FR-35 | Stream migration log lines to the operator as the run proceeds | Must |
| FR-36 | Allow filtering of log output by phase and by failure | Should |
| FR-37 | Report a missing prerequisite at connection time rather than after a long-running migration | Must |
| FR-38 | Offer download of the generated project | Should |

---

## 8. Non-Functional Requirements

| Ref | Category | Requirement |
| --- | --- | --- |
| NFR-01 | Security | Credentials are used for the request in hand and never written to disk |
| NFR-02 | Security | Secrets are never passed on a command line, as process arguments are readable by other processes on the host |
| NFR-03 | Security | The client secret is exchanged server-side; it is never presented from a browser origin |
| NFR-04 | Data residency | Application data travels tenant → migration host → Fabric; it is not routed through the operator's browser |
| NFR-05 | Reliability | Long-running remote operations are followed to completion rather than assumed successful |
| NFR-06 | Reliability | A transient connectivity failure while polling does not abort work already accepted by the remote service |
| NFR-07 | Integrity | Output is verified before publication; defective output is refused |
| NFR-08 | Transparency | Every approximation or omission appears in the audit report |
| NFR-09 | Observability | Failures name the specific artefact and cause, not only the symptom |
| NFR-10 | Capacity | Disk space is checked before a run begins, and working directories are reclaimed afterwards |
| NFR-11 | Portability | A generated project remains publishable to any workspace, not bound to the one it was generated against |
| NFR-12 | Usability | The operator interface is usable by a BI analyst without command-line interaction |
| NFR-13 | Maintainability | Dependencies are declared explicitly, and a missing one is reported with the exact remediation command |

---

## 9. Assumptions

| Ref | Assumption |
| --- | --- |
| AS-01 | The client holds a Microsoft Fabric capacity with a workspace available as the migration target |
| AS-02 | A service principal can be registered in Entra ID and granted Contributor on the target workspace |
| AS-03 | The tenant setting permitting service principals to use Fabric APIs is enabled |
| AS-04 | A Qlik Cloud API key can be issued with access to the applications in scope |
| AS-05 | Source Qlik applications have been reloaded and hold data at migration time |
| AS-06 | Network egress from the migration host to Qlik Cloud and Microsoft endpoints is permitted |
| AS-07 | Business users accept that visual layout is approximated rather than pixel-replicated |
| AS-08 | Analyst capacity is available to complete items the audit report flags for manual review |

## 10. Constraints

| Ref | Constraint |
| --- | --- |
| CO-01 | A `.qvf` file contains structure but not data; real rows require a live tenant connection |
| CO-02 | Embedded row data is bounded by the size a Fabric item-creation request accepts; larger volumes must use Lakehouse staging |
| CO-03 | The Fabric service cannot reach file paths on the migration host, so file-backed queries cannot resolve after publication |
| CO-04 | OneLake accepts only Storage-audience tokens; the Fabric API token is rejected there |
| CO-05 | Qlik expression semantics have no complete DAX equivalent; some expressions require human judgement |
| CO-06 | Qlik's associative model requires no explicit joins; Power BI requires modelled relationships |
| CO-07 | The QIX engine returns values in Qlik's formatted representation, which can affect numeric typing |

## 11. Dependencies

| Ref | Dependency | Owner |
| --- | --- | --- |
| DE-01 | Entra ID application registration and client secret | Client Security |
| DE-02 | Fabric workspace and capacity assignment | Client Platform |
| DE-03 | Qlik Cloud API key with application access | Qlik Administrator |
| DE-04 | Migration host with Python runtime and declared packages | Client Platform / SegueIT |
| DE-05 | Firewall allowances for Qlik Cloud, Fabric API, OneLake, and Entra endpoints | Client Network |

---

## 12. Risks

| Ref | Risk | Impact | Likelihood | Mitigation |
| --- | --- | --- | --- | --- |
| RI-01 | Complex Qlik expressions translate imperfectly | Incorrect business figures | Medium | Untranslatable expressions become reviewable stubs, never silent guesses; all are listed in the audit report |
| RI-02 | Source application has never been reloaded | Empty migration | Low | Row counts are read from the engine and reported before publishing |
| RI-03 | Service principal lacks workspace permission | Publish fails | Medium | Connection test performed before migration; error names the exact permission required |
| RI-04 | Storage-audience token unavailable | Large applications cannot publish | Medium | Reported at connection time, before a multi-minute migration begins |
| RI-05 | Numeric columns arrive formatted and degrade to text | Aggregations unavailable on those columns | Medium | Degradation is detected and reported per column in the audit report |
| RI-06 | Transient network failure during publish | Wasted run, or duplicate workspace items | Medium | Polling retries within the operation window; unconfirmed publishes warn against re-publishing |
| RI-07 | Uncommitted development work lost during branch operations | Rework | Medium | Source control discipline; commit before branch changes |
| RI-08 | Divergent Python interpreters on the host | Migration completes with empty tables | Medium | Dependency errors name the exact interpreter and remediation command |
| RI-09 | Qlik section access not migrated | Unrestricted data exposure in Fabric | High | Explicitly out of scope; client must apply Power BI RLS before release to business users |

> **RI-09 requires an explicit client decision.** Migrated reports carry no row-level
> security. They must not be released to a wider audience until equivalent
> restrictions are applied in Power BI.

---

## 13. Acceptance Criteria

The platform is accepted when, for an agreed representative sample of applications:

| Ref | Criterion |
| --- | --- |
| AC-01 | Each application migrates end to end without manual code authoring |
| AC-02 | Published reports render populated visuals, with row counts reconciling to the source application |
| AC-03 | Every visual in a published report resolves; none render a field-reference error |
| AC-04 | The automated verifier reports zero structural failures for each accepted project |
| AC-05 | No fabricated value appears in any generated artefact |
| AC-06 | Each run produces an audit report enumerating gaps, approximations, and manual-review items |
| AC-07 | An application exceeding the inline size ceiling migrates via Lakehouse staging without truncation |
| AC-08 | A defective project is refused at the pre-publish check rather than published |
| AC-09 | No credential is found persisted on the migration host after a run |
| AC-10 | A BI analyst completes a migration unaided, following the deployment and operating documentation |

---

## 14. Glossary

| Term | Definition |
| --- | --- |
| **BRD** | Business Requirement Document — this document |
| **DAX** | Data Analysis Expressions; the calculation language of Power BI |
| **Delta table** | Open table format read natively by Fabric |
| **Direct Lake** | Fabric mode in which a semantic model reads Delta files in place, without import or a live query |
| **Fabric** | Microsoft Fabric, the target analytics platform |
| **Lakehouse** | Fabric storage item holding files and Delta tables |
| **M / Power Query** | The data-transformation language behind a Power BI import query |
| **Master item** | A Qlik dimension or measure defined once in a library and reused across charts |
| **OneLake** | The storage layer beneath Fabric |
| **PBIP** | Power BI Project; the folder-based, source-controllable project format |
| **PBIR** | The modern report definition format inside a PBIP |
| **QIX** | The Qlik engine protocol, over which an application's data can be read |
| **QVF** | Qlik Sense application file; carries structure but not source data |
| **Semantic model** | The Power BI dataset: tables, relationships, measures |
| **Service principal** | A non-interactive Entra ID identity used for automation |
| **Set analysis** | Qlik expression syntax for scoped aggregation |

---

*Prepared by SegueIT — https://segueit.com/*
*This document is confidential and intended solely for the named client engagement.*
