"""
配置收敛测试：确认 .env 是当前生产模型配置的唯一权威来源，
缺失/空字符串时显式报错，绝不静默 fallback 到历史默认模型。

全部为离线测试（不联网、不消耗 embedding quota）。
"""
import pytest
from unittest.mock import MagicMock, patch

from backend.config.settings import Settings, LEGACY_FALLBACK_LLM_MODEL, LEGACY_FALLBACK_EMBEDDING_MODEL
from backend.services.llm_service import LLMFactory
from backend.services.vector_service import AIAssistantEmbeddings


# ---------------------------------------------------------------------------
# 辅助：构造一份“完整有效”的 Settings（init 参数优先级高于环境变量，_env_file=None
# 避免读取本机 .env，从而与运行环境隔离）。
# ---------------------------------------------------------------------------
def _valid_settings(**overrides):
    params = dict(
        JWT_SECRET="test-jwt-secret",
        LLM_PROVIDER="dashscope",
        API_KEY="test-key",
        LLM_MODEL="kimi-k2-thinking",
        EMBEDDING_MODEL="text-embedding-v3",
        LLM_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    params.update(overrides)
    return Settings(_env_file=None, **params)


# ---------------------------------------------------------------------------
# Test 1：当前真实 .env（运行机上存在完整 .env）
# ---------------------------------------------------------------------------
def test_real_env_values():
    """确认运行机 .env 实际加载为当前生产配置。"""
    from backend.config.settings import settings

    assert settings.LLM_PROVIDER == "dashscope"
    assert settings.LLM_MODEL == "kimi-k2-thinking"
    assert settings.EMBEDDING_MODEL == "text-embedding-v3"
    # LLM_BASE_URL 当前值非空
    assert settings.LLM_BASE_URL
    # API_KEY 仅检查非空，绝不输出原文 / 前缀 / 长度 / hash
    assert settings.API_KEY


# ---------------------------------------------------------------------------
# Test 2-5：缺单个必需项 → validate_required 必须失败
# ---------------------------------------------------------------------------
def test_missing_llm_provider():
    with pytest.raises(RuntimeError, match="Missing required configuration: LLM_PROVIDER"):
        _valid_settings(LLM_PROVIDER=None).validate_required()


def test_missing_llm_model():
    with pytest.raises(RuntimeError, match="Missing required configuration: LLM_MODEL"):
        _valid_settings(LLM_MODEL=None).validate_required()


def test_missing_embedding_model():
    with pytest.raises(RuntimeError, match="Missing required configuration: EMBEDDING_MODEL"):
        _valid_settings(EMBEDDING_MODEL=None).validate_required()


def test_missing_api_key():
    # API_KEY 缺失只允许报告 "API_KEY is missing"，绝不泄露 Key 内容
    with pytest.raises(RuntimeError, match="^API_KEY is missing$"):
        _valid_settings(API_KEY=None).validate_required()


# ---------------------------------------------------------------------------
# Test 6：空字符串（LLM_PROVIDER= / LLM_MODEL= / EMBEDDING_MODEL= / API_KEY=）
# 必须都被当成无效配置。
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("field,expected", [
    ("LLM_PROVIDER", "Missing required configuration: LLM_PROVIDER"),
    ("LLM_MODEL", "Missing required configuration: LLM_MODEL"),
    ("EMBEDDING_MODEL", "Missing required configuration: EMBEDDING_MODEL"),
    ("API_KEY", "API_KEY is missing"),
])
def test_empty_string_treated_as_missing(field, expected):
    with pytest.raises(RuntimeError, match=expected):
        _valid_settings(**{field: ""}).validate_required()


# ---------------------------------------------------------------------------
# Test 7：LLM_BASE_URL 缺失 → 不失败（允许 DEFAULT_BASE_URLS 兜底）
# ---------------------------------------------------------------------------
def test_base_url_optional():
    # 不应抛出
    _valid_settings(LLM_BASE_URL=None).validate_required()
    _valid_settings(LLM_BASE_URL="").validate_required()


# ---------------------------------------------------------------------------
# Test 8：LLMFactory.from_env() 在当前 .env 下读取正确配置（不联网，仅构造客户端）
# ---------------------------------------------------------------------------
def test_from_env_uses_real_env():
    llm = LLMFactory.from_env()
    cfg = llm.config
    assert cfg.provider == "dashscope"
    assert cfg.model == "kimi-k2-thinking"
    assert cfg.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    # embedding_model 必须等于全局 EMBEDDING_MODEL
    from backend.config.settings import settings
    assert cfg.embedding_model == settings.EMBEDDING_MODEL


def test_from_env_rejects_missing_model():
    """from_env 对缺失模型配置也要显式报错，不能进入实际 LLM/Embedding 调用。"""
    from backend.config.settings import settings as real_settings
    # 通过 monkeypatch 把全局 settings 的模型字段临时置空
    import backend.services.llm_service as llm_mod
    saved = (real_settings.LLM_MODEL, real_settings.EMBEDDING_MODEL, real_settings.API_KEY)
    try:
        real_settings.LLM_MODEL = None
        with pytest.raises(ValueError, match="LLM_MODEL is missing"):
            LLMFactory.from_env()
        real_settings.LLM_MODEL = "kimi-k2-thinking"
        real_settings.EMBEDDING_MODEL = None
        with pytest.raises(ValueError, match="EMBEDDING_MODEL is missing"):
            LLMFactory.from_env()
        real_settings.EMBEDDING_MODEL = "text-embedding-v3"
        real_settings.API_KEY = None
        with pytest.raises(ValueError, match="^API_KEY is missing$"):
            LLMFactory.from_env()
    finally:
        real_settings.LLM_MODEL, real_settings.EMBEDDING_MODEL, real_settings.API_KEY = saved


# ---------------------------------------------------------------------------
# Test 9：AIAssistantEmbeddings 使用 settings.EMBEDDING_MODEL（mock client，不联网）
# ---------------------------------------------------------------------------
def test_aiassistant_embeddings_uses_settings_model():
    from backend.config.settings import settings
    with patch("backend.services.vector_service.get_llm") as mock_get_llm:
        mock_get_llm.return_value = MagicMock()
        emb = AIAssistantEmbeddings()
        assert emb.embedding_model == settings.EMBEDDING_MODEL
        assert settings.EMBEDDING_MODEL == "text-embedding-v3"


# ---------------------------------------------------------------------------
# 历史 fallback 常量保留（仅作常量，不再作为 pydantic 字段默认值）
# ---------------------------------------------------------------------------
def test_legacy_fallbacks_retained_as_constants():
    assert LEGACY_FALLBACK_LLM_MODEL == "gpt-3.5-turbo"
    assert LEGACY_FALLBACK_EMBEDDING_MODEL == "text-embedding-3-small"
