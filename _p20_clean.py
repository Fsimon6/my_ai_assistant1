import sys, json, sqlite3, httpx
sys.path.insert(0, 'C:\\Users\\Administrator\\Desktop\\my_ai_assistant')
P = lambda *a: print(*a, flush=True)
BASE = 'http://localhost:8000'; DB = 'my_ai_assistant.db'
with open('p20_persist.txt') as f:
    d = json.load(f)
uA, pw, uB, uidA, uidB = d['uA'], d['pw'], d['uB'], int(d['uidA']), int(d['uidB'])
cidA, cidB, cidBown = d['cidA'], d['cidB'], d['cidBown']

def login(un, p):
    r = httpx.post(BASE + '/api/v1/auth/login', json={'username': un, 'password': p}, timeout=15)
    return (r.json().get('data') or {}).get('access_token')
def conv_count(uid, cid):
    con = sqlite3.connect(DB); cur = con.cursor()
    cur.execute('SELECT COUNT(*) FROM conversations WHERE user_id=? AND character_id=?', (uid, cid))
    n = cur.fetchone()[0]; con.close(); return n

tokA = login(uA, pw); HA = {'Authorization': 'Bearer ' + tokA}
tokB = login(uB, pw); HB = {'Authorization': 'Bearer ' + tokB}

# ---- §17 级联验证：删除 Character 不应删除 Conversation ----
before = conv_count(uidA, int(cidA))
P('conv for (A,charA) before DELETE =', before, '(expect 2)')
delA = httpx.delete(BASE + '/api/v1/characters/%s' % cidA, headers=HA, timeout=15)
P('DELETE charA =', delA.status_code, '(expect 200)')
P('GET charA after delete =', httpx.get(BASE + '/api/v1/characters/%s' % cidA, headers=HA, timeout=15).status_code, '(expect 404)')
after = conv_count(uidA, int(cidA))
P('conv for (A,charA) after DELETE charA =', after, '(expect still 2 → 无级联删除)')
P('CASCADE CHECK PASS =', after == before and before > 0)

# ---- 删除剩余测试角色 ----
P('DELETE charB_byA =', httpx.delete(BASE + '/api/v1/characters/%s' % cidB, headers=HA, timeout=15).status_code)
P('DELETE charB_own =', httpx.delete(BASE + '/api/v1/characters/%s' % cidBown, headers=HB, timeout=15).status_code)

# ---- 清理测试数据（逐层删除，避免误删真实数据）----
con = sqlite3.connect(DB); cur = con.cursor()
cur.execute("SELECT id FROM users WHERE username LIKE 'chat_persist_test_%'")
tids = [r[0] for r in cur.fetchall()]
P('test user ids =', tids)
if tids:
    ph = ','.join('?' * len(tids))
    cur.execute(f"DELETE FROM texts WHERE conversation_id IN (SELECT id FROM conversations WHERE user_id IN ({ph}))", tids)
    cur.execute(f"DELETE FROM conversations WHERE user_id IN ({ph})", tids)
    cur.execute(f"DELETE FROM ai_characters WHERE user_id IN ({ph}) OR name LIKE 'CHAT_PERSIST_TEST%'", tids)
    cur.execute(f"DELETE FROM users WHERE id IN ({ph})", tids)
    con.commit()
cur.execute('SELECT COUNT(*) FROM users'); users_now = cur.fetchone()[0]
cur.execute('SELECT COUNT(*) FROM ai_characters'); chars_now = cur.fetchone()[0]
cur.execute(f"SELECT COUNT(*) FROM conversations WHERE user_id IN ({ph})" if tids else "SELECT 0", tids if tids else ())
conv_now = cur.fetchone()[0]
cur.execute(f"SELECT COUNT(*) FROM texts t JOIN conversations c ON t.conversation_id=c.id WHERE c.user_id IN ({ph})" if tids else "SELECT 0", tids if tids else ())
texts_now = cur.fetchone()[0]
con.close()
P('AFTER CLEANUP: users=%d (expect 15 real) ai_characters=%d (expect 0) test_conversations=%d (expect 0) test_texts=%d (expect 0)' % (users_now, chars_now, conv_now, texts_now))
P('=== _p20_clean done ===')
