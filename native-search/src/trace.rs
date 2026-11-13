//! Preserve the reference interpreter's set iteration order for trace costs.
//!
//! CPython 3.13 set insertion/intersection layout, adapted from
//! Objects/setobject.c and Objects/dictobject.c (PSF license, LICENSE-PSF).
//! Hashes are copied during Python preparation; workers never call Python.

struct OrderedSet {
    entries: Vec<Option<(usize, u64)>>,
    used: usize,
}
impl OrderedSet {
    fn new() -> Self {
        Self {
            entries: vec![None; 8],
            used: 0,
        }
    }
    fn insert_slot(&mut self, key: usize, hash: u64) -> bool {
        let mask = self.entries.len() - 1;
        let mut index = hash as usize & mask;
        let mut perturb = hash;
        loop {
            let probes = if index + 9 <= mask { 9 } else { 0 };
            for slot in index..=index + probes {
                match self.entries[slot] {
                    None => {
                        self.entries[slot] = Some((key, hash));
                        return true;
                    }
                    Some((k, _)) if k == key => return false,
                    _ => {}
                }
            }
            perturb >>= 5;
            index = (index
                .wrapping_mul(5)
                .wrapping_add(1)
                .wrapping_add(perturb as usize))
                & mask;
        }
    }
    fn add(&mut self, key: usize, hash: u64) {
        if !self.insert_slot(key, hash) {
            return;
        }
        self.used += 1;
        if self.used * 5 < (self.entries.len() - 1) * 3 {
            return;
        }
        let minimum = self.used * if self.used > 50000 { 2 } else { 4 };
        let mut size = 8;
        while size <= minimum {
            size *= 2;
        }
        let old = std::mem::replace(&mut self.entries, vec![None; size]);
        for (key, hash) in old.into_iter().flatten() {
            self.insert_slot(key, hash);
        }
    }
    fn contains(&self, key: usize) -> bool {
        self.entries.iter().flatten().any(|(k, _)| *k == key)
    }
    fn iter(&self) -> impl Iterator<Item = (usize, u64)> + '_ {
        self.entries.iter().filter_map(|e| *e)
    }
}

/// `{part.occupation_id for part in parts} & trace_cells.keys()` in CPython.
pub fn jobs(
    parts: impl IntoIterator<Item = usize>,
    hash: impl Fn(usize) -> i64,
    member: impl Fn(usize) -> bool,
    trace_order: &[usize],
) -> Vec<usize> {
    let mut all = OrderedSet::new();
    for key in parts {
        all.add(key, hash(key) as u64);
    }
    let mut result = OrderedSet::new();
    if trace_order.len() <= all.used {
        for &key in trace_order {
            if all.contains(key) {
                result.add(key, hash(key) as u64);
            }
        }
    } else {
        for (key, hashed) in all.iter() {
            if member(key) {
                result.add(key, hashed);
            }
        }
    }
    result.iter().map(|(key, _)| key).collect()
}

/// Iteration order of the integer-frame set used by NoteCenterPreference.
pub fn frames(values: impl IntoIterator<Item = i32>) -> Vec<i32> {
    let mut result = OrderedSet::new();
    for frame in values {
        // These are i32 frame indices; their CPython integer hash is the
        // value itself, with the reserved -1 hash represented as -2.
        let hash = if frame == -1 { -2 } else { frame };
        result.add(frame as u32 as usize, hash as i64 as u64);
    }
    result
        .iter()
        .map(|(frame, _)| frame as u32 as i32)
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn set_intersection_matches_reference() {
        let value: serde_json::Value =
            serde_json::from_str(include_str!("../tests/trace_set_reference.json")).unwrap();
        for case in value.as_array().unwrap() {
            let hashes: Vec<i64> = serde_json::from_value(case["hashes"].clone()).unwrap();
            let parts: Vec<usize> = serde_json::from_value(case["parts"].clone()).unwrap();
            let cells: Vec<usize> = serde_json::from_value(case["cells"].clone()).unwrap();
            let expected: Vec<usize> = serde_json::from_value(case["expected"].clone()).unwrap();
            assert_eq!(
                jobs(parts, |k| hashes[k], |k| cells.contains(&k), &cells),
                expected
            );
        }
    }

    #[test]
    fn integer_frame_order_matches_reference() {
        let value: serde_json::Value =
            serde_json::from_str(include_str!("../tests/trace_frames_reference.json")).unwrap();
        for case in value.as_array().unwrap() {
            let input: Vec<i32> = serde_json::from_value(case["input"].clone()).unwrap();
            let expected: Vec<i32> = serde_json::from_value(case["expected"].clone()).unwrap();
            assert_eq!(frames(input), expected);
        }
    }
}
