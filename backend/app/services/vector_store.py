import asyncio
import logging
import re
from typing import Optional

import chromadb
import chromadb.errors
from chromadb.config import Settings as ChromaSettings

import httpx

from app.config import settings
from app.utils.metrics import errors_total

logger = logging.getLogger(__name__)

_client = None
_client_lock = asyncio.Lock()

# P1-RAG: 中文 embedding 模型单例（懒加载）
_embedding_model = None
_embedding_model_lock = asyncio.Lock()

# T17: collection_name 格式校验
# - 旧格式: agent_<uuid>（向后兼容，已有数据）
# - 新格式: ent_<enterprise_uuid>_agent_<agent_uuid>（企业级隔离前缀）
_COLLECTION_NAME_RE = re.compile(
    r"^(?:ent_[a-zA-Z0-9_-]+_)?agent_[a-zA-Z0-9_-]+$"
)


def build_collection_name(enterprise_id: str, agent_id: str) -> str:
    """T17: 构造带企业前缀的 collection_name，实现企业级向量数据隔离。

    新格式: ent_{enterprise_id}_agent_{agent_id}
    旧格式: agent_{agent_id}（无企业前缀，向后兼容）

    Args:
        enterprise_id: 企业 ID（UUID 格式）
        agent_id: Agent ID（UUID 格式）

    Returns:
        带企业前缀的 collection_name
    """
    return f"ent_{enterprise_id}_agent_{agent_id}"


async def _get_embedding_model():
    """懒加载 sentence-transformers embedding 模型。

    P1-RAG:
    - 默认使用 BAAI/bge-large-zh-v1.5，中文语义检索效果优于 ChromaDB 默认英文模型
    - 模型加载是 CPU/IO 密集型，放在线程中执行避免阻塞事件循环
    - 加载失败时返回 None，回退到 ChromaDB 默认 embedding 行为
    """
    global _embedding_model
    if _embedding_model is not None:
        return _embedding_model

    if not settings.EMBEDDING_MODEL:
        logger.info("EMBEDDING_MODEL 未配置，使用 ChromaDB 默认 embedding 模型")
        return None

    async with _embedding_model_lock:
        if _embedding_model is not None:
            return _embedding_model
        try:
            from sentence_transformers import SentenceTransformer

            model_name = settings.EMBEDDING_MODEL.strip()
            logger.info(f"正在加载 embedding 模型: {model_name} ...")

            def _load():
                return SentenceTransformer(model_name)

            _embedding_model = await asyncio.to_thread(_load)
            logger.info(f"Embedding 模型 {model_name} 加载完成")
        except Exception as e:
            errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
            logger.error(f"加载 embedding 模型失败: {e}", exc_info=True)
            _embedding_model = None

    return _embedding_model


def _embedding_api_keys() -> list[str]:
    """收集可用的 embedding API key 列表（主 key + _1/_2/_3），按序轮换。

    Returns:
        去重后的 key 列表；无任何配置时返回空列表。
    """
    keys: list[str] = []
    for name in (
        "EMBEDDING_API_KEY",
        "EMBEDDING_API_KEY_1",
        "EMBEDDING_API_KEY_2",
        "EMBEDDING_API_KEY_3",
    ):
        v = (getattr(settings, name, "") or "").strip()
        if v and v not in keys:
            keys.append(v)
    return keys


async def _encode_via_api(texts: list[str]) -> Optional[list[list[float]]]:
    """调用远程 embedding API（如 OpenRouter）生成向量。

    当配置了 EMBEDDING_API_KEY(_1/_2/_3) 时优先使用远程接口，无需本地下载模型。
    接口格式参考 https://openrouter.ai/docs/api_reference/embeddings：
    POST {EMBEDDING_API_BASE}/embeddings
    Body: {"model": EMBEDDING_MODEL, "input": [...], "encoding_format": "float"}

    多 Key 轮换：单把 key 请求失败或限流（429）时自动尝试下一把 key，
    直到全部失败才返回 None（由调用方降级）。

    Args:
        texts: 待编码文本列表

    Returns:
        每条文本对应的 embedding 向量列表；未配置或全部 key 均失败时返回 None
    """
    if not settings.EMBEDDING_MODEL:
        return None

    api_keys = _embedding_api_keys()
    if not api_keys:
        return None

    api_base = (settings.EMBEDDING_API_BASE or "https://openrouter.ai/api/v1").rstrip("/")
    url = f"{api_base}/embeddings"
    max_chars = settings.MAX_CHUNK_INPUT_CHARS or 5_000_000
    clipped = [t[:max_chars] for t in texts]
    payload = {
        "model": settings.EMBEDDING_MODEL,
        "input": clipped,
        "encoding_format": "float",
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
            embeddings = [item["embedding"] for item in data.get("data", [])]
            if not embeddings or len(embeddings) != len(texts):
                logger.warning(
                    f"Embedding API 返回数量不符: 期望 {len(texts)}，实际 {len(embeddings)}，"
                    "尝试下一把 key"
                )
                continue
            logger.info(
                f"Embedding API 调用成功: model={settings.EMBEDDING_MODEL}, "
                f"dim={len(embeddings[0])}, count={len(embeddings)}"
            )
            return embeddings
        except httpx.HTTPStatusError as e:
            # 429 限流等：记录后尝试下一把 key
            logger.warning(
                f"Embedding API key 调用失败 (HTTP {e.response.status_code})，尝试下一把 key: {e}"
            )
            continue
        except Exception as e:
            errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
            logger.error(f"Embedding API key 调用失败，尝试下一把 key: {e}", exc_info=True)
            continue

    logger.error("所有 Embedding API key 均调用失败")
    return None


async def _encode_texts(texts: list[str]) -> Optional[list[list[float]]]:
    """对文本列表进行 embedding，失败时返回 None 以回退到默认模型。

    Args:
        texts: 待编码文本列表

    Returns:
        每条文本对应的 embedding 向量列表；失败或模型未配置时返回 None
    """
    if not texts:
        return []

    # 优先使用远程 embedding API（OpenRouter 等），避免本地下载模型
    api_embeddings = await _encode_via_api(texts)
    if api_embeddings is not None:
        return api_embeddings

    model = await _get_embedding_model()
    if model is None:
        return None

    def _encode():
        # 截断单条文本避免内存/时间爆炸
        max_chars = settings.MAX_CHUNK_INPUT_CHARS or 5_000_000
        clipped = [t[:max_chars] for t in texts]
        return model.encode(
            clipped,
            convert_to_list=True,
            show_progress_bar=False,
            normalize_embeddings=True,
        )

    try:
        return await asyncio.to_thread(_encode)
    except Exception as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"Embedding 编码失败: {e}", exc_info=True)
        return None


class ChromaDBConnectionError(RuntimeError):
    """ChromaDB 连接失败（P0-06: 不再静默降级）。"""


def _build_chroma_client():
    """构造 ChromaDB 客户端。

    两种模式：
    - HttpClient（CHROMA_HOST 非空）：连接独立 ChromaDB 服务，适合 Docker Compose / 集群
    - PersistentClient（CHROMA_HOST 为空）：嵌入式模式，数据持久化到 CHROMA_PERSIST_DIR，适合单容器部署
    """
    chroma_settings = ChromaSettings(anonymized_telemetry=False)

    # 部署简化: CHROMA_HOST 为空时使用嵌入式模式
    if not settings.CHROMA_HOST:
        import os
        os.makedirs(settings.CHROMA_PERSIST_DIR, exist_ok=True)
        client = chromadb.PersistentClient(
            path=settings.CHROMA_PERSIST_DIR,
            settings=chroma_settings,
        )
        logger.info(f"ChromaDB 嵌入式模式，持久化目录: {settings.CHROMA_PERSIST_DIR}")
        return client

    # HttpClient 模式
    if settings.CHROMA_AUTH_TOKEN:
        chroma_settings = ChromaSettings(
            anonymized_telemetry=False,
            chroma_client_auth_provider="chromadb.auth.token_authn.TokenAuthClientProvider",
            chroma_client_auth_credentials=settings.CHROMA_AUTH_TOKEN,
        )
    return chromadb.HttpClient(
        host=settings.CHROMA_HOST,
        port=settings.CHROMA_PORT,
        settings=chroma_settings,
    )


async def get_chroma_client():
    """获取 ChromaDB 客户端单例。

    P0-06 修复：
    1. 连接失败时抛 ChromaDBConnectionError 而非静默降级到内存存储
    2. 首次初始化加 asyncio.Lock 防止并发创建多个 client
    """
    global _client
    if _client is not None:
        return _client

    async with _client_lock:
        # double-check，防止锁等待期间其他协程已创建
        if _client is not None:
            return _client
        try:
            client = _build_chroma_client()
            client.heartbeat()
            _client = client
            logger.info(
                f"已连接到 ChromaDB 服务 {settings.CHROMA_HOST}:{settings.CHROMA_PORT}"
                f"（认证：{'启用' if settings.CHROMA_AUTH_TOKEN else '未启用'}）"
            )
        except chromadb.errors.ChromaError as e:
            # P0-06: 不再静默降级到内存存储
            logger.error(f"无法连接到 ChromaDB 服务: {e}", exc_info=True)
            raise ChromaDBConnectionError(
                f"无法连接到 ChromaDB 服务: {e}。"
                "请检查 CHROMA_HOST/CHROMA_PORT 配置以及 ChromaDB 服务是否运行。"
            ) from e
        except (httpx.HTTPError, OSError, RuntimeError, ValueError, TypeError) as e:
            errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
            logger.error(f"无法连接到 ChromaDB 服务（未预期错误）: {e}", exc_info=True)
            raise ChromaDBConnectionError(
                f"无法连接到 ChromaDB 服务: {e}。"
                "请检查 CHROMA_HOST/CHROMA_PORT 配置以及 ChromaDB 服务是否运行。"
            ) from e
    return _client


async def _validate_collection_name(collection_name: str) -> None:
    """校验 collection_name 格式，防止越权访问其他集合。

    T17: 支持两种格式：
    - 旧格式 agent_<uuid>（向后兼容）
    - 新格式 ent_<enterprise_uuid>_agent_<agent_uuid>（企业级隔离）
    """
    if not collection_name or not _COLLECTION_NAME_RE.match(collection_name):
        raise ValueError(
            f"非法 collection_name: {collection_name}。"
            "必须形如 agent_<uuid> 或 ent_<enterprise_uuid>_agent_<agent_uuid>"
        )


class VectorStoreService:
    def __init__(self, collection_name: str, client):
        """初始化向量存储服务。

        Args:
            collection_name: 集合名，必须形如 agent_<uuid>
            client: 已构造的 chromadb client。生产代码请使用 `await VectorStoreService.create(...)`
        """
        # P0-06: 校验 collection_name 格式（T17: 支持企业前缀格式）
        if not collection_name or not _COLLECTION_NAME_RE.match(collection_name):
            raise ValueError(
                f"非法 collection_name: {collection_name}。"
                "必须形如 agent_<uuid> 或 ent_<enterprise_uuid>_agent_<agent_uuid>"
            )
        self.client = client
        self.collection_name = collection_name
        self.collection = None

    async def initialize(self) -> "VectorStoreService":
        """P0-PERF: 异步初始化 collection，避免在事件循环中同步调用 ChromaDB。"""
        self.collection = await asyncio.to_thread(
            self.client.get_or_create_collection,
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        return self

    @classmethod
    async def create(cls, collection_name: str, client=None) -> "VectorStoreService":
        """异步工厂方法，避免在事件循环中同步构造 ChromaDB client。

        BE-PER-04: VectorStoreService 同步 __init__ 会阻塞事件循环。
        生产代码统一使用 `await VectorStoreService.create(...)` 获取实例。

        Args:
            collection_name: 集合名，必须形如 agent_<uuid> 或 ent_<eid>_agent_<aid>
            client: 可选的已构造 chromadb client（用于测试注入）。
                    未提供时使用 get_chroma_client() 异步获取单例。
        """
        if client is None:
            client = await get_chroma_client()
        instance = cls(collection_name, client=client)
        await instance.initialize()
        return instance

    @classmethod
    async def create_prefixed(
        cls,
        enterprise_id: str,
        agent_id: str,
        client=None,
    ) -> "VectorStoreService":
        """T17: 使用企业前缀格式创建向量存储服务。

        新代码应优先使用此方法，确保 collection_name 包含 enterprise_id 前缀，
        实现企业级向量数据隔离。

        Args:
            enterprise_id: 企业 ID（UUID 格式）
            agent_id: Agent ID（UUID 格式）
            client: 可选的已构造 chromadb client（用于测试注入）。

        Returns:
            VectorStoreService 实例，collection_name 为 ent_{eid}_agent_{aid}
        """
        collection_name = build_collection_name(enterprise_id, agent_id)
        return await cls.create(collection_name, client=client)

    async def add_documents(
        self,
        documents: list[str],
        metadatas: list[dict],
        ids: list[str],
    ) -> None:
        if not documents:
            return

        # P1-RAG: 使用配置的中文 embedding 模型生成向量；失败则回退到 ChromaDB 默认模型
        embeddings = await _encode_texts(documents)

        def _add():
            batch_size = 100
            for i in range(0, len(documents), batch_size):
                kwargs = {
                    "documents": documents[i:i + batch_size],
                    "metadatas": metadatas[i:i + batch_size],
                    "ids": ids[i:i + batch_size],
                }
                if embeddings:
                    kwargs["embeddings"] = embeddings[i:i + batch_size]
                self.collection.add(**kwargs)

        await asyncio.to_thread(_add)

    async def search(
        self,
        query: str,
        n_results: int = 5,
        where: Optional[dict] = None,
    ) -> list[dict]:
        # P1-RAG: 使用配置的中文 embedding 模型编码查询；失败则回退到 ChromaDB 默认模型
        query_embeddings = await _encode_texts([query]) if query else None

        def _search():
            params = {
                "n_results": n_results,
            }
            if query_embeddings:
                params["query_embeddings"] = query_embeddings
            else:
                params["query_texts"] = [query]
            if where:
                params["where"] = where

            results = self.collection.query(**params)

            documents = []
            if results["documents"] and results["documents"][0]:
                for i in range(len(results["documents"][0])):
                    doc = {
                        "content": results["documents"][0][i],
                        "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                        "distance": results["distances"][0][i] if results["distances"] else None,
                        "id": results["ids"][0][i],
                    }
                    documents.append(doc)
            return documents

        try:
            return await asyncio.to_thread(_search)
        except chromadb.errors.ChromaError as e:
            logger.error(f"向量搜索失败: {e}", exc_info=True)
            return []
        except ValueError as e:
            logger.warning(f"向量搜索参数错误: {e}", exc_info=True)
            return []
        except (OSError, RuntimeError, TypeError, KeyError) as e:
            errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
            logger.error(f"向量搜索失败（未预期错误）: {e}", exc_info=True)
            raise RuntimeError(f"向量搜索失败: {e}") from e

    async def delete(self, ids: list[str]) -> None:
        if ids:
            def _delete():
                self.collection.delete(ids=ids)

            try:
                await asyncio.to_thread(_delete)
            except chromadb.errors.ChromaError as e:
                logger.error(f"删除向量失败: {e}", exc_info=True)
            except (OSError, RuntimeError, TypeError) as e:
                errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
                logger.error(f"删除向量失败（未预期错误）: {e}", exc_info=True)
                raise RuntimeError(f"删除向量失败: {e}") from e

    async def delete_by_metadata(self, where: dict) -> int:
        """按 metadata 过滤删除文档，返回删除的文档数。"""
        # P0-06: 记录操作审计
        def _delete():
            result = self.collection.get(where=where, include=[])
            ids = result.get("ids", []) if result else []
            if ids:
                self.collection.delete(ids=ids)
                logger.info(
                    f"按 metadata 删除 {len(ids)} 个向量 from {self.collection_name}"
                )
            return len(ids)

        try:
            return await asyncio.to_thread(_delete)
        except chromadb.errors.ChromaError as e:
            logger.error(f"按 metadata 删除失败: {e}", exc_info=True)
            return 0
        except (OSError, RuntimeError, TypeError) as e:
            errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
            logger.error(f"按 metadata 删除失败（未预期错误）: {e}", exc_info=True)
            raise RuntimeError(f"按 metadata 删除失败: {e}") from e

    async def delete_collection(self) -> None:
        def _delete():
            self.client.delete_collection(self.collection_name)

        try:
            await asyncio.to_thread(_delete)
        except chromadb.errors.ChromaError as e:
            logger.error(f"删除集合失败: {e}", exc_info=True)
        except (OSError, RuntimeError, TypeError) as e:
            errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
            logger.error(f"删除集合失败（未预期错误）: {e}", exc_info=True)
            raise RuntimeError(f"删除集合失败: {e}") from e

    async def count(self) -> int:
        def _count():
            return self.collection.count()

        try:
            return await asyncio.to_thread(_count)
        except chromadb.errors.ChromaError as e:
            logger.error(f"统计向量数失败: {e}", exc_info=True)
            raise RuntimeError(f"统计向量数失败: {e}") from e
        except (OSError, RuntimeError, TypeError) as e:
            errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
            logger.error(f"统计向量数失败（未预期错误）: {e}", exc_info=True)
            raise RuntimeError(f"统计向量数失败: {e}") from e
