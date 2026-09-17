# -*- coding: utf-8 -*-
"""迁移旧 Chroma 记录：为缺少 document_id 的记录补上合成 document_id（按 user_id+source 分组），
使其能在文档列表出现并被预览。保留原 user_id/source/chunk_index 以保证隔离与原文。
先备份 chroma_db，再更新。仅处理缺 document_id 的记录，已有 document_id 的记录不动。
"""
import chromadb, json, os, shutil, uuid, sys, datetime

ROOT = "C:/Users/Administrator/Desktop/my_ai_assistant"
SRC = ROOT + "/data/chroma_db"
BAK = ROOT + "/data/chroma_db.bak_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
COL = "ai_assistant_docs"

# 1) 备份
print("备份 chroma_db ->", BAK)
shutil.copytree(SRC, BAK)
print("备份完成")

# 2) 打开集合（迁移期间请确保 8000 已停止，避免 sqlite 锁冲突）
client = chromadb.PersistentClient(path=SRC)
col = client.get_collection(COL)
all_ids = col.get(include=['metadatas'])['ids']
all_metas = col.get(include=['metadatas'])['metadatas']
print("集合中总向量数:", len(all_ids))

# 3) 找出缺 document_id 的记录，按 (user_id, source) 分组
missing = []
for idd, meta in zip(all_ids, all_metas):
    if not meta or not meta.get('document_id'):
        missing.append((idd, meta or {}))

print("缺 document_id 的向量数:", len(missing))

groups = {}
for idd, meta in missing:
    uid = meta.get('user_id')
    src = meta.get('source') or idd.rsplit('_', 1)[0]  # source 缺失则用 id stem
    key = (uid, src)
    groups.setdefault(key, []).append((idd, meta))

print("需要补 document_id 的文档组数:", len(groups))

# 4) 每组分配一个合成 document_id 并更新 metadata
upd_ids = []
upd_metas = []
migrated_docs = []
for (uid, src), items in groups.items():
    new_doc_id = uuid.uuid4().hex
    fn = (meta_src := src)  # filename 用 source（旧数据已无原名）
    # 去掉 source 里临时文件名可能带的前导空格
    fn_clean = fn.strip() if isinstance(fn, str) else fn
    for idd, meta in items:
        nm = dict(meta)
        nm['document_id'] = new_doc_id
        if not nm.get('filename'):
            nm['filename'] = fn_clean
        if 'user_id' not in nm or nm.get('user_id') is None:
            nm['user_id'] = uid
        upd_ids.append(idd)
        upd_metas.append(nm)
    migrated_docs.append({'document_id': new_doc_id, 'user_id': uid, 'source': src, 'chunks': len(items)})

# 5) 批量更新
if upd_ids:
    col.update(ids=upd_ids, metadatas=upd_metas)
    print("已更新向量数:", len(upd_ids))
else:
    print("无需更新")

# 6) 真实验证：抽样确认迁移后可用 document_id+user_id 查到
print("\n=== 迁移后抽样验证 ===")
ok = 0
for d in migrated_docs[:5]:
    did = d['document_id']; uid = d['user_id']
    r1 = col.get(where={'$and': [{'document_id': did}, {'user_id': uid}]}, include=['documents'])
    r2 = col.get(where={'$and': [{'document_id': did}, {'user_id': (uid + 9999 if isinstance(uid, int) else 'x')}]}, include=['documents'])
    owner_sees = len(r1.get('ids', [])) > 0
    other_blocked = len(r2.get('ids', [])) == 0
    print("doc %s user=%s owner_sees=%s other_blocked=%s" % (did[:8], uid, owner_sees, other_blocked))
    ok += 1 if (owner_sees and other_blocked) else 0
print("抽样验证通过组数:", ok, "/", min(5, len(migrated_docs)))

print("\n迁移摘要(前 10 个文档组):")
for d in migrated_docs[:10]:
    print("  ", json.dumps(d, ensure_ascii=False))
print("总计迁移文档组:", len(migrated_docs))
