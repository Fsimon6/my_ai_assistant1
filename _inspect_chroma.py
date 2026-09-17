import chromadb, json
from collections import defaultdict

PATH = r"C:\Users\Administrator\Desktop\my_ai_assistant\data\chroma_db"
KNOWN = ["6d62e2010b5a416fb71e193b4a8e57c6", "8df317b3f09144ba9a52c0ed139cf221"]

client = chromadb.PersistentClient(path=PATH)
col = client.get_collection("ai_assistant_docs")

data = col.get(include=["metadatas", "documents"])
metas = data["metadatas"]
docs = data["documents"]
ids = data["ids"]
print("TOTAL vectors:", len(metas))

# 聚合所有 document_id
agg = defaultdict(lambda: {"filename": None, "user_id": None, "chunks": 0, "chunk_idx": []})
for m, d, cid in zip(metas, docs, ids):
    did = m.get("document_id")
    a = agg[did]
    a["filename"] = m.get("filename") or m.get("source")
    a["user_id"] = m.get("user_id")
    a["chunks"] += 1
    a["chunk_idx"].append(m.get("chunk_index"))

print("\n=== 已知 doc_id 在 Chroma 中的真实状态 ===")
for did in KNOWN:
    r = col.get(where={"document_id": did}, include=["metadatas", "documents"])
    sub = r.get("metadatas", []) or []
    print(f"\n[KNOWN] {did}")
    print("  single where 命中向量数:", len(sub))
    if sub:
        for m in sub:
            print("    user_id=", repr(m.get("user_id")), "filename=", m.get("filename"), "chunk_index=", m.get("chunk_index"))
        # 用第一个向量的 user_id 测试 $and
        uy = sub[0].get("user_id")
        r2 = col.get(where={"$and": [{"document_id": did}, {"user_id": uy}]}, include=["documents"])
        print("  $and(doc+user) 命中向量数:", len(r2.get("documents", [])))
    else:
        print("  >>> Chroma 中不存在该 document_id！")

print("\n=== 全部 document_id 聚合（文件名 / user_id / 向量数）===")
for did, a in sorted(agg.items(), key=lambda kv: -(kv[1]["chunks"])):
    print(f"{did}  user_id={repr(a['user_id'])}  chunks={a['chunks']}  file={a['filename']}")
