import urllib.request, json, io, sys, zipfile, os, tempfile, time

BASE = "http://127.0.0.1:8000"

def jreq(method, path, token=None, json_body=None, files=None):
    url = BASE + path
    data = None
    headers = {}
    if token:
        headers["Authorization"] = "Bearer " + token
    if files:
        boundary = "----bnd%d" % id(files)
        parts = []
        for name, (fname, fobj, ctype) in files.items():
            parts.append(("--" + boundary).encode())
            parts.append(('Content-Disposition: form-data; name="%s"; filename="%s"' % (name, fname)).encode())
            parts.append(("Content-Type: %s" % ctype).encode())
            parts.append(b"")
            parts.append(fobj.read())
        parts.append(("--" + boundary + "--").encode())
        parts.append(b"")
        data = b"\r\n".join(p if isinstance(p, bytes) else p.encode() for p in parts)
        headers["Content-Type"] = "multipart/form-data; boundary=" + boundary
    else:
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")

results = []
def ok(name, cond, extra=""):
    results.append((name, cond, extra))
    print(("PASS " if cond else "FAIL ") + name + ("  " + extra if extra else ""))

def login(username, password):
    r = jreq("POST", "/api/v1/auth/login", json_body={"username": username, "password": password})
    assert r[0] == 200, "login %s: %s %s" % (username, r[0], r[1][:200])
    body = json.loads(r[1])
    token = body.get("access_token") or (body.get("data") or {}).get("access_token")
    assert token, "no token"
    return token

rh = jreq("GET", "/health")
ok("HEALTH", rh[0] == 200, "code=%s" % rh[0])

u = "regp%d" % int(time.time())
jreq("POST", "/api/v1/auth/register", json_body={"username": u, "password": "pw123456", "email": u + "@x.com"})
tok = login(u, "pw123456")
ok("LOGIN", bool(tok))

tmp = tempfile.mkdtemp()
txt_path = os.path.join(tmp, "回归测试.txt")
with open(txt_path, "w", encoding="utf-8") as f:
    f.write("回归测试文档。第一段：项目支持向量检索。第二段：按文档预览功能已修复。")
md_path = os.path.join(tmp, "回归测试.md")
with open(md_path, "w", encoding="utf-8") as f:
    f.write("# 回归标题\n\n这是 **Markdown** 正文，用于验证 .md 解析。\n")

def make_docx(path, text):
    document_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                    '<w:body><w:p><w:r><w:t>%s</w:t></w:r></w:p></w:body></w:document>' % text)
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
          '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document_xml)

docx_path = os.path.join(tmp, "回归测试.docx")
make_docx(docx_path, "这是 docx 正文内容，用于验证 DOCX 解析是否成功。")

xlsx_path = r"C:\Users\Administrator\Desktop\my_ai_assistant\直邮一店 8.20号订单.xlsx"

uploaded_ids = []

def upload_and_check(label, path, ctype, tok):
    with open(path, "rb") as fh:
        r = jreq("POST", "/api/v1/rag/upload", token=tok, files={"file": (os.path.basename(path), fh, ctype)})
    ok("上传 " + label, r[0] == 200, r[1][:160])
    if r[0] == 200:
        did = json.loads(r[1]).get("document_id")
        uploaded_ids.append(did)
        rp = jreq("GET", "/api/v1/rag/documents/" + did, token=tok)
        ok("预览 " + label, rp[0] == 200, "code=%s" % rp[0])
        rq = jreq("POST", "/api/v1/rag/query", token=tok,
                  json_body={"query": "文档讲了什么", "stream": False, "context_count": 3, "document_id": did})
        ok("RAG查询 " + label, rq[0] == 200, "code=%s" % rq[0])
        return did
    return None

upload_and_check("TXT", txt_path, "text/plain", tok)
upload_and_check("MD", md_path, "text/markdown", tok)
upload_and_check("DOCX", docx_path, "application/vnd.openxmlformats-officedocument.wordprocessingml.document", tok)
upload_and_check("XLSX", xlsx_path, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", tok)

rl = jreq("GET", "/api/v1/rag/documents", token=tok)
ok("文档列表", rl[0] == 200, "code=%s" % rl[0])

if uploaded_ids:
    rd = jreq("DELETE", "/api/v1/rag/documents", token=tok, json_body={"document_ids": uploaded_ids})
    print("清理 A 文档:", rd[0], rd[1][:80])

print("\n==== SUMMARY ====")
passed = sum(1 for _, c, _ in results if c)
print("%d/%d passed" % (passed, len(results)))
sys.exit(0 if passed == len(results) else 1)
