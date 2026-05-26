"""Tests for the async event bus."""

import asyncio

import pytest

from stackchan_mcp.event_bus import DeviceEvent, EventBus


def _make_event(event: str = "touch", **data_kw) -> DeviceEvent:
    return DeviceEvent(
        event=event,
        timestamp_us=1_000_000,
        data=data_kw or {"gesture": "tap"},
        device_id="test-device",
    )


@pytest.mark.asyncio
async def test_publish_subscribe():
    """Subscriber receives published events."""
    bus = EventBus()
    received: list[DeviceEvent] = []

    async def handler(ev: DeviceEvent) -> None:
        received.append(ev)

    bus.subscribe(handler)
    ev = _make_event()
    await bus.publish(ev)
    await asyncio.sleep(0.05)

    assert len(received) == 1
    assert received[0] is ev
    await bus.shutdown()


@pytest.mark.asyncio
async def test_event_type_filter():
    """Subscriber with event_types filter only gets matching events."""
    bus = EventBus()
    received: list[DeviceEvent] = []

    async def handler(ev: DeviceEvent) -> None:
        received.append(ev)

    bus.subscribe(handler, event_types={"touch"})
    await bus.publish(_make_event("touch"))
    await bus.publish(_make_event("state_changed", state="idle"))
    await asyncio.sleep(0.05)

    assert len(received) == 1
    assert received[0].event == "touch"
    await bus.shutdown()


@pytest.mark.asyncio
async def test_multiple_subscribers():
    """Multiple subscribers each receive the same event."""
    bus = EventBus()
    a: list[DeviceEvent] = []
    b: list[DeviceEvent] = []

    bus.subscribe(lambda ev: _async_append(a, ev))
    bus.subscribe(lambda ev: _async_append(b, ev))
    await bus.publish(_make_event())
    await asyncio.sleep(0.05)

    assert len(a) == 1
    assert len(b) == 1
    await bus.shutdown()


@pytest.mark.asyncio
async def test_unsubscribe():
    """Unsubscribed handler stops receiving events."""
    bus = EventBus()
    received: list[DeviceEvent] = []

    async def handler(ev: DeviceEvent) -> None:
        received.append(ev)

    sub_id = bus.subscribe(handler)
    await bus.publish(_make_event())
    await asyncio.sleep(0.05)
    assert len(received) == 1

    bus.unsubscribe(sub_id)
    await bus.publish(_make_event())
    await asyncio.sleep(0.05)
    assert len(received) == 1
    await bus.shutdown()


@pytest.mark.asyncio
async def test_queue_overflow_drops_event():
    """When a subscriber's queue is full, publish drops the event."""
    bus = EventBus()
    received: list[DeviceEvent] = []
    gate = asyncio.Event()

    async def slow_handler(ev: DeviceEvent) -> None:
        await gate.wait()
        received.append(ev)

    bus.subscribe(slow_handler, queue_size=2)

    # Fill the queue (drain task is blocked on gate)
    await bus.publish(_make_event())
    await bus.publish(_make_event())
    # Third should be dropped
    await bus.publish(_make_event())

    gate.set()
    await asyncio.sleep(0.05)

    assert len(received) == 2
    await bus.shutdown()


@pytest.mark.asyncio
async def test_handler_exception_does_not_crash_bus():
    """A failing handler doesn't stop subsequent event delivery."""
    bus = EventBus()
    received: list[DeviceEvent] = []

    async def bad_handler(ev: DeviceEvent) -> None:
        if ev.data.get("fail"):
            raise ValueError("boom")
        received.append(ev)

    bus.subscribe(bad_handler)
    await bus.publish(_make_event(fail=True))
    await bus.publish(_make_event(fail=False))
    await asyncio.sleep(0.05)

    assert len(received) == 1
    await bus.shutdown()


@pytest.mark.asyncio
async def test_shutdown_cancels_tasks():
    """After shutdown, no drain tasks remain running."""
    bus = EventBus()
    bus.subscribe(lambda ev: _async_append([], ev))
    bus.subscribe(lambda ev: _async_append([], ev))
    assert len(bus._subscribers) == 2

    await bus.shutdown()
    assert len(bus._subscribers) == 0


async def _async_append(lst: list, ev: DeviceEvent) -> None:
    lst.append(ev)
