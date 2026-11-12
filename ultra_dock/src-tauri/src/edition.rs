//! Shared edition contracts only; the legacy runtime is not yet edition-aware.
//!
//! This module embeds the same JSON read by core/editions.py. It performs no
//! settings, registry, network, directory or process operations. Phase 1 keeps
//! the new Dev feature fail-closed until later runtime integration is verified.
#![allow(dead_code)]
use serde::{
    de::{self, MapAccess, Visitor},
    Deserialize, Deserializer,
};
use std::collections::{BTreeMap, BTreeSet};
use std::fmt;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

pub const CONTRACT_JSON: &str = include_str!("../../../core/edition_profiles.json");
const MAX_CATALOG_BYTES: usize = 262_144;
const APPS: [&str; 5] = ["suite", "sentinel", "alarm", "dock", "insights"];
const SERVICES: [&str; 6] = [
    "alarm",
    "sentinel",
    "audio",
    "addon",
    "insights_collector",
    "insights_dashboard",
];

/// Unlike the default map deserializer, reject duplicate identity/port keys.
#[derive(Debug)]
struct UniqueMap<T>(BTreeMap<String, T>);
impl<'de, T: Deserialize<'de>> Deserialize<'de> for UniqueMap<T> {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        struct UniqueVisitor<T>(std::marker::PhantomData<T>);
        impl<'de, T: Deserialize<'de>> Visitor<'de> for UniqueVisitor<T> {
            type Value = UniqueMap<T>;
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
                f.write_str("a map with unique keys")
            }
            fn visit_map<A: MapAccess<'de>>(self, mut input: A) -> Result<Self::Value, A::Error> {
                let mut map = BTreeMap::new();
                while let Some((key, value)) = input.next_entry::<String, T>()? {
                    if map.insert(key, value).is_some() {
                        return Err(de::Error::custom("duplicate edition map key"));
                    }
                }
                Ok(UniqueMap(map))
            }
        }
        deserializer.deserialize_map(UniqueVisitor(std::marker::PhantomData))
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Capabilities {
    pub alarm_delivery_enabled: bool,
    pub wake_enabled: bool,
    pub dock_enabled_by_default: bool,
    pub github_self_update_enabled: bool,
    pub production_telemetry_allowed: bool,
}
impl Capabilities {
    fn inert(&self) -> bool {
        !self.alarm_delivery_enabled
            && !self.wake_enabled
            && !self.dock_enabled_by_default
            && !self.github_self_update_enabled
            && !self.production_telemetry_allowed
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IconStyle {
    colorway: String,
    glyph: String,
    outline: String,
    badge: String,
}
impl IconStyle {
    pub fn colorway(&self) -> &str {
        &self.colorway
    }
    pub fn glyph(&self) -> &str {
        &self.glyph
    }
    pub fn outline(&self) -> &str {
        &self.outline
    }
    pub fn badge(&self) -> &str {
        &self.badge
    }
}

#[derive(Debug, PartialEq)]
pub struct EditionPaths {
    pub roaming: PathBuf,
    pub local: PathBuf,
    pub data: PathBuf,
    pub install: PathBuf,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EditionProfile {
    edition: String,
    runtime_ready: bool,
    product_name: String,
    main_binary: String,
    core_binary: String,
    bundle_identifier: String,
    install_dir_name: String,
    roaming_dir_name: String,
    local_dir_name: String,
    run_key: String,
    aumid_prefix: String,
    app_names: UniqueMap<String>,
    ports: UniqueMap<u16>,
    capabilities: Capabilities,
    icons: IconStyle,
}
impl EditionProfile {
    pub fn edition(&self) -> &str {
        &self.edition
    }
    pub fn is_dev(&self) -> bool {
        self.edition == "Dev"
    }
    pub fn runtime_ready(&self) -> bool {
        self.runtime_ready
    }
    pub fn product_name(&self) -> &str {
        &self.product_name
    }
    pub fn main_binary(&self) -> &str {
        &self.main_binary
    }
    pub fn core_binary(&self) -> &str {
        &self.core_binary
    }
    pub fn bundle_identifier(&self) -> &str {
        &self.bundle_identifier
    }
    pub fn run_key(&self) -> &str {
        &self.run_key
    }
    pub fn aumid_prefix(&self) -> &str {
        &self.aumid_prefix
    }
    pub fn capabilities(&self) -> &Capabilities {
        &self.capabilities
    }
    pub fn icons(&self) -> &IconStyle {
        &self.icons
    }
    pub fn app_names(&self) -> &BTreeMap<String, String> {
        &self.app_names.0
    }
    pub fn ports(&self) -> &BTreeMap<String, u16> {
        &self.ports.0
    }
    pub fn app_name(&self, app: &str) -> Result<&str, &'static str> {
        self.app_names
            .0
            .get(app)
            .map(String::as_str)
            .ok_or("unknown app identity")
    }
    pub fn aumid(&self, app: &str) -> Result<String, &'static str> {
        self.app_name(app)?;
        Ok(format!("{}.{}", self.aumid_prefix, app))
    }
    pub fn shortcut(&self, app: &str) -> Result<String, &'static str> {
        Ok(format!("{}.lnk", self.app_name(app)?))
    }
    pub fn port(&self, service: &str) -> Result<u16, &'static str> {
        self.ports
            .0
            .get(service)
            .copied()
            .ok_or("unknown owned service")
    }
    pub fn paths(&self, roaming_root: &Path, local_root: &Path) -> EditionPaths {
        EditionPaths {
            roaming: roaming_root.join(&self.roaming_dir_name),
            local: local_root.join(&self.local_dir_name),
            data: local_root.join(&self.local_dir_name).join("data"),
            install: local_root.join(&self.install_dir_name),
        }
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Profiles {
    #[serde(rename = "Public")]
    public: EditionProfile,
    #[serde(rename = "Dev")]
    dev: EditionProfile,
}
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EditionCatalog {
    schema: u32,
    external_lhm_default_port: u16,
    profiles: Profiles,
}
impl EditionCatalog {
    pub fn profile(&self, name: &str) -> Result<&EditionProfile, &'static str> {
        if name.eq_ignore_ascii_case("Public") {
            Ok(&self.profiles.public)
        } else if name.eq_ignore_ascii_case("Dev") {
            Ok(&self.profiles.dev)
        } else {
            Err("unknown edition; expected Public or Dev")
        }
    }
    pub fn external_lhm_default_port(&self) -> u16 {
        self.external_lhm_default_port
    }
    fn validate(&self) -> Result<(), &'static str> {
        if self.schema != 1 || self.external_lhm_default_port == 0 {
            return Err("unsupported edition schema or sensor port");
        }
        for (name, p) in [
            ("Public", &self.profiles.public),
            ("Dev", &self.profiles.dev),
        ] {
            if p.edition != name || p.runtime_ready != (name == "Public") {
                return Err("edition identity or unready Dev runtime mismatch");
            }
            for value in [
                &p.product_name,
                &p.main_binary,
                &p.core_binary,
                &p.install_dir_name,
                &p.roaming_dir_name,
                &p.local_dir_name,
            ] {
                if !component(value) {
                    return Err("invalid edition name or path component");
                }
            }
            if !p.main_binary.to_ascii_lowercase().ends_with(".exe")
                || !p.core_binary.to_ascii_lowercase().ends_with(".exe")
                || p.main_binary.eq_ignore_ascii_case(&p.core_binary)
            {
                return Err("invalid executable identities");
            }
            let bundle: Vec<_> = p.bundle_identifier.split('.').collect();
            if !text(&p.bundle_identifier)
                || bundle.len() < 2
                || bundle.iter().any(|s| {
                    s.is_empty()
                        || !s
                            .bytes()
                            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit())
                })
            {
                return Err("invalid bundle identifier");
            }
            if !text(&p.aumid_prefix)
                || !p.aumid_prefix.as_bytes()[0].is_ascii_alphabetic()
                || p.aumid_prefix
                    .split('.')
                    .any(|s| s.is_empty() || !s.bytes().all(|c| c.is_ascii_alphanumeric()))
            {
                return Err("invalid AUMID prefix");
            }
            if !text(&p.run_key)
                || p.run_key.len() > 64
                || !p.run_key.as_bytes()[0].is_ascii_alphabetic()
                || !p
                    .run_key
                    .bytes()
                    .all(|c| c.is_ascii_alphanumeric() || c == b'_' || c == b'-')
            {
                return Err("invalid Run identity");
            }
            if !APPS.iter().all(|k| p.app_names.0.contains_key(*k))
                || !SERVICES.iter().all(|k| p.ports.0.contains_key(*k))
            {
                return Err("missing required app or service");
            }
            if p.app_names
                .0
                .keys()
                .chain(p.ports.0.keys())
                .any(|k| !key(k))
            {
                return Err("invalid app or service key");
            }
            if p.app_names.0.values().any(|v| !component(v))
                || folded(p.app_names.0.values()).len() != p.app_names.0.len()
                || p.app_names
                    .0
                    .keys()
                    .any(|k| p.aumid_prefix.len() + 1 + k.len() > 128)
            {
                return Err("invalid or duplicate app identities");
            }
            let ports: BTreeSet<_> = p.ports.0.values().copied().collect();
            if ports.len() != p.ports.0.len()
                || ports.contains(&0)
                || ports.contains(&self.external_lhm_default_port)
            {
                return Err("owned ports collide or use external sensor port");
            }
            let icons = (
                &*p.icons.colorway,
                &*p.icons.glyph,
                &*p.icons.outline,
                &*p.icons.badge,
            );
            let expected = if name == "Dev" {
                ("black", "light", "light", "Dev")
            } else {
                ("public", "existing", "existing", "")
            };
            if icons != expected {
                return Err("invalid edition icon style");
            }
            if name == "Dev"
                && (!p.capabilities.inert()
                    || p.app_names.0.values().any(|s| !s.ends_with(" (Dev)")))
            {
                return Err("Dev must be labeled and inert by default");
            }
        }
        let (p, d) = (&self.profiles.public, &self.profiles.dev);
        if p.app_names.0.keys().ne(d.app_names.0.keys()) || p.ports.0.keys().ne(d.ports.0.keys()) {
            return Err("every app and service requires both editions");
        }
        for (left, right) in [
            (&p.product_name, &d.product_name),
            (&p.bundle_identifier, &d.bundle_identifier),
            (&p.run_key, &d.run_key),
            (&p.aumid_prefix, &d.aumid_prefix),
        ] {
            if left.eq_ignore_ascii_case(right) {
                return Err("edition identities collide");
            }
        }
        let executables_p = folded([&p.main_binary, &p.core_binary]);
        let executables_d = folded([&d.main_binary, &d.core_binary]);
        let paths_p = folded([&p.install_dir_name, &p.roaming_dir_name, &p.local_dir_name]);
        let paths_d = folded([&d.install_dir_name, &d.roaming_dir_name, &d.local_dir_name]);
        if !executables_p.is_disjoint(&executables_d)
            || !paths_p.is_disjoint(&paths_d)
            || !folded(p.app_names.0.values()).is_disjoint(&folded(d.app_names.0.values()))
            || p.ports
                .0
                .values()
                .any(|v| d.ports.0.values().any(|w| v == w))
        {
            return Err("edition paths, app names or ports collide");
        }
        Ok(())
    }
}
fn text(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 120
        && value.trim() == value
        && value.bytes().all(|c| (32..=126).contains(&c))
}
fn key(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 32
        && value.as_bytes()[0].is_ascii_lowercase()
        && value
            .bytes()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'_' || c == b'-')
}
fn component(value: &str) -> bool {
    let stem = value.split('.').next().unwrap_or("").to_ascii_lowercase();
    let reserved = ["con", "prn", "aux", "nul"].contains(&stem.as_str())
        || ((stem.starts_with("com") || stem.starts_with("lpt"))
            && stem.len() == 4
            && (b'1'..=b'9').contains(&stem.as_bytes()[3]));
    text(value)
        && value != "."
        && value != ".."
        && !value.ends_with('.')
        && !value.chars().any(|c| "<>:\"/\\|?*".contains(c))
        && !reserved
}
fn folded<'a>(values: impl IntoIterator<Item = &'a String>) -> BTreeSet<String> {
    values.into_iter().map(|s| s.to_ascii_lowercase()).collect()
}

pub fn parse_catalog(text: &str) -> Result<EditionCatalog, String> {
    if text.len() > MAX_CATALOG_BYTES {
        return Err("edition catalog is too large".into());
    }
    let catalog: EditionCatalog = serde_json::from_str(text).map_err(|e| e.to_string())?;
    catalog.validate().map_err(str::to_string)?;
    Ok(catalog)
}
pub fn catalog() -> Result<&'static EditionCatalog, &'static str> {
    static CATALOG: OnceLock<Result<EditionCatalog, String>> = OnceLock::new();
    CATALOG
        .get_or_init(|| parse_catalog(CONTRACT_JSON))
        .as_ref()
        .map_err(String::as_str)
}
pub fn profile(name: &str) -> Result<&'static EditionProfile, &'static str> {
    catalog()?.profile(name)
}
pub fn current() -> Result<&'static EditionProfile, &'static str> {
    profile(if cfg!(feature = "dev-edition") {
        "Dev"
    } else {
        "Public"
    })
}
pub fn runtime_ready() -> bool {
    current().is_ok_and(EditionProfile::runtime_ready)
}

pub fn resolve_profile(
    executable: &str,
    frozen: bool,
    args: &[String],
) -> Result<&'static EditionProfile, &'static str> {
    if executable.is_empty() {
        return Err("invalid executable identity");
    }
    let mut requested: Option<&EditionProfile> = None;
    let mut index = 0;
    while index < args.len() {
        let arg = &args[index];
        if arg == "--edition" || arg.starts_with("--edition=") {
            if requested.is_some() {
                return Err("duplicate edition argument");
            }
            let value = if arg == "--edition" {
                index += 1;
                args.get(index).ok_or("missing edition argument")?.as_str()
            } else {
                arg.strip_prefix("--edition=").unwrap_or("")
            };
            requested = Some(profile(value)?);
        }
        index += 1;
    }
    if !frozen {
        return requested.map(Ok).unwrap_or_else(|| profile("Public"));
    }
    let basename = executable.rsplit(['/', '\\']).next().unwrap_or("");
    let selected = [profile("Public")?, profile("Dev")?]
        .into_iter()
        .find(|p| p.core_binary.eq_ignore_ascii_case(basename))
        .ok_or("unknown frozen core identity")?;
    if requested.is_some_and(|p| p.edition != selected.edition) {
        return Err("frozen core conflicts with requested edition");
    }
    Ok(selected)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    #[test]
    fn profiles_share_one_contract_and_keep_public_identities() {
        let p = profile("Public").unwrap();
        let d = profile("Dev").unwrap();
        assert_eq!(p.product_name(), "Ultra Suite");
        assert_eq!(p.bundle_identifier(), "com.ultrasuite.app");
        assert_eq!(d.product_name(), "Ultra Suite (Dev)");
        assert_eq!(d.core_binary(), "ultrasuite-dev-core.exe");
        assert_eq!(d.app_name("insights").unwrap(), "Insights (Dev)");
        for (service, port) in [
            ("alarm", 18081),
            ("sentinel", 18084),
            ("audio", 18091),
            ("addon", 18093),
            ("insights_collector", 18300),
            ("insights_dashboard", 18301),
        ] {
            assert_eq!(d.port(service).unwrap(), port);
        }
        assert_eq!(catalog().unwrap().external_lhm_default_port(), 8085);
        assert!(d.capabilities().inert());
        assert_eq!(d.icons().colorway(), "black");
        assert_eq!(d.aumid("sentinel").unwrap(), "UltraSuite.Dev.sentinel");
        assert!(d.port("lhm").is_err());
    }
    #[test]
    fn paths_are_derived_without_creating_or_looking_up_anything() {
        let p = profile("Dev")
            .unwrap()
            .paths(Path::new("fixture-roaming"), Path::new("fixture-local"));
        assert_eq!(
            p.roaming,
            PathBuf::from("fixture-roaming").join("UltraSuite-Dev")
        );
        assert_eq!(
            p.data,
            PathBuf::from("fixture-local").join("UltraSuite-Dev/data")
        );
        assert_eq!(
            p.install,
            PathBuf::from("fixture-local").join("Ultra Suite (Dev)")
        );
    }
    #[test]
    fn frozen_resolution_rejects_unknown_and_conflicting_identities() {
        assert_eq!(
            resolve_profile(r"C:\fixture\ULTRASUITE-DEV-CORE.EXE", true, &[])
                .unwrap()
                .edition(),
            "Dev"
        );
        assert_eq!(
            resolve_profile("python.exe", false, &[]).unwrap().edition(),
            "Public"
        );
        assert_eq!(
            resolve_profile("python.exe", false, &["--edition=Dev".into()])
                .unwrap()
                .edition(),
            "Dev"
        );
        for args in [
            vec!["--edition".into()],
            vec!["--edition=unknown".into()],
            vec!["--edition=Public".into(), "--edition=Dev".into()],
        ] {
            assert!(resolve_profile("python.exe", false, &args).is_err());
        }
        assert!(resolve_profile("ultrasuite-core.exe", true, &["--edition=Dev".into()]).is_err());
        assert!(resolve_profile("unknown-core.exe", true, &[]).is_err());
        assert!(profile("Development").is_err());
        assert!(profile(" Public ").is_err());
    }
    #[test]
    fn native_feature_cannot_activate_the_unintegrated_dev_runtime() {
        assert!(!profile("Dev").unwrap().runtime_ready());
        assert_eq!(
            current().unwrap().edition(),
            if cfg!(feature = "dev-edition") {
                "Dev"
            } else {
                "Public"
            }
        );
        assert_eq!(runtime_ready(), !cfg!(feature = "dev-edition"));
    }
    #[test]
    fn malformed_schema_ports_and_dev_delivery_fail_closed() {
        let changes = [
            ("/schema", json!(true)),
            ("/profiles/Dev/runtime_ready", json!(true)),
            ("/profiles/Dev/ports/alarm", json!(8081)),
            ("/profiles/Dev/ports/audio", json!(18081)),
            ("/profiles/Dev/ports/alarm", json!(8085)),
            ("/profiles/Dev/ports/alarm", json!(true)),
            ("/profiles/Dev/roaming_dir_name", json!("../UltraSuite")),
            ("/profiles/Dev/icons/colorway", json!("orange")),
            (
                "/profiles/Dev/capabilities/alarm_delivery_enabled",
                json!(true),
            ),
            ("/profiles/Dev/capabilities/wake_enabled", json!(1)),
            ("/profiles/Dev/app_names/alarm", json!("Code Red Alarm")),
            ("/profiles/Dev/run_key", json!("UltraSuite")),
        ];
        for (pointer, value) in changes {
            let mut doc: serde_json::Value = serde_json::from_str(CONTRACT_JSON).unwrap();
            *doc.pointer_mut(pointer).unwrap() = value;
            assert!(parse_catalog(&doc.to_string()).is_err(), "{pointer}");
        }
        assert!(parse_catalog(&CONTRACT_JSON.replacen(
            "\"schema\": 1",
            "\"schema\": 1, \"schema\": 1",
            1
        ))
        .is_err());
        assert!(parse_catalog(&CONTRACT_JSON.replacen(
            "\"alarm\": 18081",
            "\"alarm\": 18081, \"alarm\": 18081",
            1
        ))
        .is_err());
    }
    #[test]
    fn future_apps_require_both_names_and_owned_ports() {
        let mut doc: serde_json::Value = serde_json::from_str(CONTRACT_JSON).unwrap();
        doc["profiles"]["Public"]["app_names"]["planner"] = json!("School Planner");
        doc["profiles"]["Public"]["ports"]["planner"] = json!(8200);
        assert!(parse_catalog(&doc.to_string()).is_err());
        doc["profiles"]["Dev"]["app_names"]["planner"] = json!("School Planner (Dev)");
        doc["profiles"]["Dev"]["ports"]["planner"] = json!(18200);
        let c = parse_catalog(&doc.to_string()).unwrap();
        assert_eq!(
            c.profile("Dev").unwrap().aumid("planner").unwrap(),
            "UltraSuite.Dev.planner"
        );
    }
    #[test]
    fn complete_aumid_must_fit_windows_limit() {
        let mut doc: serde_json::Value = serde_json::from_str(CONTRACT_JSON).unwrap();
        doc["profiles"]["Dev"]["aumid_prefix"] = json!("D".repeat(120));
        let future_key = "a".repeat(32);
        doc["profiles"]["Public"]["app_names"][&future_key] = json!("Future App");
        doc["profiles"]["Dev"]["app_names"][&future_key] = json!("Future App (Dev)");
        assert!(parse_catalog(&doc.to_string()).is_err());
    }
}
