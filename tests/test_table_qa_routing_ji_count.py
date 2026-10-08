"""Unified Table QA 路由补强测试：中文自然计数表述「几 + 量词」。

仅覆盖本次路由修复（不修改 retrieval / SQL / representation）：
- 「几单 / 几条 / 几个 / 几笔」等计数表达必须进入 PRECISE_QUERY（DuckDB）。
- 不得误伤 SEMANTIC（是什么意思）与 STRUCTURED_ACCESS（全部 SKU）。
- 「几个意思」「这是什么」等不含真实计数的问题不得被新规则误判。

另含：用户 153 真实数据的 Phase 2 执行链最小回归（mock LLM，不调 Provider）。
"""
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock

from backend.services.table_qa_service import TableQAService, Intent
from backend.services.table_query_service import TableQueryService


def _svc():
    # classify() 不依赖 self，跳过 __init__（避免触发 Chroma 初始化），保持单测轻量。
    return TableQAService.__new__(TableQAService)


# ---------------- 1) 几 + 量词 必须进入 PRECISE_QUERY ----------------
def test_ji_dan_logistics_sf():
    assert _svc().classify("物流商为SF的有几单？").intent == Intent.PRECISE_QUERY


def test_ji_tiao_logistics_records():
    assert _svc().classify("有几条物流记录？").intent == Intent.PRECISE_QUERY


def test_ji_ge_orders_total():
    assert _svc().classify("一共有几个订单？").intent == Intent.PRECISE_QUERY


def test_ji_bi_orders():
    assert _svc().classify("有几笔订单？").intent == Intent.PRECISE_QUERY


# ---------------- 2) 边界：不得误伤 semantic / structured ----------------
def test_semantic_meaning_not_affected():
    assert _svc().classify("Seller SKU 是什么意思？").intent == Intent.SEMANTIC_RETRIEVAL


def test_what_is_this_not_misrouted_by_ji_rule():
    # 「这是什么」不含「几 + 量词」，新规则不得改变其判定（保持非 PRECISE）。
    dec = _svc().classify("这是什么？")
    assert dec.intent != Intent.PRECISE_QUERY


def test_ji_ge_yi_si_excluded_from_count():
    # 「几个意思」是语义问题，负向 lookahead 必须排除，不得进入 PRECISE。
    dec = _svc().classify("你几个意思？")
    assert dec.intent != Intent.PRECISE_QUERY


def test_full_sku_still_structured():
    assert _svc().classify("全部 SKU").intent == Intent.STRUCTURED_ACCESS


# ---------------- 3) 正式计划问题回归（仅 route） ----------------
def test_formal_plan_route_matrix():
    cases = [
        ("这个表主要记录什么？", Intent.SEMANTIC_RETRIEVAL),
        ("金眼这个产品相关的数据在哪里？", Intent.SEMANTIC_RETRIEVAL),
        ("订单总数是多少？", Intent.PRECISE_QUERY),
        ("运费金额总和是多少？", Intent.PRECISE_QUERY),
        ("销量最高的 10 个 SKU？", Intent.PRECISE_QUERY),
        ("物流商为 SF 的有几单？", Intent.PRECISE_QUERY),
    ]
    for q, exp in cases:
        assert _svc().classify(q).intent == exp, q


# ---------------- 4) 真实数据最小回归：进入 Phase 2 DuckDB 路径 ----------------
def test_ji_count_enters_phase2_duckdb():
    svc = TableQAService.__new__(TableQAService)
    svc.tq = TableQueryService()
    svc.tq.ensure_user_tables(153)
    # 选“Quantity”为数值类型的 153 表（类型干净），构造 COUNT + WHERE。
    tbl = next(t for t, m in svc.tq.engine.table_meta.items()
               if m.get("user_id") == "153"
               and any(c["name"] == "Quantity" and c["type"] != "VARCHAR"
                       for c in m.get("columns", [])))
    cols = [c["name"] for c in svc.tq.engine.table_meta[tbl].get("columns", [])]
    prov_col = next((c for c in cols if "Shipping Provider" in c), cols[0])

    async def fake_nl2sql(self, q, schema, alias, allowed):
        return f'SELECT COUNT(*) AS cnt FROM "{tbl}" WHERE "{prov_col}" = \'SF\''

    async def fake_explain(self, *a, **k):
        return "（解释）"

    async def _run():
        with patch.object(TableQueryService, "_nl2sql", fake_nl2sql), \
             patch.object(TableQueryService, "_explain_text", fake_explain):
            return await svc.run(153, "物流商为SF的有几单？")

    res = asyncio.run(_run())
    assert res["route"] == "PRECISE_QUERY"
    assert "WHERE" in (res.get("sql") or "").upper()
    assert "COUNT" in (res.get("sql") or "").upper()
    # 确认并非走 semantic / structured（Phase1 返回无 sql 字段）
    assert res.get("sql") is not None


# ---------------- 5) 委托链：run() 对几计数问题调用 Phase 2 ----------------
def test_ji_count_delegates_to_run_query():
    svc = TableQAService.__new__(TableQAService)
    svc.tq = MagicMock()
    svc.tq.run_query = AsyncMock(return_value={
        "sql": "SELECT COUNT(*) FROM t", "columns": ["cnt"], "rows": [[0]],
        "match_mode": "exact", "sources": [], "explanation": "", "result_limited": False,
    })

    async def _run():
        return await svc.run(153, "物流商为SF的有几单？")

    res = asyncio.run(_run())
    assert res["route"] == "PRECISE_QUERY"
    svc.tq.run_query.assert_awaited_once()
