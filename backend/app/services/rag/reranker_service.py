"""重排序服务（远程 reranker API + bge-reranker + LLM fallback + 启发式降级）。

重排序优先级（按配置依次选择）：
1. **远程 reranker API（首选）**：配置 RERANKER_MODEL + RERANKER_API_KEY 时调用
   OpenRouter 等 /rerank 接口完成重排序，支持 _1/_2/_3 多 Key 轮换。
   云服务器不内置本地模型（构建时 SKIP_MODEL_DOWNLOAD=true），此路径为推荐方式。
2. **bge-reranker（本地，可选）**：使用 BAAI/bge-reranker-base 本地模型
   （通过 sentence-transformers 的 CrossEncoder 加载），低成本高精度，无 API 调用开销
3. **LLM 重排序（fallback）**：当 bge-reranker 模型加载失败（如测试环境无网络/
   无模型文件）时，自动降级到原有 LLM 重排序逻辑
4. **启发式重排序（最终降级）**：当 LLM 也不可用时，基于字符重叠度快速排序

设计要点：
- 模块级懒加载：首次调用时加载 bge-reranker 模型实例，避免拖慢启动和测试
- 加载失败缓存：模型加载失败后标记不可用，后续直接走 LLM fallback，不重复尝试
- 包缺失优雅降级：sentence-transformers 未安装时 import 不报错，直接走 LLM fallback
- 接口签名不变：输入 docs+query，输出重排序后的 docs，rag_engine 无需改动调用方式
"""
import asyncio
import json
import logging
from typing import Optional

import httpx

from app.config import settings
from app.services.llm_service import llm_service, ModelTier
from app.services.prompt_security import wrap_untrusted, safe_json_array_extract, validate_indices
from app.utils.metrics import errors_total

logger = logging.getLogger(__name__)

# ============================================================================
# bge-reranker 模型懒加载（模块级单例）
# ============================================================================
# 避免在 import 时就加载模型，拖慢启动和测试
# 首次调用 _get_bge_reranker() 时加载；加载失败后标记 _bge_reranker_load_attempted=True，
# 后续直接返回 None，避免重复尝试下载/加载模型
_bge_reranker = None
_bge_reranker_load_attempted = False
_BGE_RERANKER_MODEL = "BAAI/bge-reranker-base"

# 尝试导入 sentence-transformers；包未安装时优雅降级
try:
    from sentence_transformers import CrossEncoder  # type: ignore
    _SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    _SENTENCE_TRANSFORMERS_AVAILABLE = False
    CrossEncoder = None  # type: ignore[assignment,misc]


def _get_bge_reranker():
    """懒加载 bge-reranker 模型实例。

    首次调用时加载模型；加载失败（无网络/无模型文件/包未安装）后标记不可用，
    后续直接返回 None，避免重复尝试。

    Returns:
        CrossEncoder 实例，或 None（不可用时）
    """
    global _bge_reranker, _bge_reranker_load_attempted
    if _bge_reranker_load_attempted:
        return _bge_reranker
    _bge_reranker_load_attempted = True
    if not _SENTENCE_TRANSFORMERS_AVAILABLE:
        logger.warning(
            "sentence-transformers 未安装，bge-reranker 不可用，将使用 LLM 重排序 fallback"
        )
        return None
    try:
        _bge_reranker = CrossEncoder(_BGE_RERANKER_MODEL)
        logger.info(f"bge-reranker 模型加载成功: {_BGE_RERANKER_MODEL}")
    except Exception as e:
        # 常见失败原因：无网络下载模型、模型缓存损坏、内存不足等
        logger.warning(
            f"bge-reranker 模型加载失败，将使用 LLM 重排序 fallback: {e}",
            exc_info=True,
        )
        _bge_reranker = None
    return _bge_reranker


def _reset_bge_reranker_state() -> None:
    """重置 bge-reranker 加载状态（仅供测试使用）。

    测试场景下需要模拟"首次加载"或"模型不可用"时调用。
    """
    global _bge_reranker, _bge_reranker_load_attempted
    _bge_reranker = None
    _bge_reranker_load_attempted = False


def _reranker_api_keys() -> list[str]:
    """收集可用的远程 reranker API key 列表（主 key + _1/_2/_3），按序轮换。

    Returns:
        去重后的 key 列表；无任何配置时返回空列表。
    """
    keys: list[str] = []
    for name in (
        "RERANKER_API_KEY",
        "RERANKER_API_KEY_1",
        "RERANKER_API_KEY_2",
        "RERANKER_API_KEY_3",
    ):
        v = (getattr(settings, name, "") or "").strip()
        if v and v not in keys:
            keys.append(v)
    return keys


def get_reranker_status() -> dict:
    """返回 bge-reranker 当前状态，便于运维诊断与前端展示。

    Returns:
        dict with keys:
        - active: bool, 模型是否已加载可用
        - model_name: str, 模型名
        - load_attempted: bool, 是否已尝试加载
        - package_available: bool, sentence-transformers 是否安装
        - ready_marker: Optional[str], 构建期 .reranker_ready 标记内容（若存在）
        - failed_marker: Optional[str], 构建期 .reranker_download_failed 标记内容（若存在）
    """
    import os
    model_dir = os.environ.get("RERANKER_MODEL_DIR", "/app/models")
    ready_path = os.path.join(model_dir, ".reranker_ready")
    failed_path = os.path.join(model_dir, ".reranker_download_failed")

    ready_content = None
    failed_content = None
    try:
        if os.path.exists(ready_path):
            with open(ready_path, "r", encoding="utf-8") as f:
                ready_content = f.read().strip()
    except OSError:
        pass
    try:
        if os.path.exists(failed_path):
            with open(failed_path, "r", encoding="utf-8") as f:
                failed_content = f.read().strip()
    except OSError:
        pass

    return {
        "active": _bge_reranker is not None,
        "model_name": _BGE_RERANKER_MODEL,
        "load_attempted": _bge_reranker_load_attempted,
        "package_available": _SENTENCE_TRANSFORMERS_AVAILABLE,
        "ready_marker": ready_content,
        "failed_marker": failed_content,
    }


# ============================================================================
# LLM 重排序 prompt（fallback 路径使用）
# ============================================================================
RERANK_PROMPT = """你是一个信息检索专家。请根据用户问题，对以下检索结果按相关性从高到低排序。

用户问题：{query}

检索结果：
{documents}

请只返回一个 JSON 数组，包含排序后的结果编号（从0开始），如 [2, 0, 1, 3]。
只返回 JSON 数组，不要返回其他内容。"""


class RerankerService:
    """重排序服务：bge-reranker → LLM → 启发式 三级降级。"""

    async def rerank(
        self,
        query: str,
        documents: list[dict],
        top_k: Optional[int] = None,
    ) -> list[dict]:
        """对检索结果进行重排序。

        降级顺序：
        1. RERANK_ENABLED=false → 直接走启发式
        2. bge-reranker 可用 → 使用本地模型重排序
        3. bge-reranker 不可用 → LLM 重排序
        4. LLM 重排序失败 → 启发式重排序

        Args:
            query: 用户查询
            documents: 检索结果列表（每个 dict 需有 content 字段）
            top_k: 只返回前 K 个结果，None 表示返回全部

        Returns:
            重排序后的文档列表
        """
        if not documents:
            return []

        if len(documents) == 1:
            return documents

        # P1-RAG: 全局开关关闭时，直接走启发式重排序，避免任何模型调用
        if not settings.RERANK_ENABLED:
            logger.debug("RERANK_ENABLED=false，使用启发式重排序")
            return self._heuristic_rerank(query, documents, top_k)

        # P1-RAG: 优先使用远程 reranker API（OpenRouter）。云服务器不内置本地
        # bge-reranker 模型（构建时 SKIP_MODEL_DOWNLOAD=true），配置 RERANKER_MODEL
        # 后重排序走远程 API，避免消耗 LLM token。多 key 全部失败时降级到本地 bge / LLM。
        if settings.RERANKER_MODEL:
            api_reranked = await self._api_rerank(query, documents)
            if api_reranked is not None:
                if top_k:
                    api_reranked = api_reranked[:top_k]
                return api_reranked

        # D2-S11: 首选 bge-reranker（本地模型，无 API 开销）
        bge_model = _get_bge_reranker()
        if bge_model is not None:
            try:
                # 技术审计 R2 C1: CrossEncoder.predict 是 CPU 密集同步调用，
                # 必须用 to_thread 包装，否则阻塞整个事件循环导致并发请求雪崩。
                reranked = await asyncio.to_thread(
                    self._bge_rerank, query, documents, bge_model
                )
                if top_k:
                    reranked = reranked[:top_k]
                return reranked
            except (RuntimeError, ValueError, TypeError, OSError) as e:
                logger.warning(
                    f"bge-reranker 重排序失败，降级到 LLM 重排序: {e}",
                    exc_info=True,
                )
                # 继续走 LLM fallback
            except Exception as e:
                errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
                logger.error(
                    f"bge-reranker 重排序失败（未预期错误），降级到 LLM 重排序: {e}",
                    exc_info=True,
                )
                # 继续走 LLM fallback

        # D2-S11: bge-reranker 不可用或失败时，降级到 LLM 重排序
        try:
            reranked = await self._llm_rerank(query, documents)
            if top_k:
                reranked = reranked[:top_k]
            return reranked
        except (httpx.HTTPError, ValueError, RuntimeError, json.JSONDecodeError, IndexError) as e:
            logger.warning(f"LLM 重排序失败，降级到启发式重排序: {e}", exc_info=True)
            return self._heuristic_rerank(query, documents, top_k)
        except Exception as e:
            errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
            logger.error(f"LLM 重排序失败（未预期错误），降级到启发式重排序: {e}", exc_info=True)
            return self._heuristic_rerank(query, documents, top_k)

    def _bge_rerank(
        self,
        query: str,
        documents: list[dict],
        model,
    ) -> list[dict]:
        """使用 bge-reranker (CrossEncoder) 对文档进行重排序。

        CrossEncoder 对 (query, doc) 对打分，分数越高越相关。
        本地推理，无 API 调用开销。

        Args:
            query: 用户查询
            documents: 检索结果列表
            model: CrossEncoder 实例
        """
        # 构建 (query, content) 对，content 截断避免超长
        pairs = [
            (query, str(doc.get("content", ""))[:512])
            for doc in documents
        ]
        # predict 返回每个 pair 的相关性分数（越高越相关）
        scores = model.predict(pairs)

        # 按分数降序排序（稳定排序：分数相同时保留原始顺序）
        indexed = list(enumerate(documents))
        indexed.sort(
            key=lambda pair: scores[pair[0]],
            reverse=True,
        )
        return [doc for _, doc in indexed]

    async def _api_rerank(
        self,
        query: str,
        documents: list[dict],
    ) -> Optional[list[dict]]:
        """使用远程 reranker API（如 OpenRouter）对文档进行重排序。

        云服务器不内置本地 bge-reranker 模型，配置 RERANKER_MODEL + RERANKER_API_KEY
        后优先走远程 /rerank 接口，避免消耗 LLM token。
        接口格式参考 https://openrouter.ai/docs/api-reference/rerank：
        POST {RERANKER_API_BASE}/rerank
        Body: {"model": RERANKER_MODEL, "query": ..., "documents": [...], "top_n": n}
        Response: {"results": [{"index", "relevance_score"}, ...]}

        多 Key 轮换：单把 key 请求失败或限流（429）时自动尝试下一把 key，
        全部失败则返回 None，由调用方降级到本地 bge / LLM / 启发式重排序。

        Args:
            query: 用户查询
            documents: 检索结果列表（每个 dict 需有 content 字段）

        Returns:
            重排序后的文档列表；未配置或全部 key 失败时返回 None
        """
        if not settings.RERANKER_MODEL:
            return None
        api_keys = _reranker_api_keys()
        if not api_keys:
            return None

        api_base = (settings.RERANKER_API_BASE or "https://openrouter.ai/api/v1").rstrip("/")
        url = f"{api_base}/rerank"
        docs_text = [str(d.get("content", ""))[:512] for d in documents]
        payload = {
            "model": settings.RERANKER_MODEL,
            "query": query,
            "documents": docs_text,
            "top_n": len(docs_text),
        }

        for key in api_keys:
            headers = {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            }
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
                    resp = await client.post(url, json=payload, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                results = data.get("results", [])
                if not results:
                    logger.warning("Reranker API 未返回任何结果，尝试下一把 key")
                    continue
                # 按相关性分数降序，重排原始文档顺序
                ordered: list[dict] = []
                for r in sorted(
                    results, key=lambda r: r.get("relevance_score", 0.0), reverse=True
                ):
                    idx = r.get("index")
                    if isinstance(idx, int) and 0 <= idx < len(documents):
                        ordered.append(documents[idx])
                if not ordered:
                    logger.warning("Reranker API 返回的 index 均无效，尝试下一把 key")
                    continue
                logger.info(
                    f"Reranker API 调用成功: model={settings.RERANKER_MODEL}, "
                    f"docs={len(documents)}"
                )
                return ordered
            except httpx.HTTPStatusError as e:
                # 429 限流等：记录后尝试下一把 key
                logger.warning(
                    f"Reranker API key 调用失败 (HTTP {e.response.status_code})，"
                    "尝试下一把 key"
                )
                continue
            except Exception as e:
                errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
                logger.error(f"Reranker API key 调用失败，尝试下一把 key: {e}", exc_info=True)
                continue

        logger.error("所有 Reranker API key 均调用失败")
        return None

    def _heuristic_rerank(
        self,
        query: str,
        documents: list[dict],
        top_k: Optional[int] = None,
    ) -> list[dict]:
        """启发式重排序（无模型）：基于查询与文档的字符/关键词重叠度打分。

        适用于：
        - RERANK_ENABLED=false
        - bge-reranker 和 LLM 均不可用时的最终降级
        - 低成本快速重排序
        """
        query_chars = set(query.lower())
        query_len = max(1, len(query_chars))

        def _score(doc: dict) -> float:
            content = str(doc.get("content", "")).lower()
            content_chars = set(content)
            overlap = len(query_chars & content_chars)
            score = overlap / query_len
            # 完全包含查询串时给予显著加分
            if query.lower() in content:
                score += 5.0
            return score

        # sorted 稳定排序：分数相同时保留向量检索的原始顺序
        ranked = sorted(documents, key=_score, reverse=True)
        if top_k:
            ranked = ranked[:top_k]
        return ranked

    async def _llm_rerank(
        self, query: str, documents: list[dict]
    ) -> list[dict]:
        """使用 LLM 对文档进行重排序（bge-reranker 不可用时的 fallback）。"""
        # 构建文档列表文本（每个文档截断到 200 字符避免超长）
        doc_texts = []
        for i, doc in enumerate(documents):
            content = doc.get("content", "")[:200]
            doc_texts.append(f"[{i}] {content}")
        documents_text = "\n\n".join(doc_texts)

        # P0-08: 用 wrap_untrusted 包裹 query 和 documents，防止 prompt injection
        wrapped_query = wrap_untrusted(query, "用户问题")
        wrapped_docs = wrap_untrusted(documents_text, "检索结果")

        prompt = RERANK_PROMPT.format(query=wrapped_query, documents=wrapped_docs)

        # P1-3: 重排序是轻量判断任务，使用廉价模型
        response = await llm_service.chat(
            [{"role": "user", "content": prompt}],
            tier=ModelTier.CHEAP,
        )

        # P0-08: 使用 safe_json_array_extract 替代脆弱的 find/rfind，并用 validate_indices 校验去重
        order = safe_json_array_extract(response)
        if order is not None and validate_indices(order, len(documents)):
            return [documents[i] for i in order]

        # 解析失败，返回原始顺序
        return documents


# 全局单例
reranker_service = RerankerService()
