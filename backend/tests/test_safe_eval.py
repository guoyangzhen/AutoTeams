import pytest
from app.services.flow_core.safe_eval import SafeASTEvaluator, UnsafeExpressionError


def test_safe_ast_normal_expressions():
    slots = {
        "amount": 128000,
        "role": "VIP",
        "verified": True,
        "tags": ["urgent", "cross_border"],
    }

    # 简单比较
    assert SafeASTEvaluator.evaluate("amount > 100000", slots) is True
    assert SafeASTEvaluator.evaluate("amount <= 50000", slots) is False

    # 字符串相等
    assert SafeASTEvaluator.evaluate("role == 'VIP'", slots) is True
    assert SafeASTEvaluator.evaluate("role != 'GUEST'", slots) is True

    # 逻辑与或非
    assert SafeASTEvaluator.evaluate("amount > 50000 and verified", slots) is True
    assert SafeASTEvaluator.evaluate("not verified or amount < 1000", slots) is False

    # in 包含关系
    assert SafeASTEvaluator.evaluate("'urgent' in tags", slots) is True
    assert SafeASTEvaluator.evaluate("'normal' not in tags", slots) is True

    # 算术运算
    assert SafeASTEvaluator.evaluate("amount - 28000 == 100000", slots) is True


def test_safe_ast_blocks_malicious_rce_payloads():
    slots = {"user": "guest", "input": "payload"}

    # 经典 Python 反射链 RCE
    malicious_payloads = [
        "().__class__.__mro__[1].__subclasses__()",
        "__import__('os').system('id')",
        "eval('1+1')",
        "exec('import os')",
        "open('/etc/passwd').read()",
        "getattr(user, '__class__')",
        "user.__class__.__base__.__subclasses__()",
        "(lambda: 1)()",
        "[c for c in ().__class__.__bases__[0].__subclasses__()]",
    ]

    for payload in malicious_payloads:
        # 必须安全拦截并返回 False，绝不能抛出未处理异常或执行指令
        res = SafeASTEvaluator.evaluate(payload, slots)
        assert res is False, f"Malicious payload was not blocked: {payload}"


def test_safe_ast_missing_slots_fallback():
    slots = {"score": 90}
    # 未知变量应当作 None，不报错崩溃
    assert SafeASTEvaluator.evaluate("unknown_var == None", slots) is True
    assert SafeASTEvaluator.evaluate("unknown_var > 10", slots) is False
