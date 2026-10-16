# -*- coding: utf-8 -*-
"""Phase 2.2 第四项：API 层真实多 Sheet（跨表）查询验证。

经过真实 HTTP 路由 /api/v1/table-qa/query -> TableQAService -> TableQueryService，
验证同一 Workbook 多个 Sheet（多 DuckDB 表）的 JOIN 在 API 层正常返回，
sources 正确包含两 Sheet，且不会跨用户访问。

复用 api_isolation 的隔离设施：临时表示存储 + AuthMiddleware 直通 + 假用户 + 受控 LLM。
doc_id 使用全小写（生产环境 doc_id 为小写 hex/uuid）。
"""
import json

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.utils.auth import get_current_active_user
from backend.services.table_query_service import TableQueryService


class _FakeUser:
    def __init__(self, uid):
        self.id = uid
        self.is_active = True
        self.is_superuser = False
        self.email = f"u{uid}@test"


def _write_multi_sheet_rep(rep_dir, doc_id, user_id, filename, sheets):
    sheet_list = []
    for sname, columns, rows in sheets:
        col_defs = [
            {"technical_name": n, "data_type": dt, "description": desc, "col_index": i}
            for i, (n, dt, desc) in enumerate(columns)
        ]
        table_rows = [
            {"row_index": ri, "range": f"A{ri + 1}",
             "cells": [{"col_index": i, "value": rd[n]} for i, (n, _, _) in enumerate(columns)]}
            for ri, rd in enumerate(rows)
        ]
        sheet_list.append({"sheet_name": sname, "tables": [{"columns": col_defs, "rows": table_rows}]})
    rep = {
        "document_id": doc_id, "filename": filename, "user_id": user_id,
        "file_type": "xlsx", "workbook": {"sheets": sheet_list},
    }
    (rep_dir / f"{doc_id}.json").write_text(json.dumps(rep, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def isolated_store(monkeypatch, tmp_path):
    import backend.services.table_representation as trep
    store = tmp_path / "table_originals"
    store.mkdir()
    monkeypatch.setattr(trep, "table_store_dir", lambda: store)
    return store


@pytest.fixture
def auth_as(monkeypatch):
    from backend.middleware.auth import AuthMiddleware

    async def _passthrough(self, request, call_next):
        return await call_next(request)

    monkeypatch.setattr(AuthMiddleware, "dispatch", _passthrough)
    state = {"uid": 1}

    def _make():
        return _FakeUser(state["uid"])

    app.dependency_overrides[get_current_active_user] = _make
    yield state
    app.dependency_overrides.pop(get_current_active_user, None)


@pytest.fixture
def fake_llm(monkeypatch):
    async def fake_nl2sql(self, question, schema, alias, allowed):
        return f'SELECT COUNT(*) AS cnt FROM "{allowed[0]}"'
    async def fake_explain(self, *a, **k):
        return "（解释）"
    monkeypatch.setattr(TableQueryService, "_nl2sql", fake_nl2sql)
    monkeypatch.setattr(TableQueryService, "_explain_text", fake_explain)
    return fake_nl2sql


def test_api_multi_sheet_join(isolated_store, auth_as, fake_llm, monkeypatch):
    _write_multi_sheet_rep(isolated_store, "docms", 1, "orders.xlsx", [
        ("OrderSKUList", [("sku", "text", ""), ("qty", "integer", "")],
         [{"sku": "A", "qty": 10}, {"sku": "B", "qty": 20}]),
        ("Shipping", [("sku", "text", ""), ("price", "double", "")],
         [{"sku": "A", "price": 5}, {"sku": "B", "price": 8}]),
    ])
    ta, tb = "t_docms_0", "t_docms_1"
    join_sql = (
        f'SELECT a."sku", a."qty", b."price" FROM "{ta}" a '
        f'JOIN "{tb}" b ON a."sku" = b."sku" ORDER BY a."sku"'
    )

    async def join_nl2sql(self, question, schema, alias, allowed):
        return join_sql
    monkeypatch.setattr(TableQueryService, "_nl2sql", join_nl2sql)

    # --- uid=1 查询自己的两 Sheet JOIN（用计数关键词命中 PRECISE_QUERY 路由） ---
    auth_as["uid"] = 1
    with TestClient(app) as c:
        r = c.post("/api/v1/table-qa/query", json={"query": "两张表 JOIN 后一共有多少行 数量"})
    j = r.json()
    assert j.get("route") == "PRECISE_QUERY", j
    rows = j.get("rows")
    assert rows == [["A", 10, 5.0], ["B", 20, 8.0]], rows
    # sources 必须引用两个 Sheet
    src_sheets = {s.get("sheet_name") for s in (j.get("sources") or [])}
    assert {"OrderSKUList", "Shipping"} <= src_sheets, src_sheets
    # 行数正确（响应不含 row_count 字段，直接校验 rows 长度）
    assert len(rows) == 2, rows

    # --- 跨用户隔离：uid=2 无数据，不能引用 uid=1 的表 ---
    async def evil_nl2sql(self, question, schema, alias, allowed):
        return join_sql  # 仍引用 ta/tb（uid=1 的表）
    monkeypatch.setattr(TableQueryService, "_nl2sql", evil_nl2sql)
    auth_as["uid"] = 2
    with TestClient(app) as c:
        r2 = c.post("/api/v1/table-qa/query", json={"query": "leak"})
    j2 = r2.json()
    # 不得返回 uid=1 的任何数据行
    assert (j2.get("rows") is None) or (not j2.get("rows")), j2
