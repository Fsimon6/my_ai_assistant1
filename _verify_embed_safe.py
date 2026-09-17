import asyncio
import tempfile
import shutil
import hashlib
import backend.services.llm_service as llm_mod
from backend.config.settings import settings
from backend.services.vector_service import VectorStoreManager, EmbeddingError

EMB_DIM = 384

def fake_vecs(texts):
    return [[((hashlib.sha256(t.encode()).digest()[i % 32]) / 255) - 0.5
             for i in range(EMB_DIM)] for t in texts]

class FailLLM:
    async def generate_embeddings(self, texts, model=None):
        raise RuntimeError("模拟 embedding API 401 / 网络异常")
    async def chat_completion(self, messages, stream=True):
        async def g():
            yield "x"
        return g()

class OkLLM:
    async def generate_embeddings(self, texts, model=None):
        return fake_vecs(texts)
    async def chat_completion(self, messages, stream=True):
        async def g():
            yield "x"
        return g()

DOC = {"id": "d1", "content": "人工智能是研究如何让计算机执行通常需要人类智能才能完成的任务。", "metadata": {"source": "probe"}}

def run(mode):
    tmp = tempfile.mkdtemp()
    try:
        # 通过单例 _llm_instance 注入 stub，确保被 AIAssistantEmbeddings 真正使用
        llm_mod._llm_instance = OkLLM() if mode == "ok" else FailLLM()
        mgr = VectorStoreManager(persist_directory=tmp)
        # 解耦检查：embedding model 来自 settings.EMBEDDING_MODEL，而非 LLM_MODEL
        print(f"      embedding_model={mgr.embeddings.embedding_model!r} (LLM_MODEL={settings.LLM_MODEL!r})")
        before = mgr.get_collection_info()["total_documents"]
        if mode == "ok":
            ids = asyncio.run(mgr.add_documents([DOC]))
            after = mgr.get_collection_info()["total_documents"]
            res = asyncio.run(mgr.search(query=DOC["content"], k=1))
            ok = after == before + 1 and len(res) == 1
            print(f"[OK ] count {before}->{after} 写入={ids} 检索命中={len(res)} 维度={len(fake_vecs(['x'])[0])} -> {'PASS' if ok else 'FAIL'}")
        else:
            try:
                asyncio.run(mgr.add_documents([DOC]))
                print("[FAIL] BUG: add_documents 未抛异常（空向量已写入！）")
            except EmbeddingError as e:
                after = mgr.get_collection_info()["total_documents"]
                no_doc = (after == before)
                print(f"[FAIL] add_documents 抛出 EmbeddingError: {str(e)[:50]}...")
                print(f"      count 失败前后不变 {before}=={after} -> {'PASS' if no_doc else 'FAIL'} (Chroma 未写入该文档)")
                try:
                    asyncio.run(mgr.search(query=DOC["content"], k=1))
                    print("      [WARN] search 未抛异常（检索未依赖 embedding？）")
                except EmbeddingError:
                    print("      search 也因 embedding 失败正确拒绝（符合预期）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

run("ok")
run("fail")
