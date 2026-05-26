#!/usr/bin/env python3
"""End-to-end test for the event pipeline.

Spins up a local webhook receiver, starts the gateway, connects a fake
ESP32 that sends a touch event, and verifies the webhook fires.

Usage:
    uv run python scripts/test_events_e2e.py
"""

from __future__ import annotations

import asyncio
import json
import os
from aiohttp import web
import websockets


# ---- Webhook receiver ----

received_webhooks: list[dict] = []
webhook_received = asyncio.Event()


async def webhook_handler(request: web.Request) -> web.Response:
    body = await request.json()
    received_webhooks.append(body)
    print(f"\n  [webhook] received event: {json.dumps(body, indent=2)}")
    webhook_received.set()
    return web.Response(text="ok")


async def start_webhook_server(port: int = 9999) -> web.AppRunner:
    app = web.Application()
    app.router.add_post("/hook", webhook_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    print(f"[webhook] listening on http://127.0.0.1:{port}/hook")
    return runner


# ---- Fake ESP32 ----

async def fake_esp32(ws_port: int = 8765) -> None:
    """Connect as a fake ESP32, do hello handshake, then send a touch event."""
    uri = f"ws://127.0.0.1:{ws_port}"
    print(f"\n[esp32] connecting to {uri} ...")

    async with websockets.connect(
        uri, additional_headers={"Device-Id": "fake-stackchan-test"}
    ) as ws:
        # Send hello
        hello = {
            "type": "hello",
            "version": 1,
            "features": {"mcp": True},
            "transport": "websocket",
            "audio_params": {
                "format": "opus",
                "sample_rate": 16000,
                "channels": 1,
                "frame_duration": 60,
            },
        }
        await ws.send(json.dumps(hello))
        resp = json.loads(await ws.recv())
        print(f"[esp32] got hello response: session_id={resp.get('session_id', '?')[:8]}...")

        # Consume the initialize request from the gateway (it will send
        # one automatically after hello)
        async def drain_and_respond():
            """Answer MCP requests so the gateway doesn't time out."""
            try:
                async for msg in ws:
                    if isinstance(msg, bytes):
                        continue
                    data = json.loads(msg)
                    payload = data.get("payload", {})
                    method = payload.get("method", "")
                    req_id = payload.get("id")
                    if method == "initialize":
                        resp = {
                            "session_id": data.get("session_id", ""),
                            "type": "mcp",
                            "payload": {
                                "jsonrpc": "2.0",
                                "id": req_id,
                                "result": {
                                    "protocolVersion": "2024-11-05",
                                    "serverInfo": {"name": "fake-stackchan", "version": "0.0.1"},
                                    "capabilities": {"tools": {}},
                                },
                            },
                        }
                        await ws.send(json.dumps(resp))
                        print("[esp32] answered initialize")
                    elif method == "tools/list":
                        resp = {
                            "session_id": data.get("session_id", ""),
                            "type": "mcp",
                            "payload": {
                                "jsonrpc": "2.0",
                                "id": req_id,
                                "result": {"tools": []},
                            },
                        }
                        await ws.send(json.dumps(resp))
                        print("[esp32] answered tools/list")
            except websockets.exceptions.ConnectionClosed:
                pass

        drain_task = asyncio.create_task(drain_and_respond())

        # Give the gateway a moment to initialize
        await asyncio.sleep(1.5)

        # Send a touch event (tap)
        touch_event = {
            "type": "event",
            "event": "touch",
            "timestamp_us": 123456789,
            "data": {
                "gesture": "tap",
                "duration_ms": 180,
                "zones": [True, False, False],
            },
        }
        await ws.send(json.dumps(touch_event))
        print("\n[esp32] sent touch event (tap)")

        # Send a state_changed event
        await asyncio.sleep(0.5)
        state_event = {
            "type": "event",
            "event": "state_changed",
            "timestamp_us": 123457000,
            "data": {"state": "listening"},
        }
        await ws.send(json.dumps(state_event))
        print("[esp32] sent state_changed event (listening)")

        # Wait for webhooks
        await asyncio.sleep(2)
        drain_task.cancel()


async def main() -> None:
    webhook_port = 9999
    ws_port = int(os.getenv("WS_PORT", os.getenv("PORT", "8765")))

    # Set webhook env var so the gateway picks it up
    os.environ["STACKCHAN_WEBHOOK_URLS"] = f"http://127.0.0.1:{webhook_port}/hook"
    os.environ.setdefault("VISION_HOST", "127.0.0.1")

    # Start webhook receiver
    webhook_runner = await start_webhook_server(webhook_port)

    # Start gateway
    print(f"\n[gateway] starting on port {ws_port} ...")
    from stackchan_mcp.gateway import get_gateway

    gw = get_gateway()
    await gw.start(advertise_mdns=False)
    print(f"[gateway] running (webhooks -> http://127.0.0.1:{webhook_port}/hook)")

    try:
        # Run fake ESP32
        await fake_esp32(ws_port)

        # Check results
        print("\n" + "=" * 60)
        if len(received_webhooks) >= 2:
            print(f"SUCCESS: received {len(received_webhooks)} webhook(s)")
            for i, wh in enumerate(received_webhooks):
                print(f"  [{i+1}] event={wh['event']}  data={wh['data']}")
        elif len(received_webhooks) == 1:
            print(f"PARTIAL: received 1 webhook (expected 2)")
            print(f"  [1] event={received_webhooks[0]['event']}")
        else:
            print("FAIL: no webhooks received")
        print("=" * 60)

    finally:
        await gw.stop()
        await webhook_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
