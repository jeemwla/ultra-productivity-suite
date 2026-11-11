#!/usr/bin/env python3
"""
VPN button for PC Sentinel.

Two ways to switch the VPN, chosen automatically:
  1. Router API (GL.iNet firmware 4.x): when GLINET_ADMIN_PASSWORD is set in .env.
     Reads vpn-client.get_tunnel, flips "enabled" on the chosen tunnel (GLINET_TUNNEL_ID,
     default: the first tunnel) and sends it back with vpn-client.set_tunnel, then reads
     vpn-client.get_status to confirm the router really changed.
  2. OneScript: when ONESCRIPT_PATH is set and no router password is configured. The host
     bridge runs the script with -State on|off; exit code 0 means success.
State only changes when the router (or script) confirms it.
"""
import asyncio
import os
import time
from typing import Dict, Optional

import httpx

from .glinet_api import GLiNet, GLiNetError


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default) or default


class VPNController:
    def __init__(self):
        self.router_ip = _env("GLINET_ROUTER_IP", "192.168.8.1")
        self.webhook = _env("ONESCRIPT_WEBHOOK_URL", "http://127.0.0.1:8091/run-onescript")
        self.token = _env("BRIDGE_TOKEN")
        self.onescript_path = _env("ONESCRIPT_PATH")
        self.vpn_enabled: Optional[bool] = None
        self.tunnel_name: Optional[str] = None
        self.peer_name: Optional[str] = None
        self.last_ok: Optional[bool] = None
        self.message = "Ready."
        self._router: Optional[GLiNet] = None
        self._last_refresh = 0.0

    # ------------------------------------------------------------------ router helpers
    @property
    def mode(self) -> str:
        if _env("GLINET_ADMIN_PASSWORD"):
            return "router"
        if self.onescript_path:
            return "onescript"
        return "none"

    def _r(self) -> GLiNet:
        if not self._router:
            self._router = GLiNet(self.router_ip, _env("GLINET_ADMIN_PASSWORD"))
        return self._router

    def _call(self, module, method, params=None):
        try:
            return self._r().call(module, method, params)
        except GLiNetError as exc:
            if "Access denied" in str(exc) or "-32000" in str(exc) or "sid" in str(exc).lower():
                self._r().sid = None  # session expired: sign in again once
                return self._r().call(module, method, params)
            raise

    def _pick_tunnel(self, tunnels):
        want = _env("GLINET_TUNNEL_ID")
        if want:
            for t in tunnels:
                if str(t.get("tunnel_id")) == want:
                    return t
        return tunnels[0] if tunnels else None

    def _read_router_state(self):
        status = self._call("vpn-client", "get_status") or {}
        lst = status.get("status_list") or []
        want = _env("GLINET_TUNNEL_ID")
        cur = next((s for s in lst if want and str(s.get("tunnel_id")) == want), lst[0] if lst else None)
        if cur:
            self.vpn_enabled = bool(cur.get("enabled"))
            self.tunnel_name, self.peer_name = cur.get("name"), cur.get("peer_name")
        return cur

    async def refresh(self, force: bool = False):
        """Keep the on/off state in sync with the router (at most every 15 s)."""
        if self.mode != "router" or (not force and time.time() - self._last_refresh < 15):
            return
        self._last_refresh = time.time()
        try:
            await asyncio.to_thread(self._read_router_state)
        except Exception as exc:
            self.message = f"Router not reachable: {str(exc)[:120]}"

    # ------------------------------------------------------------------ toggle
    def _router_toggle(self, target: bool) -> bool:
        tunnels = (self._call("vpn-client", "get_tunnel") or {}).get("tunnels") or []
        tun = self._pick_tunnel(tunnels)
        if not tun:
            raise GLiNetError("No VPN tunnel is set up on the router. Add one in the router's VPN Dashboard first.")
        tun = dict(tun)
        tun["enabled"] = bool(target)
        res = self._call("vpn-client", "set_tunnel", tun)
        if isinstance(res, dict) and res.get("err_code"):
            raise GLiNetError(f"Router refused: {res.get('err_msg')} ({res.get('err_code')})")
        # confirm
        for _ in range(8):
            time.sleep(0.6)
            cur = self._read_router_state()
            if cur is not None and bool(cur.get("enabled")) == bool(target):
                return True
        return False

    async def toggle_vpn(self, desired_state: bool = None) -> Dict:
        if self.mode == "router":
            await self.refresh(force=True)
            target = (not bool(self.vpn_enabled)) if desired_state is None else bool(desired_state)
            try:
                ok = await asyncio.to_thread(self._router_toggle, target)
                self.last_ok = ok
                self.message = (f"VPN {'on' if target else 'off'} via {self.tunnel_name or 'tunnel'}"
                                + (f" ({self.peer_name})" if self.peer_name and target else "")) if ok else \
                    "The router accepted the change but didn't confirm it yet. Check the router's VPN Dashboard."
            except Exception as exc:
                self.last_ok = False
                self.message = f"FAILED: {str(exc)[:200]}"
            return self.status()

        if self.mode == "onescript":
            target = (not bool(self.vpn_enabled)) if desired_state is None else bool(desired_state)
            try:
                async with httpx.AsyncClient(timeout=65, headers={"X-Bridge-Token": self.token}) as client:
                    data = (await client.post(self.webhook, json={"enable": target})).json()
            except Exception as exc:
                self.last_ok, self.message = False, f"FAILED: speaker/script helper unreachable ({exc.__class__.__name__})"
                return self.status()
            if data.get("ok"):
                self.vpn_enabled, self.last_ok = target, True
                out = (data.get("stdout") or "").strip().splitlines()
                self.message = f"OneScript ran: VPN {'on' if target else 'off'}" + (f" ({out[-1][:120]})" if out else "")
            else:
                self.last_ok = False
                self.message = "FAILED: " + (data.get("error") or (data.get("stderr") or "").strip()[-200:] or f"exit {data.get('exit_code')}")
            return self.status()

        self.last_ok = False
        self.message = "Not set up: add your router password (GLINET_ADMIN_PASSWORD) or a OneScript path to .env."
        return self.status()

    def status(self) -> Dict:
        return {"router_ip": self.router_ip, "mode": self.mode, "vpn_enabled": self.vpn_enabled,
                "tunnel": self.tunnel_name, "server": self.peer_name, "last_ok": self.last_ok,
                "message": self.message}


vpn_controller = VPNController()
