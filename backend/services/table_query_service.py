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
from datetime import datetime, date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple, AsyncGenerator

import duckdb

from backend.config.settings import settings
from backend.services.table_representation import (
    load_representation,
    build_representation,
    TABLE_EXTENSIONS,
)
from backend.services.llm_service import LLMFactory, LLMConfig

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


# ----------------------------- DuckDB 引擎（单例 in-memory） -----------------------------
class DuckDBTableEngine:
    _instance = None

    def __init__(self):
        self.con = duckdb.connect(database=":memory:")
        self.table_meta: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def get(cls) -> "DuckDBTableEngine":
        if cls._instance is None:
            cls._instance = DuckDBTableEngine()
        return cls._instance

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

    def register_one(self, doc_id, user_id, filename, sheet_name, table, sheet_idx):
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
            "sheet_name": sheet_name, "columns": biz,
        }

    def register_representation(self, rep: Dict[str, Any], user_id: Any):
        doc_id = rep.get("document_id")
        filename = rep.get("filename")
        for si, sheet in enumerate(rep.get("workbook", {}).get("sheets", [])):
            for table in sheet.get("tables", []):
                self.register_one(doc_id, user_id, filename, sheet.get("sheet_name"), table, si)

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
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError("LLM 未返回有效 JSON")
    obj = json.loads(m.group(0))
    sql = obj.get("sql")
    if not sql:
        raise ValueError("JSON 缺少 sql 字段")
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
def validate_sql(sql: str, allowed: List[str]) -> None:
    s = sql.strip()
    if s.endswith(";"):
        s = s[:-1].strip()
    if ";" in s:
        raise ValueError("不允许多条 SQL 语句")
    up = s.upper()
    if not (up.startswith("SELECT") or up.startswith("WITH")):
        raise ValueError("只允许 SELECT 查询")
    forbidden = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "ATTACH",
                 "LOAD", "INSTALL", "COPY", "MERGE", "TRUNCATE", "GRANT", "EXEC",
                 "PRAGMA", "REPLACE", "VACUUM"]
    for kw in forbidden:
        if re.search(r"\b" + kw + r"\b", up):
            raise ValueError(f"禁止的 SQL 操作: {kw}")
    low = s.lower()
    if not any(t.lower() in low for t in allowed):
        raise ValueError("SQL 未引用任何允许的表")
    # 禁止引用非允许表（表名形如 t_<hex>_<idx>）
    for m in re.findall(r"\b(t_[a-f0-9]+_\d+)\b", low):
        if m not in allowed:
            raise ValueError(f"引用了不允许的表: {m}")


# ----------------------------- 查询范围 / 来源推导（保证一致） -----------------------------
def _parse_referenced_tables(sql: str, allowed: List[str]) -> List[str]:
    """提取 SQL 实际引用的、且属于 allowed 白名单的表名（t_<hex>_<idx>）。

    这是“实际查询范围”的唯一事实来源：sources 与 answer 必须与之完全一致，
    杜绝“SQL 只查一张表、sources 却报全部表”的不一致。
    """
    found = re.findall(r"\b(t_[a-f0-9]+_\d+)\b", sql.lower())
    seen: List[str] = []
    for t in found:
        if t in allowed and t not in seen:
            seen.append(t)
    return seen


def _parse_referenced_columns(sql: str) -> List[str]:
    """提取 SQL 中以双引号包裹的业务列名（排除 __ 源列）。"""
    cols = re.findall(r'"([^"]+)"', sql)
    return [c for c in cols if not c.startswith(SRC_PREFIX)]


def _rewrite_cross_table(sql: str, t0: str, compatible: List[str]) -> Optional[str]:
    """将单表查询改写为对 compatible 表的 UNION ALL 派生表。

    仅当 SQL 仅 FROM 一个表、且显式引用了业务列时适用；用于 document_id=None
    （或单文档多表）下对“结构兼容且语义相同”的多个表做统一聚合/筛选。
    返回改写后的 SQL；无法安全改写则返回 None（回退为单表）。
    """
    pat = re.compile(r"\bFROM\s+" + re.escape(t0) + r"\b", re.IGNORECASE)
    if not pat.search(sql):
        return None
    ref_cols = _parse_referenced_columns(sql)
    if not ref_cols:
        # 未显式引用业务列（如纯 COUNT(*)/SELECT *）：不强行跨表，保持单表，避免语义漂移
        return None
    proj = ", ".join(_quote_ident(c) for c in ref_cols)
    subs = " UNION ALL ".join(
        f"(SELECT {proj} FROM {_quote_ident(t)})" for t in compatible
    )
    return pat.sub(f"FROM ({subs}) AS __u", sql, count=1)


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
        self.engine = DuckDBTableEngine.get()

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
            "- 字符串使用单引号；精确匹配注意大小写，必要时用 LOWER(TRIM(col)) = LOWER(TRIM('值'))\n"
            "- 支持 COUNT/SUM/AVG/MIN/MAX/GROUP BY/ORDER BY/LIMIT/WHERE\n"
            "- 禁止任何写操作；禁止多条语句；列名用双引号包裹\n\n"
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
    def _execute_with_fallback(self, sql: str) -> Tuple[List[str], List[tuple], str]:
        cur = self.engine.con.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchall()
        if rows:
            return cols, rows, "exact"
        norm_sql = _rewrite_eq(sql, "normalized")
        if norm_sql != sql:
            try:
                rows2 = self.engine.con.execute(norm_sql).fetchall()
                if rows2:
                    return cols, rows2, "normalized"
            except Exception:
                pass
        fuzzy_sql = _rewrite_eq(sql, "fuzzy")
        if fuzzy_sql != sql:
            try:
                rows3 = self.engine.con.execute(fuzzy_sql).fetchall()
                if rows3:
                    return cols, rows3, "fuzzy"
            except Exception:
                pass
        return cols, rows, "exact-not-found"

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

        ref_cols = _parse_referenced_columns(sql)
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

        cols, rows, match_mode = self._execute_with_fallback(sql)
        sources = self._derive_sources(queried, cols, rows)
        return sql, cols, rows, match_mode, sources

    # ---- 解释 ----
    def _explain_prompt(self, question, sql, cols, rows, sources) -> str:
        shown = rows[:100] if rows else []
        table_str = _format_result_table(cols, shown)
        src_str = "; ".join(
            f"《{s.get('filename')}》Sheet《{s.get('sheet_name')}》" for s in sources
        ) or "当前表"
        return (
            f"用户问题：{question}\n"
            f"执行的SQL：{sql}\n"
            f"数据来源：{src_str}\n"
            f"查询结果（共{len(rows) if rows is not None else 0}行，以下展示前{len(shown)}行）：\n"
            f"{table_str}\n\n"
            "请用简体中文回答用户，说明关键数字与结论，并说明数据来源文件与Sheet；"
            "如果结果为空，明确告知未找到匹配数据。"
        )

    async def _explain_text(self, question, sql, cols, rows, sources) -> str:
        prompt = self._explain_prompt(question, sql, cols, rows, sources)
        llm = _get_llm_explain()
        out = ""
        async for t in llm.chat_completion(
            [{"role": "system", "content": "你是数据分析助手"},
             {"role": "user", "content": prompt}],
            stream=False,
        ):
            out += t
        return out

    async def _explain_stream(self, question, sql, cols, rows, sources) -> AsyncGenerator[str, None]:
        prompt = self._explain_prompt(question, sql, cols, rows, sources)
        llm = _get_llm_explain()
        async for t in llm.chat_completion(
            [{"role": "system", "content": "你是数据分析助手"},
             {"role": "user", "content": prompt}],
            stream=True,
        ):
            yield t

    # ---- 对外接口 ----
    async def run_query(self, user_id: Any, question: str, document_id: Optional[str] = None) -> Dict[str, Any]:
        sql, cols, rows, match_mode, sources = await self._pipeline(user_id, question, document_id)
        explanation = await self._explain_text(question, sql, cols, rows, sources)
        return {
            "sql": sql, "match_mode": match_mode, "columns": cols,
            "rows": [_json_safe(list(r)) for r in rows],
            "explanation": explanation, "sources": sources,
        }

    async def stream_query(self, user_id: Any, question: str, document_id: Optional[str] = None) -> AsyncGenerator[str, None]:
        sql, cols, rows, match_mode, sources = await self._pipeline(user_id, question, document_id)
        async for text in self._explain_stream(question, sql, cols, rows, sources):
            yield json.dumps(
                {"type": "chunk", "content": text, "timestamp": datetime.now().isoformat()}
            ) + "\n"
        yield json.dumps({
            "type": "complete", "content": "", "sql": sql, "match_mode": match_mode,
            "row_count": len(rows or []), "sources": sources,
            "timestamp": datetime.now().isoformat(),
        }) + "\n"
