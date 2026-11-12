"""Shared 'Send feedback' endpoint for the Alarm and Sentinel apps (forwards to the owner's collector)."""
import os
import platform

import httpx
from fastapi import HTTPException
from pydantic import BaseModel


class Feedback(BaseModel):
    kind: str = "idea"        # idea | bug | love
    message: str
    app: str = ""


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


def add_feedback_route(app, app_name: str, version: str = "2.0.1"):
    @app.post("/api/feedback")
    async def send_feedback(fb: Feedback):
        if os.environ.get("ULTRASUITE_EDITION") == "Dev":
            raise HTTPException(403, "Feedback is disabled in the Dev edition.")
        msg = fb.message.strip()
        if len(msg) < 3:
            raise HTTPException(400, "Write a few words first.")
        url = os.environ.get("ULTRASUITE_COLLECTOR", "")
        if not url:
            raise HTTPException(503, "Feedback is turned off in this build.")
        body = {"type": "feedback", "reason": f"{app_name}:{fb.kind}"[:40], "message": msg[:1000], "version": version,
                "os": f"Windows {platform.release()}", "id": anon_id()}
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(url, json=body)
            if r.status_code >= 400:
                raise HTTPException(502, f"Feedback server said {r.status_code}")
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, "Couldn't reach the feedback server. Try again later.")
        return {"ok": True}
