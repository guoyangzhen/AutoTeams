"""嵌入业务子包（PRD §4.7）。

business_embedder：业务流程/数据/规则嵌入，将企业业务上下文注入 AI 员工。
"""
from app.services.embedding.business_embedder import BusinessEmbedder

__all__ = ["BusinessEmbedder"]
