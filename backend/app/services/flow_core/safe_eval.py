"""FlowCard 条件表达式安全求值器（AST 白名单）。

背景（P0 安全修复）：原实现使用 ``eval(expr, {"__builtins__": None}, slots)``，
该「沙箱」可被 ``().__class__.__mro__`` 反射链穿透，最终拿到 os/subprocess
实现任意代码执行。condition_expression 由用户在保存规程卡时任意提交，
属于不可信输入，必须用 AST 白名单严格限定可表达的语法面。

允许的表达能力（业务分支判定所需的最小集合）：
- 比较：== != < <= > >= in / not in
- 布尔组合：and or not
- 算术：+ - * / // % **（用于金额阈值等派生比较）
- 成员测试与下标：slots 变量名、常量、``slots["key"]`` / ``slots.key`` 形式不做支持
  （槽位直接以变量名暴露，避免属性/下标链带来的逃逸面）
- 括号分组

一切其他语法（函数调用、属性访问、下标、lambda、推导式、f-string、
星号表达式、await、赋值等）一律拒绝。
"""
from __future__ import annotations

import ast
import logging
import operator
from typing import Any, Mapping

logger = logging.getLogger(__name__)

# 二元运算符白名单
_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

# 一元运算符白名单（-x / +x / not x）
_UNARY_OPS = {
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
    ast.Not: operator.not_,
}

# 比较运算符白名单
_CMP_OPS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
    # is / is not 不提供：与 == 语义混淆且无业务价值
}

# 允许的比较链长度（a < b < c），业务上 1-2 层足够
_MAX_CMP_CHAIN = 3

# 表达式长度上限，防御病态超长输入
_MAX_EXPR_LEN = 512


class SafeEvalError(ValueError):
    """表达式不满足安全白名单时抛出。"""


UnsafeExpressionError = SafeEvalError

def validate_condition_expression(expr: str) -> str:
    """校验表达式是否满足白名单；通过则原样返回，否则抛出 SafeEvalError。

    供保存规程卡等写入路径调用，把不合法表达式挡在入库前。
    """
    if not isinstance(expr, str) or not expr.strip():
        raise SafeEvalError("条件表达式不能为空")
    if len(expr) > _MAX_EXPR_LEN:
        raise SafeEvalError(f"条件表达式过长（上限 {_MAX_EXPR_LEN} 字符）")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise SafeEvalError(f"条件表达式语法错误: {e.msg}") from e
    _check_node(tree.body)
    return expr


def _check_node(node: ast.AST) -> None:
    """递归校验 AST 节点类型是否在白名单内。"""
    allowed = (
        ast.BoolOp,
        ast.UnaryOp,
        ast.BinOp,
        ast.Compare,
        ast.Constant,
        ast.Name,
        ast.Load,
        ast.And,
        ast.Or,
        ast.Not,
        ast.USub,
        ast.UAdd,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.FloorDiv,
        ast.Mod,
        ast.Pow,
        ast.Eq,
        ast.NotEq,
        ast.Lt,
        ast.LtE,
        ast.Gt,
        ast.GtE,
        ast.In,
        ast.NotIn,
        # 3.8+ 的表达式节点容器
        ast.Expression,
        ast.expr_context,
    )
    if not isinstance(node, allowed):
        raise SafeEvalError(f"条件表达式不允许使用语法: {type(node).__name__}")

    # 白名单标注为非 Tuple 的 ast.Constant 即可，无需额外处理
    for child in ast.iter_child_nodes(node):
        _check_node(child)


def evaluate_condition(expr: str, slots: Mapping[str, Any]) -> bool:
    """安全求值条件表达式，返回布尔结果。

    - 解析或求值失败时记录告警并返回 False（失败关闭，不抛出阻断主流程）
    - 只读访问 slots，不修改
    """
    try:
        validate_condition_expression(expr)
        tree = ast.parse(expr, mode="eval")
        result = _eval_node(tree.body, slots)
        return bool(result)
    except SafeEvalError as e:
        logger.warning("条件表达式被安全策略拒绝: %s (%s)", expr, e)
        return False
    except Exception as e:  # noqa: BLE001 - 求值失败一律失败关闭
        logger.warning("条件表达式求值失败: %s (%s: %s)", expr, type(e).__name__, e)
        return False


class SafeASTEvaluator:
    """类风格门面（engine.py 等调用方使用）：SafeASTEvaluator.evaluate(expr, slots)。"""

    @staticmethod
    def evaluate(expr: str, slots: Mapping[str, Any]) -> bool:
        return evaluate_condition(expr, slots)

    @staticmethod
    def validate(expr: str) -> str:
        """校验表达式合法性；合法原样返回，不合法抛出 SafeEvalError。"""
        return validate_condition_expression(expr)


def _eval_node(node: ast.AST, slots: Mapping[str, Any]) -> Any:
    """按白名单递归求值（_check_node 已保证节点类型）。"""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (bool, int, float, str)) or node.value is None:
            return node.value
        raise SafeEvalError(f"不允许的常量类型: {type(node.value).__name__}")

    if isinstance(node, ast.Name):
        name = node.id
        if name in slots:
            return slots[name]
        if name.lower() == "true":
            return True
        if name.lower() == "false":
            return False
        if name.lower() in ("none", "null"):
            return None
        return slots.get(name, None)

    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise SafeEvalError(f"不允许的运算符: {type(node.op).__name__}")
        return op(_eval_node(node.left, slots), _eval_node(node.right, slots))

    if isinstance(node, ast.UnaryOp):
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise SafeEvalError(f"不允许的一元运算符: {type(node.op).__name__}")
        return op(_eval_node(node.operand, slots))

    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            result = True
            for value in node.values:
                result = _eval_node(value, slots)
                if not result:
                    return result
            return result
        if isinstance(node.op, ast.Or):
            result = False
            for value in node.values:
                result = _eval_node(value, slots)
                if result:
                    return result
            return result
        raise SafeEvalError("不允许的布尔运算符")

    if isinstance(node, ast.Compare):
        if len(node.ops) > _MAX_CMP_CHAIN:
            raise SafeEvalError("比较链过长")
        left = _eval_node(node.left, slots)
        result = True
        for op_node, comparator in zip(node.ops, node.comparators, strict=False):
            op = _CMP_OPS.get(type(op_node))
            if op is None:
                raise SafeEvalError(f"不允许的比较运算符: {type(op_node).__name__}")
            right = _eval_node(comparator, slots)
            if not op(left, right):
                result = False
                break
            left = right
        return result

    raise SafeEvalError(f"不允许的表达式节点: {type(node).__name__}")
