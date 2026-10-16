# -*- coding: utf-8 -*-
"""Phase 2.2 第三项：跨 Sheet / 跨表查询能力验证。

不修改业务代码语义、不安装无关依赖、不调用真实模型 API、不触碰正式 SQLite/Chroma/用户数据。

【真实执行的部分】
- TableQueryService._pipeline 完整链路（AST 校验 -> crosstable 改写 -> DuckDB 执行 -> cache）
- DuckDB 原生 JOIN / UNION / UNION ALL 语义（含去重 vs 保留全部行的区别）
- validate_sql（AST）对多表白名单的递归校验

【mock 的部分】
- NL2SQL / 解释文本（受控返回，绕过真实 LLM）
- ensure_user_tables（表已注册到内存引擎，不再从磁盘加载）

本轮验证的跨表能力：
  A. 同 Workbook 多 Sheet -> 多 DuckDB 表 -> JOIN 正确结果
  B. UNION（去重）语义
  C. UNION ALL（保留全部行，不去重）语义
  D. 跨表引用中只要有一张未授权表 -> 整条 SQL 拒绝（AST 兜底）
  E. 跨用户表隔离（用户 A 的表不能 JOIN 用户 B 的表）
  F. Cache 不同 table scope 不共享结果
  G. 修改某张表后，涉及它的多表查询 cache 必须失效
  H. CTE + 多表 组合可被 AST 正确校验并执行
  I. “全量枚举”自动跨表改写使用 UNION ALL（保留重复行，不去重）
"""
import asyncio

import pytest

from backend.services.cache_service import get_cache_service
from backend.services.table_query_service import (
    TableQueryService,
    DuckDBTableEngine,
    validate_sql,
)


# ----------------------------- 隔离 helper（复用 hardening 模式） -----------------------------
def _col_defs(columns):
    return [
        {"technical_name": n, "data_type": dt, "description": desc, "col_index": i}
        for i, (n, dt, desc) in enumerate(columns)
    ]


def _rows(columns, rows):
    out = []
    for ri, rd in enumerate(rows):
        cells = [{"col_index": i, "value": rd[n]} for i, (n, _, _) in enumerate(columns)]
        out.append({"row_index": ri, "range": f"A{ri + 1}", "cells": cells})
    return out


def _build_rep(doc_id, user_id, filename, columns, rows):
    return {
        "document_id": doc_id,
        "filename": filename,
        "user_id": user_id,
        "workbook": {
            "sheets": [{
                "sheet_name": "Sheet1",
                "tables": [{"columns": _col_defs(columns), "rows": _rows(columns, rows)}],
            }]
        },
    }


def _build_multi_sheet_rep(doc_id, user_id, filename, sheets):
    """sheets: list of (sheet_name, columns, rows)。每个 sheet 注册为独立 DuckDB 表。"""
    sheet_list = []
    for sname, cols, rows in sheets:
        sheet_list.append({
            "sheet_name": sname,
            "tables": [{"columns": _col_defs(cols), "rows": _rows(cols, rows)}],
        })
    return {
        "document_id": doc_id,
        "filename": filename,
        "user_id": user_id,
        "workbook": {"sheets": sheet_list},
    }


def _isolated_tq(rep, user_id=153):
    tq = TableQueryService()
    engine = DuckDBTableEngine()
    tq.engine = engine
    if rep is not None:
        engine.register_representation(rep, user_id)
    return tq


def _tables_of_doc(engine: DuckDBTableEngine, doc_id: str) -> list:
    """返回某 document 注册的所有物理表名（按 sheet 顺序）。"""
    return [t for t, m in engine.table_meta.items() if m.get("document_id") == doc_id]


def _patch_llm(monkeypatch, sql=None, explain="（解释）"):
    async def fake_nl2sql(self, question, schema, alias, allowed):
        return sql
    async def fake_explain(self, *args, **kwargs):
        return explain
    monkeypatch.setattr(TableQueryService, "_nl2sql", fake_nl2sql)
    monkeypatch.setattr(TableQueryService, "_explain_text", fake_explain)


@pytest.fixture(autouse=True)
def _clear_cache():
    """每个测试前清空全局缓存单例，避免跨测试 HIT 干扰。"""
    get_cache_service()._cache.clear()
    yield


def _count_execute(monkeypatch):
    calls = {"n": 0}
    orig = TableQueryService._execute_with_fallback

    def counted(self, sql, *a, **k):
        calls["n"] += 1
        return orig(self, sql, *a, **k)

    monkeypatch.setattr(TableQueryService, "_execute_with_fallback", counted)
    return calls


def _tabs(tq, doc_id):
    return _tables_of_doc(tq.engine, doc_id)


# ============================ A. 跨 Sheet JOIN ============================
def test_cross_sheet_join(monkeypatch):
    rep = _build_multi_sheet_rep(
        "wb1", 153, "book.xlsx",
        [
            ("SheetA", [("sku", "text", ""), ("qty", "integer", "")],
             [{"sku": "A", "qty": 10}, {"sku": "B", "qty": 20}]),
            ("SheetB", [("sku", "text", ""), ("price", "double", "")],
             [{"sku": "A", "price": 5}, {"sku": "B", "price": 8}]),
        ],
    )
    tq = _isolated_tq(rep)
    ta, tb = _tabs(tq, "wb1")
    sql = (
        f'SELECT a."sku", a."qty", b."price" '
        f'FROM "{ta}" a JOIN "{tb}" b ON a."sku" = b."sku" ORDER BY a."sku"'
    )
    _patch_llm(monkeypatch, sql=sql)
    monkeypatch.setattr(TableQueryService, "ensure_user_tables", lambda self, uid: None)
    calls = _count_execute(monkeypatch)

    res = asyncio.run(tq.run_query(153, "join sku qty price"))
    got = [(r[0], float(r[1]), float(r[2])) for r in res["rows"]]
    assert got == [("A", 10.0, 5.0), ("B", 20.0, 8.0)], got
    # 两张表都属于 allowed，JOIN 正常执行且不触发缓存改写
    assert calls["n"] == 1, calls
    # sources 必须能正确识别两个表（不同 Sheet）
    src_sheets = {s["sheet_name"] for s in res["sources"]}
    assert {"SheetA", "SheetB"} <= src_sheets, src_sheets
    # cache key 必须区分这个多表 scope（后续 F 进一步验证）


# ============================ B. UNION（去重） ============================
def test_cross_sheet_union_dedup(monkeypatch):
    rep = _build_multi_sheet_rep(
        "wb2", 153, "book.xlsx",
        [
            ("SheetA", [("sku", "text", "")], [{"sku": "A"}, {"sku": "B"}]),
            ("SheetB", [("sku", "text", "")], [{"sku": "B"}, {"sku": "C"}]),
        ],
    )
    tq = _isolated_tq(rep)
    ta, tb = _tabs(tq, "wb2")
    sql = f'SELECT "sku" FROM "{ta}" UNION SELECT "sku" FROM "{tb}" ORDER BY "sku"'
    _patch_llm(monkeypatch, sql=sql)
    monkeypatch.setattr(TableQueryService, "ensure_user_tables", lambda self, uid: None)

    res = asyncio.run(tq.run_query(153, "union sku"))
    # UNION 去重：A,B,C（B 只出现一次）
    assert res["rows"] == [["A"], ["B"], ["C"]], res["rows"]
    assert len(res["rows"]) == 3


# ============================ C. UNION ALL（保留全部行） ============================
def test_cross_sheet_union_all_keeps_rows(monkeypatch):
    rep = _build_multi_sheet_rep(
        "wb3", 153, "book.xlsx",
        [
            ("SheetA", [("sku", "text", "")], [{"sku": "A"}, {"sku": "B"}]),
            ("SheetB", [("sku", "text", "")], [{"sku": "B"}, {"sku": "C"}]),
        ],
    )
    tq = _isolated_tq(rep)
    ta, tb = _tabs(tq, "wb3")
    sql = f'SELECT "sku" FROM "{ta}" UNION ALL SELECT "sku" FROM "{tb}" ORDER BY "sku"'
    _patch_llm(monkeypatch, sql=sql)
    monkeypatch.setattr(TableQueryService, "ensure_user_tables", lambda self, uid: None)

    res = asyncio.run(tq.run_query(153, "union all sku"))
    # UNION ALL 保留全部行：A,B,B,C（B 出现两次）
    assert res["rows"] == [["A"], ["B"], ["B"], ["C"]], res["rows"]
    assert len(res["rows"]) == 4


# ============================ D. 越权表：部分允许 + 部分未授权 -> 整体拒绝 ============================
def test_unauthorized_table_in_cross_query_rejected(monkeypatch):
    rep = _build_multi_sheet_rep(
        "wb4", 153, "book.xlsx",
        [
            ("SheetA", [("sku", "text", "")], [{"sku": "A"}, {"sku": "B"}]),
            ("SheetB", [("sku", "text", "")], [{"sku": "C"}]),
        ],
    )
    tq = _isolated_tq(rep)  # 注册为 user 153
    ta, tb = _tabs(tq, "wb4")
    # 注入一张属于其他用户/文档的表（不属于 153 的白名单）
    rep_other = _build_rep("otherdoc", 999, "x.xlsx", [("sku", "text", "")],
                           [{"sku": "Z"}])
    tq.engine.register_representation(rep_other, 999)
    tx = _tables_of_doc(tq.engine, "otherdoc")[0]

    sql = f'SELECT "sku" FROM "{ta}" UNION ALL SELECT "sku" FROM "{tx}"'
    _patch_llm(monkeypatch, sql=sql)
    monkeypatch.setattr(TableQueryService, "ensure_user_tables", lambda self, uid: None)

    # AST validate_sql 必须整体拒绝，绝不部分执行
    with pytest.raises(ValueError):
        asyncio.run(tq.run_query(153, "leak attempt"))
    # 直接断言 AST 校验也能单独拦下
    allowed = _tabs(tq, "wb4")
    with pytest.raises(ValueError):
        validate_sql(sql, allowed)


# ============================ E. 跨用户表隔离 ============================
def test_user_isolation_cross_table(monkeypatch):
    rep_a = _build_multi_sheet_rep(
        "wbA", 1001, "a.xlsx",
        [
            ("SheetA", [("sku", "text", ""), ("qty", "integer", "")],
             [{"sku": "A", "qty": 10}, {"sku": "B", "qty": 20}]),
            ("SheetB", [("sku", "text", ""), ("price", "double", "")],
             [{"sku": "A", "price": 5}, {"sku": "B", "price": 8}]),
        ],
    )
    rep_c = _build_rep("wbC", 2002, "c.xlsx", [("sku", "text", "")],
                       [{"sku": "X"}, {"sku": "Y"}])
    tq_a = _isolated_tq(rep_a, user_id=1001)
    tq_c = _isolated_tq(rep_c, user_id=2002)
    ta, tb = _tabs(tq_a, "wbA")
    tc = _tabs(tq_c, "wbC")[0]

    # 用户 1001 的合法跨表 JOIN 成功
    sql_ok = (
        f'SELECT a."sku", a."qty", b."price" '
        f'FROM "{ta}" a JOIN "{tb}" b ON a."sku" = b."sku" ORDER BY a."sku"'
    )
    _patch_llm(monkeypatch, sql=sql_ok)
    monkeypatch.setattr(TableQueryService, "ensure_user_tables", lambda self, uid: None)
    ra = asyncio.run(tq_a.run_query(1001, "join"))
    got = [(r[0], float(r[1]), float(r[2])) for r in ra["rows"]]
    assert got == [("A", 10.0, 5.0), ("B", 20.0, 8.0)], got

    # 用户 1001 试图跨到用户 2002 的表 tc -> 必须拒绝
    sql_bad = f'SELECT * FROM "{ta}" UNION ALL SELECT * FROM "{tc}"'
    _patch_llm(monkeypatch, sql=sql_bad)
    with pytest.raises(ValueError):
        asyncio.run(tq_a.run_query(1001, "cross user leak"))


# ============================ F. Cache：不同 table scope 不共享 ============================
def test_cache_isolation_different_table_scope(monkeypatch):
    # 同用户 153，两个 document：docA 单表；docB 双表
    repA = _build_rep("docA", 153, "a.xlsx", [("sku", "text", "")], [{"sku": "A1"}])
    repB = _build_multi_sheet_rep(
        "docB", 153, "b.xlsx",
        [
            ("SheetA", [("sku", "text", "")], [{"sku": "A1"}]),
            ("SheetB", [("sku", "text", "")], [{"sku": "B1"}]),
        ],
    )
    tq = _isolated_tq(None)
    tq.engine.register_representation(repA, 153)
    tq.engine.register_representation(repB, 153)
    ta_only = _tables_of_doc(tq.engine, "docA")[0]
    tb0, tb1 = _tables_of_doc(tq.engine, "docB")

    _patch_llm(monkeypatch, sql=f'SELECT COUNT(*) AS c FROM "{ta_only}"')
    monkeypatch.setattr(TableQueryService, "ensure_user_tables", lambda self, uid: None)
    calls = _count_execute(monkeypatch)

    # 查询 docA（scope = 单表）与 docB（scope = 双表）使用相同 SQL 模板
    rA1 = asyncio.run(tq.run_query(153, "多少行", document_id="docA"))
    assert rA1["rows"][0][0] == 1
    # 切换到 docB：allowed 范围不同 -> cache key 必不同 -> MISS
    _patch_llm(monkeypatch, sql=f'SELECT COUNT(*) AS c FROM "{tb0}"')
    rB1 = asyncio.run(tq.run_query(153, "多少行", document_id="docB"))
    assert rB1["rows"][0][0] == 1
    # 两次都是 MISS，未跨 scope 命中
    assert calls["n"] == 2, calls

    # 再次查询 docA -> 命中（不重复执行）
    _patch_llm(monkeypatch, sql=f'SELECT COUNT(*) AS c FROM "{ta_only}"')
    asyncio.run(tq.run_query(153, "多少行", document_id="docA"))
    assert calls["n"] == 2, calls


# ============================ G. Cache：修改某表后涉及它的多表查询失效 ============================
def test_cache_invalidation_on_table_update(monkeypatch):
    rep_v1 = _build_multi_sheet_rep(
        "docB", 153, "b.xlsx",
        [
            ("SheetA", [("sku", "text", "")], [{"sku": "A1"}]),
            ("SheetB", [("sku", "text", "")], [{"sku": "B1"}]),
        ],
    )
    tq = _isolated_tq(rep_v1)
    tb0, tb1 = _tabs(tq, "docB")
    sql = f'SELECT "sku" FROM "{tb0}" UNION ALL SELECT "sku" FROM "{tb1}" ORDER BY "sku"'
    _patch_llm(monkeypatch, sql=sql)
    monkeypatch.setattr(TableQueryService, "ensure_user_tables", lambda self, uid: None)
    calls = _count_execute(monkeypatch)

    r1 = asyncio.run(tq.run_query(153, "all sku", document_id="docB"))
    assert r1["rows"] == [["A1"], ["B1"]], r1["rows"]
    assert calls["n"] == 1

    # 更新 docB 的数据（rep_version 改变）
    rep_v2 = _build_multi_sheet_rep(
        "docB", 153, "b.xlsx",
        [
            ("SheetA", [("sku", "text", "")], [{"sku": "A1"}]),
            ("SheetB", [("sku", "text", "")], [{"sku": "B2"}]),  # 由 B1 改为 B2
        ],
    )
    tq.engine.register_representation(rep_v2, 153)
    r2 = asyncio.run(tq.run_query(153, "all sku", document_id="docB"))
    # 必须 MISS 并反映新数据
    assert r2["rows"] == [["A1"], ["B2"]], r2["rows"]
    assert calls["n"] == 2, calls


# ============================ H. CTE + 多表组合 ============================
def test_cte_with_multi_table(monkeypatch):
    rep = _build_multi_sheet_rep(
        "wb5", 153, "book.xlsx",
        [
            ("SheetA", [("sku", "text", ""), ("qty", "integer", "")],
             [{"sku": "A", "qty": 10}, {"sku": "B", "qty": 20}]),
            ("SheetB", [("sku", "text", ""), ("price", "double", "")],
             [{"sku": "A", "price": 5}, {"sku": "B", "price": 8}]),
        ],
    )
    tq = _isolated_tq(rep)
    ta, tb = _tabs(tq, "wb5")
    sql = (
        f'WITH cte AS (SELECT "sku", "qty" FROM "{ta}") '
        f'SELECT c."sku", c."qty", b."price" '
        f'FROM cte c JOIN "{tb}" b ON c."sku" = b."sku" ORDER BY c."sku"'
    )
    _patch_llm(monkeypatch, sql=sql)
    monkeypatch.setattr(TableQueryService, "ensure_user_tables", lambda self, uid: None)

    res = asyncio.run(tq.run_query(153, "cte join"))
    got = [(r[0], float(r[1]), float(r[2])) for r in res["rows"]]
    assert got == [("A", 10.0, 5.0), ("B", 20.0, 8.0)], got


# ============================ I. 全量枚举自动改写使用 UNION ALL（保留重复行） ============================
def test_full_enum_preserves_rows_via_union_all(monkeypatch):
    # 两个结构兼容的 Sheet，数据含重复值：全量列举必须保留全部行
    rep = _build_multi_sheet_rep(
        "wb6", 153, "book.xlsx",
        [
            ("SheetA", [("sku", "text", "")], [{"sku": "A"}, {"sku": "B"}]),
            ("SheetB", [("sku", "text", "")], [{"sku": "B"}, {"sku": "C"}]),
        ],
    )
    tq = _isolated_tq(rep)
    ta = _tabs(tq, "wb6")[0]
    # 模型只显式引用单表 + 业务列 -> crosstable 改写为 UNION ALL（保留重复行）
    _patch_llm(monkeypatch, sql=f'SELECT "sku" FROM "{ta}"')
    monkeypatch.setattr(TableQueryService, "ensure_user_tables", lambda self, uid: None)

    res = asyncio.run(tq.run_query(153, "所有 SKU"))
    # 不去重：A, B, B, C（SheetB 的 B 与 SheetA 的 B 都保留）
    assert res["rows"] == [["A"], ["B"], ["B"], ["C"]], res["rows"]
    assert len(res["rows"]) == 4
