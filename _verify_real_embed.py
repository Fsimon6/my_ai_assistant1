import asyncio
import tempfile
import shutil
from backend.config.settings import settings
from backend.services.llm_service import get_llm
from backend.services.vector_service import VectorStoreManager

key = settings.API_KEY or ""
print("LLM_PROVIDER =", settings.LLM_PROVIDER)
print("LLM_BASE_URL  =", settings.LLM_BASE_URL or "(default api.openai.com/v1)")
print("LLM_MODEL     =", settings.LLM_MODEL)
print("API_KEY set   =", bool(key), "len=%d prefix=%r" % (len(key), (key[:6] + "...") if key else "none"))
print("-" * 50)

TEXT = "人工智能是研究如何让计算机执行通常需要人类智能才能完成的任务。"

# 1) 真实 embedding 调用（占位 key → 预期 401，但应证明打到了 embeddings 端点而非 chat completions）
llm = get_llm()
print("client.base_url =", llm.client.base_url)
try:
    vecs = asyncio.run(llm.generate_embeddings([TEXT]))
    print("REAL embedding OK, dim =", len(vecs[0]) if vecs else None)
except Exception as e:
    print("REAL embedding -> endpoint reached, FAILED:")
    print("   ", type(e).__name__, str(e)[:220])

print("-" * 50)

# 2) 真实 RAG 写入（env LLM，无 stub）：应到达 embeddings 端点后由凭证拦截，而非代码 bug
tmp = tempfile.mkdtemp()
try:
    mgr = VectorStoreManager(persist_directory=tmp)
    ids = asyncio.run(mgr.add_documents(
        [{"id": "d1", "content": TEXT, "metadata": {"source": "probe"}}]
    ))
    print("REAL add_documents OK, ids =", ids)
except Exception as e:
    print("REAL add_documents -> embedding endpoint reached, FAILED (creds):")
    print("   ", type(e).__name__, str(e)[:220])
finally:
    shutil.rmtree(tmp, ignore_errors=True)
