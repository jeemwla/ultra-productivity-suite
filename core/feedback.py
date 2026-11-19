"""Shared 'Send feedback' endpoint for the Alarm and Sentinel apps (forwards to the owner's collector)."""
import os
import platform
import sys
from typing import Optional

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, Field

KINDS = ("idea", "bug", "love")
MAX_MESSAGE = 1000
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Feedback(BaseModel):
    kind: str = Field("idea", max_length=20)   # idea | bug | love; anything else is filed as an idea
    message: str = Field(..., max_length=4 * MAX_MESSAGE)  # longer text is refused before any work is done
    app: str = Field("", max_length=40)


def suite_version() -> str:
    """The running suite's version from VERSION (source checkout or bundled exe), so feedback is filed under the
    release that actually sent it."""
    for base in (_ROOT, getattr(sys, "_MEIPASS", None)):
        if not base:
            continue
        try:
            with open(os.path.join(base, "VERSION"), encoding="utf-8") as f:
                v = f.read().strip()
            if v:
                return v[:20]
        except OSError:
            pass
    return "unknown"


def anon_id() -> str:
    """Random per-PC id (same file the desktop app uses). Created on first use; contains nothing personal."""
    conf = os.environ.get("ULTRASUITE_CONF") or os.path.join(os.environ.get("APPDATA", ""), "UltraSuite")
    p = os.path.join(conf, "id.txt")
    try:
        with open(p, encoding="utf-8") as handle:
            v = handle.read().strip()
        if len(v) >= 16:
            return v
    except Exception:
        pass
    import secrets
    v = secrets.token_hex(12)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as handle:
            handle.write(v)
    except Exception:
        pass
    return v


def add_feedback_route(app, app_name: str, version: Optional[str] = None):
    sent_version = version or suite_version()

    @app.post("/api/feedback")
    async def send_feedback(fb: Feedback):
        if os.environ.get("ULTRASUITE_EDITION") == "Dev":
            raise HTTPException(403, "Feedback is disabled in the Dev edition.")
        msg = fb.message.strip()
        if len(msg) < 3:
            raise HTTPException(400, "Write a few words first.")
        if len(msg) > MAX_MESSAGE:
            raise HTTPException(400, f"Keep it under {MAX_MESSAGE} characters ({len(msg)} now).")
        url = os.environ.get("ULTRASUITE_COLLECTOR", "")
        if not url:
            raise HTTPException(503, "Feedback is turned off in this build.")
        kind = fb.kind if fb.kind in KINDS else "idea"
        body = {"type": "feedback", "reason": f"{app_name}:{kind}"[:40], "message": msg, "version": sent_version,
                "os": f"Windows {platform.release()}", "id": anon_id()}
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(url, json=body)
            if r.status_code == 429:
                raise HTTPException(429, "Too much feedback from this PC in the last hour. Try again later.")
            if r.status_code >= 400:
                raise HTTPException(502, f"Feedback server said {r.status_code}")
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, "Couldn't reach the feedback server. Try again later.")
        return {"ok": True}
