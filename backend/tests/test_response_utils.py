"""P3-5: app/utils/response.py 覆盖率补充测试。"""
from fastapi.responses import JSONResponse

from app.utils.response import success_response, error_response


class TestSuccessResponse:
    """success_response 测试。"""

    def test_with_data_only(self):
        resp = success_response({"id": 1})
        assert resp["success"] is True
        assert resp["data"] == {"id": 1}
        assert "message" not in resp

    def test_with_data_and_message(self):
        resp = success_response({"id": 1}, message="ok")
        assert resp["success"] is True
        assert resp["data"] == {"id": 1}
        assert resp["message"] == "ok"


class TestErrorResponse:
    """error_response 测试。"""

    def test_default_code(self):
        resp = error_response("bad request")
        assert isinstance(resp, JSONResponse)
        assert resp.status_code == 400
        body = resp.body.decode("utf-8")
        assert '"success":false' in body
        assert '"message":"bad request"' in body

    def test_custom_code(self):
        resp = error_response("not found", code=404)
        assert resp.status_code == 404
