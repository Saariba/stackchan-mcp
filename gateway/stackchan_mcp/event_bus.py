"""Lightweight async event bus for device-originated push events.

Events flow:  ESP32 firmware
               -> WebSocket JSON {"type":"event", ...}
               -> ESP32Manager._handler dispatches to EventBus.publish()
               -> subscribers (webhook dispatcher, logger, future consumers)
                  each receive a copy via their asyncio.Queue
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

EventHandler = Callable[["DeviceEvent"], Awaitable[None]]


@dataclass(frozen=True)
class DeviceEvent:
    """A single device event as received from the firmware."""

    event: str
    timestamp_us: int
    data: dict[str, Any]
    device_id: str = "unknown"
    raw: dict[str, Any] = field(default_factory=dict)


class EventBus:
    """Async fan-out event bus.

    Publishers call ``publish(event)``.  Each registered subscriber gets
    a copy delivered to its own ``asyncio.Queue``; a background task
    drains the queue and invokes the handler.  Slow subscribers do not
    block the publisher or each other.
    """

    def __init__(self) -> None:
        self._subscribers: dict[
            int,
            tuple[EventHandler, asyncio.Queue[DeviceEvent], asyncio.Task[None] | None],
        ] = {}
        self._next_id = 0
        self._event_filter: dict[int, set[str] | None] = {}

    def subscribe(
        self,
        handler: EventHandler,
        event_types: set[str] | None = None,
        queue_size: int = 256,
    ) -> int:
        """Register a subscriber.  Returns a subscription ID.

        ``event_types``: deliver only events whose ``event`` field is in
        this set.  ``None`` means all events.
        """
        sub_id = self._next_id
        self._next_id += 1
        queue: asyncio.Queue[DeviceEvent] = asyncio.Queue(maxsize=queue_size)
        task = asyncio.create_task(self._drain(sub_id, handler, queue))
        self._subscribers[sub_id] = (handler, queue, task)
        self._event_filter[sub_id] = event_types
        return sub_id

    def unsubscribe(self, sub_id: int) -> None:
        """Remove a subscriber by ID."""
        entry = self._subscribers.pop(sub_id, None)
        self._event_filter.pop(sub_id, None)
        if entry:
            _, _, task = entry
            if task and not task.done():
                task.cancel()

    async def publish(self, event: DeviceEvent) -> None:
        """Fan out an event to all matching subscribers (non-blocking)."""
        for sub_id, (_, queue, _) in self._subscribers.items():
            event_filter = self._event_filter.get(sub_id)
            if event_filter is not None and event.event not in event_filter:
                continue
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(
                    "Event bus: subscriber %d queue full, dropping %s event",
                    sub_id,
                    event.event,
                )

    async def _drain(
        self,
        sub_id: int,
        handler: EventHandler,
        queue: asyncio.Queue[DeviceEvent],
    ) -> None:
        """Background task that drains a subscriber's queue."""
        try:
            while True:
                event = await queue.get()
                try:
                    await handler(event)
                except Exception:
                    logger.exception(
                        "Event bus: subscriber %d handler error for %s",
                        sub_id,
                        event.event,
                    )
        except asyncio.CancelledError:
            pass

    async def shutdown(self) -> None:
        """Cancel all drain tasks."""
        for sub_id in list(self._subscribers):
            self.unsubscribe(sub_id)
