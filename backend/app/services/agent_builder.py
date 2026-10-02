import asyncio
import logging
import uuid
from typing import Awaitable, Callable, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skill import Skill
from app.services.document_processor import process_document
from app.services.image_processor import process_image
from app.services.video_processor import process_video
from app.services.audio_transcriber import audio_transcriber
from app.services.rag.chunker_service import chunker_service
# BE-SEC-02: 统一错误码


logger = logging.getLogger(__name__)

# P2-1: 文件内容提取最大并发数（限制 DB 锁竞争、内存与外部服务压力）
_MAX_CONCURRENT_FILE_PROCESSING = 3

# 进度回调类型：async fn(progress: float, message: str, processed: int,
#                        failed: int, knowledge: int, total: int) -> None
ProgressCallback = Callable[..., Awaitable[None]]

SYSTEM_PROMPT_TEMPLATE = """你是一个企业知识助手，服务于"{enterprise_name}"。
你的名字是"{agent_name}"。

你的职责是基于企业知识库回答用户的问题。
{description}

回答规则：
1. 优先使用知识库中的信息回答问题
2. 如果知识库中没有相关信息，请明确告知用户
3. 保持回答准确、专业、有条理
4. 对于不确定的信息，说明不确定性
5. 引用来源时标注出处

知识库概况：
- 包含 {file_count} 个文件
- 涵盖 {knowledge_count} 条知识片段
"""


def _detect_file_types(files: list) -> dict[str, int]:
    type_counts: dict[str, int] = {}
    for f in files:
        type_counts[f.file_type] = type_counts.get(f.file_type, 0) + 1
    return type_counts


async def _create_default_skills(
    db: AsyncSession,
    agent_id: str,
    file_types: dict[str, int],
    selected_skills: Optional[list[str]] = None,
) -> list[Skill]:
    """创建 Agent 默认/选中技能。

    如果 selected_skills 非空，优先按 Setup 向导中用户选择的 skill catalog 创建；
    否则按文件类型自动推断默认技能。
    """
    skills = []

    # P1-6.4: Setup 向导用户已选技能，直接创建对应 Skill 记录
    if selected_skills:
        for skill_key in selected_skills:
            catalog = _SETUP_SKILL_CATALOG.get(skill_key)
            if not catalog:
                continue
            skill = Skill(
                id=str(uuid.uuid4()),
                agent_id=agent_id,
                name=catalog["name"],
                description=catalog["description"],
                skill_type=catalog.get("skill_type", "custom"),
                input_type=catalog.get("input_type", "text"),
                output_type=catalog.get("output_type", "text"),
                config=catalog.get("config", {}),
            )
            db.add(skill)
            skills.append(skill)

    # 未选择技能时，按文件类型自动推断兜底
    if not skills:
        if file_types.get("image", 0) > 0:
            skill = Skill(
                id=str(uuid.uuid4()),
                agent_id=agent_id,
                name="图片分析",
                description=f"分析图片内容，识别其中的对象、文字和关键信息。已学习 {file_types['image']} 张图片。",
                skill_type="custom",
                input_type="image",
                output_type="text",
                config={
                    "prompt_template": "请分析这张图片的内容，识别其中的对象、文字和关键信息。",
                    "file_count": file_types["image"],
                },
            )
            db.add(skill)
            skills.append(skill)

        if file_types.get("video", 0) > 0:
            skill = Skill(
                id=str(uuid.uuid4()),
                agent_id=agent_id,
                name="视频问答",
                description=f"基于视频内容回答问题，支持搜索知识点。已处理 {file_types['video']} 个视频。",
                skill_type="custom",
                input_type="text",
                output_type="text",
                config={
                    "prompt_template": "根据已处理的视频内容，回答以下问题：\n{input}",
                    "file_count": file_types["video"],
                },
            )
            db.add(skill)
            skills.append(skill)

        doc_count = file_types.get("document", 0) + file_types.get("spreadsheet", 0) + file_types.get("presentation", 0)
        if doc_count > 0:
            skill = Skill(
                id=str(uuid.uuid4()),
                agent_id=agent_id,
                name="文档摘要",
                description=f"生成文档摘要，提取关键要点。已处理 {doc_count} 个文档。",
                skill_type="text_summarization",
                input_type="text",
                output_type="text",
                config={
                    "prompt_template": "请将以下文档内容总结为简洁的摘要：\n{input}",
                    "file_count": doc_count,
                },
            )
            db.add(skill)
            skills.append(skill)

            skill = Skill(
                id=str(uuid.uuid4()),
                agent_id=agent_id,
                name="数据提取",
                description=f"从文档中提取结构化数据。已处理 {doc_count} 个文档。",
                skill_type="data_extraction",
                input_type="text",
                output_type="text",
                config={
                    "prompt_template": "请从以下文本中提取关键信息：\n{input}",
                    "fields": ["关键信息", "数据点", "结论"],
                    "file_count": doc_count,
                },
            )
            db.add(skill)
            skills.append(skill)

    return skills


# P1-6.4: Setup 向导技能 catalog（与 frontend/src/pages/Setup.tsx 保持一致）
_SETUP_SKILL_CATALOG = {
    "ticket-classification": {
        "name": "工单分类",
        "description": "自动识别并分类客户工单类型",
        "skill_type": "classification",
        "input_type": "text",
        "output_type": "text",
        "config": {"prompt_template": "请判断以下工单的类型并给出分类理由：\n{input}"},
    },
    "faq-match": {
        "name": "FAQ 匹配",
        "description": "基于知识库检索常见问题答案",
        "skill_type": "faq_match",
        "input_type": "text",
        "output_type": "text",
        "config": {"prompt_template": "请基于知识库回答以下常见问题：\n{input}"},
    },
    "sentiment-analysis": {
        "name": "情感分析",
        "description": "分析文本情感倾向与紧急程度",
        "skill_type": "sentiment_analysis",
        "input_type": "text",
        "output_type": "text",
        "config": {"prompt_template": "请分析以下文本的情感倾向和紧急程度：\n{input}"},
    },
    "knowledge-qa": {
        "name": "知识问答",
        "description": "基于知识库的精准问答能力",
        "skill_type": "knowledge_qa",
        "input_type": "text",
        "output_type": "text",
        "config": {"prompt_template": "请基于知识库精准回答以下问题：\n{input}"},
    },
    "summarization": {
        "name": "内容摘要",
        "description": "自动提取长文本的关键摘要",
        "skill_type": "text_summarization",
        "input_type": "text",
        "output_type": "text",
        "config": {"prompt_template": "请为以下文本生成简洁摘要：\n{input}"},
    },
    "code-search": {
        "name": "代码检索",
        "description": "在代码库中语义化检索相关片段",
        "skill_type": "code_search",
        "input_type": "text",
        "output_type": "text",
        "config": {"prompt_template": "请在代码库中检索与以下问题相关的代码片段：\n{input}"},
    },
    "data-cleaning": {
        "name": "数据清洗",
        "description": "清洗与归一化非结构化数据",
        "skill_type": "data_cleaning",
        "input_type": "text",
        "output_type": "text",
        "config": {"prompt_template": "请清洗并归一化以下非结构化数据：\n{input}"},
    },
    "intent-recognition": {
        "name": "意图识别",
        "description": "识别用户输入的真实意图",
        "skill_type": "intent_recognition",
        "input_type": "text",
        "output_type": "text",
        "config": {"prompt_template": "请识别以下用户输入的真实意图：\n{input}"},
    },
    "multi-turn-dialog": {
        "name": "多轮对话",
        "description": "维护上下文的多轮对话管理",
        "skill_type": "multi_turn_dialog",
        "input_type": "text",
        "output_type": "text",
        "config": {"prompt_template": "请结合上下文回答以下多轮对话问题：\n{input}"},
    },
}


# 3.3.2: build_agent 已删除（死代码）。
# 该函数是旧的 Agent 构建入口，已被 services/agent_graph.py 的 LangGraph 实现
# 完全取代（api/process.py:87 注释明确"不再调用 agent_builder.build_agent 旧路径"）。
# 本模块仅保留 _create_default_skills / _detect_file_types / _process_file / SYSTEM_PROMPT_TEMPLATE
# 等辅助函数，供 agent_graph.py 与 incremental_updater.py 调用。


async def _process_file(path: str, file_type: str) -> list[str]:
    text = ""

    if file_type in ("document", "spreadsheet", "presentation"):
        text = await process_document(path)
    elif file_type == "image":
        result = await process_image(path)
        text = result.get("analysis", "")
    elif file_type == "video":
        result = await process_video(path)
        text = result.get("summary", "") + "\n" + result.get("transcription", "")
    elif file_type == "audio":
        # D5: 纯音频文件（mp3, wav, flac, aac）使用 faster-whisper 转录
        text = await audio_transcriber.transcribe(path)
    elif file_type == "code":
        text = await process_document(path)
    else:
        return []

    if not text:
        return []

    # P1-1: 使用结构感知分块替代固定500字符分块
    # P2-1: 分块是 CPU 密集型操作，放到线程池避免阻塞事件循环
    return await asyncio.to_thread(chunker_service.chunk_text, text, file_type=file_type)
