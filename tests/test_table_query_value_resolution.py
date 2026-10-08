"""Unified Table QA 语义值解析（schema-aware, 确定性）回归测试。

仅覆盖本轮新增的等值过滤值解析（不修改 routing / SQL 安全 / DuckDB 类型）：
- 自然语言简称/缩写（SF -> SF International）确定性映射到表中真实枚举值；
- 唯一候选自动改写；多个候选抛 ValueResolutionAmbiguous（上层转 AMBIGUOUS）；
- 无候选不强行映射；保持 user/document/table 隔离（candidate 仅来自引用表）；
- 不解析高基数列（SKU/tracking/order id）、不破坏比较/不等运算符、不改数值过滤。

另含：通过正式 POST /api/v1/table-qa/query 入口的真实 5 店集成回归（mock LLM）。
"""
import json
from unittest.mock import patch

import pytest

from backend.services.table_query_service import (
    TableQueryService,
    ValueResolutionAmbiguous,
)

DOC5 = "240b6829ec8b4444b63f3054db698d6c"
T5 = f"t_{DOC5}_0"


class _FakeEngine:
    """最小引擎桩：仅提供 value resolution 所需的 table_meta / column_examples。"""

    def __init__(self, table_meta, examples):
        self.table_meta = table_meta
        self._examples = examples  # {(tname, col): json_str}

    def column_examples(self, tname, col):
        return self._examples.get((tname, col), "")


def _svc(table_meta, examples):
    svc = TableQueryService.__new__(TableQueryService)
    svc.engine = _FakeEngine(table_meta, examples)
    return svc


def _meta(cols_by_table):
    meta = {}
    for t, cols in cols_by_table.items():
        meta[t] = {
            "user_id": "153", "document_id": DOC5, "filename": "f.xlsx",
            "sheet_name": "S", "columns": cols,
        }
    return meta


def _vchar(name):
    return {"name": name, "type": "VARCHAR"}


# ---------------- 1) 唯一候选：SF -> SF International ----------------
def test_sf_unique_resolution():
    svc = _svc(_meta({T5: [_vchar("Shipping Provider Name")]}),
              {(T5, "Shipping Provider Name"): json.dumps(["SF International"])})
    out = svc._resolve_sql_values(
        f'SELECT COUNT(*) FROM {T5} WHERE "Shipping Provider Name" = \'SF\'', [T5])
    assert "SF International" in out
    assert "'SF'" not in out
    assert "Shipping Provider Name" in out


def test_sf_lowercase_resolution():
    svc = _svc(_meta({T5: [_vchar("Shipping Provider Name")]}),
              {(T5, "Shipping Provider Name"): json.dumps(["SF International"])})
    out = svc._resolve_sql_values(
        f'WHERE "Shipping Provider Name" = \'sf\'', [T5])
    assert "SF International" in out


def test_sf_with_surrounding_spaces():
    svc = _svc(_meta({T5: [_vchar("Shipping Provider Name")]}),
              {(T5, "Shipping Provider Name"): json.dumps(["SF International"])})
    out = svc._resolve_sql_values(
        f'WHERE "Shipping Provider Name" = \'  SF  \'', [T5])
    assert "SF International" in out


def test_sf_in_lower_trim_form():
    svc = _svc(_meta({T5: [_vchar("Shipping Provider Name")]}),
              {(T5, "Shipping Provider Name"): json.dumps(["SF International"])})
    out = svc._resolve_sql_values(
        f'WHERE LOWER(TRIM("Shipping Provider Name")) = LOWER(TRIM(\'SF\'))', [T5])
    assert "SF International" in out


# ---------------- 2) 精确值保持不变 ----------------
def test_exact_value_no_change():
    svc = _svc(_meta({T5: [_vchar("Shipping Provider Name")]}),
              {(T5, "Shipping Provider Name"): json.dumps(["SF International"])})
    sql = f'WHERE "Shipping Provider Name" = \'SF International\''
    assert svc._resolve_sql_values(sql, [T5]) == sql


# ---------------- 3) 无候选：不强行映射 ----------------
def test_no_candidate_keeps_literal():
    svc = _svc(_meta({T5: [_vchar("Shipping Provider Name")]}),
              {(T5, "Shipping Provider Name"): json.dumps(["SF International"])})
    sql = f'WHERE "Shipping Provider Name" = \'FedEx\''
    assert svc._resolve_sql_values(sql, [T5]) == sql


def test_empty_literal_no_error():
    svc = _svc(_meta({T5: [_vchar("Shipping Provider Name")]}),
              {(T5, "Shipping Provider Name"): json.dumps(["SF International"])})
    sql = f'WHERE "Shipping Provider Name" = \'\''
    assert svc._resolve_sql_values(sql, [T5]) == sql


# ---------------- 4) 多候选：抛异常，不擅自猜测 ----------------
def test_multi_candidate_raises():
    svc = _svc(_meta({T5: [_vchar("Shipping Provider Name")]}),
              {(T5, "Shipping Provider Name"): json.dumps(
                  ["SF International", "SF Express", "SF Local"])})
    with pytest.raises(ValueResolutionAmbiguous):
        svc._resolve_sql_values(
            f'WHERE "Shipping Provider Name" = \'SF\'', [T5])


# ---------------- 5) 隔离：candidate 仅来自引用表 ----------------
def test_isolation_only_referenced_tables():
    t_a = "t_aaaa_0"
    t_b = "t_bbbb_0"
    svc = _svc(
        _meta({t_a: [_vchar("Shipping Provider Name")],
               t_b: [_vchar("Shipping Provider Name")]}),
        {(t_a, "Shipping Provider Name"): json.dumps(["SF International"]),
         (t_b, "Shipping Provider Name"): json.dumps(["SF Express"])},
    )
    # 仅引用 t_a -> 唯一候选 SF International
    out = svc._resolve_sql_values(
        f'WHERE "Shipping Provider Name" = \'SF\'', [t_a])
    assert "SF International" in out
    # 同时引用 t_a + t_b -> 多候选 -> 澄清
    with pytest.raises(ValueResolutionAmbiguous):
        svc._resolve_sql_values(
            f'WHERE "Shipping Provider Name" = \'SF\'', [t_a, t_b])


# ---------------- 6) 不解析高基数列 / 不破坏比较运算符 ----------------
def test_high_cardinality_column_skipped():
    svc = _svc(_meta({T5: [_vchar("Tracking ID")]}),
              {(T5, "Tracking ID"): ""})  # 空 -> 视为高基数，跳过
    sql = f'WHERE "Tracking ID" = \'SF\''
    assert svc._resolve_sql_values(sql, [T5]) == sql


def test_comparison_operator_not_mangled():
    svc = _svc(_meta({T5: [_vchar("Shipping Provider Name")]}),
              {(T5, "Shipping Provider Name"): json.dumps(["SF International"])})
    sql = f'WHERE "Quantity" >= 100'
    assert svc._resolve_sql_values(sql, [T5]) == sql


def test_numeric_filter_untouched():
    svc = _svc(_meta({T5: [_vchar("Shipping Provider Name")]}),
              {(T5, "Shipping Provider Name"): json.dumps(["SF International"])})
    sql = f'WHERE "Quantity" > 100'
    assert svc._resolve_sql_values(sql, [T5]) == sql


# ---------------- 7) 正式 API 集成回归（真实 5 店，mock LLM） ----------------
def _build_client():
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api.v1 import table_qa as tq_mod

    async def fake_user():
        return SimpleNamespace(id=153, email="u153@test", is_active=True)

    app = FastAPI()
    app.include_router(tq_mod.router)
    app.dependency_overrides[tq_mod.get_current_active_user] = fake_user
    svc = tq_mod._service
    svc.tq.ensure_user_tables(153)
    return TestClient(app), svc


def test_api_sf_resolves_to_sf_international():
    client, _ = _build_client()

    async def fake_nl2sql(self, q, schema, alias, allowed):
        # 模拟 LLM 直出用户原值 'SF'（已知缩写盲区）
        return f'SELECT COUNT(*) AS cnt FROM "{T5}" WHERE "Shipping Provider Name" = \'SF\''

    async def fake_explain(self, *a, **k):
        return "（解释）"

    with patch.object(
            TableQueryService, "_nl2sql", fake_nl2sql), \
         patch.object(
            TableQueryService, "_explain_text", fake_explain):
        r = client.post("/api/v1/table-qa/query",
                        json={"query": "物流商为 SF 的有几单？",
                              "document_id": DOC5, "stream": False})
    assert r.status_code == 200
    j = r.json()
    assert j["route"] == "PRECISE_QUERY", j
    sql = (j.get("sql") or "")
    assert "SF International" in sql, sql          # 值解析已改写为真实值
    assert "'SF'" not in sql, sql
    # 5 店全部 447 行均为 SF International -> 计数应为 447（不再错误返回 0）
    flat = [c for row in (j.get("rows") or []) for c in row]
    assert 447 in flat, (j.get("rows"), j.get("answer"))


def test_api_sf_international_consistent():
    client, _ = _build_client()

    async def fake_nl2sql(self, q, schema, alias, allowed):
        return (f'SELECT COUNT(*) AS cnt FROM "{T5}" '
                f'WHERE "Shipping Provider Name" = \'SF International\'')

    async def fake_explain(self, *a, **k):
        return "（解释）"

    with patch.object(
            TableQueryService, "_nl2sql", fake_nl2sql), \
         patch.object(
            TableQueryService, "_explain_text", fake_explain):
        r = client.post("/api/v1/table-qa/query",
                        json={"query": "物流商为 SF International 的有几单？",
                              "document_id": DOC5, "stream": False})
    assert r.status_code == 200
    j = r.json()
    assert j["route"] == "PRECISE_QUERY", j
    flat = [c for row in (j.get("rows") or []) for c in row]
    assert 447 in flat, (j.get("rows"), j.get("answer"))
