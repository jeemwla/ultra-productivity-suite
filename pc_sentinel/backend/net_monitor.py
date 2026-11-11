#!/usr/bin/env python3
"""
Lightweight 24/7 latency monitor + on-demand download speed test.

- Latency uses a TCP handshake (a few bytes every couple of seconds), so it never
  eats bandwidth while gaming. Targets are configurable with PING_TARGETS in .env:
      PING_TARGETS=Cloudflare|1.1.1.1|53,Google|8.8.8.8|53,Router|192.168.8.1|80
- The speed test only runs when you press the button. Failures are reported as failures.
"""
import asyncio
import os
import statistics
import time
from collections import deque
from typing import Dict, List, Optional
import httpx


def _targets() -> List[dict]:
    raw = os.getenv("PING_TARGETS", "").strip()
    router = os.getenv("GLINET_ROUTER_IP", "").strip()
    items = []
    if raw:
        for i, part in enumerate(raw.split(",")):
            bits = [b.strip() for b in part.split("|")]
            if len(bits) == 3 and bits[2].isdigit():
                items.append({"id": f"t{i}", "name": bits[0], "host": bits[1], "port": int(bits[2])})
    if not items:
        items = [{"id": "cloudflare", "name": "Cloudflare DNS", "host": "1.1.1.1", "port": 53},
                 {"id": "google", "name": "Google DNS", "host": "8.8.8.8", "port": 53}]
        if router:
            items.append({"id": "router", "name": "Your router", "host": router, "port": 80})
    return items


PING_TARGETS = _targets()
history: Dict[str, deque] = {t["id"]: deque(maxlen=60) for t in PING_TARGETS}


async def measure_tcp_ping(host: str, port: int, timeout: float = 1.5) -> Optional[float]:
    start = time.perf_counter()
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        ms = round((time.perf_counter() - start) * 1000.0, 1)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return ms
    except Exception:
        return None


async def get_live_ping_snapshot() -> Dict:
    values = await asyncio.gather(*(measure_tcp_ping(t["host"], t["port"]) for t in PING_TARGETS))
    results = []
    for t, ms in zip(PING_TARGETS, values):
        h = history[t["id"]]
        h.append(ms)
        ok = [v for v in h if v is not None]
        loss = round(100 * (len(h) - len(ok)) / len(h)) if h else 0
        results.append({"id": t["id"], "name": t["name"], "host": t["host"], "latency_ms": ms,
                        "avg_ms": round(sum(ok) / len(ok), 1) if ok else None,
                        "loss_pct": loss, "history": list(h)[-30:]})
    primary = results[0] if results else {}
    ok = [v for v in history[PING_TARGETS[0]["id"]] if v is not None] if PING_TARGETS else []
    jitter = round(statistics.pstdev(ok[-20:]), 1) if len(ok) >= 3 else None
    online = any(r["latency_ms"] is not None for r in results)
    return {"online": online, "primary_ping_ms": primary.get("latency_ms"), "jitter_ms": jitter, "targets": results}


_speed_lock = asyncio.Lock()


async def run_on_demand_speedtest() -> Dict:
    """Download ~25 MB from Cloudflare's speed endpoint (stops early after 8 s)."""
    if _speed_lock.locked():
        return {"ok": False, "error": "A speed test is already running."}
    async with _speed_lock:
        url = "https://speed.cloudflare.com/__down?bytes=25000000"
        got, start = 0, time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                async with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    async for chunk in resp.aiter_bytes():
                        got += len(chunk)
                        if time.perf_counter() - start > 8:
                            break
            elapsed = max(0.05, time.perf_counter() - start)
            return {"ok": True, "download_mbps": round(got * 8 / elapsed / 1_000_000, 1),
                    "megabytes": round(got / 1_000_000, 1), "seconds": round(elapsed, 1),
                    "server": "Cloudflare"}
        except Exception as exc:
            return {"ok": False, "error": f"Speed test failed: {exc.__class__.__name__}"}
