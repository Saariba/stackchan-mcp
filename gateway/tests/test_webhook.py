"""Tests for the webhook dispatcher and configuration parser."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from stackchan_mcp.event_bus import DeviceEvent
from stackchan_mcp.webhook import WebhookDispatcher, WebhookTarget, parse_webhook_config


def _make_event(event: str = "touch", **data_kw) -> DeviceEvent:
    return DeviceEvent(
        event=event,
        timestamp_us=1_000_000,
        data=data_kw or {"gesture": "tap"},
        device_id="test-device",
    )


# ---------- parse_webhook_config ----------


class TestParseWebhookConfig:
    def test_empty(self, monkeypatch):
        monkeypatch.delenv("STACKCHAN_WEBHOOK_URLS", raising=False)
        assert parse_webhook_config() == []

    def test_single_url(self, monkeypatch):
        monkeypatch.setenv("STACKCHAN_WEBHOOK_URLS", "https://a.com/hook")
        monkeypatch.delenv("STACKCHAN_WEBHOOK_TOKENS", raising=False)
        monkeypatch.delenv("STACKCHAN_WEBHOOK_EVENTS", raising=False)
        targets = parse_webhook_config()
        assert len(targets) == 1
        assert targets[0].url == "https://a.com/hook"
        assert targets[0].token == ""
        assert targets[0].event_types is None

    def test_multiple_urls_and_tokens(self, monkeypatch):
        monkeypatch.setenv("STACKCHAN_WEBHOOK_URLS", "https://a.com,https://b.com")
        monkeypatch.setenv("STACKCHAN_WEBHOOK_TOKENS", "tok_a,tok_b")
        monkeypatch.delenv("STACKCHAN_WEBHOOK_EVENTS", raising=False)
        targets = parse_webhook_config()
        assert len(targets) == 2
        assert targets[0].token == "tok_a"
        assert targets[1].token == "tok_b"

    def test_event_filter(self, monkeypatch):
        monkeypatch.setenv("STACKCHAN_WEBHOOK_URLS", "https://a.com")
        monkeypatch.delenv("STACKCHAN_WEBHOOK_TOKENS", raising=False)
        monkeypatch.setenv("STACKCHAN_WEBHOOK_EVENTS", "touch,state_changed")
        targets = parse_webhook_config()
        assert targets[0].event_types == {"touch", "state_changed"}

    def test_fewer_tokens_than_urls(self, monkeypatch):
        monkeypatch.setenv("STACKCHAN_WEBHOOK_URLS", "https://a.com,https://b.com")
        monkeypatch.setenv("STACKCHAN_WEBHOOK_TOKENS", "tok_a")
        monkeypatch.delenv("STACKCHAN_WEBHOOK_EVENTS", raising=False)
        targets = parse_webhook_config()
        assert targets[0].token == "tok_a"
        assert targets[1].token == ""


# ---------- WebhookDispatcher ----------


@pytest.mark.asyncio
async def test_dispatch_posts_to_target():
    """Events are POSTed as JSON to target URLs."""
    target = WebhookTarget(url="https://hook.example.com/test", token="secret")
    dispatcher = WebhookDispatcher([target])

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.post = MagicMock(return_value=mock_resp)
    mock_session.closed = False
    dispatcher._session = mock_session

    await dispatcher.handle_event(_make_event())

    mock_session.post.assert_called_once()
    call_args = mock_session.post.call_args
    assert call_args[0][0] == "https://hook.example.com/test"
    body = json.loads(call_args[1]["data"])
    assert body["event"] == "touch"
    assert body["device_id"] == "test-device"
    assert body["data"]["gesture"] == "tap"
    headers = call_args[1]["headers"]
    assert headers["Authorization"] == "Bearer secret"

    await dispatcher.close()


@pytest.mark.asyncio
async def test_dispatch_filters_by_event_type():
    """Target with event_types filter skips non-matching events."""
    target = WebhookTarget(
        url="https://hook.example.com/test",
        event_types={"state_changed"},
    )
    dispatcher = WebhookDispatcher([target])

    mock_session = AsyncMock()
    mock_session.closed = False
    dispatcher._session = mock_session

    await dispatcher.handle_event(_make_event("touch"))

    mock_session.post.assert_not_called()
    await dispatcher.close()


@pytest.mark.asyncio
async def test_dispatch_handles_http_error(caplog):
    """HTTP 4xx/5xx is logged but doesn't raise."""
    target = WebhookTarget(url="https://hook.example.com/fail")
    dispatcher = WebhookDispatcher([target])

    mock_resp = AsyncMock()
    mock_resp.status = 500
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.post = MagicMock(return_value=mock_resp)
    mock_session.closed = False
    dispatcher._session = mock_session

    await dispatcher.handle_event(_make_event())
    assert "returned 500" in caplog.text

    await dispatcher.close()


@pytest.mark.asyncio
async def test_dispatch_handles_network_error(caplog):
    """Network exceptions are caught and logged."""
    target = WebhookTarget(url="https://unreachable.example.com")
    dispatcher = WebhookDispatcher([target])

    mock_session = AsyncMock()
    mock_session.post = MagicMock(side_effect=OSError("connection refused"))
    mock_session.closed = False
    dispatcher._session = mock_session

    await dispatcher.handle_event(_make_event())
    assert "failed for touch event" in caplog.text

    await dispatcher.close()
