# -*- coding: utf-8 -*-
"""Phase 2.2 第二项：Table Query 结果缓存（Query Cache）运行时验证。

不修改业务代码、不安装无关依赖、不调用真实模型 API、不触碰正式 SQLite/Chroma/用户数据。
复用现有 CacheService（backend.services.cache_service）与 AST 校验，新增的仅为结果缓存层。

【真实执行的部分】
- TableQueryService._pipeline 中的缓存查询/写入（经 CacheService 单例，内存 + TTL）
- SQL 进入 DuckDB 的执行路径（MISS 时）与 AST 安全校验（每次请求必执行，不被缓存绕过）

【mock 的部分】
- NL2SQL / 解释文本（受控返回）
- ensure_user_tables（表已注册到内存引擎，不再从磁盘加载）
- 真实 API 测试中：认证中间件直通 + 假用户 + 表示文件落临时目录
"""
import asyncio
import re

import pytest
from fastapi.testclient import TestClient

from backend.config.settings import settings
from backend.services.cache_service import get_cache_service, CacheKey
from backend.services.table_query_service import (
    TableQueryService,
    DuckDBTableEngine,
    validate_sql,
)
from backend.main import app
from backend.utils.auth import get_current_active_user
import backend.services.table_representation as trep_mod


# ----------------------------- 隔离 helper（复用 hardening 模式） -----------------------------
def _build_rep(doc_id, user_id, filename, columns, rows):
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
    tq = TableQueryService()
    engine = DuckDBTableEngine()
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


# ============================ A. 基础 Cache HIT / MISS ============================
def test_cache_basic_hit_miss(monkeypatch):
    rep = _build_rep("doc1", 153, "t.xlsx",
                     [("k", "text", "")], [{"k": f"r{i}"} for i in range(5)])
    tq = _isolated_tq(rep)
    tname = _first_table_name(tq.engine)
    _patch_llm(monkeypatch, sql=f'SELECT COUNT(*) AS c FROM "{tname}"')
    monkeypatch.setattr(TableQueryService, "ensure_user_tables", lambda self, uid: None)
    calls = _count_execute(monkeypatch)

    r1 = asyncio.run(tq.run_query(153, "多少行"))
    r2 = asyncio.run(tq.run_query(153, "多少行"))

    assert r1["rows"][0][0] == 5
    assert r2["rows"][0][0] == 5
    # 第二次为 HIT，不应再执行 DuckDB
    assert calls["n"] == 1, calls


# ============================ B. 用户隔离 ============================
def test_cache_user_isolation(monkeypatch):
    rep_a = _build_rep("docA", 1001, "a.xlsx",
                       [("k", "text", "")], [{"k": f"a{i}"} for i in range(3)])
    rep_b = _build_rep("docB", 2002, "b.xlsx",
                       [("k", "text", "")], [{"k": f"b{i}"} for i in range(7)])
    tq_a = _isolated_tq(rep_a, user_id=1001)
    tq_b = _isolated_tq(rep_b, user_id=2002)
    ta = _first_table_name(tq_a.engine)
    tb = _first_table_name(tq_b.engine)

    async def fake(self, q, sch, al, allowed):
        t = allowed[0] if allowed else "t_none_0"
        return f'SELECT COUNT(*) AS c FROM "{t}"'

    async def fake_explain(self, *a, **k):
        return "（解释）"
    monkeypatch.setattr(TableQueryService, "_nl2sql", fake)
    monkeypatch.setattr(TableQueryService, "_explain_text", fake_explain)
    monkeypatch.setattr(TableQueryService, "ensure_user_tables", lambda self, uid: None)

    ra = asyncio.run(tq_a.run_query(1001, "多少行"))
    rb = asyncio.run(tq_b.run_query(2002, "多少行"))
    assert ra["rows"][0][0] == 3
    assert rb["rows"][0][0] == 7


# ============================ C. 文档隔离 ============================
def test_cache_document_isolation(monkeypatch):
    rep_a = _build_rep("docA", 153, "a.xlsx",
                       [("k", "text", "")], [{"k": f"a{i}"} for i in range(3)])
    rep_b = _build_rep("docB", 153, "b.xlsx",
                       [("k", "text", "")], [{"k": f"b{i}"} for i in range(5)])
    tq = _isolated_tq(None)
    tq.engine.register_representation(rep_a, 153)
    tq.engine.register_representation(rep_b, 153)

    async def fake(self, q, sch, al, allowed):
        t = allowed[0] if allowed else "t_none_0"
        return f'SELECT COUNT(*) AS c FROM "{t}"'

    monkeypatch.setattr(TableQueryService, "_nl2sql", fake)

    async def fake_explain(self, *a, **k):
        return "（解释）"
    monkeypatch.setattr(TableQueryService, "_explain_text", fake_explain)
    monkeypatch.setattr(TableQueryService, "ensure_user_tables", lambda self, uid: None)

    ra = asyncio.run(tq.run_query(153, "多少行", document_id="docA"))
    rb = asyncio.run(tq.run_query(153, "多少行", document_id="docB"))
    assert ra["rows"][0][0] == 3
    assert rb["rows"][0][0] == 5


# ============================ D. SQL 区分 ============================
def test_cache_sql_distinction(monkeypatch):
    rep = _build_rep("doc1", 153, "t.xlsx",
                     [("k", "text", ""), ("amount", "numeric", "")],
                     [{"k": f"r{i}", "amount": i} for i in range(5)])
    tq = _isolated_tq(rep)
    tname = _first_table_name(tq.engine)
    ctl = {"sql": None}

    async def fake(self, q, sch, al, allowed):
        return ctl["sql"]

    monkeypatch.setattr(TableQueryService, "_nl2sql", fake)

    async def fake_explain(self, *a, **k):
        return "（解释）"
    monkeypatch.setattr(TableQueryService, "_explain_text", fake_explain)
    monkeypatch.setattr(TableQueryService, "ensure_user_tables", lambda self, uid: None)
    calls = _count_execute(monkeypatch)

    ctl["sql"] = f'SELECT COUNT(*) AS c FROM "{tname}"'
    r1 = asyncio.run(tq.run_query(153, "多少行"))
    ctl["sql"] = f'SELECT SUM(amount) AS s FROM "{tname}"'
    r2 = asyncio.run(tq.run_query(153, "多少行"))

    assert r1["rows"][0][0] == 5
    assert r2["rows"][0][0] == 10  # 0+1+2+3+4
    # 不同 SQL -> 两次都执行（各自 MISS）
    assert calls["n"] == 2, calls


# ============================ E. SQL normalization ============================
def test_cache_sql_normalization(monkeypatch):
    rep = _build_rep("doc1", 153, "t.xlsx",
                     [("k", "text", "")], [{"k": f"r{i}"} for i in range(5)])
    tq = _isolated_tq(rep)
    tname = _first_table_name(tq.engine)
    ctl = {"sql": None}

    async def fake(self, q, sch, al, allowed):
        return ctl["sql"]

    monkeypatch.setattr(TableQueryService, "_nl2sql", fake)

    async def fake_explain(self, *a, **k):
        return "（解释）"
    monkeypatch.setattr(TableQueryService, "_explain_text", fake_explain)
    monkeypatch.setattr(TableQueryService, "ensure_user_tables", lambda self, uid: None)
    calls = _count_execute(monkeypatch)

    ctl["sql"] = f'SELECT COUNT(*) AS c FROM "{tname}"'
    asyncio.run(tq.run_query(153, "多少行"))
    ctl["sql"] = f'select count(*) as c from "{tname}"'  # 大小写/空白不同
    asyncio.run(tq.run_query(153, "多少行"))

    # 语义相同（仅大小写/空白差异）-> 归一后 key 一致 -> HIT
    assert calls["n"] == 1, calls


# ============================ F. 文档更新失效 ============================
def test_cache_document_update_invalidation(monkeypatch):
    rep_v1 = _build_rep("doc1", 153, "t.xlsx",
                        [("k", "text", "")], [{"k": f"r{i}"} for i in range(100)])
    rep_v2 = _build_rep("doc1", 153, "t.xlsx",
                        [("k", "text", "")], [{"k": f"r{i}"} for i in range(200)])
    tq = _isolated_tq(rep_v1)
    tname = _first_table_name(tq.engine)
    _patch_llm(monkeypatch, sql=f'SELECT COUNT(*) AS c FROM "{tname}"')
    monkeypatch.setattr(TableQueryService, "ensure_user_tables", lambda self, uid: None)

    r1 = asyncio.run(tq.run_query(153, "多少行"))
    assert r1["rows"][0][0] == 100

    # 文档内容更新：重新注册 v2（覆盖同表，rep_version 改变）
    tq.engine.register_representation(rep_v2, 153)
    r2 = asyncio.run(tq.run_query(153, "多少行"))
    # 必须 MISS 新数据，绝不能返回旧的 100
    assert r2["rows"][0][0] == 200, r2


# ============================ G. 写入失败不致命 ============================
def test_cache_write_failure_non_fatal(monkeypatch):
    rep = _build_rep("doc1", 153, "t.xlsx",
                     [("k", "text", "")], [{"k": f"r{i}"} for i in range(5)])
    tq = _isolated_tq(rep)
    tname = _first_table_name(tq.engine)
    _patch_llm(monkeypatch, sql=f'SELECT COUNT(*) AS c FROM "{tname}"')
    monkeypatch.setattr(TableQueryService, "ensure_user_tables", lambda self, uid: None)
    cache = get_cache_service()

    async def boom(*a, **k):
        raise RuntimeError("simulated write failure")

    monkeypatch.setattr(cache, "set", boom)
    r = asyncio.run(tq.run_query(153, "多少行"))
    # 即使缓存写入失败，DuckDB 查询仍成功返回
    assert r["rows"][0][0] == 5


# ============================ H. 读取失败不致命 ============================
def test_cache_read_failure_non_fatal(monkeypatch):
    rep = _build_rep("doc1", 153, "t.xlsx",
                     [("k", "text", "")], [{"k": f"r{i}"} for i in range(5)])
    tq = _isolated_tq(rep)
    tname = _first_table_name(tq.engine)
    _patch_llm(monkeypatch, sql=f'SELECT COUNT(*) AS c FROM "{tname}"')
    monkeypatch.setattr(TableQueryService, "ensure_user_tables", lambda self, uid: None)
    cache = get_cache_service()

    async def boom(*a, **k):
        raise RuntimeError("simulated read failure")

    monkeypatch.setattr(cache, "get", boom)
    r = asyncio.run(tq.run_query(153, "多少行"))
    # 缓存读取失败 -> 回退 DuckDB，结果正确
    assert r["rows"][0][0] == 5


# ============================ I. 权限：即使缓存存在也拒绝越权 ============================
def test_cache_permission_denied_even_if_cached(monkeypatch):
    rep_a = _build_rep("docA", 1001, "a.xlsx",
                       [("k", "text", "")], [{"k": f"a{i}"} for i in range(3)])
    tq_a = _isolated_tq(rep_a, user_id=1001)
    ta = _first_table_name(tq_a.engine)
    _patch_llm(monkeypatch, sql=f'SELECT COUNT(*) AS c FROM "{ta}"')
    monkeypatch.setattr(TableQueryService, "ensure_user_tables", lambda self, uid: None)

    ra = asyncio.run(tq_a.run_query(1001, "多少行", document_id="docA"))
    assert ra["rows"][0][0] == 3  # A 成功并缓存

    # 用户 B 根本没有该文档：即使缓存里有 A 的结果，也必须被权限拒绝
    tq_b = _isolated_tq(None, user_id=2002)
    monkeypatch.setattr(tq_b.__class__, "ensure_user_tables", lambda self, uid: None)
    with pytest.raises(ValueError):
        asyncio.run(tq_b.run_query(2002, "多少行", document_id="docA"))


# ============================ J. AST 校验不被缓存绕过 ============================
def test_cache_ast_not_bypassed(monkeypatch):
    rep = _build_rep("doc1", 153, "t.xlsx",
                     [("k", "text", "")], [{"k": f"r{i}"} for i in range(5)])
    tq = _isolated_tq(rep)
    tname = _first_table_name(tq.engine)
    _patch_llm(monkeypatch, sql=f'SELECT COUNT(*) AS c FROM "{tname}"')
    monkeypatch.setattr(TableQueryService, "ensure_user_tables", lambda self, uid: None)
    # 先正常查询，缓存合法结果
    asyncio.run(tq.run_query(153, "多少行"))

    # 恶意 SQL（越权表）：即使缓存存在，也必须先过 AST 校验
    async def evil(self, q, sch, al, allowed):
        return 'SELECT * FROM t_evil_9'

    monkeypatch.setattr(TableQueryService, "_nl2sql", evil)
    with pytest.raises(ValueError):
        asyncio.run(tq.run_query(153, "恶意"))


# ============================ 真实 API：HIT / MISS ============================
class _FakeUser:
    def __init__(self, uid):
        self.id = uid
        self.is_active = True
        self.is_superuser = False
        self.email = f"u{uid}@test"


@pytest.fixture
def isolated_store(monkeypatch, tmp_path):
    store = tmp_path / "table_originals"
    store.mkdir()
    monkeypatch.setattr(trep_mod, "table_store_dir", lambda: store)
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


def _write_rep(rep_dir, doc_id, user_id, filename, columns, rows):
    col_defs = [
        {"technical_name": n, "data_type": dt, "description": desc, "col_index": i}
        for i, (n, dt, desc) in enumerate(columns)
    ]
    table_rows = []
    for ri, rd in enumerate(rows):
        cells = [{"col_index": i, "value": rd[n]} for i, (n, _, _) in enumerate(columns)]
        table_rows.append({"row_index": ri, "range": f"A{ri + 1}", "cells": cells})
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
        __import__("json").dumps(rep, ensure_ascii=False), encoding="utf-8"
    )


def test_api_cache_hit_skip_duckdb(isolated_store, auth_as, monkeypatch):
    _write_rep(isolated_store, "doc1", 1, "A.xlsx",
               [("k", "text", "")], [{"k": f"r{i}"} for i in range(5)])

    calls = {"n": 0}
    orig = TableQueryService._execute_with_fallback

    def counted(self, sql, *a, **k):
        calls["n"] += 1
        return orig(self, sql, *a, **k)

    monkeypatch.setattr(TableQueryService, "_execute_with_fallback", counted)

    async def fake_nl2sql(self, q, schema, alias, allowed):
        t = allowed[0] if allowed else "t_none_0"
        return f'SELECT COUNT(*) AS cnt FROM "{t}"'

    async def fake_explain(self, *a, **k):
        return "（解释）"

    monkeypatch.setattr(TableQueryService, "_nl2sql", fake_nl2sql)
    monkeypatch.setattr(TableQueryService, "_explain_text", fake_explain)
    auth_as["uid"] = 1

    with TestClient(app) as c:
        r1 = c.post("/api/v1/table-qa/query", json={"query": "多少行"})
        r2 = c.post("/api/v1/table-qa/query", json={"query": "多少行"})

    assert r1.status_code == 200
    assert r1.json()["rows"][0][0] == 5
    assert r2.json()["rows"][0][0] == 5
    # 第二次为 HIT，不应再执行 DuckDB
    assert calls["n"] == 1, calls


def test_api_cache_update_invalidation(isolated_store, auth_as, monkeypatch):
    calls = {"n": 0}
    orig = TableQueryService._execute_with_fallback

    def counted(self, sql, *a, **k):
        calls["n"] += 1
        return orig(self, sql, *a, **k)

    monkeypatch.setattr(TableQueryService, "_execute_with_fallback", counted)

    async def fake_nl2sql(self, q, schema, alias, allowed):
        t = allowed[0] if allowed else "t_none_0"
        return f'SELECT COUNT(*) AS cnt FROM "{t}"'

    async def fake_explain(self, *a, **k):
        return "（解释）"

    monkeypatch.setattr(TableQueryService, "_nl2sql", fake_nl2sql)
    monkeypatch.setattr(TableQueryService, "_explain_text", fake_explain)
    auth_as["uid"] = 1

    _write_rep(isolated_store, "doc1", 1, "A.xlsx",
               [("k", "text", "")], [{"k": f"r{i}"} for i in range(100)])
    with TestClient(app) as c:
        r1 = c.post("/api/v1/table-qa/query", json={"query": "多少行"})
    assert r1.json()["rows"][0][0] == 100

    # 文档内容更新（覆盖同 document_id）
    _write_rep(isolated_store, "doc1", 1, "A.xlsx",
               [("k", "text", "")], [{"k": f"r{i}"} for i in range(200)])
    with TestClient(app) as c:
        r2 = c.post("/api/v1/table-qa/query", json={"query": "多少行"})
    assert r2.json()["rows"][0][0] == 200, r2.json()
    # 两次均执行 DuckDB（v1 MISS + v2 数据版本变化 MISS）
    assert calls["n"] == 2, calls
