import json
import httpx

BASE = "http://127.0.0.1:8000"


def show(name, resp):
    print(f"{name} -> {resp.status_code} {resp.text[:300]}")


# 1. health
r = httpx.get(BASE + "/health", timeout=10)
show("HEALTH", r)

# 2. register
import time as _t
_uname = "u%d" % int(_t.time())
r = httpx.post(BASE + "/api/v1/auth/register", json={
    "email": _uname + "@example.com",
    "username": _uname,
    "password": "pass123456",
    "full_name": "Phase6",
}, timeout=10)
show("REGISTER", r)

# 3. login (JSON username/password)
r = httpx.post(BASE + "/api/v1/auth/login", json={
    "username": _uname,
    "password": "pass123456",
}, timeout=10)
show("LOGIN", r)
token = None
try:
    token = r.json().get("data", {}).get("access_token")
    print("TOKEN_LEN", len(token) if token else 0)
except Exception as e:
    print("TOKEN_PARSE_FAIL", e)

# 4. /me with token
if token:
    r = httpx.get(BASE + "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}, timeout=10)
    show("ME(token)", r)

# 5. /me no token
r = httpx.get(BASE + "/api/v1/auth/me", timeout=10)
show("ME(no_token)", r)

# 6. /me invalid token
r = httpx.get(BASE + "/api/v1/auth/me", headers={"Authorization": "Bearer invalid.token.here"}, timeout=10)
show("ME(bad_token)", r)

# 7. login wrong password
r = httpx.post(BASE + "/api/v1/auth/login", json={
    "username": "phase5user",
    "password": "wrongpass",
}, timeout=10)
show("LOGIN(wrong_pw)", r)
