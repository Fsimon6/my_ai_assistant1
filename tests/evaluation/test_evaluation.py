"""Phase 2.3 Evaluation 测试入口。

设计原则：
  - 普通 `pytest`            : 仅运行框架 self-test（不调用真实 LLM，不注册真实数据）。
  - `RUN_EVALUATION=1 pytest -m evaluation` : 真正运行真实 LLM E2E（受 marker + skipif 双重保护）。

self-test 验证：dataset 加载、metrics 评估逻辑、report 生成、marker 注册。
真实 E2E 验证：真实用户问题 -> 生产 TableQAService.classify -> 生产 TableQueryService._pipeline
              (真实 NL2SQL -> AST -> value resolution -> DuckDB) -> 生产 _explain_text -> 与 Ground Truth 比较。
"""
import os
import sys

import pytest

from .dataset import load_dataset, case_by_id, REAL_XLSX, EVAL_USER
from .metrics import evaluate_result, evaluate_answer, aggregate, numeric_close
from .report import write_report

RUN_EVALUATION = os.environ.get("RUN_EVALUATION") == "1"


# ----------------------------- 框架 self-test（默认运行，不调 LLM） -----------------------------
def test_dataset_loads():
    ds = load_dataset()
    assert len(ds) >= 10, "第一版数据集应有至少 10 个 Case"
    ids = {c.case_id for c in ds}
    assert len(ids) == len(ds), "case_id 必须唯一"
    # 真实数据文件应存在
    assert os.path.exists(REAL_XLSX), f"真实 Excel 缺失: {REAL_XLSX}"


def test_numeric_close():
    assert numeric_close(447.0, 447) is True
    assert numeric_close(8.025369127516786, 8.03, tol=0.01) is True
    assert numeric_close(27.63, 27.6, tol=0.01) is True
    assert numeric_close(27.63, 30.0, tol=0.01) is False


def test_evaluate_result_scalar():
    from types import SimpleNamespace
    case = SimpleNamespace(expected_result=447, expected_row_count=None, expected_contains_values=[])
    rec = {"actual_result": 447, "actual_row_count": 1, "actual_rows": [[447]]}
    rm, rcm = evaluate_result(case, rec)
    assert rm is True and rcm is None


def test_evaluate_result_rowcount_and_contains():
    from types import SimpleNamespace
    case = SimpleNamespace(expected_result=None, expected_row_count=5,
                           expected_contains_values=[27.63, 14.06])
    rec = {"actual_result": None, "actual_row_count": 5,
           "actual_rows": [["a", 27.63], ["b", 14.06], ["c", 13.93], ["d", 13.88], ["e", 12.9]]}
    rm, rcm = evaluate_result(case, rec)
    assert rm is True and rcm is True


def test_report_generation(tmp_path):
    # 用合成结果验证 report 不报错且产出 JSON/CSV
    synthetic = [{
        "case_id": "x", "question": "q", "expected_intent": "PRECISE_QUERY",
        "actual_intent": "PRECISE_QUERY", "route_match": True, "tags": ["t"],
        "sql_generation_success": True, "generated_sql": "SELECT 1",
        "referenced_tables": [], "referenced_columns": [], "validation_success": True,
        "value_resolution_success": True, "value_resolution_error": None,
        "execution_success": True, "execution_error": None, "actual_result": 1,
        "actual_row_count": 1, "actual_rows": [[1]], "final_answer": "1",
        "answer_match": True, "result_match": True, "row_count_match": True,
        "llm_error": None, "failure_category": "PASS", "elapsed_ms": 12.0,
        "skip_execution": False,
    }]
    jp, cp = write_report(synthetic, str(tmp_path))
    assert os.path.exists(jp) and os.path.exists(cp)


# ----------------------------- 真实 LLM E2E（需显式开启） -----------------------------
@pytest.mark.evaluation
@pytest.mark.skipif(not RUN_EVALUATION, reason="真实 LLM E2E 默认跳过；运行: RUN_EVALUATION=1 pytest -m evaluation")
async def test_real_llm_e2e():
    from .runner import run_all
    cases = load_dataset()
    results = await run_all(cases)
    assert len(results) == len(cases)
    jp, cp = write_report(results)
    # 打印摘要（便于人工查看，不计入断言）
    from .metrics import aggregate
    print("\n=== Phase 2.3 E2E Metrics ===")
    print(aggregate(results).to_dict())
    for r in results:
        print(f"  {r['case_id']:24s} intent={r['actual_intent']:18s} "
              f"sql_ok={r['sql_generation_success']} exec={r['execution_success']} "
              f"result={r['result_match']} cat={r['failure_category']}")
    print(f"  report: {jp}")
