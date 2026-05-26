"""HTTP webhook dispatcher for device events.

Receives events from the EventBus and POSTs them as JSON to configured
webhook URLs.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field

import aiohttp

from .event_bus import DeviceEvent

logger = logging.getLogger(__name__)


@dataclass
class WebhookTarget:
    """A single webhook endpoint."""

    url: str
    token: str = ""
    event_types: set[str] | None = field(default=None)


class WebhookDispatcher:
    """Dispatch device events to HTTP webhook endpoints."""

    def __init__(
        self,
        targets: list[WebhookTarget],
        timeout: float = 5.0,
    ) -> None:
        self._targets = targets
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def handle_event(self, event: DeviceEvent) -> None:
        """EventBus subscriber callback.  POSTs the event to all matching targets."""
        session = await self._ensure_session()
        payload = {
            "event": event.event,
            "timestamp_us": event.timestamp_us,
            "device_id": event.device_id,
            "data": event.data,
            "gateway_time": time.time(),
        }
        body = json.dumps(payload)

        for target in self._targets:
            if target.event_types and event.event not in target.event_types:
                continue
            try:
                headers: dict[str, str] = {"Content-Type": "application/json"}
                if target.token:
                    headers["Authorization"] = f"Bearer {target.token}"
                async with session.post(
                    target.url, data=body, headers=headers
                ) as resp:
                    if resp.status >= 400:
                        logger.warning(
                            "Webhook %s returned %d for %s event",
                            target.url,
                            resp.status,
                            event.event,
                        )
            except Exception:
                logger.exception(
                    "Webhook %s failed for %s event",
                    target.url,
                    event.event,
                )

    async def close(self) -> None:
        """Close the underlying HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()


def parse_webhook_config() -> list[WebhookTarget]:
    """Build webhook targets from ``STACKCHAN_WEBHOOK_*`` env vars."""
    urls_raw = os.getenv("STACKCHAN_WEBHOOK_URLS", "")
    if not urls_raw:
        return []
    urls = [u.strip() for u in urls_raw.split(",") if u.strip()]
    if not urls:
        return []

    tokens_raw = os.getenv("STACKCHAN_WEBHOOK_TOKENS", "")
    tokens = [t.strip() for t in tokens_raw.split(",")] if tokens_raw else []

    events_raw = os.getenv("STACKCHAN_WEBHOOK_EVENTS", "")
    event_filter: set[str] | None = (
        {e.strip() for e in events_raw.split(",") if e.strip()}
        if events_raw
        else None
    )

    targets: list[WebhookTarget] = []
    for i, url in enumerate(urls):
        token = tokens[i] if i < len(tokens) else ""
        targets.append(
            WebhookTarget(url=url, token=token, event_types=event_filter)
        )
    return targets
