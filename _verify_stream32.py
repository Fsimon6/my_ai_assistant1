import urllib.request, json, time, random

BASE = "http://127.0.0.1:8000"

def post(path, body, token=None):
    data = json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    return urllib.request.urlopen(req, timeout=60)

def read_ndjson(req, label):
    r = urllib.request.urlopen(req, timeout=60)
    chunks, t0 = [], time.time()
    for raw in r:
        line = raw.decode().strip()
        if not line:
            continue
        obj = json.loads(line)
        if obj.get("type") == "chunk":
            chunks.append(obj["content"])
            if len(chunks) <= 3:
                print(f"  [{label}] chunk@{time.time()-t0:.2f}s: {obj['content']!r}")
        elif obj.get("type") == "complete":
            print(f"  [{label}] COMPLETE frame received")
        elif obj.get("type") == "error":
            print(f"  [{label}] ERROR frame: {obj.get('content')}")
    print(f"[{label}] TOTAL chunks={len(chunks)} full={''.join(chunks)[:160]!r}")
    return chunks

u = "e2e32_%d" % random.randint(1000, 9999)
pw = "Test123456"
try:
    post("/api/v1/auth/register", {"username": u, "email": u + "@x.com", "password": pw, "password_confirm": pw})
except Exception as e:
    print("register note:", e)
resp = post("/api/v1/auth/login", {"username": u, "password": pw})
token = json.loads(resp.read())["data"]["access_token"]
print("LOGIN OK user=", u)

# 默认模型角色（model='' api_key=''）
resp = post("/api/v1/characters/", {"name": "e2e32c", "system_prompt": "你是一个简洁的助手。", "model": "", "api_key": ""}, token=token)
cid = json.loads(resp.read())["data"]["id"]
print("CHARACTER id=", cid, "(default model path)")

req = urllib.request.Request(BASE + f"/api/v1/characters/{cid}/speak/stream",
                              data=json.dumps({"message": "用两句话介绍北京。"}).encode(), method="POST")
req.add_header("Content-Type", "application/json")
req.add_header("Authorization", "Bearer " + token)
print("=== 普通 Chat（默认模型）流式 ===")
read_ndjson(req, "normal-default")

# RAG 流式（无需文档也能走 LLM 流式）
req2 = urllib.request.Request(BASE + "/api/v1/rag/query?stream=true",
                               data=json.dumps({"query": "用一句话介绍人工智能。", "stream": True, "context_count": 3}).encode(), method="POST")
req2.add_header("Content-Type", "application/json")
req2.add_header("Authorization", "Bearer " + token)
print("=== RAG Chat 流式 ===")
read_ndjson(req2, "rag")

# 清理测试角色
try:
    urllib.request.Request(BASE + f"/api/v1/characters/{cid}", method="DELETE").add_header("Authorization", "Bearer " + token)
    urllib.request.urlopen(urllib.request.Request(BASE + f"/api/v1/characters/{cid}", method="DELETE", headers={"Authorization": "Bearer " + token}), timeout=30)
    print("CLEANED character", cid)
except Exception as e:
    print("clean note:", e)
