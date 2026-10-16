"""Phase 2.3 Evaluation 报告生成（JSON 完整链路 + CSV 摘要）。

注意：报告可能包含真实业务数据（actual_rows），默认不提交（见 reports/.gitkeep 说明）。
"""
import csv
import json
import os
from datetime import datetime
from typing import Any, Dict, List

from .metrics import aggregate

REPORTS_DIR = os.path.join(os.path.dirname(__file__), "reports")


def write_report(results: List[Dict[str, Any]], out_dir: str = REPORTS_DIR):
    os.makedirs(out_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = os.path.join(out_dir, f"{ts}.json")
    csv_path = os.path.join(out_dir, f"{ts}.csv")

    metrics = aggregate(results)
    payload = {
        "generated_at": ts,
        "metrics": metrics.to_dict(),
        "results": results,  # 含 actual_rows（真实业务数据），请勿提交
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)

    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "case_id", "question", "expected_intent", "actual_intent", "route_match",
            "sql_generation_success", "validation_success", "execution_success",
            "result_match", "row_count_match", "answer_match", "failure_category", "elapsed_ms",
        ])
        for r in results:
            w.writerow([
                r.get("case_id"), r.get("question"), r.get("expected_intent"),
                r.get("actual_intent"), r.get("route_match"),
                r.get("sql_generation_success"), r.get("validation_success"),
                r.get("execution_success"), r.get("result_match"), r.get("row_count_match"),
                r.get("answer_match"), r.get("failure_category"), r.get("elapsed_ms"),
            ])
    return json_path, csv_path
