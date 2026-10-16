# -*- coding: utf-8 -*-
"""
Phase 2：精确查询与计算（Representation -> DuckDB -> NL2SQL -> Validator -> Query -> Result）

设计约束（严格遵守）：
- 不重新解析原始 Excel：数据来源为 Phase 1 已生成的 Unified Table Representation
  （load_representation / build_representation），DuckDB 表完全由 Representation 构建。
- user_id 隔离：只加载当前用户归属的 Representation；Validator 强制 SQL 仅引用本用户表名。
- 类型策略优先用 Representation columns[].data_type，叠加值级安全判定
  （长数字 / 前导零 / 标识符列 -> VARCHAR），不主要依赖列名猜类型。
- 值匹配精确优先；宽松匹配仅受控且明确标注 match_mode。
- 不改动 Chroma / Phase 1 / 认证 / 前端 / .env / 端口。
- DuckDB 第一版：单例 in-memory 连接，每次 query 用 Representation 重建物理表，
  无复杂跨请求缓存/状态机（正确性 / 可测试性 / 可重复构建优先）。
"""
import os
import re
import json
import logging
import threading
from datetime import datetime, date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple, AsyncGenerator

import duckdb

import sqlglot
from sqlglot import exp

from backend.config.settings import settings
from backend.services.table_representation import (
    load_representation,
    build_representation,
    TABLE_EXTENSIONS,
)
from backend.services.llm_service import LLMFactory, LLMConfig
from backend.services.cache_service import get_cache_service, CacheKey

logger = logging.getLogger(__name__)

# 源列前缀（注入到每张 DuckDB 表，避免与业务列冲突）
SRC_PREFIX = "__"
SRC_COLUMNS = {
    f"{SRC_PREFIX}document_id": "VARCHAR",
    f"{SRC_PREFIX}user_id": "VARCHAR",
    f"{SRC_PREFIX}filename": "VARCHAR",
    f"{SRC_PREFIX}sheet_name": "VARCHAR",
    f"{SRC_PREFIX}row_index": "BIGINT",
    f"{SRC_PREFIX}range": "VARCHAR",
}

# 显式标识符列名（值看起来像数字，但必须作为字符串，保证精确，不失精度/前导零）
_IDENTIFIER_NAME_RE = re.compile(
    r"(order\s*id|sku|seller\s*sku|编号|单号|订单号|序号|邮编|手机|电话|账号|id$|code|no\.?$|number|工号|货号|条码)",
    re.IGNORECASE,
)

# 中文字段/值别名（仅用于在生成 SQL 前提示 LLM，不用于无脑替换业务数据）
COLUMN_ALIASES = {
    "物流商": "Shipping Provider Name",
    "物流": "Shipping Provider Name",
    "快递": "Shipping Provider Name",
    "销量": "Quantity",
    "数量": "Quantity",
    "sku": "SKU ID",
    # “运费”/“运费金额”/“原始运费” 默认对应原始运费（业务测试语义）
    "运费": "Original Shipping Fee",
    "运费金额": "Original Shipping Fee",
    "原始运费": "Original Shipping Fee",
    # 折后运费显式指向折扣后运费，避免与“运费”互相污染
    "折后运费": "Shipping Fee After Discount",
    # “金额”过于宽泛（可指商品金额/订单金额/运费等），不强行映射到固定列，
    # 交由 Schema Context 与列名推断，避免误导 NL2SQL 选错列。
    "订单": "Order ID",
    "订单号": "Order ID",
    # 金额 / 单价 语义区分（精确短语；不映射裸“价格”，避免误伤 SKU 单价查询）
    "订单金额": "Order Amount",
    "订单价格": "Order Amount",
    "总价": "Order Amount",
    "成交金额": "Order Amount",
    "商品单价": "SKU Unit Original Price",
    "SKU单价": "SKU Unit Original Price",
    "原始单价": "SKU Unit Original Price",
    "单位价格": "SKU Unit Original Price",
}
VALUE_ALIASES = {
    "金眼": "golden eye",
    "金眼物流": "golden eye",
}


# ----------------------------- 类型与值工具 -----------------------------
def _quote_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _is_leading_zero(val: Any) -> bool:
    s = str(val).strip()
    return len(s) > 1 and s[0] == "0" and s[1:].isdigit()


def _is_long_number(val: Any) -> bool:
    try:
        f = float(str(val).replace(",", "").replace("%", ""))
    except (ValueError, TypeError):
        return False
    return abs(f) > 2 ** 53


def _clean_numeric(v: Any) -> Optional[float]:
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d",
    "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S",
)


def _parse_date(v: Any) -> Optional[datetime]:
    if v is None:
        return None
    s = str(v).strip()
    if s == "":
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _classify_column(col: Dict[str, Any], sample_values: List[Any]) -> Tuple[str, str]:
    """返回 (duckdb_type, reason)。优先用 Representation data_type。"""
    name = str(col.get("technical_name", ""))
    dtype = col.get("data_type", "text")
    non_null = [v for v in sample_values if v is not None and str(v).strip() != ""]
    if dtype == "date":
        if (not non_null) or all(_parse_date(v) is not None for v in non_null):
            return "TIMESTAMP", "representation:date"
        return "VARCHAR", "date-unparseable"
    if dtype == "text":
        return "VARCHAR", "representation:text"
    # dtype == 'numeric'
    if _IDENTIFIER_NAME_RE.search(name):
        return "VARCHAR", "identifier-column"
    if any(_is_long_number(v) for v in non_null):
        return "VARCHAR", "long-number-precision"
    if any(_is_leading_zero(v) for v in non_null):
        return "VARCHAR", "leading-zero"
    return "DOUBLE", "numeric-measure"


# ----------------------- DuckDB 引擎（请求级 in-memory，非单例） -----------------------
class DuckDBTableEngine:
    """每次查询使用的独立 in-memory DuckDB 连接（请求级生命周期）。

    不再使用进程级单例：每个 TableQueryService 实例拥有自己的连接与 table_meta，
    请求结束即 close()，彻底消除跨请求共享连接 / 中断互相影响 / 内存表残留问题。
    """

    def __init__(self):
        self.con = duckdb.connect(database=":memory:")
        self.table_meta: Dict[str, Dict[str, Any]] = {}

    def close(self) -> None:
        """关闭底层 DuckDB 连接，释放内存表（请求结束后调用）。"""
        try:
            self.con.close()
        except Exception:  # noqa: BLE001
            pass

    def _table_name(self, document_id: str, sheet_idx: int) -> str:
        safe = re.sub(r"[^A-Za-z0-9]", "_", str(document_id))
        return f"t_{safe}_{sheet_idx}"

    def _convert(self, duck_type: str, raw: Any, doc: str, sheet: str, col: str, row: Any) -> Any:
        if raw is None:
            return None
        if duck_type == "DOUBLE":
            f = _clean_numeric(raw)
            if f is None:
                logger.warning(
                    "数值转换失败(置NULL) doc=%s sheet=%s col=%s row=%s val=%r",
                    doc, sheet, col, row, raw,
                )
                return None
            return f
        if duck_type == "TIMESTAMP":
            dt = _parse_date(raw)
            if dt is None:
                logger.warning(
                    "日期转换失败(置NULL) doc=%s sheet=%s col=%s row=%s val=%r",
                    doc, sheet, col, row, raw,
                )
                return None
            return dt
        return str(raw)

    def register_one(self, doc_id, user_id, filename, sheet_name, table, sheet_idx, rep_version):
        cols = table.get("columns", [])
        rows = table.get("rows", [])
        # 先剔除被 Phase 1 表头检测误判为数据的“描述行”，再采样/入库，
        # 否则脏行会让度量列被误判为 VARCHAR，导致聚合/CAST 失败。
        keep_rows = [r for r in rows if not self._is_junk_header_row(r, cols)]
        if len(keep_rows) != len(rows):
            logger.info(
                "文档 %s Sheet《%s》剔除 %d 行脏行（表头/描述重复），保留 %d 行",
                doc_id, sheet_name, len(rows) - len(keep_rows), len(keep_rows),
            )
        sample: Dict[int, List[Any]] = {c["col_index"]: [] for c in cols}
        for r in keep_rows:
            for cell in r.get("cells", []):
                ci = cell.get("col_index")
                if ci in sample and len(sample[ci]) < 50:
                    sample[ci].append(cell.get("value"))
        biz = []
        for c in cols:
            duck_type, reason = _classify_column(c, sample.get(c["col_index"], []))
            biz.append({
                "name": c["technical_name"], "type": duck_type, "reason": reason,
                "desc": c.get("description"), "col_index": c["col_index"],
            })
        col_defs = [_quote_ident(b["name"]) + " " + b["type"] for b in biz]
        src_defs = [_quote_ident(n) + " " + t for n, t in SRC_COLUMNS.items()]
        tname = self._table_name(doc_id, sheet_idx)
        self.con.execute(f"DROP TABLE IF EXISTS {tname}")
        self.con.execute(f"CREATE TABLE {tname} ({', '.join(col_defs + src_defs)})")
        data: List[Tuple] = []
        for r in keep_rows:
            cell_map = {cell.get("col_index"): cell.get("value") for cell in r.get("cells", [])}
            vals: List[Any] = []
            for b in biz:
                raw = cell_map.get(b["col_index"])
                vals.append(self._convert(b["type"], raw, doc_id, sheet_name, b["name"], r.get("row_index")))
            vals += [doc_id, str(user_id), filename, sheet_name, r.get("row_index"), r.get("range")]
            data.append(tuple(vals))
        if data:
            n = len(biz) + len(SRC_COLUMNS)
            placeholders = ",".join(["?"] * n)
            self.con.executemany(f"INSERT INTO {tname} VALUES ({placeholders})", data)
        self.table_meta[tname] = {
            "user_id": str(user_id), "document_id": doc_id, "filename": filename,
            "sheet_name": sheet_name, "columns": biz, "rep_version": rep_version,
        }

    def register_representation(self, rep: Dict[str, Any], user_id: Any):
        doc_id = rep.get("document_id")
        filename = rep.get("filename")
        rep_version = _rep_data_version(rep)
        for si, sheet in enumerate(rep.get("workbook", {}).get("sheets", [])):
            for table in sheet.get("tables", []):
                self.register_one(doc_id, user_id, filename, sheet.get("sheet_name"), table, si, rep_version)

    @staticmethod
    def _is_junk_header_row(row: Dict[str, Any], cols: List[Dict[str, Any]]) -> bool:
        """跳过被 Phase 1 表头检测误判为数据的“描述行/表头重复行”。

        Phase 1 对“标题行 + 描述行”两行表头，若描述行含数字会提前终止表头判定，
        导致描述行被当成数据行（其首列值形如 'Platform unique order ID.'）。
        判定：
          1) 首列值等于该列 technical_name 或 description；
          2) 首列本应是标识符/数值列，其值却是“带空格的长英文短语”（描述文本）。
        """
        if not cols:
            return False
        first_idx = cols[0]["col_index"]
        first_val = None
        for cell in row.get("cells", []):
            if cell.get("col_index") == first_idx:
                first_val = cell.get("value")
                break
        if first_val is None:
            return False
        v = str(first_val).strip()
        name = str(cols[0].get("technical_name", "")).strip()
        desc = str(cols[0].get("description") or "").strip()
        if v == name or (desc != "" and v == desc):
            return True
        # 首列是标识符/数值列，但值像英文描述短语（含空格的长字母串）
        if (cols[0].get("data_type") == "numeric"
                or _IDENTIFIER_NAME_RE.search(name)):
            if re.search(r"[A-Za-z]", v) and " " in v and len(v) > 6:
                return True
        return False

    def column_examples(self, tname: str, col: str) -> str:
        try:
            cnt = self.con.execute(
                f"SELECT COUNT(DISTINCT {_quote_ident(col)}) FROM {tname}"
            ).fetchone()[0]
            if cnt and cnt <= 30:
                rs = self.con.execute(
                    f"SELECT DISTINCT {_quote_ident(col)} FROM {tname} "
                    f"WHERE {_quote_ident(col)} IS NOT NULL LIMIT 30"
                ).fetchall()
                return json.dumps([r[0] for r in rs], ensure_ascii=False)
        except Exception:
            return ""
        return ""


# ----------------------------- LLM 辅助 -----------------------------
def _get_llm_nl2sql() -> Any:
    return LLMFactory.create_llm(LLMConfig(
        provider=settings.LLM_PROVIDER, api_key=settings.API_KEY,
        base_url=settings.LLM_BASE_URL, model=settings.LLM_MODEL,
        temperature=0.0, max_tokens=1500,
    ))


def _get_llm_explain() -> Any:
    return LLMFactory.create_llm(LLMConfig(
        provider=settings.LLM_PROVIDER, api_key=settings.API_KEY,
        base_url=settings.LLM_BASE_URL, model=settings.LLM_MODEL,
        temperature=0.3, max_tokens=2000,
    ))


def _extract_sql_json(text: str) -> str:
    t = (text or "").strip()
    if not t:
        raise ValueError("LLM 未返回有效 JSON（空响应）")
    # 1) 优先剥离 markdown 代码围栏（```json ... ``` 或 ``` ... ```）
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", t, re.DOTALL)
    if fence:
        t = fence.group(1).strip()
    # 2) 定位首个 '{'，用 JSONDecoder.raw_decode 容忍前后的说明文字，
    #    并严格按 JSON 规范解析（SQL 字符串内部的 '{' / '}' 视为字面量，不会被误截断）。
    idx = t.find("{")
    if idx == -1:
        raise ValueError("LLM 未返回有效 JSON（缺少 {）")
    try:
        obj, _ = json.JSONDecoder().raw_decode(t[idx:])
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM 返回的 JSON 无法解析：{e}")
    if not isinstance(obj, dict) or "sql" not in obj:
        raise ValueError("JSON 缺少 sql 字段")
    sql = obj.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("JSON 中的 sql 字段为空或非法")
    return sql.strip().rstrip(";")


def _format_result_table(cols: List[str], rows: List[tuple]) -> str:
    if not cols:
        return "(无列)"
    lines = [" | ".join(str(c) for c in cols)]
    for r in rows:
        lines.append(" | ".join("" if v is None else str(v) for v in r))
    return "\n".join(lines)


def _json_safe(v: Any) -> Any:
    """将 DuckDB 返回的非 JSON 原生类型（Decimal/datetime/bytes）转为可序列化值。"""
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        return [_json_safe(x) for x in v]
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, bytes):
        return v.decode("utf-8", "ignore")
    return v


# ----------------------------- SQL 安全校验 -----------------------------
def _wrap_with_limit(sql: str, max_rows: int):
    """把查询包成 ``SELECT * FROM (<sql>) LIMIT (max_rows+1)`` 的哨兵包裹。

    仅当顶层是 SELECT/UNION 这类查询表达式时安全：GROUP BY / ORDER BY / UNION /
    子查询 / 聚合都保留在子查询内部，只在外层追加一个 LIMIT，不会破坏任何既有语义。
    WITH/CTE、VALUES、以括号开头的表达式无法安全包裹，返回 ``None``
    （调用方改用 fetchmany 哨兵做返回上限保护）。
    """
    s = sql.strip()
    if s.endswith(";"):
        s = s[:-1].strip()
    up = s.upper()
    # 顶层必须是 SELECT（含 UNION 串联的 SELECT）；WITH/CTE 不能包裹
    if not up.startswith("SELECT"):
        return None
    return f'SELECT * FROM ({s}) AS __tq_cap LIMIT {max_rows + 1}'


# ----------------------------- SQL 安全校验（AST 基础） -----------------------------
# 基于 sqlglot（DuckDB dialect）的 AST 解析，作为 SQL 安全校验的可靠基础：
#  - 仅允许只读 SELECT / 含 CTE 的查询（WITH ... SELECT）
#  - 所有物理表引用必须 ∈ allowed，递归覆盖 CTE/子查询/JOIN/UNION，并区分 CTE 名与物理表名
#  - 禁止通过表函数（read_csv 等）、文件字面量（FROM 'x.csv'）、扫描函数（sqlite_scan 等）访问外部数据源
#  - 允许无外部 I/O 的安全生成器表函数（range/generate_series/unnest 等，用于合法笛卡尔积/展开）
#  - 禁止 INSERT/UPDATE/DELETE/DROP/CREATE/ALTER/ATTACH/DETACH/COPY/CALL/PRAGMA 等
#  - 禁止多语句；解析失败默认拒绝，绝不退回更宽松路径
_DANGEROUS_FUNC = {
    "readcsv", "readcsvauto", "readparquet", "readjson", "readtext", "readexcel",
    "readblob", "readiceberg", "sqlitescan", "postgresscan", "mysqlscan",
    "duckdbscan", "attachment", "pragmatableinfo", "pragmadatabaselist",
    "pragmafunctionlist", "pragmacollations", "attach", "detach", "copy",
}
# 无外部 I/O 的表值生成器（仅用于合法笛卡尔积 / 行展开，不访问任何数据源）
_SAFE_GENERATOR_FUNCS = {
    "range", "generateseries", "generate_series", "unnest", "repeat",
    "stringsplit", "stringtoarray", "flatten", "regexpsplittotable", "explode",
}
_FILE_EXTS = (
    ".csv", ".parquet", ".parq", ".json", ".xlsx", ".xls", ".db",
    ".duckdb", ".txt", ".tsv", ".gz", ".orc", ".avro",
)


def _norm_ident(name: str) -> str:
    """归一化标识符用于安全集合匹配（去非字母数字、转小写）。"""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _func_call_name(node) -> str:
    """提取函数调用名：具名函数取类名，匿名函数取首标识符。"""
    if isinstance(node, exp.Anonymous):
        this = node.this
        if isinstance(this, exp.Identifier):
            return this.name
        return str(this)
    return type(node).__name__


def _rep_data_version(rep: Dict[str, Any]) -> str:
    """对 Representation 的业务数据做确定性指纹（sha256 前 16 位）。

    仅基于 workbook 的表结构 + 行数据，反映「实际查询返回的内容」。
    文件名 / 描述变化不影响指纹；行列数据变化必然改变指纹。
    用于 Table Query Cache Key 的 scope_version，使文档内容更新后旧缓存自动失效。
    """
    import hashlib
    import json

    canonical = json.dumps(
        rep.get("workbook", {}), sort_keys=True, ensure_ascii=False, default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _normalize_sql(sql: str) -> str:
    """对 SQL 做语义归一用于缓存键：小写化关键字、统一空白、规范结构。

    若解析失败则回退为原 SQL（缓存粒度变粗但不影响正确性）。
    注意：sqlglot 的 normalize 只做格式化，绝不合并语义不同的 SQL。
    """
    try:
        return sqlglot.parse_one(sql, dialect="duckdb").sql(
            dialect="duckdb", normalize=True
        )
    except Exception:  # noqa: BLE001
        return sql


def validate_sql(sql: str, allowed: List[str]) -> None:
    s = (sql or "").strip()
    if not s:
        raise ValueError("SQL 为空")
    # 解析：语法错误 / 不支持语法 / 歧义一律拒绝（绝不退回宽松路径）
    try:
        statements = sqlglot.parse(s, dialect="duckdb")
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"SQL 解析失败，已拒绝执行：{exc}")
    statements = [st for st in statements if st is not None]
    if len(statements) != 1:
        raise ValueError("不允许多条 SQL 语句")
    tree = statements[0]
    # 顶层只允许只读查询（WITH...SELECT 的 root 是 Select，with 为其属性）
    if not isinstance(tree, (exp.Select, exp.Union)):
        raise ValueError("只允许 SELECT 查询（含 WITH/CTE）")

    allowed_set = {a.lower() for a in allowed}
    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}

    # 危险函数 / 外部数据源函数（含作为表达式使用时）
    for node in tree.walk():
        if isinstance(node, (exp.Func, exp.Anonymous)):
            if _norm_ident(_func_call_name(node)) in _DANGEROUS_FUNC:
                raise ValueError(f"禁止的 SQL 函数/数据源：{_func_call_name(node)}")

    # 递归检查所有物理表引用（FROM / JOIN / 子查询 / UNION 分支）
    referenced_tables = set()
    for table in tree.find_all(exp.Table):
        name = table.name or ""
        # 跨 schema / catalog 引用一律禁止
        if table.db or table.catalog:
            raise ValueError(f"禁止跨 schema/catalog 引用：{name}")
        low = name.lower()
        # CTE 名视为派生表，非物理表（其定义内部已递归检查）
        if low in cte_names:
            continue
        # 空 name 表源：必须是安全的生成器表函数，否则视为外部数据源
        if not name:
            src = table.this
            if isinstance(src, (exp.Func, exp.Anonymous)) and \
               _norm_ident(_func_call_name(src)) in _SAFE_GENERATOR_FUNCS:
                continue
            raise ValueError("禁止引用外部数据源或表函数")
        # 文件路径 / 外部数据文件字面量
        if "/" in name or "\\" in name or low.endswith(_FILE_EXTS):
            raise ValueError(f"禁止引用外部数据源：{name}")
        if low not in allowed_set:
            raise ValueError(f"引用了未授权的表：{name}")
        referenced_tables.add(low)

    if not referenced_tables:
        raise ValueError("SQL 未引用任何允许的表")


# ----------------------------- 查询范围 / 来源推导（保证一致） -----------------------------
def _parse_referenced_tables(sql: str, allowed: List[str]) -> List[str]:
    """提取 SQL 实际引用的、且属于 allowed 白名单的物理表名。

    基于 sqlglot AST 遍历所有 exp.Table（兼容引号标识符 / 表别名 / 大小写变体 /
    非 hex 命名的真实表名），与 validate_sql 的口径完全一致；仅保留在 allowed 内的
    表。这是“实际查询范围”的事实来源：sources 与 answer 必须与之完全一致，
    杜绝“SQL 只查一张表、sources 却报全部表”的不一致。
    """
    try:
        stmt = sqlglot.parse_one(sql, dialect="duckdb")
    except Exception:  # noqa: BLE001
        return []
    allowed_set = {a.lower() for a in allowed}
    seen: List[str] = []
    for table in stmt.find_all(exp.Table):
        name = (table.name or "").lower()
        if name and name in allowed_set and name not in seen:
            seen.append(name)
    return seen


# ----------------------------- 语义值解析（schema-aware，确定性） -----------------------------
class ValueResolutionAmbiguous(ValueError):
    """等值过滤字面量同时匹配多个候选枚举值，需用户澄清（不擅自猜测）。"""


def _norm_value(v: Any) -> str:
    """归一化枚举值：去首尾空格、折叠内部空白、转小写，用于确定性与缩写匹配。"""
    return re.sub(r"\s+", " ", str(v).strip().lower())


# 匹配等值比较右操作数：支持 `= 'X'` 与 `= LOWER(TRIM('X'))` 两种 NL2SQL 常见写法。
# 负向后顾 `(?<![<>=!])` 排除 `>=` / `<=` / `!=` / `==`，避免误伤比较/不等运算符。
# 仅捕获字面量内容；匹配到唯一候选时整段替换为 `= '候选值'`（候选为存储原值，精确匹配即可）。
_EQ_LITERAL = re.compile(
    r"(?<![<>=!])=(?:\s*LOWER\(\s*TRIM\()?\s*'((?:[^']|'')*)'\s*\)?\s*",
    re.IGNORECASE,
)


def _parse_referenced_columns(sql: str, exclude=None) -> List[str]:
    """提取 SQL 中以双引号包裹的业务列名（排除 __ 源列，以及物理表名）。

    exclude 用于传入 allowed / compatible 等物理表名集合，避免把引号包裹的
    表名（如 FROM "t_xxx_0"）误判为业务列，否则会导致跨表兼容判断失效。
    """
    cols = re.findall(r'"([^"]+)"', sql)
    bad = set(exclude) if exclude else set()
    return [c for c in cols if not c.startswith(SRC_PREFIX) and c not in bad]


def _rewrite_cross_table(sql: str, t0: str, compatible: List[str]) -> Optional[str]:
    """将单表查询改写为对 compatible 表的 UNION ALL 派生表。

    仅当 SQL 仅 FROM 一个表、且显式引用了业务列时适用；用于 document_id=None
    （或单文档多表）下对“结构兼容且语义相同”的多个表做统一聚合/筛选。
    返回改写后的 SQL；无法安全改写则返回 None（回退为单表）。
    """
    ref_cols = _parse_referenced_columns(sql, compatible)
    if not ref_cols:
        # 未显式引用业务列（如纯 COUNT(*)/SELECT *）：不强行跨表，保持单表，避免语义漂移
        return None
    proj = ", ".join(_quote_ident(c) for c in ref_cols)
    subs = " UNION ALL ".join(
        f"(SELECT {proj} FROM {_quote_ident(t)})" for t in compatible
    )
    # 兼容带引号（NL2SQL 生成的表名常带双引号）与不带引号的表名引用；
    # 用精确字符串变体替换，避免正则引号边界处理带来的残留引号问题
    for variant in (f'FROM "{t0}"', f"FROM '{t0}'", f"FROM {t0}"):
        if variant in sql:
            return sql.replace(variant, f"FROM ({subs}) AS __u", 1)
    return None


# ----------------------------- 受控值匹配兜底（精确优先） -----------------------------
def _rewrite_eq(sql: str, mode: str) -> str:
    """仅对 "col" = 'val' 形式的字符串等值条件做改写。数值等值不受影响。"""
    pat = re.compile(r'("(?:[^"]+)")\s*=\s*\'((?:[^\'\\]|\\.)*)\'')

    def repl(m: re.Match) -> str:
        col = m.group(1)
        val = m.group(2).replace("%", "\\%").replace("_", "\\_")
        if mode == "normalized":
            return f"LOWER(TRIM({col})) = LOWER(TRIM('{val}'))"
        # fuzzy：受控子串匹配，明确标注 match_mode
        return f"LOWER({col}) LIKE '%' || LOWER('{val}') || '%'"

    return pat.sub(repl, sql)


# ----------------------------- 精确查询服务 -----------------------------
class TableQueryService:
    def __init__(self):
        # 每实例独占一个 in-memory DuckDB 连接；请求级生命周期由调用方 close()。
        self.engine = DuckDBTableEngine()

    def close(self) -> None:
        self.engine.close()

    def __enter__(self) -> "TableQueryService":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- 数据加载（Representation -> DuckDB） ----
    def _load_user_representations(self, user_id: Any) -> List[Dict[str, Any]]:
        from backend.services.table_representation import table_store_dir
        d = table_store_dir()
        reps: List[Dict[str, Any]] = []
        if not d.exists():
            return reps
        for p in d.glob("*.json"):
            try:
                rep = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if str(rep.get("user_id")) == str(user_id):
                reps.append(rep)
        return reps

    def list_user_table_documents(self, user_id: Any) -> List[Dict[str, Any]]:
        """列出当前用户可被 Table Query 实际查询的表格文档（数据源=Table Representation 文件系统）。

        与 Phase 2 查询引擎（document_id=None 时的可查询集合）共用同一枚举逻辑
        （_load_user_representations 按 user_id 过滤），确保“下拉框集合 == 实际可查询集合”，
        不再依赖 Chroma 文档列表。只返回有效表格类型（xlsx/xls/csv/tsv），
        按 filename 稳定排序（filename 相同再退而用 document_id）。
        不读取其他用户数据、不修改 representation 文件、不新建数据库表。
        """
        valid_types = {ext.lstrip(".") for ext in TABLE_EXTENSIONS}
        docs: List[Dict[str, Any]] = []
        for rep in self._load_user_representations(user_id):
            ft = (rep.get("file_type") or "").lower()
            if ft not in valid_types:
                continue
            docs.append({
                "document_id": rep.get("document_id"),
                "filename": rep.get("filename") or rep.get("original_filename") or "未知文件",
                "type": ft,
            })
        docs.sort(key=lambda d: ((d["filename"] or "").lower(), d["document_id"] or ""))
        return docs

    def ensure_user_tables(self, user_id: Any) -> None:
        for rep in self._load_user_representations(user_id):
            self.engine.register_representation(rep, user_id)

    def load_from_file(self, file_path: str, user_id: Any, filename: Optional[str] = None) -> str:
        """测试/直接查询：从文件路径构建 Representation（只读打开原文件，不写 table_originals）。"""
        ext = os.path.splitext(file_path)[1].lower()
        if ext not in TABLE_EXTENSIONS:
            raise ValueError(f"不支持的表格类型：{ext}")
        document_id = "file_" + os.urandom(8).hex()
        rep = build_representation(
            file_path, document_id, user_id,
            filename or os.path.basename(file_path), ext.lstrip("."), file_path,
        )
        self.engine.register_representation(rep, user_id)
        return document_id

    # ---- Schema Context ----
    def _build_schema_context(self, allowed: List[str]) -> Tuple[str, str]:
        parts: List[str] = []
        for tname in allowed:
            meta = self.engine.table_meta.get(tname)
            if not meta:
                continue
            parts.append(
                f"\n表 {tname} （文件《{meta['filename']}》Sheet《{meta['sheet_name']}》）:"
            )
            for c in meta["columns"]:
                line = f"  - {_quote_ident(c['name'])} {c['type']}"
                if c.get("desc"):
                    line += f" （{c['desc']}）"
                parts.append(line)
            for c in meta["columns"]:
                if c["type"] == "VARCHAR":
                    examples = self.engine.column_examples(tname, c["name"])
                    if examples:
                        parts.append(f"    取值示例({c['name']}): {examples}")
        alias_lines = [
            f'“{zh}”通常指列 {_quote_ident(en)}' for zh, en in COLUMN_ALIASES.items()
        ] + [
            f'用户说“{zh}”通常对应值 {en!r}' for zh, en in VALUE_ALIASES.items()
        ]
        return "\n".join(parts), "\n".join(alias_lines)

    # ---- NL2SQL ----
    async def _nl2sql(self, question: str, schema: str, alias: str, allowed: List[str]) -> str:
        prompt = (
            "你是一个把自然语言转换为 DuckDB SQL 的助手。\n"
            "只允许查询下列表（不要使用其他表名）：\n"
            f"{schema}\n\n"
            "术语提示：\n"
            f"{alias}\n\n"
            "规则：\n"
            '- 只输出一个 JSON：{"sql": "SELECT ..."}\n'
            "- 金额与单价区分：问题含“订单金额/订单价格/总价/成交金额”时使用 Order Amount 列；"
            "含“SKU 单价/商品单价/原始单价/单位价格”时使用 SKU Unit Original Price 列；"
            "泛指“价格”且针对订单整体（如“价格最高的订单”）默认使用 Order Amount。\n"
            "- 字符串过滤统一写成 LOWER(TRIM(\"列名\")) = LOWER(TRIM('值')) 形式，单层括号严格成对，"
            "不要写成裸 '值' 或在语句末尾附加多余括号。\n"
            "- 支持 COUNT/SUM/AVG/MIN/MAX/GROUP BY/ORDER BY/LIMIT/WHERE\n"
            "- 问题涉及多张表时，可使用 JOIN / UNION / UNION ALL 跨表查询；"
            "列名用表别名限定（如 a.\"col\"）。UNION 会去重，UNION ALL 保留全部行；"
            "全量列举明细时优先 UNION ALL 以免丢失重复行\n"
            "- 禁止任何写操作；禁止多条语句；列名用双引号包裹\n"
            "- 只输出上述单个 JSON，确保 SQL 语法完整、括号严格成对、不要添加任何多余括号或注释；"
            "计数请统一使用 COUNT(*)，聚合使用 COUNT(列)/SUM(列)/AVG(列)/MIN(列)/MAX(列)，"
            "函数括号必须完整闭合。\n\n"
            f"问题：{question}\n"
        )
        llm = _get_llm_nl2sql()
        resp = ""
        async for chunk in llm.chat_completion(
            [{"role": "system", "content": "你是SQL生成助手"},
             {"role": "user", "content": prompt}],
            stream=False,
        ):
            resp += chunk
        return _extract_sql_json(resp)

    # ---- 执行 + 受控兜底 ----
    def _execute_sql(self, sql: str, cap_rows: bool = True):
        """执行单条 SQL，带超时保护（watchdog 线程 + con.interrupt）与可选的服务端行数上限。

        返回 ``(cols, rows, truncated)``：
        - ``truncated=True`` 表示原始结果超过 ``SQL_MAX_RESULT_ROWS`` 安全上限，已被截断。
        - 超时：触发 ``con.interrupt()`` 取消查询；DuckDB 单例连接可继续复用，
          不会留下仍在执行的查询，也不会污染下一次 Table QA；
          并以 ``ValueError`` 明确上抛，供上层识别为错误（绝不静默继续）。
        """
        con = self.engine.con
        timeout = settings.SQL_QUERY_TIMEOUT
        done = threading.Event()
        watch = None
        if timeout and timeout > 0:
            def _watch() -> None:
                if not done.wait(timeout):
                    try:
                        con.interrupt()
                    except Exception:  # noqa: BLE001
                        pass
            watch = threading.Thread(target=_watch, daemon=True)
            watch.start()
        try:
            wrapped = _wrap_with_limit(sql, settings.SQL_MAX_RESULT_ROWS) if cap_rows else sql
            sentinel = settings.SQL_MAX_RESULT_ROWS + 1
            if wrapped is not None:
                cur = con.execute(wrapped)
                cols = [d[0] for d in cur.description] if cur.description else []
                rows = cur.fetchall()
                if len(rows) >= sentinel:
                    # 命中哨兵 -> 原始结果超过安全上限，截断并标记
                    return cols, rows[: settings.SQL_MAX_RESULT_ROWS], True
                return cols, rows, False
            # 无法包裹（WITH/CTE 等）：以 fetchmany 哨兵做返回上限保护
            cur = con.execute(sql)
            cols = [d[0] for d in cur.description] if cur.description else []
            first = cur.fetchmany(sentinel)
            if len(first) >= sentinel:
                return cols, list(first)[: settings.SQL_MAX_RESULT_ROWS], True
            rest = cur.fetchall()
            return cols, list(first) + list(rest), False
        except duckdb.InterruptException:
            raise ValueError(f"SQL 执行超时（超过 {timeout:g} 秒），查询已终止")
        finally:
            done.set()
            if watch is not None:
                watch.join(timeout=max(timeout, 0) + 5)

    # ---- 语义值解析：把自然语言简称/缩写映射到表中真实枚举值 ----
    def _value_candidates(self, literal: str, ref_tables: List[str]) -> List[str]:
        """在允许访问的引用表（user/document 隔离内）的低基数 VARCHAR 列中，
        找出与字面量匹配的候选枚举值（确定性，不调用 LLM）。

        匹配规则（归一化后）：
        - 完全相等；或
        - 候选以 `字面量 + 空格` 开头（缩写形式，如 'sf' -> 'sf international'）；或
        - 字面量是候选的某个 token（如 'sf' -> 'sf international'）。
        仅扫描 column_examples 返回的列（distinct <= 30），避免把高基数列
        （SKU / tracking / order id 等）全量拉入解析。
        """
        nlit = _norm_value(literal)
        if not nlit:
            return []
        cands: set = set()
        for t in ref_tables:
            meta = self.engine.table_meta.get(t)
            if not meta:
                continue
            for c in meta["columns"]:
                if c["type"] != "VARCHAR":
                    continue
                ex = self.engine.column_examples(t, c["name"])
                if not ex:
                    continue  # 高基数或出错 -> 跳过，不解析
                for dv in json.loads(ex):
                    nd = _norm_value(dv)
                    if nd == nlit or nd.startswith(nlit + " ") or nlit in nd.split(" "):
                        cands.add(dv)
        return list(cands)

    def _resolve_sql_values(self, sql: str, ref_tables: List[str]) -> str:
        """对 SQL 中等值过滤的字面量做 schema-aware 值解析。

        - 唯一候选 -> 改写为真实存储值（精确匹配，绕过 LLM 的缩写盲区）。
        - 多个候选 -> 抛出 ValueResolutionAmbiguous（交由上层转 AMBIGUOUS 澄清）。
        - 无候选 -> 保持原样（不强行映射；查询可能自然返回空，由解释层告知未找到）。

        candidate 仅来自 ref_tables（当前 user/document 允许访问的表），不跨用户/文档。
        """
        def repl(m: "re.Match") -> str:
            lit = m.group(1)
            cands = self._value_candidates(lit, ref_tables)
            if len(cands) == 1:
                val = cands[0].replace("'", "''")
                return f"= '{val}'"
            if len(cands) > 1:
                raise ValueResolutionAmbiguous(
                    f"值『{lit}』匹配到多个候选：{sorted(cands)}，请明确指定具体值。"
                )
            return m.group(0)

        return _EQ_LITERAL.sub(repl, sql)

    def _execute_with_fallback(self, sql: str) -> Tuple[List[str], List[tuple], str, bool]:
        cols, rows, truncated = self._execute_sql(sql, cap_rows=True)
        if rows:
            return cols, rows, "exact", truncated
        norm_sql = _rewrite_eq(sql, "normalized")
        if norm_sql != sql:
            try:
                cols, rows2, tr2 = self._execute_sql(norm_sql, cap_rows=True)
                if rows2:
                    return cols, rows2, "normalized", tr2
            except Exception:  # noqa: BLE001
                pass
        fuzzy_sql = _rewrite_eq(sql, "fuzzy")
        if fuzzy_sql != sql:
            try:
                cols, rows3, tr3 = self._execute_sql(fuzzy_sql, cap_rows=True)
                if rows3:
                    return cols, rows3, "fuzzy", tr3
            except Exception:  # noqa: BLE001
                pass
        return cols, rows, "exact-not-found", truncated

    def _derive_sources(self, queried: List[str], cols: List[str], rows: List[tuple]) -> List[Dict[str, Any]]:
        idx = {c: i for i, c in enumerate(cols)}
        if "__filename" in idx and rows:
            seen = set()
            out: List[Dict[str, Any]] = []
            for r in rows:
                key = (r[idx.get("__filename")], r[idx.get("__sheet_name")],
                       r[idx.get("__row_index")], r[idx.get("__range")])
                if key not in seen:
                    seen.add(key)
                    out.append({
                        "document_id": r[idx.get("__document_id")],
                        "filename": r[idx.get("__filename")],
                        "sheet_name": r[idx.get("__sheet_name")],
                        "row_index": r[idx.get("__row_index")],
                        "range": r[idx.get("__range")],
                    })
            return out[:50]
        # 仅返回“实际被查询到的表”，绝不过度上报为当前用户的全部表
        out = []
        for t in queried:
            m = self.engine.table_meta.get(t)
            if m:
                out.append({
                    "document_id": m["document_id"], "filename": m["filename"],
                    "sheet_name": m["sheet_name"],
                    "table_id": None, "range": None, "row_index": None,
                })
        return out

    def _allowed(self, user_id: Any, document_id: Optional[str]) -> List[str]:
        self.ensure_user_tables(user_id)
        uid = str(user_id)
        # 无论 document_id 是否为空，白名单都必须严格属于当前 user_id，
        # 杜绝进程级单例 engine 中其他用户遗留表被纳入（跨用户数据暴露）。
        tables = [
            t for t in self.engine.table_meta.keys()
            if self.engine.table_meta.get(t, {}).get("user_id") == uid
        ]
        if document_id:
            tables = [t for t in tables
                      if self.engine.table_meta.get(t, {}).get("document_id") == document_id]
        return tables

    def _compatible_tables(self, allowed: List[str], ref_cols: List[str]) -> List[str]:
        """在 allowed 中筛选：同时包含全部 ref_cols 且这些列类型一致的表（UNION 兼容）。

        document_id=None（或全部表>1）时，仅把“结构兼容、语义相同”的表并入跨表查询；
        列缺失或类型不一致（如一张 VARCHAR、一张 DOUBLE）的表会被排除，绝不强行 UNION。
        """
        pairs: List[Tuple[str, Dict[str, str]]] = []
        for t in allowed:
            m = self.engine.table_meta.get(t)
            if not m:
                continue
            cmap = {c["name"]: c["type"] for c in m["columns"]}
            if all(col in cmap for col in ref_cols):
                pairs.append((t, cmap))
        if not pairs:
            return []
        ref_types = {col: pairs[0][1][col] for col in ref_cols}
        return [t for t, cmap in pairs
                if all(cmap.get(col) == ref_types[col] for col in ref_cols)]

    # ---- Table Query 结果缓存辅助（性能优化，非正确性依赖）----
    def _scope_version(self, allowed: List[str]) -> str:
        """基于所有 allowed 表的 rep_version 组合出数据版本指纹。
        任一表内容变化都会改变指纹，使对应缓存自动 MISS。
        """
        import hashlib

        vers = []
        for t in allowed:
            meta = self.engine.table_meta.get(t)
            vers.append(meta.get("rep_version", "unknown") if meta else "missing")
        return hashlib.sha256("|".join(sorted(vers)).encode()).hexdigest()[:16]

    async def _try_cache_get(self, cache, key: str):
        try:
            val = await cache.get(key)
            if isinstance(val, tuple) and len(val) == 4:
                return val
            return None
        except Exception:  # noqa: BLE001
            logger.warning("Table query cache read failed key=%s", key)
            return None

    async def _try_cache_set(self, cache, key: str, value) -> None:
        try:
            await cache.set(key, value, ttl=settings.TABLE_QUERY_CACHE_TTL)
        except Exception:  # noqa: BLE001
            logger.warning("Table query cache write failed key=%s", key)

    async def _pipeline(self, user_id: Any, question: str, document_id: Optional[str]):
        allowed = self._allowed(user_id, document_id)
        if not allowed:
            raise ValueError("当前用户没有可查询的表格文档")
        schema, alias = self._build_schema_context(allowed)
        sql = await self._nl2sql(question, schema, alias, allowed)
        validate_sql(sql, allowed)

        # 以“SQL 实际引用的表”作为查询范围事实来源（杜绝 sources 过度上报）
        ref_tables = _parse_referenced_tables(sql, allowed)
        queried = list(ref_tables) or list(allowed)

        ref_cols = _parse_referenced_columns(sql, allowed)
        # document_id=None（或全部兼容表 >1）时：若 LLM 仅引用单表、但存在结构兼容的同语义表，
        # 改写为 UNION ALL，使“实际 SQL = 查询范围 = sources = answer 语义”四者完全一致；
        # 若仅单表可用或不兼容，则保持单表，sources 只报实际查询到的那张表。
        if len(ref_tables) == 1 and ref_cols:
            compatible = self._compatible_tables(allowed, ref_cols)
            if len(compatible) > 1:
                t0 = ref_tables[0]
                rewritten = _rewrite_cross_table(sql, t0, compatible)
                if rewritten is not None:
                    validate_sql(rewritten, allowed)  # 二次安全校验：仅引用 allowed 内表
                    sql = rewritten
                    queried = list(compatible)

        # schema-aware 值解析：把自然语言简称/缩写映射到表中真实枚举值
        # （仅改写等值过滤字面量；多候选交由上层澄清，无候选保持原样）。
        sql = self._resolve_sql_values(sql, queried)

        # 纵深防御：值解析会改写 SQL 文本（即使仅替换字面量值），任何最终进入
        # cache / DuckDB 的 SQL 都必须再次经过 AST 校验，杜绝“修改后未重新校验”窗口。
        validate_sql(sql, allowed)

        # ---- Table Query 结果缓存（性能优化，非正确性依赖）----
        # 安全前置已完成：validate_sql 已在 NL2SQL 后、crosstable 改写后、值解析后
        # 三次运行（见上文）；Cache Key 含 user_id / document_id / allowed_tables /
        # normalized_sql / 数据版本指纹，故跨用户 / 跨文档 / 跨表范围 / 不同 SQL /
        # 文档内容更新 都不会错误命中。缓存命中绝不跳过 AST 校验。
        cache = get_cache_service()
        cache_key = CacheKey.table_query(
            user_id, document_id, allowed,
            _normalize_sql(sql), self._scope_version(allowed),
        )
        cached = await self._try_cache_get(cache, cache_key)
        if cached is not None:
            logger.info(
                "Table query cache HIT user=%s doc=%s key=%s",
                user_id, document_id, cache_key,
            )
            cols, rows, match_mode, truncated = cached
            sources = self._derive_sources(queried, cols, rows)
            return sql, cols, rows, match_mode, sources, truncated

        cols, rows, match_mode, truncated = self._execute_with_fallback(sql)
        sources = self._derive_sources(queried, cols, rows)

        await self._try_cache_set(cache, cache_key, (cols, rows, match_mode, truncated))
        logger.info(
            "Table query cache MISS user=%s doc=%s key=%s",
            user_id, document_id, cache_key,
        )
        return sql, cols, rows, match_mode, sources, truncated

    # ---- 解释 ----
    def _explain_prompt(self, question, sql, cols, rows, sources, result_limited=False) -> str:
        shown = rows[:100] if rows else []
        table_str = _format_result_table(cols, shown)
        src_str = "; ".join(
            f"《{s.get('filename')}》Sheet《{s.get('sheet_name')}》" for s in sources
        ) or "当前表"
        note = ""
        if result_limited:
            note = (
                f"\n\n【重要】本次查询结果超过安全上限（最多展示 {settings.SQL_MAX_RESULT_ROWS} 行），"
                f"当前展示的为前 {len(shown)} 行，并非完整结果；"
                f"回答时必须明确告知用户“结果因超过安全上限被限制，仅展示部分数据”，"
                f"绝不能把部分结果说成完整结果。"
            )
        return (
            f"用户问题：{question}\n"
            f"执行的SQL：{sql}\n"
            f"数据来源：{src_str}\n"
            f"查询结果（共{len(rows) if rows is not None else 0}行，以下展示前{len(shown)}行）：\n"
            f"{table_str}\n{note}\n\n"
            "请用简体中文回答用户，说明关键数字与结论，并说明数据来源文件与Sheet；"
            "如果结果为空，明确告知未找到匹配数据。"
        )

    async def _explain_text(self, question, sql, cols, rows, sources, result_limited=False) -> str:
        prompt = self._explain_prompt(question, sql, cols, rows, sources, result_limited)
        llm = _get_llm_explain()
        out = ""
        async for t in llm.chat_completion(
            [{"role": "system", "content": "你是数据分析助手"},
             {"role": "user", "content": prompt}],
            stream=False,
        ):
            out += t
        return out

    async def _explain_stream(self, question, sql, cols, rows, sources, result_limited=False) -> AsyncGenerator[str, None]:
        prompt = self._explain_prompt(question, sql, cols, rows, sources, result_limited)
        llm = _get_llm_explain()
        async for t in llm.chat_completion(
            [{"role": "system", "content": "你是数据分析助手"},
             {"role": "user", "content": prompt}],
            stream=True,
        ):
            yield t

    # ---- 对外接口 ----
    async def run_query(self, user_id: Any, question: str, document_id: Optional[str] = None) -> Dict[str, Any]:
        sql, cols, rows, match_mode, sources, truncated = await self._pipeline(user_id, question, document_id)
        explanation = await self._explain_text(question, sql, cols, rows, sources, truncated)
        return {
            "sql": sql, "match_mode": match_mode, "columns": cols,
            "rows": [_json_safe(list(r)) for r in rows],
            "explanation": explanation, "sources": sources,
            "result_limited": truncated,
        }

    async def stream_query(self, user_id: Any, question: str, document_id: Optional[str] = None) -> AsyncGenerator[str, None]:
        sql, cols, rows, match_mode, sources, truncated = await self._pipeline(user_id, question, document_id)
        async for text in self._explain_stream(question, sql, cols, rows, sources, truncated):
            yield json.dumps(
                {"type": "chunk", "content": text, "timestamp": datetime.now().isoformat()}
            ) + "\n"
        yield json.dumps({
            "type": "complete", "content": "", "sql": sql, "match_mode": match_mode,
            "row_count": len(rows or []), "sources": sources,
            "result_limited": truncated,
            "timestamp": datetime.now().isoformat(),
        }) + "\n"
