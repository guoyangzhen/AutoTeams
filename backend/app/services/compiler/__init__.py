"""编译器子包。

对应 PRD §4.3 五级编译器：
- Information → Knowledge → Process → Capability → Runtime
- 每级含输入/输出/置信度
- completeness.py：完成度评估框架
"""
from app.services.compiler.base import (
    CompilerBase,
    CompilationContext,
    CompilationResult,
    Stage,
)
from app.services.compiler.information_compiler import InformationCompiler
from app.services.compiler.knowledge_compiler import KnowledgeCompiler
from app.services.compiler.process_compiler import ProcessCompiler
from app.services.compiler.capability_compiler import CapabilityCompiler
from app.services.compiler.runtime_compiler import RuntimeCompiler
from app.services.compiler.completeness import CompletenessCalculator

__all__ = [
    "CompilerBase",
    "CompilationContext",
    "CompilationResult",
    "Stage",
    "InformationCompiler",
    "KnowledgeCompiler",
    "ProcessCompiler",
    "CapabilityCompiler",
    "RuntimeCompiler",
    "CompletenessCalculator",
]
