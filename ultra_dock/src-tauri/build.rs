fn main() {
    // Where opt-in install/uninstall stats and feedback are sent (empty = disabled).
    // Set ULTRASUITE_COLLECTOR_URL when building, or edit ../config/telemetry.json.
    let url = std::env::var("ULTRASUITE_COLLECTOR_URL").ok().filter(|s| !s.is_empty()).unwrap_or_else(|| {
        std::fs::read_to_string("../config/telemetry.json").ok()
            .and_then(|s| serde_json::from_str::<serde_json::Value>(&s).ok())
            .and_then(|v| v["collector_url"].as_str().map(String::from))
            .unwrap_or_default()
    });
    // The Dev edition never sends production telemetry, so its build has no collector at all.
    let url = if std::env::var_os("CARGO_FEATURE_DEV_EDITION").is_some() { String::new() } else { url };
    println!("cargo:rustc-env=ULTRASUITE_COLLECTOR_URL={}", url);
    println!("cargo:rerun-if-changed=../config/telemetry.json");
    println!("cargo:rerun-if-env-changed=ULTRASUITE_COLLECTOR_URL");
    tauri_build::build()
}
