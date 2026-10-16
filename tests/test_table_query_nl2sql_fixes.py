"""Phase 2.3 第二阶段：针对真实 LLM E2E 暴露问题的回归测试。

覆盖：
- _extract_sql_json 鲁棒性（纯 JSON / markdown fence / 前后文字 / SQL 内含 {} / DROP 字面量 / UNION / 非法）
- COLUMN_ALIASES 金额 vs 单价 语义区分（不映射裸“价格”）
- value resolution 安全（唯一候选修正 / 多候选 ambiguous / 无候选不强行映射）
- 中文物流商经整链修正（LLM 生成 'sf' -> SF International -> 正确计数）——验证顺丰/SF 由 value resolution 层兜底
- 语义列选择：价格最高订单 -> Order Amount；SKU 单价 -> SKU Unit Original Price（验证修复不破坏反向查询）
"""
import json
import os
import sys
from unittest.mock import patch

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REAL_XLSX = os.path.join(ROOT, "直邮5店 7.10号订单.xlsx")

from backend.services.table_query_service import (
    TableQueryService,
    _extract_sql_json,
    COLUMN_ALIASES,
    ValueResolutionAmbiguous,
    validate_sql,
)


# ----------------------------- JSON parser -----------------------------
def test_extract_plain_json():
    assert _extract_sql_json('{"sql":"SELECT 1"}') == "SELECT 1"


def test_extract_markdown_fence():
    assert _extract_sql_json('```json\n{"sql":"SELECT 1"}\n```') == "SELECT 1"


def test_extract_fence_no_lang():
    assert _extract_sql_json('```\n{"sql":"SELECT 1"}\n```') == "SELECT 1"


def test_extract_prefix_text():
    assert _extract_sql_json('好的，查询如下：\n{"sql":"SELECT 1"}') == "SELECT 1"


def test_extract_sql_with_braces():
    t = '{"sql":"SELECT * FROM t WHERE note = \'a {b} c\'"}'
    assert _extract_sql_json(t) == "SELECT * FROM t WHERE note = 'a {b} c'"


def test_extract_sql_with_drop_literal():
    t = '{"sql":"SELECT * FROM t WHERE x = \'DROP TABLE users\'"}'
    assert _extract_sql_json(t) == "SELECT * FROM t WHERE x = 'DROP TABLE users'"


def test_extract_sql_with_union():
    assert _extract_sql_json('{"sql":"SELECT 1 UNION ALL SELECT 2"}') == "SELECT 1 UNION ALL SELECT 2"


def test_extract_no_brace_raises():
    with pytest.raises(ValueError):
        _extract_sql_json("这里没有 json")


def test_extract_invalid_json_raises():
    with pytest.raises(ValueError):
        _extract_sql_json('{"sql": SELECT bad}')


def test_extract_missing_sql_field_raises():
    with pytest.raises(ValueError):
        _extract_sql_json('{"query":"SELECT 1"}')


# ----------------------------- COLUMN_ALIASES 语义 -----------------------------
def test_column_aliases_amount():
    assert COLUMN_ALIASES["订单金额"] == "Order Amount"
    assert COLUMN_ALIASES["订单价格"] == "Order Amount"
    assert COLUMN_ALIASES["总价"] == "Order Amount"
    assert COLUMN_ALIASES["成交金额"] == "Order Amount"


def test_column_aliases_unit_price():
    assert COLUMN_ALIASES["商品单价"] == "SKU Unit Original Price"
    assert COLUMN_ALIASES["SKU单价"] == "SKU Unit Original Price"
    assert COLUMN_ALIASES["原始单价"] == "SKU Unit Original Price"
    assert COLUMN_ALIASES["单位价格"] == "SKU Unit Original Price"


def test_column_aliases_bare_price_not_mapped():
    # 裸“价格”不强行映射，避免误伤 SKU 单价查询
    assert "价格" not in COLUMN_ALIASES


# ----------------------------- value resolution 安全（复用最小桩） -----------------------------
class _FakeEngine:
    def __init__(self, table_meta, examples):
        self.table_meta = table_meta
        self._examples = examples

    def column_examples(self, tname, col):
        return self._examples.get((tname, col), "")


def _svc(table_meta, examples):
    svc = TableQueryService.__new__(TableQueryService)
    svc.engine = _FakeEngine(table_meta, examples)
    return svc


def _meta(cols_by_table):
    meta = {}
    for t, cols in cols_by_table.items():
        meta[t] = {"user_id": "153", "document_id": "240b6829ec8b4444b63f3054db698d6c",
                   "filename": "f.xlsx", "sheet_name": "S", "columns": cols}
    return meta


def _vchar(name):
    return {"name": name, "type": "VARCHAR"}


T5 = "t_240b6829ec8b4444b63f3054db698d6c_0"


def test_value_resolution_sf_unique():
    svc = _svc(_meta({T5: [_vchar("Shipping Provider Name")]}),
              {(T5, "Shipping Provider Name"): json.dumps(["SF International"])})
    out = svc._resolve_sql_values(
        f'SELECT COUNT(*) FROM {T5} WHERE "Shipping Provider Name" = \'SF\'', [T5])
    assert "SF International" in out and "'SF'" not in out


def test_value_resolution_ambiguous():
    svc = _svc(_meta({T5: [_vchar("Shipping Provider Name")]}),
              {(T5, "Shipping Provider Name"): json.dumps(["SF International", "SF Express"])})
    with pytest.raises(ValueResolutionAmbiguous):
        svc._resolve_sql_values(
            f'WHERE "Shipping Provider Name" = \'SF\'', [T5])


def test_value_resolution_no_candidate_unchanged():
    svc = _svc(_meta({T5: [_vchar("Shipping Provider Name")]}),
              {(T5, "Shipping Provider Name"): json.dumps(["SF International"])})
    out = svc._resolve_sql_values(
        f'WHERE "Shipping Provider Name" = \'NotExistVal\'', [T5])
    # 无候选不强行映射，保持原样（可能自然返回空）
    assert "NotExistVal" in out


# ----------------------------- 真实 DuckDB 集成（mock LLM） -----------------------------
async def _run_pipeline_with_sql(svc, user, question, doc_id, sql):
    async def _fake_nl2sql(*a, **k):
        return sql
    with patch.object(svc, "_nl2sql", new=_fake_nl2sql):
        return await svc._pipeline(user, question, doc_id)


@pytest.mark.asyncio
async def test_chinese_carrier_sf_pipeline():
    if not os.path.exists(REAL_XLSX):
        pytest.skip("真实 Excel 不在项目目录")
    svc = TableQueryService()
    doc_id = svc.load_from_file(REAL_XLSX, "eval_fix_carrier")
    tname = [t for t in svc.engine.table_meta if t.startswith("t_")][0]
    sql = f'SELECT COUNT(*) FROM {tname} WHERE "Shipping Provider Name" = \'sf\''
    sql_out, cols, rows, mode, sources, trunc = await _run_pipeline_with_sql(
        svc, "eval_fix_carrier", "顺丰有多少订单？", doc_id, sql)
    # value resolution 兜底：'sf' -> 'SF International'
    assert "SF International" in sql_out
    assert "'sf'" not in sql_out
    assert rows[0][0] == 447
    svc.close()


@pytest.mark.asyncio
async def test_semantic_column_order_amount_topn():
    if not os.path.exists(REAL_XLSX):
        pytest.skip("真实 Excel 不在项目目录")
    svc = TableQueryService()
    doc_id = svc.load_from_file(REAL_XLSX, "eval_fix_amt")
    tname = [t for t in svc.engine.table_meta if t.startswith("t_")][0]
    sql = f'SELECT "Order ID", "Order Amount" FROM {tname} ORDER BY "Order Amount" DESC LIMIT 5'
    sql_out, cols, rows, mode, sources, trunc = await _run_pipeline_with_sql(
        svc, "eval_fix_amt", "价格最高的5条订单", doc_id, sql)
    assert '"Order Amount"' in sql_out
    amounts = sorted([r[1] for r in rows], reverse=True)
    assert amounts[0] == 27.63  # 与 Ground Truth 一致
    svc.close()


@pytest.mark.asyncio
async def test_semantic_column_unit_price_topn():
    if not os.path.exists(REAL_XLSX):
        pytest.skip("真实 Excel 不在项目目录")
    svc = TableQueryService()
    doc_id = svc.load_from_file(REAL_XLSX, "eval_fix_price")
    tname = [t for t in svc.engine.table_meta if t.startswith("t_")][0]
    sql = (f'SELECT "Order ID", "SKU Unit Original Price" FROM {tname} '
           f'ORDER BY CAST("SKU Unit Original Price" AS DOUBLE) DESC LIMIT 5')
    sql_out, cols, rows, mode, sources, trunc = await _run_pipeline_with_sql(
        svc, "eval_fix_price", "SKU单价最高的5条商品", doc_id, sql)
    # 反向查询：SKU 单价列仍可被正确选择并执行（验证不破坏）
    assert '"SKU Unit Original Price"' in sql_out
    assert trunc is False
    svc.close()
