"""Phase 2.3 Evaluation Runner。

严格走生产调用链：
  生产 TableQAService.classify  -> 路由（actual_intent）
  生产 TableQueryService.load_from_file -> 内存注册真实 Excel（不持久化、不污染 data/）
  生产 TableQueryService._pipeline     -> 真实 NL2SQL -> AST validate -> value resolution
                                          -> (cache) -> DuckDB execute -> sources
  生产 TableQueryService._explain_text -> 真实 LLM 生成自然语言答案

不修改任何生产代码；仅为评估而驱动现有生产方法。
"""
import asyncio
import time
import traceback
from typing import Any, Dict, List, Optional

from backend.services.table_query_service import TableQueryService, ValueResolutionAmbiguous
from backend.services.table_qa_service import TableQAService

from .dataset import REAL_XLSX, EVAL_USER, Case
from .metrics import evaluate_result, evaluate_answer, infer_failure


def _is_llm_error(exc: Exception) -> Optional[str]:
    """将 LLM/网络/配额类异常归类为 LLM_* 错误（独立于 NL2SQL 质量）。"""
    msg = f"{type(exc).__name__}: {exc}"
    low = msg.lower()
    mod = getattr(type(exc), "__module__", "") or ""
    if "httpx" in mod or "openai" in mod or "requests" in mod:
        if "timeout" in low or "timed out" in low:
            return "LLM_TIMEOUT"
        if "429" in low or "rate" in low or "ratelimit" in low:
            return "LLM_RATE_LIMIT"
        if any(k in low for k in ("401", "403", "unauthorized", "billing", "insufficient", "quota", "api key")):
            return "LLM_BILLING_ERROR"
        return "LLM_API_ERROR"
    if "timeout" in low or "timed out" in low:
        return "LLM_TIMEOUT"
    if "429" in low or "rate limit" in low:
        return "LLM_RATE_LIMIT"
    if any(k in low for k in ("billing", "insufficient", "quota", "api key", "unauthorized", "401", "403")):
        return "LLM_BILLING_ERROR"
    return None


def _classify_value_error(msg: str) -> str:
    if "没有可查询" in msg:
        return "NO_DOCUMENT"
    if "超时" in msg or "timeout" in msg.lower():
        return "SQL_TIMEOUT"
    if any(k in msg for k in ("校验", "白名单", "不允许", "禁止", "非法", "unauthorized table", "security")):
        return "SQL_VALIDATION_FAILED"
    return "EXECUTION_ERROR"


async def run_case(case: Case) -> Dict[str, Any]:
    start = time.time()
    rec: Dict[str, Any] = {
        "case_id": case.case_id,
        "question": case.question,
        "expected_intent": case.expected_intent,
        "tags": list(case.tags),
        "actual_intent": "UNKNOWN",
        "route_match": False,
        "sql_generation_success": None,
        "generated_sql": None,
        "referenced_tables": [],
        "referenced_columns": [],
        "validation_success": None,
        "value_resolution_success": None,
        "value_resolution_error": None,
        "execution_success": None,
        "execution_error": None,
        "actual_result": None,
        "actual_row_count": None,
        "actual_rows": None,
        "final_answer": None,
        "answer_match": None,
        "result_match": None,
        "row_count_match": None,
        "llm_error": None,
        "failure_category": "UNKNOWN",
        "elapsed_ms": 0.0,
        "skip_execution": case.skip_execution,
    }

    # ---- 1) 路由（生产 classify，无需数据）----
    try:
        dec = TableQAService().classify(case.question)
        rec["actual_intent"] = dec.intent.value
    except Exception as e:  # noqa: BLE001
        rec["actual_intent"] = "UNKNOWN"
    rec["route_match"] = (rec["actual_intent"] == case.expected_intent)

    if case.skip_execution:
        rec["failure_category"] = "ROUTE_ONLY" if rec["route_match"] else "ROUTE_MISMATCH"
        rec["elapsed_ms"] = (time.time() - start) * 1000
        return rec

    # ---- 2) 真实执行：内存注册真实 Excel + 生产 _pipeline ----
    tq = TableQueryService()
    try:
        doc_id = tq.load_from_file(REAL_XLSX, EVAL_USER)
    except Exception as e:  # noqa: BLE001
        rec["llm_error"] = f"REGISTER_ERROR: {e}"
        rec["failure_category"] = "UNKNOWN"
        rec["elapsed_ms"] = (time.time() - start) * 1000
        return rec

    try:
        sql, cols, rows, match_mode, sources, truncated = await tq._pipeline(
            EVAL_USER, case.question, doc_id
        )
        rec["sql_generation_success"] = True
        rec["generated_sql"] = sql
        rec["validation_success"] = True
        rec["value_resolution_success"] = True
        rec["execution_success"] = True
        rec["actual_row_count"] = len(rows)
        rec["actual_rows"] = rows
        rec["referenced_columns"] = list(cols)
        rec["referenced_tables"] = [s.get("document_id") for s in (sources or [])]
        if rows and rows[0]:
            rec["actual_result"] = rows[0][0]
    except ValueResolutionAmbiguous as e:
        rec["value_resolution_success"] = False
        rec["value_resolution_error"] = str(e)
        rec["sql_generation_success"] = True  # NL2SQL 已生成，仅值解析歧义
    except ValueError as e:
        msg = str(e)
        kind = _classify_value_error(msg)
        if kind == "NO_DOCUMENT":
            rec["execution_success"] = False
            rec["execution_error"] = msg
        elif kind == "SQL_VALIDATION_FAILED":
            rec["validation_success"] = False
            rec["execution_error"] = msg
        elif kind == "SQL_TIMEOUT":
            rec["execution_success"] = False
            rec["execution_error"] = msg
        else:
            rec["execution_success"] = False
            rec["execution_error"] = msg
    except Exception as e:  # noqa: BLE001
        llm = _is_llm_error(e)
        if llm:
            rec["sql_generation_success"] = False
            rec["llm_error"] = llm
        else:
            rec["execution_success"] = False
            rec["execution_error"] = f"{type(e).__name__}: {e}"
    finally:
        try:
            tq.close()
        except Exception:  # noqa: BLE001
            pass

    # ---- 3) 自然语言答案（生产 _explain_text，独立容错）----
    if rec.get("execution_success") and rec.get("generated_sql"):
        tq2 = TableQueryService()
        try:
            doc_id2 = tq2.load_from_file(REAL_XLSX, EVAL_USER)
            sql2, cols2, rows2, _, sources2, trunc2 = await tq2._pipeline(
                EVAL_USER, case.question, doc_id2
            )
            rec["final_answer"] = await tq2._explain_text(
                case.question, sql2, cols2, rows2, sources2, trunc2
            )
        except Exception as e:  # noqa: BLE001
            llm = _is_llm_error(e)
            if llm:
                rec["llm_error"] = rec.get("llm_error") or llm
            rec["final_answer"] = f"[explain failed: {type(e).__name__}]"
        finally:
            try:
                tq2.close()
            except Exception:  # noqa: BLE001
                pass

    # ---- 4) 统一评估（覆盖所有路径：成功 / 各类失败）----
    rec["result_match"], rec["row_count_match"] = evaluate_result(case, rec)
    rec["answer_match"] = evaluate_answer(case, rec)
    rec["failure_category"] = infer_failure(rec)

    rec["elapsed_ms"] = round((time.time() - start) * 1000, 1)
    return rec


async def run_all(cases: List[Case]) -> List[Dict[str, Any]]:
    results = []
    for c in cases:
        results.append(await run_case(c))
    return results
