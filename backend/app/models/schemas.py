"""ORM 模型 JSON 列的 Pydantic Schema 约束。

依据重构计划 §4.2.3：
    ``JSON`` 列规范化：``config``、``memory_config`` 等 JSON 列定义 Pydantic schema 约束

为什么放在 ``app/models/`` 而不是 ``app/schemas/``：
本仓 ``app/schemas/`` 是**对外 API 契约**（请求体 / 响应体）的归属地；本模块约束的是
**ORM 内部 JSON 列**的形状，是数据完整性问题而非接口问题，二者生命周期不同
（API 契约随版本演进，列约束随数据迁移演进），因此分开放置。
⚠️ 若把两者混在一起，后续有人为「改接口字段」而改动本模块就会波及数据库读取。

⚠️ 关于 ``BackgroundJob``：§4.2.3 提出「合并 ``CompilationJob`` 与 ``ProcessingTask``
为统一的 ``BackgroundJob`` 模型」，但**该模型目前并不存在**（grep 全仓无此符号），
它是一次带迁移的模型合并，不属于本次「为现有 JSON 列建 schema」的范围。
本模块因此覆盖**现有**两个 job 类模型的 JSON 列（``ProcessingTask.error_log``、
``CompilationArtifact.output``），使后续真做合并时 schema 侧已就绪——
不预先虚构一个不存在的模型。

向后兼容原则：全部 schema 使用 ``extra="allow"``。数据库里存在历史脏数据与
已下线字段，若用 ``extra="forbid"``，任何一次读取都会抛 ValidationError，
把「规范化 schema」变成「线上事故源」。约束只用于**校验新写入**与**规范化取值**，
历史数据读取路径不受影响。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.compiler import MemoryConfig  # 复用既有定义，不重复造

logger = logging.getLogger(__name__)

__all__ = [
    "AgentKnowledgeConfig",
    "AgentConfig",
    "ProcessingTaskErrorEntry",
    "ProcessingTaskErrorLog",
    "CompilationArtifactOutput",
    "resolve_top_k",
    "TOP_K_MIN",
    "TOP_K_MAX",
    "DEFAULT_TOP_K",
]

# 与 app/api/agents/chat.py::_resolve_top_k 保持一致的取值域
TOP_K_MIN = 1
TOP_K_MAX = 20
DEFAULT_TOP_K = 5


class AgentKnowledgeConfig(BaseModel):
    """``Agent.config["knowledge"]`` 子结构。

    真实来源：知识库页「分块与索引配置」写入 ``config.knowledge.topK``，
    检索链路 ``_resolve_top_k`` 读取并钳制到 1–20。
    """

    model_config = ConfigDict(extra="allow")

    # ⚠️ 刻意**不加** ge/le 硬约束：库里已存在 topK=999 之类的历史值。
    # 在此处直接拒绝会让读取路径抛 ValidationError——把「规范化 schema」
    # 变成线上事故源。既有契约是「越界则钳制到 1–20」（见 _resolve_top_k），
    # 故本 schema 接受原值、由 resolved_top_k() 钳制。合法域：1–20。
    topK: Optional[int] = None

    @field_validator("topK", mode="before")
    @classmethod
    def _coerce_top_k(cls, v: Any) -> Any:
        """与 AgentConfig.top_k 同理：非数值归一为 None，不让脏值炸掉读取路径。"""
        if v is None or isinstance(v, bool):
            return None
        if isinstance(v, int):
            return v
        if isinstance(v, str):
            try:
                return int(v.strip())
            except ValueError:
                return None
        return None


class AgentConfig(BaseModel):
    """``Agent.config`` 列的规范结构。

    实际存在的键（来自代码而非注释臆测）：

    - ``knowledge.topK``  知识库检索 Top-K，现行写法
    - ``top_k``          早期的扁平别名，``_resolve_top_k`` 仍兼容读取
    - ``memory_config``  三层记忆配置，复用 ``app.schemas.compiler.MemoryConfig``

    ``model`` 列注释提到的 ``temperature`` / ``max_tokens`` 属 LLM 采样参数，
    目前**没有任何代码读取**它们（grep 确认），故这里刻意不声明——
    声明了就是给不存在的行为背书。若将来真正接线再加字段。
    """

    model_config = ConfigDict(extra="allow")

    knowledge: AgentKnowledgeConfig = Field(default_factory=AgentKnowledgeConfig)
    # 同上：越界值接受但钳制，不在 schema 层硬拒
    top_k: Optional[int] = None
    memory_config: Optional[MemoryConfig] = None

    @field_validator("knowledge", mode="before")
    @classmethod
    def _coerce_knowledge(cls, v: Any) -> Any:
        """``knowledge`` 为 null 时退化为空结构，避免整列因一个 None 键校验失败。"""
        return {} if v is None else v

    @field_validator("top_k", mode="before")
    @classmethod
    def _coerce_top_k(cls, v: Any) -> Any:
        """非数值（字符串 / dict / list）的 top_k 归一为 None。

        库中可能存在 ``top_k: "abc"`` 这类脏值，既有契约 ``_resolve_top_k``
        对它是「回落默认值」而非抛错。若此处直接让 Pydantic 拒绝，
        读一行 Agent 就会炸——与「schema 服务于数据完整性、不制造事故」的
        初衷相反。
        """
        if v is None or isinstance(v, bool):
            return None
        if isinstance(v, int):
            return v
        if isinstance(v, str):
            try:
                return int(v.strip())
            except ValueError:
                return None
        return None

    def resolved_top_k(self) -> int:
        """解析检索 Top-K，语义与 ``_resolve_top_k`` 严格一致。

        优先级：``knowledge.topK`` > 扁平 ``top_k`` > ``DEFAULT_TOP_K``；
        越界或非整数一律回落到默认，不抛错——检索不该因为一条脏配置整体失败。
        """
        raw = self.knowledge.topK
        if raw is None:
            raw = self.top_k
        if raw is None:
            return DEFAULT_TOP_K
        return max(TOP_K_MIN, min(TOP_K_MAX, int(raw)))


def resolve_top_k(config: Optional[Dict[str, Any]]) -> int:
    """从裸 dict（ORM 列原值）解析 Top-K，供服务层替换 ``_resolve_top_k``。

    解析失败（列里是 list / str 等脏数据）时回落到默认值，绝不抛。
    """
    if not isinstance(config, dict):
        return DEFAULT_TOP_K
    try:
        return AgentConfig.model_validate(config).resolved_top_k()
    except Exception:  # noqa: BLE001 —— 脏数据不应阻断检索
        return DEFAULT_TOP_K


class ProcessingTaskErrorEntry(BaseModel):
    """``ProcessingTask.error_log`` 的单条记录。

    形状取自模型注释：``[{file, error, timestamp}]``。
    """

    model_config = ConfigDict(extra="allow")

    file: Optional[str] = None
    error: Optional[str] = None
    timestamp: Optional[datetime] = None


class ProcessingTaskErrorLog(BaseModel):
    """``ProcessingTask.error_log`` 列整体（list 形态）。"""

    model_config = ConfigDict(extra="allow")

    entries: List[ProcessingTaskErrorEntry] = Field(default_factory=list)

    @classmethod
    def coerce(cls, raw: Any) -> "ProcessingTaskErrorLog":
        """把列原值规范化为该结构；非 list / 脏数据一律降级为空列表。

        不用 ``List[...]`` 直接 validate，是因为历史行里可能存了 dict 或字符串，
        严格校验会在读路径上抛错。
        """
        if not isinstance(raw, list):
            return cls(entries=[])
        good: List[ProcessingTaskErrorEntry] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                good.append(ProcessingTaskErrorEntry.model_validate(item))
            except Exception as exc:  # noqa: BLE001 —— 单条坏记录跳过，不影响其余
                logger.debug("跳过无法解析的处理任务错误记录: %s", exc)
                continue
        return cls(entries=good)


class CompilationArtifactOutput(BaseModel):
    """``CompilationArtifact.output`` 列。

    该列承载五级编译器各级的产出（information/knowledge/process/capability/runtime），
    **每一级的结构都不同**，因此这里只约束公共外壳（stage / 关键标量），
    各级私有结构仍以自由 dict 承载（``extra="allow"``）。

    刻意不为每级编造强类型：compiler 五个 schema 的输出形态由
    ``app/schemas/compiler.py`` 各自定义，此处再复制一份必然与它们漂移。
    """

    model_config = ConfigDict(extra="allow")

    stage: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    summary: Optional[str] = None
