from typing import ClassVar, Optional
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# 历史兼容 fallback（仅作常量保留，用于测试 / 非生产代码的默认占位）。
# 注意：它们不再是 pydantic 字段默认值——生产运行必须经过 validate_required() 校验，
# 缺失即启动失败，绝不静默落到这些历史默认值。
LEGACY_FALLBACK_LLM_MODEL = "gpt-3.5-turbo"
LEGACY_FALLBACK_EMBEDDING_MODEL = "text-embedding-3-small"


class Settings(BaseSettings):
    # 基础配置
    ENVIRONMENT: str = "development"
    # JWT 签名密钥：必填，无默认值，缺失时应用启动必须失败
    JWT_SECRET: str
    DEBUG: bool = False

    # JWT 访问令牌有效期（分钟）
    # 注：原代码引用 settings.ACCESS_TOKEN_EXPIRE_MINUTES，此处补齐以免启动即 AttributeError
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    # 数据库
    DATABASE_URL: str = "sqlite:///./my_ai_assistant.db"

    # Redis
    REDIS_URL: str | None = None

    # 向量数据库
    VECTOR_DB_PATH: str = "./data/chroma_db"
    VECTOR_DB_COLLECTION: str = "documents"

    # 大模型供应商（通用 API_KEY 驱动 OpenAI 兼容接口）
    # 以下四个字段为「生产必需」：缺失或为空字符串均视为未配置，
    # 由 validate_required() 在启动期显式报错，不得静默回退到历史默认模型。
    # 设为 Optional 且不给字符串默认值，是为了让「未配置」可被检测到，而不是悄悄变成
    # "openai" / "gpt-3.5-turbo" / "text-embedding-3-small"。
    LLM_PROVIDER: Optional[str] = None
    # 通用大模型调用密钥（OpenAI / DashScope / 智谱 使用；Ollama / local 可为空）
    API_KEY: Optional[str] = None
    # OpenAI 兼容接口地址（DeepSeek / 本地 Ollama 等填此处）；允许为空，
    # 为空时由对应 provider 的 DEFAULT_BASE_URLS 兜底（见 llm_service.OpenAILikeLLM）。
    LLM_BASE_URL: str | None = None
    LLM_MODEL: Optional[str] = None

    # 嵌入模型（EMBEDDING_MODEL 仅用于 Embedding，与 LLM_MODEL（Chat）解耦）；
    # 同样为生产必需，缺失/空字符串即启动失败。
    EMBEDDING_PROVIDER: str = "local"
    EMBEDDING_MODEL: Optional[str] = None
    EMBEDDING_DIMENSIONS: int = 512
    EMBEDDING_DEVICE: str = "cpu"

    # CORS
    BACKEND_CORS_ORIGINS: list[str] | str = []

    @field_validator("BACKEND_CORS_ORIGINS", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v):
        if isinstance(v, str):
            return [i.strip() for i in v.split(",")]
        return v

    # 日志
    LOG_LEVEL: str = "INFO"
    LOG_FILE: str | None = None

    # Table-aware Retrieval（Phase 1）：全量枚举查询时单表最大加载行组数。
    # 20 仅为初始阈值、非最终产品规范；超过则回退普通 semantic top-k（大表属 Phase 2）。
    TABLE_FULL_LOAD_MAX_ROW_GROUPS: int = 20

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 生产必需配置项（缺失/空字符串即视为未配置）。
    REQUIRED_KEYS: ClassVar[tuple] = ("LLM_PROVIDER", "API_KEY", "LLM_MODEL", "EMBEDDING_MODEL")

    def validate_required(self) -> None:
        """启动期配置校验：生产必需项不得缺失或为空。

        - 任意一项为 None 或空字符串（含纯空白）都视为未配置，显式抛 RuntimeError。
        - 绝不静默使用历史默认模型（LEGACY_FALLBACK_*）。
        - API_KEY 仅报告缺失，绝不输出 Key 原文 / 前缀 / 长度 / hash。
        - LLM_PROVIDER 还必须是受支持的值，否则同样显式报错。

        该方法只在 ProductionConfig / DevelopmentConfig / 顶层 Config 的
        validate_config() 中调用；TestingConfig 不调用，以隔离测试配置。
        """
        missing = []
        for key in self.REQUIRED_KEYS:
            val = getattr(self, key)
            if val is None or (isinstance(val, str) and val.strip() == ""):
                missing.append(key)

        # API_KEY 单独用安全措辞，避免任何泄露风险
        if "API_KEY" in missing:
            raise RuntimeError("API_KEY is missing")
        if missing:
            raise RuntimeError(
                "Missing required configuration: " + ", ".join(missing)
            )

        # provider 必须是受支持的值（空字符串已在上面拦截）
        supported = {"openai", "dashscope", "zhipu", "ollama", "local"}
        if self.LLM_PROVIDER not in supported:
            raise RuntimeError(
                f"Unsupported LLM_PROVIDER: {self.LLM_PROVIDER!r} "
                f"(expected one of {sorted(supported)})"
            )


settings = Settings()
