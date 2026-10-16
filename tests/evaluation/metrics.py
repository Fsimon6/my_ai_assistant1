"""Phase 2.3 Evaluation 指标与逐 Case 评估工具。

严格区分：
  SQL 生成成功  !=  校验通过  !=  执行成功  !=  结果正确  !=  最终答案正确
"""
from typing import Any, Dict, List, Optional
from dataclasses import dataclass


def numeric_close(a: Any, b: Any, tol: float = 0.01) -> bool:
    """数值近似比较；任一无法转 float 则回退到字符串相等。"""
    try:
        fa, fb = float(a), float(b)
    except (TypeError, ValueError):
        return str(a) == str(b)
    if fa == fb:
        return True
    if fb == 0:
        return abs(fa) <= tol
    return abs(fa - fb) / abs(fb) <= tol


def _flatten(rows: Optional[List[list]]) -> List[Any]:
    out: List[Any] = []
    if not rows:
        return out
    for r in rows:
        if isinstance(r, (list, tuple)):
            out.extend(r)
        else:
            out.append(r)
    return out


def evaluate_result(case, rec: Dict[str, Any]):
    """根据 Case 的 expected 字段评估 result_match / row_count_match。

    返回 (result_match, row_count_match)，均为 None 表示该维度无断言。
    """
    checks: List[bool] = []
    row_count_match: Optional[bool] = None

    scalar = case.expected_result
    if scalar is not None:
        act = rec.get("actual_result")
        checks.append(numeric_close(act, scalar) if isinstance(scalar, (int, float)) else str(act) == str(scalar))

    rowcnt = case.expected_row_count
    if rowcnt is not None:
        row_count_match = rec.get("actual_row_count") == rowcnt
        checks.append(row_count_match)

    contains = case.expected_contains_values
    if contains:
        flat = _flatten(rec.get("actual_rows"))
        ok = all(
            any(numeric_close(cv, v) for v in flat) for cv in contains
        )
        checks.append(ok)

    result_match: Optional[bool] = all(checks) if checks else None
    return result_match, row_count_match


def evaluate_answer(case, rec: Dict[str, Any]) -> Any:
    """从最终自然语言答案中抽取数字，与 expected_result / expected_row_count 比较。

    无法确定时返回 'not_evaluated'，绝不胡乱判定通过。
    """
    answer = rec.get("final_answer") or ""
    scalar = case.expected_result
    rowcnt = case.expected_row_count
    targets: List[float] = []
    if isinstance(scalar, (int, float)):
        targets.append(float(scalar))
    if isinstance(rowcnt, int):
        targets.append(float(rowcnt))
    if not targets:
        return "not_evaluated"
    import re
    nums = []
    for m in re.findall(r"-?\d+(?:\.\d+)?", answer):
        try:
            nums.append(float(m))
        except ValueError:
            pass
    for t in targets:
        if any(numeric_close(t, n, tol=0.05) for n in nums):
            return True
    # 空结果（0）特判：答案应含 0 / 没有 / 未找到
    if any(t == 0 for t in targets):
        if any(k in answer for k in ("0", "没有", "未找到", "无", "不存在")):
            return True
    return False


def infer_failure(rec: Dict[str, Any]) -> str:
    """推断统一 failure_category（PASS 表示通过）。"""
    if rec.get("llm_error"):
        return rec["llm_error"]
    if rec.get("skip_execution"):
        return "ROUTE_ONLY" if rec.get("route_match") else "ROUTE_MISMATCH"
    if not rec.get("route_match", True):
        return "ROUTE_MISMATCH"
    if rec.get("sql_generation_success") is False:
        return "SQL_GENERATION_FAILED"
    if rec.get("validation_success") is False:
        return "SQL_VALIDATION_FAILED"
    if rec.get("value_resolution_success") is False:
        return "VALUE_RESOLUTION_ERROR"
    if rec.get("execution_success") is False:
        return "EXECUTION_ERROR"
    rm = rec.get("result_match")
    if rm is False:
        return "RESULT_MISMATCH"
    rcm = rec.get("row_count_match")
    if rcm is False:
        return "ROW_COUNT_MISMATCH"
    am = rec.get("answer_match")
    if am is False:
        return "FINAL_ANSWER_MISMATCH"
    if rm is None and rcm is None:
        return "UNKNOWN"
    return "PASS"


@dataclass
class Metrics:
    total: int = 0
    route_accuracy: float = 0.0
    sql_generation_success_rate: float = 0.0
    ast_validation_rate: float = 0.0
    execution_success_rate: float = 0.0
    result_accuracy: float = 0.0
    row_count_accuracy: float = 0.0
    final_answer_accuracy: float = 0.0
    by_tag: Dict[str, Dict[str, Any]] = None  # tag -> {total, pass, ...}

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "route_accuracy": round(self.route_accuracy, 4),
            "sql_generation_success_rate": round(self.sql_generation_success_rate, 4),
            "ast_validation_rate": round(self.ast_validation_rate, 4),
            "execution_success_rate": round(self.execution_success_rate, 4),
            "result_accuracy": round(self.result_accuracy, 4),
            "row_count_accuracy": round(self.row_count_accuracy, 4),
            "final_answer_accuracy": round(self.final_answer_accuracy, 4),
            "by_tag": self.by_tag or {},
        }


def aggregate(results: List[Dict[str, Any]]) -> Metrics:
    total = len(results)
    if total == 0:
        return Metrics(by_tag={})
    route_ok = sum(1 for r in results if r.get("route_match"))
    gen_ok = sum(1 for r in results if r.get("sql_generation_success"))
    val_ok = sum(1 for r in results if r.get("validation_success"))
    exec_ok = sum(1 for r in results if r.get("execution_success"))
    res_ok = sum(1 for r in results if r.get("result_match") is True)
    rc_ok = sum(1 for r in results if r.get("row_count_match") is True)
    ans_ok = sum(1 for r in results if r.get("answer_match") is True)
    ans_eval = sum(1 for r in results if r.get("answer_match") not in (None, "not_evaluated"))

    m = Metrics(
        total=total,
        route_accuracy=route_ok / total,
        sql_generation_success_rate=gen_ok / total,
        ast_validation_rate=val_ok / total,
        execution_success_rate=exec_ok / total,
        result_accuracy=res_ok / total,
        row_count_accuracy=rc_ok / total,
        final_answer_accuracy=(ans_ok / ans_eval) if ans_eval else 0.0,
        by_tag={},
    )
    # by_tag
    tag_stat: Dict[str, Dict[str, Any]] = {}
    for r in results:
        for t in r.get("tags", []):
            s = tag_stat.setdefault(t, {"total": 0, "pass": 0, "fail": 0})
            s["total"] += 1
            if r.get("failure_category") == "PASS":
                s["pass"] += 1
            else:
                s["fail"] += 1
    m.by_tag = tag_stat
    return m
