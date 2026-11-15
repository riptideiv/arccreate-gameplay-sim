use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;
use std::sync::{Arc, OnceLock};
pub type XY = [f64; 2];
pub type Cost = [f64; 40];

#[derive(Deserialize)]
pub struct Input {
    pub options: Value,
    pub multi: Value,
    pub offset: i64,
    pub items: Vec<Item>,
    pub candidates: Vec<Candidate>,
    pub groups: Vec<Vec<usize>>,
    #[serde(default)]
    pub trace_order: Vec<usize>,
    pub screen: Vec<XY>,
    pub geometry: crate::geometry::Geometry,
    #[serde(default)]
    pub wrist: Value,
}
#[derive(Deserialize)]
pub struct Item {
    pub kind: String,
    pub oid: String,
    pub parent: Option<String>,
    pub colour: i32,
    pub start: i64,
    pub end: i64,
    pub first_tick: i64,
    pub last_tick: i64,
    pub score_weight: i64,
    pub red: bool,
    pub forced: bool,
    pub continuous: bool,
    pub native: bool,
    pub source_end: i64,
    pub latest_down: Option<i64>,
    #[serde(default)]
    pub earliest_down: i64,
    pub points_start: i32,
    pub points: Vec<XY>,
    pub trace_points: Vec<XY>,
    pub polygons: Vec<Vec<XY>>,
    pub region_centres: Vec<XY>,
    pub centre: XY,
    #[serde(default = "full_note_center_scale")]
    pub note_center_scale: f64,
    pub trace_start: i32,
    pub trace: Vec<XY>,
    #[serde(default)]
    pub trace_hash: i64,
    #[serde(default)]
    pub trace_member: bool,
    pub guides_start: i32,
    pub guides: Vec<Vec<XY>>,
    pub contested: bool,
}
fn full_note_center_scale() -> f64 {
    1.
}
impl Item {
    pub fn point(&self, f: i32) -> XY {
        self.points[(f - self.points_start).clamp(0, self.points.len() as i32 - 1) as usize]
    }
    pub fn trace_point(&self, f: i32) -> XY {
        self.trace_points
            [(f - self.points_start).clamp(0, self.trace_points.len() as i32 - 1) as usize]
    }
    pub fn polygon(&self, f: i32) -> &[XY] {
        &self.polygons[(f - self.points_start).clamp(0, self.polygons.len() as i32 - 1) as usize]
    }
    pub fn region(&self, f: i32) -> XY {
        self.region_centres
            [(f - self.points_start).clamp(0, self.region_centres.len() as i32 - 1) as usize]
    }
}
#[derive(Clone, Deserialize)]
pub struct Candidate {
    pub id: i64,
    pub item: usize,
    pub down: i32,
    pub up: i32,
    pub path: Vec<XY>,
    pub first: XY,
    pub held_to_end: bool,
    pub held_release: bool,
    pub untouched: f64,
    pub part_base: [[f64; 9]; 2],
    pub nominal: [i64; 2],
    pub fixed: Option<usize>,
    #[serde(default)]
    pub share_only: bool,
}
#[derive(Clone, Serialize, Deserialize)]
pub struct Part {
    pub candidate: usize,
    pub start: i32,
    pub stop: i32,
}
#[derive(Clone, Serialize)]
pub struct Arrival {
    pub up: i32,
    pub path: Vec<XY>,
}
#[derive(Clone, Serialize)]
pub struct Touch {
    #[serde(skip)]
    pub uid: usize,
    pub parts: Vec<Part>,
    pub bridges: Vec<[i32; 2]>,
    pub down: i32,
    pub path: Vec<XY>,
    pub over: f64,
    pub arrival: Option<Arrival>,
    #[serde(skip)]
    pub bindings: BTreeMap<i32, std::collections::BTreeSet<i32>>,
    #[serde(skip)]
    pub measures: OnceLock<[f64; 5]>, // trace distance, deviation, held energy, centre, arrival energy
    #[serde(skip)]
    pub trace_jobs: Vec<usize>,
    #[serde(skip)]
    pub prefix: OnceLock<Arc<Touch>>,
    #[serde(skip)]
    pub arrival_base: Option<Arc<Touch>>,
    #[serde(skip)]
    pub arrival_initialized: OnceLock<()>,
    #[serde(skip)]
    pub inherited_measures: OnceLock<[Option<f64>; 2]>,
}
impl Touch {
    pub fn up(&self) -> i32 {
        self.down + self.path.len() as i32
    }
    pub fn first(&self) -> XY {
        self.path[0]
    }
    pub fn last(&self) -> XY {
        *self.path.last().unwrap()
    }
    pub fn xy(&self, f: i32) -> XY {
        self.path[(f - self.down) as usize]
    }
    pub fn active(&self, f: i32) -> bool {
        self.down <= f && f < self.up()
    }
}
#[derive(Clone)]
pub struct State {
    pub hands: [Option<Arc<Touch>>; 2],
    pub runs: [i32; 2],
    pub owned: [Option<Arc<Touch>>; 2],
    pub locks: [i64; 4],
    pub cost: Cost,
    pub node: Option<Arc<Node>>,
    pub arms: Option<Arc<crate::arms::ArmState>>,
}
#[derive(Clone)]
pub struct Node {
    pub previous: Option<Arc<Node>>,
    pub item: usize,
    pub hand: usize,
    pub touch: Option<Arc<Touch>>,
    pub extension: bool,
    pub increment: Cost,
}
pub fn n(v: &Value, k: &str) -> f64 {
    v[k].as_f64().unwrap_or(0.)
}
pub fn b(v: &Value, k: &str) -> bool {
    v[k].as_bool().unwrap_or(false)
}
pub fn dist(a: XY, b: XY) -> f64 {
    crate::numeric::dist(a, b)
}
pub fn bits(value: f64) -> u64 {
    if value == 0. {
        0
    } else {
        value.to_bits()
    }
}
pub fn frame_ms(f: i32, offset: i64) -> i64 {
    ((f as f64 * 1000. / 60.).round_ties_even() as i64) - offset
}
pub fn frame_after(t: i64, offset: i64) -> i32 {
    let mut f = (((t + offset) as f64 * 60. / 1000.).ceil()) as i32;
    while frame_ms(f - 1, offset) >= t {
        f -= 1;
    }
    f
}
pub fn min_jerk(s: f64) -> f64 {
    s * s * s * (10. - 15. * s + 6. * s * s)
}
