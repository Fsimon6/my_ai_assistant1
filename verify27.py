# -*- coding: utf-8 -*-
"""阶段27 验证：SQLite WAL / 全局异常 / 上传限制 / 登录限流 / 并发流式 / RAG 隔离回归。"""
import io
import json
import os
import sys
import uuid
import threading
import urllib.request
import urllib.error

BASE = "http://localhost:8000"
ROOT = os.getcwd()
UPLOAD_DIR = os.path.join(os.path.dirname(ROOT), "ai_assistant_uploads") if False else os.path.join(
    os.path.dirname(ROOT), "ai_assistant_uploads")
# 实际临时目录在系统 temp 下
import tempfile
UPLOAD_DIR = os.path.join(tempfile.gettempdir(), "ai_assistant_uploads")


def req(method, path, token=None, data=None, headers=None, raw=False):
    url = BASE + path
    h = dict(headers or {})
    if token:
        h["Authorization"] = f"Bearer {token}"
    body = None
    if data is not None:
        if isinstance(data, (dict, list)):
            body = json.dumps(data).encode()
            h["Content-Type"] = "application/json"
        else:
            body = data
    r = urllib.request.Request(url, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=180) as resp:
            txt = resp.read().decode("utf-8", "ignore")
            return resp.status, (txt if raw else (json.loads(txt) if txt else {}))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")


def register_login(tag):
    uname = f"e2e27_{tag}_{uuid.uuid4().hex[:6]}"
    pw = "Test@123456"
    req("POST", "/api/v1/auth/register", data={"username": uname, "email": f"{uname}@e.com", "password": pw})
    st, b = req("POST", "/api/v1/auth/login", data={"username": uname, "password": pw})
    return uname, pw, b["data"]["access_token"]


def multipart(path, token, field, filename, content, mime=None, meta=None):
    boundary = "----v27" + uuid.uuid4().hex
    parts = [f"--{boundary}\r\n".encode(),
             f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'.encode()]
    if mime:
        parts.append(f"Content-Type: {mime}\r\n".encode())
    parts.append(b"\r\n")
    parts.append(content if isinstance(content, bytes) else content.encode())
    parts.append(b"\r\n")
    if meta:
        parts += [f"--{boundary}\r\n".encode(), b'Content-Disposition: form-data; name="metadata"\r\n\r\n',
                  meta.encode(), b"\r\n"]
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    return req("POST", path, token=token, data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})


def main():
    fails = []
    print("== 1. 注册触发写，验证 WAL ==")
    _, _, ta = register_login("a")
    import sqlite3
    c = sqlite3.connect(os.path.join(ROOT, "my_ai_assistant.db"))
    jm = c.execute("PRAGMA journal_mode").fetchone()[0]
    c.close()
    print(f"  journal_mode={jm}")
    if str(jm).lower() != "wal":
        fails.append(f"journal_mode={jm}，WAL 未生效")

    print("== 2. 全局异常处理器注册 + 500 信封 ==")
    sys.path.insert(0, ROOT)
    from fastapi import Request
    import asyncio
    from backend.utils.exceptions import global_exception_handler
    from backend.main import app
    print(f"  Exception 处理器已注册: {Exception in app.exception_handlers}")
    if Exception not in app.exception_handlers:
        fails.append("global_exception_handler 未注册")
    scope = {"type": "http", "method": "GET", "url": "http://x/", "headers": [],
             "client": ("1.2.3.4", 1234), "path": "/", "query_string": b""}
    resp = asyncio.new_event_loop().run_until_complete(global_exception_handler(Request(scope), Exception("boom")))
    body = json.loads(resp.body)
    print(f"  500 状态码={resp.status_code} code={body['error']['code']} msg={body['error']['message'][:20]}")
    if resp.status_code != 500 or body.get("error", {}).get("code") != "ERR_INTERNAL":
        fails.append("全局异常处理器返回格式不符")

    print("== 3. 上传限制 ==")
    # 正常
    st, up = multipart("/api/v1/rag/upload", ta, "file", "note.txt", "正常文档内容，用于检索回归。", mime="text/plain")
    print(f"  正常txt: {st} success={up.get('success') if isinstance(up,dict) else up}")
    if st != 200 or not (isinstance(up, dict) and up.get("success")):
        fails.append("正常上传应成功")
    ids = up.get("document_ids", []) if isinstance(up, dict) else []
    # 超限 (11MB)
    st_big, _ = multipart("/api/v1/rag/upload", ta, "file", "big.txt", b"x" * (11 * 1024 * 1024), mime="text/plain")
    print(f"  超限11MB: {st_big} (期望413)")
    if st_big != 413:
        fails.append("超限文件未被拒绝(413)")
    # 非法扩展名
    st_ext, _ = multipart("/api/v1/rag/upload", ta, "file", "x.exe", "hello", mime="application/octet-stream")
    print(f"  非法扩展名exe: {st_ext} (期望400)")
    if st_ext != 400:
        fails.append("非法扩展名未被拒绝(400)")
    # MIME 不符
    st_mime, _ = multipart("/api/v1/rag/upload", ta, "file", "x.txt", "hello", mime="image/png")
    print(f"  MIME不符(txt但image/png): {st_mime} (期望415)")
    if st_mime != 415:
        fails.append("MIME 不符未被拒绝(415)")
    # 解析失败(corrupt docx) -> 优雅失败且清理孤儿
    before = set(os.listdir(UPLOAD_DIR)) if os.path.isdir(UPLOAD_DIR) else set()
    st_bad, bad = multipart("/api/v1/rag/upload", ta, "file", "bad.docx",
                            b"not a real docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    after = set(os.listdir(UPLOAD_DIR)) if os.path.isdir(UPLOAD_DIR) else set()
    print(f"  corrupt docx: {st_bad} success={bad.get('success') if isinstance(bad,dict) else bad}; 孤儿文件增量={after-before}")
    if after - before:
        fails.append("上传失败/解析失败时残留孤儿临时文件")
    # 清理正常上传的向量
    if ids:
        req("DELETE", "/api/v1/rag/documents", token=ta, data=ids)

    print("== 4. 登录失败限流 429 ==")
    uname_b, pw_b, _ = register_login("b")
    got429 = False
    for i in range(7):
        st, _ = req("POST", "/api/v1/auth/login", data={"username": uname_b, "password": "wrong" + str(i)})
        if st == 429:
            got429 = True
            print(f"  第{i+1}次失败登录 -> 429 (限流生效)")
            break
        else:
            print(f"  第{i+1}次失败登录 -> {st}")
    if not got429:
        fails.append("登录失败限流未生效(429)")
    # 正确登录应成功(清零)
    st_ok, _ = req("POST", "/api/v1/auth/login", data={"username": uname_b, "password": pw_b})
    print(f"  正确登录 -> {st_ok} (期望200)")
    if st_ok != 200:
        fails.append("限流后正确登录被拒")

    print("== 5. 并发流式(同角色2并发) ==")
    _, _, tc = register_login("c")
    st, cb = req("POST", "/api/v1/characters/", token=tc, data={"name": "并发角色", "system_prompt": "你是测试助手。", "model": "gpt-3.5-turbo"})
    cid = cb["data"]["id"]
    responses = []
    errs = []
    lock = threading.Barrier(2)

    def send(msg):
        try:
            lock.wait()
            s, raw = req("POST", f"/api/v1/characters/{cid}/speak/stream", token=tc,
                         data={"message": msg, "stream": False}, raw=True)
            full = ""
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if o.get("type") == "complete":
                    full = o.get("content", "")
            responses.append((s, full))
        except Exception as e:
            errs.append(str(e))

    t1 = threading.Thread(target=send, args=("并发消息A",))
    t2 = threading.Thread(target=send, args=("并发消息B",))
    t1.start(); t2.start(); t1.join(); t2.join()
    print(f"  并发结果: 错误={errs} 响应数={len(responses)} 状态={[r[0] for r in responses]}")
    if errs:
        fails.append(f"并发流式抛出异常: {errs}")
    # 校验历史
    st_h, hist = req("GET", f"/api/v1/characters/{cid}/conversations?limit=100", token=tc)
    msgs = []
    if isinstance(hist, dict):
        # /conversations 返回 data.conversations 即消息列表（每条 {role,content,...}）
        msgs = hist.get("data", {}).get("conversations", [])
    print(f"  历史消息数={len(msgs)} 角色分布={[m['role'] for m in msgs]}")
    if len(msgs) != 4:
        fails.append(f"并发后历史消息数={len(msgs)}，期望4(2user+2assistant)")
    if any(m["role"] == "assistant" and "生成失败" in m["content"] for m in msgs):
        fails.append("并发出现 assistant 失败标记(可能被中断)")

    print("== 6. RAG 跨用户隔离回归 ==")
    _, _, td = register_login("d")
    doc = ("机密文档：唯一验证码 TOKEN-27-X。").encode("utf-8")
    st_u, up2 = multipart("/api/v1/rag/upload", td, "file", "secret27.txt", doc, mime="text/plain")
    dids = up2.get("document_ids", []) if isinstance(up2, dict) else []
    st_q1, r1 = req("POST", "/api/v1/rag/query", token=td, data={"query": "文档里的唯一验证码是什么？", "stream": False})
    a_resp = r1.get("response", "") if isinstance(r1, dict) else ""
    st_q2, r2 = req("POST", "/api/v1/rag/query", token=tc, data={"query": "文档里的唯一验证码是什么？", "stream": False})
    b_resp = r2.get("response", "") if isinstance(r2, dict) else ""
    print(f"  D(所有者)含TOKEN: {'TOKEN-27-X' in a_resp};  C(他人)含TOKEN: {'TOKEN-27-X' in b_resp}")
    if "TOKEN-27-X" not in a_resp:
        fails.append("所有者未检索到自己的私密文档")
    if "TOKEN-27-X" in b_resp:
        fails.append("他人跨用户命中私密内容(RAG隔离回归失败)")
    if dids:
        req("DELETE", "/api/v1/rag/documents", token=td, data=dids)

    print("\n== 结果 ==\n" + ("ALL PASS ✅" if not fails else "FAIL ❌:\n- " + "\n- ".join(fails)))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
