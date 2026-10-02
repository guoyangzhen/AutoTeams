"""本地路径授权模型 —— 协作工作台与本地工具桥接。

用户在本机提供「本地文件夹路径 + 授权范围」，AutoTeams 云端仅保存授权记录，
真正的文件读写删除由用户本机的本地守护进程（Local Runner）在授权目录内执行。

安全要点：
- 只存 setup_token 的 SHA-256 哈希，绝不落明文 token。
- scope 区分 read / read_write，Runner 端强制校验。
- status 生命周期：pending → connected → offline / revoked。
"""
import uuid

from sqlalchemy import Column, String, Text, DateTime, ForeignKey, JSON, Boolean

from app.database import Base
from app.utils.time import utcnow


class LocalPathGrant(Base):
    """本地路径授权记录。"""

    __tablename__ = "local_path_grants"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    enterprise_id = Column(
        String(36), ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id = Column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 用户给该路径的友好名称（可为空）
    label = Column(String(128), nullable=True)
    # 用户声明的本地绝对路径
    local_path = Column(Text, nullable=False)
    # 授权范围：read / read_write
    scope = Column(String(16), nullable=False, server_default="read")
    # 状态：pending / connected / offline / revoked
    status = Column(String(16), nullable=False, server_default="pending")
    # Runner 上报的标识与已就绪工具清单
    runner_id = Column(String(128), nullable=True)
    tool_manifest = Column(JSON, nullable=True)
    # Runner 本地校验后解析出的真实路径（存在性/合法性通过后才回填）
    resolved_path = Column(Text, nullable=True)
    # 一次性 setup token 的 SHA-256 哈希（校验通过后即失效）
    setup_token_hash = Column(String(64), nullable=True)
    setup_token_expires_at = Column(DateTime(timezone=True), nullable=True)
    # 是否已由 Runner 认领（认领后 token 失效）
    claimed = Column(Boolean, nullable=False, server_default="false")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<LocalPathGrant id={self.id} scope={self.scope} status={self.status}>"
