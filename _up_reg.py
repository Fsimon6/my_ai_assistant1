import sys, os, json, shutil, requests
sys.path.insert(0, r'C:\Users\Administrator\Desktop\my_ai_assistant')
ROOT = r'C:\Users\Administrator\Desktop\my_ai_assistant'
BASE = 'http://127.0.0.1:8000'
USER, PW = 'envtest', 'EnvTest123'

s = requests.Session()
r = s.post(f'{BASE}/api/v1/auth/login', json={'username': USER, 'password': PW}, timeout=20)
print('LOGIN', r.status_code, 'token_len', len(r.json().get('data', {}).get('access_token', '')))
H = {'Authorization': f'Bearer {r.json()["data"]["access_token"]}'}

# 复制真实 Excel 为测试副本（不动原文件）
SRC = os.path.join(ROOT, '直邮一店 8.20号订单.xlsx')
COPY = os.path.join(ROOT, '_test_upload_real.xlsx')
shutil.copyfile(SRC, COPY)

# 上传（模拟浏览器 FormData file 字段）
with open(COPY, 'rb') as f:
    up = s.post(f'{BASE}/api/v1/rag/upload',
                files={'file': ('_test_upload_real.xlsx', f, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},
                headers=H, timeout=180)
j = up.json()
print('UPLOAD', up.status_code, 'success=', j.get('success'), 'doc_id=', j.get('document_id'), 'chunks=', j.get('total_chunks'))

did = j.get('document_id')

# 列表出现
dj = s.get(f'{BASE}/api/v1/rag/documents', headers=H, timeout=30).json()
in_list = any(d['document_id'] == did for d in dj.get('documents', []))
print('IN_LIST', in_list)

# 表格结构（预览数据）
ts = s.get(f'{BASE}/api/v1/rag/documents/{did}/table-structure', headers=H, timeout=30).json()
sh = ts.get('structure', {}).get('workbook', {}).get('sheets', [{}])[0]
print('TABLE sheet=', sh.get('sheet_name'), 'col_count=', sh.get('tables', [{}])[0].get('col_count'), 'row_count=', sh.get('tables', [{}])[0].get('row_count'))

# RAG 查询
q = s.post(f'{BASE}/api/v1/rag/query', json={'query': '这个表格一共有多少列？', 'stream': False, 'context_count': 3, 'document_id': did}, headers=H, timeout=180).json()
print('RAG_ANSWER', repr(q.get('response', ''))[:200])

# 清理
s.delete(f'{BASE}/api/v1/rag/documents', json={'document_ids': [did]}, headers=H, timeout=60)
for p in (__file__, COPY) + tuple(__import__('glob').glob(os.path.join(ROOT, 'data', 'table_originals', f'{did}.*'))):
    try: os.remove(p)
    except: pass
print('CLEANED')
