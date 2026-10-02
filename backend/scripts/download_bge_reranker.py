"""D2-S11: 在 Docker 构建阶段预下载 bge-reranker 模型。

用途：避免生产环境（Railway/Docker）首次请求时从 HuggingFace 下载模型，
导致请求超时或容器启动失败。构建镜像时把模型权重 bake 进 /app/models，
运行时使用同一路径，实现零下载冷启动。

用法：
    python scripts/download_bge_reranker.py

环境变量：
    RERANKER_MODEL_DIR: 模型缓存目录，默认 /app/models
    RERANKER_MODEL_NAME: 模型名，默认 BAAI/bge-reranker-base

可观测性：
    - 成功时写标记文件 <model_dir>/.reranker_ready（含模型名与时间戳）
    - 失败时写标记文件 <model_dir>/.reranker_download_failed（含错误原因与时间戳）
    - 运行时 reranker_service.py 检查这两个标记，便于诊断"为何降级到 LLM 重排序"
"""
import os
import sys
from datetime import datetime, timezone


def _write_marker(path: str, content: str) -> None:
    """写标记文件，便于运维诊断。失败不阻塞主流程。"""
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError:
        pass


def main() -> int:
    model_dir = os.environ.get("RERANKER_MODEL_DIR", "/app/models")
    model_name = os.environ.get("RERANKER_MODEL_NAME", "BAAI/bge-reranker-base")

    os.makedirs(model_dir, exist_ok=True)
    # sentence-transformers 通过 SENTENCE_TRANSFORMERS_HOME 决定缓存根目录
    os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", model_dir)

    ready_marker = os.path.join(model_dir, ".reranker_ready")
    failed_marker = os.path.join(model_dir, ".reranker_download_failed")
    now = datetime.now(timezone.utc).isoformat()

    print(f"[reranker] 开始预下载模型: {model_name}")
    print(f"[reranker] 缓存目录: {model_dir}")

    try:
        from sentence_transformers import CrossEncoder
    except ImportError as e:
        print(f"[reranker] 警告: sentence-transformers 未安装，跳过预下载: {e}")
        _write_marker(
            failed_marker,
            f"sentence-transformers not installed at {now}\nerror: {e}\n",
        )
        return 0

    try:
        # 实例化即触发下载并缓存到 SENTENCE_TRANSFORMERS_HOME
        model = CrossEncoder(model_name)
        print(f"[reranker] 模型预下载成功: {model_name}")
        print(f"[reranker] 模型信息: {model}")
        # 写成功标记，清除失败标记
        _write_marker(ready_marker, f"model={model_name}\nready_at={now}\n")
        try:
            os.remove(failed_marker)
        except OSError:
            pass
    except Exception as e:
        print(f"[reranker] 错误: 模型预下载失败: {e}", file=sys.stderr)
        # 构建阶段失败时不阻塞镜像构建（运行时仍可回退到 LLM fallback）
        # 写失败标记便于运维诊断；继续尝试 embedding 下载（两者相互独立）
        _write_marker(
            failed_marker,
            f"model={model_name}\nfailed_at={now}\nerror: {type(e).__name__}: {e}\n",
        )

    # 运行影响审计 Fix3: 同步预下载 embedding 模型，避免生产环境首次 RAG 查询时
    # 从 HuggingFace 下载约 1.3GB（中国网络访问 HF 可能超时/失败）。
    # 失败不阻塞镜像构建（运行时 vector_store 会回退到 ChromaDB 默认英文模型）。
    embedding_model_name = os.environ.get(
        "EMBEDDING_MODEL_NAME", "BAAI/bge-large-zh-v1.5"
    )
    emb_ready_marker = os.path.join(model_dir, ".embedding_ready")
    emb_failed_marker = os.path.join(model_dir, ".embedding_download_failed")
    print(f"[embedding] 开始预下载模型: {embedding_model_name}")

    try:
        from sentence_transformers import SentenceTransformer  # noqa: F401
    except ImportError as e:
        print(f"[embedding] 警告: sentence-transformers 未安装，跳过预下载: {e}")
        _write_marker(
            emb_failed_marker,
            f"sentence-transformers not installed at {now}\nerror: {e}\n",
        )
        return 0

    try:
        emb_model = SentenceTransformer(embedding_model_name)
        print(f"[embedding] 模型预下载成功: {embedding_model_name}")
        _write_marker(
            emb_ready_marker, f"model={embedding_model_name}\nready_at={now}\n"
        )
        try:
            os.remove(emb_failed_marker)
        except OSError:
            pass
    except Exception as e:
        print(f"[embedding] 错误: 模型预下载失败: {e}", file=sys.stderr)
        _write_marker(
            emb_failed_marker,
            f"model={embedding_model_name}\nfailed_at={now}\n"
            f"error: {type(e).__name__}: {e}\n",
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
