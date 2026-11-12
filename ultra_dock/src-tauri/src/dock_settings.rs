//! Dock settings window support: checks and cleans what the settings page wants to save to organizer.json, so a
//! bad edit can never break the Dock. Kept free of Windows calls so it can be tested.
use serde_json::{json, Map, Value};

pub const MAX_CATEGORIES: usize = 40;
pub const MAX_KEYWORDS: usize = 200;
pub const MAX_KEEP: usize = 200;
pub const MAX_TEXT: usize = 120;
/// Apps that match no category land here; it always exists and cannot be removed.
pub const OTHER: &str = "Other";

fn clean_text(s: &str) -> Option<String> {
    let t: String = s.trim().chars().filter(|c| !c.is_control()).take(MAX_TEXT).collect();
    let t = t.trim().to_string();
    (!t.is_empty()).then_some(t)
}

fn clean_list(v: &Value, max: usize, lower: bool) -> Vec<String> {
    let mut out: Vec<String> = vec![];
    for s in v.as_array().into_iter().flatten().filter_map(|x| x.as_str()).filter_map(clean_text) {
        let s = if lower { s.to_lowercase() } else { s };
        if !out.iter().any(|o| o.eq_ignore_ascii_case(&s)) { out.push(s); }
        if out.len() >= max { break; }
    }
    out
}

/// Returns the organizer.json document to write, or a message saying what is wrong.
/// - keep: app path pieces that stay as normal taskbar buttons
/// - order: category names in display order (every category appears once; "Other" is always present)
/// - categories: name -> keywords matched against the app's file path
/// - overrides: app file name -> category (must be a known category)
pub fn sanitize(input: &Value, help: &str) -> Result<Value, String> {
    if !input.is_object() { return Err("The settings could not be read.".into()); }
    let keep = clean_list(&input["keep"], MAX_KEEP, false);
    let mut cats: Vec<(String, Vec<String>)> = vec![];
    if let Some(m) = input["categories"].as_object() {
        for (name, list) in m {
            let Some(name) = clean_text(name) else { continue };
            if name.starts_with("__") { return Err(format!("\"{name}\" cannot be used as a category name.")); }
            if cats.iter().any(|(n, _)| n.eq_ignore_ascii_case(&name)) { return Err(format!("There are two categories called \"{name}\".")); }
            cats.push((name, clean_list(list, MAX_KEYWORDS, true)));
        }
    }
    if cats.len() > MAX_CATEGORIES { return Err(format!("At most {MAX_CATEGORIES} categories.")); }
    // order: the requested order first, then any category it left out; "Other" last unless placed
    let mut order: Vec<String> = vec![];
    for o in clean_list(&input["order"], MAX_CATEGORIES + 1, false) {
        let known = cats.iter().find(|(n, _)| n.eq_ignore_ascii_case(&o)).map(|(n, _)| n.clone())
            .or_else(|| o.eq_ignore_ascii_case(OTHER).then(|| OTHER.to_string()));
        if let Some(k) = known { if !order.contains(&k) { order.push(k); } }
    }
    for (n, _) in &cats { if !order.contains(n) { order.push(n.clone()); } }
    if !order.iter().any(|o| o == OTHER) { order.push(OTHER.to_string()); }
    let mut overrides = Map::new();
    if let Some(m) = input["overrides"].as_object() {
        for (file, cat) in m {
            let (Some(file), Some(cat)) = (clean_text(file), cat.as_str().and_then(clean_text)) else { continue };
            let file = file.rsplit(['\\', '/']).next().unwrap_or(&file).to_lowercase();
            let Some(cat) = order.iter().find(|o| o.eq_ignore_ascii_case(&cat)).cloned() else { continue };
            overrides.insert(file, Value::String(cat));
        }
    }
    let mut categories = Map::new();
    for (n, k) in cats { if n != OTHER { categories.insert(n, json!(k)); } }
    Ok(json!({ "_help": help, "keep": keep, "order": order, "categories": categories, "overrides": overrides }))
}

/// The keep-list entry for an app: its file name with a leading backslash, matched against the full path.
pub fn keep_entry(exe: &str) -> String {
    let file = exe.rsplit(['\\', '/']).next().unwrap_or(exe);
    format!("\\{file}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_normal_document_round_trips() {
        let doc = json!({ "keep": ["\\Opera\\opera.exe"], "order": ["Games", "Chat"],
            "categories": { "Games": ["riot", "Steam"], "Chat": ["discord"] }, "overrides": { "Beeper.exe": "Chat" } });
        let out = sanitize(&doc, "help").unwrap();
        assert_eq!(out["keep"], json!(["\\Opera\\opera.exe"]));
        assert_eq!(out["order"], json!(["Games", "Chat", "Other"]));
        assert_eq!(out["categories"]["Games"], json!(["riot", "steam"]), "keywords are matched without case");
        assert_eq!(out["overrides"]["beeper.exe"], json!("Chat"));
        assert_eq!(out["_help"], json!("help"));
    }

    #[test]
    fn messy_input_is_cleaned_not_saved_broken() {
        let doc = json!({ "keep": ["  \\a.exe ", "\\A.EXE", "", 5], "order": ["Ghost", "chat", "Other", "Chat"],
            "categories": { "Chat": ["discord", "DISCORD", "  "], "  ": ["x"] },
            "overrides": { "C:\\Apps\\Zoom.exe": "chat", "x.exe": "Nope", "y.exe": 3 } });
        let out = sanitize(&doc, "").unwrap();
        assert_eq!(out["keep"], json!(["\\a.exe"]));
        assert_eq!(out["order"], json!(["Chat", "Other"]), "unknown names dropped, duplicates removed");
        assert_eq!(out["categories"]["Chat"], json!(["discord"]));
        assert_eq!(out["overrides"], json!({ "zoom.exe": "Chat" }), "path reduced to the file name, unknown category dropped");
    }

    #[test]
    fn categories_left_out_of_the_order_are_kept_and_other_always_exists() {
        let doc = json!({ "order": [], "categories": { "Games": [], "Tools": ["code.exe"] } });
        let out = sanitize(&doc, "").unwrap();
        assert_eq!(out["order"], json!(["Games", "Tools", "Other"]));
        let out = sanitize(&json!({ "order": ["Other", "Games"], "categories": { "Games": [] } }), "").unwrap();
        assert_eq!(out["order"], json!(["Other", "Games"]), "Other may be placed anywhere");
        assert!(out["categories"].get("Other").is_none(), "Other has no keywords");
    }

    #[test]
    fn duplicate_or_reserved_names_are_refused() {
        assert!(sanitize(&json!({ "categories": { "Chat": [], "chat": [] } }), "").is_err());
        assert!(sanitize(&json!({ "categories": { "__more": [] } }), "").is_err());
        assert!(sanitize(&json!([1, 2]), "").is_err());
        let many: Map<String, Value> = (0..=MAX_CATEGORIES).map(|i| (format!("C{i}"), json!([]))).collect();
        assert!(sanitize(&json!({ "categories": many }), "").is_err());
    }

    #[test]
    fn long_lists_and_text_are_capped() {
        let words: Vec<String> = (0..500).map(|i| format!("w{i}")).collect();
        let out = sanitize(&json!({ "categories": { "Tools": words } }), "").unwrap();
        assert_eq!(out["categories"]["Tools"].as_array().unwrap().len(), MAX_KEYWORDS);
        let long = "x".repeat(500);
        let out = sanitize(&json!({ "keep": [long] }), "").unwrap();
        assert_eq!(out["keep"][0].as_str().unwrap().len(), MAX_TEXT);
    }

    #[test]
    fn the_bundled_defaults_pass_unchanged_in_meaning() {
        let d: Value = serde_json::from_str(include_str!("../../config/organizer.json")).unwrap();
        let out = sanitize(&d, d["_help"].as_str().unwrap()).unwrap();
        assert_eq!(out["keep"], d["keep"]);
        assert_eq!(out["order"], d["order"]);
        for (k, v) in d["categories"].as_object().unwrap() { assert_eq!(&out["categories"][k], v, "{k}"); }
    }

    #[test]
    fn keep_entries_use_the_file_name() {
        assert_eq!(keep_entry("C:\\Program Files\\Beeper\\Beeper.exe"), "\\Beeper.exe");
        assert_eq!(keep_entry("Zoom.exe"), "\\Zoom.exe");
    }
}
