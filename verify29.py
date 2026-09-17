# -*- coding: utf-8 -*-
"""阶段29 RAG 文档生命周期 + A/B 隔离（生产配置端口 8001）"""
import urllib.request, json, urllib.error, uuid, os, sys, asyncio, shutil

BASE = os.environ.get('VERIFY29_BASE', 'http://localhost:8001')
P = print

def req(method, path, token=None, data=None, raw=False):
    url = BASE + path
    body = None
    headers = {}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if data is not None:
        if isinstance(data, (dict, list)) and not raw:
            body = json.dumps(data).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        elif raw:
            boundary = '----bnd' + uuid.uuid4().hex
            parts = []
            for k, v in data.get('fields', {}).items():
                parts.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (boundary, k, v)).encode('utf-8'))
            for nm, fn, ct in data.get('files', []):
                parts.append(('--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\nContent-Type: text/plain\r\n\r\n' % (boundary, nm, fn)).encode('utf-8'))
                parts.append(ct if isinstance(ct, bytes) else ct.encode('utf-8'))
                parts.append(b'\r\n')
            parts.append(('--%s--\r\n' % boundary).encode('utf-8'))
            body = b''.join(parts)
            headers['Content-Type'] = 'multipart/form-data; boundary=' + boundary
    r = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        resp = urllib.request.urlopen(r, timeout=60)
        txt = resp.read().decode('utf-8', 'ignore')
        return resp.status, (txt if raw else (json.loads(txt) if txt else {}))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'ignore')
    except Exception as e:
        return -1, str(e)

def reg(n):
    pw = 'Pass!23'
    req('POST', '/api/v1/auth/register', data={'username': n, 'email': n+'@ex.com', 'password': pw, 'confirm_password': pw})
    st, r = req('POST', '/api/v1/auth/login', data={'username': n, 'password': pw})
    return (r.get('data') or {}).get('access_token')

def up(t, l, s):
    c = '文档标识 %s\n机密代号 MARK-%s-XYZ\n机密内容 TOKEN-%s\n' % (l, l, s)
    st, r = req('POST', '/api/v1/rag/upload', token=t, raw=True,
                data={'fields': {'metadata': json.dumps({'label': l})}, 'files': [('file', l+'.txt', c)]})
    if isinstance(r, str):
        try: r = json.loads(r)
        except Exception: pass
    return st, (r.get('document_id') if isinstance(r, dict) else None)

def q(t, query):
    return req('POST', '/api/v1/rag/query', token=t, data={'query': query, 'stream': False})

def delete(t, ids):
    return req('DELETE', '/api/v1/rag/documents', token=t, data={'document_ids': ids})


def main():
    ok = True
    P('=== 1. Auth (prod) ===')
    ta = reg('e2e29_A'); tb = reg('e2e29_B')
    P('  A/B login OK')
    ok = ok and ta and tb

    P('=== 2. Character list (prod) ===')
    st, r = req('GET', '/api/v1/characters', token=ta)
    P('  characters', st, 'count=', len(r.get('characters', [])) if isinstance(r, dict) else r)
    ok = ok and st == 200

    P('=== 3. Upload 返回真实 document_id ===')
    _, da = up(ta, 'docA', 'A29ZZZ')
    _, db = up(tb, 'docB', 'B29YYY')
    P('  docA_id=', da, '| docB_id=', db)
    ok = ok and da and db

    P('=== 4. 向量级隔离（确定性，user_id where 过滤）===')
    d = './data/chroma_iso'; shutil.rmtree(d, ignore_errors=True)
    from backend.services.vector_service import VectorStoreManager
    async def iso():
        vs = VectorStoreManager(persist_directory=d)
        await vs.add_documents([
            {'content': 'secret A TOKEN-AAA MARK-A1', 'metadata': {'user_id': 1, 'document_id': 'da', 'chunk_index': 0}, 'id': 'da_0'},
            {'content': 'secret B TOKEN-BBB MARK-B1', 'metadata': {'user_id': 2, 'document_id': 'db', 'chunk_index': 0}, 'id': 'db_0'},
        ])
        ra = await vs.search('TOKEN-AAA', k=5, filter_dict={'user_id': 1})
        rb = await vs.search('TOKEN-AAA', k=5, filter_dict={'user_id': 2})
        return [x['content'] for x in ra], [x['content'] for x in rb]
    ra, rb = asyncio.run(iso())
    P('  user1 检索 ->', ra, '| user2 检索 ->', rb)
    iso_ok = (ra == ['secret A TOKEN-AAA MARK-A1']) and (rb == ['secret B TOKEN-BBB MARK-B1'])
    ok = ok and iso_ok
    shutil.rmtree(d, ignore_errors=True)

    P('=== 5. A 删除自有文档（闭环）===')
    _, dd = delete(ta, [da]); dc = dd.get('deleted_count') if isinstance(dd, dict) else None
    P('  deleted_count=', dc, dd.get('message') if isinstance(dd, dict) else dd)
    ok = ok and dc and dc > 0

    P('=== 6. A 删除后重查（标记应消失）===')
    _, qa2 = q(ta, 'TOKEN-A29ZZZ')
    see_after = 'MARK-A29ZZZ-XYZ' in (qa2.get('response') or '')
    P('  删除后 A 仍检索到标记?', see_after, '(应 False)')
    ok = ok and not see_after

    P('=== 7. A 越权删除 B 文档（应 blocked）===')
    _, dx = delete(ta, [db]); dcx = dx.get('deleted_count') if isinstance(dx, dict) else None
    P('  cross-delete deleted_count=', dcx, '(须 0)')
    ok = ok and dcx == 0

    P('=== 8. B 文档仍存在（未被误删）===')
    _, qb = q(tb, 'TOKEN-B29YYY')
    bok = 'TOKEN-B29YYY' in (qb.get('response') or '')
    P('  B 仍可检索自身文档?', bok)
    ok = ok and bok

    P('=== 9. 清理测试向量（生命周期闭环）===')
    # A 的 docA 已在 step5 删除；此处幂等再删（deleted_count 应为 0）
    _, dda = delete(ta, [da]); dca = dda.get('deleted_count') if isinstance(dda, dict) else None
    # 删除 B 残留测试文档，避免 Chroma 累积测试向量
    _, ddb = delete(tb, [db]); dcb = ddb.get('deleted_count') if isinstance(ddb, dict) else None
    P('  cleanup A re-delete_count=', dca, '| B delete_count=', dcb)
    ok = ok and dcb and dcb > 0

    P('\n=== RESULT:', 'PASS' if ok else 'FAIL', '===')
    sys.exit(0 if ok else 1)

if __name__ == '__main__':
    main()
