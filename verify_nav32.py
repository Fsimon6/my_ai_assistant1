# -*- coding: utf-8 -*-
"""Stage32 nav verification (temp). Local Vite:5173 + backend:8000."""
import time, json, sys, urllib.request, urllib.error
from playwright.sync_api import sync_playwright

BASE = "http://localhost:5173"
API = "http://127.0.0.1:8000/api/v1"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
RUN = time.strftime("%Y%m%d_%H%M%S")
U = f"nav_{RUN}"
PW = "NavPass123"

def log(s):
    print(s, flush=True)

def api(path, token=None, data=None, method="POST"):
    h = {"Content-Type": "application/json"}
    if token: h["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(API+path, data=json.dumps(data).encode() if data is not None else None,
        headers=h, method=method)
    try:
        r = urllib.request.urlopen(req, timeout=60); return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode(errors="ignore"))

R = {}
console_errors, api_5xx = [], []

def click_menu(pg, label):
    pg.locator(".el-menu-item", has_text=label).first.click(timeout=8000)

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME)
    pg = b.new_page()
    pg.context.set_default_timeout(12000)
    pg.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    pg.on("response", lambda r: api_5xx.append(r.status) if r.status >= 500 else None)

    st, jr = api("/auth/register", None, {"username": U, "email": U+"@ex.com", "password": PW})
    R["api_register"] = st
    log(f"api_register={st}")

    pg.goto(BASE+"/login", wait_until="networkidle")
    pg.fill('input[placeholder="用户名或邮箱"]', U)
    pg.fill('input[placeholder="密码"]', PW)
    pg.click("button.login-button")
    pg.wait_for_url("**/dashboard", timeout=12000)
    R["login_url"] = pg.url
    log(f"login_url={pg.url}")

    # NAV PRESENCE (capture before any click)
    try:
        R["nav_count"] = pg.locator(".nav-menu").count()
        R["nav_text"] = pg.locator(".nav-menu").inner_text()
    except Exception as e:
        R["nav_text"] = f"ERR {e}"
    log(f"nav_count={R.get('nav_count')} nav_text={R.get('nav_text','')!r}")

    def safe(name, fn):
        try:
            fn(); R[name] = "ok"
        except Exception as e:
            R[name] = f"FAIL {type(e).__name__}"

    safe("click_dashboard", lambda: (click_menu(pg, "控制面板"), pg.wait_for_timeout(400), R.__setitem__('url_dashboard', pg.url)))
    safe("click_characters", lambda: (click_menu(pg, "AI角色"), pg.wait_for_url("**/characters", timeout=8000)))
    safe("click_chat", lambda: (click_menu(pg, "聊天"), pg.wait_for_timeout(900)))
    safe("click_knowledge", lambda: (click_menu(pg, "知识库"), pg.wait_for_url("**/knowledge", timeout=8000)))
    safe("knowledge_has_nav", lambda: R.__setitem__('knowledge_has_nav', pg.locator(".nav-menu").count() > 0))

    safe("direct_chat", lambda: (pg.goto(BASE+"/chat", wait_until="networkidle"), pg.wait_for_timeout(900), R.__setitem__('direct_chat_url', pg.url)))
    safe("direct_knowledge", lambda: (pg.goto(BASE+"/knowledge", wait_until="networkidle"), pg.wait_for_timeout(500), R.__setitem__('direct_knowledge_url', pg.url)))

    token = pg.evaluate("localStorage.getItem('access_token')")
    st, jc = api("/characters/", token, {"name": f"{U}_char", "system_prompt": "p"})
    cid = (jc.get("data") or {}).get("id") or jc.get("id")
    R["char_created"] = cid is not None
    if cid:
        safe("chat_entry", lambda: (pg.goto(BASE+f"/chat/{cid}", wait_until="networkidle"), pg.wait_for_timeout(900),
                                     R.__setitem__('chat_url', pg.url),
                                     R.__setitem__('chat_has_nav', pg.locator(".nav-menu").count() > 0),
                                     R.__setitem__('chat_has_input', pg.locator("textarea").count() > 0)))
        safe("reload_stays", lambda: (pg.reload(wait_until="networkidle"), pg.wait_for_timeout(900), R.__setitem__('reload_url', pg.url)))
        safe("back", lambda: (pg.go_back(), pg.wait_for_timeout(1000), R.__setitem__('back_url', pg.url)))
        api(f"/characters/{cid}", token, None, method="DELETE")

    R["console_errors"] = len(console_errors)
    R["api_5xx"] = len(api_5xx)
    b.close()

log("NAV_RESULTS:")
for k, v in R.items():
    log(f"  {k} = {v}")
