"""AutoTeams 5.0 · 战役 3：具身物理执行器 2.0 协议与三重护栏（Local Runner 2.0）。

本模块是云端与端侧 Local Runner 2.0 之间的**具身物理操作契约**与**物理沙箱阻断中枢**，
对应《AutoTeams 5.0 演进蓝图与全自主蜂群架构设计》战役 3 的三项技术规格：

1. **内核升级**：物理操作分两条通道下发 —— ``browser_action``（无头浏览器导航/点击/表格录入）
   与 ``desktop_accessibility``（系统辅助功能树读取与受控输入模拟）。
2. **物理沙箱阻断**：所有点击、键入与跨窗口操作在下发前（云端）与执行前（端侧）由
   **Tri-Rule Guardrails（三重护栏）**实时审查：
   - 规则一 **凭据代填过滤器**：严禁任务正文携带硬编码密码 / 凭据；命中凭据字段时必须
     改用 Fernet 凭据机代填引用（``credential_ref``），否则整单阻断；
   - 规则二 **高危操作双因子确认**：高危操作（转账确认、批量删除、下单签署等）强制暂停，
     进入 ``require_2fa`` 状态，必须依次通过「云端意图确认」+「端侧 6 位物理确认码」两道因子；
   - 规则三 **作用域与跳转面收敛**：写操作必须落在已授权的 ``physical`` 作用域内，
     且浏览器只允许 http/https 目标（拒绝 ``file:`` / ``javascript:`` / ``data:``）。
3. **实时视窗流**：端侧把操作视窗降采样为**无损 8bit 灰度帧**（或关键帧变化日志）回传，
   云端按序归档并以帧流接口推送到前端工作台。

设备身份与租户边界（AUD-04）
--------------------------
历史实现用**全局共享的 ``X-Bridge-Secret``** 鉴权，设备档案不含企业归属，
于是任何拿到该密钥的终端都能被任意租户定位，高危确认码还会随心跳广播给
**全部在线设备**。现在的契约是：

- 设备注册（云端登录用户通道）时由服务端固化 ``enterprise_id`` / ``owner_user_id``，
  端侧无法自称企业；
- 注册时签发**逐设备长期凭据**（只存 SHA-256 哈希），端侧用它换取
  **短期访问令牌**（15 分钟，同样只存哈希）；机器端点一律只认设备令牌，
  ``X-Bridge-Secret`` 不再有任何设备语义（clean cutover）；
- 每一个机器调用都强制执行「device.enterprise_id == 调用者企业」且
  「task.device_id == device.id」，跨租户访问统一按「不存在」处理；
- 第二道因子确认码只投递给**任务属主设备**的心跳，其他设备的心跳永远拿不到；
- 撤销单台设备不影响其他设备，也不需要轮换任何全局内部密钥。

持久化与保留策略（AUD-18）
-------------------------
设备档案、任务账本、视窗帧、双因子挑战、审计留痕与设备指令全部落库
（``app/models/runner_v2.py``），新进程实例可以直接看到历史任务。
有界保留策略（由 ``purge_expired`` 实现，心跳时按 ``PURGE_INTERVAL_SECONDS``
节流触发，也可在运维任务中直接调用）：

===========================  ==========  =====================================
对象                          保留上限      清理规则
===========================  ==========  =====================================
``runner_task_frames``        120 帧/任务   超出即删最旧帧；任务进入终态后
                                         ``FRAME_RETENTION_HOURS``（6 小时）
                                         起整任务清空
``runner_tasks``              30 天         仅清理终态任务（completed / failed /
                                         cancelled / blocked）；非终态任务
                                         永不因保留策略被删
``runner_challenges``         7 天          仅清理已终结的挑战
``runner_audit_entries``      180 天        按 ``created_at`` 清理
``runner_device_commands``    1 小时        pending 指令超时即作废
``runner_device_credentials`` 撤销后 30 天  已撤销 / 已过期凭据清理
``runner_device_tokens``      过期后 7 天   已过期 / 已撤销令牌清理
===========================  ==========  =====================================

``runner_devices`` 本身不做时间清理（设备档案是企业资产，需要显式撤销）。

设计约束（与 4.0 ``LocalRunnerBridge`` / ``Flow-Core`` 三阶护栏一致）：
- 护栏裁决是**纯函数**，不依赖数据库，便于单测与端侧镜像实现复用同一套语义；
- 审计留痕**永不落明文凭据**（``mask_secrets`` 在写入前抹除）；
- 设备确认码以 Fernet 密文落库，只在投递给属主设备时解密。
"""
from __future__ import annotations

import base64
import enum
import hashlib
import hmac
import re
import secrets
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.runner_v2 import (
    RunnerAuditEntry,
    RunnerChallenge,
    RunnerDevice,
    RunnerDeviceCommand,
    RunnerDeviceCredential,
    RunnerDeviceToken,
    RunnerTask,
    RunnerTaskFrame,
)
from app.utils.credential_crypto import CredentialDecryptError, decrypt_credential, encrypt_credential
from app.utils.db_tenant_context import TenantResolver, resolve_and_bind_tenant
from app.utils.time import utcnow


# ============================================================
# 常量：会话时序与流控上限
# ============================================================

#: 端侧心跳保活窗口。Runner 每 15s 心跳，超过该窗口未心跳判定离线。
HEARTBEAT_TTL_SECONDS = 45.0

CHALLENGE_TTL_SECONDS = 300.0

#: 单个物理任务最多保留的视窗帧数量（极低带宽约束下的环形缓冲上界）。
MAX_FRAMES_PER_TASK = 120

#: 单帧灰度像素载荷上限（160x100 = 16000 字节，留出余量）。
MAX_FRAME_PAYLOAD_BYTES = 64 * 1024

#: 视窗流降采样尺寸契约（前端按此尺寸重建 ImageData）。
FRAME_WIDTH = 160
FRAME_HEIGHT = 100

# ---- 设备身份与保留策略常量（AUD-04 / AUD-18）----

#: 设备短期访问令牌有效期（秒）。端侧在过期前用长期凭据续期。
DEVICE_TOKEN_TTL_SECONDS = 15 * 60

#: 设备长期凭据有效期（天）。
DEVICE_CREDENTIAL_TTL_DAYS = 180

#: 设备指令（云端 → 端侧工具桥接）有效期（秒）。
DEVICE_COMMAND_TTL_SECONDS = 60.0

# ---- 有界保留策略（AUD-18，数值与模块 docstring 的表格一一对应）----

#: 任务进入终态后，视窗帧继续保留的时长（小时）。
FRAME_RETENTION_HOURS = 6

#: 终态物理任务保留天数（非终态任务永不因保留策略被清理）。
TASK_RETENTION_DAYS = 30

#: 已终结双因子挑战保留天数。
CHALLENGE_RETENTION_DAYS = 7

#: 审计留痕保留天数。
AUDIT_RETENTION_DAYS = 180

#: 已撤销 / 已过期设备长期凭据的保留天数。
CREDENTIAL_RETENTION_DAYS = 30

#: 已过期 / 已撤销短期令牌在过期后的保留天数。
TOKEN_RETENTION_DAYS = 7

#: result JSON 里的内部记账键：记录**已确认的 step 身份**集合。
#:
#: 账本按 step 身份（而不是任意回执字符串）记账，且**不做滑动淘汰**：其上界
#: 天然等于该任务的 step 数（`MAX_STEPS_PER_TASK` 已限制）。淘汰旧条目会让
#: 断线重连补发的旧回执被当成新回执二次记账。
RECEIPT_LEDGER_KEY = "receipt_ledger"


#: 清理节流间隔（秒）：心跳按此间隔触发一次全量清理，避免每跳都扫表。
PURGE_INTERVAL_SECONDS = 600.0

#: 单次审计读取返回的最大条数。
MAX_AUDIT_ENTRIES = 500


# ============================================================
# 枚举：通道 / 裁决 / 状态机
# ============================================================


class PhysicalChannel(str, enum.Enum):
    """具身物理操作的两条端侧通道。"""

    BROWSER = "browser_action"
    DESKTOP = "desktop_accessibility"


class GuardAction(str, enum.Enum):
    """Tri-Rule 护栏裁决结果。"""

    ALLOW = "allow"
    BLOCK = "block"
    REQUIRE_2FA = "require_2fa"


class TaskState(str, enum.Enum):
    """物理任务生命周期。"""

    BLOCKED = "blocked"
    AWAITING_2FA = "awaiting_2fa"
    DISPATCHED = "dispatched"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ChallengeState(str, enum.Enum):
    """高危操作双因子确认状态机（两道因子串联）。"""

    PENDING_ENDPOINT = "pending_endpoint_confirmation"
    PENDING_DEVICE_CODE = "pending_device_code"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class PhysicalProtocolError(Exception):
    """物理协议层错误（携带稳定的机器可读 code）。"""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


#: 任务状态机允许的迁移。
_ALLOWED_TASK_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.BLOCKED: frozenset(),
    TaskState.AWAITING_2FA: frozenset({TaskState.DISPATCHED, TaskState.CANCELLED}),
    TaskState.DISPATCHED: frozenset({TaskState.EXECUTING, TaskState.FAILED, TaskState.CANCELLED}),
    TaskState.EXECUTING: frozenset({TaskState.COMPLETED, TaskState.FAILED}),
    TaskState.COMPLETED: frozenset(),
    TaskState.FAILED: frozenset(),
    TaskState.CANCELLED: frozenset(),
}


# ============================================================
# Tri-Rule Guardrails：规则一 凭据代填过滤器
# ============================================================

#: 命中即视为「凭据字段」的标签/选择器/无障碍节点名片段。
_CREDENTIAL_FIELD_PATTERN = re.compile(
    r"(password|passwd|pwd|pass_?code|密码|口令|验证码|校验码|动态码|短信码|"
    r"otp|mfa|2fa|token|secret|api[_\-\s]?key|access[_\-\s]?key|private[_\-\s]?key|私钥|"
    r"credential|凭据|身份证号|id[_\-\s]?card)",
    re.IGNORECASE,
)

#: 正文中直接携带凭据赋值的形态（``password=xxx`` / ``"pwd": "xxx"``）。
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(password|passwd|pwd|口令|密码|token|secret|api[_\-\s]?key|access[_\-\s]?key|私钥|credential)"
    r"\s*[:=]\s*[\"']?[^\s\"',;]{4,}",
    re.IGNORECASE,
)

#: 高熵裸串（长十六进制 / base64 串）直接出现在录入正文中的形态。
_HIGH_ENTROPY_LITERAL_PATTERN = re.compile(r"^[A-Za-z0-9+/=_-]{24,}$")


# ============================================================
# Tri-Rule Guardrails：规则二 高危操作词表
# ============================================================

_HIGH_RISK_PATTERN = re.compile(
    r"(转账|汇款|打款|付款|支付|确认支付|立即支付|结算|清分|提现|"
    r"批量删除|全部删除|清空|销毁|不可撤销|"
    r"下单|提交订单|签约|签署|授权书|绑定卡|"
    r"transfer|wire\s*transfer|payment|pay\b|remittance|settle|withdraw|"
    r"bulk[_\-\s]?delete|delete[_\-\s]?all|purge|irreversible)",
    re.IGNORECASE,
)


# ============================================================
# Tri-Rule Guardrails：规则三 作用域与跳转面
# ============================================================

#: 允许的浏览器协议（其余一律阻断，防 file:// 与 javascript: 越权）。
ALLOWED_URL_SCHEMES = ("http", "https")

#: 只读操作：辅助功能树读取 / 页面导航读取态，任何作用域都允许。
READ_ONLY_OPS = frozenset({"navigate", "read_tree", "screenshot", "focus"})

#: 变更型操作：点击、录入、受控输入模拟、控件调用。
MUTATING_OPS = frozenset({"click", "fill_table", "input_text", "press_keys", "invoke"})

#: 每条通道允许的操作集合。
CHANNEL_OPS: dict[PhysicalChannel, frozenset[str]] = {
    PhysicalChannel.BROWSER: frozenset(
        {"navigate", "click", "fill_table", "screenshot", "press_keys"}
    ),
    PhysicalChannel.DESKTOP: frozenset(
        {"read_tree", "click", "input_text", "invoke", "focus"}
    ),
}

#: 变更型操作要求的端侧作用域。
PHYSICAL_SCOPE = "physical"


# ============================================================
# 工具函数
# ============================================================


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_ts() -> float:
    return time.time()


def _iso(value: float) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def mask_secrets(text: Optional[str]) -> str:
    """抹除正文中的凭据片段，保证审计留痕永不落明文。"""
    if not text:
        return ""
    masked = _SECRET_ASSIGNMENT_PATTERN.sub(lambda m: f"{m.group(1)}=[REDACTED]", text)
    if _HIGH_ENTROPY_LITERAL_PATTERN.match(masked.strip()):
        return "[REDACTED]"
    return masked


def _flatten_text(value: Any, depth: int = 0) -> str:
    """把 step 中所有可读文本摊平，用于关键词与凭据审查（深度受限）。"""
    if depth > 4:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)) or value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(_flatten_text(v, depth + 1) for v in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(_flatten_text(v, depth + 1) for v in value)
    return ""


def _flatten_pairs(value: Any, depth: int = 0) -> str:
    """把 dict 渲染为 ``key=value`` 形态，使「列名=取值」也进入凭据审查。"""
    if depth > 4:
        return ""
    if isinstance(value, dict):
        return " ".join(
            f"{_flatten_text(k, depth + 1)}={_flatten_text(v, depth + 1)}"
            for k, v in value.items()
        )
    if isinstance(value, (list, tuple)):
        return " ".join(_flatten_pairs(v, depth + 1) for v in value)
    return _flatten_text(value, depth)


def _field_names(step: dict[str, Any]) -> str:
    """收集该 step 命中的全部字段名（选择器、标签、表格列名）。"""
    names: list[str] = []
    for key in ("target", "label", "field", "name", "role", "op"):
        names.append(_flatten_text(step.get(key)))
    rows = step.get("rows")
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                names.extend(_flatten_text(k) for k in row.keys())
    return " ".join(n for n in names if n)


def _is_credential_field(step: dict[str, Any]) -> bool:
    """判定该 step 是否在向「凭据类字段」写入内容。"""
    return bool(_CREDENTIAL_FIELD_PATTERN.search(_field_names(step)))


def _has_literal_secret(step: dict[str, Any]) -> Optional[str]:
    """检测 step 是否携带硬编码凭据；返回命中的证据描述（已脱敏）。"""
    text = _flatten_text(step.get("text"))
    rows = step.get("rows")
    if isinstance(rows, list):
        text = f"{text} {_flatten_pairs(rows)}".strip()

    if text:
        assignment = _SECRET_ASSIGNMENT_PATTERN.search(text)
        if assignment:
            return f"正文出现凭据赋值片段（{assignment.group(1)}）"
        stripped = text.strip()
        if _HIGH_ENTROPY_LITERAL_PATTERN.match(stripped):
            return "正文出现高熵疑似密钥串"

    if _is_credential_field(step) and text.strip():
        # 目标是凭据字段却直接给字面量：必须改走凭据机代填。
        return "凭据字段被直接写入明文"
    return None


def _is_high_risk_step(step: dict[str, Any]) -> bool:
    """判定该 step 是否属于高危操作（语义标签 + 操作类型双重识别）。"""
    if str(step.get("risk", "")).lower() == "high":
        return True
    if str(step.get("op")) in READ_ONLY_OPS:
        return False
    haystack = " ".join(
        _flatten_text(step.get(key)) for key in ("target", "label", "text", "url", "name")
    )
    return bool(_HIGH_RISK_PATTERN.search(haystack))


# ============================================================
# 裁决结果
# ============================================================


@dataclass(frozen=True)
class GuardrailVerdict:
    """Tri-Rule 护栏裁决。"""

    action: GuardAction
    rule: str
    reason: str
    step_index: Optional[int] = None

    @property
    def blocked(self) -> bool:
        return self.action == GuardAction.BLOCK

    @property
    def requires_2fa(self) -> bool:
        return self.action == GuardAction.REQUIRE_2FA

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


ALLOW_VERDICT = GuardrailVerdict(
    action=GuardAction.ALLOW, rule="tri_rule_pass", reason="三重护栏全部通过"
)


# ============================================================
# 护栏引擎（纯函数）
# ============================================================


def _resolve_channel(raw_channel: Any) -> PhysicalChannel:
    try:
        return PhysicalChannel(str(raw_channel))
    except ValueError as exc:
        raise PhysicalProtocolError(
            "RUNNER_V2_INVALID_CHANNEL",
            f"未知物理通道: {raw_channel}（仅支持 browser_action / desktop_accessibility）",
        ) from exc


def evaluate_task(task: dict[str, Any], runner_scopes: Iterable[str]) -> GuardrailVerdict:
    """对一条物理任务执行 Tri-Rule 护栏审查。

    Args:
        task: 物理任务原始载荷（``channel`` / ``steps`` / ``credential_refs``）。
        runner_scopes: 目标端侧 Runner 已授予的作用域集合。

    Returns:
        :class:`GuardrailVerdict`。规则一（凭据代填过滤器）优先于规则二（高危双因子），
        规则三（作用域与跳转面）贯穿每一步的合法性检查。
    """
    scopes = {str(s) for s in runner_scopes}
    channel = _resolve_channel(task.get("channel"))
    allowed_ops = CHANNEL_OPS[channel]

    steps = task.get("steps")
    if not isinstance(steps, list) or not steps:
        raise PhysicalProtocolError(
            "RUNNER_V2_EMPTY_STEPS", "物理任务必须包含至少一个 step"
        )

    high_risk_indexes: list[int] = []

    for index, raw_step in enumerate(steps):
        if not isinstance(raw_step, dict):
            raise PhysicalProtocolError(
                "RUNNER_V2_INVALID_STEP", f"step[{index}] 必须是对象", status_code=422
            )
        step = raw_step
        op = str(step.get("op", ""))
        if not op:
            raise PhysicalProtocolError(
                "RUNNER_V2_INVALID_STEP", f"step[{index}] 缺少 op", status_code=422
            )

        # ---- 规则三（上半）：通道与操作面白名单 ----
        if op not in allowed_ops:
            return GuardrailVerdict(
                action=GuardAction.BLOCK,
                rule="tri_rule_channel_surface",
                reason=f"通道 {channel.value} 不支持操作 {op}",
                step_index=index,
            )

        # ---- 规则三（下半）：作用域收敛 ----
        if op in MUTATING_OPS and PHYSICAL_SCOPE not in scopes:
            return GuardrailVerdict(
                action=GuardAction.BLOCK,
                rule="tri_rule_scope",
                reason=f"端侧未授予 {PHYSICAL_SCOPE} 作用域，禁止执行变更型操作 {op}",
                step_index=index,
            )

        # ---- 规则三（跳转面）：浏览器目标协议收敛 ----
        url = str(step.get("url", "")).strip()
        if op == "navigate":
            if not url:
                return GuardrailVerdict(
                    action=GuardAction.BLOCK,
                    rule="tri_rule_navigation",
                    reason="navigate 必须提供 url",
                    step_index=index,
                )
            scheme = url.split(":", 1)[0].lower() if ":" in url else ""
            if scheme not in ALLOWED_URL_SCHEMES:
                return GuardrailVerdict(
                    action=GuardAction.BLOCK,
                    rule="tri_rule_navigation",
                    reason=f"禁止导航到非 http(s) 目标: {mask_secrets(url)}",
                    step_index=index,
                )
        elif url:
            scheme = url.split(":", 1)[0].lower() if ":" in url else ""
            if scheme not in ALLOWED_URL_SCHEMES:
                return GuardrailVerdict(
                    action=GuardAction.BLOCK,
                    rule="tri_rule_navigation",
                    reason=f"禁止引用非 http(s) 资源: {mask_secrets(url)}",
                    step_index=index,
                )

        # ---- 规则一：凭据代填过滤器（优先级最高，命中即整单阻断） ----
        literal = _has_literal_secret(step)
        if literal is not None:
            return GuardrailVerdict(
                action=GuardAction.BLOCK,
                rule="tri_rule_credential_autofill",
                reason=(
                    f"{literal}；凭据必须由端侧 Fernet 凭据机按 credential_ref 代填，"
                    "严禁随任务下发明文"
                ),
                step_index=index,
            )
        if _is_credential_field(step) and not str(step.get("credential_ref", "")).strip():
            return GuardrailVerdict(
                action=GuardAction.BLOCK,
                rule="tri_rule_credential_autofill",
                reason="凭据字段必须携带 credential_ref 由凭据机代填",
                step_index=index,
            )

        # ---- 规则二：高危操作标记（累积到任务级裁决） ----
        if _is_high_risk_step(step):
            high_risk_indexes.append(index)

    if high_risk_indexes:
        first = high_risk_indexes[0]
        return GuardrailVerdict(
            action=GuardAction.REQUIRE_2FA,
            rule="tri_rule_high_risk_two_factor",
            reason=(
                f"命中高危操作（step 索引 {high_risk_indexes}），"
                "必须完成云端意图确认 + 端侧物理确认码双因子后方可执行"
            ),
            step_index=first,
        )

    return ALLOW_VERDICT


# ============================================================
# 设备身份：凭据签发与校验（AUD-04）
# ============================================================


def _sha256_hex(raw: str) -> str:
    """凭据 / 令牌只以 SHA-256 哈希落库，数据库泄露无法直接冒充设备。"""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _random_secret(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def _utc(value: datetime) -> datetime:
    """SQLite 取回的 datetime 可能丢时区，统一补成 UTC 再比较。"""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    """把库内时间戳序列化为 ISO8601 字符串（前端契约）。"""
    if value is None:
        return None
    return _utc(value).isoformat()


def public_task_result(result: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """对外视图：剥离内部回执账本，只保留业务结果字段。"""
    if not isinstance(result, dict):
        return result
    return {key: value for key, value in result.items() if key != RECEIPT_LEDGER_KEY}


def _confirmed_step_ids(result: Optional[dict[str, Any]]) -> set[str]:
    """读取任务上已确认的 step 身份集合。"""
    raw = result.get(RECEIPT_LEDGER_KEY) if isinstance(result, dict) else None
    if not isinstance(raw, dict):
        return set()
    return {str(item) for item in (raw.get("step_ids") or [])}


def _task_step_ids(task: RunnerTask) -> list[str]:
    """任务真实存在的 step 身份（按下发顺序，去重）。"""
    identities: list[str] = []
    for index, step in enumerate(task.steps or []):
        identity = str((step or {}).get("step_id") or f"s{index}")
        if identity not in identities:
            identities.append(identity)
    return identities


#: 服务端可授予设备的作用域白名单：端侧自报不在此集合内的作用域一律忽略。
ALLOWED_DEVICE_SCOPES = frozenset({"read", "physical", "desktop"})

#: 进入终态、不再接受任何迁移的任务状态。
TERMINAL_TASK_STATES = (
    TaskState.BLOCKED.value,
    TaskState.COMPLETED.value,
    TaskState.FAILED.value,
    TaskState.CANCELLED.value,
)

#: 双因子挑战的终结状态。
TERMINAL_CHALLENGE_STATES = (
    ChallengeState.APPROVED.value,
    ChallengeState.REJECTED.value,
    ChallengeState.EXPIRED.value,
)


# ============================================================
# 协议中枢（全部状态落库）
# ============================================================


class RunnerV2Protocol:
    """设备注册表 + 任务账本 + 视窗帧池 + 审计留痕 + 双因子状态机。

    所有方法都是 ``async`` 且显式接收 ``AsyncSession``：Runner 2.0 的状态
    不再存活于任何进程内存（AUD-18），新进程实例可以直接看到历史任务。
    """

    def __init__(self) -> None:
        self._last_purge_at: float = 0.0

    # ------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------

    @staticmethod
    def device_to_dict(device: RunnerDevice) -> dict[str, Any]:
        last_seen = _utc(device.last_seen_at) if device.last_seen_at else None
        online = (
            device.status == "active"
            and last_seen is not None
            and (utcnow() - last_seen).total_seconds() <= HEARTBEAT_TTL_SECONDS
        )
        return {
            "device_id": device.id,
            "runner_id": device.runner_id,
            "device_label": device.device_label,
            "enterprise_id": device.enterprise_id,
            "scopes": list(device.scopes or []),
            "platform": device.platform,
            "version": device.version,
            "capabilities": dict(device.capabilities or {}),
            "status": device.status,
            "online": online,
            "last_seen": _iso(device.last_seen_at),
            "registered_at": _iso(device.created_at),
        }

    @staticmethod
    def task_to_dict(task: RunnerTask, runner_id: Optional[str] = None) -> dict[str, Any]:
        return {
            "task_id": task.task_id,
            "enterprise_id": task.enterprise_id,
            "device_id": task.device_id,
            "channel": task.channel,
            "state": task.state,
            "runner_id": runner_id,
            "created_by": task.created_by,
            "challenge_id": task.challenge_id,
            "verdict": dict(task.verdict or {}),
            "result": public_task_result(task.result),
            "created_at": _iso(task.created_at),
            "updated_at": _iso(task.updated_at),
        }

    @staticmethod
    def frame_to_dict(frame: RunnerTaskFrame) -> dict[str, Any]:
        return {
            "task_id": frame.task_id,
            "seq": frame.seq,
            "kind": frame.kind,
            "width": frame.width,
            "height": frame.height,
            "payload_b64": frame.payload_b64,
            "digest": frame.digest,
            "byte_size": frame.byte_size,
            "captured_at": _iso(frame.captured_at),
        }

    @staticmethod
    def challenge_to_dict(challenge: RunnerChallenge) -> dict[str, Any]:
        """云端视图：**永不**包含 device_code。"""
        return {
            "challenge_id": challenge.challenge_id,
            "task_id": challenge.task_id,
            "enterprise_id": challenge.enterprise_id,
            "device_id": challenge.device_id,
            "state": challenge.state,
            "reason": challenge.reason,
            "endpoint_confirmed_by": challenge.endpoint_confirmed_by,
            "device_verified": challenge.device_verified_at is not None,
            "expires_at": _iso(challenge.expires_at),
            "created_at": _iso(challenge.created_at),
        }

    @staticmethod
    def audit_to_dict(entry: RunnerAuditEntry) -> dict[str, Any]:
        return {
            "trace_id": entry.trace_id,
            "task_id": entry.task_id,
            "device_id": entry.device_id,
            "runner_id": entry.runner_id,
            "actor": entry.actor,
            "event": entry.event,
            "decision": entry.decision,
            "detail": entry.detail,
            "created_at": _iso(entry.created_at),
        }

    # ------------------------------------------------------------
    # 归属校验：设备 → 企业 → 任务
    # ------------------------------------------------------------

    async def _require_device(
        self, db: AsyncSession, device_id: str, enterprise_id: str
    ) -> RunnerDevice:
        """按「主键 + 企业」加载设备；不存在与跨企业统一 404（不泄露存在性）。"""
        result = await db.execute(
            select(RunnerDevice).where(
                RunnerDevice.id == device_id,
                RunnerDevice.enterprise_id == enterprise_id,
            )
        )
        device = result.scalar_one_or_none()
        if device is None:
            raise PhysicalProtocolError("RUNNER_V2_DEVICE_NOT_FOUND", "端侧设备不存在", 404)
        return device

    async def _require_online_device(
        self, db: AsyncSession, *, enterprise_id: str, runner_id: str
    ) -> RunnerDevice:
        """按企业解析在线设备：跨企业派发在协议层就断掉（AUD-04 复现路径）。"""
        result = await db.execute(
            select(RunnerDevice).where(
                RunnerDevice.enterprise_id == enterprise_id,
                RunnerDevice.runner_id == runner_id,
            )
        )
        device = result.scalar_one_or_none()
        if device is None:
            raise PhysicalProtocolError(
                "RUNNER_V2_RUNNER_UNKNOWN", f"端侧 Runner 未注册: {runner_id}", 404
            )
        if device.status != "active":
            raise PhysicalProtocolError(
                "RUNNER_V2_DEVICE_REVOKED", f"端侧设备凭据已撤销: {runner_id}", 403
            )
        if not self.device_to_dict(device)["online"]:
            raise PhysicalProtocolError(
                "RUNNER_V2_RUNNER_OFFLINE", f"端侧 Runner 心跳超时: {runner_id}", 409
            )
        return device

    async def _require_task_for_device(
        self, db: AsyncSession, device: RunnerDevice, task_id: str
    ) -> RunnerTask:
        """任务必须属于当前设备；否则按「不存在」处理。"""
        task = await db.get(RunnerTask, task_id)
        if task is None or task.device_id != device.id:
            raise PhysicalProtocolError("RUNNER_V2_TASK_NOT_FOUND", "物理任务不存在", 404)
        return task

    async def _require_challenge_for_device(
        self, db: AsyncSession, device: RunnerDevice, challenge_id: str
    ) -> RunnerChallenge:
        challenge = await db.get(RunnerChallenge, challenge_id)
        if challenge is None or challenge.device_id != device.id:
            raise PhysicalProtocolError(
                "RUNNER_V2_CHALLENGE_NOT_FOUND", "确认挑战不存在", 404
            )
        return challenge

    async def _require_challenge_for_enterprise(
        self, db: AsyncSession, challenge_id: str, enterprise_id: str
    ) -> RunnerChallenge:
        result = await db.execute(
            select(RunnerChallenge).where(
                RunnerChallenge.challenge_id == challenge_id,
                RunnerChallenge.enterprise_id == enterprise_id,
            )
        )
        challenge = result.scalar_one_or_none()
        if challenge is None:
            raise PhysicalProtocolError(
                "RUNNER_V2_CHALLENGE_NOT_FOUND", "确认挑战不存在", 404
            )
        return challenge

    @staticmethod
    def _decrypt_device_code(challenge: RunnerChallenge) -> str:
        try:
            return decrypt_credential(challenge.device_code_encrypted)
        except CredentialDecryptError as exc:  # 密钥轮换后无法还原：按不可用收敛
            raise PhysicalProtocolError(
                "RUNNER_V2_DEVICE_CODE_UNAVAILABLE",
                "端侧物理确认码不可用，请重新发起确认",
                409,
            ) from exc

    # ------------------------------------------------------------
    # 设备注册与凭据（AUD-04）
    # ------------------------------------------------------------

    async def register_device(
        self,
        db: AsyncSession,
        *,
        enterprise_id: str,
        owner_user_id: str,
        runner_id: str,
        device_label: Optional[str] = None,
        scopes: Optional[Iterable[str]] = None,
        credential_label: Optional[str] = None,
    ) -> dict[str, Any]:
        """注册设备：企业归属与所有者由**服务端**写入，端侧无法自称。"""
        result = await db.execute(
            select(RunnerDevice).where(
                RunnerDevice.enterprise_id == enterprise_id,
                RunnerDevice.runner_id == runner_id,
            )
        )
        if result.scalar_one_or_none() is not None:
            raise PhysicalProtocolError(
                "RUNNER_V2_DEVICE_EXISTS", f"该企业已存在同名端侧设备: {runner_id}", 409
            )

        granted = sorted({str(s) for s in (scopes or [])} & ALLOWED_DEVICE_SCOPES)
        device = RunnerDevice(
            enterprise_id=enterprise_id,
            owner_user_id=owner_user_id,
            runner_id=runner_id,
            device_label=device_label,
            scopes=granted,
            platform="unknown",
            version="0.0.0",
            capabilities={},
            status="active",
        )
        db.add(device)
        await db.flush()

        secret = _random_secret()
        credential = RunnerDeviceCredential(
            device_id=device.id,
            secret_hash=_sha256_hex(secret),
            label=credential_label,
            expires_at=utcnow() + timedelta(days=DEVICE_CREDENTIAL_TTL_DAYS),
        )
        db.add(credential)
        await self._record_audit(
            db,
            enterprise_id=enterprise_id,
            event="device_registered",
            decision="allow",
            detail=f"注册端侧设备 {runner_id}，授予作用域 {granted}",
            device_id=device.id,
            runner_id=runner_id,
            actor=owner_user_id,
        )
        await db.commit()

        payload = self.device_to_dict(device)
        payload["device_secret"] = secret
        payload["credential_expires_at"] = _iso(credential.expires_at)
        return payload

    async def rotate_device_credential(
        self,
        db: AsyncSession,
        *,
        device_id: str,
        enterprise_id: str,
        actor: str,
        label: Optional[str] = None,
    ) -> dict[str, Any]:
        """轮换单台设备的长期凭据：旧凭据立即失效，其他设备不受影响。"""
        device = await self._require_device(db, device_id, enterprise_id)
        now = utcnow()
        await db.execute(
            RunnerDeviceCredential.__table__.update()
            .where(
                RunnerDeviceCredential.device_id == device_id,
                RunnerDeviceCredential.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        secret = _random_secret()
        credential = RunnerDeviceCredential(
            device_id=device.id,
            secret_hash=_sha256_hex(secret),
            label=label,
            expires_at=now + timedelta(days=DEVICE_CREDENTIAL_TTL_DAYS),
        )
        db.add(credential)
        await self._record_audit(
            db,
            enterprise_id=enterprise_id,
            event="device_credential_rotated",
            decision="allow",
            detail=f"轮换端侧设备 {device.runner_id} 的长期凭据",
            device_id=device.id,
            runner_id=device.runner_id,
            actor=actor,
        )
        await db.commit()
        payload = self.device_to_dict(device)
        payload["device_secret"] = secret
        payload["credential_expires_at"] = _iso(credential.expires_at)
        return payload

    async def revoke_device(
        self,
        db: AsyncSession,
        *,
        device_id: str,
        enterprise_id: str,
        actor: str,
        reason: Optional[str] = None,
    ) -> dict[str, Any]:
        """撤销单台设备：作废其全部长期凭据与短期令牌，其他设备不受影响。"""
        device = await self._require_device(db, device_id, enterprise_id)
        now = utcnow()
        device.status = "revoked"
        await db.execute(
            RunnerDeviceCredential.__table__.update()
            .where(
                RunnerDeviceCredential.device_id == device_id,
                RunnerDeviceCredential.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        await db.execute(
            RunnerDeviceToken.__table__.update()
            .where(
                RunnerDeviceToken.device_id == device_id,
                RunnerDeviceToken.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        await self._record_audit(
            db,
            enterprise_id=enterprise_id,
            event="device_revoked",
            decision="revoke",
            detail=(
                f"撤销端侧设备 {device.runner_id}"
                + (f"：{mask_secrets(reason)}" if reason else "")
            ),
            device_id=device.id,
            runner_id=device.runner_id,
            actor=actor,
        )
        await db.commit()
        return self.device_to_dict(device)

    async def exchange_device_token(
        self, db: AsyncSession, *, device_id: str, device_secret: str
    ) -> dict[str, Any]:
        """用长期凭据换短期访问令牌（默认 15 分钟）。"""
        if db.get_bind().dialect.name == "postgresql":
            # AUD-19：受约束运行角色下必须先凭设备密钥解析出唯一租户，否则
            # 凭据/令牌表读不到任何行。解析函数要求出示密钥摘要，不是凭
            # device_id 就能换租户；解析不出租户一律按认证失败处理。
            enterprise_id = await resolve_and_bind_tenant(
                db,
                TenantResolver.RUNNER_CREDENTIAL,
                {"device_id": device_id, "secret_hash": _sha256_hex(str(device_secret))},
            )
            if enterprise_id is None:
                raise PhysicalProtocolError(
                    "RUNNER_V2_DEVICE_AUTH_FAILED", "设备凭据无效", 401
                )
        result = await db.execute(
            select(RunnerDeviceCredential, RunnerDevice)
            .join(RunnerDevice, RunnerDevice.id == RunnerDeviceCredential.device_id)
            .where(
                RunnerDeviceCredential.secret_hash == _sha256_hex(str(device_secret)),
                RunnerDevice.id == device_id,
            )
        )
        row = result.first()
        if row is None:
            raise PhysicalProtocolError("RUNNER_V2_DEVICE_AUTH_FAILED", "设备凭据无效", 401)
        credential, device = row
        if credential.revoked_at is not None:
            raise PhysicalProtocolError(
                "RUNNER_V2_DEVICE_CREDENTIAL_REVOKED", "设备凭据已撤销", 401
            )
        if credential.expires_at is not None and _utc(credential.expires_at) <= utcnow():
            raise PhysicalProtocolError(
                "RUNNER_V2_DEVICE_CREDENTIAL_EXPIRED", "设备凭据已过期，请重新注册设备", 401
            )
        if device.status != "active":
            raise PhysicalProtocolError("RUNNER_V2_DEVICE_REVOKED", "端侧设备已撤销", 401)

        token = _random_secret()
        record = RunnerDeviceToken(
            device_id=device.id,
            token_hash=_sha256_hex(token),
            expires_at=utcnow() + timedelta(seconds=DEVICE_TOKEN_TTL_SECONDS),
        )
        db.add(record)
        credential.last_used_at = utcnow()
        await db.commit()
        return {
            "device_id": device.id,
            "runner_id": device.runner_id,
            "enterprise_id": device.enterprise_id,
            "access_token": token,
            "token_type": "Bearer",
            "expires_at": _iso(record.expires_at),
            "scopes": list(device.scopes or []),
        }

    async def authenticate_device_token(
        self, db: AsyncSession, *, token: str
    ) -> RunnerDevice:
        """解析设备访问令牌；无效 / 过期 / 已撤销一律 401（失败关闭）。"""
        if not token or not token.strip():
            raise PhysicalProtocolError(
                "RUNNER_V2_DEVICE_TOKEN_MISSING", "缺少设备访问令牌", 401
            )
        if db.get_bind().dialect.name == "postgresql":
            # AUD-19：同上，先用令牌摘要解析租户，再校验令牌本身。
            enterprise_id = await resolve_and_bind_tenant(
                db,
                TenantResolver.RUNNER_ACCESS,
                {"token_hash": _sha256_hex(token.strip())},
            )
            if enterprise_id is None:
                raise PhysicalProtocolError(
                    "RUNNER_V2_DEVICE_TOKEN_INVALID", "设备访问令牌无效", 401
                )
        result = await db.execute(
            select(RunnerDeviceToken, RunnerDevice)
            .join(RunnerDevice, RunnerDevice.id == RunnerDeviceToken.device_id)
            .where(RunnerDeviceToken.token_hash == _sha256_hex(token.strip()))
        )
        row = result.first()
        if row is None:
            raise PhysicalProtocolError(
                "RUNNER_V2_DEVICE_TOKEN_INVALID", "设备访问令牌无效", 401
            )
        record, device = row
        if record.revoked_at is not None:
            raise PhysicalProtocolError(
                "RUNNER_V2_DEVICE_TOKEN_REVOKED", "设备访问令牌已撤销", 401
            )
        if _utc(record.expires_at) <= utcnow():
            raise PhysicalProtocolError(
                "RUNNER_V2_DEVICE_TOKEN_EXPIRED", "设备访问令牌已过期", 401
            )
        if device.status != "active":
            raise PhysicalProtocolError("RUNNER_V2_DEVICE_REVOKED", "端侧设备已撤销", 401)
        record.last_used_at = utcnow()
        return device

    async def list_devices(
        self, db: AsyncSession, enterprise_id: str, limit: int = 200
    ) -> list[dict[str, Any]]:
        """列出**本企业**的端侧设备（AUD-04：不再全库列举）。"""
        result = await db.execute(
            select(RunnerDevice)
            .where(RunnerDevice.enterprise_id == enterprise_id)
            .order_by(RunnerDevice.created_at.desc())
            .limit(limit)
        )
        return [self.device_to_dict(device) for device in result.scalars().all()]

    async def service_touch_device_presence(
        self, db: AsyncSession, *, enterprise_id: str, device_id: str
    ) -> dict[str, Any]:
        """服务间通道：登记「某企业某设备」的在线状态，不返回任何租户数据。

        collaboration-service 与 backend 之间本就是服务级互信，但这条通道
        **不能**成为跨租户设备读写的后门，因此它既不接受设备令牌，也不返回
        任务、确认码、凭据或设备列表。
        """
        device = await self._require_device(db, device_id, enterprise_id)
        if device.last_seen_at is None:
            device.last_seen_at = utcnow()
            await db.commit()
        return {
            "device_id": device.id,
            "runner_id": device.runner_id,
            "status": device.status,
            "online": self.device_to_dict(device)["online"],
        }


    # ------------------------------------------------------------
    # 端侧心跳
    # ------------------------------------------------------------

    async def heartbeat(
        self,
        db: AsyncSession,
        *,
        device: RunnerDevice,
        runner_id: str,
        scopes: Iterable[str],
        platform: str = "unknown",
        version: str = "0.0.0",
        capabilities: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """端侧心跳：续期档案，回带**本设备**的待执行任务与待确认通知。"""
        if runner_id and runner_id != device.runner_id:
            raise PhysicalProtocolError(
                "RUNNER_V2_DEVICE_IDENTITY_MISMATCH",
                "心跳 runner_id 与注册设备不一致（一卡一身份）",
                403,
            )

        reported = {str(s) for s in scopes} & ALLOWED_DEVICE_SCOPES
        granted = set(device.scopes or [])
        escalation = sorted(reported - granted)

        device.platform = platform or device.platform
        device.version = version or device.version
        device.capabilities = dict(capabilities or {})
        device.last_seen_at = utcnow()

        if escalation:
            await self._record_audit(
                db,
                enterprise_id=device.enterprise_id,
                event="runner_scope_escalation_denied",
                decision="deny",
                detail=(
                    f"设备 {device.runner_id} 自报未授权作用域 {escalation}，"
                    f"按服务端授权 {sorted(granted)} 执行"
                ),
                device_id=device.id,
                runner_id=device.runner_id,
                actor="runner",
            )

        pending = await self._pending_tasks(db, device)
        notifications = await self._device_notifications(db, device)
        await self._purge_if_due(db)
        await db.commit()

        return {
            "server_time": _iso(utcnow()),
            "heartbeat_ttl_seconds": HEARTBEAT_TTL_SECONDS,
            "runner": self.device_to_dict(device),
            "granted_scopes": sorted(granted),
            "pending_tasks": pending,
            "notifications": notifications,
        }

    async def _pending_tasks(
        self, db: AsyncSession, device: RunnerDevice
    ) -> list[dict[str, Any]]:
        result = await db.execute(
            select(RunnerTask)
            .where(
                RunnerTask.device_id == device.id,
                RunnerTask.state.in_([TaskState.DISPATCHED.value, TaskState.EXECUTING.value]),
            )
            .order_by(RunnerTask.created_at.asc())
        )
        return [
            {
                "task_id": task.task_id,
                "channel": task.channel,
                "steps": list(task.steps or []),
            }
            for task in result.scalars().all()
        ]

    async def _device_notifications(
        self, db: AsyncSession, device: RunnerDevice
    ) -> list[dict[str, Any]]:
        """第二道因子确认码**只**投递给任务属主设备（AUD-04 复现路径）。"""
        result = await db.execute(
            select(RunnerChallenge).where(
                RunnerChallenge.device_id == device.id,
                RunnerChallenge.state.in_(
                    [
                        ChallengeState.PENDING_ENDPOINT.value,
                        ChallengeState.PENDING_DEVICE_CODE.value,
                    ]
                ),
            )
        )
        notifications: list[dict[str, Any]] = []
        for challenge in result.scalars().all():
            await self._expire_if_needed(db, challenge)
            if challenge.state == ChallengeState.PENDING_ENDPOINT.value:
                # 第一道因子尚未完成：端侧只感知到「有待人工确认」，不拿确认码。
                notifications.append(
                    {
                        "challenge_id": challenge.challenge_id,
                        "task_id": challenge.task_id,
                        "state": challenge.state,
                        "reason": challenge.reason,
                    }
                )
            elif challenge.state == ChallengeState.PENDING_DEVICE_CODE.value:
                notifications.append(
                    {
                        "challenge_id": challenge.challenge_id,
                        "task_id": challenge.task_id,
                        "state": challenge.state,
                        "reason": challenge.reason,
                        "device_code": self._decrypt_device_code(challenge),
                    }
                )
        return notifications

    # ------------------------------------------------------------
    # 任务下发与领取
    # ------------------------------------------------------------

    @staticmethod
    def _normalize_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """为每条 step 赋予**唯一且稳定**的 ``step_id``，端侧据此生成幂等回执。

        端侧可以显式给出 ``step_id``（保持兼容），但重复、空白或非字符串身份
        一律不接受：身份重复时端侧账本（键为 ``task_id::step_id``）会跳过后续
        动作，云端也会因"已确认集合覆盖全部身份"而过早判定任务完成。
        因此冲突或缺失时由服务端生成 ``s{index}`` / ``s{index}-{n}`` 保证唯一，
        保证 step 数量与身份数量始终一致。
        """
        normalized: list[dict[str, Any]] = []
        used: set[str] = set()
        for index, raw in enumerate(steps):
            step = dict(raw)
            explicit = step.get("step_id")
            candidate = (
                explicit.strip() if isinstance(explicit, str) and explicit.strip() else ""
            )
            if not candidate or candidate in used:
                candidate = f"s{index}"
                suffix = 1
                while candidate in used:
                    candidate = f"s{index}-{suffix}"
                    suffix += 1
            step["step_id"] = candidate
            used.add(candidate)
            normalized.append(step)
        return normalized

    async def dispatch_task(
        self,
        db: AsyncSession,
        *,
        enterprise_id: str,
        channel_raw: Any,
        steps: list[dict[str, Any]],
        runner_id: str,
        actor: str,
    ) -> dict[str, Any]:
        """下发物理任务：先过护栏，再决定放行 / 阻断 / 双因子挂起。"""
        channel = _resolve_channel(channel_raw)
        device = await self._require_online_device(
            db, enterprise_id=enterprise_id, runner_id=runner_id
        )

        verdict = evaluate_task(
            {"channel": channel.value, "steps": steps}, list(device.scopes or [])
        )
        task_id = f"ptask-{uuid.uuid4().hex[:16]}"
        normalized = self._normalize_steps(steps)

        challenge: Optional[RunnerChallenge] = None
        if verdict.blocked:
            state = TaskState.BLOCKED
        elif verdict.requires_2fa:
            state = TaskState.AWAITING_2FA
        else:
            state = TaskState.DISPATCHED

        task = RunnerTask(
            task_id=task_id,
            enterprise_id=enterprise_id,
            device_id=device.id,
            channel=channel.value,
            state=state.value,
            verdict=verdict.to_dict(),
            steps=normalized,
            created_by=actor,
        )
        db.add(task)
        await db.flush()

        if state == TaskState.AWAITING_2FA:
            challenge = self._new_challenge(
                task_id=task_id,
                enterprise_id=enterprise_id,
                device_id=device.id,
                reason=verdict.reason,
            )
            db.add(challenge)
            task.challenge_id = challenge.challenge_id

        await self._record_audit(
            db,
            enterprise_id=enterprise_id,
            event={
                TaskState.BLOCKED: "task_blocked",
                TaskState.AWAITING_2FA: "task_pending_2fa",
            }.get(state, "task_dispatched"),
            decision=verdict.rule,
            detail=(
                f"[{verdict.reason}] 任务 {task_id} 被物理沙箱阻断"
                if state == TaskState.BLOCKED
                else (
                    f"任务 {task_id} 命中高危操作，挂起等待双因子确认"
                    if state == TaskState.AWAITING_2FA
                    else f"任务 {task_id} 已下发端侧 {device.runner_id}"
                )
            ),
            task_id=task_id,
            device_id=device.id,
            runner_id=device.runner_id,
            actor=actor,
        )
        await db.commit()

        payload = self.task_to_dict(task, device.runner_id)
        if challenge is not None:
            payload["challenge"] = self.challenge_to_dict(challenge)
        return payload

    async def claim_task(
        self, db: AsyncSession, *, device: RunnerDevice, task_id: str
    ) -> dict[str, Any]:
        """端侧领取任务：必须是**本设备**的任务，其他设备的任务按不存在处理。"""
        task = await self._require_task_for_device(db, device, task_id)
        if task.state == TaskState.DISPATCHED.value:
            self._transition(task, TaskState.EXECUTING)
            await self._record_audit(
                db,
                enterprise_id=task.enterprise_id,
                event="task_claimed",
                decision="allow",
                detail=f"端侧 {device.runner_id} 领取任务 {task_id}",
                task_id=task_id,
                device_id=device.id,
                runner_id=device.runner_id,
                actor="runner",
            )
            await db.commit()
        elif task.state not in (
            TaskState.EXECUTING.value,
            TaskState.COMPLETED.value,
            TaskState.FAILED.value,
        ):
            raise PhysicalProtocolError(
                "RUNNER_V2_TASK_STATE_INVALID",
                f"任务当前状态 {task.state} 不接受领取",
                409,
            )
        return self.task_to_dict(task, device.runner_id)

    async def list_tasks(
        self, db: AsyncSession, enterprise_id: str, limit: int = 200
    ) -> list[dict[str, Any]]:
        result = await db.execute(
            select(RunnerTask, RunnerDevice.runner_id)
            .join(RunnerDevice, RunnerDevice.id == RunnerTask.device_id)
            .where(RunnerTask.enterprise_id == enterprise_id)
            .order_by(RunnerTask.created_at.desc())
            .limit(limit)
        )
        return [self.task_to_dict(task, runner_id) for task, runner_id in result.all()]

    async def get_task(
        self, db: AsyncSession, task_id: str, enterprise_id: str
    ) -> tuple[RunnerTask, str]:
        result = await db.execute(
            select(RunnerTask, RunnerDevice.runner_id)
            .join(RunnerDevice, RunnerDevice.id == RunnerTask.device_id)
            .where(
                RunnerTask.task_id == task_id,
                RunnerTask.enterprise_id == enterprise_id,
            )
        )
        row = result.first()
        if row is None:
            raise PhysicalProtocolError("RUNNER_V2_TASK_NOT_FOUND", "物理任务不存在", 404)
        task, runner_id = row
        return task, runner_id

    @staticmethod
    def _transition(task: RunnerTask, state: TaskState) -> None:
        if state not in _ALLOWED_TASK_TRANSITIONS.get(TaskState(task.state), frozenset()):
            raise PhysicalProtocolError(
                "RUNNER_V2_TASK_STATE_INVALID",
                f"任务状态不可从 {task.state} 迁移到 {state.value}",
                409,
            )
        task.state = state.value
        task.updated_at = utcnow()

    # ------------------------------------------------------------
    # 视窗帧
    # ------------------------------------------------------------

    async def push_frame(
        self,
        db: AsyncSession,
        *,
        device: RunnerDevice,
        task_id: str,
        kind: str,
        width: int,
        height: int,
        payload_b64: str,
    ) -> dict[str, Any]:
        """归档一条端侧视窗灰度帧（关键帧或变化帧）。"""
        task = await self._require_task_for_device(db, device, task_id)
        try:
            raw = base64.b64decode(payload_b64, validate=True)
        except Exception as exc:  # noqa: BLE001 - 统一收敛为协议错误
            raise PhysicalProtocolError(
                "RUNNER_V2_FRAME_INVALID", "视窗帧载荷不是合法 base64", 422
            ) from exc
        if len(raw) > MAX_FRAME_PAYLOAD_BYTES:
            raise PhysicalProtocolError(
                "RUNNER_V2_FRAME_TOO_LARGE",
                f"视窗帧超过 {MAX_FRAME_PAYLOAD_BYTES} 字节上限",
                422,
            )
        if kind not in ("keyframe", "delta"):
            raise PhysicalProtocolError(
                "RUNNER_V2_FRAME_KIND", "视窗帧类型必须是 keyframe 或 delta", 422
            )
        if width <= 0 or height <= 0 or width * height != len(raw):
            raise PhysicalProtocolError(
                "RUNNER_V2_FRAME_GEOMETRY", "视窗帧尺寸与灰度像素数量不一致", 422
            )

        seq = task.frame_seq + 1
        frame = RunnerTaskFrame(
            task_id=task_id,
            seq=seq,
            kind=kind,
            width=width,
            height=height,
            payload_b64=payload_b64,
            digest=hashlib.sha256(raw).hexdigest()[:32],
            byte_size=len(raw),
        )
        db.add(frame)
        task.frame_seq = seq
        task.updated_at = utcnow()
        # 环形上界：超出即删最旧帧，保证单任务帧数有界。
        overflow = seq - MAX_FRAMES_PER_TASK
        if overflow > 0:
            await db.execute(
                delete(RunnerTaskFrame).where(
                    RunnerTaskFrame.task_id == task_id,
                    RunnerTaskFrame.seq <= overflow,
                )
            )
        await db.commit()
        return self.frame_to_dict(frame)

    async def read_frames(
        self, db: AsyncSession, task_id: str, since_seq: int = 0
    ) -> list[dict[str, Any]]:
        result = await db.execute(
            select(RunnerTaskFrame)
            .where(RunnerTaskFrame.task_id == task_id, RunnerTaskFrame.seq > since_seq)
            .order_by(RunnerTaskFrame.seq.asc())
        )
        return [self.frame_to_dict(frame) for frame in result.scalars().all()]

    # ------------------------------------------------------------
    # 执行结果（幂等回执）
    # ------------------------------------------------------------

    async def report_result(
        self,
        db: AsyncSession,
        *,
        device: RunnerDevice,
        task_id: str,
        ok: bool,
        data: Optional[dict[str, Any]] = None,
        error: Optional[str] = None,
        receipt_id: Optional[str] = None,
        step_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """端侧回传物理执行结果。

        并发与幂等语义（AUD-18）：

        * 记账前对任务行加 ``SELECT ... FOR UPDATE``（SQLite 忽略该子句，
          PostgreSQL 上由行锁串行化同一任务的并发回执），避免两个 API 实例
          同时读到"未记账"并各自写一条终态审计；
        * 幂等键是**真实 step 身份**：``step_id`` 必须属于该任务的 step 集合；
          已确认的 step 重发只回放结果，不重复记账、不重复审计。任意新
          ``receipt_id`` 字符串不会被当成步骤计数；
        * 账本不做滑动淘汰（上界 = 任务 step 数），淘汰会让重发的旧回执
          被当成新回执二次记账；
        * 任一 step 失败立即进入终态 ``failed``，不会被后续 step 的成功覆盖；
        * 全部 step 确认完毕才进入终态；非可执行状态（blocked / awaiting_2fa /
          cancelled）与已终结任务一律拒绝新回执。

        ``step_id`` 为空表示"整任务结果"（旧契约），直接进入终态；此后该任务
        不再接受任何回执。
        """
        # 行锁：同任务的并发回执必须串行，否则两个实例会各自写终态与审计。
        locked = await db.execute(
            select(RunnerTask)
            .where(
                RunnerTask.task_id == task_id,
                RunnerTask.device_id == device.id,
            )
            .with_for_update()
        )
        task = locked.scalar_one_or_none()
        if task is None:
            raise PhysicalProtocolError("RUNNER_V2_TASK_NOT_FOUND", "物理任务不存在", 404)

        valid_steps = set(_task_step_ids(task))
        confirmed = _confirmed_step_ids(task.result)
        identity = (step_id or "").strip()

        if identity and identity not in valid_steps:
            raise PhysicalProtocolError(
                "RUNNER_V2_STEP_UNKNOWN",
                f"回执对应的 step 不属于该任务: {identity}",
                422,
            )

        if identity and identity in confirmed:
            # 动作已执行、ack 丢失后重连：回放既有结果，不重复记账。
            return self.task_to_dict(task, device.runner_id)

        if task.state not in (TaskState.DISPATCHED.value, TaskState.EXECUTING.value):
            raise PhysicalProtocolError(
                "RUNNER_V2_TASK_NOT_EXECUTABLE",
                f"任务当前状态 {task.state} 不接受执行回执",
                409,
            )

        if identity:
            confirmed.add(identity)
        # 失败立即终结：否则后续 step 的成功会把失败任务覆盖成 completed。
        finished = not ok or not identity or confirmed >= valid_steps

        if finished:
            if task.state == TaskState.DISPATCHED.value:
                self._transition(task, TaskState.EXECUTING)
            self._transition(task, TaskState.COMPLETED if ok else TaskState.FAILED)
            task.completed_at = utcnow()
        elif task.state == TaskState.DISPATCHED.value:
            self._transition(task, TaskState.EXECUTING)

        task.result = {
            "ok": ok,
            "data": data or {},
            "error": mask_secrets(error) if error else None,
            RECEIPT_LEDGER_KEY: {"step_ids": sorted(confirmed)},
        }
        if receipt_id:
            task.last_receipt_id = receipt_id
        task.updated_at = utcnow()

        if finished:
            await self._record_audit(
                db,
                enterprise_id=task.enterprise_id,
                event="task_completed" if ok else "task_failed",
                decision="allow" if ok else "operation_failed",
                detail=(
                    f"任务 {task_id} 端侧执行完成"
                    if ok
                    else f"任务 {task_id} 端侧执行失败：{mask_secrets(error)}"
                ),
                task_id=task_id,
                device_id=device.id,
                runner_id=device.runner_id,
                actor=task.created_by or "runner",
            )
        else:
            await self._record_audit(
                db,
                enterprise_id=task.enterprise_id,
                event="task_step_reported",
                decision="allow",
                detail=(
                    f"任务 {task_id} 步骤 {identity} 结果已确认"
                    f"（{len(confirmed)}/{len(valid_steps)}），任务继续执行"
                ),
                task_id=task_id,
                device_id=device.id,
                runner_id=device.runner_id,
                actor=task.created_by or "runner",
            )
        await db.commit()
        return self.task_to_dict(task, device.runner_id)

    # ------------------------------------------------------------
    # 双因子确认
    # ------------------------------------------------------------

    def _new_challenge(
        self, *, task_id: str, enterprise_id: str, device_id: str, reason: str
    ) -> RunnerChallenge:
        return RunnerChallenge(
            challenge_id=f"2fa-{uuid.uuid4().hex[:12]}",
            task_id=task_id,
            enterprise_id=enterprise_id,
            device_id=device_id,
            state=ChallengeState.PENDING_ENDPOINT.value,
            reason=reason,
            device_code_encrypted=encrypt_credential(f"{secrets.randbelow(10 ** 6):06d}"),
            expires_at=utcnow() + timedelta(seconds=CHALLENGE_TTL_SECONDS),
        )

    async def _expire_if_needed(
        self, db: AsyncSession, challenge: RunnerChallenge
    ) -> RunnerChallenge:
        if challenge.state in TERMINAL_CHALLENGE_STATES:
            return challenge
        if _utc(challenge.expires_at) > utcnow():
            return challenge
        challenge.state = ChallengeState.EXPIRED.value
        challenge.updated_at = utcnow()
        task = await db.get(RunnerTask, challenge.task_id)
        if task is not None and task.state == TaskState.AWAITING_2FA.value:
            task.state = TaskState.CANCELLED.value
            task.updated_at = utcnow()
        await self._record_audit(
            db,
            enterprise_id=challenge.enterprise_id,
            event="challenge_expired",
            decision="timeout",
            detail=f"高危操作双因子确认超时作废（任务 {challenge.task_id}）",
            task_id=challenge.task_id,
            device_id=challenge.device_id,
            actor="system",
        )
        await db.commit()
        return challenge

    async def get_challenge(
        self, db: AsyncSession, challenge_id: str, enterprise_id: str
    ) -> RunnerChallenge:
        challenge = await self._require_challenge_for_enterprise(
            db, challenge_id, enterprise_id
        )
        return await self._expire_if_needed(db, challenge)

    async def list_challenges(
        self, db: AsyncSession, enterprise_id: str, limit: int = 200
    ) -> list[dict[str, Any]]:
        result = await db.execute(
            select(RunnerChallenge)
            .where(RunnerChallenge.enterprise_id == enterprise_id)
            .order_by(RunnerChallenge.created_at.desc())
            .limit(limit)
        )
        challenges = result.scalars().all()
        for challenge in challenges:
            await self._expire_if_needed(db, challenge)
        return [self.challenge_to_dict(challenge) for challenge in challenges]

    async def confirm_endpoint_factor(
        self, db: AsyncSession, challenge_id: str, enterprise_id: str, actor: str
    ) -> dict[str, Any]:
        """第一道因子：云端操作意图确认。"""
        challenge = await self._require_challenge_for_enterprise(
            db, challenge_id, enterprise_id
        )
        await self._expire_if_needed(db, challenge)
        if challenge.state != ChallengeState.PENDING_ENDPOINT.value:
            raise PhysicalProtocolError(
                "RUNNER_V2_CHALLENGE_STATE_INVALID",
                f"当前状态 {challenge.state} 不接受云端确认",
                409,
            )
        challenge.state = ChallengeState.PENDING_DEVICE_CODE.value
        challenge.endpoint_confirmed_by = actor
        challenge.endpoint_confirmed_at = utcnow()
        challenge.updated_at = utcnow()
        await self._record_audit(
            db,
            enterprise_id=enterprise_id,
            event="challenge_endpoint_confirmed",
            decision="allow",
            detail=f"高危操作第一道因子已确认（操作者 {actor}），等待端侧物理确认码",
            task_id=challenge.task_id,
            device_id=challenge.device_id,
            actor=actor,
        )
        await db.commit()
        return self.challenge_to_dict(challenge)

    async def reject_challenge(
        self, db: AsyncSession, challenge_id: str, enterprise_id: str, actor: str
    ) -> dict[str, Any]:
        """人工否决高危操作，任务立即取消且不下发端侧。"""
        challenge = await self._require_challenge_for_enterprise(
            db, challenge_id, enterprise_id
        )
        await self._expire_if_needed(db, challenge)
        if challenge.state in (ChallengeState.APPROVED.value, ChallengeState.REJECTED.value):
            raise PhysicalProtocolError(
                "RUNNER_V2_CHALLENGE_STATE_INVALID", f"当前状态 {challenge.state} 不可否决", 409
            )
        challenge.state = ChallengeState.REJECTED.value
        challenge.updated_at = utcnow()
        task = await db.get(RunnerTask, challenge.task_id)
        if task is not None and task.state == TaskState.AWAITING_2FA.value:
            self._transition(task, TaskState.CANCELLED)
        await self._record_audit(
            db,
            enterprise_id=enterprise_id,
            event="challenge_rejected",
            decision="deny",
            detail=f"高危操作被 {actor} 否决，任务取消",
            task_id=challenge.task_id,
            device_id=challenge.device_id,
            actor=actor,
        )
        await db.commit()
        return self.challenge_to_dict(challenge)

    async def verify_device_factor(
        self,
        db: AsyncSession,
        *,
        device: RunnerDevice,
        challenge_id: str,
        device_code: str,
    ) -> dict[str, Any]:
        """第二道因子：端侧物理确认码，只接受**任务属主设备**提交。"""
        challenge = await self._require_challenge_for_device(db, device, challenge_id)
        await self._expire_if_needed(db, challenge)
        if challenge.state != ChallengeState.PENDING_DEVICE_CODE.value:
            raise PhysicalProtocolError(
                "RUNNER_V2_CHALLENGE_STATE_INVALID",
                f"当前状态 {challenge.state} 不接受端侧确认码",
                409,
            )
        expected = self._decrypt_device_code(challenge)
        if not hmac.compare_digest(str(device_code).strip(), expected):
            await self._record_audit(
                db,
                enterprise_id=challenge.enterprise_id,
                event="challenge_device_code_rejected",
                decision="deny",
                detail="端侧物理确认码校验失败",
                task_id=challenge.task_id,
                device_id=device.id,
                actor="runner",
            )
            await db.commit()
            raise PhysicalProtocolError(
                "RUNNER_V2_DEVICE_CODE_INVALID", "端侧物理确认码不正确", 401
            )

        challenge.state = ChallengeState.APPROVED.value
        challenge.device_verified_at = utcnow()
        challenge.updated_at = utcnow()
        task = await db.get(RunnerTask, challenge.task_id)
        if task is not None and task.state == TaskState.AWAITING_2FA.value:
            self._transition(task, TaskState.DISPATCHED)
        await self._record_audit(
            db,
            enterprise_id=challenge.enterprise_id,
            event="challenge_approved",
            decision="allow",
            detail="双因子确认通过，任务放行至端侧执行",
            task_id=challenge.task_id,
            device_id=device.id,
            runner_id=device.runner_id,
            actor="runner",
        )
        await db.commit()

        result = self.challenge_to_dict(challenge)
        if task is not None:
            result["task"] = self.task_to_dict(task, device.runner_id)
        return result

    # ------------------------------------------------------------
    # 审计留痕
    # ------------------------------------------------------------

    async def _record_audit(
        self,
        db: AsyncSession,
        *,
        enterprise_id: Optional[str],
        event: str,
        decision: str,
        detail: str,
        task_id: Optional[str] = None,
        device_id: Optional[str] = None,
        runner_id: Optional[str] = None,
        actor: str = "",
    ) -> RunnerAuditEntry:
        entry = RunnerAuditEntry(
            trace_id=f"trace-{uuid.uuid4().hex[:16]}",
            enterprise_id=enterprise_id,
            task_id=task_id,
            device_id=device_id,
            runner_id=runner_id,
            actor=actor,
            event=event,
            decision=decision,
            detail=mask_secrets(detail),
        )
        db.add(entry)
        return entry

    async def read_audit(
        self, db: AsyncSession, enterprise_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        capped = max(1, min(int(limit), MAX_AUDIT_ENTRIES))
        result = await db.execute(
            select(RunnerAuditEntry)
            .where(
                or_(
                    RunnerAuditEntry.enterprise_id == enterprise_id,
                    RunnerAuditEntry.enterprise_id.is_(None),
                )
            )
            .order_by(RunnerAuditEntry.created_at.desc())
            .limit(capped)
        )
        return [self.audit_to_dict(entry) for entry in result.scalars().all()]

    # ------------------------------------------------------------
    # 有界保留（AUD-18）
    # ------------------------------------------------------------

    async def purge_expired(self, db: AsyncSession) -> dict[str, int]:
        """按模块 docstring 的保留表清理过期数据，返回各表删除行数。"""
        now = utcnow()
        report: dict[str, int] = {}

        # 1) 终态任务超过 FRAME_RETENTION_HOURS 后清空其视窗帧
        stale_task_ids = select(RunnerTask.task_id).where(
            RunnerTask.state.in_(TERMINAL_TASK_STATES),
            RunnerTask.updated_at < now - timedelta(hours=FRAME_RETENTION_HOURS),
        )
        result = await db.execute(
            delete(RunnerTaskFrame).where(RunnerTaskFrame.task_id.in_(stale_task_ids))
        )
        report["runner_task_frames"] = int(result.rowcount or 0)

        # 2) 先清理已终结挑战（其外键指向任务），再清理终态任务
        result = await db.execute(
            delete(RunnerChallenge).where(
                RunnerChallenge.state.in_(TERMINAL_CHALLENGE_STATES),
                RunnerChallenge.updated_at < now - timedelta(days=CHALLENGE_RETENTION_DAYS),
            )
        )
        report["runner_challenges"] = int(result.rowcount or 0)

        result = await db.execute(
            delete(RunnerTask).where(
                RunnerTask.state.in_(TERMINAL_TASK_STATES),
                RunnerTask.updated_at < now - timedelta(days=TASK_RETENTION_DAYS),
            )
        )
        report["runner_tasks"] = int(result.rowcount or 0)

        result = await db.execute(
            delete(RunnerAuditEntry).where(
                RunnerAuditEntry.created_at < now - timedelta(days=AUDIT_RETENTION_DAYS)
            )
        )
        report["runner_audit_entries"] = int(result.rowcount or 0)

        result = await db.execute(
            delete(RunnerDeviceCommand).where(
                RunnerDeviceCommand.expires_at < now - timedelta(days=1)
            )
        )
        report["runner_device_commands"] = int(result.rowcount or 0)

        result = await db.execute(
            delete(RunnerDeviceCredential).where(
                or_(
                    RunnerDeviceCredential.revoked_at
                    < now - timedelta(days=CREDENTIAL_RETENTION_DAYS),
                    RunnerDeviceCredential.expires_at
                    < now - timedelta(days=CREDENTIAL_RETENTION_DAYS),
                )
            )
        )
        report["runner_device_credentials"] = int(result.rowcount or 0)

        result = await db.execute(
            delete(RunnerDeviceToken).where(
                or_(
                    RunnerDeviceToken.expires_at < now - timedelta(days=TOKEN_RETENTION_DAYS),
                    (RunnerDeviceToken.revoked_at.isnot(None))
                    & (RunnerDeviceToken.revoked_at < now - timedelta(days=TOKEN_RETENTION_DAYS)),
                )
            )
        )
        report["runner_device_tokens"] = int(result.rowcount or 0)

        await db.commit()
        self._last_purge_at = time.time()
        return report

    async def _purge_if_due(self, db: AsyncSession) -> None:
        """心跳触发的节流清理：默认 10 分钟一次，避免每跳全表扫描。"""
        if (time.time() - self._last_purge_at) < PURGE_INTERVAL_SECONDS:
            return
        await self.purge_expired(db)

    def reset_purge_throttle(self) -> None:
        """重置清理节流（仅供单测与运维手工触发使用）。"""
        self._last_purge_at = 0.0


#: 协议中枢单例（无状态：所有实体都在数据库里）。
runner_v2_protocol = RunnerV2Protocol()
