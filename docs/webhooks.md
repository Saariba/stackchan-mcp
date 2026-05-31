# Webhook events

The StackChan firmware pushes device events (touch, shake, wake-word, state changes, battery, etc.) to the gateway over its existing WebSocket. The gateway fans those events out to configurable HTTP webhook endpoints — so the device can trigger external automations in Home Assistant, n8n, IFTTT, Pipedream, or any HTTP receiver, without the consumer ever talking to MCP.

This document is the full reference. The README has a short overview that links here.

## Contents
- [How it works](#how-it-works)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Event envelope](#event-envelope)
- [Event reference](#event-reference)
- [Receiver examples](#receiver-examples)
- [Troubleshooting](#troubleshooting)
- [Adding a new event type](#adding-a-new-event-type)
- [Internals](#internals)

## How it works

```
┌──────────────────┐                ┌────────────────────────┐                ┌──────────────────┐
│ ESP32 firmware   │  WebSocket     │ gateway (Python)       │  HTTP POST     │ Your webhook URL │
│                  │  JSON event    │                        │  JSON payload  │                  │
│ EmitXxxEvent()   ├───────────────▶│ ESP32Manager._handler  ├───────────────▶│ Home Assistant / │
│ SendJsonString() │  (existing     │   ↓                    │  (one POST per │ n8n / Pipedream/ │
│                  │   WS, no       │ EventBus.publish       │   subscriber)  │ custom endpoint  │
│                  │   new          │   ↓                    │                │                  │
│                  │   transport)   │ WebhookDispatcher      │                │                  │
└──────────────────┘                └────────────────────────┘                └──────────────────┘
```

Key properties:
- **Push, not poll.** Events are delivered within ~200 ms of detection on the device.
- **No firmware changes needed per webhook.** The firmware emits one JSON shape over WebSocket; the gateway handles all HTTP fan-out.
- **No MCP client needed.** Webhooks work whether or not Claude (or any MCP client) is connected.
- **Slow webhooks don't block the device.** Each subscriber has its own async queue; a 30-second-hanging endpoint cannot stall the WebSocket read loop or other webhook targets.

## Quick start

Set one environment variable on the machine running the gateway:

```bash
STACKCHAN_WEBHOOK_URLS=https://your-endpoint.com/hook \
  uv --directory /path/to/stackchan-mcp/gateway run stackchan-mcp
```

At startup you should see:

```
Webhooks configured: 1 target(s) -> https://your-endpoint.com/hook
```

Tap your StackChan's head — within ~200 ms, your endpoint receives:

```json
{
  "event": "touch",
  "timestamp_us": 1716739200123000,
  "device_id": "stackchan-abc",
  "data": {"gesture": "tap", "duration_ms": 180, "zones": [true, false, false]},
  "gateway_time": 1716739200.123
}
```

The gateway also logs `Device event: touch {...}` at INFO level for every event, regardless of whether webhook delivery succeeded.

## Configuration

All configuration is via environment variables on the **gateway** process. **The device firmware needs no webhook configuration** — it just emits events to whatever gateway it's already talking to.

| Variable | Default | Format | Description |
|---|---|---|---|
| `STACKCHAN_WEBHOOK_URLS` | *(unset)* | comma-separated URLs | Endpoints that receive event POSTs. If unset, the gateway logs a warning at startup and events are visible only in the gateway log. |
| `STACKCHAN_WEBHOOK_TOKENS` | *(unset)* | comma-separated Bearer tokens | Positional match to `STACKCHAN_WEBHOOK_URLS`. The i-th token is sent as `Authorization: Bearer <token>` to the i-th URL. Use an empty position (e.g. `,tok2`) to send the i-th URL without auth. |
| `STACKCHAN_WEBHOOK_EVENTS` | *(all)* | comma-separated event names | Whitelist of event types to forward. If set, only events whose `event` field appears in this list reach **any** configured webhook. Defaults to forwarding every event. |

### Multiple webhooks

```bash
STACKCHAN_WEBHOOK_URLS=https://hass.lan/api/webhook/stackchan,https://n8n.lan/webhook/stackchan \
STACKCHAN_WEBHOOK_TOKENS=,n8n_secret \
  uv --directory /path/to/gateway run stackchan-mcp
```

The first URL gets no `Authorization` header; the second gets `Authorization: Bearer n8n_secret`.

### Per-target event filtering — not yet supported

`STACKCHAN_WEBHOOK_EVENTS` applies to every configured target. Per-URL filtering (e.g. "Home Assistant gets `touch` only, n8n gets everything") is planned for a follow-up. Today, if you need separate event subsets, run two gateways or filter on the receiver side.

## Event envelope

Every event the gateway POSTs has this shape:

```json
{
  "event": "<event_name>",
  "timestamp_us": 1716739200123000,
  "device_id": "<mac or device-id header>",
  "data": { /* event-specific payload, see reference */ },
  "gateway_time": 1716739200.123
}
```

| Field | Type | Source | Notes |
|---|---|---|---|
| `event` | string | firmware | Stable event name. See [event reference](#event-reference). |
| `timestamp_us` | integer | firmware | `esp_timer_get_time()` on the device, microseconds since boot. Use this for ordering within a single device session; it resets on reboot. |
| `device_id` | string | gateway | The `Device-Id` HTTP header the device sent on WebSocket connect, or `"unknown"`. Typically the device's MAC address. |
| `data` | object | firmware | Event-specific payload. Schema depends on `event`. |
| `gateway_time` | float | gateway | Unix epoch seconds when the gateway received the event. Use this for ordering across devices or absolute timestamps. |

## Event reference

The firmware currently emits 10 event types. The table below is the canonical reference for each event's `data` schema.

| Event | Trigger | Sensor / source |
|---|---|---|
| [`touch`](#touch) | Head tap or stroke | Si12T capacitive touchpad on head |
| [`lcd_touch`](#lcd_touch) | LCD screen tap or long-press | FT6336 capacitive touchscreen |
| [`imu_motion`](#imu_motion) | Shake or pickup | BMI270 6-axis IMU |
| [`state_changed`](#state_changed) | Device state transition | Application state machine |
| [`wake_word_detected`](#wake_word_detected) | Wake word recognized | Audio processor (xiaozhi) |
| [`listen_start`](#listen_start--listen_stop) | Mic capture opens | Audio pipeline |
| [`listen_stop`](#listen_start--listen_stop) | Mic capture closes | Audio pipeline |
| [`tts_start`](#tts_start--tts_stop) | Speaker playback begins | TTS pipeline |
| [`tts_stop`](#tts_start--tts_stop) | Speaker playback ends | TTS pipeline |
| [`low_battery`](#low_battery) | Battery crosses below 20% while discharging | AXP2101 PMIC |

### `touch`

A confirmed tap or stroke on the head touchpad. Detection runs on a 100 ms poll with debouncing (2 samples to confirm press, 4 samples to confirm release). Fires once per gesture, at release.

| Field | Type | Description |
|---|---|---|
| `gesture` | string | `"tap"` (duration < 400 ms) or `"stroke"` (duration ≥ 400 ms) |
| `duration_ms` | integer | How long the touch was held |
| `zones` | array of 3 booleans | Which of the three head zones (CH1, CH2, CH3) were touched at press-start |

```json
{"gesture": "stroke", "duration_ms": 720, "zones": [false, true, true]}
```

The firmware also reacts locally: `tap` triggers a "surprised" expression, `stroke` triggers "embarrassed" plus a servo wobble.

### `lcd_touch`

LCD screen tap or long-press. Detection runs on a 20 ms poll. Fires once per gesture at release. The firmware separately uses LCD taps as a push-to-talk trigger; the event is supplementary and does not change that behavior.

| Field | Type | Description |
|---|---|---|
| `action` | string | `"tap"` (duration < 500 ms) or `"long_press"` (≥ 500 ms) |
| `duration_ms` | integer | How long the touch was held |

```json
{"action": "tap", "duration_ms": 120}
```

### `imu_motion`

Shake or pickup detection by the BMI270 accelerometer. The IMU is polled at 80 ms; detection runs in software (no Bosch any-motion feature configs) so the thresholds are reviewable in `stackchan.cc`.

| Field | Type | Description |
|---|---|---|
| `motion` | string | `"shake"` or `"pickup"` |
| `score` | integer | For `shake`: differential acceleration L1 norm at the firing peak. For `pickup`: Z-axis gravity vector delta in raw LSB |

```json
{"motion": "shake", "score": 21570}
{"motion": "pickup", "score": 8325}
```

Detection rules:
- **Shake**: requires 3 differential-acceleration peaks above threshold within 1 s; 2 s cooldown between fires.
- **Pickup**: Z-axis gravity vector shift > 6000 LSB from a slowly-tracking baseline; 3 s cooldown.

Avatar reactions: `shake` → "sad", `pickup` → "surprised", both auto-revert after a short hold.

### `state_changed`

Fires on **every** device state machine transition (idle → listening → speaking → connecting → etc.). This is the broadest event — it gives you the high-level lifecycle. For narrower signals tied to actual mic/speaker activity, prefer [`listen_start`/`listen_stop`](#listen_start--listen_stop) and [`tts_start`/`tts_stop`](#tts_start--tts_stop).

| Field | Type | Description |
|---|---|---|
| `state` | string | New state name: `"unknown"`, `"starting"`, `"wifi_configuring"`, `"idle"`, `"connecting"`, `"listening"`, `"speaking"`, `"upgrading"`, `"activating"`, `"audio_testing"`, `"fatal_error"` |

```json
{"state": "listening"}
```

### `wake_word_detected`

Fires whenever the audio processor confirms a wake word, **regardless** of what the firmware then does (start listening, abort current speech, etc.). One event per detection.

| Field | Type | Description |
|---|---|---|
| `wake_word` | string | The detected wake-word string as reported by the audio service (e.g. `"hi stackchan"`) |

```json
{"wake_word": "hi stackchan"}
```

### `listen_start` / `listen_stop`

Mic capture boundary. Narrower than `state_changed: listening` — these fire only on paths that actually open the mic, and `listen_start` carries the listening mode.

`listen_start`:
| Field | Type | Description |
|---|---|---|
| `mode` | string | `"manual"` (gateway-controlled stop), `"auto"` (VAD-controlled), or `"realtime"` (full-duplex) |

```json
{"mode": "manual"}
```

`listen_stop`: empty `data` object (`{}`).

Known limitation: the wake-word activation path goes through `HandleWakeWordDetectedEvent` directly, bypassing `HandleStartListeningEvent`. So if a wake word triggers listening, you'll see `wake_word_detected` + `state_changed: listening` but **not** `listen_start`. Push-to-talk and gateway-initiated `listen()` calls do fire `listen_start`.

### `tts_start` / `tts_stop`

Anchored to the gateway's TTS bracket messages (`{"type":"tts","state":"start"}` / `"stop"`), so they fire specifically when TTS audio is playing — distinct from `state_changed: speaking`, which also fires for boot sounds, popup cues, and other non-TTS audio.

`tts_start`: empty `data` object (`{}`).

`tts_stop`:
| Field | Type | Description |
|---|---|---|
| `duration_ms` | integer | How long TTS audio played, measured from `tts_start` to `tts_stop`. Omitted if no matching `tts_start` was observed (e.g. stray stop). |

```json
{"duration_ms": 2400}
```

### `low_battery`

Edge-triggered: fires once when the battery crosses **below 20% while discharging**, and re-arms when the battery recovers above 20% or charging starts. Uses the same threshold as the on-screen low-battery popup. Voltage is not included (the AXP2101 driver in this firmware doesn't expose a voltage getter; adding one would require new I2C reads).

| Field | Type | Description |
|---|---|---|
| `percent` | integer | Current battery percentage (0–100) |
| `is_critical` | boolean | Always `true` in the current implementation. Reserved for a future second tier (e.g. `<5%`). |

```json
{"percent": 15, "is_critical": true}
```

## Receiver examples

### Quick test with Pipedream

[Pipedream](https://pipedream.com) gives you a free HTTPS endpoint that logs every POST it receives — useful as a first sanity check.

1. Create a new workflow → HTTP trigger → copy the unique URL.
2. Start the gateway with `STACKCHAN_WEBHOOK_URLS=<your-url>`.
3. Tap the StackChan. You'll see the JSON envelope arrive in the Pipedream event log within ~200 ms.

### curl receiver for local testing

The simplest possible receiver — a one-line shell loop:

```bash
while true; do
  printf 'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok' | nc -l 9999
done
```

Then `STACKCHAN_WEBHOOK_URLS=http://127.0.0.1:9999/hook`. The headers and JSON body print to stderr.

For a more useful tester, the repo ships [`gateway/scripts/test_events_e2e.py`](../gateway/scripts/test_events_e2e.py) which starts an aiohttp receiver, spins up the gateway, sends every event type from a fake ESP32, and verifies each one arrives.

### Home Assistant

In `configuration.yaml`:

```yaml
automation:
  - alias: "StackChan head tap toggles bedroom light"
    trigger:
      - platform: webhook
        webhook_id: stackchan_events
        local_only: false  # set to false only if your gateway isn't on HA's host
    condition:
      - condition: template
        value_template: "{{ trigger.json.event == 'touch' and trigger.json.data.gesture == 'tap' }}"
    action:
      - service: light.toggle
        target:
          entity_id: light.bedroom
```

Then `STACKCHAN_WEBHOOK_URLS=http://homeassistant.local:8123/api/webhook/stackchan_events`.

If you want HA to react to several event types, register one webhook in HA and branch on `trigger.json.event` in the automation conditions.

### n8n

Add an HTTP webhook node, set it to `POST`, copy the URL. Use `STACKCHAN_WEBHOOK_URLS=<url>` and `STACKCHAN_WEBHOOK_TOKENS=<your_secret>` if you want Bearer auth. The event JSON is available at `{{$json.event}}`, `{{$json.data.gesture}}`, etc.

### IFTTT / Maker webhooks

IFTTT's Maker channel expects `value1`/`value2`/`value3` form fields, not arbitrary JSON. Use n8n, Make, or a small Cloudflare Worker as a shim — for example:

```js
// Cloudflare Worker that re-shapes our JSON into IFTTT Maker format
export default {
  async fetch(req) {
    const body = await req.json();
    return fetch(`https://maker.ifttt.com/trigger/${body.event}/with/key/IFTTT_KEY`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        value1: body.event,
        value2: JSON.stringify(body.data),
        value3: body.device_id,
      }),
    });
  }
}
```

## Troubleshooting

### "I tapped the head but nothing arrives at my webhook"

Walk down the chain — the gateway logs tell you exactly which leg failed.

**1. Did the gateway register your webhook URL?** At startup, look for:
```
Webhooks configured: 1 target(s) -> https://your-url.com/hook
```
If you see `No webhooks configured — device events will be logged but not forwarded.` instead, `STACKCHAN_WEBHOOK_URLS` wasn't set in the gateway's environment. Double-check that you set it on the same shell line that ran the gateway (`STACKCHAN_WEBHOOK_URLS=... uv ... run stackchan-mcp`).

**2. Is the device emitting events?** Tap the head, then look for a line like:
```
Device event: touch {'gesture': 'tap', 'duration_ms': 180, 'zones': [true, false, false]}
```
If you see this, the firmware → gateway leg is healthy and the only remaining issue is webhook delivery. If you **don't** see this, the device isn't sending events — see step 4.

**3. Are webhooks failing with HTTP errors?** The dispatcher logs at WARNING:
```
Webhook https://your-url.com/hook returned 401 for touch event
```
or at ERROR with a traceback for network failures (DNS, TCP, TLS). Fix the receiver URL / auth / network reachability and the next event will deliver.

**4. Device-side check.** Connect to the device's serial console (`idf.py monitor` or `screen /dev/cu.usbmodem...`) and tap the head. You should see firmware logs like:
```
TAP duration=180 ms
IMU: shake detected (score=21570)
```
If those don't appear, the firmware isn't detecting your gesture. If they appear but no gateway-side `Device event: ...` shows, check that the WebSocket session is healthy (gateway log will show `ESP32 ready: device=...` with the right MAC).

### "Webhooks fire for some events but not others"

`STACKCHAN_WEBHOOK_EVENTS` is set. The variable is a strict whitelist — only events whose name appears verbatim are forwarded. Unset it to forward everything, or include the event name you're missing.

### "I see `Replacing existing ESP32 connection`"

Two firmware instances are connecting to the same gateway port (e.g. you flashed the same firmware to two devices that share a `Device-Id`, or a previous WebSocket session didn't close cleanly). The gateway only tracks one connection at a time; the newer one wins. Webhook delivery resumes once the connection is stable.

### "Events arrive in bursts after a delay"

The dispatcher has a 5 s timeout per HTTP request and uses a per-subscriber `asyncio.Queue` with default size 256. If your webhook endpoint is slow (>5 s per response) the events queue up and deliver as the endpoint catches up. The gateway log will show timeout warnings if delivery is consistently slow.

## Adding a new event type

The system is designed so a new event = one `SendJsonString()` call in firmware. **Zero gateway changes needed** — the event bus dispatches any `type: "event"` message automatically.

1. Decide the event name and payload schema. Keep names short and stable (`battery_full`, `head_lifted`, etc.); add fields to `data` as needed.

2. In firmware, build the JSON with cJSON and call `Application::GetInstance().SendJsonString(str)`. Pattern in `firmware/main/boards/stackchan/stackchan.cc`:

   ```cpp
   void EmitMyEvent(int value) {
       cJSON* root = cJSON_CreateObject();
       if (!root) return;
       cJSON_AddStringToObject(root, "type", "event");
       cJSON_AddStringToObject(root, "event", "my_event");
       cJSON_AddNumberToObject(root, "timestamp_us",
                               static_cast<double>(esp_timer_get_time()));
       cJSON* data = cJSON_AddObjectToObject(root, "data");
       if (data) {
           cJSON_AddNumberToObject(data, "value", value);
       }
       char* str = cJSON_PrintUnformatted(root);
       if (str) {
           Application::GetInstance().SendJsonString(std::string(str));
           cJSON_free(str);
       }
       cJSON_Delete(root);
   }
   ```

   If you're already on the main task, you can call `protocol_->SendText(str)` directly instead — that skips the schedule trampoline. `SendJsonString` is always safe and is what background-task code (timer callbacks, IMU task, LVGL task) should use.

3. Call your emit helper from the detection point.

4. Build and flash. The new event will start arriving at any webhook that doesn't have it filtered out by `STACKCHAN_WEBHOOK_EVENTS`.

5. **Document it here.** Add a row to the [event reference](#event-reference) table and a section with the payload schema. The README event table also needs the new row — keep both in sync.

## Internals

For contributors changing the event system itself (not just adding events):

- **Wire format**: any WebSocket text frame with `type: "event"` is treated as an event. Frames with `type: "mcp"`, `type: "hello"`, `type: "avatar_set_loaded"`, etc. are still dispatched to their existing handlers.
- **Gateway entry point**: `ESP32Manager._handler` in `gateway/stackchan_mcp/esp32_client.py` — the `elif msg_type == "event":` branch parses the JSON into a `DeviceEvent` dataclass and calls `event_bus.publish(...)`.
- **Event bus**: `gateway/stackchan_mcp/event_bus.py`. Async fan-out with per-subscriber `asyncio.Queue` (default capacity 256). `publish()` is non-blocking (`put_nowait`); overflow drops events with a warning. Subscribers run on their own drain task so a slow subscriber cannot block the read loop or other subscribers.
- **Webhook dispatcher**: `gateway/stackchan_mcp/webhook.py`. Holds a shared `aiohttp.ClientSession` with a 5 s timeout. POSTs JSON to every configured target whose `event_types` filter (if any) matches. Per-URL failures are caught and logged; they don't abort delivery to other targets.
- **Logging subscriber**: `Gateway.start()` in `gateway/stackchan_mcp/gateway.py` subscribes a small async function that logs every event at INFO. This is what produces the `Device event: ...` lines and is intentionally always on — it makes the firmware → gateway leg visible without DEBUG logging.
- **Tests**: `gateway/tests/test_event_bus.py` (unit), `gateway/tests/test_webhook.py` (unit, mocked aiohttp), `gateway/scripts/test_events_e2e.py` (integration — spins up the whole pipeline with a fake ESP32 and a local HTTP receiver).

### Not yet implemented (Phase 2)

- **MCP notifications**: emit events as MCP `notifications/message` to connected MCP clients, not just webhooks. The MCP SDK exposes `ServerSession.send_notification()`; the gating problem is capturing the session reference from `Server.run()` for async push.
- **SSE endpoint**: an `/events` Server-Sent Events stream on the gateway's HTTP capture server, for browser-based consumers that don't have an HTTP endpoint.
- **Webhook retry + backoff**: today, a single POST failure drops the event. A retry queue with exponential backoff would harden delivery against transient endpoint downtime.
- **Per-URL event filters**: extend `STACKCHAN_WEBHOOK_EVENTS` from "applies to all targets" to "per-target subset" (e.g. `STACKCHAN_WEBHOOK_EVENTS_0=touch,lcd_touch` for the first URL).
