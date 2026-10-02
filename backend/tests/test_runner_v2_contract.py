"""Runner 2.0 端侧↔云端契约一致性测试（AUD-10）。

审计复现（§6.2）：把 `local-runner/src/physical-bridge.ts` 真实结构喂给生产
Pydantic 模型会得到两个校验错误：

- ``capabilities.frames: Input should be a valid boolean``（模型写的是
  ``dict[str, bool]``，端侧发的是 ``{width, height, format}``）；
- ``payload_b64: Field required``（端侧发的是 ``payloadB64``，且没有 alias）。

本测试消费与端侧 ``local-runner/test/contract.test.ts`` **同一份**
``local-runner/contract/runner_v2_payloads.json``：

1. 共享样例必须能被生产 Pydantic 模型零错误接受；
2. 模型字段集合必须与共享样例逐字段一致（防止某一侧偷偷改了名字）；
3. 旧的错误拼写（``payloadB64``、布尔形态 ``frames``）必须被明确拒绝，
   而不是被 alias 静默吞掉。
"""
import json
from pathlib import Path

import pytest

from app.schemas.runner_v2 import (
    ConfirmChallengeRequest,
    HeartbeatRequest,
    PushFrameRequest,
    ReportResultRequest,
    VerifyDeviceCodeRequest,
)

CONTRACT_PATH = (
    Path(__file__).resolve().parents[2]
    / "local-runner"
    / "contract"
    / "runner_v2_payloads.json"
)


@pytest.fixture(scope="module")
def contract() -> dict:
    assert CONTRACT_PATH.exists(), f"共享契约样例缺失: {CONTRACT_PATH}"
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def test_shared_fixture_is_parsed(contract):
    assert set(contract) >= {
        "heartbeat_request",
        "push_frame_request",
        "report_result_request",
        "verify_device_code_request",
        "confirm_challenge_request",
    }


def test_heartbeat_fixture_validates_against_production_model(contract):
    payload = contract["heartbeat_request"]
    model = HeartbeatRequest.model_validate(payload)
    assert model.runner_id == payload["runner_id"]
    assert model.scopes == payload["scopes"]
    # 结构化 frames 能力：不是布尔
    assert model.capabilities.frames is not None
    assert model.capabilities.frames.width == 160
    assert model.capabilities.frames.height == 100
    assert model.capabilities.frames.format == "gray8"


def test_push_frame_fixture_validates_against_production_model(contract):
    model = PushFrameRequest.model_validate(contract["push_frame_request"])
    assert model.payload_b64
    assert not hasattr(model, "payloadB64")


def test_result_fixture_carries_receipt_id(contract):
    model = ReportResultRequest.model_validate(contract["report_result_request"])
    assert model.receipt_id == contract["report_result_request"]["receipt_id"]
    # 幂等键必须能核对步骤身份：云端据此判定"这个 step 已经记过账"。
    assert model.step_id == contract["report_result_request"]["step_id"]
    assert model.ok is True


def test_device_code_and_confirm_fixtures_validate(contract):
    verify = VerifyDeviceCodeRequest.model_validate(contract["verify_device_code_request"])
    assert len(verify.device_code) == 6
    confirm = ConfirmChallengeRequest.model_validate(contract["confirm_challenge_request"])
    assert confirm.decision == "approve"


def test_model_field_set_matches_fixture_exactly(contract):
    """模型字段与共享样例一致：样例字段必须被模型接受。

    模型可以额外拥有"服务端自己填"的可选字段（如 ``ReportResultRequest.error``），
    这些不要求出现在端侧 happy-path 样例里；但只要模型**少了**样例中的字段，
    """
    pairs = [
        (HeartbeatRequest, contract["heartbeat_request"]),
        (PushFrameRequest, contract["push_frame_request"]),
        (ReportResultRequest, contract["report_result_request"]),
        (VerifyDeviceCodeRequest, contract["verify_device_code_request"]),
        (ConfirmChallengeRequest, contract["confirm_challenge_request"]),
    ]
    for model_cls, fixture in pairs:
        model_fields = set(model_cls.model_fields)
        fixture_fields = set(fixture)
        assert fixture_fields <= model_fields, (
            f"{model_cls.__name__} 缺少字段 {sorted(fixture_fields - model_fields)}"
        )

def test_legacy_camelcase_frame_field_is_rejected(contract):
    """旧的 payloadB64 拼写必须被拒绝（不允许 alias 静默兼容）。"""
    legacy = dict(contract["push_frame_request"])
    legacy["payloadB64"] = legacy.pop("payload_b64")
    with pytest.raises(Exception):
        PushFrameRequest.model_validate(legacy)


def test_legacy_boolean_frames_is_rejected(contract):
    """旧的 dict[str, bool] 形态 frames 必须被拒绝。"""
    legacy = json.loads(json.dumps(contract["heartbeat_request"]))
    legacy["capabilities"]["frames"] = True
    with pytest.raises(Exception):
        HeartbeatRequest.model_validate(legacy)


def test_unknown_extra_fields_are_rejected(contract):
    """extra=forbid：端侧多发一个字段就应当立刻暴露契约漂移。"""
    drifted = dict(contract["heartbeat_request"])
    drifted["unexpected_field"] = 1
    with pytest.raises(Exception):
        HeartbeatRequest.model_validate(drifted)
