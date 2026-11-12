"""Pure Public/Dev contracts. This module does not select or activate a runtime.

Importing creates no directories, opens no settings and starts no services. The
catalog is read lazily from the shared source JSON. Native Rust embeds that same
file. A capability permits a future operation; in particular Public telemetry
still requires consent. These contracts alone do not enforce runtime isolation.
"""
from dataclasses import dataclass
from functools import lru_cache
import json
import ntpath
from pathlib import Path
import re
from types import MappingProxyType
from typing import Mapping, Sequence

SCHEMA_VERSION = 1
MAX_CATALOG_BYTES = 262_144
_APPS = {"suite", "sentinel", "alarm", "dock", "insights"}
_SERVICES = {"alarm", "sentinel", "audio", "addon", "insights_collector", "insights_dashboard"}
_PROFILE_FIELDS = {
    "edition", "runtime_ready", "product_name", "main_binary", "core_binary", "bundle_identifier",
    "install_dir_name", "roaming_dir_name", "local_dir_name", "run_key", "aumid_prefix",
    "app_names", "ports", "capabilities", "icons",
}
_CAPABILITIES = {"alarm_delivery_enabled", "wake_enabled", "dock_enabled_by_default",
                 "github_self_update_enabled", "production_telemetry_allowed"}
_ICONS = {"colorway", "glyph", "outline", "badge"}
_KEY = re.compile(r"[a-z][a-z0-9_\-]{0,31}\Z")
_BUNDLE = re.compile(r"[a-z0-9]+(?:\.[a-z0-9]+)+\Z")
_AUMID = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z0-9]+)*\Z")
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def _keys(value, expected, label):
    if type(value) is not dict or set(value) != expected:
        raise ValueError(f"Invalid {label} fields")


def _text(value):
    return (type(value) is str and 0 < len(value) <= 120 and value == value.strip()
            and value.isascii() and value.isprintable())


def _component(value):
    return (_text(value) and value not in {".", ".."} and not value.endswith(".")
            and not any(c in value for c in '<>:"/\\|?*')
            and value.split(".")[0].lower() not in _RESERVED)


@dataclass(frozen=True)
class Capabilities:
    alarm_delivery_enabled: bool
    wake_enabled: bool
    dock_enabled_by_default: bool
    github_self_update_enabled: bool
    production_telemetry_allowed: bool


@dataclass(frozen=True)
class IconStyle:
    colorway: str
    glyph: str
    outline: str
    badge: str


@dataclass(frozen=True)
class EditionPaths:
    roaming: Path
    local: Path
    data: Path
    install: Path


@dataclass(frozen=True)
class EditionProfile:
    edition: str
    runtime_ready: bool
    product_name: str
    main_binary: str
    core_binary: str
    bundle_identifier: str
    install_dir_name: str
    roaming_dir_name: str
    local_dir_name: str
    run_key: str
    aumid_prefix: str
    app_names: Mapping[str, str]
    ports: Mapping[str, int]
    capabilities: Capabilities
    icons: IconStyle

    @property
    def is_dev(self):
        return self.edition == "Dev"

    def app_name(self, app):
        try:
            return self.app_names[app]
        except (KeyError, TypeError):
            raise ValueError("Unknown app identity") from None

    def aumid(self, app):
        self.app_name(app)
        return f"{self.aumid_prefix}.{app}"

    def shortcut(self, app):
        return self.app_name(app) + ".lnk"

    def port(self, service):
        try:
            return self.ports[service]
        except (KeyError, TypeError):
            raise ValueError("Unknown owned service") from None

    def paths(self, roaming_root, local_root):
        """Derive paths from explicit roots without reading env or creating them."""
        roaming, local = Path(roaming_root), Path(local_root)
        return EditionPaths(roaming / self.roaming_dir_name, local / self.local_dir_name,
                            local / self.local_dir_name / "data", local / self.install_dir_name)


@dataclass(frozen=True)
class EditionCatalog:
    schema: int
    external_lhm_default_port: int
    profiles: Mapping[str, EditionProfile]

    def profile(self, name):
        if type(name) is str:
            for key, value in self.profiles.items():
                if name.lower() == key.lower():
                    return value
        raise ValueError("Unknown edition; expected Public or Dev")


def validate_catalog(document):
    """Validate a JSON-shaped object and return a deeply read-only contract."""
    _keys(document, {"schema", "external_lhm_default_port", "profiles"}, "catalog")
    if type(document["schema"]) is not int or document["schema"] != SCHEMA_VERSION:
        raise ValueError("Unsupported edition schema")
    external = document["external_lhm_default_port"]
    if type(external) is not int or not 1 <= external <= 65535:
        raise ValueError("Invalid external sensor port")
    _keys(document["profiles"], {"Public", "Dev"}, "edition")
    profiles = {}
    for name, raw in document["profiles"].items():
        _keys(raw, _PROFILE_FIELDS, "profile")
        if raw["edition"] != name:
            raise ValueError("Edition key/identity mismatch")
        if raw["runtime_ready"] is not True:  # false keeps an edition's builds from starting at all
            raise ValueError("Edition runtime integration is not ready")
        for field in ("product_name", "main_binary", "core_binary", "install_dir_name", "roaming_dir_name", "local_dir_name"):
            if not _component(raw[field]):
                raise ValueError("Invalid edition name or path component")
        if not all(raw[k].lower().endswith(".exe") for k in ("main_binary", "core_binary")):
            raise ValueError("Edition executables must end in .exe")
        if raw["main_binary"].lower() == raw["core_binary"].lower():
            raise ValueError("Main and core executable identities must differ")
        if not _text(raw["bundle_identifier"]) or not _BUNDLE.fullmatch(raw["bundle_identifier"]):
            raise ValueError("Invalid bundle identifier")
        if not _text(raw["aumid_prefix"]) or not _AUMID.fullmatch(raw["aumid_prefix"]):
            raise ValueError("Invalid AppUserModelID prefix")
        if not _text(raw["run_key"]) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_\-]{0,63}", raw["run_key"]):
            raise ValueError("Invalid Run key identity")
        apps, ports = raw["app_names"], raw["ports"]
        if type(apps) is not dict or not _APPS <= set(apps):
            raise ValueError("Missing required app identity")
        if type(ports) is not dict or not _SERVICES <= set(ports):
            raise ValueError("Missing required owned service")
        if any(type(k) is not str or not _KEY.fullmatch(k) for k in (*apps, *ports)):
            raise ValueError("Invalid app or service key")
        if any(not _component(v) for v in apps.values()) or len({v.lower() for v in apps.values()}) != len(apps):
            raise ValueError("Invalid or duplicate app names")
        if any(len(raw["aumid_prefix"] + "." + key) > 128 for key in apps):
            raise ValueError("AppUserModelID is too long")
        if any(type(v) is not int or not 1 <= v <= 65535 or v == external for v in ports.values()):
            raise ValueError("Invalid owned service port")
        if len(set(ports.values())) != len(ports):
            raise ValueError("Owned service ports collide")
        _keys(raw["capabilities"], _CAPABILITIES, "capability")
        if any(type(v) is not bool for v in raw["capabilities"].values()):
            raise ValueError("Capabilities must be booleans")
        _keys(raw["icons"], _ICONS, "icon")
        expected_icons = {"colorway": "black", "glyph": "light", "outline": "light", "badge": "Dev"} if name == "Dev" else {"colorway": "public", "glyph": "existing", "outline": "existing", "badge": ""}
        if raw["icons"] != expected_icons:
            raise ValueError("Invalid edition icon style")
        if name == "Dev" and (any(raw["capabilities"].values()) or any(not title.endswith(" (Dev)") for title in apps.values())):
            raise ValueError("Dev must be labeled and inert by default")
        profiles[name] = EditionProfile(**{k: raw[k] for k in _PROFILE_FIELDS - {"app_names", "ports", "capabilities", "icons"}},
            app_names=MappingProxyType(dict(apps)), ports=MappingProxyType(dict(ports)),
            capabilities=Capabilities(**raw["capabilities"]), icons=IconStyle(**raw["icons"]))
    public, dev = profiles["Public"], profiles["Dev"]
    if set(public.app_names) != set(dev.app_names) or set(public.ports) != set(dev.ports):
        raise ValueError("Every app and service requires both editions")
    for field in ("product_name", "bundle_identifier", "run_key", "aumid_prefix"):
        if getattr(public, field).lower() == getattr(dev, field).lower():
            raise ValueError("Edition identities collide")
    for fields in (("main_binary", "core_binary"), ("install_dir_name", "roaming_dir_name", "local_dir_name")):
        left = {getattr(public, f).lower() for f in fields}
        right = {getattr(dev, f).lower() for f in fields}
        if left & right:
            raise ValueError("Edition executables or paths collide")
    if (set(public.ports.values()) & set(dev.ports.values())
            or {v.lower() for v in public.app_names.values()} & {v.lower() for v in dev.app_names.values()}):
        raise ValueError("Edition ports or app names collide")
    return EditionCatalog(SCHEMA_VERSION, external, MappingProxyType(profiles))


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate edition JSON key")
        value[key] = item
    return value


def parse_catalog(text):
    if type(text) is not str or len(text.encode("utf-8")) > MAX_CATALOG_BYTES:
        raise ValueError("Invalid edition catalog size or text")
    return validate_catalog(json.loads(text, object_pairs_hook=_unique_object))


@lru_cache(maxsize=1)
def catalog():
    return parse_catalog(Path(__file__).with_name("edition_profiles.json").read_text(encoding="utf-8"))


def get_profile(name="Public"):
    return catalog().profile(name)


def resolve_profile(executable: str, frozen: bool, argv: Sequence[str] = ()):
    """Select only from a frozen core identity or explicit source --edition.

    argv excludes argv[0]. It may contain other CLI arguments, which this small
    resolver leaves to the application. It never consults inherited environment.
    Unknown frozen identities and duplicate/missing/conflicting flags fail closed.
    """
    if type(executable) is not str or not executable or type(frozen) is not bool:
        raise ValueError("Invalid executable identity or frozen flag")
    if not isinstance(argv, Sequence) or isinstance(argv, (str, bytes)) or any(type(arg) is not str for arg in argv):
        raise ValueError("Edition arguments must be strings")
    requested = None
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--edition" or arg.startswith("--edition="):
            if requested is not None:
                raise ValueError("Duplicate edition argument")
            if arg == "--edition":
                index += 1
                if index == len(argv):
                    raise ValueError("Missing edition argument")
                value = argv[index]
            else:
                value = arg.partition("=")[2]
            requested = get_profile(value)
        index += 1
    if not frozen:
        return requested or get_profile("Public")
    basename = ntpath.basename(executable).lower()
    matches = [p for p in catalog().profiles.values() if p.core_binary.lower() == basename]
    if len(matches) != 1:
        raise ValueError("Unknown frozen core identity")
    selected = matches[0]
    if requested is not None and requested.edition != selected.edition:
        raise ValueError("Frozen core conflicts with requested edition")
    return selected
