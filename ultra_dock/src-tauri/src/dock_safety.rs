//! A native button may disappear only when its replacement is usable.
#[derive(Clone, Copy, Debug)]
pub struct Visibility {
    pub enabled: bool,
    pub guard_ready: bool,
    pub main_responsive: bool,
    pub strip_visible: bool,
    pub strip_fits: bool,
    pub all_taskbars_covered: bool,
    pub rendered_age: u64,
}
impl Visibility {
    pub fn may_hide(self) -> bool {
        self.enabled && self.guard_ready && self.main_responsive && self.strip_visible
            && self.strip_fits && self.all_taskbars_covered && self.rendered_age < 5
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn exhaustively_checks_visibility_and_render_age_combinations() {
        let mut cases = 0;
        for bits in 0..64 {
            for age in 0..=30 {
                let state = Visibility { enabled: bits & 1 != 0, guard_ready: bits & 2 != 0,
                    main_responsive: bits & 4 != 0, strip_visible: bits & 8 != 0,
                    strip_fits: bits & 16 != 0, all_taskbars_covered: bits & 32 != 0, rendered_age: age };
                assert_eq!(state.may_hide(), bits == 63 && age < 5, "{state:?}");
                cases += 1;
            }
        }
        assert_eq!(cases, 1984);
    }
}
