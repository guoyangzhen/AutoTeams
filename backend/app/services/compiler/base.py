"""编译器抽象基类（PRD §4.3 五级编译器）。

定义 CompilerBase 抽象基类 + CompilationContext + CompilationResult。
每级编译器继承 CompilerBase，实现 compile() 方法。

五级编译器设计（PRD §4.3）：
1. Information Compiler：文件解析 + NER + 结构化输出
2. Knowledge Compiler：实体对齐 + 关系抽取 + 图构建 + 向量化
3. Process Compiler：SOP 提取 + 审批流建模 + KPI 关联
4. Capability Compiler：岗位能力矩阵构建
5. Runtime Compiler：整合为 Runtime + 工具绑定 + Agent 配置模板

每级产出：output + confidence + discovered_summary（供编译动画展示）
"""
import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.compiler import CompilationArtifact

logger = logging.getLogger(__name__)


class Stage(str, Enum):
    """编译层级枚举。"""
    INFORMATION = "information"
    KNOWLEDGE = "knowledge"
    PROCESS = "process"
    CAPABILITY = "capability"
    RUNTIME = "runtime"


@dataclass
class CompilationContext:
    """编译上下文——传递给每级编译器的运行环境信息。

    包含企业 ID、数据库会话、上游编译产物、LLM 调用句柄等。
    """
    enterprise_id: str
    db: AsyncSession
    folder_path: Optional[str] = None  # Information Compiler 的文件源
    upstream: dict[str, Any] = field(default_factory=dict)  # 上游编译产物
    llm_stats: dict[str, Any] = field(default_factory=dict)


@dataclass
class CompilationResult:
    """单级编译结果。"""
    stage: str
    output: Any  # 该级的输出（schema 对象或 dict）
    confidence: float = 0.0
    discovered_summary: str = ""  # 供编译动画展示
    discovered_count: int = 0  # 发现的条目数

    def to_artifact_data(self) -> dict:
        """转换为可存储的 artifact 数据。"""
        if hasattr(self.output, "model_dump"):
            output_data = self.output.model_dump(mode="json")
        elif isinstance(self.output, dict):
            output_data = self.output
        else:
            output_data = {"value": str(self.output)}
        return {
            "output": output_data,
            "confidence": self.confidence,
            "discovered_summary": self.discovered_summary,
        }


class CompilerBase(ABC):
    """编译器抽象基类。

    每级编译器继承此类，实现 compile() 方法。
    compile() 接收 CompilationContext，返回 CompilationResult。
    """

    stage: str = "base"

    def __init__(self):
        self.logger = logging.getLogger(f"compiler.{self.stage}")

    @abstractmethod
    async def compile(self, ctx: CompilationContext) -> CompilationResult:
        """执行编译。

        Args:
            ctx: 编译上下文

        Returns:
            编译结果（含 output + confidence + discovered_summary）
        """
        ...

    def _make_entry_id(self, prefix: str) -> str:
        """生成带前缀的唯一 ID。"""
        return f"{prefix}_{uuid.uuid4().hex[:12]}"

    async def save_artifact(
        self,
        ctx: CompilationContext,
        result: CompilationResult,
        job_id: str,
        duration_ms: Optional[int] = None,
    ) -> CompilationArtifact:
        """将编译产物保存为 artifact（供后续层级消费 + 编译动画展示）。

        duration_ms 记录该级实际耗时，供「回放模式」按真实时序播放五级管道动画
        （路演场景下真实编译需 10-20 分钟，无法现场等待，改为回放真实历史数据）。
        """
        artifact_data = result.to_artifact_data()
        artifact = CompilationArtifact(
            enterprise_id=ctx.enterprise_id,
            job_id=job_id,
            stage=result.stage,
            output=artifact_data["output"],
            confidence=result.confidence,
            discovered_summary=artifact_data["discovered_summary"],
            duration_ms=duration_ms,
        )
        ctx.db.add(artifact)
        await ctx.db.flush()
        return artifact

    @staticmethod
    def calculate_confidence(
        total: int, extracted: int, base: float = 0.3, max_bonus: float = 0.7
    ) -> float:
        """根据提取率计算置信度。

        置信度 = base + max_bonus * (extracted / max(total, 1))
        """
        if total <= 0:
            return base
        ratio = min(extracted / total, 1.0)
        return min(base + max_bonus * ratio, 1.0)


def safe_json_parse(text: str):
    """安全解析 LLM 返回的 JSON（支持数组和对象）。

    prompt_security.safe_json_extract 只提取首个 {...} 对象，
    无法处理 JSON 数组 [...]。本函数先尝试 json.loads 解析完整文本，
    失败时尝试剥离 markdown 代码块包裹后重新解析，
    然后尝试提取并修复截断的 JSON 数组/对象（LLM max_tokens 截断场景），
    最后回退到 safe_json_extract。

    Returns:
        解析后的 Python 对象（list/dict），或 None
    """
    import json
    import re
    if not text:
        return None
    text = text.strip()
    # 先尝试完整解析（适用于干净的 JSON 响应）
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    # 剥离 markdown 代码块包裹（```json ... ``` 或 ``` ... ```）
    code_block_match = re.search(r'```(?:json)?\s*\n?([\s\S]*?)\n?```', text)
    if code_block_match:
        inner = code_block_match.group(1).strip()
        try:
            return json.loads(inner)
        except (json.JSONDecodeError, ValueError):
            pass
    # 尝试提取首个完整 JSON 数组 [...]
    array_match = re.search(r'\[[\s\S]*\]', text)
    if array_match:
        try:
            return json.loads(array_match.group(0))
        except (json.JSONDecodeError, ValueError):
            pass
    # 尝试提取首个完整 JSON 对象 {...}
    obj_match = re.search(r'\{[\s\S]*\}', text)
    if obj_match:
        try:
            return json.loads(obj_match.group(0))
        except (json.JSONDecodeError, ValueError):
            pass
    # ---- 截断修复：LLM 因 max_tokens 截断导致 JSON 不完整 ----
    # 场景：响应以 ```json\n[ 或 [ 开头，但没有闭合的 ] 或 ```
    # 策略：提取从 [ 到最后一个完整 } 的内容，补上 ] 闭合
    truncated_array = _repair_truncated_json_array(text)
    if truncated_array is not None:
        return truncated_array
    # 回退到 safe_json_extract（适用于嵌入在文本中的 JSON 对象）
    from app.services.prompt_security import safe_json_extract
    return safe_json_extract(text)


def _repair_truncated_json_array(text: str):
    """修复因 max_tokens 截断的 JSON 数组。

    当 LLM 响应被截断时，JSON 数组可能缺少闭合的 ]。
    本函数提取从首个 [ 到最后一个完整对象 } 的内容，补上 ] 闭合。

    Returns:
        解析后的 list，或 None（如果无法修复）
    """
    import json
    import re

    # 剥离 markdown 代码块开头（可能没有闭合的 ```）
    cleaned = text
    code_block_start = re.search(r'```(?:json)?\s*\n?', cleaned)
    if code_block_start:
        cleaned = cleaned[code_block_start.end():]

    # 找到第一个 [
    bracket_idx = cleaned.find('[')
    if bracket_idx == -1:
        return None

    # 取从 [ 开始的所有内容
    fragment = cleaned[bracket_idx:]

    # 找到最后一个完整的 } （对象闭合）
    last_brace = fragment.rfind('}')
    if last_brace == -1:
        return None

    # 截取到最后一个 } 为止，补上 ] 闭合
    repaired = fragment[:last_brace + 1] + ']'

    # 如果最后一个 } 后面有逗号或空白，需要处理
    # 检查 } 后面是否跟着 , 或空白（表示后面本应有下一个对象但被截断）
    after_brace = fragment[last_brace + 1:].lstrip()
    if after_brace.startswith(','):
        # } 后面有逗号，直接用 } + ]
        repaired = fragment[:last_brace + 1] + ']'

    try:
        result = json.loads(repaired)
        if isinstance(result, list):
            logger.debug(
                f"修复截断的 JSON 数组：提取到 {len(result)} 个元素"
            )
            return result
    except (json.JSONDecodeError, ValueError):
        pass

    # 第二次尝试：逐个提取完整的 {...} 对象，手动构建数组
    objects = re.findall(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', fragment)
    if objects:
        try:
            items = [json.loads(obj) for obj in objects]
            logger.debug(
                f"修复截断的 JSON 数组（逐对象提取）：提取到 {len(items)} 个元素"
            )
            return items
        except (json.JSONDecodeError, ValueError):
            pass

    return None
