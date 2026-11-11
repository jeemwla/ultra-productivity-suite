#!/usr/bin/env python3
'''
GL.iNet VPN toggle for App 4 (fixed version).

- This backend runs inside Docker (Linux), so it cannot run a Windows PowerShell script itself.
  It asks the host bridge (01_pebble_code_red_alarm/backend/audio_enforcer.py, port 8091) to run
  the user's OneScript: POST {ONESCRIPT_WEBHOOK_URL} {"enable": true|false}.
- The OneScript is where the real router logic lives (GL.iNet web API, SSH, etc.).
- State only changes when the script reports success. Failures are shown as failures.

NOTE: a direct GL.iNet firmware 4.x JSON-RPC client is NOT implemented, because the official
API docs (https://dev.gl-inet.com/router-4.x-api/) could not be fetched and verified during
setup. See HANDOFF_MASTER_PROMPT.md for how to finish it.
'''
import os
from typing import Dict
import httpx


class GLiNetVPNController:
    def __init__(self):
        self.router_ip = os.getenv("GLINET_ROUTER_IP", "192.168.8.1")
        self.vpn_type = os.getenv("GLINET_VPN_TYPE", "wireguard")
        self.webhook = os.getenv("ONESCRIPT_WEBHOOK_URL", "http://host.docker.internal:8091/run-onescript")
        self.token = os.getenv("BRIDGE_TOKEN", "")
        self.onescript_path = os.getenv("ONESCRIPT_PATH", "")
        self.vpn_enabled = False
        self.last_ok = None
        self.last_action_msg = "Idle. Press the button to run OneScript."

    async def toggle_vpn(self, desired_state: bool = None) -> Dict:
        target = (not self.vpn_enabled) if desired_state is None else bool(desired_state)
        try:
            async with httpx.AsyncClient(timeout=65, headers={"X-Bridge-Token": self.token}) as client:
                resp = await client.post(self.webhook, json={"enable": target})
                data = resp.json()
        except Exception as exc:
            self.last_ok = False
            self.last_action_msg = f"FAILED: host bridge unreachable at {self.webhook} ({exc})"
            return self.status()

        if data.get("ok"):
            self.vpn_enabled = target
            self.last_ok = True
            out = (data.get("stdout") or "").strip().splitlines()
            self.last_action_msg = f"OneScript OK -> VPN {'ON' if target else 'OFF'}" + (f": {out[-1]}" if out else "")
        else:
            self.last_ok = False
            err = data.get("error") or (data.get("stderr") or "").strip()[-300:] or f"exit {data.get('exit_code')}"
            self.last_action_msg = "FAILED: " + err
        return self.status()

    def status(self) -> Dict:
        return {
            "router_ip": self.router_ip,
            "vpn_type": self.vpn_type.upper(),
            "vpn_enabled": self.vpn_enabled,
            "last_ok": self.last_ok,
            "onescript_path": self.onescript_path,
            "message": self.last_action_msg
        }


vpn_controller = GLiNetVPNController()
