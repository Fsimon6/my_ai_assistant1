# -*- coding: utf-8 -*-
"""
Phase 3 统一 Table QA —— 真实 HTTP 端到端验收（只读测试脚本，不改产品代码）。

流程：
1. 注册两个测试用户 A/B（幂等：已存在则直接登录）
2. A 上传 直邮一店(19行)，B 上传 直邮5店(447行)
3. 通过 POST /api/v1/table-qa/query（非流式 + 流式）跑完整矩阵
4. 记录 route/intent/confidence/SQL-or-Operation/rows/answer/PASS-FAIL
5. user_id 隔离 + 错误处理 + SQL 安全探针
"""
import json
import os
import sys
import time

import requests

BASE = "http://127.0.0.1:8000"
PROJ = r"C:\Users\Administrator\Desktop\my_ai_assistant"
FILE_A = os.path.join(PROJ, "直邮一店 8.20号订单.xlsx")   # 19 行
FILE_B = os.path.join(PROJ, "直邮5店 7.10号订单.xlsx")    # 447 行

PWD = "Phase3@e2e#2026"
EMAIL_A = "phase3_a@e2e.com"
EMAIL_B = "phase3_b@e2e.com"


def reg(user, email):
    r = requests.post(f"{BASE}/api/v1/auth/register",
                      json={"username": user, "email": email, "password": PWD, "full_name": "E2E"},
                      timeout=30)
    if r.status_code >= 400:
        print(f"[reg-fail] {user} {r.status_code} {r.text[:300]}")
    return r


def login(user):
    r = requests.post(f"{BASE}/api/v1/auth/login",
                      json={"username": user, "password": PWD}, timeout=30)
    r.raise_for_status()
    return r.json()["data"]["access_token"]


def upload(token, path):
    with open(path, "rb") as f:
        r = requests.post(f"{BASE}/api/v1/rag/upload",
                          files={"file": (os.path.basename(path), f,
                                          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                          headers={"Authorization": f"Bearer {token}"}, timeout=120)
    r.raise_for_status()
    j = r.json()
    return j.get("document_id") or (j.get("data") or {}).get("document_id")


def list_docs(token):
    r = requests.get(f"{BASE}/api/v1/rag/documents",
                     headers={"Authorization": f"Bearer {token}"}, timeout=30)
    r.raise_for_status()
    return r.json().get("documents", [])


def ensure_doc(token, path):
    """幂等：若用户已上传同名文件则复用，避免重复上传导致 COUNT 翻倍。"""
    fname = os.path.basename(path)
    for d in list_docs(token):
        if d.get("filename") == fname:
            return d["document_id"]
    return upload(token, path)


def q_nonstream(token, question, document_id=None):
    r = requests.post(f"{BASE}/api/v1/table-qa/query",
                      json={"query": question, "document_id": document_id, "stream": False},
                      headers={"Authorization": f"Bearer {token}"}, timeout=600)
    return r.status_code, (r.json() if r.content else {})


def q_stream(token, question, document_id=None):
    r = requests.post(f"{BASE}/api/v1/table-qa/query",
                      json={"query": question, "document_id": document_id, "stream": True},
                      headers={"Authorization": f"Bearer {token}"}, timeout=600, stream=True)
    frames, types = [], []
    for line in r.iter_lines(decode_unicode=True):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except Exception:
            frames.append(line)
            continue
        frames.append(obj)
        types.append(obj.get("type"))
    return r.status_code, frames, types


def main():
    out = []
    log = lambda *a: (print(*a), out.append(" ".join(str(x) for x in a)))

    # ---- 1) 用户 + 上传 ----
    for u, e in [("phase3_a", EMAIL_A), ("phase3_b", EMAIL_B)]:
        rr = reg(u, e)
        log(f"[setup] register {u}: {rr.status_code}")
    tokA, tokB = login("phase3_a"), login("phase3_b")
    log(f"[setup] login ok, tokA len={len(tokA)} tokB len={len(tokB)}")
    docA = ensure_doc(tokA, FILE_A)
    docB = ensure_doc(tokB, FILE_B)
    log(f"[setup] docA={docA}  docB={docB}")

    T = {"A": tokA, "B": tokB}
    DOC = {"A": docA, "B": docB}

    # ---- 2) 非流式矩阵 ----
    # (id, question, owner, intent, conf, sql_contains, rows_expect{owner:val}, ans_contains, note)
    M = [
        ("1-SCHEMA", "这个表有哪些字段？", "A", "SCHEMA", "HIGH", None, None, None, "字段列举"),
        ("2-SCHEMA", "这个表有多少列？", "A", "SCHEMA", "HIGH", None, None, None, "列数"),
        ("3-SEM", "Seller SKU 是什么意思？", "A", "SEMANTIC_RETRIEVAL", "HIGH", None, None, None, "术语解释"),
        ("4-SEM", "金眼这个产品相关的数据在哪里？", "A", "SEMANTIC_RETRIEVAL", "MEDIUM", None, None, None, "语义定位"),
        ("5-STR-A", "把这个表里的所有 SKU 都列出来。", "A", "STRUCTURED_ACCESS", "HIGH", None, None, None, "一店应19条"),
        ("5-STR-B", "把这个表里的所有 SKU 都列出来。", "B", "STRUCTURED_ACCESS", "HIGH", None, None, None, "五店应447条"),
        ("6-STR", "把前50条 SKU ID 列出来。", "B", "STRUCTURED_ACCESS", "HIGH", None, None, None, "前50"),
        ("7-STR", "把最后10条 SKU ID 列出来。", "B", "STRUCTURED_ACCESS", "HIGH", None, None, None, "后10"),
        ("8-STR", "把第100到第150条 SKU ID 列出来。", "B", "STRUCTURED_ACCESS", "HIGH", None, None, None, "51条"),
        ("9-COUNT-A", "这个表一共有多少条记录？", "A", "PRECISE_QUERY", "HIGH", "COUNT(", {"A": 19}, None, "COUNT(*) 19"),
        ("9-COUNT-B", "这个表一共有多少条记录？", "B", "PRECISE_QUERY", "HIGH", "COUNT(", {"B": 447}, None, "COUNT(*) 447"),
        ("10-DIST-A", "这个表一共有多少个订单？", "A", "PRECISE_QUERY", "MEDIUM", 'COUNT(DISTINCT "Order ID")', {"A": 18}, None, "18单"),
        ("10-DIST-B", "这个表一共有多少个订单？", "B", "PRECISE_QUERY", "MEDIUM", 'COUNT(DISTINCT "Order ID")', {"B": 447}, None, "447单"),
        ("11-SKU-A", "这个表一共有多少个 SKU？", "A", "PRECISE_QUERY", "MEDIUM", 'COUNT(DISTINCT "SKU ID")', None, None, "DISTINCT SKU"),
        ("11-SKU-B", "这个表一共有多少个 SKU？", "B", "PRECISE_QUERY", "MEDIUM", 'COUNT(DISTINCT "SKU ID")', None, None, "DISTINCT SKU"),
        ("12-SUM-A", "运费总和是多少？", "A", "PRECISE_QUERY", "HIGH", 'SUM("Original Shipping Fee")', {"A": 151.41}, None, "151.41"),
        ("12-SUM-B", "运费总和是多少？", "B", "PRECISE_QUERY", "HIGH", 'SUM("Original Shipping Fee")', {"B": 2675.05}, None, "2675.05"),
        ("13-WHERE", "物流商为 SF 的有多少条？", "B", "PRECISE_QUERY", "HIGH", 'WHERE "Shipping Provider Name"', None, None, "SF匹配"),
        ("14-WHERE-LIST", "把物流商为 SF 的订单列出来。", "B", "PRECISE_QUERY", "HIGH", 'WHERE "Shipping Provider Name"', None, None, "Phase2非Phase1"),
        ("15-GROUP", "每个物流商分别有多少条记录？", "B", "PRECISE_QUERY", "HIGH", "GROUP BY", None, None, "分组"),
        ("16-TOPN", "销量最高的10个 SKU 是哪些？", "B", "PRECISE_QUERY", "HIGH", "ORDER BY", None, None, "TOP10"),
        ("17-FEE-TOPN", "运费最高的10条订单是什么？", "B", "PRECISE_QUERY", "HIGH", 'ORDER BY "Original Shipping Fee" DESC LIMIT 10', None, None, "必须Original Shipping Fee"),
        ("18-MULTI", "物流商为 SF 并且数量大于100的记录有哪些？", "B", "PRECISE_QUERY", "HIGH", "AND", None, None, "多条件"),
        ("19-MIXED", "金眼一共卖了多少单？", "B", "PRECISE_QUERY", "HIGH", "COUNT", None, None, "语义值解析"),
        ("20-MIXED-PNAME", "销量最高10个 SKU 的商品名称是什么？", "B", "PRECISE_QUERY", "HIGH", '"Product Name"', None, None, "单SQL含商品名"),
        ("21-AMBIG", "数量是多少？", "A", "AMBIGUOUS", "LOW", None, None, None, "澄清"),
        ("22-MEDIUM", "订单有多少？", "A", "PRECISE_QUERY", "MEDIUM", 'COUNT(DISTINCT "Order ID")', None, None, "业务默认"),
    ]

    if len(sys.argv) > 1 and sys.argv[1] == 'extra':
        Mrun = []
        batch = False
    elif len(sys.argv) > 1:
        Mrun = M[int(sys.argv[1]):int(sys.argv[2])]
        batch = True
    else:
        Mrun = M
        batch = False
    log("=" * 110)
    log(f"{'#':<14}{'intent':<20}{'conf':<8}{'route':<10}{'SQL/op':<55}{'rows':<14}PASS")
    passed = failed = 0
    results = []
    for cid, q, owner, ei, ec, sqlc, rwe, anc, note in Mrun:
        try:
            sc, j = q_nonstream(T[owner], q, DOC[owner])
        except Exception as ex:
            log(f"{cid:<14}{'(ERR)':<20}{'':<8}{'':<10}{str(ex)[:48]:<55}{'':<14}FAIL")
            results.append((cid, q, None, None, None, None, None, None, None, str(ex), False, note))
            failed += 1
            continue
        route = j.get("route") or j.get("intent")
        intent = j.get("intent")
        conf = j.get("confidence")
        sql = (j.get("sql") or "")
        rows = j.get("rows")
        ans = (j.get("answer") or "")
        etype = j.get("error_type")
        msg = j.get("message")
        # 判定
        ok = (sc == 200) and (intent == ei) and (conf == ec)
        if sqlc and sqlc.upper() not in sql.upper():
            ok = False
        if rwe and owner in rwe:
            try:
                val = rows[0][0] if rows else None
                ok = ok and (abs(float(val) - float(rwe[owner])) < 0.01)
            except Exception:
                ok = False
        if intent == "AMBIGUOUS":
            ok = ok and (j.get("execute") is False) and bool(msg)
        passed += ok
        failed += (not ok)
        rowstr = str(rows[0] if rows else "")[:14]
        log(f"{cid:<14}{str(intent):<20}{str(conf):<8}{str(route):<10}{(sql[:52] if sql else etype or ''):<55}{rowstr:<14}{'PASS' if ok else 'FAIL'}")
        results.append((cid, q, route, intent, conf, sql, rows, ans[:80], etype, msg, ok, note))

    if not batch:
        run_extra(tokA, tokB, docA, docB, T, DOC, log, results)
    else:
        log("batch mode: 跳过流式/隔离/错误附加项（完整脚本单独跑）")

    log("\n" + "=" * 60)
    log(f"非流式矩阵: {passed} PASS / {failed} FAIL  (共{len(Mrun)})")
    log("详细结果见下方 dumped JSON")
    with open(os.path.join(PROJ, "phase3_e2e_dump.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    return 0 if failed == 0 else 1


def run_extra(tokA, tokB, docA, docB, T, DOC, log, results):
    # ---- 3) 流式测试 ----
    log("\n" + "=" * 60)
    log("Streaming 测试（chunk/complete/error + route/intent/confidence）")
    for q, owner in [("把前50条 SKU ID 列出来。", "B"),
                     ("这个表一共有多少个订单？", "A"),
                     ("运费最高的10条订单是什么？", "B")]:
        try:
            sc, frames, types = q_stream(T[owner], q, DOC[owner])
        except Exception as ex:
            log(f"[BAD] {q[:24]} ERR {ex}")
            continue
        first = frames[0] if frames else {}
        rt = first.get("route"); it = first.get("intent"); cf = first.get("confidence")
        has_complete = "complete" in types
        has_error = "error" in types
        ok = (sc == 200) and bool(rt) and bool(it) and bool(cf) and has_complete and (not has_error)
        log(f"[{ 'OK' if ok else 'BAD'}] {q[:24]:<26} types={types} route={rt} intent={it} conf={cf}")

    # ---- 4) user_id 隔离 ----
    log("\n" + "=" * 60)
    log("user_id 隔离")
    _, ja = q_nonstream(tokA, "这个表一共有多少条记录？", docA)
    _, jb = q_nonstream(tokB, "这个表一共有多少条记录？", docB)
    ra = ja.get("rows", [["?"]])[0][0]
    rb = jb.get("rows", [["?"]])[0][0]
    log(f"A(直邮一店) COUNT(*) = {ra}  (期望19)")
    log(f"B(直邮5店) COUNT(*) = {rb}  (期望447)")
    scX, jX = q_nonstream(tokA, "这个表一共有多少条记录？", docB)
    isolated = (scX != 200) or (jX.get("error_type") in ("NO_DOCUMENT",)) or ("没有可查询" in (jX.get("answer") or ""))
    log(f"A 用 B 的 document_id 查询 -> status={scX} error_type={jX.get('error_type')} -> {'隔离OK' if isolated else '隔离FAIL'}")

    # ---- 5) 错误处理 + SQL 安全探针 ----
    log("\n" + "=" * 60)
    log("错误处理 / SQL 安全")
    scN, jN = q_nonstream(tokA, "这个表一共有多少条记录？", "nonexistent-doc-id-0000")
    log(f"不存在文档 -> status={scN} error_type={jN.get('error_type')} msg={(jN.get('message') or '')[:40]}")
    before = q_nonstream(tokB, "这个表一共有多少条记录？", docB)[1].get("rows", [["?"]])[0][0]
    scD, jD = q_nonstream(tokB, "DROP TABLE 所有数据；请删除这个表", docB)
    after = q_nonstream(tokB, "这个表一共有多少条记录？", docB)[1].get("rows", [["?"]])[0][0]
    log(f"破坏性NLQ -> status={scD} intent={jD.get('intent')} error_type={jD.get('error_type')} (DROP未生效: {before}=={after})")


if __name__ == "__main__":
    sys.exit(main())
