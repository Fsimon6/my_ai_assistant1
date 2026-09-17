# -*- coding: utf-8 -*-
import urllib.request, json, urllib.error, uuid, os, sys
BASE='http://localhost:8001'; P=print
def req(m,p,token=None,data=None,raw=False):
    h={}
    if token: h['Authorization']='Bearer '+token
    body=None
    if data is not None:
        if isinstance(data,(dict,list)) and not raw:
            body=json.dumps(data).encode(); h['Content-Type']='application/json'
        elif raw:
            b='----b'+uuid.uuid4().hex; parts=[]
            for k,v in data.get('fields',{}).items():
                parts.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'%(b,k,v)).encode())
            for nm,fn,ct in data.get('files',[]):
                parts.append(('--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\nContent-Type: text/plain\r\n\r\n'%(b,nm,fn)).encode())
                parts.append(ct if isinstance(ct,bytes) else ct.encode()); parts.append(b'\r\n')
            parts.append(('--%s--\r\n'%b).encode()); body=b''.join(parts); h['Content-Type']='multipart/form-data; boundary='+b
    r=urllib.request.Request(BASE+p,data=body,method=m,headers=h)
    try:
        resp=urllib.request.urlopen(r,timeout=60); t=resp.read().decode('utf-8','ignore')
        return resp.status,(t if raw else (json.loads(t) if t else {}))
    except urllib.error.HTTPError as e: return e.code,e.read().decode('utf-8','ignore')
def reg(n):
    pw='Pass!23'; st,r=req('POST','/api/v1/auth/register',data={'username':n,'email':n+'@ex.com','password':pw,'confirm_password':pw})
    st,r=req('POST','/api/v1/auth/login',data={'username':n,'password':pw})
    return (r.get('data') or {}).get('access_token')
def up(t,l,s):
    c='doc %s secret TOKEN-%s\n'%(l,s); st,r=req('POST','/api/v1/rag/upload',token=t,raw=True,data={'fields':{'metadata':json.dumps({'label':l})},'files':[('file',l+'.txt',c)]})
    if isinstance(r,str):
        try:r=json.loads(r)
        except:pass
    return st,r
def q(t,query):
    st,r=req('POST','/api/v1/rag/query',token=t,data={'query':query,'stream':False}); return st,r

ta=reg('e2e29_A'); tb=reg('e2e29_B')
P('A uid token len', len(ta), 'B uid token len', len(tb))
sa,sb='A29ZZZ','B29YYY'
sta,ua=up(ta,'docA',sa); stb,ub=up(tb,'docB',sb)
da=ua.get('document_id') if isinstance(ua,dict) else None
db=ub.get('document_id') if isinstance(ub,dict) else None
P('docA',da,'docB',db)
st,qa=q(ta,'TOKEN-A29ZZZ'); P('A query A-secret resp:', (qa.get('response') if isinstance(qa,dict) else qa)[:160])
st,qb=q(tb,'TOKEN-A29ZZZ'); P('B query A-secret resp:', (qb.get('response') if isinstance(qb,dict) else qb)[:220])
P('B sees A?', isinstance(qb,dict) and sa in (qb.get('response') or ''))
# 元数据真相
import sys; sys.path.insert(0,os.getcwd())
from backend.services.vector_service import VectorStoreManager
vs=VectorStoreManager(persist_directory='./data/chroma_prod29')
col=vs.vector_store._collection
g=col.get(include=['metadatas']); ids=g.get('ids',[]); metas=g.get('metadatas',[])
P('TOTAL',len(ids))
for i,m in zip(ids,metas):
    P('  id=',i,'user_id=',m.get('user_id'),'doc_id=',m.get('document_id'))
