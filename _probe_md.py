import urllib.request, json, os
BASE = "http://127.0.0.1:8000"

def req(m, p, t=None, b=None, f=None):
    h = {}
    if t:
        h["Authorization"] = "Bearer " + t
    data = None
    if f:
        import io
        boundary = "bnd123"
        parts = []
        for n, (fn, fo, c) in f.items():
            parts.append(("--" + boundary).encode())
            parts.append(('Content-Disposition: form-data; name="%s"; filename="%s"' % (n, fn)).encode())
            parts.append(("Content-Type: %s" % c).encode())
            parts.append(b"")
            parts.append(fo.read())
        parts.append(("--" + boundary + "--").encode())
        parts.append(b"")
        data = b"\r\n".join(parts)
        h["Content-Type"] = "multipart/form-data; boundary=" + boundary
    elif b is not None:
        data = json.dumps(b).encode()
        h["Content-Type"] = "application/json"
    r = urllib.request.Request(BASE + p, data=data, headers=h, method=m)
    try:
        with urllib.request.urlopen(r, timeout=60) as x:
            return x.status, x.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()

u = "probe_" + os.urandom(3).hex()
req("POST", "/api/v1/auth/register", b={"username": u, "password": "pw123456", "email": u + "@x.com"})
tok = json.loads(req("POST", "/api/v1/auth/login", b={"username": u, "password": "pw123456"})[1])["access_token"]
md = r"C:\Users\Administrator\Desktop\my_ai_assistant\_probe_tmp\probe.md"
with open(md, "rb") as fh:
    print(req("POST", "/api/v1/rag/upload", t=tok, f={"file": (os.path.basename(md), fh, "text/markdown")}))
