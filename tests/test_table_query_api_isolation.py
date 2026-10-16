# -*- coding: utf-8 -*-
"""API 层并发隔离 + 跨用户数据隔离 运行时验证（Phase 2.1 R1 修复验收）。

复用项目现有设施，不修改业务代码、不安装依赖、不触碰真实数据与模型 API：
- conftest 的 app + FastAPI TestClient（真实请求处理链）
- 通过 app.dependency_overrides 注入假用户对象（仅提供 .id，不查真实 DB）
- monkeypatch backend.services.table_representation.table_store_dir 指向临时目录
  （不写真实 table_originals，不读其他用户数据）
- monkeypatch TableQueryService._nl2sql / _explain_text 返回受控 SQL / 解释（不调用真实 LLM）

【真实执行的部分】
- FastAPI 路由 /api/v1/table-qa/query 的完整请求处理链
- DuckDB 真实建表、真实执行 SQL
- validate_sql 真实校验（白名单 / 关键字黑名单 / 多语句拦截）
- _allowed 按 user_id 真实过滤（二次兜底）
- 连接生命周期：每次请求新建 TableQueryService + finally close()
- 超时 watchdog + con.interrupt 真实触发，且互不干扰（请求线程隔离）

【mock 的部分】
- 认证用户对象（仅 .id）
- NL2SQL / 解释文本（受控返回）
- 表示文件存储位置（临时目录）
"""
import json
import re
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.utils.auth import get_current_active_user
from backend.services import table_qa_service as tqa_mod
from backend.services import table_query_service as tq_mod
from backend.services.table_query_service import (
    TableQueryService,
    DuckDBTableEngine,
    validate_sql,
)
from backend.config.settings import settings


# ----------------------------- 假用户 -----------------------------
class _FakeUser:
    def __init__(self, uid):
        self.id = uid
        self.is_active = True
        self.is_superuser = False
        self.email = f"u{uid}@test"


# ----------------------------- 表示文件构造（落临时目录） -----------------------------
def _table_name_for(doc_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9]", "_", str(doc_id))
    return f"t_{safe}_0"


def _write_rep(rep_dir: Path, doc_id, user_id, filename, columns, rows):
    """写一份与 register_representation 兼容的 Representation JSON 到临时目录。"""
    col_defs = [
        {"technical_name": n, "data_type": dt, "description": desc, "col_index": i}
        for i, (n, dt, desc) in enumerate(columns)
    ]
    table_rows = []
    for ri, rd in enumerate(rows):
        table_rows.append({
            "row_index": ri,
            "range": f"A{ri + 1}",
            "cells": [{"col_index": i, "value": rd[n]} for i, (n, _, _) in enumerate(columns)],
        })
    rep = {
        "document_id": doc_id,
        "filename": filename,
        "user_id": user_id,
        "file_type": "xlsx",
        "workbook": {
            "sheets": [{
                "sheet_name": "Sheet1",
                "tables": [{"columns": col_defs, "rows": table_rows}],
            }]
        },
    }
    (rep_dir / f"{doc_id}.json").write_text(
        json.dumps(rep, ensure_ascii=False), encoding="utf-8"
    )


# ----------------------------- 夹具：临时表示存储 -----------------------------
@pytest.fixture
def isolated_store(monkeypatch, tmp_path):
    import backend.services.table_representation as trep

    store = tmp_path / "table_originals"
    store.mkdir()
    monkeypatch.setattr(trep, "table_store_dir", lambda: store)
    return store


# ----------------------------- 夹具：注入假用户 -----------------------------
@pytest.fixture
def auth_as(monkeypatch):
    from backend.middleware.auth import AuthMiddleware

    # 测试中将鉴权中间件改为直通：避免其用真实 SessionLocal 查真实数据库（不修改正式数据），
    # 真实用户身份由下方 get_current_active_user 依赖覆盖注入的假用户提供。
    async def _passthrough(self, request, call_next):
        return await call_next(request)

    monkeypatch.setattr(AuthMiddleware, "dispatch", _passthrough)

    state = {"uid": 1}

    def _make():
        return _FakeUser(state["uid"])

    app.dependency_overrides[get_current_active_user] = _make
    yield state
    app.dependency_overrides.pop(get_current_active_user, None)


# ----------------------------- 夹具：受控 LLM -----------------------------
@pytest.fixture
def fake_llm(monkeypatch):
    ctrl = {"mode": "count"}

    async def fake_nl2sql(self, question, schema, alias, allowed):
        t = allowed[0] if allowed else "t_none_0"
        if ctrl["mode"] == "heavy":
            # 引用 allowed 表以通过 validate_sql 白名单，但内嵌 20 亿行 range 子查询 -> 真实超时
            return (
                f'SELECT COUNT(*) AS cnt FROM "{t}" a, '
                f'(SELECT range i FROM range(2000000000)) b'
            )
        return f'SELECT COUNT(*) AS cnt FROM "{t}"'

    async def fake_explain(self, *a, **k):
        return "（解释）"

    monkeypatch.setattr(TableQueryService, "_nl2sql", fake_nl2sql)
    monkeypatch.setattr(TableQueryService, "_explain_text", fake_explain)
    return ctrl


# ============================================================
# 测试 1：跨用户功能隔离 + 越权读取被真实校验拒绝
# ============================================================
def test_api_cross_user_isolation(isolated_store, auth_as, fake_llm):
    # A=1001（3 行），B=2002（7 行），独立表示文件
    _write_rep(isolated_store, "doc_A_001", 1001, "A.xlsx",
               [("k", "text", "")], [{"k": f"a{i}"} for i in range(3)])
    _write_rep(isolated_store, "doc_B_001", 2002, "B.xlsx",
               [("k", "text", "")], [{"k": f"b{i}"} for i in range(7)])
    a_tab = _table_name_for("doc_A_001")

    # --- A 查询自己的数据 ---
    auth_as["uid"] = 1001
    with TestClient(app) as c:
        rA = c.post("/api/v1/table-qa/query", json={"query": "A 文档有多少行 数量"})
    jA = rA.json()
    assert jA.get("route") == "PRECISE_QUERY", jA
    assert jA.get("rows") and jA["rows"][0][0] == 3, jA  # 仅 A 的 3 行

    # --- B 查询自己的数据 ---
    auth_as["uid"] = 2002
    with TestClient(app) as c:
        rB = c.post("/api/v1/table-qa/query", json={"query": "B 文档有多少行 数量"})
    jB = rB.json()
    assert jB.get("rows") and jB["rows"][0][0] == 7, jB  # 仅 B 的 7 行

    # --- B 越权：让 NL2SQL 直指 A 的表，走真实 validate_sql 应被拒，且绝不泄露 A 数据 ---
    async def malicious_nl2sql(self, question, schema, alias, allowed):
        return f'SELECT * FROM "{a_tab}"'

    # 用 patch.object 注入恶意 NL2SQL（仅本段生效，结束即还原）
    import unittest.mock
    patcher = unittest.mock.patch.object(TableQueryService, "_nl2sql", malicious_nl2sql)
    patcher.start()
    try:
        auth_as["uid"] = 2002
        with TestClient(app) as c:
            rM = c.post("/api/v1/table-qa/query", json={"query": "B 尝试读取 A 数量"})
        jM = rM.json()
        flat = json.dumps(jM, ensure_ascii=False)
        # 不得出现 A 的任何数据标记
        assert "a0" not in flat, flat
        assert "a1" not in flat, flat
        # 必须被拒绝（error_type 存在 或 无 rows）
        assert (jM.get("error_type") is not None) or (not jM.get("rows")), jM
    finally:
        patcher.stop()

    # --- 直接核验校验边界：B 的允许表名单下引用 A 表必须抛错 ---
    b_tab = _table_name_for("doc_B_001")
    with pytest.raises(ValueError):
        validate_sql(f'SELECT * FROM "{a_tab}"', [b_tab])


# ============================================================
# 测试 2：API 层并发请求隔离（不同连接 + 超时互不干扰 + 连接清理）
# ============================================================
def test_api_concurrent_requests_isolated_connections(isolated_store, auth_as, fake_llm, monkeypatch):
    # 单用户 1 的一份数据（5 行）
    _write_rep(isolated_store, "doc_T_001", 1, "T.xlsx",
               [("k", "text", "")], [{"k": f"r{i}"} for i in range(5)])

    # 追踪每次请求创建的连接与关闭情况
    created = []   # (thread_ident, id_tq, id_engine, id_con)
    closed = []    # id_engine
    orig_init = TableQueryService.__init__
    orig_close = DuckDBTableEngine.close

    def traced_init(self):
        ret = orig_init(self)
        created.append((threading.get_ident(), id(self), id(self.engine), id(self.engine.con)))
        return ret

    def traced_close(self):
        closed.append(id(self))
        return orig_close(self)

    monkeypatch.setattr(TableQueryService, "__init__", traced_init)
    monkeypatch.setattr(DuckDBTableEngine, "close", traced_close)

    # 缩短超时，触发真实 watchdog + con.interrupt
    monkeypatch.setattr(settings, "SQL_QUERY_TIMEOUT", 0.3)

    # 让 NL2SQL 按问题分支：HEAVY -> 巨型查询（真实超时），其余 -> 普通计数
    async def nl2sql_branch(self, question, schema, alias, allowed):
        t = allowed[0] if allowed else "t_none_0"
        if "HEAVY" in question:
            return (
                f'SELECT COUNT(*) AS cnt FROM "{t}" a, '
                f'(SELECT range i FROM range(2000000000)) b'
            )
        return f'SELECT COUNT(*) AS cnt FROM "{t}"'

    monkeypatch.setattr(TableQueryService, "_nl2sql", nl2sql_branch)

    auth_as["uid"] = 1

    results = {}

    def heavy_req():
        with TestClient(app) as c:
            results["heavy"] = c.post(
                "/api/v1/table-qa/query", json={"query": "HEAVY 压测 有多少行"}
            )

    def quick_req():
        with TestClient(app) as c:
            results["quick"] = c.post(
                "/api/v1/table-qa/query", json={"query": "文档里有多少行"}
            )

    th = threading.Thread(target=heavy_req)
    tq = threading.Thread(target=quick_req)
    th.start()
    tq.start()
    th.join(timeout=30)
    tq.join(timeout=30)

    jH = results["heavy"].json()
    jQ = results["quick"].json()

    # 快速请求独立成功，返回自身 5 行
    assert jQ.get("rows") and jQ["rows"][0][0] == 5, jQ
    # 重型请求被超时中断，受控返回错误（不影响快速请求）
    assert ("超时" in (jH.get("answer") or "")) or (jH.get("error_type") is not None), jH

    # 两个请求使用了不同的 TableQueryService 实例与不同连接
    assert len(created) == 2, created
    tq_ids = {c[1] for c in created}
    con_ids = {c[3] for c in created}
    assert len(tq_ids) == 2, "两个请求共享了同一个 TableQueryService 实例"
    assert len(con_ids) == 2, "两个请求共享了同一个 DuckDB 连接"
    # 所有创建的连接均被关闭（无泄漏）
    created_engines = {c[2] for c in created}
    assert created_engines == set(closed), (created_engines, set(closed))
    assert len(closed) == 2


# ============================================================
# 测试 3：直接校验 validate_sql 跨用户边界（确定性单元检查）
# ============================================================
def test_cross_user_validate_sql_boundary(isolated_store, monkeypatch):
    import backend.services.table_representation as trep

    _write_rep(isolated_store, "doc_A_001", 1001, "A.xlsx",
               [("k", "text", "")], [{"k": "a"}])
    _write_rep(isolated_store, "doc_B_001", 2002, "B.xlsx",
               [("k", "text", "")], [{"k": "b"}])
    a_tab = _table_name_for("doc_A_001")
    b_tab = _table_name_for("doc_B_001")

    # A 自己的查询合法
    validate_sql(f'SELECT * FROM "{a_tab}"', [a_tab])
    # B 引用 A 的表 -> 必须拒绝
    with pytest.raises(ValueError):
        validate_sql(f'SELECT * FROM "{a_tab}"', [b_tab])
    # 写操作必须拒绝
    with pytest.raises(ValueError):
        validate_sql(f'DROP TABLE "{a_tab}"', [a_tab])
    with pytest.raises(ValueError):
        validate_sql(f'DELETE FROM "{a_tab}"', [a_tab])
    # 多语句必须拒绝
    with pytest.raises(ValueError):
        validate_sql(f'SELECT * FROM "{a_tab}"; SELECT 1', [a_tab])
