# -*- coding: utf-8 -*-
"""
Phase 3 统一 Table QA —— Router Matrix + 执行级回归验证（只读编排，不修改 Phase 1/2）。

覆盖：
1) 13 项边界问题：route / intent / confidence（纯规则，0 LLM）
2) 执行级：通过 TableQAService 跑 PRECISE / STRUCTURED / AMBIGUOUS，
   复用冻结的 Phase 2（DuckDB NL2SQL）与 Phase 1（rag_query），验证统一层不破坏既有能力。
"""
import asyncio
import glob
import json
import os
import sys

sys.path.insert(0, r"C:\Users\Administrator\Desktop\my_ai_assistant")

from backend.services.table_qa_service import TableQAService, Intent, Confidence
from backend.services.table_query_service import TableQueryService

TEST_USER = 990001  # 隔离的测试用户，不影响真实数据


# ----------------- 1) Router Matrix（纯 classify） -----------------
MATRIX = [
    ("A", "这个表有多少列？", Intent.SCHEMA, Confidence.HIGH),
    ("B", "这个表有多少条记录？", Intent.PRECISE_QUERY, Confidence.MEDIUM),
    ("C", "这个表有多少个订单？", Intent.PRECISE_QUERY, Confidence.MEDIUM),
    ("D", "这个表有多少个 SKU？", Intent.PRECISE_QUERY, Confidence.MEDIUM),
    ("E", "这个表里的所有 SKU 是什么？", Intent.STRUCTURED_ACCESS, Confidence.HIGH),
    ("F", "前50条 SKU 是什么？", Intent.STRUCTURED_ACCESS, Confidence.HIGH),
    ("G", "Seller SKU 是什么意思？", Intent.SEMANTIC_RETRIEVAL, Confidence.HIGH),
    ("H", "金眼这个产品相关的数据在哪里？", Intent.SEMANTIC_RETRIEVAL, Confidence.MEDIUM),
    ("I", "金眼一共卖了多少单？", Intent.PRECISE_QUERY, Confidence.HIGH),
    ("J", "哪个 SKU 卖得最多？", Intent.PRECISE_QUERY, Confidence.HIGH),
    ("K", "销量最高10个 SKU 的商品名称是什么？", Intent.PRECISE_QUERY, Confidence.HIGH),
    ("L", "订单有多少？", Intent.PRECISE_QUERY, Confidence.MEDIUM),
    ("M", "数量是多少？", Intent.AMBIGUOUS, Confidence.LOW),
]


def test_classify():
    svc = TableQAService()
    print("=" * 78)
    print("1) Router Matrix（route / intent / confidence）")
    print("=" * 78)
    print(f"{'#':<3}{'问题':<34}{'intent':<20}{'conf':<8}PASS")
    passed = 0
    for label, q, exp_i, exp_c in MATRIX:
        d = svc.classify(q)
        ok = (d.intent == exp_i and d.confidence == exp_c)
        passed += 1 if ok else 0
        print(f"{label:<3}{q:<32}{d.intent.value:<20}{d.confidence.value:<8}{'PASS' if ok else 'FAIL'}"
              + ("" if ok else f"  (期望 {exp_i.value}/{exp_c.value})"))
    print(f"\nRouter Matrix: {passed}/{len(MATRIX)} PASS")
    return passed == len(MATRIX)


# ----------------- 2) 执行级回归 -----------------
async def test_execution():
    print("\n" + "=" * 78)
    print("2) 执行级回归（经 TableQAService 调用冻结的 Phase 1 / Phase 2）")
    print("=" * 78)

    svc = TableQAService()

    # 注册真实 xlsx 到隔离测试用户（仅内存 + 磁盘 rep，不污染 Chroma）
    xlsx_files = glob.glob(os.path.join(
        r"C:\Users\Administrator\Desktop\my_ai_assistant\backend\data\table_originals", "*.xlsx"))
    if not xlsx_files:
        print("未找到真实 xlsx，跳过执行级测试")
        return True
    tq = TableQueryService()
    doc_id = tq.load_from_file(xlsx_files[0], TEST_USER, os.path.basename(xlsx_files[0]))
    print(f"已注册测试文档：{doc_id} <- {os.path.basename(xlsx_files[0])}")

    rows_total = None
    cases = [
        ("B", "这个表一共有多少条记录？", Intent.PRECISE_QUERY, "COUNT(*)"),
        ("C", "这个表一共有多少个不同的 Order ID？", Intent.PRECISE_QUERY, "COUNT(DISTINCT \"Order ID\")"),
        ("7", "Original Shipping Fee 总和是多少？", Intent.PRECISE_QUERY, "SUM"),
        ("4", "Shipping Provider Name 为 SF International 的订单有多少条？", Intent.PRECISE_QUERY, "WHERE"),
        ("13", "按照 Original Shipping Fee 从高到低排列，最高的 10 条是什么？", Intent.PRECISE_QUERY, "ORDER BY"),
        ("14", "销量最高的 10 个 SKU 是哪些？", Intent.PRECISE_QUERY, "GROUP BY"),
        ("E", "这个表里的所有 SKU 是什么？", Intent.STRUCTURED_ACCESS, None),
        ("M", "数量是多少？", Intent.AMBIGUOUS, None),
    ]
    all_ok = True
    for label, q, exp_i, must_sql in cases:
        d = svc.classify(q)
        if not d.execute:
            res = await svc.run(user_id=TEST_USER, question=q, document_id=doc_id if exp_i != Intent.AMBIGUOUS else None)
            intent_ok = (d.intent == exp_i)
            print(f"[{label}] {q}\n   route={res.get('route')} intent={res.get('intent')} "
                  f"conf={res.get('confidence')} -> 澄清: {res.get('message')[:30]}... "
                  f"{'PASS' if intent_ok else 'FAIL'}")
            all_ok &= intent_ok
            continue

        res = await svc.run(user_id=TEST_USER, question=q, document_id=doc_id)
        intent_ok = (d.intent == exp_i)
        sql = (res.get("sql") or "").upper()
        sql_ok = True
        if must_sql:
            # must_sql 为关键子句提示（COUNT(*)/COUNT(DISTINCT "ORDER ID")/SUM/WHERE/ORDER BY/GROUP BY）
            if must_sql == "COUNT(*)":
                sql_ok = "COUNT(" in sql and "*)" in sql
            elif must_sql.startswith("COUNT(DISTINCT"):
                sql_ok = "COUNT(DISTINCT" in sql and "ORDER ID" in sql
            elif must_sql == "SUM":
                sql_ok = "SUM(" in sql
            elif must_sql == "WHERE":
                sql_ok = "WHERE" in sql
            elif must_sql == "ORDER BY":
                sql_ok = "ORDER BY" in sql and "LIMIT" in sql
            elif must_sql == "GROUP BY":
                sql_ok = "GROUP BY" in sql
        ans = (res.get("answer") or "").strip()
        src_ok = isinstance(res.get("sources"), list)
        if label == "B" and res.get("rows") is not None:
            rows_total = len(res["rows"])
        ok = intent_ok and sql_ok and bool(ans) and src_ok
        all_ok &= ok
        print(f"[{label}] {q}\n   route={res.get('route')} intent={res.get('intent')} "
              f"conf={res.get('confidence')} chain={res.get('chain')}")
        print(f"   SQL={res.get('sql')}")
        print(f"   答案={ans[:60]}{'...' if len(ans) > 60 else ''}  sources={len(res.get('sources') or [])}条"
              f"  {'PASS' if ok else 'FAIL'}"
              + ("" if sql_ok else "  [SQL子句缺失]"))
    if rows_total is not None:
        print(f"\n  真实文档总行数 COUNT(*) = {rows_total}")
    return all_ok


async def main():
    ok1 = test_classify()
    ok2 = await test_execution()
    print("\n" + "=" * 78)
    print(f"Router Matrix: {'PASS' if ok1 else 'FAIL'} | 执行级回归: {'PASS' if ok2 else 'FAIL'}")
    print("=" * 78)
    return 0 if (ok1 and ok2) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
