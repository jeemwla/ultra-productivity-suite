#!/usr/bin/env python3
"""
Host Audio Bridge for the Code Red Alarm (runs on the PC itself, not in Docker).

- Lists every speaker/output device on this computer and lets the user pick one
  ("default" = whatever the OS is currently using). The choice is saved in
  device_config.json next to this file, so it works on any PC with any speakers.
- Plays the siren on the chosen device (sounddevice/PortAudio; falls back to the
  default device via winsound on Windows).
- On Windows, locks that device at 100% volume and unmuted while ringing (pycaw),
  then restores the previous volume when the alarm stops.
- Runs the user's OneScript VPN hook for the Hardware Sentinel.
- Listens on 127.0.0.1 only and requires X-Bridge-Token (from the suite's .env).

  python audio_enforcer.py --list-devices
  python audio_enforcer.py --serve [--port 8091]
"""
import sys as _sys, os as _os
if _sys.stdout is None or _sys.stderr is None:  # pythonw: no console, log to file
    _log = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "audio_enforcer.log"), "a", buffering=1, encoding="utf-8")
    _sys.stdout = _sys.stdout or _log
    _sys.stderr = _sys.stderr or _log
import warnings as _w
_w.filterwarnings("ignore")

import argparse
import json
import math
import os
import struct
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
_envp = os.path.join(HERE, "..", "..", ".env")
if os.path.exists(_envp):
    for _line in open(_envp, encoding="utf-8"):
        if "=" in _line and not _line.lstrip().startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

IS_WINDOWS = os.name == "nt"
try:
    import sounddevice as sd
    SD_OK = True
except Exception:
    SD_OK = False
PYCAW_OK = False
if IS_WINDOWS:
    try:
        from comtypes import CoInitialize, CoUninitialize
        from pycaw.pycaw import AudioUtilities
        PYCAW_OK = True
    except Exception:
        pass
    import winsound

BRIDGE_TOKEN = os.getenv("BRIDGE_TOKEN", "")
ONESCRIPT_PATH = os.getenv("ONESCRIPT_PATH", "")
CONFIG_PATH = os.path.join(os.environ.get("ULTRASUITE_DATA") or HERE, "device_config.json")
DEFAULT_DURATION = int(os.getenv("DEFAULT_RING_DURATION_SEC", "600"))


# ----------------------------------------------------------------------------- devices
def _preferred_hostapi():
    if not SD_OK:
        return None
    names = [h["name"] for h in sd.query_hostapis()]
    for want in ("Windows WASAPI", "Core Audio", "PulseAudio", "ALSA"):
        if want in names:
            return names.index(want)
    return None


def list_outputs() -> list:
    """Output devices with full names, de-duplicated. Always starts with 'default'."""
    out = [{"name": "default", "label": "System default output", "default": True}]
    if SD_OK:
        try:
            api = _preferred_hostapi()
            seen = set()
            for i, d in enumerate(sd.query_devices()):
                if d["max_output_channels"] < 1 or (api is not None and d["hostapi"] != api):
                    continue
                if d["name"] in seen:
                    continue
                seen.add(d["name"])
                out.append({"name": d["name"], "label": d["name"], "default": False})
            if api is not None:
                didx = sd.query_hostapis(api).get("default_output_device", -1)
                if didx is not None and didx >= 0:
                    out[0]["label"] = f"System default ({sd.query_devices(didx)['name']})"
        except Exception as exc:
            print(f"[bridge] device scan failed: {exc}")
    elif PYCAW_OK:
        CoInitialize()
        try:
            for dev in AudioUtilities.GetAllDevices():
                n = dev.FriendlyName or ""
                if n and "Active" in str(dev.state) and not n.lower().startswith("microphone"):
                    out.append({"name": n, "label": n, "default": False})
        finally:
            CoUninitialize()
    return out


# ----------------------------------------------------------------------------- config
def load_config() -> dict:
    cfg = {"output": "default", "lock_volume": False}
    if os.path.exists(CONFIG_PATH):
        try:
            cfg.update(json.load(open(CONFIG_PATH, encoding="utf-8")))
            return cfg
        except Exception:
            pass
    # First run on a new PC: honor ALARM_SPEAKER_MATCH (older name: PEBBLE_DEVICE_MATCH) if that device exists here
    match = (os.getenv("ALARM_SPEAKER_MATCH") or os.getenv("PEBBLE_DEVICE_MATCH") or "").strip().lower()
    if match:
        for d in list_outputs()[1:]:
            if match in d["name"].lower():
                cfg["output"] = d["name"]
                break
    return cfg


def save_config(cfg: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def _sd_device_for(name: str):
    if not SD_OK or name == "default":
        api = _preferred_hostapi() if SD_OK else None
        if api is not None:
            didx = sd.query_hostapis(api).get("default_output_device", -1)
            return didx if didx is not None and didx >= 0 else None
        return None
    api = _preferred_hostapi()
    devs = list(sd.query_devices())
    for i, d in enumerate(devs):
        if d["max_output_channels"] > 0 and d["name"] == name and (api is None or d["hostapi"] == api):
            return i
    for i, d in enumerate(devs):  # fuzzy fallback: names differ per host API
        n = d["name"].lower()
        if d["max_output_channels"] > 0 and (name.lower() in n or n in name.lower()):
            return i
    return None


def _endpoint_volumes(name: str):
    """pycaw volume interfaces for the chosen device (Windows only)."""
    vols = []
    try:
        targets = []
        if name != "default":
            low = name.lower()
            for dev in AudioUtilities.GetAllDevices():
                n = (dev.FriendlyName or "").lower()
                if n and "Active" in str(dev.state) and (n == low or low in n or n in low):
                    targets.append(dev)
        if not targets:
            targets.append(AudioUtilities.GetSpeakers())
        for dev in targets:
            if hasattr(dev, "EndpointVolume"):
                vols.append(dev.EndpointVolume)
            else:  # older pycaw
                from ctypes import cast, POINTER
                from comtypes import CLSCTX_ALL
                from pycaw.pycaw import IAudioEndpointVolume
                iface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                vols.append(cast(iface, POINTER(IAudioEndpointVolume)))
    except Exception as exc:
        print(f"[bridge] volume endpoint error: {exc}")
    return vols


# ----------------------------------------------------------------------------- siren
def siren_pcm(sr: int, seconds: float = 2.0) -> bytes:
    """Two-tone 960/1440 Hz siren, stereo int16, loopable."""
    frames = bytearray()
    for i in range(int(sr * seconds)):
        t = i / sr
        f = 960.0 if int(t * 4) % 2 == 0 else 1440.0
        v = 0.85 * math.sin(2 * math.pi * f * t) + 0.15 * math.sin(6 * math.pi * f * t)
        s = int(max(-1.0, min(1.0, v)) * 30000)
        frames += struct.pack("<hh", s, s)
    return bytes(frames)


_PCM_CACHE = {}


class Siren:
    def __init__(self):
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.is_ringing = False
        self.code_red = False
        self.alarm_id = None
        self.started_at = None
        self.duration = DEFAULT_DURATION
        self.device_used = None
        self.last_error = None

    def start(self, alarm_id: str, duration: int, code_red: bool) -> bool:
        with self.lock:
            if self.is_ringing:
                return False
            self.stop_event.clear()
            self.is_ringing, self.code_red, self.alarm_id = True, code_red, alarm_id
            self.duration = max(1, int(duration or DEFAULT_DURATION))
            self.started_at = time.time()
            self.last_error = None
            threading.Thread(target=self._run, daemon=True).start()
            return True

    def stop(self):
        self.stop_event.set()

    def _play_sd(self, dev_idx, deadline):
        info = sd.query_devices(dev_idx) if dev_idx is not None else sd.query_devices(kind="output")
        sr = int(info.get("default_samplerate") or 48000)
        pcm = _PCM_CACHE.get(sr) or _PCM_CACHE.setdefault(sr, siren_pcm(sr))
        chunk = (sr // 10) * 4  # 100 ms of stereo int16
        with sd.RawOutputStream(samplerate=sr, channels=2, dtype="int16", device=dev_idx) as stream:
            self.device_used = info["name"]
            pos = 0
            while not self.stop_event.is_set() and time.time() < deadline:
                end = pos + chunk
                part = pcm[pos:end] if end <= len(pcm) else pcm[pos:] + pcm[:end - len(pcm)]
                pos = end % len(pcm)
                stream.write(part)

    def _run(self):
        cfg = load_config()
        name = cfg.get("output", "default")
        deadline = self.started_at + self.duration
        saved, enforcer = [], None
        if PYCAW_OK:
            CoInitialize()
        try:
            if PYCAW_OK and cfg.get("lock_volume") is True:
                for v in _endpoint_volumes(name):
                    try:
                        saved.append((v, v.GetMasterVolumeLevelScalar(), v.GetMute()))
                    except Exception:
                        pass

                def enforce():
                    CoInitialize()
                    try:
                        local = _endpoint_volumes(name)
                        while not self.stop_event.is_set() and time.time() < deadline:
                            for v in local:
                                try:
                                    v.SetMute(0, None)
                                    v.SetMasterVolumeLevelScalar(1.0, None)
                                except Exception:
                                    pass
                            time.sleep(0.4)
                    finally:
                        CoUninitialize()
                enforcer = threading.Thread(target=enforce, daemon=True)
                enforcer.start()

            played = False
            if SD_OK:
                try:
                    self._play_sd(_sd_device_for(name), deadline)
                    played = True
                except Exception as exc:
                    self.last_error = f"Could not play on '{name}': {exc}. Used default output instead."
                    print(f"[bridge] {self.last_error}")
            if not played and IS_WINDOWS and not self.stop_event.is_set():
                wav = os.path.join(HERE, "code_red_siren.wav")
                if not os.path.exists(wav):
                    import wave
                    with wave.open(wav, "w") as w:
                        w.setnchannels(2); w.setsampwidth(2); w.setframerate(44100)
                        w.writeframes(siren_pcm(44100))
                self.device_used = "System default output"
                winsound.PlaySound(wav, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP)
                while not self.stop_event.is_set() and time.time() < deadline:
                    time.sleep(0.2)
                winsound.PlaySound(None, winsound.SND_PURGE)
        finally:
            self.stop_event.set()
            if enforcer:
                enforcer.join(timeout=2)
            for v, level, mute in saved:  # put the user's volume back
                try:
                    v.SetMasterVolumeLevelScalar(level, None)
                    v.SetMute(mute, None)
                except Exception:
                    pass
            if PYCAW_OK:
                CoUninitialize()
            with self.lock:
                self.is_ringing, self.code_red, self.alarm_id = False, False, None

    def status(self) -> dict:
        elapsed = int(time.time() - self.started_at) if self.is_ringing and self.started_at else 0
        cfg = load_config()
        return {
            "is_ringing": self.is_ringing,
            "code_red_active": self.code_red,
            "active_alarm_id": self.alarm_id,
            "elapsed_sec": elapsed,
            "target_duration_sec": self.duration,
            "output": cfg.get("output", "default"),
            "lock_volume": cfg.get("lock_volume") is True,
            "device_used": self.device_used,
            "last_error": self.last_error,
            "capabilities": {"choose_output": SD_OK, "volume_lock": PYCAW_OK, "platform": _sys.platform},
        }


siren = Siren()


def run_onescript(enable: bool) -> dict:
    if not ONESCRIPT_PATH or not os.path.exists(ONESCRIPT_PATH):
        return {"ok": False, "error": f"OneScript not found at '{ONESCRIPT_PATH or '(ONESCRIPT_PATH not set)'}'"}
    if not IS_WINDOWS:
        return {"ok": False, "error": "OneScript (PowerShell) runs on Windows only"}
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ONESCRIPT_PATH,
             "-State", "on" if enable else "off"],
            capture_output=True, text=True, timeout=60)
        return {"ok": proc.returncode == 0, "exit_code": proc.returncode,
                "stdout": proc.stdout[-2000:], "stderr": proc.stderr[-2000:]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ----------------------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth(self):
        if not BRIDGE_TOKEN or self.headers.get("X-Bridge-Token", "") == BRIDGE_TOKEN:
            return True
        self._send({"error": "Missing or wrong X-Bridge-Token"}, 401)
        return False

    def _body(self):
        n = int(self.headers.get("Content-Length", "0") or 0)
        try:
            return json.loads(self.rfile.read(n).decode("utf-8") or "{}") if n else {}
        except Exception:
            return {}

    def do_GET(self):
        if not self._auth():
            return
        if self.path == "/devices":
            cfg = load_config()
            self._send({"devices": list_outputs(), "selected": cfg.get("output", "default"),
                        "lock_volume": cfg.get("lock_volume") is True,
                        "capabilities": siren.status()["capabilities"]})
        elif self.path == "/status":
            self._send(siren.status())
        elif self.path == "/health":
            self._send({"ok": True})
        else:
            self._send({"error": "Not found"}, 404)

    def do_POST(self):
        if not self._auth():
            return
        data = self._body()
        if self.path == "/trigger":
            started = siren.start(data.get("alarm_id", "manual"), int(data.get("duration_sec", DEFAULT_DURATION)),
                                  bool(data.get("code_red", False)))
            self._send({"ok": True, "started": started, **siren.status()})
        elif self.path == "/stop":
            siren.stop()
            self._send({"ok": True})
        elif self.path == "/output":
            cfg = load_config()
            if "output" in data:
                if data["output"] not in [d["name"] for d in list_outputs()]:
                    self._send({"ok": False, "error": "That output device is not connected"}, 400)
                    return
                cfg["output"] = data["output"]
            if "lock_volume" in data:
                cfg["lock_volume"] = bool(data["lock_volume"])
            save_config(cfg)
            self._send({"ok": True, **cfg})
        elif self.path == "/run-onescript":
            self._send(run_onescript(bool(data.get("enable", True))))
        else:
            self._send({"error": "Unknown endpoint"}, 404)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Code Red Alarm host audio bridge")
    ap.add_argument("--list-devices", action="store_true")
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--port", type=int, default=int(os.getenv("HOST_AUDIO_BRIDGE_PORT", "8091")))
    args = ap.parse_args()
    if args.list_devices:
        print(json.dumps(list_outputs(), indent=2))
        _sys.exit(0)
    bind = os.getenv("BRIDGE_BIND", "127.0.0.1")
    print(f"[bridge] http://{bind}:{args.port}  token={'set' if BRIDGE_TOKEN else 'NOT SET'}  "
          f"sounddevice={SD_OK} pycaw={PYCAW_OK}")
    ThreadingHTTPServer((bind, args.port), Handler).serve_forever()
