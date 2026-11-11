#!/usr/bin/env python3
"""
GL.iNet firmware 4.x router API client (JSON-RPC at http://<router>/rpc).

Login flow (confirmed against a GL.iNet 4.x router during setup):
  1. {"method":"challenge","params":{"username":"root"}} -> salt, nonce, alg (1 = MD5-crypt), hash-method
  2. cipher = crypt(password, "$1$<salt>$")
  3. hash   = <hash-method>("root:<cipher>:<nonce>")
  4. {"method":"login","params":{"username":"root","hash":hash}} -> sid
  5. {"method":"call","params":[sid, "<module>", "<method>", {...}]}

Command line (read-only, safe):
  python glinet_api.py --probe     sign in and list firmware + which VPN modules answer
"""
import hashlib
import json
import os
import sys
import urllib.request

ITOA64 = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def md5_crypt(password: str, salt: str) -> str:
    """FreeBSD/Linux MD5-crypt ($1$), as used by GL.iNet (Python 3.13 removed the crypt module)."""
    pw, s, magic = password.encode(), salt.encode()[:8], b"$1$"
    final = hashlib.md5(pw + s + pw).digest()
    ctx = pw + magic + s
    for pl in range(len(pw), 0, -16):
        ctx += final[:min(16, pl)]
    i = len(pw)
    while i:
        ctx += b"\x00" if i & 1 else pw[:1]
        i >>= 1
    final = hashlib.md5(ctx).digest()
    for i in range(1000):
        c = pw if i & 1 else final
        if i % 3:
            c += s
        if i % 7:
            c += pw
        c += final if i & 1 else pw
        final = hashlib.md5(c).digest()

    def to64(v, n):
        out = ""
        for _ in range(n):
            out += ITOA64[v & 0x3F]
            v >>= 6
        return out
    f = final
    enc = (to64((f[0] << 16) | (f[6] << 8) | f[12], 4) + to64((f[1] << 16) | (f[7] << 8) | f[13], 4)
           + to64((f[2] << 16) | (f[8] << 8) | f[14], 4) + to64((f[3] << 16) | (f[9] << 8) | f[15], 4)
           + to64((f[4] << 16) | (f[10] << 8) | f[5], 4) + to64(f[11], 2))
    return f"$1${salt[:8]}${enc}"


class GLiNetError(Exception):
    pass


class GLiNet:
    def __init__(self, ip: str, password: str, username: str = "root", timeout: float = 6.0):
        self.url, self.password, self.username, self.timeout = f"http://{ip}/rpc", password, username, timeout
        self.sid = None

    def _rpc(self, method: str, params):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        req = urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.loads(r.read().decode())
        if "error" in data:
            raise GLiNetError(f"{method}: {data['error']}")
        return data.get("result")

    def login(self):
        ch = self._rpc("challenge", {"username": self.username})
        if ch.get("alg") != 1:
            raise GLiNetError(f"Unsupported password algorithm {ch.get('alg')} (only MD5-crypt is implemented)")
        cipher = md5_crypt(self.password, ch["salt"])
        algo = (ch.get("hash-method") or "md5").lower()
        h = hashlib.new(algo, f"{self.username}:{cipher}:{ch['nonce']}".encode()).hexdigest()
        res = self._rpc("login", {"username": self.username, "hash": h})
        self.sid = res.get("sid") if isinstance(res, dict) else None
        if not self.sid:
            raise GLiNetError("Login failed (wrong password?)")
        return self.sid

    def call(self, module: str, method: str, params=None):
        if not self.sid:
            self.login()
        return self._rpc("call", [self.sid, module, method, params or {}])


def _env():
    env = dict(os.environ)
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip())
    return env


def probe():
    env = _env()
    ip, pw = env.get("GLINET_ROUTER_IP") or "192.168.8.1", env.get("GLINET_ADMIN_PASSWORD", "")
    if not pw:
        print("Set GLINET_ADMIN_PASSWORD in .env first (your router's admin password).")
        return 1
    r = GLiNet(ip, pw)
    try:
        r.login()
    except Exception as exc:
        print(f"Sign-in to {ip} failed: {exc}")
        return 1
    print(f"Signed in to {ip}.")
    for mod, meth in (("system", "get_info"), ("system", "get_status")):
        try:
            info = r.call(mod, meth)
            keys = {k: info.get(k) for k in ("model", "firmware_version", "firmware_type", "hardware_version") if isinstance(info, dict) and k in info}
            print(f"  {mod}.{meth}: {keys or str(info)[:200]}")
        except Exception as exc:
            print(f"  {mod}.{meth}: {exc}")
    print("VPN modules on this firmware (read-only checks):")
    for mod, meth in (("vpn-client", "get_status"), ("vpn-client", "get_tunnel"), ("wg-client", "get_status"),
                      ("wg-client", "get_all_config_list"), ("ovpn-client", "get_status"), ("ovpn-client", "get_all_config_list")):
        try:
            out = r.call(mod, meth)
            print(f"  OK  {mod}.{meth}: {json.dumps(out)[:400]}")
        except Exception as exc:
            print(f"  --  {mod}.{meth}: {str(exc)[:120]}")
    return 0


if __name__ == "__main__":
    if "--probe" in sys.argv:
        sys.exit(probe())
    print(__doc__)
