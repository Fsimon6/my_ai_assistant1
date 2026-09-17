import asyncio
import backend.services.llm_service as llm_mod

calls = {"n": 0}

class FakeLLM:
    async def generate_embeddings(self, texts, model="text-embedding-3-small"):
        import hashlib
        return [[((hashlib.sha256(t.encode()).digest()[i % 32]) / 255) - 0.5
                 for i in range(384)] for t in texts]

    async def chat_completion(self, messages, stream=True):
        calls["n"] += 1
        async def gen():
            yield "这是一段足够长的模拟RAG流式回答，用于验证缓存命中路径是否正常工作。"
        return gen()

llm_mod.get_llm = lambda: FakeLLM()

from backend.services.rag_service import RagService

async def run(q):
    svc = RagService()
    out = []
    async for c in svc.rag_query(q, context_count=2):
        out.append(c)
    return "".join(out)

Q = "什么是深度学习"
async def main():
    r1 = await run(Q)
    print("1st query -> chat_completion calls =", calls["n"], "| cached? ", "否" if calls["n"] == 1 else "异常")
    r2 = await run(Q)
    print("2nd query -> chat_completion calls =", calls["n"], "| 缓存命中(未增)?" , "是" if calls["n"] == 1 else "否")
    r3 = await run(Q)
    print("3rd query -> chat_completion calls =", calls["n"], "| 缓存命中(未增)?" , "是" if calls["n"] == 1 else "否")

asyncio.run(main())
