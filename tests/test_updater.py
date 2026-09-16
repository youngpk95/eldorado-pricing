"""Test cho updater.py (tự cập nhật từ GitHub) — mock _run_git hoàn toàn
(không gọi git thật/không cần mạng), kiểm tra logic so sánh HEAD local vs
origin/<branch> và việc lỗi mạng/git raise đúng exception type (main.py bắt
riêng UpdateCheckError/UpdatePullError để ghi rõ lên ô version trên sheet
thay vì âm thầm coi như "không có gì mới" như hành vi cũ)."""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import updater  # noqa: E402


def test_check_for_update_true_when_local_differs_from_remote(monkeypatch):
    calls = []

    def fake_run_git(*args):
        calls.append(args)
        if args == ("rev-parse", "HEAD"):
            return MagicMock(stdout="aaa111\n")
        if args == ("rev-parse", "origin/master"):
            return MagicMock(stdout="bbb222\n")
        return MagicMock(stdout="")

    monkeypatch.setattr(updater, "_run_git", fake_run_git)

    assert updater.check_for_update("master") is True
    assert calls[0] == ("fetch", "origin", "master", "--quiet")


def test_check_for_update_false_when_up_to_date(monkeypatch):
    def fake_run_git(*args):
        if args[0] == "rev-parse":
            return MagicMock(stdout="same-sha\n")
        return MagicMock(stdout="")

    monkeypatch.setattr(updater, "_run_git", fake_run_git)

    assert updater.check_for_update("master") is False


def test_check_for_update_raises_update_check_error_on_git_error(monkeypatch):
    def fake_run_git(*args):
        raise Exception("máy tạm mất mạng")

    monkeypatch.setattr(updater, "_run_git", fake_run_git)

    with pytest.raises(updater.UpdateCheckError):
        updater.check_for_update("master")


def test_pull_update_returns_none_on_success(monkeypatch):
    monkeypatch.setattr(updater, "_run_git", lambda *a: MagicMock(stdout=""))

    assert updater.pull_update("master") is None


def test_pull_update_raises_update_pull_error_on_conflict(monkeypatch):
    def fake_run_git(*args):
        raise Exception("not a fast forward")

    monkeypatch.setattr(updater, "_run_git", fake_run_git)

    with pytest.raises(updater.UpdatePullError):
        updater.pull_update("master")


def test_get_version_info_returns_count_and_short_hash(monkeypatch):
    def fake_run_git(*args):
        if args == ("rev-list", "--count", "HEAD"):
            return MagicMock(stdout="47\n")
        if args == ("rev-parse", "--short", "HEAD"):
            return MagicMock(stdout="f4ef8ee\n")
        raise AssertionError(f"unexpected git args: {args}")

    monkeypatch.setattr(updater, "_run_git", fake_run_git)

    assert updater.get_version_info() == ("47", "f4ef8ee")


def test_get_version_info_falls_back_on_error(monkeypatch):
    def fake_run_git(*args):
        raise Exception("git không tồn tại")

    monkeypatch.setattr(updater, "_run_git", fake_run_git)

    assert updater.get_version_info() == ("?", "?")
