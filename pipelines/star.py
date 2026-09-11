# pipelines/star.py
"""The OLTP-to-star transformation, DECLARED rather than implemented.

    Data Pipeline as Code    the DAG is a reviewable file
    Transformation as Code   the shape change is declarative
    Data Quality as Code     expectations travel with the table

WHY THERE IS NO MERGE STATEMENT HERE, AND WHY THAT IS THE POINT.

The obvious implementation of Slowly Changing Dimension Type 2 is a MERGE that
closes the current row and inserts a new one. It is roughly thirty lines, it is
easy to write, and it is easy to get subtly wrong: late-arriving rows, duplicate
keys within one batch, event ordering, a rerun after a partial failure. Every
one of those failures is silent -- the totals are simply wrong months later.

Lakeflow implements exactly this. Declaring `stored_as_scd_type=2` on an
AUTO CDC flow makes the platform generate new rows for changes, close previous
versions and handle the mutations, with no custom MERGE SQL. The documented
advice is blunt: do not reinvent change handling, event ordering and late-data
logic unless you absolutely must.

A HAND-WRITTEN LOADER WOULD ALSO BE A SECOND SOURCE OF TRUTH about what a
version IS -- one answer in the merge, another in the pandera contract. The
declaration keeps it in one place.

EXPECTATIONS ARE THE FRAMEWORK'S TOO. `@dlt.expect_all_or_drop` enforces
quality at the table boundary and records the results as metrics, so bad rows
are quarantined rather than silently loaded -- and the outcome becomes data.

OUTSIDE src/ ON PURPOSE, AND THE REGISTRY PROVED WHY.

This file first lived under src/cscie103_olap_oltp/pipelines/. The error-code
registry discovers codes by walking every module in the package -- deliberately,
so a new error module cannot be forgotten -- and that walk imported this file
and died on `import dlt`, which exists only inside a pipeline runtime.

The import-linter contract would have stopped a DELIBERATE import; it could not
stop a traversal that imports everything by design. The boundary is therefore
physical: pipeline source is deployed to Databricks, never installed as library
code, so it does not sit in the importable package at all.
"""

from __future__ import annotations

import dlt  # type: ignore[import-not-found]  # provided by the pipeline runtime
from pyspark.sql import functions as F

# THE SOURCE SCHEMA COMES FROM PIPELINE CONFIGURATION, NOT FROM A LITERAL.
#
# Development mode renames schemas -- `oltp` deploys as `dev_<user>_oltp` -- so
# a hardcoded name works in production and silently reads an empty schema in
# dev. The bundle composes the effective name and passes it in; this reads it.
OLTP = "{}.{}".format(
    spark.conf.get("oltp_catalog"),
    spark.conf.get("oltp_schema"),
)


# --- Dimensions ---------------------------------------------------------------
#
# THE TARGET IS DECLARED EMPTY, then filled by the flow below. That split is how
# Lakeflow expresses "this table is maintained as SCD2" rather than "this table
# is the result of one query".
# THE SURROGATE KEY IS DECLARED, AND THAT IS THE NATIVE ANSWER.
#
# FOUND BY RUNNING IT: the fact flow failed with "Attribute `customer_key` is
# not supported. Did you mean: 'customer_id'?" -- AUTO CDC versions rows with
# __START_AT and __END_AT but adds no key of its own unless the target declares
# one. The documented pattern is an IDENTITY column on the streaming table.
#
# THE TEMPTING WORKAROUND WAS A HASH of (business key, version start). It would
# have worked, and it would have been hand-rolled key management -- precisely
# what this file exists to avoid. GENERATED ALWAYS gives dense integers, one per
# VERSION, which is what a surrogate key is for.
#
# DECLARING THE SCHEMA MEANS DECLARING __START_AT AND __END_AT TOO. The docs are
# explicit: for SCD Type 2, a specified target schema must include them, with
# the same type as sequence_by. An explicit schema REPLACES the inferred one, so
# omitting them removes the validity columns the fact's as-of join needs -- which
# is how this failed on columns that had existed a moment earlier.
#
# AN IDENTITY COLUMN DISABLES CONCURRENT TRANSACTIONS on the table. Acceptable
# for a dimension one pipeline owns; worth knowing before a second writer is
# ever added.
#
# THE COST IS REAL: this list and the pandera contract describe one table in two
# places, and nothing yet checks they agree. That gate is what this needs next.
dlt.create_streaming_table(
    name="dim_product",
    schema="""
        product_key BIGINT GENERATED ALWAYS AS IDENTITY,
        product_id BIGINT,
        product_name STRING,
        category_name STRING,
        list_price DOUBLE,
        updated_at TIMESTAMP,
        __START_AT TIMESTAMP,
        __END_AT TIMESTAMP
    """,
    comment="Product dimension, SCD Type 2. Denormalised: carries category_name.",
    expect_all_or_drop={
        "product_id_present": "product_id IS NOT NULL",
        "price_non_negative": "list_price >= 0",
    },
)


@dlt.view(name="product_source")
def product_source():  # type: ignore[no-untyped-def]
    """The OLTP side, joined to remove the category lookup.

    THIS JOIN IS THE DENORMALISATION. Third normal form put category_name in
    its own table so an update touches one row; the star folds it back in so a
    read touches one table. Doing it here, once, is what keeps every downstream
    query from repeating it.
    """
    product = dlt.read_stream(f"{OLTP}.product")
    category = dlt.read(f"{OLTP}.category")

    # THE JOIN COLUMN IS QUALIFIED, AND updated_at MUST BE TOO.
    #
    # Both tables carry updated_at -- every OLTP table does, because the CDC
    # flow sequences by it -- so an unqualified reference after the join is
    # AMBIGUOUS_REFERENCE rather than a sensible default. Spark is right to
    # refuse: picking one silently would decide which table's clock orders the
    # dimension, and the wrong choice would reorder history invisibly.
    #
    # THE PRODUCT'S CLOCK IS THE CORRECT ONE. This flow maintains dim_product;
    # a category renamed at a later timestamp must not make every product in it
    # look newly changed.
    return product.join(category, on="category_id", how="left").select(
        product["product_id"],
        product["product_name"],
        F.coalesce(category["category_name"], F.lit("Unknown")).alias("category_name"),
        product["list_price"],
        product["updated_at"],
    )


dlt.create_auto_cdc_flow(
    target="dim_product",
    source="product_source",
    keys=["product_id"],
    # SEQUENCING IS THE FRAMEWORK'S JOB. Given this column it orders changes and
    # handles late arrivals; a hand-written merge would need to solve that
    # itself, and usually does not.
    sequence_by=F.col("updated_at"),
    stored_as_scd_type=2,
)


dlt.create_streaming_table(
    name="dim_customer",
    schema="""
        customer_key BIGINT GENERATED ALWAYS AS IDENTITY,
        customer_id BIGINT,
        full_name STRING,
        email_domain STRING,
        updated_at TIMESTAMP,
        __START_AT TIMESTAMP,
        __END_AT TIMESTAMP
    """,
    comment="Customer dimension, SCD Type 2. Carries an email DOMAIN, never an address.",
    expect_all_or_drop={
        "customer_id_present": "customer_id IS NOT NULL",
        "no_personal_address": "email_domain NOT LIKE '%@%'",
    },
)


@dlt.view(name="customer_source")
def customer_source():  # type: ignore[no-untyped-def]
    return dlt.read_stream(f"{OLTP}.customer").select(
        "customer_id",
        "full_name",
        "email_domain",
        "updated_at",
    )


dlt.create_auto_cdc_flow(
    target="dim_customer",
    source="customer_source",
    keys=["customer_id"],
    sequence_by=F.col("updated_at"),
    stored_as_scd_type=2,
)


# --- Fact ---------------------------------------------------------------------


@dlt.table(
    name="fact_order_line",
    comment="One row per order line. Grain: (order_id, line_number).",
    partition_cols=["date_key"],
)
@dlt.expect_all_or_drop(
    {
        # THE GRAIN, ENFORCED AT THE BOUNDARY. A duplicate here double-counts
        # revenue and nothing downstream reports an error.
        "keys_present": "order_id IS NOT NULL AND line_number IS NOT NULL",
        "measures_non_negative": "quantity > 0 AND line_revenue >= 0",
        # NEVER A NULL FOREIGN KEY. The Unknown member is the alternative.
        "dimensions_resolved": "product_key IS NOT NULL AND customer_key IS NOT NULL",
    }
)
def fact_order_line():  # type: ignore[no-untyped-def]
    """Order lines resolved to the dimension versions current AT THE TIME.

    LAKEFLOW NAMES ITS VALIDITY COLUMNS __START_AT AND __END_AT, IN UPPERCASE,
    AND THEY ARE REACHED BY SUBSCRIPT RATHER THAN ATTRIBUTE. Python mangles
    dunder attribute access inside a class and PySpark refuses it outright --
    the error even suggests the correct case. Those names are also the reason a
    later migration off Lakeflow would touch this join: they are the framework's
    vocabulary, not ours.

    THE AS-OF JOIN IS WHAT MAKES SCD2 WORTH HAVING. Joining to `is_current`
    would attach today's price to a two-year-old sale; joining within the
    validity range attaches the price that was actually charged.
    """
    lines = dlt.read_stream(f"{OLTP}.order_line")
    orders = dlt.read(f"{OLTP}.order")
    products = dlt.read("dim_product")
    customers = dlt.read("dim_customer")

    enriched = lines.join(orders, on="order_id", how="inner")

    with_product = enriched.join(
        products,
        (enriched.product_id == products.product_id)
        & (enriched.ordered_at >= products["__START_AT"])
        & (products["__END_AT"].isNull() | (enriched.ordered_at < products["__END_AT"])),
        how="left",
    )

    with_customer = with_product.join(
        customers,
        (with_product.customer_id == customers.customer_id)
        & (with_product.ordered_at >= customers["__START_AT"])
        & (customers["__END_AT"].isNull() | (with_product.ordered_at < customers["__END_AT"])),
        how="left",
    )

    return with_customer.select(
        "order_id",
        "line_number",
        # AN UNRESOLVED REFERENCE BECOMES THE UNKNOWN MEMBER rather than a null,
        # so the row stays countable instead of vanishing from an inner join.
        F.coalesce(customers["customer_key"], F.lit(-1)).alias("customer_key"),
        F.coalesce(products["product_key"], F.lit(-1)).alias("product_key"),
        F.date_format("ordered_at", "yyyyMMdd").cast("int").alias("date_key"),
        "quantity",
        "unit_price",
        (F.col("quantity") * F.col("unit_price")).alias("line_revenue"),
    )
