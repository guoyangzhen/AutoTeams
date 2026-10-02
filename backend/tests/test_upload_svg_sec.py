import pytest
from app.utils.upload_validation import detect_file_type, validate_content_type, FileUploadError
from app.utils.error_codes import ErrorCode


def test_svg_upload_is_strictly_blocked():
    # P1-5: 显式阻止 .svg 上传以防御同源存储型 XSS
    with pytest.raises(FileUploadError) as exc_info:
        detect_file_type("malicious.svg")
    assert exc_info.value.args[0] == ErrorCode.FILE_TYPE_NOT_ALLOWED

    with pytest.raises(FileUploadError) as exc_info:
        detect_file_type("compressed.svgz")
    assert exc_info.value.args[0] == ErrorCode.FILE_TYPE_NOT_ALLOWED


def test_svg_mime_type_is_not_allowed():
    # image/svg+xml 从 image 白名单中剥离
    with pytest.raises(FileUploadError) as exc_info:
        validate_content_type("test.png", "image/svg+xml", "image")
    assert exc_info.value.args[0] == ErrorCode.FILE_TYPE_NOT_ALLOWED


def test_standard_images_allowed():
    # 正常 PNG/JPEG 仍然放行
    assert detect_file_type("photo.png") == "image"
    assert detect_file_type("banner.jpg") == "image"
    validate_content_type("photo.png", "image/png", "image")
    validate_content_type("banner.jpg", "image/jpeg", "image")
