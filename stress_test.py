#!/usr/bin/env python3
"""Concurrent stress test for the CGrep SDR dashboard.

Run in test mode on the server, then:

    python stress_test.py --url https://example.com --users 100 --duration 60

Only opens read-only REST and WebSocket paths; does not POST or mutate data.
"""
import argparse
import asyncio
import time
import urllib.request
import urllib.error

import websockets


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def rest_call(base: str, path: str, timeout: int = 10):
    """Fetch a single REST endpoint (async wrapper around urllib)."""

    def fetch():
        url = base + path
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json, */*",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status
        except urllib.error.HTTPError as exc:
            return exc.code
        except Exception as exc:
            return str(exc)

    return await asyncio.to_thread(fetch)


async def ws_loop(ws_url: str, origin: str, name: str, duration: float):
    """Open a WebSocket, wait for the first message, hold for `duration`."""
    t0 = time.time()
    try:
        async with websockets.connect(
            ws_url,
            origin=origin,
            user_agent_header="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            open_timeout=5,
            close_timeout=5,
        ) as ws:
            await asyncio.wait_for(ws.recv(), timeout=5)
            await asyncio.sleep(duration)
            return name, "ok", time.time() - t0
    except Exception as exc:
        return name, "err", time.time() - t0, str(exc)


async def one_user(base: str, ws_base: str, origin: str, user_id: int, duration: float):
    """Simulate one user: a few REST calls + two WebSockets (sdr + chat)."""
    http = {
        "history": await rest_call(base, "/api/history"),
        "telemetry": await rest_call(base, "/api/telemetry"),
        "sdr_list": await rest_call(base, "/api/sdr/list"),
        "peaks": await rest_call(
            base,
            "/api/sdr/site_1/peaks?freqs="
            "88.7,90.0,90.7,92.5,93.0,93.3,97.7,99.3,100.0,100.5,"
            "101.1,101.3,103.0,103.3,104.0,105.0,105.5,106.0,106.3,107.0,107.7,223.936",
        ),
    }
    sdr, chat = await asyncio.gather(
        ws_loop(ws_base + "/ws/sdr", origin, "sdr", duration),
        ws_loop(ws_base + "/ws/chat", origin, "chat", duration),
    )
    return user_id, http, sdr, chat


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main(base: str, users: int, duration: float, step: bool):
    ws_base = base.replace("http://", "ws://").replace("https://", "wss://")
    origin = base.rstrip("/")

    targets = [users] if not step else [50, 100, 250, 500, 1000]
    targets = [t for t in targets if t <= users]
    if not targets:
        targets = [users]

    for target in targets:
        print(f"\n[stress] base={base} users={target} duration={duration}s")
        t0 = time.time()
        tasks = [
            asyncio.create_task(one_user(base, ws_base, origin, i, duration))
            for i in range(target)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        elapsed = time.time() - t0

        ok = err = http_err = 0
        for r in results:
            if isinstance(r, Exception):
                err += 1
                continue
            _, http, sdr, chat = r
            if any(not isinstance(v, int) or v >= 400 for v in http.values()):
                http_err += 1
            ok += sum(1 for s in (sdr, chat) if s[1] == "ok")
            err += sum(1 for s in (sdr, chat) if s[1] == "err")

        total_ws = target * 2
        print(
            f"[stress] elapsed={elapsed:.1f}s "
            f"ws_ok={ok}/{total_ws} ws_err={err} http_err={http_err}"
        )

        if err or http_err:
            print("[stress] first failures started here — this is the practical ceiling.")
            # Show a sample of what actually failed
            for r in results[:5]:
                if isinstance(r, Exception):
                    print(f"  exc: {r}")
                else:
                    _, http, sdr, chat = r
                    print(f"  sample http={http} sdr={sdr} chat={chat}")
                break
            if step:
                break


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stress test the CGrep dashboard")
    parser.add_argument("--url", default="http://localhost:8000", help="Base URL")
    parser.add_argument("--users", type=int, default=100, help="Max concurrent users")
    parser.add_argument("--duration", type=int, default=60, help="Seconds to keep each connection")
    parser.add_argument("--step", action="store_true", help="Ramp 50/100/250/500/1000 automatically")
    args = parser.parse_args()

    asyncio.run(main(args.url, args.users, float(args.duration), args.step))
