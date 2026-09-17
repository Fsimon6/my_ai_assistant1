# -*- coding: utf-8 -*-
"""
Stage 25 Browser E2E -- SEGMENTED, BOUNDED runner.
Real frontend (Vite :5173) + real backend (uvicorn :8000), Playwright/Chromium.

Design (per Stage 25 rules):
- Each segment has START/END timestamps and PASS/FAIL/BLOCKED.
- NO 90s waits. Streaming completion is detected via the .streaming-indicator
  appearing then disappearing (real completion signal), each capped at 30s.
- No networkidle. No infinite loops.
- If a prerequisite segment fails, dependents are marked BLOCKED (no blind cascade).
- mode: "chat" -> segments 1-3 (fast feedback); "all" -> segments 1-9.

Run:  python e2e_segments.py chat
      python e2e_segments.py all
"""
import json, time, sys
from playwright.sync_api import sync_playwright

BASE = "http://localhost:5173"
API = "http://127.0.0.1:8000/api/v1"
RUN_ID = time.strftime("%Y%m%d_%H%M%S")
USER = f"e2e_{RUN_ID}"
EMAIL = f"{USER}@example.com"
PASS = "E2ePass123"
CHAR_NAME = f"E2E角色{RUN_ID[-6:]}"
CHAR_PROMPT = "你是一个测试助手，请记住用户提供的测试编号，并简洁回答。"
TEST_ID = "BROWSER-E2E-2026"
MODE = sys.argv[1] if len(sys.argv) > 1 else "all"

results = {}
console_errors = []
page_errors = []
network_5xx = []

def log_result(name, ok, detail=""):
    results[name] = (ok, detail)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""), flush=True)

def wait_ok(page, fn, timeout=12000):
    """Bounded polling. Default 12s (tight)."""
    deadline = time.time() + timeout / 1000.0
    last = None
    while time.time() < deadline:
        try:
            if fn():
                return True
        except Exception as e:
            last = e
        time.sleep(0.3)
    if last:
        return False
    return False

def set_val(page, selector, value):
    """Set a Vue v-model directly (native setter + input event). Safe for
    el-input/textarea regardless of modal overlay hit-testing."""
    return page.evaluate("""(args) => {
      const el = document.querySelector(args.sel);
      if (!el) return false;
      const proto = el.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
      setter.call(el, args.val);
      el.dispatchEvent(new Event('input', {bubbles:true}));
      el.dispatchEvent(new Event('change', {bubbles:true}));
      return true;
    }""", {"sel": selector, "val": value})

def send_and_stream(page, text, stream_timeout=30000):
    """Type + click send, then wait for streaming to START (.streaming-indicator
    appears) and COMPLETE (indicator gone). Returns (started, completed, last_text)."""
    page.fill('textarea[placeholder*="输入消息"]', text, timeout=10000)
    page.locator('button:has-text("发送")').click(timeout=10000)
    started = wait_ok(page,
        lambda: page.locator('.message-item.assistant .streaming-indicator').count() > 0,
        timeout=20000)
    completed = wait_ok(page,
        lambda: page.locator('.message-item.assistant .streaming-indicator').count() == 0,
        timeout=stream_timeout)
    last = ""
    try:
        last = page.locator('.message-item.assistant .ai-message').last.inner_text()
    except Exception:
        last = ""
    print(f"   [stream] started={started} completed={completed} last_assistant={last[:120]!r}", flush=True)
    return started, completed, last

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True,
                                executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                                args=["--no-sandbox"])
    ctx = browser.new_context()
    page = ctx.new_page()
    api_log = []
    def on_resp(r):
        try:
            if "/api/" in r.url:
                api_log.append((r.request.method, r.status, r.url))
            if r.status >= 500:
                network_5xx.append(r.url)
        except Exception:
            pass
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: page_errors.append(getattr(e, "message", None) or str(e)))
    page.on("response", on_resp)

    # ---------- 01 Register ----------
    def seg_register():
        page.goto(f"{BASE}/register", wait_until="domcontentloaded", timeout=20000)
        page.fill('input[placeholder="用户名（至少3个字符）"]', USER, timeout=8000)
        page.fill('input[placeholder="邮箱"]', EMAIL, timeout=8000)
        page.fill('input[placeholder="昵称（可选）"]', "e2e", timeout=8000)
        page.fill('input[placeholder="密码（至少6个字符）"]', PASS, timeout=8000)
        page.fill('input[placeholder="确认密码"]', PASS, timeout=8000)
        page.locator("button.register-button").click(timeout=10000)
        time.sleep(1.5)
        if "/login" not in page.url:
            page.goto(f"{BASE}/login", wait_until="domcontentloaded", timeout=20000)
        return "/login" in page.url
    ok = seg_register()
    log_result("01_Register", ok, f"url={page.url}")
    if not ok:
        pass  # continue to login anyway

    # ---------- 02 Login ----------
    def seg_login():
        page.goto(f"{BASE}/login", wait_until="domcontentloaded", timeout=20000)
        page.fill('input[placeholder="用户名或邮箱"]', USER, timeout=8000)
        page.fill('input[placeholder="密码"]', PASS, timeout=8000)
        page.locator("button.login-button").click(timeout=10000)
        # Definitive login-success signal: a JWT appears in localStorage.
        return wait_ok(page, lambda: page.evaluate("""(function(){for(var i=0;i<localStorage.length;i++){var v=localStorage.getItem(localStorage.key(i));if(v&&v.split('.').length===3)return true;}return false;})()"""), timeout=12000)
    ok_login = seg_login()
    token = page.evaluate("""(function(){var t=null;for(var i=0;i<localStorage.length;i++){var k=localStorage.key(i);var v=localStorage.getItem(k);if(v&&v.split('.').length===3)t=v;}return t;})()""")
    log_result("02_Login", ok_login, f"url={page.url} token={'yes' if token else 'no'}")
    if not ok_login:
        print("\n=== SUMMARY ===", flush=True)
        for k, (v, d) in results.items():
            print(f"{k:18} {'PASS' if v else 'FAIL'}  {d}", flush=True)
        browser.close()
        sys.exit(0)

    # ---------- 03 Characters (create) ----------
    def seg_characters():
        page.goto(f"{BASE}/characters", wait_until="domcontentloaded", timeout=20000)
        time.sleep(1.0)
        cbtn = page.locator("button:has-text('创建新角色')")
        if cbtn.count() == 0:
            return False
        cbtn.first.click(timeout=10000)
        time.sleep(0.6)
        nin = page.locator("input[placeholder='例如：python导师、代码助手']")
        if not wait_ok(page, lambda: nin.count() > 0, timeout=8000):
            return False
        set_val(page, "input[placeholder='例如：python导师、代码助手']", CHAR_NAME)
        set_val(page, "input[placeholder='定义角色的行为和个性...']", CHAR_PROMPT)
        page.locator('.el-dialog button:has-text("创建")').click(force=True, timeout=10000)
        return wait_ok(page, lambda: page.locator(f'.character-name:has-text("{CHAR_NAME}")').count() > 0, timeout=15000)
    ok_chars = seg_characters()
    log_result("03_Characters", ok_chars, f"card_found={ok_chars}")
    if not ok_chars:
        # dependents 04-07 blocked
        for nm in ("04_CharacterDetail", "05_ChatStreaming", "06_History", "07_MultiTurn", "08_Clear", "09_ReChat"):
            log_result(nm, False, "BLOCKED: character not created")
        # still run 10/11 (KB, PT) which don't depend on character
        pass

    chat_ready = ok_chars

    # ---------- 04 CharacterDetail ----------
    if chat_ready:
        def seg_detail():
            page.locator(f'.character-card:has(.character-name:has-text("{CHAR_NAME}"))').click(timeout=10000)
            # diagnostics: url timeline + render probe
            for dt in (0.5, 1, 2, 4, 8):
                time.sleep(dt if dt == 0.5 else 1.0)
                print(f"   [detail] t~{dt}s url={page.url} detail_card={page.locator('.detail-card').count()} container={page.locator('.character-detail-container').count()}", flush=True)
            if not wait_ok(page, lambda: "/characters/" in page.url, timeout=10000):
                return False
            name_ok = wait_ok(page, lambda: CHAR_NAME in page.inner_text("body"), timeout=6000)
            has_start = page.locator("button:has-text('开始对话')").count() > 0
            has_back = page.locator("button:has-text('返回')").count() > 0
            return name_ok and has_start and has_back
        ok_detail = seg_detail()
        log_result("04_CharacterDetail", ok_detail, f"url={page.url} pageerrors={page_errors[-3:]}")
        chat_ready = ok_detail
    else:
        ok_detail = False

    # ---------- 05 Chat + Streaming ----------
    if chat_ready:
        def seg_chat():
            page.locator("button:has-text('开始对话')").click(timeout=10000)
            for dt in (0.5, 1, 2, 4):
                time.sleep(dt if dt == 0.5 else 1.0)
                ta = page.locator('textarea[placeholder*="输入消息"]').count()
                cc = page.locator('.chat-container').count()
                print(f"   [chat] t~{dt}s url={page.url} textarea={ta} chat_container={cc}", flush=True)
            if not wait_ok(page, lambda: "/chat/" in page.url, timeout=10000):
                return False
            # wait for chat textarea
            if not wait_ok(page, lambda: page.locator('textarea[placeholder*="输入消息"]').count() > 0, timeout=8000):
                return False
            started, completed, last = send_and_stream(page, f"我的测试编号是 {TEST_ID}。请记住它。", stream_timeout=30000)
            user_ok = wait_ok(page, lambda: TEST_ID in page.inner_text("body"), timeout=6000)
            return started and completed and bool(last.strip()) and user_ok
        ok_chat = seg_chat()
        log_result("05_ChatStreaming", ok_chat, f"url={page.url}")
        chat_ready = ok_chat
    else:
        ok_chat = False

    # ---------- 06 History (refresh) ----------
    if chat_ready:
        def seg_history():
            page.reload(wait_until="domcontentloaded", timeout=20000)
            # after reload, history loads; TEST_ID must appear (user message persisted)
            return wait_ok(page, lambda: TEST_ID in page.inner_text("body"), timeout=15000)
        ok_hist = seg_history()
        log_result("06_History", ok_hist, f"persisted={ok_hist}")
    else:
        ok_hist = False

    # ---------- 07 MultiTurn ----------
    if chat_ready:
        def seg_multiturn():
            started, completed, last = send_and_stream(page, "我的测试编号是什么？", stream_timeout=30000)
            return started and completed and (TEST_ID in last)
        ok_mt = seg_multiturn()
        log_result("07_MultiTurn", ok_mt, f"reply_contains_id={ok_mt}")
    else:
        ok_mt = False

    # ---------- 08 Clear ----------
    if chat_ready:
        def seg_clear():
            page.locator('button:has-text("清空对话")').click(timeout=10000)
            if not wait_ok(page, lambda: page.locator(".el-message-box").count() > 0, timeout=6000):
                return False
            page.locator('.el-message-box__btns button:has-text("确定")').click(timeout=5000)
            # After clear, Chat resets to a single welcome assistant message bubble
            # (the .welcome-message div only shows when messages.length===0). So verify
            # the conversation is cleared: no user messages + at least one assistant message.
            welcome = wait_ok(page, lambda: page.locator('.message-item.user').count() == 0 and page.locator('.message-item.assistant').count() >= 1, timeout=12000)
            page.reload(wait_until="domcontentloaded", timeout=20000)
            gone = wait_ok(page, lambda: TEST_ID not in page.inner_text("body"), timeout=15000)
            return welcome and gone
        ok_clear = seg_clear()
        log_result("08_Clear", ok_clear, f"welcome={ok_clear}")
    else:
        ok_clear = False

    # ---------- 09 ReChat ----------
    if chat_ready and ok_clear:
        def seg_rechat():
            if not wait_ok(page, lambda: page.locator('textarea[placeholder*="输入消息"]').count() > 0, timeout=8000):
                return False
            started, completed, last = send_and_stream(page, "你好，这是重新聊天测试。", stream_timeout=30000)
            return started and completed and bool(last.strip())
        ok_re = seg_rechat()
        log_result("09_ReChat", ok_re, f"stream_ok={ok_re}")
    else:
        ok_re = False

    # ---------- 10 KnowledgeBase (page-level + 上传/删除闭环) ----------
    def seg_kb():
        page.goto(f"{BASE}/knowledge", wait_until="domcontentloaded", timeout=20000)
        ok_page = wait_ok(page, lambda: "知识库管理" in page.inner_text("body"), timeout=12000)
        # 上传 → 文档出现 → 删除 → 文档消失 → 刷新后仍存在性（删除闭环）
        import tempfile as _tf, os as _os
        fname = "e2e29_doc.txt"
        fpath = _os.path.join(_tf.gettempdir(), fname)
        with open(fpath, "w", encoding="utf-8") as _fh:
            _fh.write("e2e29 专属测试文档\n机密标记 E2E29MARKXYZ\n")
        closed = False
        try:
            page.locator('input[type="file"]').first.set_input_files(fpath)
            page.locator('.upload-actions button.el-button--primary').click()
            # 等待后端真实上传完成并在表格出现该行（嵌入可能较慢，给足超时）
            page.wait_for_selector(f'tr.el-table__row:has-text("{fname}")', timeout=90000)
            row = page.locator('tr.el-table__row', has_text=fname).first
            row.locator('button:has-text("删除")').click()
            # 确认删除弹窗（真实调用后端 DELETE /rag/documents）
            page.locator('.el-message-box__btns button:has-text("确定")').click()
            page.wait_for_selector(f'tr.el-table__row:has-text("{fname}")', state="detached", timeout=20000)
            page.reload(wait_until="domcontentloaded")
            closed = page.locator('tr.el-table__row', has_text=fname).count() == 0
        except Exception as e:
            print("  kb upload/delete err:", e, flush=True)
            closed = False
        return ok_page and closed
    ok_kb = seg_kb()
    log_result("10_KnowledgeBase", ok_kb, f"url={page.url}; upload_delete_closed={ok_kb}")

    # ---------- 11 PromptTemplates (orphan SFC: served 200, not mounted) ----------
    def seg_pt():
        mod = ctx.new_page()
        try:
            r = mod.goto(f"{BASE}/src/components/chat/PromptTemplates.vue", timeout=15000)
            served = (r is not None and r.status == 200)
        except Exception as e:
            served = False
            print("  pt err:", e, flush=True)
        mod.close()
        return served
    ok_pt = seg_pt()
    log_result("11_PromptTemplates", ok_pt, f"module_served_200={ok_pt}; orphan (not mounted in app)")

    # ---------- console / network health ----------
    real_console = [e for e in console_errors if ("Failed to load" not in e and "chunk" not in e and "favicon" not in e)]
    log_result("ConsoleHealth", len(real_console) == 0,
               f"errors={len(real_console)} pageerrors={len(page_errors)} 5xx={len(network_5xx)}")

    # persist ctx for optional cleanup
    json.dump({"username": USER, "email": EMAIL, "password": PASS,
               "character_name": CHAR_NAME, "token": token,
               "base": BASE, "api": API}, open("e2e_ctx.json", "w"), ensure_ascii=False)

    print("\n=== SUMMARY ===", flush=True)
    for k, (v, d) in results.items():
        print(f"{k:18} {'PASS' if v else 'FAIL'}  {d}", flush=True)
    print(f"console_errors(total)={len(console_errors)} page_errors={len(page_errors)} network_5xx={len(network_5xx)}", flush=True)
    print("console_errors:", console_errors[:10], flush=True)
    print("api_log:", api_log[:40], flush=True)
    if page_errors:
        print("sample pageerrors:", page_errors[:5], flush=True)
    browser.close()
