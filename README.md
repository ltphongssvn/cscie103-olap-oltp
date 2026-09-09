# README.md

# cscie103-olap-oltp

OLTP (ER / 3NF) to OLAP (dimensional / star schema) data platform on Databricks,
built as an Everything-as-Code / Everything-as-Data system.

## Premise

| | OLTP | OLAP |
|---|---|---|
| Function | Day-to-day operation | Decision support |
| Design | Application oriented (3NF ER) | Subject oriented (star/snowflake) |
| Data | Current, detailed, isolated | Historical, summarized, consolidated |
| Access | Read/write, short transactions | Lots of scans, complex queries |
| Metric | Transaction throughput | Query throughput, response time |

Three tiers: OLTP sources -> ETL/conforming -> OLAP star schema.

## Architectural principle

Code expresses intent. Execution produces evidence. Evaluators produce verdicts.
The platform preserves all of them as data.

```
Policy as Code -> Execution -> Evidence as Data -> Verdict as Data
              -> Decision as Data -> Enforcement
```

Verdicts are three-valued: `pass` / `fail` / `unknown`. Absence of evidence is
never silently a pass. Every rule carries a stable id and a reason code.

## Layout

| Path | World | Holds |
|---|---|---|
| `src/cscie103_olap_oltp/contracts` | as code | Pydantic / Pandera schemas |
| `src/cscie103_olap_oltp/oltp` | as code | 3NF source-side models |
| `src/cscie103_olap_oltp/olap` | as code | Dimensions, facts, SCD2, grain |
| `src/cscie103_olap_oltp/etl` | as code | Pure transformation stages |
| `src/cscie103_olap_oltp/policy` | as code | Executable rules |
| `src/cscie103_olap_oltp/evidence` | as code | Evidence/verdict emitters |
| `contracts/odcs` | as code | Published ODCS v3.1 contracts |
| `policies` | as code | OPA/Rego repository policy |
| `resources` | as code | Databricks bundle resources |
| `tests` | as code | pytest: unit, integration, acceptance |
| `.artifacts` | as data | Evidence, verdicts, decisions (gitignored) |

## Stack

Python 3.12, PySpark on Databricks serverless, Pydantic v2, Pandera,
ODCS v3.1, pytest, Databricks Asset Bundles, OPA, GitHub Actions.

## Workflow

GitFlow. `main` is release; `develop` is the integration branch. Every feature
branch is cut from the verified-latest `develop` tip in its own git worktree.
Strict outside-in TDD: RED observed before GREEN, always.
