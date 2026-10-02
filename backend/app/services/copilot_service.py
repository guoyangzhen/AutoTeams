"""对话式 Agent 创建 Copilot 服务。

目标：用户在对话框说一句话即可完成 Agent 创建，跳过 7 步 Setup 向导。

流程：
    parse_user_intent（LLM tool calling 风格提取 folder_path / agent_name_hint）
    → scan_folder（复用现有安全扫描，受 UPLOAD_ROOT 限制）
    → auto_generate_plan（基于文件特征生成 name / description / system_prompt）
    → build_agent_via_graph（require_approval=False，thread_id=uuid4）

设计要点：
- 复用 folder_scanner.scan_folder 的路径安全校验，与 /folders/scan、/setup/start 保持一致
- LLM 不可用（fallback 模式）时用正则兜底提取路径，保证演示链路在无 API Key 时仍可用
- 本服务不直接写 DB：build_agent_via_graph 内部用独立 session（async_session_factory）写入，
  符合「service 层写操作显式 commit」约束——此处无写操作故无需 commit
- 异常以可区分类型抛出（ValueError/FileNotFoundError/PermissionError），由 API 层映射 HTTP 状态码
"""
import asyncio
import logging
import os
import re
import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.services.llm_service import llm_service, ModelTier
from app.services.folder_scanner import scan_folder
from app.services.prompt_security import (
    wrap_untrusted,
    safe_json_extract,
    truncate,
    SYSTEM_PROMPT_GUARDRAIL,
)
from app.utils.error_codes import ErrorCode
from app.utils.metrics import errors_total

logger = logging.getLogger(__name__)

# 意图解析 Prompt：要求 LLM 以 tool calling 风格输出结构化 JSON
_INTENT_PROMPT = """你是 Agent 创建助手。从用户消息中提取创建智能体所需的关键信息。

用户消息：
{user_message}

请提取以下信息并以 JSON 返回（不要返回其他内容）：
{{
  "folder_path": "用户提到的知识源文件夹路径（Windows 如 D:\\\\公司文档 或 Unix 如 /home/user/docs，若未提及则为空字符串）",
  "agent_name_hint": "用户期望的智能体名称（如 HR 答疑助手，若未提及则为空字符串）",
  "description_hint": "用户对智能体职责的描述（若未提及则为空字符串）"
}}

规则：
- folder_path 必须是文件系统路径，不要包含引号、句号、逗号等标点
- 若用户消息中无明确路径，folder_path 返回空字符串
- 只返回 JSON
"""

# 正则兜底：提取 Windows 盘符路径或 Unix 绝对路径
# (?<![A-Za-z]) 排除 http: 这类伪路径（冒号前为字母时不匹配盘符）
_WIN_PATH_RE = re.compile(r'(?<![A-Za-z])[A-Za-z]:[\\/][^\s\'"`，,。；;:）)】\]]+')
_UNIX_PATH_RE = re.compile(r'(?:^|\s)(/[^\s\'"`，,。；;:）)】\]]+)')


class CopilotService:
    """对话式 Agent 创建服务。"""

    async def create_agent_from_message(
        self, message: str, user: User, db: AsyncSession
    ) -> dict:
        """从用户一句话创建 Agent。

        Args:
            message: 用户自然语言消息，如 "用 D:\\公司文档 做一个 HR 答疑助手"
            user: 当前用户（需已绑定 enterprise_id）
            db: 请求级 DB session（本服务不直接写库，graph 内部用独立 session）

        Returns:
            {status, thread_id, agent_id, agent_name, redirect_url, build_status, message}

        Raises:
            ValueError: 未绑定企业 / 未能解析出路径 / 路径不合法
            FileNotFoundError: 文件夹不存在
            PermissionError: 无访问权限
        """
        if not user.enterprise_id:
            raise ValueError(ErrorCode.SETUP_ENTERPRISE_REQUIRED)

        # 1. 解析意图（LLM tool calling 风格 + 正则兜底）
        intent = await self.parse_user_intent(message)
        folder_path = intent.get("folder_path", "").strip()
        if not folder_path:
            raise ValueError(ErrorCode.INVALID_REQUEST)

        # 2. 扫描文件夹（复用安全扫描；通过 to_thread 避免阻塞事件循环）
        #    递归扫描以获取准确的文件特征供方案生成使用
        try:
            files = await asyncio.to_thread(scan_folder, folder_path, True)
        except FileNotFoundError as e:
            logger.warning(f"Copilot 文件夹不存在: {e}")
            raise FileNotFoundError(ErrorCode.FOLDER_NOT_FOUND) from e
        except PermissionError as e:
            logger.warning(f"Copilot 文件夹无访问权限: {e}")
            raise PermissionError(ErrorCode.FOLDER_ACCESS_DENIED) from e
        except ValueError as e:
            # PathSecurityError 在 scan_folder 中被转为 ValueError
            logger.warning(f"Copilot 路径校验失败: {e}")
            raise ValueError(ErrorCode.FOLDER_PATH_INVALID) from e

        # 3. 基于文件特征自动生成方案（对齐 AgentBuildState 字段）
        plan = await self.auto_generate_plan(folder_path, files, intent)

        # 4. 调用 LangGraph 构建（默认自动审批，演示零中断）
        thread_id = str(uuid.uuid4())
        # 延迟导入避免循环依赖
        from app.services.agent_graph import build_agent_via_graph
        from app.database import async_session_factory

        result = await build_agent_via_graph(
            db_session_factory=async_session_factory,
            enterprise_id=user.enterprise_id,
            name=plan["name"],
            description=plan["description"],
            folder_path=folder_path,
            require_approval=False,
            thread_id=thread_id,
        )

        return {
            "status": "building",
            "thread_id": thread_id,
            "agent_id": result.get("agent_id"),
            "agent_name": plan["name"],
            "redirect_url": f"/canvas/{thread_id}",
            "build_status": result.get("status", "completed"),
            "message": f"已为你创建「{plan['name']}」，正在自动构建中，预计 1-2 分钟完成。",
        }

    async def parse_user_intent(self, message: str) -> dict:
        """解析用户意图，提取 folder_path / agent_name_hint / description_hint。

        优先用 LLM 提取；LLM 不可用或返回非法 JSON 时用正则兜底，保证演示链路可用。
        """
        safe_msg = wrap_untrusted(truncate(message, 2000), "用户消息")
        prompt = _INTENT_PROMPT.format(user_message=safe_msg)

        intent: Optional[dict] = None
        try:
            reply = await llm_service.chat(
                [
                    {"role": "system", "content": SYSTEM_PROMPT_GUARDRAIL},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_tokens=512,
                tier=ModelTier.CHEAP,
            )
            intent = safe_json_extract(reply)
        except Exception as e:
            errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
            logger.warning(f"LLM 意图解析失败，使用正则兜底: {e}", exc_info=True)

        if not isinstance(intent, dict):
            intent = {}

        folder_path = str(intent.get("folder_path", "")).strip()
        agent_name_hint = str(intent.get("agent_name_hint", "")).strip()
        description_hint = str(intent.get("description_hint", "")).strip()

        # 正则兜底：LLM 未提取到路径时从原文提取
        if not folder_path:
            folder_path = self._extract_path_regex(message)

        return {
            "folder_path": folder_path,
            "agent_name_hint": agent_name_hint,
            "description_hint": description_hint,
        }

    @staticmethod
    def _extract_path_regex(message: str) -> str:
        """正则兜底提取文件系统路径（Windows 盘符路径优先，其次 Unix 绝对路径）。"""
        m = _WIN_PATH_RE.search(message)
        if m:
            return m.group(0).rstrip(".,;:，。；：")
        m = _UNIX_PATH_RE.search(message)
        if m:
            return m.group(1).rstrip(".,;:，。；：")
        return ""

    async def auto_generate_plan(
        self, folder_path: str, files: list, intent: dict
    ) -> dict:
        """基于文件特征 + 意图生成 name / description / system_prompt。

        输出结构对齐 AgentBuildState 字段（name/description/system_prompt/folder_path），
        便于直接注入 LangGraph initial_state。

        说明：build_agent_via_graph 的 builder_node 会用 SYSTEM_PROMPT_TEMPLATE 重新生成
        最终 system_prompt，此处 system_prompt 仅作 plan 字段对齐与日志展示。
        """
        # 文件特征统计
        type_counts: dict[str, int] = {}
        total_size = 0
        for f in files:
            type_counts[f.file_type] = type_counts.get(f.file_type, 0) + 1
            total_size += f.size
        folder_name = os.path.basename(os.path.normpath(folder_path)) or "知识库"

        # 名称：意图提示 > 文件夹名 + "助手"
        name = intent.get("agent_name_hint") or f"{folder_name}助手"

        # 描述：意图提示 > 基于文件特征的确定性描述
        type_desc = "、".join(f"{t}({c}个)" for t, c in type_counts.items()) or "未知类型"
        size_mb = total_size / 1024 / 1024
        if intent.get("description_hint"):
            description = intent["description_hint"]
        elif files:
            description = (
                f"基于「{folder_name}」构建的知识问答智能体，"
                f"共 {len(files)} 个文件（{type_desc}，约 {size_mb:.1f}MB），"
                f"可针对文档内容进行检索与答疑。"
            )
        else:
            description = f"基于「{folder_name}」构建的知识问答智能体，可针对文档内容进行检索与答疑。"

        # system_prompt：模板（graph builder_node 会重新生成，此处仅作字段对齐）
        system_prompt = (
            f"你是「{name}」，一个企业知识助手。"
            f"你的职责是基于知识库（来源：{folder_name}）回答用户问题。"
            f"请优先检索知识库内容作答，无法确定时如实告知，不要编造。"
        )

        return {
            "name": name,
            "description": description,
            "system_prompt": system_prompt,
            "folder_path": folder_path,
        }


copilot_service = CopilotService()
