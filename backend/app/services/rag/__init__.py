"""RAG 引擎模块。

重构说明（P1-1）：
之前只有简单的 ChromaDB 向量搜索（n_results=3），无查询分类、无重排序、无多轮检索。
现升级为 Agentic RAG 双通道架构：

1. 查询分类器：判断简单/复杂查询，自动路由
2. 传统 RAG：简单查询 → 单次检索 + 重排序（<500ms）
3. Agentic RAG：复杂查询 → ReAct 多轮检索 + Self-RAG 评估（1-3s）
4. 结构感知分块：替代固定500字符分块
5. bge-reranker 重排序：本地模型对检索结果进行二次排序（D2-S11）
   - bge-reranker 不可用时降级到 LLM 重排序
   - LLM 不可用时降级到启发式重排序

D2-S9 检索元数据：
- rag_engine.search 返回值包含 query_type/iterations/refined_queries/self_rag_score
- 这些字段由 agents.py 透传到 stream_chat SSE 流，供前端展示检索过程

设计原则：
- 抽象层 + 适配器模式：支持后续替换为 Milvus/bge-m3 等更强大的后端
- 优雅降级：bge-reranker / LLM 不可用时回退到简单检索
- 可观测：每步操作都有日志，便于调试
"""
from app.services.rag.rag_engine import rag_engine

__all__ = ["rag_engine"]
