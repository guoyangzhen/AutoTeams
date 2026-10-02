from typing import Any, Optional
from fastapi.responses import JSONResponse


def success_response(data: Any = None, message: Optional[str] = None) -> dict:
    response = {"success": True, "data": data}
    if message:
        response["message"] = message
    return response


def error_response(message: str, code: int = 400) -> JSONResponse:
    return JSONResponse(
        status_code=code,
        content={"success": False, "message": message, "data": None}
    )
