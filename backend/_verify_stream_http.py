# -*- coding: utf-8 -*-
import sys, os, time, json, uuid
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import requests

BASE = "http://127.0.0.1:8000"
uname = "strm_" + uuid.uuid4().hex[:8]
pwd = "Test123456"
email = uname + "@example.com"

r = requests.post(BASE + "/api/v1/auth/register",
                  json={"username": uname, "email": email, "password": pwd, "full_name": "S"})
print("register", r.status_code, r.text[:120])
r = requests.post(BASE + "/api/v1/auth/login", json={"username": uname, "password": pwd})
print("login", r.status_code)
token = r.json()["data"]["access_token"]
r = requests.post(BASE + "/api/v1/characters",
                  json={"name": "流式测试", "system_prompt": "你是一个简洁助手。", "model": "gpt-3.5-turbo"},
                  headers={"Authorization": f"Bearer {token}"})
print("create", r.status_code)
cid = r.json()["data"]["id"]

t0 = time.time()
resp = requests.post(BASE + f"/api/v1/characters/{cid}/speak/stream",
                     json={"message": "用三段话介绍人工智能。", "stream": True},
                     headers={"Authorization": f"Bearer {token}", "Accept": "application/x-ndjson"},
                     stream=True)
print("stream status", resp.status_code)
n = 0
for line in resp.iter_lines(decode_unicode=True):
    if not line:
        continue
    n += 1
    print("[%.3fs] line#%d :: %s" % (time.time() - t0, n, line[:90]))
print("TOTAL lines=%d elapsed=%.3fs" % (n, time.time() - t0))
