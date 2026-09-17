import asyncio
import hashlib
import tempfile
import shutil
import sys

# ---- Stub LLM so VectorStoreManager / AIAssistantEmbeddings work WITHOUT API key / torch ----
DIM = 384

class FakeLLM:
    async def generate_embeddings(self, texts, model="text-embedding-3-small"):
        out = []
        for t in texts:
            h = hashlib.sha256(t.encode("utf-8")).digest()
            vec = [((h[i % len(h)] / 255.0) - 0.5) for i in range(DIM)]
            out.append(vec)
        return out

    async def chat_completion(self, messages, stream=True):
        async def gen():
            yield "这是一段来自模拟 LLM 的流式回答，用于验证 RAG 检索链路可贯通。"
        return gen()

import backend.services.llm_service as llm_mod
llm_mod._llm_instance = FakeLLM()
# ensure get_llm returns our fake regardless of singleton state
_orig_get = llm_mod.get_llm
def fake_get_llm():
    return FakeLLM()
llm_mod.get_llm = fake_get_llm

from backend.services.vector_service import VectorStoreManager
from backend.services.rag_service import RagService

tmp = tempfile.mkdtemp()
print("=== Chroma CRUD verification (chromadb 1.5.9, real production code) ===")
try:
    mgr = VectorStoreManager(persist_directory=tmp)
    print("[init] VectorStoreManager constructed OK")

    docs = [
        {"id": "d1", "content": "机器学习是人工智能的一个分支", "metadata": {"source": "t1"}},
        {"id": "d2", "content": "深度学习是机器学习的一个子集", "metadata": {"source": "t2"}},
        {"id": "d3", "content": "自然语言处理涉及文本理解", "metadata": {"source": "t3"}},
    ]
    ids = asyncio.run(mgr.add_documents(docs))
    print("[add] ids =", ids)

    info = mgr.get_collection_info()
    print("[info] total_documents =", info.get("total_documents"))

    res = asyncio.run(mgr.search(query="人工智能", k=2))
    print("[search] returned", len(res), "results; keys =", sorted(res[0].keys()) if res else None)
    for r in res:
        print("   -", r["id"], r["score"], r["content"][:12])

    ok = asyncio.run(mgr.delete_documents(ids=["d2"]))
    print("[delete] success =", ok, "; total =", mgr.get_collection_info().get("total_documents"))
except Exception as e:
    import traceback
    print("!!! Chroma CRUD ERROR:", repr(e))
    traceback.print_exc()

print()
print("=== Persistence verification (reopen) ===")
try:
    mgr2 = VectorStoreManager(persist_directory=tmp)
    info2 = mgr2.get_collection_info()
    print("[reopen] total_documents =", info2.get("total_documents"))
    res2 = asyncio.run(mgr2.search(query="机器学习", k=1))
    print("[reopen search] content =", res2[0]["content"] if res2 else None)
except Exception as e:
    import traceback
    print("!!! Persistence ERROR:", repr(e))
    traceback.print_exc()

print()
print("=== RagService init + rag_query (cache miss path) ===")
try:
    svc = RagService()
    print("[rag init] RagService constructed OK")
    chunks = []
    async def collect():
        async for c in svc.rag_query("什么是深度学习", context_count=2):
            chunks.append(c)
    asyncio.run(collect())
    print("[rag_query] collected chunks:", len(chunks), "; content[:30] =", "".join(chunks)[:30])
except Exception as e:
    import traceback
    print("!!! RagService ERROR:", repr(e))
    traceback.print_exc()

shutil.rmtree(tmp, ignore_errors=True)
print("\nDONE")
