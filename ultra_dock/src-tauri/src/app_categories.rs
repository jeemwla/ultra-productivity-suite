//! Pure install-root evidence; callers retain category and manual-override policy.

/// Lexical evidence only: requires a platform root, title directory and file.
pub fn game_install_path(exe: &str) -> bool {
    const ROOTS: &[&[&str]] = &[
        &["steamapps", "common"],
        &["epic games"], &["epicgames"], &["gog games"],
        &["gog galaxy", "games"], &["xboxgames"],
        &["ubisoft game launcher", "games"],
        &["origin games"], &["ea games"],
    ];
    let normalized = exe.replace('\\', "/").to_ascii_lowercase();
    if normalized.ends_with('/') {
        return false;
    }
    let segments: Vec<_> = normalized.split('/').filter(|s| !s.is_empty()).collect();
    // Without filesystem resolution, do not infer where traversal paths land.
    if segments.iter().any(|s| matches!(*s, "." | "..")) {
        return false;
    }
    let Some((_, directories)) = segments.split_last() else { return false };
    ROOTS.iter().any(|root| {
        directories.windows(root.len() + 1).any(|window| window.starts_with(root))
    })
}

#[cfg(test)]
mod tests {
    use super::game_install_path;

    #[test]
    fn recognizes_unseen_titles_under_platform_roots() {
        for path in [
            r"D:\SteamLibrary\steamapps\common\Nebula Orchard\orchard.exe",
            r"D:\Epic Games\Paper Comet\comet.exe",
            r"D:\EpicGames\Velvet Meteor\bin\meteor.exe",
            r"D:\GOG Games\Copper Moon\moon.exe",
            r"C:\Program Files (x86)\GOG Galaxy\Games\Moss Engine\moss.exe",
            r"D:\XboxGames\Glass Planet\Content\planet.exe",
            r"C:\Ubisoft Game Launcher\games\Cloud Harbor\harbor.exe",
            r"D:\Origin Games\Amber Tide\tide.exe",
            r"D:\EA Games\Silent Prism\prism.exe",
        ] {
            assert!(game_install_path(path), "missed {path}");
        }
    }

    #[test]
    fn accepts_case_slash_variants_and_unicode_title_names() {
        for path in [
            "d:/STEAMAPPS/CoMmOn/星の庭/遊び.exe",
            r"D:\ePiC gAmEs/雪の町\bin/firefox.exe",
            "//server/share/gOg GaLaXy/GaMeS/Café Orbit/orbit.exe",
        ] {
            assert!(game_install_path(path), "missed {path}");
        }
    }

    #[test]
    fn platform_evidence_survives_chat_and_browser_basename_keywords() {
        for name in ["discord.exe", "chat.exe", "chrome.exe", "browser.exe"] {
            assert!(game_install_path(&format!("D:/steamapps/common/Unseen/{name}")));
        }
    }

    #[test]
    fn rejects_partial_segments_generic_games_and_filename_matches() {
        for path in [
            r"D:\steamapps-fake\common\Nebula\game.exe",
            r"D:\steamapps\common-backup\Nebula\game.exe",
            r"D:\GOG Games Backup\Nebula\game.exe",
            r"D:\GOG Galaxy\Games Backup\Nebula\game.exe",
            r"D:\Epic Games Backup\Nebula\game.exe",
            r"D:\NotEpicGames\Nebula\game.exe",
            r"D:\XboxGames-old\Nebula\game.exe",
            r"D:\Ubisoft Game Launcher\games-old\Nebula\game.exe",
            r"D:\Origin Games Backup\Nebula\game.exe",
            r"D:\EA Games Backup\Nebula\game.exe",
            r"D:\Games\Nebula\game.exe",
            r"D:\Downloads\steamapps-common.exe",
            r"D:\Downloads\GOG Games.exe",
            r"D:\Downloads\EpicGames.exe",
            r"D:\Downloads\XboxGames",
            "D:/资料/普通游戏/浏览器.exe",
            "D:/资料/steamapps-fake/common/星/chat.exe",
            "D:/資料/ＥＡ Games/星/game.exe",
            "",
            "game.exe",
        ] {
            assert!(!game_install_path(path), "false positive {path}");
        }
    }

    #[test]
    fn requires_a_title_directory_and_file_without_parent_traversal() {
        for path in [
            "D:/steamapps/common/game.exe",
            "D:/Epic Games/game.exe",
            "D:/XboxGames/Nebula/",
            "D:/steamapps/common/Nebula/../../chat.exe",
        ] {
            assert!(!game_install_path(path), "false positive {path}");
        }
    }
}
