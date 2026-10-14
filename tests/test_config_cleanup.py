"""
配置系统清理（第一轮）回归测试。

目标：确认本轮最小清理不引入任何运行行为变化：
- 正常 Settings 初始化；
- 缺少必需模型配置时 validate_required 必须失败；
- testing 环境配置（TestingConfig）可正常加载；
- backend.config 包在默认（development）与 testing 环境下均可无错导入
  （验证此前 __init__.py 误写 `TestConfig` 的命名错误已修正为 `TestingConfig`）；
- 已删除的死字段不再存在（无残留引用）；
- 历史 fallback 常量保留（既有 test_config_required.py 依赖，删除需下一轮评估）。

全部离线：不联网、不读取本机 .env（_env_file=None）、不触碰 Chroma / Auth / 数据。
"""
import importlib

import pytest


def test_settings_normal_init():
    """正常 Settings 初始化：required 字段齐备时不抛错。"""
    from backend.config.settings import Settings

    s = Settings(
        _env_file=None,
        JWT_SECRET="test-secret",
        LLM_PROVIDER="dashscope",
        API_KEY="test-key",
        LLM_MODEL="kimi-k2-thinking",
        EMBEDDING_MODEL="text-embedding-v3",
    )
    assert s.LLM_PROVIDER == "dashscope"
    assert s.LLM_MODEL == "kimi-k2-thinking"
    assert s.EMBEDDING_MODEL == "text-embedding-v3"
    assert s.API_KEY == "test-key"


def test_missing_required_model_config_raises():
    """缺少必需模型配置时，validate_required 必须显式失败。"""
    from backend.config.settings import Settings

    with pytest.raises(
        RuntimeError, match="Missing required configuration: LLM_MODEL"
    ):
        Settings(
            _env_file=None,
            JWT_SECRET="test-secret",
            LLM_PROVIDER="dashscope",
            API_KEY="test-key",
            LLM_MODEL=None,  # 缺失
            EMBEDDING_MODEL="text-embedding-v3",
        ).validate_required()


def test_removed_dead_fields_absent():
    """已删除的死字段（EMBEDDING_PROVIDER/DIMENSIONS/DEVICE）不再存在，确认无残留引用。"""
    from backend.config.settings import Settings

    s = Settings(
        _env_file=None,
        JWT_SECRET="x",
        LLM_PROVIDER="o",
        API_KEY="k",
        LLM_MODEL="m",
        EMBEDDING_MODEL="e",
    )
    for field in ("EMBEDDING_PROVIDER", "EMBEDDING_DIMENSIONS", "EMBEDDING_DEVICE"):
        assert not hasattr(s, field), f"{field} 应已在清理第一轮删除"


def test_testing_config_loads():
    """testing 环境配置（TestingConfig）可正常实例化。"""
    from backend.config.testing import TestingConfig

    cfg = TestingConfig()
    assert cfg.ENVIRONMENT == "testing"
    assert cfg.JWT_SECRET


def test_config_package_import_default_env_no_error():
    """默认（development）环境下 import backend.config 不应报错。"""
    import backend.config  # noqa: F401

    assert backend.config.Config is not None


def test_config_package_import_testing_env_no_error(monkeypatch):
    """
    此前 __init__.py 误写 `from .testing import TestConfig`，
    而 testing 模块定义的是 TestingConfig，导致 ENVIRONMENT=testing 时导入即崩溃。
    修复后应能无错导入且 Config 指向 TestingConfig。
    """
    import backend.config as config_pkg

    monkeypatch.setenv("ENVIRONMENT", "testing")
    importlib.reload(config_pkg)
    try:
        assert config_pkg.Config.__name__ == "TestingConfig"
    finally:
        # 还原默认环境并 reload，避免影响后续测试模块导入状态
        monkeypatch.setenv("ENVIRONMENT", "development")
        importlib.reload(config_pkg)


def test_legacy_fallback_constants_retained():
    """历史 fallback 常量仍保留（既有测试依赖；删除需下一轮单独评估）。"""
    from backend.config.settings import (
        LEGACY_FALLBACK_EMBEDDING_MODEL,
        LEGACY_FALLBACK_LLM_MODEL,
    )

    assert LEGACY_FALLBACK_LLM_MODEL == "gpt-3.5-turbo"
    assert LEGACY_FALLBACK_EMBEDDING_MODEL == "text-embedding-3-small"
