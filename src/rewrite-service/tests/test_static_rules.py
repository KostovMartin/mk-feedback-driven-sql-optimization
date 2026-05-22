from __future__ import annotations

from app.rules.engine import apply_rule_candidates

SCHEMA_CONTEXT = {
    "tables": [
        {
            "name": "lineitem",
            "columns": [
                {"name": "l_orderkey", "nullable": False},
                {"name": "l_partkey", "nullable": False},
            ],
        },
        {
            "name": "partsupp",
            "columns": [
                {"name": "ps_partkey", "nullable": True},
                {"name": "ps_supplycost", "nullable": False},
            ],
        },
        {
            "name": "partsupp_nn",
            "columns": [
                {"name": "ps_partkey", "nullable": False},
                {"name": "ps_supplycost", "nullable": False},
            ],
        },
    ]
}


def _candidates(
    sql: str,
    *,
    families: list[str] | None = None,
    schema_context: dict[str, object] | None = None,
) -> dict[str, str]:
    candidates, _ = apply_rule_candidates(
        sql,
        max_candidates=50,
        allowed_rule_families=families,
        schema_context=schema_context or SCHEMA_CONTEXT,
    )
    return {candidate.source_detail: candidate.sql_text for candidate in candidates}


def test_a1_removes_weaker_same_operator_predicate() -> None:
    sql = "SELECT l_orderkey FROM lineitem WHERE l_partkey > 10 AND l_partkey > 5;"

    candidates = _candidates(sql, families=["A"])

    assert "rule:predicate_redundant_elimination" in candidates
    assert "l_partkey > 5" not in candidates["rule:predicate_redundant_elimination"]
    assert "l_partkey > 10" in candidates["rule:predicate_redundant_elimination"]


def test_a1_does_not_collapse_range_bounds() -> None:
    sql = "SELECT l_orderkey FROM lineitem WHERE l_partkey > 10 AND l_partkey < 20;"

    assert "rule:predicate_redundant_elimination" not in _candidates(sql, families=["A"])


def test_rules_reject_outer_clauses_that_renderer_cannot_preserve() -> None:
    unsupported_outer_clause_queries = [
        "SELECT DISTINCT l_orderkey FROM lineitem WHERE l_partkey > 10 AND l_partkey > 5;",
        "SELECT l_orderkey FROM lineitem WHERE l_partkey > 10 AND l_partkey > 5 LIMIT 5;",
        "SELECT l_orderkey FROM lineitem WHERE l_partkey > 10 AND l_partkey > 5 OFFSET 2;",
        (
            "WITH recent AS (SELECT l_orderkey FROM lineitem) "
            "SELECT l_orderkey FROM lineitem WHERE l_partkey > 10 AND l_partkey > 5;"
        ),
        "SELECT l_orderkey FROM lineitem WHERE l_partkey > 10 AND l_partkey > 5 FOR UPDATE;",
    ]

    for sql in unsupported_outer_clause_queries:
        assert _candidates(sql, families=["A"]) == {}


def test_a2_simplifies_non_erasing_boolean_identity() -> None:
    sql = "SELECT l_orderkey FROM lineitem WHERE l_partkey > $1 AND TRUE;"

    candidates = _candidates(sql, families=["A"])

    assert candidates["rule:boolean_simplification"].strip() == (
        "SELECT l_orderkey\nFROM lineitem\nWHERE l_partkey > $1;"
    )


def test_a2_does_not_erase_the_only_predicate() -> None:
    sql = "SELECT l_orderkey FROM lineitem WHERE TRUE;"

    assert "rule:boolean_simplification" not in _candidates(sql, families=["A"])


def test_a3_normalizes_negated_comparison_without_changing_parameters() -> None:
    sql = "SELECT l_orderkey FROM lineitem WHERE NOT (l_partkey = $1);"

    candidates = _candidates(sql, families=["A"])

    assert candidates["rule:comparison_normalization"].strip() == (
        "SELECT l_orderkey\nFROM lineitem\nWHERE l_partkey <> $1;"
    )


def test_a3_leaves_non_comparison_predicates_alone() -> None:
    sql = "SELECT l_orderkey FROM lineitem WHERE NOT (l_partkey IS NULL);"

    assert "rule:comparison_normalization" not in _candidates(sql, families=["A"])


def test_b1_rewrites_positive_in_even_when_subquery_column_is_nullable() -> None:
    sql = (
        "SELECT l_orderkey FROM lineitem "
        "WHERE l_partkey IN (SELECT ps_partkey FROM partsupp WHERE ps_supplycost > $1);"
    )

    candidates = _candidates(sql, families=["B"])

    rewritten = candidates["rule:in_to_exists"]
    assert "WHERE EXISTS" in rewritten
    assert "ps_partkey = l_partkey" in rewritten


def test_b1_requires_simple_single_target_subquery() -> None:
    sql = (
        "SELECT l_orderkey FROM lineitem "
        "WHERE l_partkey IN (SELECT ps_partkey FROM partsupp GROUP BY ps_partkey);"
    )

    assert "rule:in_to_exists" not in _candidates(sql, families=["B"])


def test_b3_requires_not_null_outer_and_subquery_columns() -> None:
    nullable_sql = (
        "SELECT l_orderkey FROM lineitem "
        "WHERE l_partkey NOT IN (SELECT ps_partkey FROM partsupp WHERE ps_supplycost > $1);"
    )
    safe_sql = (
        "SELECT l_orderkey FROM lineitem "
        "WHERE l_partkey NOT IN (SELECT ps_partkey FROM partsupp_nn WHERE ps_supplycost > $1);"
    )

    assert "rule:not_in_to_not_exists" not in _candidates(nullable_sql, families=["B"])
    assert "rule:not_in_to_not_exists" in _candidates(safe_sql, families=["B"])


def test_b5_rewrites_count_existence_test_to_exists() -> None:
    sql = (
        "SELECT l_orderkey FROM lineitem WHERE ("
        "SELECT COUNT(*) FROM partsupp WHERE ps_supplycost > $1 AND ps_partkey = l_partkey"
        ") > 0;"
    )

    candidates = _candidates(sql, families=["B"])

    rewritten = candidates["rule:count_gt_zero_to_exists"]
    assert "COUNT" not in rewritten.upper()
    assert "EXISTS" in rewritten
    assert "ps_partkey = l_partkey" in rewritten


def test_b5_requires_count_star_greater_than_zero_existence_test() -> None:
    sql = (
        "SELECT l_orderkey FROM lineitem WHERE ("
        "SELECT COUNT(ps_partkey) FROM partsupp WHERE ps_supplycost > $1 AND ps_partkey = l_partkey"
        ") > 0;"
    )

    assert "rule:count_gt_zero_to_exists" not in _candidates(sql, families=["B"])


def test_c1_converts_two_table_implicit_join_to_explicit_join() -> None:
    sql = (
        "SELECT l.l_orderkey FROM lineitem l, partsupp p "
        "WHERE l.l_partkey = p.ps_partkey AND p.ps_supplycost > $1;"
    )

    candidates = _candidates(sql, families=["C"])

    rewritten = candidates["rule:implicit_to_explicit_join"]
    assert "INNER JOIN partsupp AS p ON l.l_partkey = p.ps_partkey" in rewritten
    assert "WHERE p.ps_supplycost > $1" in rewritten


def test_c1_converts_connected_multi_table_implicit_join_to_explicit_join() -> None:
    sql = (
        "SELECT MIN(t.title) AS movie_title "
        "FROM company_type AS ct, info_type AS it, movie_companies AS mc, "
        "movie_info_idx AS mi_idx, title AS t "
        "WHERE ct.kind = 'production companies' "
        "AND it.info = 'top 250 rank' "
        "AND ct.id = mc.company_type_id "
        "AND t.id = mc.movie_id "
        "AND t.id = mi_idx.movie_id "
        "AND mc.movie_id = mi_idx.movie_id "
        "AND it.id = mi_idx.info_type_id;"
    )

    candidates = _candidates(sql, families=["C"])

    rewritten = candidates["rule:implicit_to_explicit_join"]
    assert "INNER JOIN movie_companies AS mc ON ct.id = mc.company_type_id" in rewritten
    assert "INNER JOIN title AS t ON t.id = mc.movie_id" in rewritten
    assert (
        "INNER JOIN movie_info_idx AS mi_idx "
        "ON t.id = mi_idx.movie_id AND mc.movie_id = mi_idx.movie_id"
    ) in rewritten
    assert "INNER JOIN info_type AS it ON it.id = mi_idx.info_type_id" in rewritten
    assert "WHERE ct.kind = 'production companies' AND it.info = 'top 250 rank'" in rewritten


def test_c1_offers_alternate_multi_table_join_order_for_bandit_pool() -> None:
    sql = (
        "SELECT MIN(t.title) AS movie_title "
        "FROM company_type AS ct, info_type AS it, movie_companies AS mc, "
        "movie_info_idx AS mi_idx, title AS t "
        "WHERE ct.kind = 'production companies' "
        "AND it.info = 'top 250 rank' "
        "AND ct.id = mc.company_type_id "
        "AND t.id = mc.movie_id "
        "AND t.id = mi_idx.movie_id "
        "AND mc.movie_id = mi_idx.movie_id "
        "AND it.id = mi_idx.info_type_id;"
    )

    candidates = _candidates(sql, families=["C"])

    assert "rule:implicit_to_explicit_join" in candidates
    assert "rule:implicit_to_explicit_join_alternate_order" in candidates
    assert (
        candidates["rule:implicit_to_explicit_join"]
        != candidates["rule:implicit_to_explicit_join_alternate_order"]
    )


def test_c1_preserves_or_filter_grouping_when_converting_implicit_join() -> None:
    sql = (
        "SELECT MIN(n.name) AS voicing_actress, MIN(t.title) AS voiced_movie "
        "FROM cast_info AS ci, movie_companies AS mc, movie_info AS mi, name AS n, title AS t "
        "WHERE mc.note IS NOT NULL "
        "AND (mc.note LIKE '%(USA)%' OR mc.note LIKE '%(worldwide)%') "
        "AND mi.info IS NOT NULL "
        "AND (mi.info LIKE 'Japan:%200%' OR mi.info LIKE 'USA:%200%') "
        "AND n.gender = 'f' "
        "AND t.id = mi.movie_id "
        "AND t.id = mc.movie_id "
        "AND t.id = ci.movie_id "
        "AND mc.movie_id = mi.movie_id "
        "AND n.id = ci.person_id;"
    )

    candidates = _candidates(sql, families=["C"])

    rewritten = candidates["rule:implicit_to_explicit_join"]
    assert "(mc.note LIKE '%(USA)%' OR mc.note LIKE '%(worldwide)%')" in rewritten
    assert "(mi.info LIKE 'Japan:%200%' OR mi.info LIKE 'USA:%200%')" in rewritten


def test_c1_requires_equality_join_predicate() -> None:
    sql = (
        "SELECT l.l_orderkey FROM lineitem l, partsupp p "
        "WHERE l.l_partkey > p.ps_partkey AND p.ps_supplycost > $1;"
    )

    assert "rule:implicit_to_explicit_join" not in _candidates(sql, families=["C"])


def test_d2_removes_redundant_outer_group_by() -> None:
    sql = (
        "SELECT l_partkey FROM ("
        "SELECT l_partkey FROM lineitem GROUP BY l_partkey"
        ") grouped GROUP BY l_partkey;"
    )

    candidates = _candidates(sql, families=["D"])

    assert candidates["rule:redundant_group_by_elimination"].strip() == (
        "SELECT l_partkey\n"
        "FROM (SELECT l_partkey FROM lineitem GROUP BY l_partkey) AS grouped;"
    )


def test_d2_removes_redundant_outer_group_by_over_inner_aggregate_outputs() -> None:
    sql = (
        "SELECT nation_name, total_revenue FROM ("
        "SELECT n.n_name AS nation_name, SUM(l.l_extendedprice) AS total_revenue "
        "FROM lineitem l JOIN orders o ON l.l_orderkey = o.o_orderkey "
        "JOIN customer c ON o.o_custkey = c.c_custkey "
        "JOIN nation n ON c.c_nationkey = n.n_nationkey "
        "GROUP BY n.n_name"
        ") subq GROUP BY nation_name, total_revenue ORDER BY total_revenue DESC;"
    )

    candidates = _candidates(sql, families=["D"])

    rewritten = candidates["rule:redundant_group_by_elimination"]
    assert "GROUP BY nation_name, total_revenue" not in rewritten
    assert "ORDER BY total_revenue DESC" in rewritten


def test_d2_rejects_outer_aggregate_projection() -> None:
    sql = (
        "SELECT COUNT(*) FROM ("
        "SELECT l_partkey FROM lineitem GROUP BY l_partkey"
        ") grouped GROUP BY l_partkey;"
    )

    assert "rule:redundant_group_by_elimination" not in _candidates(sql, families=["D"])


def test_e2_prunes_only_unused_subquery_projection_columns() -> None:
    sql = "SELECT d.l_orderkey FROM (SELECT l_orderkey, l_partkey FROM lineitem) d;"

    candidates = _candidates(sql, families=["E"])

    assert candidates["rule:subquery_column_prune"].strip() == (
        "SELECT d.l_orderkey\nFROM (SELECT l_orderkey\nFROM lineitem) AS d;"
    )


def test_e2_rejects_distinct_subquery_column_pruning_counterexample() -> None:
    sql = "SELECT s.x FROM (SELECT DISTINCT a AS x, b FROM t) s;"

    assert "rule:subquery_column_prune" not in _candidates(sql, families=["E"])
