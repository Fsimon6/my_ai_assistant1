"""Unified Table QA 路由回归：排序 / Top-N / 极值 必须走 PRECISE_QUERY。

仅覆盖本次路由修复（不修改 retrieval / SQL / representation / DuckDB 类型）：
- 明确排序 / Top-N / 极值（排序 / 降序 / 升序 / 从高到低 / 从低到高 / 最高 / 最低 /
  最大 / 最小 / 排名前 / TopN）必须进入 PRECISE_QUERY（DuckDB 数值排序），
  不得被 _detect_structured_intent 误判成 STRUCTURED_ACCESS。
- 纯全量枚举（全部 SKU / 列出所有 SKU / 列出某列）继续 STRUCTURED_ACCESS，不去重、不走 LIMIT。
- 聚合 / 计数（总和 / 平均 / 几单）继续 PRECISE_QUERY。
- 语义含义 / 定位（是什么意思 / 在哪里）继续 SEMANTIC_RETRIEVAL。

另含：通过正式 POST /api/v1/table-qa/query 入口的集成回归（mock LLM，不调 Provider）。
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.services.table_qa_service import TableQAService, Intent
from backend.services.table_query_service import TableQueryService
from backend.api.v1 import table_qa as tq_mod


DOC5 = "240b6829ec8b4444b63f3054db698d6c"


def _svc():
    # classify() 不依赖 self，跳过 __init__（避免触发 Chroma 初始化），保持单测轻量。
    return TableQAService.__new__(TableQAService)


# ---------------- 1) 排序 / Top-N / 极值 必须进入 PRECISE_QUERY ----------------
def test_sort_desc_top10_orders():
    assert _svc().classify("按 Quantity 从高到低排列前 10 条订单。").intent == Intent.PRECISE_QUERY


def test_sort_asc_top10_orders():
    assert _svc().classify("按 Quantity 从低到高取前 10 条订单。").intent == Intent.PRECISE_QUERY


def test_top_sales_sku():
    assert _svc().classify("销量最高的 10 个 SKU。").intent == Intent.PRECISE_QUERY


def test_top_shipping_fee_orders():
    assert _svc().classify("Original Shipping Fee 最高的 10 笔订单。").intent == Intent.PRECISE_QUERY


def test_sort_desc_by_fee():
    assert _svc().classify("按运费降序排列。").intent == Intent.PRECISE_QUERY


# ---------------- 2) 纯全量枚举继续 STRUCTURED_ACCESS ----------------
def test_full_sku_still_structured():
    assert _svc().classify("把这个表里的全部 SKU 提取出来。").intent == Intent.STRUCTURED_ACCESS


def test_list_all_sku_structured():
    assert _svc().classify("列出这个表所有 SKU。").intent == Intent.STRUCTURED_ACCESS


def test_list_all_shipping_provider_structured():
    assert _svc().classify("把所有 Shipping Provider Name 列出来。").intent == Intent.STRUCTURED_ACCESS


# ---------------- 3) 聚合 / 计数继续 PRECISE_QUERY ----------------
def test_sum_fee_precise():
    assert _svc().classify("Original Shipping Fee 总和是多少？").intent == Intent.PRECISE_QUERY


def test_avg_quantity_precise():
    assert _svc().classify("Quantity 平均是多少？").intent == Intent.PRECISE_QUERY


def test_sf_count_precise():
    assert _svc().classify("物流商为 SF 的有几单？").intent == Intent.PRECISE_QUERY


# ---------------- 4) 语义继续 SEMANTIC_RETRIEVAL ----------------
def test_semantic_meaning():
    assert _svc().classify("Seller SKU 是什么意思？").intent == Intent.SEMANTIC_RETRIEVAL


def test_semantic_locate():
    assert _svc().classify("金眼这个产品相关的数据在哪里？").intent == Intent.SEMANTIC_RETRIEVAL


# ---------------- 4b) 边界：无显式排序方向的枚举不得误判为 PRECISE ----------------
def test_original_order_enumeration_not_precise():
    # “按原顺序排列出来”不含排序方向词，新增排序规则不得误判为 PRECISE_QUERY
    # （该 phrasing 在既有枚举识别中也不是 STRUCTURED，关键是未被错误拉去 PRECISE）。
    assert _svc().classify("把这个表按原顺序排列出来。").intent != Intent.PRECISE_QUERY


def test_list_all_sku_original_order_stays_structured():
    # “列出所有 SKU 并按原顺序展示”仍识别为全量枚举 STRUCTURED_ACCESS（不去重/不走 LIMIT）。
    assert _svc().classify("列出所有 SKU 并按原顺序展示。").intent == Intent.STRUCTURED_ACCESS


# ---------------- 5) 完整路由矩阵（单测，纯 classify） ----------------
def test_full_route_matrix():
    cases = [
        # 排序 / Top-N / 极值 -> PRECISE
        ("按 Quantity 从高到低排列前 10 条订单。", Intent.PRECISE_QUERY),
        ("按 Quantity 从低到高取前 10 条订单。", Intent.PRECISE_QUERY),
        ("销量最高的 10 个 SKU。", Intent.PRECISE_QUERY),
        ("Original Shipping Fee 最高的 10 笔订单。", Intent.PRECISE_QUERY),
        ("按运费降序排列。", Intent.PRECISE_QUERY),
        # 全量枚举 -> STRUCTURED
        ("把这个表里的全部 SKU 提取出来。", Intent.STRUCTURED_ACCESS),
        ("列出这个表所有 SKU。", Intent.STRUCTURED_ACCESS),
        ("把所有 Shipping Provider Name 列出来。", Intent.STRUCTURED_ACCESS),
        # 聚合 / 计数 -> PRECISE
        ("Original Shipping Fee 总和是多少？", Intent.PRECISE_QUERY),
        ("Quantity 平均是多少？", Intent.PRECISE_QUERY),
        ("物流商为 SF 的有几单？", Intent.PRECISE_QUERY),
        # 语义 -> SEMANTIC
        ("Seller SKU 是什么意思？", Intent.SEMANTIC_RETRIEVAL),
        ("金眼这个产品相关的数据在哪里？", Intent.SEMANTIC_RETRIEVAL),
    ]
    for q, exp in cases:
        dec = _svc().classify(q)
        assert dec.intent == exp, f"{q!r} -> {dec.intent.value} (期望 {exp.value})"


# ---------------- 6) 正式 API 集成回归（mock LLM，不调 Provider） ----------------
def _build_client():
    async def fake_user():
        return SimpleNamespace(id=153, email="u153@test", is_active=True)

    app = FastAPI()
    app.include_router(tq_mod.router)
    app.dependency_overrides[tq_mod.get_current_active_user] = fake_user
    svc = tq_mod._service
    svc.tq.ensure_user_tables(153)
    eng = svc.tq.engine
    t5 = next(t for t, m in eng.table_meta.items() if "5店" in m["filename"])
    return TestClient(app), t5


def test_api_sort_query_routes_precise_and_executes_numeric_order():
    client, t5 = _build_client()

    async def fake_nl2sql(self, q, schema, alias, allowed):
        return f'SELECT "SKU ID","Quantity" FROM "{t5}" ORDER BY "Quantity" DESC LIMIT 10'

    async def fake_explain(self, *a, **k):
        return "（解释）"

    with patch.object(TableQueryService, "_nl2sql", fake_nl2sql), \
         patch.object(TableQueryService, "_explain_text", fake_explain):
        r = client.post("/api/v1/table-qa/query",
                        json={"query": "按 Quantity 从高到低排列前 10 条订单。",
                              "document_id": DOC5, "stream": False})
    assert r.status_code == 200
    j = r.json()
    assert j["route"] == "PRECISE_QUERY", j
    sql = (j.get("sql") or "").upper()
    assert "ORDER BY" in sql, sql
    assert "DESC" in sql, sql
    assert "LIMIT 10" in sql, sql
    rows = j.get("rows") or []
    assert rows, "PRECISE 排序查询应返回行"
    # 数值降序：Quantity 列（第 2 列）单调不增（DOUBLE 列保证数值排序）
    qtys = [row[1] for row in rows]
    assert all(qtys[i] >= qtys[i + 1] for i in range(len(qtys) - 1)), qtys


def test_api_full_sku_routes_structured():
    client, _ = _build_client()
    r = client.post("/api/v1/table-qa/query",
                    json={"query": "把这个表里的全部 SKU 提取出来。",
                          "document_id": DOC5, "stream": False})
    assert r.status_code == 200
    j = r.json()
    # 路由在检索之前即确定，不受 Chroma 检索是否成功影响
    assert j["route"] == "STRUCTURED_ACCESS", j
    # 若 Chroma 检索成功，应保留全量 447 行（不去重、不走 top-k）
    if j.get("error_type") is None:
        assert "447" in (j.get("answer") or ""), j.get("answer")
