# -*- coding: utf-8 -*-
"""Phase 2.2 第一项：DuckDB Text-to-SQL 的 AST 安全校验（sqlglot）运行时验证。

不修改业务代码、不安装无关依赖、不调用真实模型 API、不触碰正式 SQLite/Chroma/用户数据。

【真实执行的部分】
- validate_sql() 的真实 sqlglot AST 解析与递归白名单/危险函数校验
- TableQueryService.run_query 真实管线（含 validate_sql 在 _pipeline 中的调用）

【mock 的部分】
- NL2SQL / 解释文本（受控返回，不调用真实 LLM）
- ensure_user_tables（表已注册到内存引擎，不再从磁盘加载）
"""
import asyncio

import pytest

from backend.config.settings import settings
from backend.services.table_query_service import (
    TableQueryService,
    DuckDBTableEngine,
    validate_sql,
)


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


ALLOWED = ["t_abc_0", "t_def_1"]


# ============================================================
# 1. 简单合法 SELECT
# ============================================================
def test_validate_simple_select_allowed():
    validate_sql("SELECT * FROM t_abc_0", ALLOWED)
    validate_sql('SELECT * FROM "t_abc_0"', ALLOWED)
    validate_sql("SELECT a, COUNT(*) FROM t_abc_0 GROUP BY a", ALLOWED)
    validate_sql("SELECT * FROM t_abc_0 WHERE x = 1 ORDER BY x DESC LIMIT 10", ALLOWED)


# ============================================================
# 2. 合法 CTE 与嵌套子查询
# ============================================================
def test_validate_cte_and_nested_subquery_allowed():
    validate_sql(
        "WITH c AS (SELECT * FROM t_abc_0) SELECT * FROM c", ALLOWED
    )
    validate_sql(
        "WITH c AS (SELECT * FROM t_abc_0) "
        "SELECT * FROM c JOIN t_def_1 d ON c.k = d.k", ALLOWED
    )
    validate_sql(
        "SELECT * FROM (SELECT * FROM t_abc_0 WHERE x > 1) sub WHERE y = 2", ALLOWED
    )
    validate_sql(
        "WITH x AS (SELECT * FROM t_abc_0), y AS (SELECT * FROM t_def_1) "
        "SELECT * FROM x JOIN y ON x.k = y.k", ALLOWED
    )


# ============================================================
# 3. 合法 JOIN 与多查询分支白名单验证
# ============================================================
def test_validate_join_and_branches_allowed():
    validate_sql(
        "SELECT a.k, b.v FROM t_abc_0 a INNER JOIN t_def_1 b ON a.k = b.k", ALLOWED
    )
    validate_sql(
        "SELECT * FROM t_abc_0 UNION ALL SELECT * FROM t_def_1", ALLOWED
    )
    validate_sql(
        "SELECT * FROM t_abc_0 UNION SELECT * FROM t_def_1", ALLOWED
    )
    # 引用两个 allowed 表的不同分支都应通过
    validate_sql(
        "SELECT * FROM t_abc_0 WHERE k IN (SELECT k FROM t_def_1)", ALLOWED
    )


# ============================================================
# 4. 未授权物理表出现在最外层 / CTE 内部 / 子查询 / UNION 分支均被拒绝
# ============================================================
def test_validate_rejects_unauthorized_table_everywhere():
    # 最外层
    with pytest.raises(ValueError):
        validate_sql("SELECT * FROM t_other_9", ALLOWED)
    # CTE 内部引用未授权表
    with pytest.raises(ValueError):
        validate_sql("WITH c AS (SELECT * FROM t_other_9) SELECT * FROM c", ALLOWED)
    # 子查询内
    with pytest.raises(ValueError):
        validate_sql("SELECT * FROM (SELECT * FROM t_other_9) s", ALLOWED)
    # UNION 分支
    with pytest.raises(ValueError):
        validate_sql(
            "SELECT * FROM t_abc_0 UNION SELECT * FROM t_other_9", ALLOWED
        )
    # 其他用户的表（形如 t_ 但不在白名单）
    with pytest.raises(ValueError):
        validate_sql('SELECT * FROM "t_xyz_0"', ALLOWED)


# ============================================================
# 5. DROP/DELETE/COPY/PRAGMA/ATTACH/CALL/多语句 被拒绝
# ============================================================
def test_validate_rejects_dml_ddl_and_multi_statement():
    for sql in [
        "INSERT INTO t_abc_0 VALUES (1)",
        "UPDATE t_abc_0 SET x = 1",
        "DELETE FROM t_abc_0",
        "DROP TABLE t_abc_0",
        "CREATE TABLE t_x_0 (a INT)",
        "ALTER TABLE t_abc_0 ADD COLUMN y INT",
        "ATTACH 'x.db' AS db",
        "DETACH db",
        "COPY t_abc_0 TO 'f.csv'",
        "PRAGMA table_info('t_abc_0')",
        "CALL pragma_table_info('t_abc_0')",
        "SELECT * FROM t_abc_0; SELECT 1",
        "SELECT 1; SELECT 2",
        "SELECT * FROM t_abc_0 WHERE 1=1; DROP TABLE t_abc_0",
    ]:
        with pytest.raises(ValueError):
            validate_sql(sql, ALLOWED)


# ============================================================
# 6. 通过 DuckDB 表函数读取外部文件 / 其他数据源被拒绝
# ============================================================
def test_validate_rejects_external_data_sources():
    for sql in [
        "SELECT * FROM read_csv('x.csv')",
        "SELECT * FROM read_csv_auto('x.csv')",
        "SELECT * FROM read_parquet('x.parquet')",
        "SELECT * FROM read_json('x.json')",
        "SELECT * FROM read_text('x.txt')",
        "SELECT * FROM read_excel('x.xlsx')",
        "SELECT * FROM 'file.csv'",
        "SELECT * FROM sqlite_scan('x.db', 't')",
        "SELECT * FROM postgres_scan('host', 'db', 't')",
        "SELECT * FROM mysql_scan('x', 't')",
        "SELECT * FROM (SELECT * FROM read_csv('e.csv')) sub",
    ]:
        with pytest.raises(ValueError):
            validate_sql(sql, ALLOWED)


# ============================================================
# 7. 引号标识符 / 表别名 / 大小写变体不绕过白名单
# ============================================================
def test_validate_quoted_alias_case_variants():
    # 合法：引号标识符仍指向 allowed（大小写不敏感匹配）
    validate_sql('SELECT * FROM "t_abc_0"', ALLOWED)
    validate_sql("SELECT * FROM t_abc_0 a WHERE a.k = 1", ALLOWED)
    # 尝试大小写变体绕过：真实物理表为小写，DuckDB 未加引号大小写不敏感，
    # 这里仅校验 AST 层白名单；若引用非白名单表名（含改写大小写）应拒绝
    with pytest.raises(ValueError):
        validate_sql('SELECT * FROM "T_NOTALLOWED_0"', ALLOWED)
    # 跨 schema/catalog 引用禁止
    with pytest.raises(ValueError):
        validate_sql("SELECT * FROM main.t_abc_0", ALLOWED)
    with pytest.raises(ValueError):
        validate_sql("SELECT * FROM other.t_abc_0", ALLOWED)


# ============================================================
# 8. 语法错误 / 解析失败安全拒绝
# ============================================================
def test_validate_syntax_error_rejected():
    for sql in [
        "SELECT FROM WHERE",
        "SELCT * FROM t_abc_0",
        "SELECT * FROM t_abc_0 ((",
        "WITH c (SELECT 1) SELECT * FROM c",  # 缺 AS
        "SELECT * FROM",
    ]:
        with pytest.raises(ValueError):
            validate_sql(sql, ALLOWED)


# ============================================================
# 9. 字符串/注释含危险关键字不应仅因文本命中而误拒，但仍须 AST 校验
# ============================================================
def test_validate_textual_keyword_in_literal_not_false_reject():
    # 合法：字符串字面量包含 DROP 关键字
    validate_sql(
        "SELECT * FROM t_abc_0 WHERE note = 'DROP TABLE x'", ALLOWED
    )
    # 合法：注释包含 DROP/DELETE 关键字
    validate_sql(
        "SELECT * FROM t_abc_0 -- 注意这里 DROP TABLE 只是注释\nWHERE k = 1", ALLOWED
    )
    # 字符串含 INSERT 也不误拒
    validate_sql(
        "SELECT * FROM t_abc_0 WHERE log = 'please INSERT row'", ALLOWED
    )


# ============================================================
# 10. 集成：真实管线中 AST 校验生效且不破坏合法查询 / 超时仍工作
# ============================================================
def test_pipeline_legal_query_executes(monkeypatch):
    rep = _build_rep(
        "doc_153", 153, "t.xlsx",
        [("k", "text", ""), ("v", "numeric", "")],
        [{"k": f"r{i}", "v": i} for i in range(5)],
    )
    tq = _isolated_tq(rep)
    tname = _first_table_name(tq.engine)
    _patch_llm(monkeypatch, sql=f'SELECT COUNT(*) AS c FROM "{tname}"')
    monkeypatch.setattr(TableQueryService, "ensure_user_tables", lambda self, uid: None)

    res = asyncio.run(tq.run_query(153, "有多少行"))
    assert res["rows"][0][0] == 5
    assert res["result_limited"] is False


def test_pipeline_malicious_nl2sql_blocked(monkeypatch):
    rep = _build_rep(
        "doc_153", 153, "t.xlsx", [("k", "text", "")], [{"k": "a"}]
    )
    tq = _isolated_tq(rep)
    tname = _first_table_name(tq.engine)

    # 越权：NL2SQL 试图读取其他用户的表（不在 allowed）
    _patch_llm(monkeypatch, sql='SELECT * FROM t_evil_9')
    monkeypatch.setattr(TableQueryService, "ensure_user_tables", lambda self, uid: None)
    with pytest.raises(ValueError):
        asyncio.run(tq.run_query(153, "越权查询"))

    # 危险数据源：NL2SQL 试图通过表函数读外部文件
    _patch_llm(monkeypatch, sql="SELECT * FROM read_csv('secret.csv')")
    with pytest.raises(ValueError):
        asyncio.run(tq.run_query(153, "读外部文件"))


def test_pipeline_timeout_still_detected(monkeypatch):
    # 巨型笛卡尔积（基于安全生成器 range）必须能被 AST 放行，且超时仍被 watchdog 捕获
    monkeypatch.setattr(settings, "SQL_QUERY_TIMEOUT", 0.3)
    rep = _build_rep("doc_to", 153, "t.xlsx", [("k", "text", "")], [{"k": "a"}])
    tq = _isolated_tq(rep)
    tname = _first_table_name(tq.engine)
    heavy = (
        f'SELECT count(*) FROM "{tname}" a, '
        "(SELECT range i FROM range(2000000000)) b"
    )
    _patch_llm(monkeypatch, sql=heavy)
    monkeypatch.setattr(TableQueryService, "ensure_user_tables", lambda self, uid: None)
    with pytest.raises(ValueError) as exc:
        asyncio.run(tq.run_query(153, "压测超时"))
    assert "超时" in str(exc.value)
    # 超时后连接可继续复用
    cols, rows, truncated = tq._execute_sql("SELECT 1 AS x", cap_rows=False)
    assert rows == [(1,)]
