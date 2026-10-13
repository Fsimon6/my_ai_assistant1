"""
启动期模型必需配置 fail-fast 校验测试。

隔离策略（不污染正式运行配置、不依赖修改运行中单例字段）：
- 单元测试：通过 monkeypatch 切到空临时目录（避免读取项目根 .env）并显式注入 env，
  然后构造全新的 Settings() 实例（非模块单例）调用 validate_required()。
- 启动链路测试：通过子进程运行真实 lifespan（plain python，非 pytest），
  验证校验确实在应用启动期、接收请求之前执行；子进程使用临时工作目录与临时
  DATABASE_URL，副作用完全隔离在项目之外，也不继承开发shell的真实模型配置。
"""
import os
import sys
import subprocess
import tempfile
import textwrap
from pathlib import Path

import pytest


REQUIRED = ("LLM_PROVIDER", "API_KEY", "LLM_MODEL", "EMBEDDING_MODEL")


def _env(**overrides):
    """一份「完整有效」的模型必需配置；ollama 不要求真实 API_KEY，也不触发请求。"""
    base = {
        "LLM_PROVIDER": "ollama",
        "API_KEY": "dummy-key",
        "LLM_MODEL": "chat-model",
        "EMBEDDING_MODEL": "embed-model",
    }
    base.update(overrides)
    return base


def _fresh_settings(monkeypatch, tmp_path, env):
    """构造只受注入 env 影响、不读取真实 .env 的全新 Settings 实例。"""
    monkeypatch.chdir(tmp_path)
    for k in REQUIRED + ("LLM_BASE_URL",):
        monkeypatch.delenv(k, raising=False)
    # JWT_SECRET 是 pydantic 必填字段，构造 Settings 时需要存在；
    # 与本轮「模型必需配置」校验无关，固定测试值以保证构造确定性。
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from backend.config.settings import Settings
    return Settings()


# --------------------------- 单元测试：validate_required 逻辑 ---------------------------

def test_validate_required_passes_when_all_present(monkeypatch, tmp_path):
    s = _fresh_settings(monkeypatch, tmp_path, _env())
    s.validate_required()  # 不应抛异常


def test_missing_llm_model_fails(monkeypatch, tmp_path):
    env = _env()
    env.pop("LLM_MODEL")
    s = _fresh_settings(monkeypatch, tmp_path, env)
    with pytest.raises(RuntimeError, match="LLM_MODEL"):
        s.validate_required()


def test_missing_embedding_model_fails(monkeypatch, tmp_path):
    env = _env()
    env.pop("EMBEDDING_MODEL")
    s = _fresh_settings(monkeypatch, tmp_path, env)
    with pytest.raises(RuntimeError, match="EMBEDDING_MODEL"):
        s.validate_required()


def test_missing_llm_provider_fails(monkeypatch, tmp_path):
    env = _env()
    env.pop("LLM_PROVIDER")
    s = _fresh_settings(monkeypatch, tmp_path, env)
    with pytest.raises(RuntimeError, match="LLM_PROVIDER"):
        s.validate_required()


def test_missing_api_key_fails_without_leak(monkeypatch, tmp_path):
    env = _env()
    env.pop("API_KEY")
    s = _fresh_settings(monkeypatch, tmp_path, env)
    with pytest.raises(RuntimeError) as exc:
        s.validate_required()
    assert "API_KEY" in str(exc.value)
    # 即便环境变量里曾有 key，缺失报错也不应回显其值
    assert "dummy-key" not in str(exc.value)


def test_empty_string_treated_as_missing(monkeypatch, tmp_path):
    env = _env(LLM_MODEL="", EMBEDDING_MODEL="  ")
    s = _fresh_settings(monkeypatch, tmp_path, env)
    with pytest.raises(RuntimeError) as exc:
        s.validate_required()
    msg = str(exc.value)
    assert "LLM_MODEL" in msg
    assert "EMBEDDING_MODEL" in msg


def test_unsupported_provider_fails(monkeypatch, tmp_path):
    env = _env(LLM_PROVIDER="banana")
    s = _fresh_settings(monkeypatch, tmp_path, env)
    with pytest.raises(RuntimeError, match="Unsupported LLM_PROVIDER"):
        s.validate_required()


def test_no_silent_default_masks_missing(monkeypatch, tmp_path):
    """字段缺失时不应被任何非空默认值掩盖：Settings 解析结果应为 None/空，并被校验捕获。"""
    # 完全不注入任何模型配置
    s = _fresh_settings(monkeypatch, tmp_path, {})
    # 证实运行时 settings 没有静默落到历史默认模型（缺失即为 None，而非某默认模型名）
    assert s.LLM_MODEL is None
    assert s.EMBEDDING_MODEL is None
    assert s.LLM_PROVIDER is None
    # API_KEY 缺失时给出专用报错（且不回显 key 值）
    with pytest.raises(RuntimeError, match="API_KEY is missing"):
        s.validate_required()

    # 若仅 API_KEY 提供，其余三项缺失应被一并列出（不被默认值掩盖）
    s2 = _fresh_settings(monkeypatch, tmp_path, {"API_KEY": "dummy-key"})
    with pytest.raises(RuntimeError) as exc:
        s2.validate_required()
    msg = str(exc.value)
    for key in ("LLM_PROVIDER", "LLM_MODEL", "EMBEDDING_MODEL"):
        assert key in msg


def test_error_does_not_leak_api_key_value(monkeypatch, tmp_path):
    """模型配置错误包含配置项名称，但绝不泄露 API Key 原文。"""
    sentinel = "LEAK_SENTINEL_KEY_ABC"
    env = _env(API_KEY=sentinel)
    env.pop("LLM_MODEL")  # 以另一项缺失触发报错
    s = _fresh_settings(monkeypatch, tmp_path, env)
    with pytest.raises(RuntimeError) as exc:
        s.validate_required()
    msg = str(exc.value)
    assert "LLM_MODEL" in msg
    assert sentinel not in msg


# --------------------------- 启动链路测试：真实 lifespan（子进程） ---------------------------

def _run_lifespan(env_extra):
    """在干净子进程中运行 backend.main 的 lifespan，返回 CompletedProcess。

    子进程继承 os.environ 但先清除 4 个必需键，再仅按 env_extra 注入，
    确保不继承开发 shell 的真实模型配置；使用临时工作目录与临时 DATABASE_URL。
    """
    project_root = Path(__file__).parent.parent
    tmp = tempfile.mkdtemp()
    env = dict(os.environ)
    for k in REQUIRED:
        env.pop(k, None)
    if "JWT_SECRET" not in env:
        env["JWT_SECRET"] = "test-jwt-secret"
    env["PYTHONPATH"] = str(project_root)
    env["DATABASE_URL"] = f"sqlite:///{os.path.join(tmp, 'test_lifespan.db')}"
    env.update(env_extra)

    code = textwrap.dedent(
        """
        import asyncio
        from backend.main import app, lifespan
        async def _m():
            async with lifespan(app):
                print("LIFESPAN_OK")
        asyncio.run(_m())
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        cwd=tmp,
        capture_output=True,
        text=True,
        timeout=180,
    )
    try:
        os.remove(os.path.join(tmp, "test_lifespan.db"))
    except OSError:
        pass
    return proc


def test_lifespan_passes_with_valid_config():
    proc = _run_lifespan(_env())
    assert proc.returncode == 0, proc.stderr
    assert "LIFESPAN_OK" in proc.stdout


def test_lifespan_fails_before_serving_when_llm_model_missing():
    env = _env()
    env.pop("LLM_MODEL")
    proc = _run_lifespan(env)
    assert proc.returncode != 0
    # 必须在接收请求前失败：lifespan 不应走到 yield
    assert "LIFESPAN_OK" not in proc.stdout
    assert "LLM_MODEL" in proc.stderr


def test_lifespan_error_does_not_leak_api_key():
    env = _env(API_KEY="LEAK_SENTINEL_KEY_ABC")
    env.pop("LLM_MODEL")
    proc = _run_lifespan(env)
    assert proc.returncode != 0
    assert "LEAK_SENTINEL_KEY_ABC" not in proc.stderr
    assert "LEAK_SENTINEL_KEY_ABC" not in proc.stdout
