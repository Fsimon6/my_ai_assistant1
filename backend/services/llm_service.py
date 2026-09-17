# -*- coding: utf-8 -*-
import os
from typing import Dict, List, Optional, AsyncGenerator
from backend.services.types import ChatMessage, SearchResult
from abc import ABC, abstractmethod
from dataclasses import dataclass
from ..utils.retry import retry, llm_retry_config
from backend.config.settings import settings
import logging


logger = logging.getLogger(__name__)

# 远程 embedding 单次请求最大文本数。
# DashScope / OpenAI-compatible embedding 接口要求单次请求 input 文本数 <= 20
# （部分 provider 更宽松，但本项目受 provider 约束固定为 20）。
# 可用环境变量 EMBEDDING_BATCH_SIZE 覆盖，但不得违反所用 provider 的限制。
EMBEDDING_MAX_BATCH = int(os.getenv('EMBEDDING_BATCH_SIZE', '20'))

@dataclass
class LLMConfig:
    """LLM配置类"""
    provider: str  # openai / dashscope / zhipu / ollama / local
    api_key: str
    api_secret: Optional[str] = None
    base_url: Optional[str] = None
    model: str = 'ERNIE-3.5-8k'
    embedding_model: Optional[str] = None
    temperature: float = 0.7
    max_tokens: int = 2000
    timeout: int = 30


class BaseLLM(ABC):
    """大模型基类"""

    def __init__(self, config: LLMConfig):
        self.config = config

    @abstractmethod
    async def chat_completion(
            self,
            messages: List[ChatMessage],
            stream: bool = True
    ) -> AsyncGenerator[str, None]:
        """补全聊天接口"""
        pass

    @abstractmethod
    async def generate_embeddings(
            self,
            text: List[str],
            model: str = 'text-embedding-v1'
    ) -> List[List[float]]:
        """生成文本向量"""
        pass


class OpenAILikeLLM(BaseLLM):
    """OpenAI 兼容接口（OpenAI / 通义千问 DashScope / 智谱 / 本地 Ollama 等）

    统一使用通用 API_KEY 作为配置入口；各 provider 的默认 base_url 在此处区分，
    允许通过 LLM_BASE_URL 覆盖。Ollama / local 可不传 API_KEY。
    """

    # 各 provider 的默认 OpenAI 兼容 base_url（可被 LLM_BASE_URL 覆盖）
    DEFAULT_BASE_URLS = {
        'openai': 'https://api.openai.com/v1',
        'dashscope': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        'zhipu': 'https://open.bigmodel.cn/api/paas/v4',
        'ollama': 'http://localhost:11434/v1',
        'local': 'http://localhost:11434/v1',
    }

    def __init__(self, config: LLMConfig):
        super().__init__(config)
        try:
            from openai import AsyncOpenAI
            import httpx
            base_url = self.config.base_url or self.DEFAULT_BASE_URLS.get(
                self.config.provider, 'https://api.openai.com/v1'
            )
            # Ollama / local 不需要 API Key
            api_key = self.config.api_key or 'not-needed'

            # 显式超时 + 重试，杜绝“SDK 默认 600s 超时 + 2 次重试”在模型服务
            # 发生 403/429/网络异常时使单次用户请求挂起数百秒：
            # - connect=10s：连接阶段快速失败（避免不可达 host 长时间空等）
            # - read=整体读取/流式分块间隔上限（默认 120s；流式靠“分块间隔”而非“总时长”
            #   判定卡死，不会截断正常长文本生成）
            # - write/pool：固定为较小值
            # - max_retries 默认 1：仅吞掉瞬时抖动；不掩盖 401/403（不可重试），
            #   也不在长超时上反复重试放大等待。
            # 默认已足够安全，可通过环境变量覆盖，但无需改动 .env。
            _read_timeout = float(os.getenv('LLM_TIMEOUT', '120'))
            _max_retries = int(os.getenv('LLM_MAX_RETRIES', '1'))
            _timeout = httpx.Timeout(
                connect=10.0,
                read=_read_timeout,
                write=30.0,
                pool=10.0,
            )
            self.client = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=_timeout,
                max_retries=_max_retries,
            )
        except ImportError:
            raise ImportError('请安装openai：pip install openai')

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        stream: bool = False
    ) -> AsyncGenerator[str, None]:
        """OpenAI兼容聊天补全"""
        try:
            if stream:
                response = await self.client.chat.completions.create(
                    model=self.config.model,
                    messages=messages,
                    temperature=self.config.temperature,
                    max_tokens=self.config.max_tokens,
                    stream=True
                )

                async for chunk in response:
                    # 健壮解析：不假设每个 SSE chunk 都有 choices[0]。
                    # DashScope / 部分 OpenAI-compatible provider 会在流末尾发送
                    # 仅含 usage 的 chunk（choices=[]），此时没有可产出的文本，
                    # 直接跳过即可——既不抛 IndexError，也不丢失任何正常文本增量。
                    choices = getattr(chunk, 'choices', None)
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = getattr(choice, 'delta', None)
                    if delta is None:
                        continue
                    content = getattr(delta, 'content', None)
                    if content:
                        yield content
            else:
                response = await self.client.chat.completions.create(
                    model=self.config.model,
                    messages=messages,
                    temperature=self.config.temperature,
                    max_tokens=self.config.max_tokens,
                )
                yield response.choices[0].message.content

        except Exception as e:
            logger.error(f'OpenAI调用失败：{e}')
            raise

    async def generate_embeddings(
        self,
        texts: List[str],
        model: Optional[str] = None
    ) -> List[List[float]]:
        """生成 OpenAI 兼容 embedding（必须走 embeddings 端点，绝不能误用 chat completions）

        模型优先级：显式传入 > config.embedding_model(settings.EMBEDDING_MODEL) > 兜底默认。

        单批大小限制：DashScope / OpenAI-compatible embedding 接口要求单次请求
        input 文本数 <= 20（部分 provider 更严格）。本方法在“唯一合理的 batch 边界”
        （即真正的远程调用处）做顺序拆批，保证：
          - 每次远程请求 texts 数量 <= EMBEDDING_MAX_BATCH
          - 输入 N 条文本，返回 N 条向量（按 input 顺序合并）
          - 任一批失败立即向上抛出，绝不静默吞错导致 Chroma 写入不完整
        """
        model = model or self.config.embedding_model or 'text-embedding-3-small'
        if not texts:
            return []

        total = len(texts)
        batch_size = EMBEDDING_MAX_BATCH
        if total <= batch_size:
            logger.info(f'Embedding 单批：size={total}')
            return await self._embed_batch(texts, model, 1, 1)

        batches = [texts[i:i + batch_size] for i in range(0, total, batch_size)]
        logger.info(
            f'Embedding 拆批：共 {total} 条文本，分成 {len(batches)} 批'
            f'（每批≤{batch_size}）'
        )
        results: List[List[float]] = []
        for idx, batch in enumerate(batches, 1):
            results.extend(await self._embed_batch(batch, model, idx, len(batches)))
        return results

    async def _embed_batch(
        self,
        batch: List[str],
        model: str,
        idx: int,
        total_batches: int,
    ) -> List[List[float]]:
        """执行单批远程 embedding；失败记录批号/批大小/原因并向上抛出。"""
        try:
            response = await self.client.embeddings.create(model=model, input=batch)
        except Exception as e:
            logger.error(
                f'Embedding batch {idx}/{total_batches} 失败（size={len(batch)}）：{e}'
            )
            raise
        # 按 API 返回的 index 排序，确保与输入顺序严格一致（即使服务乱序返回）
        ordered = sorted(response.data, key=lambda d: (d.index if d.index is not None else 0))
        vectors = [d.embedding for d in ordered]
        if len(vectors) != len(batch):
            raise RuntimeError(
                f'Embedding batch {idx}/{total_batches} 返回数量异常：'
                f'期望 {len(batch)} 条，实际 {len(vectors)} 条'
            )
        logger.info(f'Embedding batch {idx}/{total_batches}, size={len(batch)} 完成')
        return vectors

class LLMFactory:
    """LLM工厂类（配置统一来自 settings）"""

    # provider 是否需要 API_KEY
    PROVIDERS_REQUIRE_KEY = {'openai', 'dashscope', 'zhipu'}
    SUPPORTED_PROVIDERS = {'openai', 'dashscope', 'zhipu', 'ollama', 'local'}

    @staticmethod
    def create_llm(config: LLMConfig) -> BaseLLM:
        """创建LLM实例"""
        if config.provider in LLMFactory.SUPPORTED_PROVIDERS:
            return OpenAILikeLLM(config)
        raise ValueError(f'不支持的provider：{config.provider}')

    @staticmethod
    def from_env() -> BaseLLM:
        """从 settings 创建 LLM（统一配置入口）"""
        provider = settings.LLM_PROVIDER
        if provider not in LLMFactory.SUPPORTED_PROVIDERS:
            raise ValueError(f'不支持的provider：{provider}')

        # 按 provider 判断是否必须提供 API_KEY
        api_key = settings.API_KEY
        if provider in LLMFactory.PROVIDERS_REQUIRE_KEY and not api_key:
            raise ValueError(f'provider={provider} 需要设置 API_KEY 环境变量')

        config = LLMConfig(
            provider=provider,
            api_key=api_key,
            base_url=settings.LLM_BASE_URL,
            model=settings.LLM_MODEL,
            embedding_model=settings.EMBEDDING_MODEL,
            temperature=float(os.getenv('LLM_TEMPERATURE', '0.7')),
            max_tokens=int(os.getenv('LLM_MAX_TOKENS', '2000')),
        )

        return LLMFactory.create_llm(config)


# 单例实例
_llm_instance = None

def get_llm() -> BaseLLM:
    """获取LLM单例"""
    global _llm_instance
    if _llm_instance is None:
        _llm_instance = LLMFactory.from_env()
    return _llm_instance