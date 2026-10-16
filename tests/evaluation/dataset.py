"""Phase 2.3 Evaluation 数据集。

Ground Truth 来自真实 Excel（直邮5店 7.10号订单.xlsx）经生产 Table Representation
解析后在 DuckDB 中的确定性计算结果，**不由 LLM 生成**。所有数值在 `_compute_gt.py`
（临时脚本，已删除）中通过生产 `TableQueryService.load_from_file` + DuckDB 精确得出。

真实数据规模（生产解析后）：447 数据行，单 Sheet `OrderSKUList`。
关键列：
  - Order ID (VARCHAR, 447 唯一)
  - SKU ID   (VARCHAR, 447 唯一)
  - Shipping Provider Name (VARCHAR, 真实值仅 'SF International' ×447)
  - Quantity (DOUBLE, 全 1.0)
  - Order Amount (DOUBLE, 区间 0.3 ~ 27.63, 均值 ~8.03)
  - Created Time (VARCHAR 字符串, 格式 'MM/DD/YYYY H:MM:SS PM')
"""
import os
import sys
from dataclasses import dataclass, field, asdict
from typing import Any, List, Optional

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 真实数据源（项目根目录内，主项目文件，非副本）
REAL_XLSX = os.path.join(ROOT, "直邮5店 7.10号订单.xlsx")
EVAL_USER = "eval_phase23"

# 经生产 representation + DuckDB 计算得到的精确 Ground Truth 常量
GT_TOTAL_ROWS = 447
GT_SF_INTERNATIONAL = 447
GT_DISTINCT_SKU = 447
GT_SUM_QTY = 447.0
GT_AVG_AMT = 8.025369127516786
GT_MAX_AMT = 27.63
GT_MIN_AMT = 0.3
GT_TOP5_AMT = [27.63, 14.06, 13.93, 13.88, 12.9]  # 金额从高到低前 5


@dataclass
class Case:
    case_id: str
    question: str
    expected_intent: str  # PRECISE_QUERY / STRUCTURED_ACCESS / SCHEMA / SEMANTIC_RETRIEVAL / AMBIGUOUS
    expected_operation: Optional[str] = None  # COUNT / SUM / AVG / MIN / MAX / ENUMERATE / FILTER
    expected_columns: List[str] = field(default_factory=list)
    expected_values: List[str] = field(default_factory=list)
    expected_tables: List[str] = field(default_factory=list)  # 动态 doc_id，留空表示不校验表名
    expected_result: Any = None          # 标量结果（聚合/计数）
    expected_row_count: Optional[int] = None
    expected_rows: List[Any] = field(default_factory=list)
    expected_contains_values: List[Any] = field(default_factory=list)  # 结果中应包含的关键值
    expected_answer: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    skip_execution: bool = False  # True: 仅校验路由（STRUCTURED_ACCESS 走 Phase1，不执行 NL2SQL）

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# 第一版数据集（13 个高质量真实 Case，基于 447 行真实数据）
# 覆盖：简单计数 / 精确过滤 / 中文术语(预期失败) / 缩写映射 / 聚合 / 排序 Top-N /
#       去重 vs 全量枚举 / 空结果。
# 注：单文件 SKU 全部唯一，无法体现"19行/15唯一"的 UNION ALL 不去重差异；
#     该差异化场景留待后续跨表数据。去重 vs 不去重改用 Shipping Provider Name
#     （447 行全为 SF International）体现。
# ---------------------------------------------------------------------------
DATASET: List[Case] = [
    # ---- A. 简单计数 ----
    Case(
        case_id="count_total_001", question="一共有多少订单？",
        expected_intent="PRECISE_QUERY", expected_operation="COUNT",
        expected_result=GT_TOTAL_ROWS, expected_row_count=1,
        tags=["count", "simple"],
    ),
    # ---- B. 精确过滤 ----
    Case(
        case_id="filter_sf_001",
        question="Shipping Provider Name 是 SF International 的有多少单？",
        expected_intent="PRECISE_QUERY", expected_operation="COUNT",
        expected_columns=["Shipping Provider Name"], expected_values=["SF International"],
        expected_result=GT_SF_INTERNATIONAL, expected_row_count=1,
        tags=["filter", "value_resolution"],
    ),
    Case(
        case_id="filter_sf_002",
        question="物流商为 SF International 的订单有多少？",
        expected_intent="PRECISE_QUERY", expected_operation="COUNT",
        expected_columns=["Shipping Provider Name"], expected_values=["SF International"],
        expected_result=GT_SF_INTERNATIONAL, expected_row_count=1,
        tags=["filter", "cn_column"],
    ),
    # ---- C. 中文业务术语（预期失败：VALUE_ALIASES 未覆盖 顺丰→SF International）----
    Case(
        case_id="cn_shunfeng_001", question="顺丰有多少订单？",
        expected_intent="PRECISE_QUERY", expected_operation="COUNT",
        expected_columns=["Shipping Provider Name"], expected_values=["SF International"],
        expected_result=GT_SF_INTERNATIONAL, expected_row_count=1,
        tags=["value_resolution", "cn_term", "expected_failure"],
    ),
    # ---- D. SF 缩写（预期通过 value resolution 修正 'SF' -> 'SF International'）----
    Case(
        case_id="abbr_sf_001", question="SF 有多少订单？",
        expected_intent="PRECISE_QUERY", expected_operation="COUNT",
        expected_columns=["Shipping Provider Name"], expected_values=["SF International"],
        expected_result=GT_SF_INTERNATIONAL, expected_row_count=1,
        tags=["value_resolution", "abbr"],
    ),
    # ---- E. 聚合 ----
    Case(
        case_id="agg_sum_qty_001", question="总数量是多少？",
        expected_intent="PRECISE_QUERY", expected_operation="SUM",
        expected_columns=["Quantity"], expected_result=GT_SUM_QTY, expected_row_count=1,
        tags=["aggregation", "sum"],
    ),
    Case(
        case_id="agg_avg_amt_001", question="平均订单金额是多少？",
        expected_intent="PRECISE_QUERY", expected_operation="AVG",
        expected_columns=["Order Amount"], expected_result=GT_AVG_AMT, expected_row_count=1,
        tags=["aggregation", "avg"],
    ),
    Case(
        case_id="agg_max_amt_001", question="最高订单金额是多少？",
        expected_intent="PRECISE_QUERY", expected_operation="MAX",
        expected_columns=["Order Amount"], expected_result=GT_MAX_AMT, expected_row_count=1,
        tags=["aggregation", "max"],
    ),
    Case(
        case_id="agg_min_amt_001", question="最低订单金额是多少？",
        expected_intent="PRECISE_QUERY", expected_operation="MIN",
        expected_columns=["Order Amount"], expected_result=GT_MIN_AMT, expected_row_count=1,
        tags=["aggregation", "min"],
    ),
    # ---- F. 排序 / Top-N ----
    Case(
        case_id="topn_amt_001", question="价格最高的 5 条订单是什么？",
        expected_intent="PRECISE_QUERY", expected_operation="TOP_N",
        expected_columns=["Order Amount"], expected_row_count=5,
        expected_contains_values=GT_TOP5_AMT,
        tags=["top_n", "sorting"],
    ),
    Case(
        case_id="sort_amt_desc_001", question="按订单金额从高到低排序，前 10 个订单的金额是多少？",
        expected_intent="PRECISE_QUERY", expected_operation="TOP_N",
        expected_columns=["Order Amount"], expected_row_count=10,
        expected_contains_values=[GT_MAX_AMT],
        tags=["top_n", "sorting"],
    ),
    # ---- G. 去重 vs 全量枚举 ----
    Case(
        case_id="enum_distinct_ship_001", question="有多少个不同的物流商？",
        expected_intent="PRECISE_QUERY", expected_operation="COUNT_DISTINCT",
        expected_columns=["Shipping Provider Name"], expected_result=1, expected_row_count=1,
        tags=["enumeration", "distinct"],
    ),
    # ---- H. 空结果 ----
    Case(
        case_id="empty_ship_001", question="物流商为 UPS 的有多少单？",
        expected_intent="PRECISE_QUERY", expected_operation="COUNT",
        expected_columns=["Shipping Provider Name"], expected_values=["UPS"],
        expected_result=0, expected_row_count=1,
        tags=["empty_result"],
    ),
    # ---- 路由校验（STRUCTURED_ACCESS，走 Phase1，不执行 NL2SQL）----
    Case(
        case_id="route_enum_all_sku_001", question="把所有 SKU 列出来",
        expected_intent="STRUCTURED_ACCESS", expected_operation="ENUMERATE",
        expected_columns=["SKU ID"], expected_row_count=GT_DISTINCT_SKU,
        skip_execution=True,
        tags=["route", "enumeration", "struct_access"],
    ),
]


def load_dataset() -> List[Case]:
    return list(DATASET)


def case_by_id(case_id: str) -> Optional[Case]:
    for c in DATASET:
        if c.case_id == case_id:
            return c
    return None
