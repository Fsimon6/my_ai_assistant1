# -*- coding: utf-8 -*-
import os
from playwright.sync_api import sync_playwright

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

def try_browser(path, label):
    print(f"=== {label} ({path}) exists={os.path.exists(path)} ===")
    if not os.path.exists(path):
        return
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, executable_path=path, args=["--no-sandbox"])
        pg = b.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append("PAGEERR:" + str(e)))
        pg.on("console", lambda m: errs.append("CONSOLE:" + m.text) if m.type == "error" else None)
        pg.goto("http://127.0.0.1:5173/register", wait_until="domcontentloaded", timeout=30000)
        pg.wait_for_timeout(6000)
        print("title:", pg.title())
        print("has user input:", pg.locator('input[placeholder="用户名或邮箱"]').count())
        print("body head:", pg.inner_text("body")[:200].replace("\n", " "))
        print("errors:", errs[:8])
        b.close()

try_browser(CHROME, "Chrome")
try_browser(EDGE, "Edge")
