"""Agent Chat 端点 + SSE 流式 + chat 助手 (3.3.3 拆分)。

涵盖职责：
- 同步对话：POST /agents/{id}/chat
- 流式对话：POST /agents/{id}/chat/stream（SSE，含并发限制/超时/断连检测）
- chat 专属助手：会话获取、知识检索、消息构建、token 估算、来源构建、持久化重试
"""
import asyncio
import contextlib
import json
import logging

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db, async_session_factory
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User
from app.schemas.agent import ChatMessage
from app.services.vector_store import VectorStoreService, ChromaDBConnectionError
from app.services.rag import rag_engine
from app.services.llm_service import llm_service, ModelTier, LLMUsageStats
from app.services.rag_evaluator import rag_evaluator
from app.utils.security import get_current_user
from app.utils.response import success_response
from app.utils.rate_limit import rate_limit_chat, rate_limit_sse
# P1-06: Prometheus 指标
from app.utils.metrics import sse_active_streams, llm_tokens_total, errors_total
# P2-3: tiktoken 精确 token 估算与截断
from app.utils.tokens import (
    count_text_tokens,
    count_messages_tokens,
    truncate_messages_to_budget,
)

from app.api.agents._helpers import _get_agent_or_404

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agents", tags=["Agent"])

# P1-02: SSE 流并发限制（单实例最多 50 个并发流式请求）
# 防止单个实例过载导致 LLM API 限流或 OOM
# 5.3.3: 改用分布式信号量，Redis 可用时多实例共享并发限制
# 3.4.4: 模块常量统一使用 UPPER_SNAKE_CASE（不加前导 _）
SSE_CONCURRENCY_LIMIT = 50

# 知识检索默认 Top-K（Agent 未配置 config.knowledge.topK 时的回退值）
DEFAULT_TOP_K = 5


def _get_sse_semaphore():
    """惰性初始化 SSE 信号量。

    Redis 可用时返回 RedisSemaphore（多实例共享 limit），
    不可用时回退到 asyncio.Semaphore（单实例行为）。
    """
    from app.utils.distributed_semaphore import get_distributed_semaphore
    return get_distributed_semaphore("sse", SSE_CONCURRENCY_LIMIT)


async def _resolve_enterprise_llm(
    db: AsyncSession, user: User
) -> dict:
    """解析企业自定义模型 API 配置（密钥解密后仅存内存，不落日志/响应）。

    未配置或未启用时返回全空 dict，由 llm_service 回退到全局配置。
    """
    if not user.enterprise_id:
        return {"model": None, "api_key": None, "api_base": None}
    from app.services.llm_config import get_config, resolve_enterprise_llm
    config = await get_config(db, user.enterprise_id)
    return resolve_enterprise_llm(config)


# P1-02: 单 chunk 间隔超时（秒）
# 超过此时间未收到任何 chunk，认为流异常，主动关闭
SSE_CHUNK_TIMEOUT_SECONDS = 60
# P1-05: SSE 流全局总超时（秒）
# 超过此时间强制关闭连接，防止单个流式响应无限占用资源
SSE_GLOBAL_TIMEOUT_SECONDS = 300


def _emit_sse_error(conv_id: str, message: str, error_type: str | None = None) -> str:
    """3.4.8: 统一 SSE 错误事件生成 + metrics/log 上报。

    全局 @app.exception_handler(Exception) 不适用于 StreamingResponse 已开始输出后
    的异常（SSE 流中途异常由 event_generator 内的 try/except 捕获）。此函数统一
    处理三条上报路径，避免每个 catch 块重复实现导致漏报：

    1. errors_total 指标（若提供 error_type，用于 Prometheus 告警）
    2. 服务端日志记录（用于排查）
    3. SSE error 事件字符串（返回供 yield，通知前端）

    调用方负责 yield 返回值并设置 msg_id = "error"。
    """
    if error_type:
        try:
            errors_total.labels(module=__name__, exception_type=error_type).inc()
        except (ValueError, TypeError, OSError) as metric_err:
            logger.warning(f"记录 SSE 错误指标失败: {metric_err}", exc_info=True)
    logger.error(f"SSE 错误（conv={conv_id}）: {message}")
    return f"data: {json.dumps({'error': message, 'done': True}, ensure_ascii=False)}\n\n"


@router.post("/{agent_id}/chat")
@rate_limit_chat()
async def chat_with_agent(
    agent_id: str,
    data: ChatMessage,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    agent = await _get_agent_or_404(db, agent_id, current_user)

    conversation = await _get_or_create_conversation(
        db, current_user.id, agent_id, data.conversation_id
    )

    user_message = Message(
        conversation_id=conversation.id,
        role="user",
        content=data.content,
    )
    db.add(user_message)
    await db.flush()
    # 显式 commit：确保用户消息持久化，后续历史查询能看到它
    await db.commit()

    # 获取多轮对话历史（在 commit 之后，能看到刚保存的用户消息）
    history = await _fetch_conversation_history(db, conversation.id, exclude_id=user_message.id)

    context_docs, rag_meta = await _search_knowledge(
        agent.enterprise_id, agent_id, data.content, _resolve_top_k(agent)
    )

    messages = _build_messages(agent.system_prompt, data.content, context_docs, history)
    # P1-3: 用户主问答使用强模型；传入 LLMUsageStats 追踪成本
    # P1-6.4: 若 Agent 在 Setup 向导中指定了模型，优先使用该模型
    agent_model = (agent.config or {}).get("model") if agent.config else None
    # 企业自定义模型 API 配置优先，其次 Agent 模型，最后全局配置
    llm_override = await _resolve_enterprise_llm(db, current_user)
    effective_model = llm_override.get("model") or agent_model
    llm_stats = LLMUsageStats()
    reply_content = await llm_service.chat(
        messages,
        model=effective_model,
        tier=ModelTier.STRONG,
        stats=llm_stats,
        api_key=llm_override.get("api_key"),
        api_base=llm_override.get("api_base"),
    )

    # 优先使用 LLM 实际返回的 token 数，若未返回则用估算值
    total_tokens = llm_stats.total_tokens or (
        _estimate_tokens_for_messages(messages) + _estimate_tokens(reply_content)
    )

    # P1-06: 记录 LLM token 消耗指标
    used_model = llm_stats.model_used or settings.OPENAI_MODEL
    try:
        if llm_stats.prompt_tokens:
            llm_tokens_total.labels(model=used_model, direction="prompt").inc(llm_stats.prompt_tokens)
        if llm_stats.completion_tokens:
            llm_tokens_total.labels(model=used_model, direction="completion").inc(llm_stats.completion_tokens)
    except (ValueError, TypeError, OSError) as e:
        # P1-4: 指标记录失败不应影响主请求，但需记录以便排查
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.warning("记录 LLM token 指标失败", exc_info=True)

    assistant_message = Message(
        conversation_id=conversation.id,
        role="assistant",
        content=reply_content,
        sources=[{"content": d["content"], "source": d["metadata"].get("source", "")} for d in context_docs] if context_docs else None,
        token_count=total_tokens,
        # P1-3: 使用实际使用的模型名（可能因故障转移而变化）
        model_used=llm_stats.model_used or settings.OPENAI_MODEL,
    )
    db.add(assistant_message)
    await db.flush()
    await db.commit()
    await db.refresh(assistant_message)

    # P1-RAG: 对话后异步自动评估 RAG 质量
    try:
        asyncio.create_task(rag_evaluator.evaluate_message(str(assistant_message.id)))
    except (RuntimeError, TypeError) as e:
        logger.warning(f"创建 RAG 评估任务失败: {e}", exc_info=True)

    return success_response({
        "message_id": str(assistant_message.id),
        "content": reply_content,
        "sources": assistant_message.sources,
        "conversation_id": str(conversation.id),
        "token_count": total_tokens,
        # P1-3: 返回实际使用的模型名（可能因故障转移而变化）和成本统计
        "model_used": llm_stats.model_used or settings.OPENAI_MODEL,
        "llm_stats": llm_stats.to_dict(),
    })


@router.post("/{agent_id}/chat/stream")
@rate_limit_sse()
async def chat_with_agent_stream(
    agent_id: str,
    data: ChatMessage,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    agent = await _get_agent_or_404(db, agent_id, current_user)

    conversation = await _get_or_create_conversation(
        db, current_user.id, agent_id, data.conversation_id
    )

    user_message = Message(
        conversation_id=conversation.id,
        role="user",
        content=data.content,
    )
    db.add(user_message)
    await db.flush()
    # 显式 commit：确保用户消息持久化，后续历史查询能看到它
    await db.commit()

    # 获取多轮对话历史（在 commit 之后）
    history = await _fetch_conversation_history(db, conversation.id, exclude_id=user_message.id)

    context_docs, rag_meta = await _search_knowledge(
        agent.enterprise_id, agent_id, data.content, _resolve_top_k(agent)
    )

    llm_messages = _build_messages(agent.system_prompt, data.content, context_docs, history)

    conv_id = str(conversation.id)
    # 预计算估算 token（用于保存到消息记录）
    prompt_tokens = _estimate_tokens_for_messages(llm_messages)
    # P1-6.4: 若 Agent 在 Setup 向导中指定了模型，优先使用该模型
    agent_model = (agent.config or {}).get("model") if agent.config else None
    # 企业自定义模型 API 配置优先，其次 Agent 模型，最后全局配置
    llm_override = await _resolve_enterprise_llm(db, current_user)
    effective_model = llm_override.get("model") or agent_model
    # P1-3: 流式接口使用强模型；model_used 在 stream 结束时确定
    # 3.4.1: 调用公开方法 get_model_for_tier（原 _get_model_for_tier 已改为公开）
    model_name = effective_model or llm_service.get_model_for_tier(ModelTier.STRONG)
    # P1-02: 捕获 request 到闭包变量，供 event_generator 检测断开
    req = request

    # 技术审计 R2 H1: 此后所有 DB 依赖数据已提取到局部变量（conv_id/history/
    # model_name 等），event_generator 内部使用独立 session（async_session_factory）。
    # 显式关闭请求 db 释放连接回池，避免 StreamingResponse 期间（可达 300s）
    # 无意义占用连接。get_db 在 stream 结束后会再次 close（幂等，无副作用）。
    await db.close()

    async def event_generator():
        # P1-02-A: 并发限制 —— 获取信号量，超时返回 503
        semaphore = _get_sse_semaphore()
        try:
            await asyncio.wait_for(semaphore.acquire(), timeout=5.0)
        except asyncio.TimeoutError:
            yield (
                f"data: {json.dumps({'error': '服务器繁忙，请稍后重试', 'done': True}, ensure_ascii=False)}\n\n"
            )
            return

        # P1-06: 活跃 SSE 流计数 +1
        try:
            sse_active_streams.inc()
        except (ValueError, TypeError, OSError) as e:
            errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
            logger.warning("增加 SSE 活跃流指标失败", exc_info=True)

        full_content = ""
        # P1-RAG: 预构建来源信息，异常路径也能在最终事件里返回空来源
        sources = _build_sources(context_docs)
        # P1-05: 全局超时控制
        start_time = asyncio.get_event_loop().time()
        # D3-S9 后端: 在流式内容开始前透传检索元数据，供前端展示检索轮次/引用来源/Self-RAG 评分
        # retrieval_rounds 来自 agentic_rag 的 iterations；self_rag_score 由 D2 在 agentic_rag.py 提供
        # （D2 未合并时为 None，前端按缺失处理）
        retrieval_metadata = {
            "query_type": rag_meta.get("query_type"),
            "retrieval_rounds": rag_meta.get("iterations", 0),
            "refined_queries": rag_meta.get("refined_queries", []),
            "sources": sources or [],
            "self_rag_score": rag_meta.get("self_rag_score"),
        }
        yield (
            f"data: {json.dumps({'retrieval_metadata': retrieval_metadata, 'done': False}, ensure_ascii=False)}\n\n"
        )
        # 3.1.6: stream 在 try 外声明，便于 finally 兜底关闭
        stream = None
        try:
            # 流式接口使用 STRONG tier（流开始后无法故障转移）
            # P1-6.4: 若 Agent 在 Setup 向导中指定了模型，优先使用该模型
            stream = llm_service.chat_stream(
                llm_messages,
                model=effective_model,
                tier=ModelTier.STRONG,
                api_key=llm_override.get("api_key"),
                api_base=llm_override.get("api_base"),
            ).__aiter__()
            while True:
                # P1-05: 全局超时检查
                elapsed = asyncio.get_event_loop().time() - start_time
                if elapsed > SSE_GLOBAL_TIMEOUT_SECONDS:
                    logger.warning(
                        f"SSE 流全局超时（{SSE_GLOBAL_TIMEOUT_SECONDS}s，conv={conv_id}），主动关闭"
                    )
                    yield (
                        f"data: {json.dumps({'error': '响应超时，请重试', 'done': True}, ensure_ascii=False)}\n\n"
                    )
                    break
                # P1-02-B: 客户端断开检测
                if await req.is_disconnected():
                    logger.info(f"SSE 客户端断开（conv={conv_id}），主动关闭 LLM 流")
                    # 3.1.6: 主动关闭 LLM stream，避免断连后继续消耗 API 配额与 SSE 信号量
                    with contextlib.suppress(Exception):
                        await stream.aclose()
                    stream = None
                    break
                # P1-02-C: 单 chunk 间隔超时
                try:
                    remaining = SSE_CHUNK_TIMEOUT_SECONDS
                    chunk = await asyncio.wait_for(
                        stream.__anext__(), timeout=remaining
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        f"SSE chunk 超时（{SSE_CHUNK_TIMEOUT_SECONDS}s 无数据，conv={conv_id}），主动关闭"
                    )
                    yield (
                        f"data: {json.dumps({'error': '响应超时，请重试', 'done': True}, ensure_ascii=False)}\n\n"
                    )
                    break
                except StopAsyncIteration:
                    break
                full_content += chunk
                yield f"data: {json.dumps({'content': chunk, 'done': False}, ensure_ascii=False)}\n\n"

            # 保存助手消息（仅在非错误退出且有内容时）
            if full_content:
                saved_msg_id = await _save_assistant_message_with_retry(
                    conv_id=conv_id,
                    full_content=full_content,
                    prompt_tokens=prompt_tokens,
                    model_name=model_name,
                    # 3.2.7: 复用已计算的 sources，避免重复调用 _build_sources
                    sources=sources,
                )
                if saved_msg_id is None:
                    # BE-REL-03: 持久化失败时通过 SSE 通知前端，避免用户以为消息已保存
                    # 3.4.8: 统一走 _emit_sse_error，补全 metrics 上报（原实现漏报 errors_total）
                    yield _emit_sse_error(
                        conv_id, "消息保存失败，历史记录可能缺失",
                        error_type="MessageSaveFailed",
                    )
                    msg_id = "error"
                else:
                    msg_id = saved_msg_id
                    # P1-RAG: 流式消息保存后异步评估 RAG 质量
                    try:
                        asyncio.create_task(rag_evaluator.evaluate_message(saved_msg_id))
                    except (RuntimeError, TypeError) as e:
                        logger.warning(f"创建流式 RAG 评估任务失败: {e}", exc_info=True)
            else:
                msg_id = "empty"
        except asyncio.CancelledError:
            logger.info(f"SSE 流被取消（conv={conv_id}）")
            raise
        except (httpx.HTTPError, RuntimeError, OSError, ValueError, TypeError) as e:
            # 3.4.8: 统一走 _emit_sse_error，避免 metrics/log 上报逻辑重复
            #  unwrap llm_service.chat_stream 抛出的 RuntimeError，露出原始认证/网络错误
            root = e.__cause__ if e.__cause__ is not None else e
            root_type = type(root).__name__
            # 安全修复：仅返回通用错误消息，不向客户端暴露内部异常详情
            if "AuthenticationError" in root_type or "Unauthorized" in str(root) or "invalid_api_key" in str(root):
                user_msg = "LLM 服务认证失败，请联系管理员检查配置"
            elif "RateLimitError" in root_type or "429" in str(root):
                user_msg = "请求过于频繁，请稍后重试"
            elif "APIConnectionError" in root_type or "ConnectError" in str(root):
                user_msg = "服务暂时不可用，请稍后重试"
            else:
                user_msg = "服务暂时不可用，请稍后重试"
            yield _emit_sse_error(
                conv_id, user_msg,
                error_type=type(e).__name__,
            )
            logger.error(f"SSE 流式异常详情（conv={conv_id}）: {e}", exc_info=True)
            msg_id = "error"
        finally:
            # 3.1.6: 兜底关闭 LLM stream，避免异常路径下底层 HTTP 连接与 API 配额泄漏
            if stream is not None:
                with contextlib.suppress(Exception):
                    await stream.aclose()
                stream = None
            # P1-02-B: 释放信号量（无论正常结束还是异常）
            semaphore.release()
            # P1-06: 活跃 SSE 流计数 -1
            try:
                sse_active_streams.dec()
            except (ValueError, TypeError, OSError) as e:
                errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
                logger.warning("减少 SSE 活跃流指标失败", exc_info=True)

        yield f"data: {json.dumps({'content': '', 'done': True, 'message_id': msg_id, 'conversation_id': conv_id, 'sources': sources}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _get_or_create_conversation(
    db: AsyncSession, user_id: str, agent_id: str, conversation_id: str | None
) -> Conversation:
    if conversation_id:
        result = await db.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id,
            )
        )
        conv = result.scalar_one_or_none()
        if conv:
            return conv

    conv = Conversation(user_id=user_id, agent_id=agent_id, title="新对话")
    db.add(conv)
    await db.flush()
    await db.commit()
    await db.refresh(conv)
    return conv


def _resolve_top_k(agent: Agent) -> int:
    """解析该 Agent 的检索 Top-K。

    知识库页「分块与索引配置」把 topK 保存在 agent.config.knowledge.topK，
    但检索链路此前硬编码 n_results=5 —— 配置保存了却从不生效，
    属于「装饰性配置」（UI v4 §一 罪二）。此处使其真实生效。

    取值范围与前端一致钳制在 1-20，避免非法配置放大检索开销。
    """
    config = agent.config or {}
    knowledge = config.get("knowledge") if isinstance(config, dict) else None
    raw = knowledge.get("topK") if isinstance(knowledge, dict) else None
    if raw is None:
        raw = config.get("top_k") if isinstance(config, dict) else None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_TOP_K
    return max(1, min(20, value))


async def _search_knowledge(
    enterprise_id: str, agent_id: str, query: str, n_results: int = DEFAULT_TOP_K
) -> tuple[list[dict], dict]:
    """在企业命名空间检索知识库，并在迁移期对旧集合做只读兜底。

    新写入只进入 ``ent_{enterprise}_agent_{agent}``；若该集合没有命中，才读取旧的
    ``agent_{agent}`` 集合，避免迁移期间突然丢失已索引知识。响应 metadata 显式标记
    legacy_fallback，便于观测并在完成回填后删除旧集合。
    """
    try:
        vector_store = await VectorStoreService.create_prefixed(enterprise_id, agent_id)
        result = await rag_engine.search(query, vector_store, n_results=n_results)
        documents = result["documents"]
        metadata = {
            "query_type": result["query_type"],
            "iterations": result["iterations"],
            "refined_queries": result.get("refined_queries", []),
            "self_rag_score": result.get("self_rag_score"),
            "legacy_fallback": False,
        }
        if documents:
            return documents, metadata

        # 双读仅在新集合无命中时触发，旧集合只读且不再接收新文档。
        legacy_store = await VectorStoreService.create(f"agent_{agent_id}")
        legacy_docs = await legacy_store.search(query, n_results=min(3, n_results))
        if legacy_docs:
            metadata.update({"query_type": "legacy_fallback", "iterations": 1, "legacy_fallback": True})
            logger.info("RAG 使用旧集合迁移兜底: enterprise=%s agent=%s", enterprise_id, agent_id)
            return legacy_docs, metadata
        return [], metadata
    except ChromaDBConnectionError as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"RAG 检索失败（ChromaDB 连接异常）: {e}", exc_info=True)
        return [], {"query_type": "error", "iterations": 0, "refined_queries": [], "legacy_fallback": False}
    except (RuntimeError, OSError, TypeError) as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.warning(f"RAG 检索失败: {e}")
        return [], {"query_type": "error", "iterations": 0, "refined_queries": [], "legacy_fallback": False}


def _estimate_tokens(text: str) -> int:
    """P2-3: 使用 tiktoken 精确估算单段文本 token 数。

    当 tiktoken 不可用时，回退到字符近似。
    """
    return count_text_tokens(text, model=settings.OPENAI_MODEL)


def _estimate_tokens_for_messages(messages: list[dict]) -> int:
    """P2-3: 使用 tiktoken 精确估算 messages 列表的总 token 数。"""
    return count_messages_tokens(messages, model=settings.OPENAI_MODEL)


# BE-REL-03: SSE 助手消息持久化重试策略
# - 最多 3 次保存尝试
# - 指数退避：0.2s / 0.4s / 0.8s
# - 独立 session，失败不影响主请求 session
# 3.4.4: 模块常量统一使用 UPPER_SNAKE_CASE（不加前导 _）
MAX_MESSAGE_SAVE_RETRIES = 3
MESSAGE_SAVE_BASE_DELAY = 0.2


def _build_sources(context_docs: list[dict] | None) -> list[dict] | None:
    """P1-RAG: 从检索文档构建前端展示所需的来源信息。"""
    if not context_docs:
        return None
    sources = []
    for doc in context_docs:
        metadata = doc.get("metadata", {}) or {}
        sources.append({
            "content": doc.get("content", ""),
            "source": metadata.get("source") or metadata.get("file_name") or metadata.get("filename") or "未知来源",
            "file_type": metadata.get("file_type", ""),
            "distance": doc.get("distance"),
        })
    return sources


async def _save_assistant_message_with_retry(
    conv_id: str,
    full_content: str,
    prompt_tokens: int,
    model_name: str,
    sources: list[dict] | None,
) -> str | None:
    """保存助手回复消息，带指数退避重试。

    返回消息 ID；全部重试失败后返回 None，由调用方决定如何通知前端。

    3.2.7: 接收预计算的 sources，避免在 SSE 路径重复调用 _build_sources。
    """
    last_exception: Exception | None = None
    for attempt in range(1, MAX_MESSAGE_SAVE_RETRIES + 1):
        try:
            async with async_session_factory() as new_db:
                completion_tokens = _estimate_tokens(full_content)
                total_tokens = prompt_tokens + completion_tokens
                # P1-06: 记录 LLM token 消耗指标（流式，使用估算值）
                try:
                    llm_tokens_total.labels(model=model_name, direction="prompt").inc(prompt_tokens)
                    llm_tokens_total.labels(model=model_name, direction="completion").inc(completion_tokens)
                except (ValueError, TypeError, OSError) as e:
                    errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
                    logger.warning("记录流式 LLM token 指标失败", exc_info=True)

                assistant_message = Message(
                    conversation_id=conv_id,
                    role="assistant",
                    content=full_content,
                    sources=sources,
                    token_count=total_tokens,
                    model_used=model_name,
                )
                new_db.add(assistant_message)
                await new_db.flush()
                await new_db.commit()
                await new_db.refresh(assistant_message)
                return str(assistant_message.id)
        except (SQLAlchemyError, OSError, RuntimeError, ValueError, TypeError) as e:
            last_exception = e
            errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
            logger.warning(
                f"保存助手消息失败（尝试 {attempt}/{MAX_MESSAGE_SAVE_RETRIES}）: {e}",
                exc_info=True,
            )
            if attempt < MAX_MESSAGE_SAVE_RETRIES:
                await asyncio.sleep(MESSAGE_SAVE_BASE_DELAY * (2 ** (attempt - 1)))

    logger.error(
        f"保存助手消息最终失败，对话 {conv_id} 历史可能缺失: {last_exception}",
        exc_info=True,
    )
    return None


async def _fetch_conversation_history(
    db: AsyncSession,
    conversation_id: str,
    exclude_id: str | None = None,
    max_messages: int = 20,
) -> list[Message]:
    """获取对话历史消息，按时间正序返回。

    限制最多 max_messages 条，且总 token 不超过 MAX_CONTEXT_TOKENS 的一半
    （另一半留给 system prompt + context docs + 当前 query + 回复）。
    从最近的消息开始保留（更相关），超出预算的旧消息被丢弃。
    """
    query = (
        select(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.is_deleted == False,  # noqa: E712
        )
        .order_by(Message.created_at.desc())
        .limit(max_messages)
    )
    if exclude_id:
        query = query.where(Message.id != exclude_id)

    result = await db.execute(query)
    # DB 查询结果按时间倒序（最新在前）
    recent_first = result.scalars().all()

    # 按 token 预算裁剪：从最新的开始保留，旧消息超预算则丢弃
    budget = settings.MAX_CONTEXT_TOKENS // 2
    kept_reversed: list[Message] = []
    used = 0
    for msg in recent_first:
        t = _estimate_tokens(msg.content) + 4
        if used + t > budget:
            break
        kept_reversed.append(msg)
        used += t

    # 反转为时间正序（旧→新），供 LLM 消费
    kept_reversed.reverse()
    return kept_reversed


def _build_messages(
    system_prompt: str,
    user_query: str,
    context_docs: list[dict],
    history: list[Message] | None = None,
) -> list[dict]:
    """构建 LLM messages，包含多轮对话历史和 token 窗口管理。

    消息顺序：
    1. system: agent 的系统提示
    2. system: 知识库检索到的上下文（如果有）
    3. 历史对话消息（user/assistant 交替，按时间正序）
    4. user: 当前查询

    P2-3: 最终使用 tiktoken 精确估算并截断，确保不超出上下文窗口。
    """
    messages: list[dict] = [{"role": "system", "content": system_prompt}]

    if context_docs:
        context = "\n\n".join(
            [f"[来源: {d['metadata'].get('source', '未知')}]\n{d['content']}" for d in context_docs]
        )
        messages.append({
            "role": "system",
            "content": f"以下是从知识库中检索到的相关信息：\n\n{context}\n\n请基于以上信息回答用户的问题。",
        })

    # 插入多轮对话历史
    if history:
        for msg in history:
            messages.append({"role": msg.role, "content": msg.content})

    messages.append({"role": "user", "content": user_query})

    # P2-3: 最终 token 预算保护（为回复预留 1K token）
    model = settings.OPENAI_MODEL
    input_budget = max(settings.MAX_CONTEXT_TOKENS - 1000, 0)
    total_tokens = count_messages_tokens(messages, model=model)
    if total_tokens > input_budget:
        logger.warning(
            f"消息总 token {total_tokens} 超过输入预算 {input_budget}，"
            "将截断历史消息和上下文"
        )
        messages = truncate_messages_to_budget(messages, input_budget, model=model)

    return messages
