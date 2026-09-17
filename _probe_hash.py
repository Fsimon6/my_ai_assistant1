import httpx, json, time
BASE = "http://127.0.0.1:8000"
uname = "chk%d" % int(time.time())
# register
reg = httpx.post(BASE + "/api/v1/auth/register", json={"email": uname+"@e.com","username": uname,"password":"pass123456","full_name":"C"}, timeout=10)
print("REGISTER", reg.status_code)
# login
lg = httpx.post(BASE + "/api/v1/auth/login", json={"username": uname, "password":"pass123456"}, timeout=10)
tok = lg.json().get("data",{}).get("access_token")
print("LOGIN", lg.status_code, "token?", bool(tok))
H = {"Authorization": f"Bearer {tok}"}
# required routes not 404
checks = {
  "health": (BASE+"/health", "GET", None),
  "register": (BASE+"/api/v1/auth/register", "POST", None),
  "login": (BASE+"/api/v1/auth/login", "POST", None),
  "me": (BASE+"/api/v1/auth/me", "GET", H),
  "rag_collection_info": (BASE+"/api/v1/rag/collection-info", "GET", H),
  "characters_list": (BASE+"/api/v1/characters/", "GET", H),
  "characters_create": (BASE+"/api/v1/characters/", "POST", H),
}
for name,(url,meth,hd) in checks.items():
    try:
        rr = httpx.request(meth, url, headers=hd, json={} if meth=="POST" else None, timeout=10)
        print(f"{name}: {rr.status_code}  (404=not mounted)")
    except Exception as e:
        print(f"{name}: ERR {e}")
