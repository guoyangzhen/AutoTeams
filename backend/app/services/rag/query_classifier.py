"""查询分类器（判断简单/复杂查询，自动路由）。

重构说明（P1-1）：
之前所有查询都走相同的单次检索路径，简单问题浪费算力，复杂问题检索不足。
现新增查询分类器，将查询分为 simple / complex 两类：

- simple: 事实型查询，如"公司的地址是什么"→ 走传统 RAG（单次检索+重排序）
- complex: 分析型查询，如"比较A方案和B方案的优劣"→ 走 Agentic RAG（多轮检索+推理）

设计：
- 基于 LLM 判断（精确）
- 基于规则快速判断（低延迟 fallback）
- 优雅降级：LLM 不可用时走规则判断
"""
import json
import logging
from typing import Literal

import httpx

from app.services.llm_service import llm_service, ModelTier
from app.utils.metrics import errors_total

logger = logging.getLogger(__name__)

QueryType = Literal["simple", "complex"]

CLASSIFY_PROMPT = """请判断以下用户查询的类型：

查询：{query}

分类规则：
- simple: 事实型/定义型查询，通常可以通过单个检索结果回答。如"公司地址"、"产品名称"、"定义是什么"
- complex: 分析型/比较型/推理型查询，需要综合多个信息源或深度推理。如"比较"、"分析"、"为什么"、"如何实现"、"优缺点"

只返回 "simple" 或 "complex"，不要返回其他内容。"""

# 规则判断的关键词
_COMPLEX_KEYWORDS = [
    "比较", "对比", "分析", "为什么", "如何", "优缺点", "利弊",
    "总结", "归纳", "推理", "推导", "评估", "建议", "方案",
    "区别", "异同", "关联", "关系", "影响",
    "compare", "analyze", "why", "how", "summarize", "evaluate",
]


class QueryClassifier:
    """查询分类器。"""

    async def classify(self, query: str) -> QueryType:
        """判断查询类型。

        先用 LLM 精确判断，LLM 不可用时降级为规则判断。
        """
        # 短查询（<10字符）大概率是简单查询
        if len(query.strip()) < 10:
            return "simple"

        try:
            return await self._llm_classify(query)
        except (httpx.HTTPError, ValueError, RuntimeError, json.JSONDecodeError) as e:
            logger.warning(f"LLM 分类失败，降级为规则判断: {e}", exc_info=True)
            return self._rule_classify(query)
        except Exception as e:
            errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
            logger.error(f"LLM 分类失败（未预期错误），降级为规则判断: {e}", exc_info=True)
            return self._rule_classify(query)

    async def _llm_classify(self, query: str) -> QueryType:
        """使用 LLM 判断查询类型。"""
        prompt = CLASSIFY_PROMPT.format(query=query)
        # P1-3: 查询分类是轻量任务，使用廉价模型（如 gpt-3.5-turbo）
        response = await llm_service.chat(
            [{"role": "user", "content": prompt}],
            tier=ModelTier.CHEAP,
        )
        result = response.strip().lower()
        if "complex" in result:
            return "complex"
        return "simple"

    def _rule_classify(self, query: str) -> QueryType:
        """基于规则的快速分类（fallback）。"""
        query_lower = query.lower()
        for keyword in _COMPLEX_KEYWORDS:
            if keyword in query_lower:
                return "complex"
        return "simple"


# 全局单例
query_classifier = QueryClassifier()
