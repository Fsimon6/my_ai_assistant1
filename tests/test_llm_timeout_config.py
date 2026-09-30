# -*- coding: utf-8 -*-
"""验证 LLM/Embedding 客户端的 read timeout 真实来源 = settings.LLM_TIMEOUT。

- settings.LLM_TIMEOUT 默认值应为 120。
- OpenAILikeLLM 构造时传给 AsyncOpenAI 的 httpx.Timeout.read 应等于 settings.LLM_TIMEOUT。
- 不调用远程模型、不等待真实 120 秒；仅 mock AsyncOpenAI 构造函数捕获 timeout 参数。
"""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.config.settings import settings  # noqa: E402
from backend.services.llm_service import OpenAILikeLLM, LLMConfig  # noqa: E402


def test_settings_llm_timeout_default():
    # 默认情况下（.env 未覆盖）应为 120，且与历史运行行为一致
    assert settings.LLM_TIMEOUT == 120


def test_openai_client_read_timeout_from_settings():
    captured = {}

    def fake_async_openai(api_key=None, base_url=None, timeout=None, max_retries=None):
        captured['timeout'] = timeout
        return MagicMock()

    with patch('openai.AsyncOpenAI', side_effect=fake_async_openai):
        OpenAILikeLLM(LLMConfig(
            provider='dashscope',
            api_key='test-key',
            model='test-model',
            embedding_model='test-embed',
        ))

    assert captured.get('timeout') is not None
    # 实际生效的 read timeout 必须来自 settings.LLM_TIMEOUT
    assert captured['timeout'].read == settings.LLM_TIMEOUT
    # 其余 timeout 结构保持不变（connect=10 / write=30 / pool=10）
    assert captured['timeout'].connect == 10.0
    assert captured['timeout'].write == 30.0
    assert captured['timeout'].pool == 10.0
