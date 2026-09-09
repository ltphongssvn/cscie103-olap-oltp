# src/cscie103_olap_oltp/__init__.py
"""OLTP (ER/3NF) to OLAP (dimensional) data platform.

The package is split by which world a module belongs to, so the boundary is
visible from the import path alone:

    contracts   AS CODE   Pydantic and Pandera schemas: what must be true
    oltp        AS CODE   3NF source-side entity models
    olap        AS CODE   dimensions, facts, grain, SCD Type 2
    etl         AS CODE   pure transformation stages between the two
    policy      AS CODE   executable rules with stable ids and reason codes
    evidence    AS DATA   emitters for evidence, verdicts and decisions

Code expresses intent. Execution produces evidence. Evaluators produce verdicts.
The platform preserves all of them as data.
"""

# DECLARED HERE AND NOWHERE ELSE. pyproject.toml carries the distribution
# version; this is the importable one. They are kept identical by the release
# gate rather than by remembering, because two copies of one fact drift.
__version__ = "0.1.0"
