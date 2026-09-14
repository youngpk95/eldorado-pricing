"""Test cho config.load_google_service_account — đặc biệt cách MẶC ĐỊNH mới
(bỏ file .json vào folder service_account/, không cần convert/dán JSON thành
1 dòng vào .env — nhân viên/chính chủ rất dễ làm hỏng khi tự convert) và xác
nhận cách cũ (biến GOOGLE_SERVICE_ACCOUNT_JSON) vẫn hoạt động song song,
được ưu tiên trước folder khi cả 2 cùng tồn tại."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

SAMPLE_CREDS = {"type": "service_account", "project_id": "p", "client_email": "sa@p.iam.gserviceaccount.com"}


def test_loads_from_folder_when_exactly_one_json_file(monkeypatch, tmp_path):
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
    monkeypatch.setattr(config, "SERVICE_ACCOUNT_DIR", tmp_path)
    (tmp_path / "my-service-account.json").write_text(json.dumps(SAMPLE_CREDS), encoding="utf-8")

    assert config.load_google_service_account() == SAMPLE_CREDS


def test_raises_clear_error_when_folder_empty_and_no_env_var(monkeypatch, tmp_path):
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
    monkeypatch.setattr(config, "SERVICE_ACCOUNT_DIR", tmp_path)

    with pytest.raises(RuntimeError, match="service_account"):
        config.load_google_service_account()


def test_raises_clear_error_when_folder_has_multiple_json_files(monkeypatch, tmp_path):
    """Không được ÂM THẦM chọn đại 1 file — dễ dùng nhầm credential cũ/của
    dự án khác nếu ai đó quên xoá file thừa trong folder."""
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
    monkeypatch.setattr(config, "SERVICE_ACCOUNT_DIR", tmp_path)
    (tmp_path / "a.json").write_text(json.dumps(SAMPLE_CREDS), encoding="utf-8")
    (tmp_path / "b.json").write_text(json.dumps(SAMPLE_CREDS), encoding="utf-8")

    with pytest.raises(RuntimeError, match="ĐÚNG 1 file"):
        config.load_google_service_account()


def test_env_var_inline_json_takes_priority_over_folder(monkeypatch, tmp_path):
    """Cách cũ (dán JSON 1 dòng vào .env) vẫn phải hoạt động cho ai đang dùng
    kiểu này — và được ưu tiên trước folder nếu cả 2 cùng có, để không đổi
    hành vi bất ngờ với người đã cấu hình sẵn."""
    monkeypatch.setattr(config, "SERVICE_ACCOUNT_DIR", tmp_path)
    (tmp_path / "should-be-ignored.json").write_text(json.dumps({"other": "value"}), encoding="utf-8")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", json.dumps(SAMPLE_CREDS))

    assert config.load_google_service_account() == SAMPLE_CREDS


def test_env_var_file_path_still_works(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SERVICE_ACCOUNT_DIR", tmp_path)
    creds_file = tmp_path / "custom-name.json"
    creds_file.write_text(json.dumps(SAMPLE_CREDS), encoding="utf-8")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", str(creds_file))

    assert config.load_google_service_account() == SAMPLE_CREDS
