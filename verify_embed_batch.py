# -*- coding: utf-8 -*-
"""Phase1 验证 A：embedding 拆批逻辑（mock，不消耗真实 API / 不写 Chroma）。

验证目标：
- 输入 21 / 50 / 100 / 448 条文本，全部能正确拆批
- 每次远程请求 <= 20 条
- 返回向量数 == 输入文本数
- 向量顺序与输入顺序一致（按 API index 合并）
"""
import os
import sys

sys.path.insert(0, r'C:\Users\Administrator\Desktop\my_ai_assistant')
os.environ.setdefault('PYTHONPATH', r'C:\Users\Administrator\Desktop\my_ai_assistant')

from backend.services.llm_service import get_llm


class _FakeEmbedding:
    def __init__(self, index, embedding):
        self.index = index
        self.embedding = embedding


class _FakeResp:
    def __init__(self, data):
        self.data = data


async def main():
    llm = get_llm()
    calls = []

    async def fake_create(model, input):
        calls.append(len(input))
        # embedding[0] 编码全局下标（从文本 "text-{i}" 解析），d.index 为批内位置。
        # 生产代码按 d.index 排序后跨批拼接，最终 vecs[k][0] 应等于全局下标 k。
        data = []
        for wi, t in enumerate(input):
            gi = int(t.split('-')[1])
            data.append(_FakeEmbedding(wi, [float(gi), 0.0, 0.0]))
        return _FakeResp(data)

    # 替换远程调用（仅影响本进程单例）
    llm.client.embeddings.create = fake_create

    all_ok = True
    for n in [21, 50, 100, 448]:
        calls.clear()
        texts = [f"text-{i}" for i in range(n)]
        vecs = await llm.generate_embeddings(texts)
        ok_count = len(vecs) == n
        ok_order = all(vecs[i][0] == float(i) for i in range(n))
        ok_batch = all(c <= 20 for c in calls)
        status = "OK" if (ok_count and ok_order and ok_batch) else "FAIL"
        if status == "FAIL":
            all_ok = False
        print(
            f"[{status}] n={n:>3} 返回向量数={len(vecs)} "
            f"批次数={len(calls)} 每批大小={calls} "
            f"count_ok={ok_count} order_ok={ok_order} batch<=20={ok_batch}"
        )
    print("RESULT:", "PASS" if all_ok else "FAIL")
    return 0 if all_ok else 1


if __name__ == '__main__':
    import asyncio
    sys.exit(asyncio.run(main()))
