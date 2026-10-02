"""Skill 安全筛查机制。

三层筛查：
1. 静态规则审查（自动）：Prompt 注入模式、类型/权限/资源限制
2. 沙箱试运行（自动）：在隔离环境中用样例输入执行待审批 Skill
3. 人工审批（HITL）：静态 + 试运行通过后进入 pending，等待管理员审批
"""
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from app.models.skill import Skill

logger = logging.getLogger(__name__)

# 沙箱试运行输出风险模式：疑似系统路径、命令注入、外联 URL
_REVIEW_OUTPUT_PATTERNS = [
    re.compile(r"(?i)(?:C:|/home/|/root/|/etc/|/var/|/usr/|\\\\|/\.ssh|/\.aws|/\.env|\.git/)", re.IGNORECASE),
    re.compile(r"(?i)(?:rm\s+-rf|sudo\s+|curl\s+|wget\s+|nc\s+-|bash\s+-i|python\s+-c|eval\s*\()"),
    re.compile(r"(?i)(?:https?://|ftp://|file://|s3://)", re.IGNORECASE),
]


# 已知 Prompt 注入/越狱风险模式（大小写不敏感）
_INJECTION_PATTERNS = [
    r"ignore\s+(?:previous|above|all)\s+instructions",
    r"ignore\s+the\s+system\s+prompt",
    r"you\s+are\s+now\s+(?:in\s+)?(?:developer|maintenance|debug)\s+mode",
    r"(?:disregard|bypass|override)\s+(?:safety|security|guidelines?|rules?)",
    r"DAN\s+mode",
    r" jailbreak ",
    r"\{\{.*?\}\}",  # 模板引擎常见注入标记
    r"<%.*?%>",
    r"`\s*rm\s+-rf",
    r"`\s*sudo\s+",
]

_ALLOWED_SKILL_TYPES = {
    "text_generation",
    "text_summarization",
    "text_classification",
    "data_extraction",
    "translation",
    "code_generation",
    "custom",
    # B6: 低风险自动放行类型（只读检索与摘要，无副作用）
    "search",
    "lookup",
    "summary",
}

# 合理的权限声明集合；超出此集合视为高风险
_ALLOWED_PERMISSIONS = {
    "read:files",
    "read:conversations",
    "read:skills",
    "write:skills",
    "execute:llm",
}

_MAX_PROMPT_LENGTH = 8000
_MAX_PARAMS_COUNT = 20
_MAX_NESTED_DEPTH = 3


@dataclass
class ScreeningResult:
    passed: bool = False
    risk_level: str = "low"  # low / medium / high
    issues: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "risk_level": self.risk_level,
            "issues": self.issues,
            "details": self.details,
        }


class SkillScreener:
    """Skill 静态安全筛查器。"""

    def __init__(self):
        self._injection_patterns = [re.compile(p, re.IGNORECASE) for p in _INJECTION_PATTERNS]

    def screen(self, skill: Skill, sample_input: Optional[dict] = None) -> ScreeningResult:
        """对 Skill 执行完整静态筛查，返回 ScreeningResult。"""
        result = ScreeningResult()
        self._check_type(skill, result)
        self._check_prompt_template(skill, result)
        self._check_permissions(skill, result)
        self._check_config_limits(skill, result)
        self._check_description(skill, result)

        # 综合判定：无 issue 且风险等级不为 high 才算通过
        if result.risk_level != "high" and not result.issues:
            result.passed = True
        elif result.risk_level == "high":
            result.passed = False

        result.details["sample_input"] = sample_input or self._default_sample_input(skill)
        return result

    async def review_run(
        self,
        db: Any,
        skill: Skill,
        user_id: str,
        sample_input: Optional[dict] = None,
    ) -> dict:
        """P1-SKILL: 沙箱试运行待审批 Skill。

        在隔离输入下执行 Skill，记录执行结果并扫描输出风险。
        试运行结果写入 SkillExecution 表（is_review_run=True）。
        返回 review_run 结果字典，供 review_result 使用。
        """
        from app.services.skill_executor import skill_executor

        sample = sample_input or self._default_sample_input(skill)
        review = {
            "sample_input": sample,
            "executed": False,
            "execution_status": None,
            "output_risks": [],
            "passed": False,
        }

        try:
            result = await skill_executor.execute_skill(
                db=db,
                skill_id=str(skill.id),
                input_data=sample,
                user_id=user_id,
                agent_id=skill.agent_id,
                is_review_run=True,
            )
            review["executed"] = True
            review["execution_status"] = result.get("status", "success")
            review["output_data"] = result.get("output_data", {})
        except Exception as e:
            logger.warning(f"Skill 沙箱试运行执行失败 skill_id={skill.id}: {e}")
            review["executed"] = True
            review["execution_status"] = "failed"
            review["execution_error"] = str(e)

        # 扫描输出风险
        output_text = self._extract_output_text(review.get("output_data", {}))
        for pattern in _REVIEW_OUTPUT_PATTERNS:
            match = pattern.search(output_text)
            if match:
                review["output_risks"].append(f"检测到可疑输出模式: {match.group(0)}")

        review["passed"] = (
            review["execution_status"] in ("success", None)
            and not review["output_risks"]
        )
        return review

    def _extract_output_text(self, output_data: Any) -> str:
        """从 Skill 输出中提取文本用于风险扫描。"""
        if isinstance(output_data, str):
            return output_data
        if isinstance(output_data, dict):
            parts = []
            for v in output_data.values():
                if isinstance(v, str):
                    parts.append(v)
                elif isinstance(v, (list, dict)):
                    parts.append(str(v))
            return "\n".join(parts)
        return str(output_data)

    def _check_type(self, skill: Skill, result: ScreeningResult) -> None:
        if skill.skill_type not in _ALLOWED_SKILL_TYPES:
            result.issues.append(f"不支持的技能类型：{skill.skill_type}")
            result.risk_level = "high"

    def _check_prompt_template(self, skill: Skill, result: ScreeningResult) -> None:
        prompt = (skill.config or {}).get("prompt_template", "")
        if not prompt:
            if skill.skill_type == "custom":
                result.issues.append("custom 类型技能缺少 prompt_template")
                result.risk_level = max_risk(result.risk_level, "medium")
            return

        if len(prompt) > _MAX_PROMPT_LENGTH:
            result.issues.append(f"prompt_template 长度 {len(prompt)} 超过上限 {_MAX_PROMPT_LENGTH}")
            result.risk_level = max_risk(result.risk_level, "medium")

        for pattern in self._injection_patterns:
            if pattern.search(prompt):
                result.issues.append(f"检测到可疑注入模式：{pattern.pattern[:60]}")
                result.risk_level = "high"

        # 计数占位符数量，防止过度模板化
        placeholder_count = prompt.count("{")
        if placeholder_count > 10:
            result.issues.append(f"prompt_template 中占位符数量过多：{placeholder_count}")
            result.risk_level = max_risk(result.risk_level, "medium")

    def _check_permissions(self, skill: Skill, result: ScreeningResult) -> None:
        perms = skill.permissions or []
        if not isinstance(perms, list):
            result.issues.append("permissions 必须是字符串列表")
            result.risk_level = "high"
            return

        for p in perms:
            if p not in _ALLOWED_PERMISSIONS:
                result.issues.append(f"权限声明超出合理范围：{p}")
                result.risk_level = max_risk(result.risk_level, "medium")

    def _check_config_limits(self, skill: Skill, result: ScreeningResult) -> None:
        config = skill.config or {}
        if len(config) > _MAX_PARAMS_COUNT:
            result.issues.append(f"config 参数数量 {len(config)} 超过上限 {_MAX_PARAMS_COUNT}")
            result.risk_level = max_risk(result.risk_level, "medium")

        depth = _nested_depth(config)
        if depth > _MAX_NESTED_DEPTH:
            result.issues.append(f"config 嵌套深度 {depth} 超过上限 {_MAX_NESTED_DEPTH}")
            result.risk_level = max_risk(result.risk_level, "medium")

    def _check_description(self, skill: Skill, result: ScreeningResult) -> None:
        desc = skill.description or ""
        if len(desc) > 2000:
            result.issues.append("description 超过 2000 字符")
            result.risk_level = max_risk(result.risk_level, "low")

    def _default_sample_input(self, skill: Skill) -> dict:
        """为沙箱试运行生成一个默认样例输入。"""
        input_type = skill.input_type or "text"
        if input_type == "image":
            return {"text": "请分析示例图片内容", "image_url": "https://example.com/demo.png"}
        return {"text": "这是一个用于安全筛查的示例输入。"}


def max_risk(current: str, new: str) -> str:
    """风险等级取高者。"""
    order = {"low": 0, "medium": 1, "high": 2}
    return current if order.get(current, 0) >= order.get(new, 0) else new


def _nested_depth(obj: Any, depth: int = 0) -> int:
    """计算 dict/list 的最大嵌套深度。"""
    if isinstance(obj, dict):
        if not obj:
            return depth
        return max(_nested_depth(v, depth + 1) for v in obj.values())
    if isinstance(obj, list):
        if not obj:
            return depth
        return max(_nested_depth(item, depth + 1) for item in obj)
    return depth
