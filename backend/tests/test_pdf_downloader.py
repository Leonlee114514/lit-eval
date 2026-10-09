"""PDF 下载通道测试（全部不联网）。

这个模块过去**零测试覆盖**：347 行里包含"只把 %PDF- 开头的内容落盘"、
"四通道顺序"、"skip_legal / verify_tls 开关"这些判断，过去只能靠线上试。
这里用替身函数把每个通道的行为钉住 —— 尤其"HTML 落地页绝不入库"那条：
它一旦失灵，整批评估会拿一张网页当全文读。

CLI（根目录 download_pdf.py）现在只是 backend 实现的薄包装，共用同一套行为。
"""
from __future__ import annotations

import pytest

from app.services.content import pdf_downloader


@pytest.fixture
def channels(monkeypatch):
    """把四个通道换成可控替身：按名字查表返回 payload，并记录调用顺序。"""
    payload: dict[str, bytes | None] = {
        "unpaywall": None, "openalex": None, "scihub": None, "browser": None,
    }
    calls: list[str] = []

    def _make(name):
        def _inner(*args, **kwargs):
            calls.append(name)
            return payload[name], f"{name}-reason"
        return _inner

    for name in payload:
        monkeypatch.setattr(pdf_downloader, f"_try_{name}", _make(name))
    return payload, calls


def test_first_successful_channel_wins_and_stops_early(channels, tmp_path):
    payload, calls = channels
    payload["unpaywall"] = b"%PDF-1.7 real pdf"
    dest = tmp_path / "a.pdf"

    res = pdf_downloader.download_pdf("10.1/x", dest, email="e@x.com")

    assert res.ok and res.channel == "unpaywall"
    assert dest.read_bytes() == b"%PDF-1.7 real pdf"
    assert calls == ["unpaywall"], "命中后不该再试后面的通道"


def test_all_channels_fail_reports_every_reason(channels, tmp_path):
    payload, calls = channels

    res = pdf_downloader.download_pdf("10.1/x", tmp_path / "a.pdf", email="e@x.com")

    assert not res.ok
    assert calls == ["unpaywall", "openalex", "scihub", "browser"]
    for name in ("unpaywall", "openalex", "scihub", "browser"):
        assert f"{name}: {name}-reason" in res.reason


def test_skip_legal_runs_only_scihub(channels, tmp_path):
    payload, calls = channels
    payload["scihub"] = b"%PDF-1.4"

    res = pdf_downloader.download_pdf(
        "10.1/x", tmp_path / "a.pdf", email="e@x.com", skip_legal=True
    )

    assert res.ok and res.channel == "scihub"
    assert calls == ["scihub"], "--scihub-only 不该碰合法通道"


def test_disabling_scihub_skips_that_channel(channels, tmp_path):
    payload, calls = channels
    payload["browser"] = b"%PDF-1.5"

    res = pdf_downloader.download_pdf(
        "10.1/x", tmp_path / "a.pdf", email="e@x.com", scihub_enabled=False
    )

    assert res.ok and res.channel == "browser"
    assert "scihub" not in calls


def test_non_pdf_payload_is_rejected_and_not_written(channels, tmp_path):
    payload, _ = channels
    payload["unpaywall"] = b"<html>landing page</html>"
    dest = tmp_path / "a.pdf"

    res = pdf_downloader.download_pdf("10.1/x", dest, email="e@x.com")

    assert not res.ok
    assert "非 PDF" in (res.reason or "")
    assert not dest.exists(), "HTML 落地页绝不能写进 fulltext_path"


def test_all_channels_disabled_still_reports_a_reason(tmp_path):
    res = pdf_downloader.download_pdf(
        "10.1/x", tmp_path / "a.pdf", email="e@x.com",
        skip_legal=True, scihub_enabled=False, use_browser=False,
    )
    assert not res.ok and res.reason


def test_make_session_honours_proxy_and_verify_tls():
    sess = pdf_downloader._make_session("http://127.0.0.1:7890", verify_tls=False)
    assert sess.proxies["http"] == "http://127.0.0.1:7890"
    assert sess.verify is False

    plain = pdf_downloader._make_session("")
    assert plain.verify is True
    assert not plain.proxies
