import urllib.request, json, time, random

BASE = "http://127.0.0.1:8000"

def _req(method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        r = urllib.request.urlopen(req, timeout=60)
        raw = r.read().decode()
        try: return r.status, json.loads(raw)
        except Exception: return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try: return e.code, json.loads(raw)
        except Exception: return e.code, raw

def register(u, pw): _req("POST","/api/v1/auth/register",{"username":u,"email":u+"@x.com","password":pw,"password_confirm":pw})
def login(u, pw):
    s,d=_req("POST","/api/v1/auth/login",{"username":u,"password":pw}); return d["data"]["access_token"]
def create(u,token):
    s,d=_req("POST","/api/v1/characters/",{"name":u,"system_prompt":"sys "+u,"model":"","api_key":""},token)
    assert s==200,("create",s,d); return d.get("data") or d.get("character") or d
def detail(cid,token):
    s,d=_req("GET",f"/api/v1/characters/{cid}",token=token); return s,(d.get("data") or d.get("character") or d)
def update(cid,body,token): return _req("PUT",f"/api/v1/characters/{cid}",body,token)
def deletec(cid,token): return _req("DELETE",f"/api/v1/characters/{cid}",token=token)
def stream_chat(cid,token,msg):
    req=urllib.request.Request(BASE+f"/api/v1/characters/{cid}/speak/stream",data=json.dumps({"message":msg}).encode(),method="POST")
    req.add_header("Content-Type","application/json"); req.add_header("Authorization","Bearer "+token)
    chunks=[]
    try:
        r=urllib.request.urlopen(req,timeout=60)
        for line in r:
            line=line.decode().strip()
            if not line: continue
            o=json.loads(line)
            if o.get("type")=="chunk": chunks.append(o["content"])
            elif o.get("type")=="error": print("  stream ERROR:",o.get("error"))
        return chunks,200
    except urllib.error.HTTPError as e:
        return None,e.code

from backend.database.base import SessionLocal
from backend.models.character import Conversation, Text, AICharacter

ok=True
def check(c,l):
    global ok
    print(("PASS" if c else "FAIL"),"-",l)
    if not c: ok=False

u="e2e80u%d"%random.randint(1000,9999); v="e2e80v%d"%random.randint(1000,9999); pw="Test123456"
register(u,pw); register(v,pw); tU=login(u,pw); tV=login(v,pw)
C=create(u,tU); cid=C["id"]; check(bool(cid),"Create C")
s,d=update(cid,{"model":"deepseek-v3"},tU); check(s>=400,"Update model-only rejected (BOTH-OR-NEITHER)")
s,d=update(cid,{"model":"deepseek-v3","api_key":"sk-validkey12345"},tU); check(s==200,"Update model+key -> 200")
D=create("e2e80D",tU); did=D["id"]
chunks,st=stream_chat(did,tU,"你好"); check(st==200 and len(chunks)>0,"D streaming default model (regression)")
time.sleep(1)
db=SessionLocal(); uid=db.query(AICharacter).filter(AICharacter.id==int(did)).first().user_id
check(db.query(Conversation).filter(Conversation.character_id==int(did),Conversation.user_id==uid).count()>0,"Conversation created for D")
s,d=deletec(did,tU); check(s==200,"Delete D -> 200")
db2=SessionLocal()
check(db2.query(Conversation).filter(Conversation.character_id==int(did)).count()==0,"Conversations cascade-deleted")
check(db2.query(Text).join(Conversation,Text.conversation_id==Conversation.id).filter(Conversation.character_id==int(did)).count()==0,"Texts cascade-deleted")
db.close(); db2.close()
s,d=detail(cid,tV); check(s==404,"V cannot GET C (404)")
s,d=update(cid,{"name":"hack"},tV); check(s==404,"V cannot UPDATE C (404)")
s,d=deletec(cid,tV); check(s==404,"V cannot DELETE C (404)")
s,d=deletec(cid,tU); check(s==200,"Delete C -> 200")
chunks,st=stream_chat(cid,tU,"hi"); check(st==404,"Deleted C chat -> 404")
print("\nRESULT:","ALL PASS" if ok else "SOME FAIL")
