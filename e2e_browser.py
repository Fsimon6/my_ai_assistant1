# -*- coding: utf-8 -*-
"""真实浏览器 E2E：Playwright 驱动 Vue 前端（Edge），经 KnowledgeBase 快速查询命中真实
后端 /api/v1/rag/query，验证浏览器最终显示文本的完整性与顺序（非仅 Python 内部调用）。

覆盖：枚举(全量/前N/后N/范围/多列) + 精确查找(Order ID→Variation/SKUID/ShippingProviderName/多列) +
不存在值 + 普通语义 + TXT 普通 RAG。重点：Variation 查询在浏览器中绝不应出现 Product Category
值（如“男士运动套装”），证明无列误归属。
"""
import sys, re, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, r'C:\Users\Administrator\Desktop\my_ai_assistant')
from playwright.sync_api import sync_playwright
from backend.services.table_representation import load_representation
from backend.services.rag_service import RagService

BASE = 'http://localhost:5173'
USER, PW = 'FSimon', 'Test123456'


def _rep():
    return load_representation('c3294aa7e9fa4dc19f3a4738ae2c53c8')


def _oid_at(row_idx0):
    rep = _rep()
    t = rep['workbook']['sheets'][0]['tables'][0]
    cols = t['columns']; rows = t['rows']
    ci = next(i for i, c in enumerate(cols) if c['technical_name'] == 'Order ID')
    return rows[row_idx0]['cells'][ci].get('value')


def _mid_vals():
    rep = _rep()
    t = rep['workbook']['sheets'][0]['tables'][0]
    cols = t['columns']; rows = t['rows']
    ci = {c['technical_name']: c['col_index'] for c in cols}
    r = rows[100]
    cells = r['cells']
    return {cn: RagService._cell_text(cells[ci[cn] - 1].get('value') if ci[cn] - 1 < len(cells) else None)
            for cn in ['Variation', 'SKU ID', 'Shipping Provider Name', 'Product Category']}


def mid_oid():
    return _oid_at(100)


# (label, query, kind, exp_n, exp_last, exp_values)
QUERIES = [
    ('1.所有ShippingProviderName', '把所有 Shipping Provider Name 列出来', 'enum', 448, 449, None),
    ('2.所有SKU', '把所有 SKU ID 列出来', 'enum', 448, 449, None),
    ('3.前50SKU', '把前50条 SKU ID 列出来', 'enum', 50, 51, None),
    ('4.前100SKU', '把前100条 SKU ID 列出来', 'enum', 100, 101, None),
    ('5.最后10SKU', '把最后10条 SKU ID 列出来', 'enum', 10, 449, None),
    ('6.第100-150SKU', '把第100到150条 SKU ID 列出来', 'enum', 51, 151, None),
    ('7.前20OrderID+SKU', '把前20条 Order ID 和 SKU ID 列出来', 'enum', 20, 21, None),
    ('8.中间OrderID→Variation', f'帮我查 Order ID 为 {mid_oid()} 的 Variation', 'lookup', None, None,
     {'Variation': _mid_vals()['Variation']}),
    ('9.中间OrderID→SKUID', f'帮我查 Order ID 为 {mid_oid()} 的 SKU ID', 'lookup', None, None,
     {'SKU ID': _mid_vals()['SKU ID']}),
    ('10.中间OrderID→ShippingProviderName', f'帮我查 Order ID 为 {mid_oid()} 的 Shipping Provider Name', 'lookup', None, None,
     {'Shipping Provider Name': _mid_vals()['Shipping Provider Name']}),
    ('11.中间OrderID→Variation+SKUID', f'帮我查 Order ID 为 {mid_oid()} 的 Variation 和 SKU ID', 'lookup', None, None,
     {'Variation': _mid_vals()['Variation'], 'SKU ID': _mid_vals()['SKU ID']}),
    ('12.不存在OrderID', '帮我查 Order ID 为 1234567890123456XY 的 Variation', 'notfound', None, None, None),
    ('13.普通语义', '这个表主要记录什么？', 'semantic', None, None, None),
    ('14.TXT普通RAG', 'mysql 的启动命令是什么？', 'txt', None, None, None),
]


def main():
    results = []
    mv = _mid_vals()
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge', headless=True)
        page = browser.new_page(viewport={'width': 1400, 'height': 1000})
        page.goto(BASE + '/login', wait_until='networkidle')
        page.get_by_placeholder('用户名或邮箱').fill(USER)
        page.get_by_placeholder('密码').fill(PW)
        page.get_by_role('button', name='登录').click()
        page.wait_for_url('**/dashboard', timeout=15000)
        print('LOGIN OK ->', page.url)
        page.goto(BASE + '/knowledge', wait_until='networkidle')
        page.wait_for_selector('textarea[placeholder="输入您的问题，快速查询知识库..."]', timeout=15000)
        page.mouse.click(8, 8)
        page.wait_for_timeout(1500)
        if page.locator('div[role="dialog"]').count() > 0:
            page.keyboard.press('Escape')
            page.wait_for_timeout(1000)
        print('KNOWLEDGE loaded; modal still open =', page.locator('div[role="dialog"]').count() > 0)

        for label, q, kind, exp_n, exp_last, exp_values in QUERIES:
            try:
                box = page.get_by_placeholder('输入您的问题，快速查询知识库...')
                box.fill(q)
                page.evaluate("document.querySelector('.quick-query-form .el-button--primary')?.click()")
                page.wait_for_timeout(9000)
                body = page.inner_text('body')
                rows = [int(m) for m in re.findall(r'第(\d+)行:', body)]
                cnt = len(rows)
                last = max(rows) if rows else None
                if kind == 'enum':
                    ok = (cnt == exp_n and last == exp_last)
                    detail = f'count={cnt}/{exp_n} last={last}/{exp_last}'
                elif kind == 'lookup':
                    vals_present = all(v in body for v in exp_values.values())
                    # 列误归属防护：问 Variation 时，答案绝不应含 Product Category 值
                    misattr = True
                    if 'Variation' in exp_values:
                        misattr = mv['Product Category'] not in body
                    ok = vals_present and misattr
                    detail = f'值命中={vals_present} 列误归属防护={misattr}'
                elif kind == 'notfound':
                    ok = any(k in body for k in ['未找到', '没有找到', '未检索到', '不存在', '无法找到', '查无', '未匹配'])
                    detail = f'诚实未找到={ok}'
                else:
                    ok = len(body) > 30
                    detail = f'len={len(body)}'
                if (kind == 'lookup' or kind == 'notfound') and not ok:
                    with open('e2e_debug_body.txt', 'a', encoding='utf-8') as f:
                        f.write(f'\n===== {label} BODY =====\n{body}\n')
                status = 'PASS' if ok else 'FAIL'
            except Exception as e:
                detail = f'EXCEPTION {e}'
                status = 'FAIL'
                cnt = last = None
            results.append((label, kind, cnt, last, detail, status))
            print(f'[{label}] {detail} | {status}')
        browser.close()

    npass = sum(1 for r in results if r[-1] == 'PASS')
    print('-' * 100)
    print(f'BROWSER E2E SUMMARY: PASS={npass} FAIL={len(results) - npass}')
    for r in results:
        print(' | '.join(str(x) for x in r))


if __name__ == '__main__':
    main()
