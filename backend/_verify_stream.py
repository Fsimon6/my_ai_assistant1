# -*- coding: utf-8 -*-
import asyncio, time, sys, os
# 让 backend 包可导入（项目根在 backend 的父目录）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend.services.llm_service import get_llm


async def main():
    llm = get_llm()
    cfg = llm.config
    print("PROVIDER=%s MODEL=%s BASE_URL=%s" % (cfg.provider, cfg.model, cfg.base_url))
    messages = [
        {"role": "system", "content": "你是一个简洁的助手。"},
        {"role": "user", "content": "用三段话介绍人工智能，每段独立成句。"},
    ]
    t0 = time.time()
    n = 0
    total = 0
    try:
        async for chunk in llm.chat_completion(messages=messages, stream=True):
            n += 1
            total += len(chunk)
            print("[%.3fs] chunk#%d len=%d :: %r" % (time.time() - t0, n, len(chunk), chunk[:40]))
        print("TOTAL chunks=%d chars=%d elapsed=%.3fs" % (n, total, time.time() - t0))
    except Exception as e:
        print("ERROR:", repr(e))


asyncio.run(main())
