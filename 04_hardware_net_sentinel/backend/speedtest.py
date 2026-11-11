#!/usr/bin/env python3
"""
Full speed test against Cloudflare's speed endpoints (the same ones speed.cloudflare.com uses).

Phases (about 25 seconds total), streamed live so the UI can animate:
  1. ping      ~2 s   12 small requests: idle latency and jitter
  2. download  ~10 s  6 parallel streams, live Mbps every 200 ms
  3. upload    ~10 s  4 parallel streams, live Mbps every 200 ms
"""
import asyncio
import os
import re
import statistics
import time
from typing import Awaitable, Callable, Dict, List

import httpx

BASE = "https://speed.cloudflare.com"
DOWN_SECONDS = float(os.getenv("SPEEDTEST_DOWN_SECONDS", "10"))
UP_SECONDS = float(os.getenv("SPEEDTEST_UP_SECONDS", "10"))
DOWN_STREAMS, UP_STREAMS = 6, 4
TICK = 0.2
COLO = {"LAX": "Los Angeles", "SJC": "San Jose", "SFO": "San Francisco", "SEA": "Seattle", "PDX": "Portland",
        "LAS": "Las Vegas", "PHX": "Phoenix", "DEN": "Denver", "DFW": "Dallas", "IAH": "Houston", "ORD": "Chicago",
        "ATL": "Atlanta", "MIA": "Miami", "IAD": "Ashburn", "EWR": "Newark", "JFK": "New York", "BOS": "Boston",
        "YYZ": "Toronto", "YVR": "Vancouver", "LHR": "London", "FRA": "Frankfurt", "AMS": "Amsterdam", "CDG": "Paris",
        "NRT": "Tokyo", "ICN": "Seoul", "SIN": "Singapore", "HKG": "Hong Kong", "SYD": "Sydney"}

Send = Callable[[Dict], Awaitable[None]]
_lock = asyncio.Lock()


MIRRORS = {"lax": "Los Angeles", "sea": "Seattle", "nyc": "New York", "mia": "Miami", "lon": "London", "par": "Paris",
           "fra": "Frankfurt", "ams": "Amsterdam", "tyo": "Tokyo", "sgp": "Singapore", "syd": "Sydney"}


async def _nearest_mirror():
    """Pick the DataPacket public test mirror with the fastest TCP connect."""
    async def connect_ms(code):
        host = f"{code}.download.datapacket.com"
        t = time.perf_counter()
        try:
            _, w = await asyncio.wait_for(asyncio.open_connection(host, 443), timeout=2.0)
            w.close()
            return (time.perf_counter() - t, code)
        except Exception:
            return (99.0, code)
    results = sorted(await asyncio.gather(*(connect_ms(c) for c in MIRRORS)))
    best = results[0]
    if best[0] >= 99:
        return None
    return f"https://{best[1]}.download.datapacket.com/1000mb.bin", f"DataPacket {MIRRORS[best[1]]}"


def _mbps(nbytes: float, seconds: float) -> float:
    return round(nbytes * 8 / max(seconds, 1e-6) / 1_000_000, 1)


async def _ping(client: httpx.AsyncClient, send: Send) -> Dict:
    samples: List[float] = []
    colo = None
    rtts: List[float] = []
    for i in range(12):
        t = time.perf_counter()
        r = await client.get(f"{BASE}/__down", params={"bytes": "0"})
        ms = (time.perf_counter() - t) * 1000
        # Prefer the TCP round-trip time Cloudflare measured (cfL4 rtt, microseconds);
        # otherwise subtract the server's own processing time from the request time.
        timing = ", ".join(r.headers.get_list("server-timing"))
        m = re.search(r"min_rtt=(\d+)", timing)
        if m:
            ms = int(m.group(1)) / 1000.0
            # min_rtt only ever drops on a connection; add the observed swing for jitter
            m2 = re.search(r"[?&]rtt=(\d+)", timing)
            if m2 and i > 0:
                rtts.append(int(m2.group(1)) / 1000.0)
        else:
            for part in timing.split(","):
                if "dur=" in part and "cfL4" not in part:
                    try:
                        ms -= float(part.split("dur=")[1].split(";")[0].split('"')[0])
                    except ValueError:
                        pass
        colo = colo or (r.headers.get("cf-ray", "").rsplit("-", 1)[-1] or None)
        if i > 0:  # first request includes the TLS handshake
            samples.append(max(ms, 0.1))
            await send({"type": "progress", "phase": "ping", "ping_ms": round(min(samples), 1),
                        "progress": i / 11})
        await asyncio.sleep(0.08)
    ping = min(samples)
    # Jitter: average change between consecutive round-trip times (as most speed tests report it)
    series = rtts if len(rtts) > 2 else samples
    jitter = statistics.mean(abs(a - b) for a, b in zip(series, series[1:])) if len(series) > 1 else 0.0
    return {"ping_ms": round(ping, 1), "jitter_ms": round(jitter, 1), "colo": colo}


async def _throughput(phase: str, worker, streams: int, seconds: float, send: Send) -> float:
    counter = {"bytes": 0}
    stop = asyncio.Event()
    tasks = [asyncio.create_task(worker(counter, stop)) for _ in range(streams)]
    start = time.perf_counter()
    window: List[tuple] = [(start, 0)]
    series: List[float] = []
    try:
        while (now := time.perf_counter()) - start < seconds:
            await asyncio.sleep(TICK)
            now = time.perf_counter()
            window.append((now, counter["bytes"]))
            # rate over the last ~1 s for a smooth but responsive needle
            while len(window) > 2 and now - window[0][0] > 1.0:
                window.pop(0)
            live = _mbps(window[-1][1] - window[0][1], window[-1][0] - window[0][0])
            series.append(live)
            await send({"type": "progress", "phase": phase, "mbps": live,
                        "progress": min(1.0, (now - start) / seconds)})
    finally:
        stop.set()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    if counter.get("error") and counter["bytes"] == 0:
        print(f"[speedtest] {phase} failed: {counter['error']}")
    # Result: average of the samples after the first 2 s ramp-up, ignoring the slowest 20%
    steady = sorted(series[int(2 / TICK):] or series)
    keep = steady[int(len(steady) * 0.2):] or steady
    return round(sum(keep) / len(keep), 1) if keep else 0.0


async def run(send: Send) -> Dict:
    if _lock.locked():
        await send({"type": "error", "error": "A speed test is already running."})
        return {}
    async with _lock:
        limits = httpx.Limits(max_connections=16, max_keepalive_connections=16)
        timeout = httpx.Timeout(15.0, connect=6.0)
        try:
            async with httpx.AsyncClient(limits=limits, timeout=timeout, http2=False) as client:
                await send({"type": "phase", "phase": "ping"})
                p = await _ping(client, send)
                await send({"type": "phase_done", "phase": "ping", **p,
                            "server": COLO.get(p["colo"] or "", p["colo"] or "Cloudflare")})

                # Cloudflare rate-limits heavy repeat testing (HTTP 429). If so, use the nearest public mirror.
                down_url, down_server = f"{BASE}/__down?bytes=50000000", None
                try:
                    async with client.stream("GET", down_url) as probe:  # headers only, body not read
                        limited = probe.status_code != 200
                except Exception:
                    limited = True
                if limited:
                    mirror = await _nearest_mirror()
                    if mirror:
                        down_url, down_server = mirror
                        await send({"type": "note", "note": f"Cloudflare is busy; downloading from {down_server} instead."})

                async def down_worker(counter, stop):
                    while not stop.is_set():
                        try:
                            async with client.stream("GET", down_url) as r:
                                if r.status_code >= 400:
                                    counter["error"] = f"HTTP {r.status_code}"
                                    await asyncio.sleep(0.5)
                                    continue
                                async for chunk in r.aiter_raw():
                                    counter["bytes"] += len(chunk)
                                    if stop.is_set():
                                        return
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            counter["error"] = f"{exc.__class__.__name__}: {exc}"
                            await asyncio.sleep(0.2)

                await send({"type": "phase", "phase": "download"})
                down = await _throughput("download", down_worker, DOWN_STREAMS, DOWN_SECONDS, send)
                await send({"type": "phase_done", "phase": "download", "mbps": down, "server": down_server})

                block = os.urandom(256 * 1024)

                async def up_worker(counter, stop):
                    async def body():
                        for _ in range(100):  # up to 25 MB per request
                            if stop.is_set():
                                return
                            counter["bytes"] += len(block)
                            yield block
                    while not stop.is_set():
                        try:
                            await client.post(f"{BASE}/__up", content=body(),
                                              headers={"Content-Type": "application/octet-stream"})
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            counter["error"] = f"{exc.__class__.__name__}: {exc}"
                            await asyncio.sleep(0.2)

                await send({"type": "phase", "phase": "upload"})
                up = await _throughput("upload", up_worker, UP_STREAMS, UP_SECONDS, send)
                await send({"type": "phase_done", "phase": "upload", "mbps": up})

                result = {"type": "done", "download_mbps": down, "upload_mbps": up, "ping_ms": p["ping_ms"],
                          "jitter_ms": p["jitter_ms"], "server": COLO.get(p["colo"] or "", p["colo"] or "Cloudflare"),
                          "at": time.time()}
                await send(result)
                return result
        except Exception as exc:
            await send({"type": "error", "error": f"Speed test failed: {exc.__class__.__name__}. Check your connection."})
            return {}
