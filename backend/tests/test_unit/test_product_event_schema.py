"""产品行为事件的隐私边界回归测试。"""
import pytest
from pydantic import ValidationError

from app.schemas.product_event import ProductEventRequest


def test_product_event_accepts_only_minimal_company_overview_fields():
    event = ProductEventRequest(
        event_name="company_brief_action_clicked",
        session_id="session_2uQoEe4q8m3Yw7P1",
        journey_state="needs_compile",
        action="compile",
        reduced_motion=True,
    )

    assert event.surface == "company_overview"
    assert event.action == "compile"
    assert event.reduced_motion is True
    # 请求体没有 metadata、文本内容、资源 ID 或路径等自由扩展字段。
    assert set(event.model_dump()).issubset(
        {"event_name", "session_id", "surface", "journey_state", "action", "tour_step", "reduced_motion"}
    )


def test_product_event_rejects_unknown_event_name_and_short_session_id():
    with pytest.raises(ValidationError):
        ProductEventRequest(
            event_name="company_brief_free_text",  # type: ignore[arg-type]
            session_id="session_2uQoEe4q8m3Yw7P1",
        )

    with pytest.raises(ValidationError):
        ProductEventRequest(
            event_name="company_brief_viewed",
            session_id="too-short",
        )


def test_product_event_rejects_out_of_range_tour_step_and_unapproved_action():
    with pytest.raises(ValidationError):
        ProductEventRequest(
            event_name="company_tour_step_viewed",
            session_id="session_2uQoEe4q8m3Yw7P1",
            tour_step=5,
        )

    with pytest.raises(ValidationError):
        ProductEventRequest(
            event_name="company_brief_action_clicked",
            session_id="session_2uQoEe4q8m3Yw7P1",
            action="shell_execute",  # type: ignore[arg-type]
        )
