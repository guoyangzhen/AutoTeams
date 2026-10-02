import uuid
from sqlalchemy import Column, String, DateTime, Boolean, ForeignKey
from app.database import Base
from app.utils.time import utcnow


class LLMApiConfig(Base):
    """企业级模型 API 配置（每企业一条）。

    安全设计（对齐 LiteLLM / Open WebUI）：
    - API Key 列仅存储 Fernet 加密后的密文，绝不落明文。
    - 任何读接口只返回掩码，明文密钥仅在服务端内存中解密后立即用于 LLM 调用。
    - 字段 openai_enabled / anthropic_enabled 决定当前生效的 provider，
      两者同时启用时前端应提示，避免歧义。
    """

    __tablename__ = "llm_api_configs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    enterprise_id = Column(
        String(36),
        ForeignKey("enterprises.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # ---- OpenAI 兼容 API ----
    openai_enabled = Column(Boolean, default=False, nullable=False)
    openai_api_base = Column(String, nullable=True)
    openai_api_key = Column(String, nullable=True)  # Fernet 密文
    openai_model = Column(String, nullable=True)

    # ---- Anthropic API ----
    anthropic_enabled = Column(Boolean, default=False, nullable=False)
    anthropic_api_base = Column(String, nullable=True)
    anthropic_api_key = Column(String, nullable=True)  # Fernet 密文
    anthropic_model = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return (
            f"<LLMApiConfig enterprise_id={self.enterprise_id} "
            f"openai_enabled={self.openai_enabled} "
            f"anthropic_enabled={self.anthropic_enabled}>"
        )
