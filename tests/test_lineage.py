# tests/test_lineage.py
"""Lineage, derived from the pipeline rather than described beside it.

    Lineage as Code   the graph is declared by the transformation itself
    Lineage as Data   it is emitted as queryable facts, not a picture

WHY LINEAGE IS NOT A DIAGRAM. A drawing of the flow is a claim someone made
once; it cannot fail. The question lineage exists to answer is operational --
"if I change oltp.product, what breaks" -- and an answer that is not derived
from the actual pipeline is worse than none, because it is trusted.

THE SOURCE IS THE PIPELINE, NOT A SECOND DESCRIPTION. Unity Catalog records
table-level lineage for every pipeline update automatically, so the graph
already exists as data. Re-declaring it here would be the duplication this
repository refuses; reading it is the whole job.

THE INVARIANTS ARE THE DOMAIN'S:

    every OLAP table traces back to at least one OLTP table
    no OLAP table depends on a table outside this catalog
    the fact depends on BOTH dimensions -- a star with one is not a star
    dim_product depends on category, which is the denormalisation itself
    nothing in the OLTP schema depends on anything in OLAP (no cycle)

THE LAST ONE IS THE ARCHITECTURAL RULE. A dependency pointing back from the
warehouse into the source turns a batch into a loop, and the failure is a
pipeline that never converges rather than an error anybody reads.
"""

import pytest

from cscie103_olap_oltp.olap.lineage import OLAP_TABLES, upstream_of

pytestmark = [pytest.mark.integration, pytest.mark.spark]


def test_every_olap_table_has_upstream_lineage() -> None:
    """A TABLE WITH NO RECORDED SOURCE IS EITHER UNBUILT OR UNTRACED, and both
    are worth failing on: the first means the pipeline never ran, the second
    means the catalog cannot answer the question lineage exists for."""
    for table in OLAP_TABLES:
        assert upstream_of(table), f"{table} has no recorded upstream"


def test_the_fact_depends_on_both_dimensions() -> None:
    """A STAR WITH ONE DIMENSION IS A LIST. The fact must reach both, or the
    as-of join silently resolved against only one of them."""
    upstream = upstream_of("fact_order_line")

    assert any("dim_product" in name for name in upstream)
    assert any("dim_customer" in name for name in upstream)


def test_the_product_dimension_depends_on_category() -> None:
    """THE DENORMALISATION, VISIBLE IN THE GRAPH.

    dim_product carries category_name, so it must depend on the category table.
    If that edge is absent, the join was dropped and the dimension is silently
    carrying "Unknown" for every row.
    """
    upstream = upstream_of("dim_product")

    assert any(name.endswith(".category") for name in upstream)
    assert any(name.endswith(".product") for name in upstream)


def test_no_olap_table_depends_on_another_catalog() -> None:
    """ISOLATION IS THE ONE BOUNDARY THIS TIER OFFERS.

    Free Edition shares a metastore with the sibling project, so a stray
    reference to its catalog would be invisible in code and obvious here.
    """
    for table in OLAP_TABLES:
        for name in upstream_of(table):
            assert name.startswith("cscie103_olap_oltp."), f"{table} reaches {name}"


def test_the_oltp_side_depends_on_nothing_in_olap() -> None:
    """NO CYCLE. A dependency from source back into the warehouse turns a batch
    into a loop that never converges, and nothing reports an error."""
    for table in OLAP_TABLES:
        for name in upstream_of(table):
            assert ".olap" not in name.replace(table, ""), f"{table} reads its own schema"
