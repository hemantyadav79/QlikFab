# Scope of Work

**Project:** QlikFab — Autonomous Qlik Sense to Microsoft Fabric Migration Platform
**Supplier:** SegueIT — https://segueit.com/
**Document ID:** SEG-QF-SOW-001
**Version:** 1.0
**Date:** 13 August 2026
**Status:** Issued for signature
**Classification:** Client Confidential

---

## Document Control

| Version | Date | Author | Change summary |
| --- | --- | --- | --- |
| 0.1 | 05 Aug 2026 | SegueIT Delivery | Initial scope draft |
| 1.0 | 13 Aug 2026 | SegueIT Delivery | Issued for signature |

### Signatures

| Party | Name | Title | Signature | Date |
| --- | --- | --- | --- | --- |
| Client | *TBC* | *TBC* | | |
| SegueIT | *TBC* | *TBC* | | |

This Scope of Work is governed by the Master Services Agreement between the
parties. Where this document and the MSA conflict, the MSA prevails.

---

## 1. Engagement Summary

SegueIT will deliver QlikFab, an automated migration platform that converts Qlik
Sense applications into Microsoft Fabric semantic models and reports, together
with the documentation and knowledge transfer required for the Client to operate
it independently.

The engagement covers platform delivery and the migration of an agreed pilot
set of applications. Migration of the Client's wider application portfolio is
addressed as an optional extension in Section 5.

---

## 2. Objectives

| Ref | Objective |
| --- | --- |
| OB-01 | Deliver a working migration platform installed in the Client's environment |
| OB-02 | Automate the mechanical portion of Qlik-to-Fabric migration end to end |
| OB-03 | Ensure migrated reports carry the source application's real data |
| OB-04 | Produce an auditable record of every migration run |
| OB-05 | Transfer operating knowledge to the Client's BI team |

---

## 3. Scope of Services

### 3.1 In scope

**Phase 1 — Discovery and Environment Readiness**

| Ref | Activity |
| --- | --- |
| 1.1 | Review the Client's Qlik estate and select the pilot application set |
| 1.2 | Confirm Fabric workspace, capacity, and identity model |
| 1.3 | Define acceptance criteria and fidelity expectations with the BI Lead |
| 1.4 | Confirm network egress and firewall allowances |
| 1.5 | Document prerequisites and produce the environment readiness checklist |

**Phase 2 — Platform Deployment**

| Ref | Activity |
| --- | --- |
| 2.1 | Install the platform on the designated migration host |
| 2.2 | Configure the Entra ID service principal and Fabric workspace access |
| 2.3 | Configure Qlik Cloud tenant connectivity |
| 2.4 | Validate both connections through the platform's built-in connection tests |
| 2.5 | Execute a smoke-test migration and confirm a published, populated report |

**Phase 3 — Pilot Migration**

| Ref | Activity |
| --- | --- |
| 3.1 | Migrate each application in the agreed pilot set |
| 3.2 | Review each migration audit report with the Client's BI Lead |
| 3.3 | Complete or triage items flagged for manual review |
| 3.4 | Reconcile row counts and key figures against the source application |
| 3.5 | Record outcomes and any residual gaps per application |

**Phase 4 — Handover and Enablement**

| Ref | Activity |
| --- | --- |
| 4.1 | Deliver the documentation set listed in Section 4 |
| 4.2 | Conduct operator training for the Client's BI team |
| 4.3 | Conduct a technical walkthrough for the Client's platform team |
| 4.4 | Provide an agreed period of post-handover support (Section 8) |

### 3.2 Out of scope

The following are explicitly excluded. Any may be added by the change control
procedure in Section 10.

| Ref | Exclusion |
| --- | --- |
| EX-01 | Migration of Qlik NPrinting, Alerting, or Automation artefacts |
| EX-02 | Translation of Qlik section access or row-level security into Power BI RLS |
| EX-03 | Migration or replacement of Qlik extensions and custom visuals |
| EX-04 | Pixel-accurate replication of Qlik sheet layout |
| EX-05 | Rebuilding or re-pointing the upstream data pipelines that feed Qlik |
| EX-06 | Fabric capacity sizing, cost management, or performance tuning |
| EX-07 | Ongoing dual-run reconciliation between Qlik and Fabric |
| EX-08 | Decommissioning of the Qlik estate |
| EX-09 | End-user training on Power BI as a product |
| EX-10 | Migration of applications beyond the agreed pilot set |
| EX-11 | Translation of Qlik variables used to construct expressions dynamically at runtime |
| EX-12 | Provision of Microsoft or Qlik licences |

> **EX-02 carries a release risk.** Migrated reports carry no row-level security.
> The Client is responsible for applying equivalent restrictions in Power BI
> before releasing any migrated report to a wider audience.

---

## 4. Deliverables

| Ref | Deliverable | Format | Acceptance |
| --- | --- | --- | --- |
| DL-01 | QlikFab platform, installed and configured | Deployed software | Smoke-test migration publishes a populated report |
| DL-02 | Business Requirement Document | Document | Client review and sign-off |
| DL-03 | Scope of Work (this document) | Document | Signature by both parties |
| DL-04 | Detailed Design Document | Document | Client technical review |
| DL-05 | Deployment Document | Document | Successful independent deployment by Client staff |
| DL-06 | Migrated pilot applications in the Fabric workspace | Fabric items | Acceptance criteria in Section 7 met |
| DL-07 | Per-application migration audit reports | Markdown, one per run | Delivered with each migration |
| DL-08 | Operator training session | Session plus materials | Delivered and attended |
| DL-09 | Technical walkthrough | Session | Delivered and attended |

---

## 5. Optional Extensions

Priced separately and not included in this Scope of Work.

| Ref | Extension |
| --- | --- |
| OP-01 | Migration of the remaining application portfolio beyond the pilot set |
| OP-02 | Design and implementation of Power BI row-level security equivalent to Qlik section access |
| OP-03 | Rebuilding Qlik extensions as Power BI custom visuals |
| OP-04 | Automated Qlik-to-Fabric figure reconciliation harness |
| OP-05 | Integration of the migration pipeline into a CI/CD process |
| OP-06 | Extended managed support beyond the period in Section 8 |

---

## 6. Timeline and Milestones

Indicative, assuming Client prerequisites are met on schedule. Working days.

| Milestone | Phase | Duration | Dependency |
| --- | --- | --- | --- |
| M1 — Discovery complete | 1 | 5 days | Client SME availability |
| M2 — Environment ready | 1–2 | 5 days | Workspace, service principal, Qlik key issued |
| M3 — Platform deployed and smoke-tested | 2 | 5 days | M2 |
| M4 — Pilot migration complete | 3 | 10 days | M3, pilot set agreed |
| M5 — Acceptance | 3 | 5 days | M4, Client review |
| M6 — Handover complete | 4 | 5 days | M5 |

**Indicative total: 35 working days.**

Timeline assumes the pilot set is agreed at M1 and does not change. Pilot
applications of unusual complexity may extend M4; this will be raised as a change
request rather than absorbed silently.

---

## 7. Acceptance Procedure

### 7.1 Criteria

A migrated application is accepted when:

| Ref | Criterion |
| --- | --- |
| AC-01 | The semantic model and report exist in the target workspace |
| AC-02 | Every visual resolves; none renders a field-reference error |
| AC-03 | Row counts reconcile to the source application, or any difference is stated and agreed |
| AC-04 | The automated verifier reports zero structural failures |
| AC-05 | An audit report has been delivered and reviewed |
| AC-06 | Items flagged for manual review have been triaged and dispositioned |
| AC-07 | No fabricated value appears in any generated artefact |

The platform is accepted when all pilot applications are accepted and
deliverables DL-01 to DL-09 are delivered.

### 7.2 Process

1. SegueIT notifies the Client that a deliverable is ready for acceptance.
2. The Client has **five working days** to accept or raise defects in writing.
3. SegueIT remedies valid defects and resubmits.
4. Absent written response within the review period, the deliverable is deemed
   accepted.

### 7.3 Defect definition

A defect is a failure to meet a stated acceptance criterion. The following are
**not** defects:

- An approximation that the audit report discloses and the criteria permit
  (for example, visual layout differences or an approximated chart type).
- An expression flagged for manual review because it cannot be translated
  deterministically.
- An empty table where the source application itself holds no rows.
- Absence of functionality listed as out of scope in Section 3.2.

---

## 8. Post-Handover Support

| Item | Terms |
| --- | --- |
| Period | 20 working days from acceptance |
| Coverage | Defects in delivered platform components |
| Excluded | New features, out-of-scope items, Client environment changes, third-party platform changes |
| Channel | Nominated SegueIT contact, business hours |
| Response | Next business day acknowledgement |

Support beyond this period is available under OP-06.

---

## 9. Roles and Responsibilities

### 9.1 SegueIT

| Role | Responsibility |
| --- | --- |
| Engagement Lead | Commercial ownership, change control, escalation |
| Technical Lead | Architecture, design decisions, technical quality |
| Migration Engineer | Platform deployment, migration execution, defect resolution |
| BI Consultant | Expression translation review, fidelity assessment |

### 9.2 Client

| Role | Responsibility |
| --- | --- |
| Business Sponsor | Scope and commercial approval, escalation |
| BI Lead | Acceptance criteria, audit report review, sign-off |
| Platform Team | Fabric workspace, capacity, migration host |
| Security Team | Service principal approval, identity review |
| Qlik Administrator | API key issuance, source application access |

### 9.3 RACI

| Activity | SegueIT | Client BI | Client Platform | Client Security |
| --- | --- | --- | --- | --- |
| Discovery and pilot selection | R | A | C | I |
| Fabric workspace provisioning | C | I | R/A | C |
| Service principal creation | C | I | C | R/A |
| Qlik API key issuance | C | I | R/A | C |
| Platform deployment | R/A | I | C | I |
| Migration execution | R/A | C | I | I |
| Audit report review | C | R/A | I | I |
| Manual-review triage | C | R/A | I | I |
| Acceptance sign-off | I | A | C | I |
| RLS implementation (OP-02) | C | R/A | C | C |

*R = Responsible, A = Accountable, C = Consulted, I = Informed*

---

## 10. Change Control

1. Either party may raise a change request in writing.
2. SegueIT assesses the impact on scope, timeline, and cost within **five working
   days**.
3. Work does not begin until both parties approve the assessment in writing.
4. Approved changes are appended to this document as numbered addenda.

No change is treated as approved by conduct, verbal agreement, or informal
correspondence.

---

## 11. Client Obligations

Delivery depends on the Client providing, at the times agreed:

| Ref | Obligation |
| --- | --- |
| CL-01 | A Microsoft Fabric workspace on an active capacity |
| CL-02 | An Entra ID service principal with Contributor on that workspace |
| CL-03 | The tenant setting permitting service principals to use Fabric APIs, enabled |
| CL-04 | A Qlik Cloud API key with access to the applications in scope |
| CL-05 | A migration host meeting the specification in the Deployment Document |
| CL-06 | Network egress to Qlik Cloud, Fabric API, OneLake, and Entra endpoints |
| CL-07 | Reloaded source applications holding current data |
| CL-08 | SME availability for discovery, review, and acceptance |
| CL-09 | Timely written acceptance decisions within the stated review period |

Delay in meeting these obligations may affect the timeline in Section 6 and will
be managed through change control.

---

## 12. Assumptions

| Ref | Assumption |
| --- | --- |
| AS-01 | The pilot set is agreed at M1 and remains stable |
| AS-02 | Source applications are representative of the wider portfolio |
| AS-03 | Work is performed remotely unless otherwise agreed |
| AS-04 | The Client accepts approximated visual layout |
| AS-05 | Client analyst capacity is available for manual-review triage |
| AS-06 | Qlik and Microsoft platform behaviour does not change materially during the engagement |
| AS-07 | One migration host is sufficient for the pilot volume |

---

## 13. Commercials

| Item | Terms |
| --- | --- |
| Basis | *To be completed — fixed price or time and materials* |
| Fees | *TBC* |
| Expenses | Pre-approved, at cost |
| Invoicing | On milestone acceptance |
| Payment | *Per MSA* |
| Third-party licences | Client responsibility; not included |

---

*Prepared by SegueIT — https://segueit.com/*
*This document is confidential and intended solely for the named client engagement.*
