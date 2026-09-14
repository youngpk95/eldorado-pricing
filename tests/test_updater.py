"""Test cho updater.py (tự cập nhật từ GitHub) — mock _run_git hoàn toàn
(không gọi git thật/không cần mạng), kiểm tra logic so sánh HEAD local vs
origin/<branch> và việc pull không bao giờ raise ra ngoài (main.py dựa vào
điều này để không bị crash vòng lặp chính khi mất mạng/conflict)."""
import sys
from pathlib import Path
from unittest.mock import MagicMock

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


def test_check_for_update_false_and_no_raise_on_git_error(monkeypatch):
    def fake_run_git(*args):
        raise Exception("máy tạm mất mạng")

    monkeypatch.setattr(updater, "_run_git", fake_run_git)

    assert updater.check_for_update("master") is False


def test_pull_update_returns_true_on_success(monkeypatch):
    monkeypatch.setattr(updater, "_run_git", lambda *a: MagicMock(stdout=""))

    assert updater.pull_update("master") is True


def test_pull_update_returns_false_and_no_raise_on_conflict(monkeypatch):
    def fake_run_git(*args):
        raise Exception("not a fast forward")

    monkeypatch.setattr(updater, "_run_git", fake_run_git)

    assert updater.pull_update("master") is False
