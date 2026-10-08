# -*- coding: utf-8 -*-
"""Phase 2 安全加固正式测试：SQL 执行超时 + 服务端结果行数上限。

仅覆盖本轮新增的“执行层”安全能力（不影响 NL2SQL 生成 / Routing / Citation / Phase 1）：
- Test A: 普通小 SQL（SELECT 1）正常返回，不被任何安全限制误伤。
- Test B: 聚合 COUNT/SUM 正常返回，不受 max rows 错误拦截。
- Test C: 超过 SQL_MAX_RESULT_ROWS 的 SELECT 被服务端安全限制，且明确标记超限（不静默谎称完整）。
- Test D: SQL 超时能被捕获（watchdog + con.interrupt），不会静默继续，也不会污染下一次 query。
- Test E: Unified Table QA 回归 —— PRECISE_QUERY（运费总和）仍然正确返回 151.41。
- Test F: STRUCTURED_ACCESS 回归 —— “全部 SKU” 仍路由到 Phase 1，且完全不受 SQL max rows 影响。

所有 LLM / Provider 均 mock，不调用真实模型。
"""
import asyncio
from unittest.mock import MagicMock

import pytest

from backend.config.settings import settings
from backend.services.table_query_service import (
    TableQueryService,
    DuckDBTableEngine,
)
from backend.services import table_qa_service as tqa_mod


# ----------------------------- 测试夹具 -----------------------------
def _build_rep(doc_id, user_id, filename, columns, rows):
    """构造 register_representation 所需的精简 Representation。

    columns: [(technical_name, data_type, description), ...]
    rows:    [ {technical_name: value}, ... ]
    """
    col_defs = [
        {"technical_name": n, "data_type": dt, "description": desc, "col_index": i}
        for i, (n, dt, desc) in enumerate(columns)
    ]
    table_rows = []
    for ri, rd in enumerate(rows):
        cells = [{"col_index": i, "value": rd[n]} for i, (n, _, _) in enumerate(columns)]
        table_rows.append({"row_index": ri, "range": f"A{ri + 1}", "cells": cells})
    return {
        "document_id": doc_id,
        "filename": filename,
        "user_id": user_id,
        "workbook": {
            "sheets": [{
                "sheet_name": "Sheet1",
                "tables": [{"columns": col_defs, "rows": table_rows}],
            }]
        },
    }


def _isolated_tq(rep, user_id=153):
    """构造一个使用全新 DuckDB 引擎、且不从磁盘加载的 TableQueryService（隔离、确定性）。"""
    tq = TableQueryService()
    engine = DuckDBTableEngine()  # 全新实例，避免污染进程级单例
    tq.engine = engine
    if rep is not None:
        engine.register_representation(rep, user_id)
    return tq


def _first_table_name(engine: DuckDBTableEngine) -> str:
    return next(iter(engine.table_meta.keys()))


def _patch_llm(monkeypatch, sql=None, explain="（解释）"):
    async def fake_nl2sql(self, question, schema, alias, allowed):
        return sql
    async def fake_explain(self, *args, **kwargs):
        return explain
    monkeypatch.setattr(TableQueryService, "_nl2sql", fake_nl2sql)
    monkeypatch.setattr(TableQueryService, "_explain_text", fake_explain)


# ----------------------------- Test A：普通小 SQL -----------------------------
def test_A_select_one_normal():
    tq = _isolated_tq(None)
    cols, rows, truncated = tq._execute_sql("SELECT 1 AS x", cap_rows=True)
    assert rows == [(1,)]
    assert truncated is False


# ----------------------------- Test B：聚合不受 max rows 影响 -----------------------------
def test_B_aggregate_not_blocked():
    cols_def = [("SKU ID", "text", ""), ("Original Shipping Fee", "numeric", "")]
    # 5 行，运费合计 151.41（与真实业务回归用例一致）
    fee_vals = [10.0, 20.0, 30.0, 40.0, 51.41]
    rows = [{"SKU ID": f"S{i}", "Original Shipping Fee": v} for i, v in enumerate(fee_vals)]
    rep = _build_rep("doc_153", 153, "直邮一店 8.20号订单.xlsx", cols_def, rows)
    tq = _isolated_tq(rep)
    tname = _first_table_name(tq.engine)

    cols, r_count, tr = tq._execute_sql(f'SELECT COUNT(*) AS c FROM "{tname}"', cap_rows=True)
    assert tr is False
    assert r_count[0][0] == 5

    cols, r_sum, tr2 = tq._execute_sql(
        f'SELECT SUM("{ "Original Shipping Fee" }") AS s FROM "{tname}"', cap_rows=True
    )
    assert tr2 is False
    assert abs(r_sum[0][0] - 151.41) < 1e-6


# ----------------------------- Test C：超过 max rows 被安全限制 -----------------------------
def test_C_exceed_max_rows_limited(monkeypatch):
    monkeypatch.setattr(settings, "SQL_MAX_RESULT_ROWS", 10)
    cols_def = [("val", "text", "")]
    rows = [{"val": f"r{i}"} for i in range(100)]  # 100 行 > 10
    rep = _build_rep("doc_big", 153, "big.xlsx", cols_def, rows)
    tq = _isolated_tq(rep)
    tname = _first_table_name(tq.engine)

    # 直接执行层：超限应被标记 truncated=True，且只返回前 10 行（不是全部 100 行）
    cols, r_rows, tr = tq._execute_sql(f'SELECT * FROM "{tname}"', cap_rows=True)
    assert tr is True
    assert len(r_rows) == 10

    # 管线层：result_limited 必须透传到统一返回，绝不谎称完整
    _patch_llm(monkeypatch, sql=f'SELECT * FROM "{tname}"', explain="（部分结果）")
    monkeypatch.setattr(TableQueryService, "ensure_user_tables", lambda self, uid: None)
    res = asyncio.run(tq.run_query(153, "列出所有行"))
    assert res["result_limited"] is True
    assert len(res["rows"]) == 10


# ----------------------------- Test D：SQL 超时捕获 + 不污染 -----------------------------
def test_D_sql_timeout_caught_and_not_polluting(monkeypatch):
    monkeypatch.setattr(settings, "SQL_QUERY_TIMEOUT", 0.3)
    tq = _isolated_tq(None)  # 全新引擎，无需数据

    heavy = (
        "SELECT count(*) FROM "
        "(SELECT range i FROM range(2000000000)) a, "
        "(SELECT range j FROM range(2000000000)) b"
    )
    with pytest.raises(ValueError) as exc:
        tq._execute_sql(heavy, cap_rows=False)
    assert "超时" in str(exc.value)

    # 超时后：同一连接仍可继续复用，不会留下仍在执行的查询，也不会污染下一次 query
    cols, rows, truncated = tq._execute_sql("SELECT 1 AS x", cap_rows=False)
    assert rows == [(1,)]
    assert truncated is False


# ----------------------------- Test E：PRECISE_QUERY 回归 151.41 -----------------------------
def test_E_precise_query_15141_regression(monkeypatch):
    cols_def = [("SKU ID", "text", ""), ("Original Shipping Fee", "numeric", "")]
    fee_vals = [10.0, 20.0, 30.0, 40.0, 51.41]
    rows = [{"SKU ID": f"S{i}", "Original Shipping Fee": v} for i, v in enumerate(fee_vals)]
    rep = _build_rep("doc_153", 153, "直邮一店 8.20号订单.xlsx", cols_def, rows)
    tq = _isolated_tq(rep)
    tname = _first_table_name(tq.engine)

    sql = f'SELECT SUM("Original Shipping Fee") AS total FROM "{tname}"'
    _patch_llm(monkeypatch, sql=sql, explain="运费总和 151.41")
    monkeypatch.setattr(TableQueryService, "ensure_user_tables", lambda self, uid: None)

    res = asyncio.run(tq.run_query(153, "运费总和是多少"))
    # PRECISE_QUERY 路径，结果不被限制
    assert res["result_limited"] is False
    flat = [v for r in res["rows"] for v in r]
    assert any(abs(v - 151.41) < 1e-6 for v in flat if isinstance(v, (int, float)))
    # 来源应指向真实文件/Sheet（不被破坏）
    assert res["sources"][0]["filename"] == "直邮一店 8.20号订单.xlsx"


# ----------------------------- Test F：STRUCTURED_ACCESS 不受 SQL limit 影响 -----------------------------
def test_F_structured_access_untouched_by_sql_limit(monkeypatch):
    # 隔离 Phase 1：mock RAG，返回全部 SKU 原始行（不去重、完整）
    mock_rag = MagicMock()

    async def fake_rag_query(query, user_id, document_id):
        for ch in ["全部", "SKU", "原始行"]:
            yield ch

    async def fake_retrieve_sources(query, user_id, document_id):
        return []

    mock_rag.rag_query = fake_rag_query
    mock_rag.retrieve_sources = fake_retrieve_sources
    monkeypatch.setattr(tqa_mod, "get_rag_service", lambda: mock_rag)

    tqa = tqa_mod.TableQAService()

    # 路由确认：全部 SKU -> STRUCTURED_ACCESS
    dec = tqa.classify("全部 SKU")
    assert dec.intent.value == "STRUCTURED_ACCESS"

    # 执行：Phase 1 路径不引入 result_limited，且答案完整
    res = asyncio.run(tqa.run(153, "全部 SKU"))
    assert res["route"] == "STRUCTURED_ACCESS"
    assert "result_limited" not in res  # Phase 1 路径根本不涉及 SQL max rows
    assert res["answer"] == "全部SKU原始行"

    # 即便把 SQL max rows 调得很小，也不应影响 STRUCTURED_ACCESS 的结果
    monkeypatch.setattr(settings, "SQL_MAX_RESULT_ROWS", 1)
    res2 = asyncio.run(tqa.run(153, "全部 SKU"))
    assert res2["route"] == "STRUCTURED_ACCESS"
    assert res2["answer"] == "全部SKU原始行"
