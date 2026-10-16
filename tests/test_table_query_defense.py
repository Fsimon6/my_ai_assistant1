# -*- coding: utf-8 -*-
"""Phase 2.2 第四项：Table Query 安全纵深加固 + 测试补全。

本轮聚焦：
1. 验证 _resolve_sql_values 修改后的 SQL 会再次经过 AST 校验（纵深防御窗口闭环）；
2. 证明即使 value-resolution 意外产生非法 SQL 结构，最终执行边界仍存在；
3. 合法字符串值（含危险字眼 / 单引号）不被二次校验误拒；
4. 补充日期 / 时间戳 / 布尔类型查询覆盖；
5. 补充 SQL_MAX_RESULT_ROWS 截断边界 + cache 一致性。

【真实执行】DuckDB 真实建表/执行、validate_sql AST、cache。
【mock】NL2SQL / 解释文本；部分用例 monkeypatch _resolve_sql_values 制造非法结构。

注：doc_id 统一使用全小写（生产环境 doc_id 为小写 hex/uuid，与 table_meta
键、_parse_referenced_tables 的 .lower() 口径一致）。
"""
import asyncio

import pytest

from backend.config.settings import settings
from backend.services.cache_service import get_cache_service
from backend.services import table_query_service as tq_mod
from backend.services.table_query_service import (
    TableQueryService,
    DuckDBTableEngine,
    validate_sql,
)


# ----------------------------- 隔离 helper（复用 cross_sheet 模式） -----------------------------
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


def _isolated_tq(rep, user_id=153):
    tq = TableQueryService()
    engine = DuckDBTableEngine()
    tq.engine = engine
    if rep is not None:
        engine.register_representation(rep, user_id)
    return tq


def _tabs(tq, doc_id):
    return [t for t, m in tq.engine.table_meta.items() if m.get("document_id") == doc_id]


def _patch_llm(monkeypatch, sql=None, explain="（解释）"):
    async def fake_nl2sql(self, question, schema, alias, allowed):
        return sql
    async def fake_explain(self, *args, **kwargs):
        return explain
    monkeypatch.setattr(TableQueryService, "_nl2sql", fake_nl2sql)
    monkeypatch.setattr(TableQueryService, "_explain_text", fake_explain)


@pytest.fixture(autouse=True)
def _clear_cache():
    get_cache_service()._cache.clear()
    yield


# ============================ A. resolve 后再次 validate（调用顺序） ============================
def test_pipeline_revalidates_after_value_resolution(monkeypatch):
    rep = _build_rep("docv", 153, "v.xlsx", [("city", "text", "")],
                     [{"city": "SF"}, {"city": "NY"}])
    tq = _isolated_tq(rep)
    t = _tabs(tq, "docv")[0]

    calls = []
    real_validate = tq_mod.validate_sql

    def traced(sql, allowed):
        calls.append(sql)
        return real_validate(sql, allowed)

    monkeypatch.setattr(tq_mod, "validate_sql", traced)

    resolve_calls = {"n": 0}
    real_resolve = TableQueryService._resolve_sql_values

    def traced_resolve(self, sql, ref_tables):
        resolve_calls["n"] += 1
        return real_resolve(self, sql, ref_tables)

    monkeypatch.setattr(TableQueryService, "_resolve_sql_values", traced_resolve)

    async def fake_nl2sql(self, q, schema, alias, allowed):
        return f'SELECT COUNT(*) AS c FROM "{t}" WHERE "city" = \'sf\''

    async def fake_explain(self, *a, **k):
        return "x"

    monkeypatch.setattr(TableQueryService, "_nl2sql", fake_nl2sql)
    monkeypatch.setattr(TableQueryService, "_explain_text", fake_explain)
    monkeypatch.setattr(TableQueryService, "ensure_user_tables", lambda self, uid: None)

    res = asyncio.run(tq.run_query(153, "city sf"))
    # resolve 发生一次
    assert resolve_calls["n"] == 1
    # 第一次 validate(nl2sql 原 SQL) + 第二次 validate(resolve 后 SQL)
    assert len(calls) == 2, calls
    assert "sf" in calls[0], calls[0]
    assert "SF" in calls[1], calls[1]
    # 结果经 value resolution 正确（SF 1 行）
    assert res["rows"][0][0] == 1, res


# ============================ B. resolve 产生非法 SQL 被第二次 validate 拒绝 ============================
def test_resolved_sql_rejected_by_revalidation(monkeypatch):
    rep = _build_rep("docm", 153, "m.xlsx", [("city", "text", "")], [{"city": "SF"}])
    tq = _isolated_tq(rep)
    t = _tabs(tq, "docm")[0]

    async def fake_nl2sql(self, q, schema, alias, allowed):
        return f'SELECT COUNT(*) AS c FROM "{t}" WHERE "city" = \'sf\''

    monkeypatch.setattr(TableQueryService, "_nl2sql", fake_nl2sql)
    monkeypatch.setattr(TableQueryService, "ensure_user_tables", lambda self, uid: None)

    # 模拟未来 value-resolution 意外产生越权 SQL 结构
    def malicious_resolve(self, sql, ref_tables):
        return f'SELECT * FROM "{t}" UNION ALL SELECT * FROM "unauthorized_table"'

    monkeypatch.setattr(TableQueryService, "_resolve_sql_values", malicious_resolve)

    with pytest.raises(ValueError):
        asyncio.run(tq.run_query(153, "evil"))


# ============================ C. 合法字符串值不误拒（危险字眼 / 单引号） ============================
def test_legal_string_values_not_rejected(monkeypatch):
    rep = _build_rep("docs", 153, "s.xlsx", [("note", "text", "")], [
        {"note": "DROP TABLE users"},
        {"note": "UNION SELECT secret"},
        {"note": "O'Brien"},
    ])
    tq = _isolated_tq(rep)
    t = _tabs(tq, "docs")[0]
    monkeypatch.setattr(TableQueryService, "ensure_user_tables", lambda self, uid: None)

    # 危险字眼作为字面量值 -> 二次校验不误拒
    _patch_llm(monkeypatch,
               sql=f'SELECT COUNT(*) AS c FROM "{t}" WHERE "note" = \'DROP TABLE users\'')
    r1 = asyncio.run(tq.run_query(153, "drop"))
    assert r1["rows"][0][0] == 1, r1

    # 单引号转义：O'Brien -> 'O''Brien'
    _patch_llm(monkeypatch,
               sql=f'SELECT COUNT(*) AS c FROM "{t}" WHERE "note" = \'O\'\'Brien\'')
    r2 = asyncio.run(tq.run_query(153, "obrien"))
    assert r2["rows"][0][0] == 1, r2


# ============================ D. 日期 / 时间戳 / 布尔类型 ============================
def test_date_timestamp_boolean_columns(monkeypatch):
    rep = _build_rep("docd", 153, "d.xlsx", [
        ("dt", "date", ""),
        ("ts", "date", ""),
    ], [
        {"dt": "2026-01-01", "ts": "2026-01-01 09:00:00"},
        {"dt": "2026-03-15", "ts": "2026-03-15 18:30:00"},
        {"dt": "2026-06-20", "ts": "2026-06-20 12:00:00"},
    ])
    tq = _isolated_tq(rep)
    t = _tabs(tq, "docd")[0]
    types = {c["name"]: c["type"] for c in tq.engine.table_meta[t]["columns"]}
    # date dtype -> TIMESTAMP
    assert types["dt"] == "TIMESTAMP" and types["ts"] == "TIMESTAMP", types
    monkeypatch.setattr(TableQueryService, "ensure_user_tables", lambda self, uid: None)

    # 日期过滤（>=）
    _patch_llm(monkeypatch,
               sql=f'SELECT COUNT(*) AS c FROM "{t}" WHERE "dt" >= \'2026-02-01\'')
    r1 = asyncio.run(tq.run_query(153, "date filter"))
    assert r1["rows"][0][0] == 2, r1

    # 时间戳 ORDER BY（验证 _json_safe 序列化）
    _patch_llm(monkeypatch, sql=f'SELECT "ts" FROM "{t}" ORDER BY "ts"')
    r2 = asyncio.run(tq.run_query(153, "ts order"))
    assert r2["rows"][0][0] == "2026-01-01T09:00:00", r2["rows"][0][0]

    # 布尔类型：当前 Representation 将 boolean 归类为 DOUBLE，此处验证 DuckDB 原生
    # BOOLEAN 在查询引擎中的正确性与 _json_safe 序列化（为未来支持铺设）。
    tq.engine.con.execute("CREATE TABLE t_bool (flag BOOLEAN)")
    tq.engine.con.execute("INSERT INTO t_bool VALUES (TRUE),(FALSE),(TRUE)")
    _, rows_t, _ = tq._execute_sql("SELECT flag FROM t_bool WHERE flag = TRUE", cap_rows=True)
    assert rows_t == [(True,), (True,)], rows_t
    _, rows_f, _ = tq._execute_sql("SELECT flag FROM t_bool WHERE flag = FALSE", cap_rows=True)
    assert rows_f == [(False,)], rows_f
    _, rows_c, _ = tq._execute_sql("SELECT COUNT(*) AS c FROM t_bool", cap_rows=True)
    assert rows_c[0][0] == 3, rows_c


# ============================ F. SQL_MAX_RESULT_ROWS 截断边界 ============================
def test_result_truncation_boundary(monkeypatch):
    tq = _isolated_tq(None)
    lim = settings.SQL_MAX_RESULT_ROWS  # 2000，哨兵 2001

    _, rows, truncated = tq._execute_sql(f"SELECT i FROM range({lim - 1}) t(i)", cap_rows=True)
    assert len(rows) == lim - 1 and truncated is False, (len(rows), truncated)

    _, rows, truncated = tq._execute_sql(f"SELECT i FROM range({lim}) t(i)", cap_rows=True)
    assert len(rows) == lim and truncated is False, (len(rows), truncated)

    _, rows, truncated = tq._execute_sql(f"SELECT i FROM range({lim + 1}) t(i)", cap_rows=True)
    assert truncated is True, (len(rows), truncated)
    assert len(rows) == lim, (len(rows), lim)


def test_truncation_preserved_across_cache(monkeypatch):
    # 用临时小 limit（不创建巨大数据），构造真实 11 行表触发截断（range 生成器会绕过
    # validate 的“物理表引用”检查，故此处用真实表）。
    lim = 10
    monkeypatch.setattr(settings, "SQL_MAX_RESULT_ROWS", lim)
    rows_data = [{"n": i} for i in range(lim + 1)]
    rep = _build_rep("doct", 153, "t.xlsx", [("n", "integer", "")], rows_data)
    tq = _isolated_tq(rep)
    t = _tabs(tq, "doct")[0]

    async def fake_nl2sql(self, q, schema, alias, allowed):
        return f'SELECT "n" FROM "{t}"'
    async def fake_explain(self, *a, **k):
        return "x"

    monkeypatch.setattr(TableQueryService, "_nl2sql", fake_nl2sql)
    monkeypatch.setattr(TableQueryService, "_explain_text", fake_explain)
    monkeypatch.setattr(TableQueryService, "ensure_user_tables", lambda self, uid: None)
    get_cache_service()._cache.clear()

    r1 = asyncio.run(tq.run_query(153, "big"))
    assert r1["result_limited"] is True
    assert len(r1["rows"]) == lim
    # HIT：结果一致
    r2 = asyncio.run(tq.run_query(153, "big"))
    assert r2["result_limited"] is True
    assert len(r2["rows"]) == lim
