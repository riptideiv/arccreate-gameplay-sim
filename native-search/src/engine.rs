use crate::model::*;
#[cfg(test)]
use rayon::prelude::*;
use std::cmp::Reverse;
use std::collections::{HashMap, HashSet};
use std::sync::{
    atomic::{AtomicBool, AtomicUsize, Ordering},
    Arc, Mutex,
};
const PEAK: f64 = 5.773502691896258;
type TouchTable = HashMap<Vec<u64>, Arc<Touch>>;

#[cfg(test)]
#[path = "pricing_tests.rs"]
mod pricing_tests;

// Indexed Rayon collection preserves each state's candidate/hand/branch order.
// Workers construct canonical geometry; only the ordered reducer can establish
// mutable prefix provenance or initialize a cost cache. This includes events
// emitted by branches which later fail a hard constraint.
enum ExpansionEvent {
    Arrival(Arc<Touch>),
    Price(Arc<Touch>, Option<Arc<Touch>>),
    Prefix(Arc<Touch>, Arc<Touch>),
    Transition(State, Cost, Option<Arc<Touch>>),
    Routed(State, Cost, Option<Arc<Touch>>, Arc<crate::arms::ArmState>),
}

pub struct Engine {
    pub input: Input,
    pub states: Vec<State>,
    pub layer: usize,
    pub visited: usize,
    pub pruned: usize,
    pub cancellation: Arc<AtomicBool>,
    pool: rayon::ThreadPool,
    crossing: Option<crate::arms::CrossingGraph>,
    completed_prefix: Option<(State, usize)>,
    final_alternatives: Vec<State>,
    singles: Vec<Arc<Touch>>,
    extensions: Mutex<HashMap<(usize, usize), Vec<Arc<Touch>>>>,
    shares: Mutex<HashMap<(usize, usize), Option<Arc<Touch>>>>,
    pairs: Mutex<HashMap<(usize, usize), [f64; 7]>>,
    next_id: AtomicUsize,
    // Worker-assigned IDs identify equality classes only, never ordering.
    // Geometry is immutable; cost/prefix OnceLocks are written by apply_event.
    interned: Vec<Mutex<TouchTable>>,
    wrist: Option<crate::wrist::WristEstimate>,
    earliest: Vec<i32>,
    lock_expiry: HashMap<(i32, i32), i64>,
    point_cache: crate::point_cache::PointCache,
}

fn zero() -> Cost {
    [0.; 40]
}
fn plus(a: &Cost, b: &Cost) -> Cost {
    let mut out = zero();
    for i in 0..40 {
        out[i] = a[i] + b[i];
    }
    out
}
fn clip(poly: &[XY], a: f64, b: f64, c: f64) -> Vec<XY> {
    let mut out = vec![];
    for i in 0..poly.len() {
        let p = poly[i];
        let q = poly[(i + 1) % poly.len()];
        let fp = a * p[0] + b * p[1] + c;
        let fq = a * q[0] + b * q[1] + c;
        if fp >= 0. {
            out.push(p);
        }
        if (fp >= 0.) != (fq >= 0.) {
            let t = fp / (fp - fq);
            out.push([p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])]);
        }
    }
    out
}
fn nearest(poly: &[XY], point: XY) -> XY {
    let area = crate::numeric::py_sum((0..poly.len()).map(|i| {
        let p = poly[i];
        let q = poly[(i + 1) % poly.len()];
        p[0] * q[1] - q[0] * p[1]
    }));
    let inside = (0..poly.len()).all(|i| {
        let p = poly[i];
        let q = poly[(i + 1) % poly.len()];
        let cross = (q[0] - p[0]) * (point[1] - p[1]) - (q[1] - p[1]) * (point[0] - p[0]);
        cross * area >= 0.
    });
    if inside {
        return point;
    }
    let mut best = poly[0];
    let mut best_d = f64::INFINITY;
    for i in 0..poly.len() {
        let p = poly[i];
        let q = poly[(i + 1) % poly.len()];
        let dx = q[0] - p[0];
        let dy = q[1] - p[1];
        let length2 = dx * dx + dy * dy;
        let t = if length2 == 0. {
            0.
        } else {
            ((point[0] - p[0]) * dx + (point[1] - p[1]) * dy) / length2
        }
        .clamp(0., 1.);
        let c = [p[0] + t * dx, p[1] + t * dy];
        let d = dist(c, point);
        if d < best_d {
            best = c;
            best_d = d;
        }
    }
    best
}
fn segment_distance(p: XY, a: XY, b: XY) -> f64 {
    let d = [b[0] - a[0], b[1] - a[1]];
    let l = d[0] * d[0] + d[1] * d[1];
    let u = if l == 0. {
        0.
    } else {
        ((p[0] - a[0]) * d[0] + (p[1] - a[1]) * d[1]) / l
    }
    .clamp(0., 1.);
    dist(p, [a[0] + u * d[0], a[1] + u * d[1]])
}

#[cfg(test)]
mod primitive_tests {
    use super::*;

    #[test]
    fn clipping_keeps_python_vertex_order() {
        let square = [[-1., -1.], [1., -1.], [1., 1.], [-1., 1.]];
        assert_eq!(
            clip(&square, 0., -1., 0.),
            vec![[-1., -1.], [1., -1.], [1., 0.], [-1., 0.]]
        );
    }

    #[test]
    fn nearest_has_no_extra_hitbox_tolerance() {
        let square = [[0., 0.], [1., 0.], [1., 1.], [0., 1.]];
        assert_eq!(nearest(&square, [-1e-13, 0.5]), [0., 0.5]);
        assert_eq!(nearest(&square, [0., 0.5]), [0., 0.5]);
    }

    #[test]
    fn signed_zero_has_one_state_key() {
        assert_eq!(bits(-0.0), bits(0.0));
        assert_ne!(bits(-1e-300), bits(0.0));
    }
}

impl Engine {
    pub fn new(input: Input, workers: usize) -> Result<Self, String> {
        let pool = rayon::ThreadPoolBuilder::new()
            .num_threads(workers)
            .build()
            .map_err(|e| e.to_string())?;
        let wrist = if n(&input.options, "wrist_twist_weight") != 0. {
            Some(crate::wrist::WristEstimate::new(
                serde_json::from_value(input.wrist.clone()).map_err(|e| e.to_string())?,
            )?)
        } else {
            None
        };
        let mut earliest: Vec<i32> = input
            .groups
            .iter()
            .map(|g| {
                g.iter()
                    .map(|i| input.candidates[*i].down)
                    .min()
                    .unwrap_or(i32::MAX)
            })
            .collect();
        if b(&input.multi, "tick_contacts") {
            for j in (0..earliest.len().saturating_sub(1)).rev() {
                earliest[j] = earliest[j].min(earliest[j + 1]);
            }
        }
        let mut lock_expiry = HashMap::new();
        if b(&input.multi, "tick_contacts") {
            for up in input
                .candidates
                .iter()
                .map(|c| c.up)
                .collect::<HashSet<_>>()
            {
                for colour in 0..4 {
                    lock_expiry.insert((colour, up), input.geometry.expiry(colour, up));
                }
            }
        }
        let cancellation = Arc::new(AtomicBool::new(false));
        let crossing = input.multi["arm_crossing_travel"].as_array().map(|v| {
            crate::arms::CrossingGraph::new(
                v[0].as_f64().unwrap(),
                n(&input.options, "speed_max_m_s"),
                v[1].as_f64().unwrap(),
                v[2].as_u64().unwrap() as usize,
            )
        });
        let initial_arms = crossing.as_ref().map(|g| Arc::new(g.initial()));
        let interned = (0..input.items.len())
            .map(|_| Mutex::new(HashMap::new()))
            .collect();
        let mut e = Self {
            input,
            states: vec![State {
                hands: [None, None],
                runs: [0, 0],
                owned: [None, None],
                locks: [-1; 4],
                cost: zero(),
                node: None,
                arms: initial_arms,
            }],
            layer: 0,
            visited: 1,
            pruned: 0,
            cancellation: cancellation.clone(),
            pool,
            crossing,
            completed_prefix: None,
            final_alternatives: vec![],
            singles: vec![],
            extensions: Mutex::new(HashMap::new()),
            shares: Mutex::new(HashMap::new()),
            pairs: Mutex::new(HashMap::new()),
            next_id: AtomicUsize::new(0),
            interned,
            wrist,
            earliest,
            lock_expiry,
            point_cache: crate::point_cache::PointCache::new(cancellation),
        };
        e.singles = (0..e.input.candidates.len())
            .map(|ci| {
                let c = &e.input.candidates[ci];
                e.touch(
                    vec![Part {
                        candidate: ci,
                        start: c.down,
                        stop: c.up,
                    }],
                    vec![],
                    c.down,
                    c.path.clone(),
                    0.,
                    None,
                    None,
                )
            })
            .collect();
        if e.input.groups.is_empty() && e.crossing.is_some() {
            e.final_alternatives = e.finished(&e.states[0]);
        }
        Ok(e)
    }
    pub fn restore(&mut self, states: &[State], layer: usize, visited: usize, pruned: usize) {
        fn touch(
            engine: &Engine,
            old: &Arc<Touch>,
            imported: &mut HashMap<usize, Arc<Touch>>,
        ) -> Arc<Touch> {
            if let Some(t) = imported.get(&old.uid) {
                return t.clone();
            }
            let new = engine.touch(
                old.parts.clone(),
                old.bridges.clone(),
                old.down,
                old.path.clone(),
                old.over,
                None,
                None,
            );
            imported.insert(old.uid, new.clone());
            if let Some(prefix) = old.prefix.get() {
                let p = touch(engine, prefix, imported);
                let _ = new.prefix.set(p);
            }
            new
        }
        let mut imported = HashMap::new();
        self.states = states
            .iter()
            .map(|s| {
                let mut s = s.clone();
                s.hands = s.hands.map(|t| t.map(|t| touch(self, &t, &mut imported)));
                s.owned = s.owned.map(|t| t.map(|t| touch(self, &t, &mut imported)));
                s
            })
            .collect();
        for cache in &mut self.interned[..layer] {
            *cache.get_mut().unwrap() = HashMap::new();
        }
        self.layer = layer;
        self.visited = visited;
        self.pruned = pruned;
    }
    fn candidate_point(&self, c: &Candidate, frame: i32) -> XY {
        if frame >= c.down && frame < c.up {
            c.path[(frame - c.down) as usize]
        } else {
            self.point(c.item, frame)
        }
    }
    fn point(&self, item: usize, frame: i32) -> XY {
        let item = &self.input.items[item];
        let raw = item.point(frame);
        let parent = item
            .parent
            .as_deref()
            .filter(|value| !value.is_empty())
            .unwrap_or(&item.oid);
        self.point_cache.get(parent, frame, raw)
    }
    fn opt(&self, k: &str) -> f64 {
        n(&self.input.options, k)
    }
    fn multi(&self, k: &str) -> f64 {
        n(&self.input.multi, k)
    }
    fn ms(&self, f: i32) -> i64 {
        frame_ms(f, self.input.offset)
    }
    fn item<'a>(&'a self, p: &Part) -> &'a Item {
        &self.input.items[self.input.candidates[p.candidate].item]
    }
    fn latest<'a>(&'a self, t: &Touch) -> &'a Candidate {
        &self.input.candidates[t
            .parts
            .iter()
            .min_by_key(|p| Reverse(self.input.candidates[p.candidate].item))
            .unwrap()
            .candidate]
    }
    fn end<'a>(&'a self, t: &'a Touch) -> &'a Part {
        t.parts
            .iter()
            .min_by_key(|p| Reverse((p.stop, self.input.candidates[p.candidate].item)))
            .unwrap()
    }
    fn nominal(&self, t: &Touch) -> [i64; 2] {
        if t.parts.len() == 1 && t.bridges.is_empty() {
            self.input.candidates[t.parts[0].candidate].nominal
        } else {
            [self.ms(t.down), self.ms(t.up())]
        }
    }
    fn touch(
        &self,
        parts: Vec<Part>,
        bridges: Vec<[i32; 2]>,
        down: i32,
        path: Vec<XY>,
        over: f64,
        arrival: Option<Arrival>,
        _prefix: Option<&Touch>,
    ) -> Arc<Touch> {
        let layer = parts
            .iter()
            .map(|p| self.input.candidates[p.candidate].item)
            .max()
            .unwrap();
        let interned = &self.interned[layer];
        let mut key = vec![parts.len() as u64];
        for p in &parts {
            key.extend([p.candidate as u64, p.start as u64, p.stop as u64]);
        }
        key.push(bridges.len() as u64);
        for b in &bridges {
            key.extend([b[0] as u64, b[1] as u64]);
        }
        key.extend([down as u64, path.len() as u64, bits(over)]);
        for p in &path {
            key.extend([bits(p[0]), bits(p[1])]);
        }
        if let Some(a) = &arrival {
            key.extend([1, a.up as u64, a.path.len() as u64]);
            for p in &a.path {
                key.extend([bits(p[0]), bits(p[1])]);
            }
        } else {
            key.push(0);
        }
        if let Some(t) = interned.lock().unwrap().get(&key) {
            return t.clone();
        }
        let arrival_base = arrival.as_ref().map(|_| {
            self.touch(
                parts.clone(),
                bridges.clone(),
                down,
                path.clone(),
                over,
                None,
                None,
            )
        });
        let bindings = self.input.geometry.bindings(&path, down);
        let mut t = Touch {
            uid: self.next_id.fetch_add(1, Ordering::Relaxed),
            parts,
            bridges,
            down,
            path,
            over,
            arrival,
            bindings,
            measures: std::sync::OnceLock::new(),
            trace_jobs: vec![],
            prefix: std::sync::OnceLock::new(),
            arrival_base,
            arrival_initialized: std::sync::OnceLock::new(),
            inherited_measures: std::sync::OnceLock::new(),
        };
        t.trace_jobs = crate::trace::jobs(
            t.parts
                .iter()
                .map(|p| self.input.candidates[p.candidate].item),
            |job| self.input.items[job].trace_hash,
            |job| self.input.items[job].trace_member,
            &self.input.trace_order,
        );
        let result = Arc::new(t);
        interned
            .lock()
            .unwrap()
            .entry(key)
            .or_insert(result)
            .clone()
    }
    fn measures<'a>(&self, t: &'a Touch) -> &'a [f64; 5] {
        t.measures.get_or_init(|| {
            let mut values = [0.; 5];
            let prefix = t.prefix.get().map(Arc::as_ref);
            if let Some(old) = prefix {
                values[1] = self.measures(old)[1];
            }
            for &job in &t.trace_jobs {
                let o = &self.input.items[job];
                let old_job = prefix.is_some_and(|old| old.trace_jobs.contains(&job));
                if !old_job {
                    values[1] += o.trace.len() as f64 / 60.;
                }
                let begin = if old_job {
                    prefix.unwrap().up()
                } else {
                    t.down
                };
                for f in begin.max(o.trace_start)..t.up().min(o.trace_start + o.trace.len() as i32)
                {
                    values[1] -=
                        (1. - dist(t.xy(f), o.trace[(f - o.trace_start) as usize]) / 0.020).max(0.)
                            / 60.;
                }
            }
            let jobs: Vec<usize> = t
                .parts
                .iter()
                .map(|p| self.input.candidates[p.candidate].item)
                .collect();
            let mut seen = HashSet::new();
            for job in jobs {
                if !seen.insert(job) {
                    continue;
                }
                let o = &self.input.items[job];
                if self.opt("note_center_weight") != 0. && o.kind != "chain" {
                    let penalty = |p: XY| {
                        crate::numeric::pow(
                            (dist(p, o.centre) - self.opt("note_center_free_mm") / 1000.).max(0.)
                                / 0.020,
                            2.,
                        ) * o.note_center_scale
                    };
                    if o.kind == "hold" {
                        let mut frames = vec![];
                        for p in &t.parts {
                            if self.input.candidates[p.candidate].item == job {
                                frames.extend(p.start.max(t.down)..p.stop.min(t.up()));
                            }
                        }
                        values[3] += crate::numeric::py_sum(
                            crate::trace::frames(frames)
                                .into_iter()
                                .map(|f| penalty(t.xy(f))),
                        ) / 60.;
                    } else {
                        values[3] += penalty(t.first());
                    }
                }
            }
            if !t.trace_jobs.is_empty() {
                let (initial, start) =
                    if let Some(old) = prefix.filter(|old| !old.trace_jobs.is_empty()) {
                        (self.measures(old)[0], old.path.len() - 1)
                    } else {
                        (0., 0)
                    };
                values[0] = initial
                    + crate::numeric::py_sum(t.path[start..].windows(2).map(|w| dist(w[0], w[1])));
            }
            values[1] = values[1].max(0.);
            if self.opt("acceleration_weight") != 0. {
                values[2] = crate::trajectory::held(
                    &t.path,
                    prefix.map(|old| (old.path.as_slice(), self.measures(old)[2])),
                );
                if let Some(arrival) = &t.arrival {
                    values[4] = crate::trajectory::transfer(
                        arrival,
                        t.down,
                        &t.path,
                        self.opt("speed_max_m_s"),
                    );
                }
            }
            if let Some(inherited) = t.inherited_measures.get() {
                if let Some(distance) = inherited[0] {
                    values[0] = distance;
                }
                if let Some(held) = inherited[1] {
                    values[2] = held;
                }
            }
            values
        })
    }
    fn weighted(&self, c: &Cost) -> f64 {
        let base_names = [
            "colour_weight",
            "crossing_weight",
            "movement_weight",
            "handover_weight",
            "apart_weight",
            "repeat_weight",
            "distance_weight",
            "partial_hold_weight",
            "partial_ground_hold_weight",
        ];
        let mut base = c[0] * self.opt(base_names[0]);
        for i in 1..9 {
            base += c[i] * self.opt(base_names[i]);
        }
        let terms = [
            (9, "share_weight"),
            (10, "second_finger_weight"),
            (11, "second_finger_s_weight"),
            (12, "side_by_side_weight"),
            (13, "takeover_weight"),
            (15, "steal_weight"),
            (16, "jack_weight"),
            (29, "preference_weight"),
            (30, "accel_weight"),
            (31, "crossing_depth_weight"),
        ];
        let mut extra = 0.;
        for (i, k) in terms {
            extra += c[i] * self.multi(k);
        }
        let mut geo = 0.;
        for (i, k) in [
            (17, "apart_level_weight"),
            (18, "apart_near_weight"),
            (19, "apart_clear_weight"),
            (20, "crossing_level_weight"),
            (21, "crossing_near_weight"),
            (22, "crossing_clear_weight"),
            (23, "repeat_far_weight"),
            (24, "second_held_weight"),
            (25, "trade_weight"),
            (26, "collision_weight"),
            (27, "collision_s_weight"),
            (28, "arc_takeover_weight"),
        ] {
            geo += c[i] * self.multi(k);
        }
        extra += geo;
        extra += c[35] * self.opt("acceleration_weight")
            + c[36] * self.opt("note_center_weight")
            + c[37] * self.opt("wrist_twist_weight");
        if b(&self.input.options, "scoring_arcs") && b(&self.input.options, "prefer_arc_trace") {
            extra += c[33] * self.opt("arc_trace_weight");
        }
        if extra != 0. {
            base + extra
        } else {
            base
        }
    }
    fn less(&self, a: &Cost, b: &Cost) -> bool {
        for index in [32, 34, 38, 39] {
            if a[index] != b[index] {
                return a[index] < b[index];
            }
        }
        self.weighted(a) < self.weighted(b)
    }
    fn kind(&self, p: XY, hand: usize, q: XY) -> usize {
        if (p[1] - q[1]).abs() < self.multi("level_dy_m") {
            return 0;
        }
        let (upper, lower, h) = if p[1] > q[1] {
            (p, q, hand)
        } else {
            (q, p, 1 - hand)
        };
        let sx = if h == 0 {
            -self.multi("shoulder_x_m")
        } else {
            self.multi("shoulder_x_m")
        };
        let x_line = upper[0]
            + (sx - upper[0]) * (lower[1] - upper[1]) / (self.multi("shoulder_y_m") - upper[1]);
        if (x_line - lower[0]).abs() < self.multi("forearm_near_m") {
            1
        } else {
            2
        }
    }
    fn overlap(&self, p: XY, hand: usize, q: XY) -> f64 {
        let segment = |p: XY, h: usize| {
            let dx = (if h == 0 { -1. } else { 1. }) * self.multi("shoulder_x_m") - p[0];
            let dy = self.multi("shoulder_y_m") - p[1];
            let s = self.multi("hand_length_m") / crate::numeric::hypot(dx, dy);
            [p[0] + dx * s, p[1] + dy * s]
        };
        let a = segment(p, hand);
        let c = segment(q, 1 - hand);
        let side =
            |o: XY, a: XY, b: XY| (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
        let crossed = (side(p, a, q) > 0.) != (side(p, a, c) > 0.)
            && (side(q, c, p) > 0.) != (side(q, c, a) > 0.);
        let gap = if crossed {
            0.
        } else {
            segment_distance(p, q, c)
                .min(segment_distance(a, q, c))
                .min(segment_distance(q, p, a))
                .min(segment_distance(c, p, a))
        };
        (2. * self.multi("hand_radius_m") - gap).max(0.)
    }
    fn pair(&self, left: &Touch, right: &Touch) -> [f64; 7] {
        let key = (left.uid, right.uid);
        if let Some(v) = self.pairs.lock().unwrap().get(&key) {
            return *v;
        }
        let mut v = [0.; 7];
        for f in left.down.max(right.down)..left.up().min(right.up()) {
            let p = left.xy(f);
            let q = right.xy(f);
            if p[0] > q[0] {
                v[0] += 1.;
                v[2 + self.kind(p, 0, q)] += 1. / 60.;
            }
            v[5] += self.overlap(p, 0, q) / 60.;
        }
        v[0] /= 60.;
        v[1] = crate::numeric::py_sum((left.down.max(right.down)..left.up().min(right.up())).map(
            |f| (left.xy(f)[0] - right.xy(f)[0] - self.opt("crossing_pass_margin_m")).max(0.),
        )) / 60.;
        if let Some(w) = &self.wrist {
            v[6] = crate::numeric::py_sum(
                (left.down.max(right.down)..left.up().min(right.up()))
                    .map(|f| w.frame(left.xy(f), right.xy(f))),
            ) / 60.;
        }
        self.pairs.lock().unwrap().insert(key, v);
        v
    }
    fn shared(&self, live: &[usize], f: i32) -> Vec<XY> {
        let mut poly = self.input.screen.clone();
        let margin = self.multi("share_margin_m");
        for &ci in live {
            let r = self.input.items[self.input.candidates[ci].item].polygon(f);
            if r.is_empty() {
                return vec![];
            }
            let area =
                crate::numeric::py_sum((0..r.len()).map(|i| {
                    r[i][0] * r[(i + 1) % r.len()][1] - r[(i + 1) % r.len()][0] * r[i][1]
                }));
            let sign = if area > 0. { 1. } else { -1. };
            for j in 0..r.len() {
                let p = r[j];
                let q = r[(j + 1) % r.len()];
                let nx = -(q[1] - p[1]) * sign;
                let ny = (q[0] - p[0]) * sign;
                let length = crate::numeric::hypot(nx, ny);
                if length == 0. {
                    continue;
                }
                let nx = nx / length;
                let ny = ny / length;
                poly = clip(&poly, nx, ny, -(nx * p[0] + ny * p[1]) - margin);
                if poly.len() < 3 {
                    return vec![];
                }
            }
        }
        poly
    }
    fn anchor(&self, live: &[usize], f: i32) -> Option<XY> {
        if live.is_empty() {
            return None;
        }
        let mandatory: Vec<usize> = live
            .iter()
            .copied()
            .filter(|i| !self.input.items[self.input.candidates[*i].item].native)
            .collect();
        let mut chosen = live;
        if b(&self.input.options, "scoring_arcs") {
            if mandatory.is_empty() {
                let &ci = live
                    .iter()
                    .min_by_key(|i| Reverse(self.input.candidates[**i].down))
                    .unwrap();
                let c = &self.input.candidates[ci];
                if c.down <= f && f < c.up {
                    return Some(c.path[(f - c.down) as usize]);
                }
            } else {
                let poly = self.shared(live, f);
                if !poly.is_empty() {
                    let arc = live
                        .iter()
                        .copied()
                        .find(|i| self.input.items[self.input.candidates[*i].item].native);
                    let p = if let Some(i) = arc {
                        let c = &self.input.candidates[i];
                        c.path[(f - c.down) as usize]
                    } else {
                        self.candidate_point(&self.input.candidates[mandatory[0]], f)
                    };
                    return Some(nearest(&poly, p));
                }
                chosen = &mandatory;
            }
        }
        if chosen.len() == 1 {
            return Some(self.candidate_point(&self.input.candidates[chosen[0]], f));
        }
        let poly = self.shared(chosen, f);
        if poly.is_empty() {
            return None;
        }
        let ci = chosen
            .iter()
            .find(|i| self.input.items[self.input.candidates[**i].item].kind == "chain")
            .unwrap_or(&chosen[0]);
        Some(nearest(
            &poly,
            self.candidate_point(&self.input.candidates[*ci], f),
        ))
    }
    fn compose(
        &self,
        mut parts: Vec<Part>,
        bridges: Vec<[i32; 2]>,
        prefix: Option<&Touch>,
    ) -> Option<Arc<Touch>> {
        if self.cancellation.load(Ordering::Relaxed) {
            return None;
        }
        parts.sort_by_key(|p| (p.start, self.input.candidates[p.candidate].item));
        if parts.len() == 1 && bridges.is_empty() {
            let p = &parts[0];
            let c = &self.input.candidates[p.candidate];
            if p.start == c.down && p.stop == c.up {
                return Some(self.singles[p.candidate].clone());
            }
        }
        let down = parts.iter().map(|p| p.start).min()?;
        let up = parts.iter().map(|p| p.stop).max()?;
        let mut path = vec![];
        let mut lives = vec![];
        let mut previous: Option<XY> = None;
        let mut served = HashSet::new();
        for f in down..up {
            if self.cancellation.load(Ordering::Relaxed) {
                return None;
            }
            let live: Vec<usize> = parts
                .iter()
                .filter(|p| p.start <= f && f < p.stop)
                .map(|p| p.candidate)
                .collect();
            lives.push(live.clone());
            if live.is_empty() {
                let span = bridges.iter().find(|s| s[0] <= f && f < s[1])?;
                let after = parts
                    .iter()
                    .filter(|p| p.start >= span[1])
                    .min_by_key(|p| p.start)?;
                let a: XY = path[(span[0] - 1 - down) as usize];
                let z = self.candidate_point(&self.input.candidates[after.candidate], span[1]);
                let s = (f - span[0] + 1) as f64 / (span[1] - span[0] + 1) as f64;
                path.push([a[0] + s * (z[0] - a[0]), a[1] + s * (z[1] - a[1])]);
                previous = path.last().copied();
                served.clear();
                continue;
            }
            let p = self.anchor(&live, f)?;
            if let Some(old) = previous {
                if dist(old, p) * 60. > self.opt("speed_max_m_s") * 1.0001 {
                    return None;
                }
                let now: HashSet<_> = live.iter().copied().collect();
                if self.multi("share_jump_max_m") != 0.
                    && !served.is_empty()
                    && now != served
                    && now.union(&served).any(|i| {
                        let k = &self.input.items[self.input.candidates[*i].item].kind;
                        k == "tap" || k == "sky"
                    })
                    && dist(old, p) > self.multi("share_jump_max_m")
                {
                    return None;
                }
            }
            served = live.into_iter().collect();
            path.push(p);
            previous = Some(p);
        }
        let mut over = 0.;
        if self.multi("accel_max_m_s2") != 0. {
            for k in 1..path.len() {
                let before = &lives[k - 1];
                let now = &lives[k];
                if before.is_empty() || now.is_empty() || before == now {
                    continue;
                }
                let kept: Vec<usize> = now.iter().filter(|i| before.contains(i)).copied().collect();
                let motion = if let (Some(a), Some(z)) = (
                    self.anchor(&kept, down + k as i32 - 1),
                    self.anchor(&kept, down + k as i32),
                ) {
                    [z[0] - a[0], z[1] - a[1]]
                } else {
                    [0., 0.]
                };
                let delta = [
                    path[k][0] - path[k - 1][0] - motion[0],
                    path[k][1] - path[k - 1][1] - motion[1],
                ];
                let frames = ((PEAK * dist(delta, [0., 0.]) / self.multi("accel_max_m_s2")).sqrt()
                    * 60.
                    - 1e-9)
                    .ceil() as usize;
                if frames <= 1 {
                    continue;
                }
                let ahead: Vec<(usize, f64)> = (k.saturating_sub(frames - 1)..k)
                    .map(|j| (j, min_jerk((j + frames - k) as f64 / frames as f64)))
                    .collect();
                let after: Vec<(usize, f64)> = (k..path.len().min(k + frames - 1))
                    .map(|j| (j, min_jerk((j - k + 1) as f64 / frames as f64) - 1.))
                    .collect();
                let mut changed = false;
                for shares in [ahead, after] {
                    let mut trial = path.clone();
                    for &(j, s) in &shares {
                        trial[j] = [path[j][0] + s * delta[0], path[j][1] + s * delta[1]];
                    }
                    let valid = shares.iter().all(|&(j, _)| {
                        // A bridge's slide follows its own construction rule;
                        // shared-contact easing may only move live-note frames.
                        if lives[j].is_empty() {
                            return false;
                        }
                        let poly = self.shared(&lives[j], down + j as i32);
                        !poly.is_empty()
                            && dist(nearest(&poly, trial[j]), trial[j]) <= 1e-6
                            && [j, j + 1].iter().all(|&i| {
                                i == 0
                                    || i >= trial.len()
                                    || dist(trial[i - 1], trial[i]) * 60.
                                        <= self.opt("speed_max_m_s") * 1.0001
                            })
                    });
                    if valid {
                        path = trial;
                        changed = true;
                        break;
                    }
                }
                if !changed {
                    over += 1.;
                }
            }
        }
        if path
            .windows(2)
            .any(|w| dist(w[0], w[1]) * 60. > self.opt("speed_max_m_s") * 1.0001)
        {
            return None;
        }
        if parts.iter().any(|p| {
            self.item(p).native
                && !self
                    .input
                    .geometry
                    .covers(self.input.candidates[p.candidate].item, &path, down)
        }) {
            return None;
        }
        Some(self.touch(
            parts,
            bridges,
            down,
            path,
            over,
            prefix.and_then(|t| t.arrival.clone()),
            prefix,
        ))
    }
}

impl Engine {
    fn extension_variants(&self, t: &Arc<Touch>, ci: usize) -> Vec<Arc<Touch>> {
        if self.cancellation.load(Ordering::Relaxed) {
            return vec![];
        }
        let key = (t.uid, ci);
        if let Some(v) = self.extensions.lock().unwrap().get(&key) {
            return v.clone();
        }
        let c = &self.input.candidates[ci];
        let o = &self.input.items[c.item];
        let end = t.up().max(c.up);
        let mut paths = vec![];
        let mut stay = t.path.clone();
        stay.resize((end - t.down) as usize, t.last());
        paths.push(stay);
        if c.down >= t.up() {
            let span = c.down - t.up() + 1;
            let a = t.last();
            let z = c.first;
            let mut straight = t.path.clone();
            for j in 1..=span {
                // Preserve Python's multiply-then-divide evaluation order;
                // precomputing j/span changes path bits and future dedup keys.
                straight.push([
                    a[0] + (z[0] - a[0]) * j as f64 / span as f64,
                    a[1] + (z[1] - a[1]) * j as f64 / span as f64,
                ]);
            }
            straight.extend_from_slice(&c.path[1..]);
            paths.push(straight.clone());
            if o.trace_member {
                for amount in [0.25, 0.5, 0.75, 1.] {
                    let mut p = t.path.clone();
                    for f in t.up()..end {
                        let q = straight[(f - t.down) as usize];
                        let target = o.trace_point(f);
                        p.push([
                            q[0] + (target[0] - q[0]) * amount,
                            q[1] + (target[1] - q[1]) * amount,
                        ]);
                    }
                    paths.push(p);
                }
            }
        }
        if o.trace_member && self.opt("acceleration_weight") != 0. && end > t.up() {
            for guide in &o.guides {
                let at =
                    |f: i32| guide[(f - o.guides_start).clamp(0, guide.len() as i32 - 1) as usize];
                let prev = at(t.up() - 1);
                let delta = [t.last()[0] - prev[0], t.last()[1] - prev[1]];
                let old = if t.path.len() > 1 {
                    t.path[t.path.len() - 2]
                } else {
                    t.last()
                };
                let before = if t.path.len() > 1 {
                    at(t.up() - 2)
                } else {
                    prev
                };
                let dv = [
                    t.last()[0] - old[0] - (prev[0] - before[0]),
                    t.last()[1] - old[1] - (prev[1] - before[1]),
                ];
                let mut p = t.path.clone();
                for f in t.up()..end {
                    let q = at(f);
                    let u = ((f - t.up() + 1) as f64 / 12.).min(1.);
                    let h = 1. - 3. * u * u + 2. * u * u * u;
                    let tangent = u - 2. * u * u + u * u * u;
                    p.push([
                        q[0] + h * delta[0] + tangent * 12. * dv[0],
                        q[1] + h * delta[1] + tangent * 12. * dv[1],
                    ]);
                }
                paths.push(p);
            }
        }
        let mut variants = vec![];
        let limit = self.opt("speed_max_m_s") * 1.00001;
        for mut path in paths {
            self.input
                .geometry
                .guard_path(o.colour, &mut path, t.down, t.path.len());
            if path.len() != (end - t.down) as usize
                || path.windows(2).any(|w| dist(w[0], w[1]) * 60. > limit)
                || !self.input.geometry.covers(c.item, &path, t.down)
            {
                continue;
            }
            let mut parts = t.parts.clone();
            parts.push(Part {
                candidate: ci,
                start: c.down,
                stop: c.up,
            });
            let mut bridges = t.bridges.clone();
            if c.down > t.up() {
                bridges.push([t.up(), c.down]);
            }
            variants.push(self.touch(parts, bridges, t.down, path, 0., t.arrival.clone(), Some(t)));
        }
        self.extensions
            .lock()
            .unwrap()
            .insert(key, variants.clone());
        variants
    }
    fn may_join(&self, t: &Touch, ci: usize) -> bool {
        let c = &self.input.candidates[ci];
        let o = &self.input.items[c.item];
        if c.down >= t.up() {
            return false;
        }
        if t.parts
            .iter()
            .any(|p| matches!(self.item(p).kind.as_str(), "tap" | "sky"))
        {
            return o.kind == "chain" && t.parts.len() == 1;
        }
        if o.kind == "chain" {
            for p in &t.parts {
                let other = self.item(p);
                if other.kind != "chain" || other.colour == o.colour {
                    continue;
                }
                for f in p.start.max(c.down)..p.stop.min(c.up) {
                    if dist(o.region(f), other.region(f)) >= 200. {
                        return false;
                    }
                }
            }
        }
        if o.kind == "hold"
            && (t.down != c.down || t.parts.iter().any(|p| self.item(p).kind == "hold"))
        {
            return false;
        }
        true
    }
    fn may_share(&self, t: &Touch, ci: usize) -> bool {
        let c = &self.input.candidates[ci];
        let end = c.up.min(t.parts.iter().map(|p| p.stop).max().unwrap());
        for f in [c.down, (c.down + end - 1).div_euclid(2), end - 1] {
            let mut live: Vec<_> = t
                .parts
                .iter()
                .filter(|p| p.start <= f && f < p.stop)
                .map(|p| p.candidate)
                .collect();
            if !live.is_empty() {
                live.push(ci);
                if self.shared(&live, f).is_empty() {
                    return false;
                }
            }
        }
        true
    }
    fn share(&self, t: &Arc<Touch>, ci: usize) -> Option<Arc<Touch>> {
        let key = (t.uid, ci);
        if let Some(v) = self.shares.lock().unwrap().get(&key) {
            return v.clone();
        }
        let c = &self.input.candidates[ci];
        let mut parts = t.parts.clone();
        parts.push(Part {
            candidate: ci,
            start: c.down,
            stop: c.up,
        });
        let result = self.compose(parts, t.bridges.clone(), Some(t));
        self.shares.lock().unwrap().insert(key, result.clone());
        result
    }
    fn jump_slide(&self, t: &Touch, ci: usize) -> Option<(Vec<XY>, i32)> {
        if !b(&self.input.multi, "jump_slides") {
            return None;
        }
        let c = &self.input.candidates[ci];
        let o = &self.input.items[c.item];
        let prev = self.item(self.end(t));
        let start = frame_after(o.start, self.input.offset);
        let frames = c.down - t.up();
        let gap = o.start - prev.end;
        if gap < 0
            || start > t.up()
            || frames < 1
            || c.down <= start
            || (gap > 0 && dist(t.last(), c.first) <= self.multi("bridge_max_distance_m"))
        {
            return None;
        }
        for count in 1..=frames {
            let z = self.candidate_point(c, t.up() + count - 1);
            let distance = dist(t.last(), z);
            let secs = count as f64 / 60.;
            if 1.875 * distance / secs <= self.opt("speed_max_m_s")
                && (self.multi("accel_max_m_s2") == 0.
                    || PEAK * distance / crate::numeric::pow(secs, 2.)
                        <= self.multi("accel_max_m_s2"))
            {
                let a = t.last();
                let mut path: Vec<XY> = (1..=count)
                    .map(|j| {
                        let s = min_jerk(j as f64 / count as f64);
                        [a[0] + s * (z[0] - a[0]), a[1] + s * (z[1] - a[1])]
                    })
                    .collect();
                path.extend((t.up() + count..c.down).map(|f| self.candidate_point(c, f)));
                return Some((path, count));
            }
        }
        None
    }
    fn bridge(&self, t: &Arc<Touch>, ci: usize) -> Option<(Arc<Touch>, Cost)> {
        let c = &self.input.candidates[ci];
        let o = &self.input.items[c.item];
        let last = self.end(t);
        let prev = self.item(last);
        if prev.kind != "chain" || prev.colour != o.colour || last.stop != t.up() {
            return None;
        }
        let slide = self.jump_slide(t, ci);
        let gap = o.start - prev.end;
        let distance = dist(t.last(), c.first);
        if slide.is_none()
            && (!(0 < gap && gap <= self.multi("bridge_max_gap_ms") as i64)
                || c.down < t.up()
                || distance > self.multi("bridge_max_distance_m")
                || distance * 60. / (c.down - t.up() + 1).max(1) as f64 > self.opt("speed_max_m_s"))
        {
            return None;
        }
        let mut path = t.path.clone();
        let mut frames = (c.down - t.up() + 1) as f64;
        let mut distance = distance;
        if let Some((p, n)) = slide {
            distance = dist(t.last(), p[(n - 1) as usize]);
            frames = n as f64;
            path.extend(p);
        } else {
            let a = t.last();
            for f in t.up()..c.down {
                let s = (f - t.up() + 1) as f64 / frames;
                path.push([
                    a[0] + s * (c.first[0] - a[0]),
                    a[1] + s * (c.first[1] - a[1]),
                ]);
            }
        }
        path.extend_from_slice(&c.path);
        let mut parts = t.parts.clone();
        parts.push(Part {
            candidate: ci,
            start: c.down,
            stop: c.up,
        });
        let mut bridges = t.bridges.clone();
        bridges.push([t.up(), c.down]);
        let touch = self.touch(
            parts,
            bridges,
            t.down,
            path,
            t.over,
            t.arrival.clone(),
            Some(t),
        );
        let mut cost = zero();
        cost[2] = crate::numeric::pow(
            (distance / (frames / 60.) - self.opt("speed_comfort_m_s")).max(0.),
            2.,
        );
        cost[6] = distance;
        Some((touch, cost))
    }
    fn place(&self, mine: &Touch, theirs: &Touch) -> Option<(XY, f64)> {
        let idle = (self.nominal(mine)[0] - self.nominal(theirs)[1]) as f64 / 1000.;
        if idle >= self.opt("apart_memory_s") {
            return None;
        }
        if idle > 0. {
            Some((theirs.last(), 1. - idle / self.opt("apart_memory_s")))
        } else {
            Some((theirs.xy(mine.down.clamp(theirs.down, theirs.up() - 1)), 1.))
        }
    }
    fn window(&self, t: &Touch) -> i64 {
        if t.parts
            .iter()
            .any(|p| matches!(self.item(p).kind.as_str(), "chain" | "hold"))
        {
            self.multi("arc_takeover_ms") as i64
        } else {
            let v = self.multi("arc_takeover_renewed_ms");
            if v == 0. {
                self.multi("arc_takeover_ms") as i64
            } else {
                v as i64
            }
        }
    }
    fn movement(&self, prev: Option<&Touch>, new: &Touch) -> Option<f64> {
        let Some(t) = prev else {
            return Some(0.);
        };
        let air = new.down - t.up();
        if air < self.opt("min_air_frames") as i32 {
            return None;
        }
        let held = self.input.candidates[self.end(t).candidate].held_release;
        if air < self.opt("held_release_air_frames") as i32 && held {
            return None;
        }
        let time = (self.ms(new.down) - self.ms(t.up())) as f64 / 1000.;
        let distance = dist(t.last(), new.first());
        if time <= 0. {
            return if time == 0. && distance < 1e-9 {
                Some(0.)
            } else {
                None
            };
        }
        let speed = distance / time;
        if speed > self.opt("speed_max_m_s") {
            None
        } else {
            Some(crate::numeric::pow(
                (speed - self.opt("speed_comfort_m_s")).max(0.),
                2.,
            ))
        }
    }
    fn new_cost(&self, state: &State, hand: usize, new: &Touch) -> Option<Cost> {
        let prev = state.hands[hand].as_deref();
        let other = state.hands[1 - hand].as_deref();
        let mut v = zero();
        if let Some(t) = prev {
            if self.multi("tap_interval_ms") != 0.
                && t.parts.len() == 1
                && t.bridges.is_empty()
                && new.parts.len() == 1
                && new.bridges.is_empty()
                && !self.item(&t.parts[0]).continuous
                && !self.item(&new.parts[0]).continuous
                && ((new.down - t.down) as f64) * 1000. < self.multi("tap_interval_ms") * 60.
            {
                return None;
            }
        }
        v[2] = self.movement(prev, new)?;
        if let Some(t) = prev {
            v[6] = dist(t.last(), new.first());
            if self.opt("repeat_weight") != 0. {
                let gap = (self.nominal(new)[0] - self.nominal(t)[1]) as f64 / 1000.;
                let comfort = if self.multi("repeat_comfort_s") != 0. {
                    self.multi("repeat_comfort_s")
                } else {
                    self.opt("repeat_comfort_s")
                };
                v[5] = crate::numeric::pow((1. - gap / comfort).max(0.), 2.);
            }
            if self.multi("accel_max_m_s2") != 0. {
                let mut land = new.up() - 1;
                for p in &new.parts {
                    if p.start != new.down {
                        continue;
                    }
                    let o = self.item(p);
                    let c = &self.input.candidates[p.candidate];
                    if o.continuous && p.start == c.down && o.latest_down.is_some() {
                        land = land
                            .min(frame_after(o.latest_down.unwrap() + 1, self.input.offset) - 1);
                    } else {
                        land = new.down;
                    }
                }
                land = land.max(new.down);
                let duration = (self.ms(land) - self.ms(t.up())) as f64 / 1000.;
                let need = if duration <= 0. {
                    if dist(t.last(), new.xy(land)) > 1e-9 {
                        f64::INFINITY
                    } else {
                        0.
                    }
                } else {
                    PEAK * dist(t.last(), new.xy(land)) / crate::numeric::pow(duration, 2.)
                };
                v[30] = f64::from(need > self.multi("accel_max_m_s2"));
            }
        }
        if let Some(t) = other {
            if self.opt("crossing_pass_s") != 0. {
                if let Some(p) = prev {
                    let at = |f: i32| t.xy(f.clamp(t.down, t.up() - 1))[0];
                    let sign = if hand == 0 { 1. } else { -1. };
                    let before = sign * (at(p.up()) - p.last()[0]);
                    let after = sign * (at(new.down) - new.first()[0]);
                    if before.abs().min(after.abs()) >= self.opt("crossing_pass_margin_m")
                        && (before > 0.) != (after > 0.)
                        && (self.ms(new.down) - self.ms(p.up())) as f64 / 1000.
                            < self.opt("crossing_pass_s")
                    {
                        return None;
                    }
                }
            }
            if let Some((q, fresh)) = self.place(new, t) {
                if self.opt("apart_weight") != 0. {
                    let depth = if hand == 0 {
                        new.first()[0] - q[0]
                    } else {
                        q[0] - new.first()[0]
                    };
                    v[4] = fresh * (depth + self.opt("apart_margin_m")).max(0.);
                }
                if v[4] != 0. {
                    v[17 + self.kind(new.first(), hand, q)] = v[4];
                }
                v[26] = fresh * self.overlap(new.first(), hand, q);
            }
            if 0 <= self.ms(new.down) - self.ms(t.up())
                && self.ms(new.down) - self.ms(t.up()) < self.multi("trade_ms") as i64
                && dist(new.first(), t.last()) < self.multi("trade_m")
            {
                v[25] = 1.;
            }
        }
        if let Some(p) = prev {
            v[23] = v[5] * (dist(p.last(), new.first()) / self.multi("repeat_far_m")).min(1.);
        }
        let held = if self.multi("arc_takeover_weight") != 0. {
            state.owned[1 - hand].as_deref()
        } else {
            other.filter(|t| {
                t.parts
                    .iter()
                    .any(|p| matches!(self.item(p).kind.as_str(), "chain" | "hold"))
            })
        };
        if let Some(held) = held {
            let dt = self.ms(new.down) - self.ms(held.up());
            if dt >= 0
                && dt < self.window(held)
                && dist(new.first(), held.last()) < self.multi("trade_m")
                && prev.map_or(true, |p| {
                    dist(p.last(), held.last()) > self.multi("arc_takeover_from_m")
                })
            {
                v[28] = 1.;
            }
        }
        // Match which unweighted diagnostic feature fields Python records.
        let record = b(&self.input.multi, "geometry_features");
        for (index, name) in [
            (17, "apart_level_weight"),
            (18, "apart_near_weight"),
            (19, "apart_clear_weight"),
        ] {
            if !record
                && self.multi("apart_level_weight") == 0.
                && self.multi("apart_near_weight") == 0.
                && self.multi("apart_clear_weight") == 0.
            {
                v[index] = 0.;
            }
            let _ = name;
        }
        for (i, k) in [
            (23, "repeat_far_weight"),
            (25, "trade_weight"),
            (26, "collision_weight"),
            (28, "arc_takeover_weight"),
        ] {
            if !record && self.multi(k) == 0. {
                v[i] = 0.;
            }
        }
        Some(v)
    }
    fn finish(
        &self,
        state: &State,
        ci: usize,
        hand: usize,
        new: Arc<Touch>,
        old: Option<&Touch>,
        mut increment: Cost,
        share: bool,
        bridge: bool,
        events: &mut Vec<ExpansionEvent>,
    ) -> Option<State> {
        let c = &self.input.candidates[ci];
        let o = &self.input.items[c.item];
        events.push(ExpansionEvent::Arrival(new.clone()));
        if old.is_none() {
            for p in &new.parts {
                let item = self.item(p);
                if item.kind == "chain"
                    && (0..4).contains(&item.colour)
                    && state.locks[item.colour as usize] >= self.ms(new.down)
                    && b(&self.input.multi, "tick_contacts")
                {
                    return None;
                }
            }
        }
        if let Some(exclusions) = self.input.options["contact_exclusions"].as_array() {
            for x in exclusions {
                let id = x[0].as_str().unwrap_or("");
                if new.parts.iter().any(|p| {
                    let i = self.item(p);
                    i.kind == "chain" && i.parent.as_deref().unwrap_or(&i.oid) == id
                }) && self.ms(new.down) <= x[2].as_i64().unwrap()
                    && self.ms(new.up() - 1) >= x[1].as_i64().unwrap()
                {
                    return None;
                }
            }
        }
        if old.is_some_and(|old| new.down == old.down && new.path.starts_with(&old.path)) {
            events.push(ExpansionEvent::Prefix(
                new.clone(),
                state.hands[hand].as_ref().unwrap().clone(),
            ));
        }
        for p in &new.parts {
            if self.item(p).native
                && !self.input.geometry.native_valid(
                    self.input.candidates[p.candidate].item,
                    &new.path,
                    new.down,
                    &new.bindings,
                )
            {
                return None;
            }
        }
        if let Some(other) = state.hands[1 - hand].as_deref() {
            for (colour, frames) in &new.bindings {
                if other
                    .bindings
                    .get(colour)
                    .is_some_and(|fs| !fs.is_disjoint(frames))
                {
                    return None;
                }
            }
            for (colour, frames) in &new.bindings {
                for &f in frames {
                    if frames.contains(&(f - 1)) {
                        continue;
                    }
                    let span = new.parts.iter().find(|p| {
                        self.item(p).kind == "chain"
                            && self.item(p).colour == *colour
                            && p.start <= f
                            && f < p.stop
                    });
                    let Some(p) = span else {
                        continue;
                    };
                    if other.down >= new.down
                        || !other.active(f)
                        || other.bindings.values().any(|fs| fs.contains(&f))
                    {
                        continue;
                    }
                    let poly = self.item(p).polygon(f);
                    if !poly.is_empty() && nearest(poly, other.xy(f)) == other.xy(f) {
                        increment[15] = 1.;
                        break;
                    }
                }
            }
            let now = if hand == 0 {
                self.pair(&new, other)
            } else {
                self.pair(other, &new)
            };
            let was = old
                .map(|t| {
                    if hand == 0 {
                        self.pair(t, other)
                    } else {
                        self.pair(other, t)
                    }
                })
                .unwrap_or([0.; 7]);
            increment[1] = now[0] - was[0];
            if self.multi("crossing_depth_weight") != 0. {
                increment[31] = now[1] - was[1];
            }
            let record = b(&self.input.multi, "geometry_features")
                || [
                    "crossing_level_weight",
                    "crossing_near_weight",
                    "crossing_clear_weight",
                    "collision_s_weight",
                ]
                .iter()
                .any(|k| self.multi(k) != 0.);
            if record {
                for j in 0..3 {
                    increment[20 + j] = now[2 + j] - was[2 + j];
                }
                increment[27] = now[5] - was[5];
            }
            if self.wrist.is_some() {
                increment[37] = now[6] - was[6];
            }
        }
        if self.multi("accel_max_m_s2") != 0. {
            increment[30] += new.over - old.map_or(0., |t| t.over);
        }
        increment[9] = f64::from(share);
        increment[14] = f64::from(bridge);
        let mut out = state.clone();
        if old.is_none() && self.multi("jack_weight") != 0. {
            let fast = state.hands[hand].as_ref().is_some_and(|t| {
                self.ms(new.down) - self.ms(t.down) < self.multi("jack_gap_ms") as i64
            });
            out.runs[hand] = if fast {
                (state.runs[hand] + 1).min(self.multi("jack_free") as i32 + 1)
            } else {
                1
            };
            increment[16] = f64::from(out.runs[hand] > self.multi("jack_free") as i32);
        }
        out.hands[hand] = Some(new.clone());
        if self.multi("arc_takeover_weight") != 0. {
            if new.parts.iter().any(|p| self.item(p).contested) {
                out.owned[hand] = Some(new.clone());
            } else if let Some(t) = &out.owned[hand] {
                if self.ms(new.down) >= self.ms(t.up()) - 1
                    && dist(new.first(), t.last()) < self.multi("trade_m")
                {
                    out.owned[hand] = Some(new.clone());
                }
            }
            for h in 0..2 {
                if out.owned[h]
                    .as_ref()
                    .is_some_and(|t| self.ms(t.up()) + self.window(t) <= self.ms(new.down))
                {
                    out.owned[h] = None;
                }
            }
        }
        if b(&self.input.multi, "tick_contacts") {
            for &colour in new.bindings.keys() {
                if (0..4).contains(&colour) {
                    out.locks[colour as usize] = out.locks[colour as usize].max(
                        self.lock_expiry
                            .get(&(colour, new.up()))
                            .copied()
                            .unwrap_or_else(|| self.input.geometry.expiry(colour, new.up())),
                    );
                }
            }
        }
        out.cost = plus(&state.cost, &increment);
        out.node = Some(Arc::new(Node {
            previous: state.node.clone(),
            item: c.item,
            hand,
            touch: Some(new),
            extension: old.is_some(),
            increment,
        }));
        if b(&self.input.options, "full_point_window") && !o.continuous {
            let error = self.ms(out.hands[hand].as_ref().unwrap().down) - o.start;
            let node = Arc::make_mut(out.node.as_mut().unwrap());
            node.increment[38] = f64::from(!(-50..50).contains(&error));
            node.increment[39] = f64::from(!(-25..25).contains(&error));
            out.cost = plus(&state.cost, &node.increment);
        }
        Some(out)
    }
    fn expand(&self, state: &State, group: usize, index: usize) -> Vec<ExpansionEvent> {
        let _point_order = self.point_cache.enter(index);
        if self.cancellation.load(Ordering::Relaxed) {
            return vec![];
        }
        let mut out = vec![];
        let item = &self.input.items[group];
        if b(&self.input.multi, "allow_misses") || item.red {
            let mut increment = zero();
            increment[32] = item.score_weight as f64;
            increment[34] = f64::from(item.red);
            let mut skipped = state.clone();
            skipped.cost = plus(&state.cost, &increment);
            skipped.node = Some(Arc::new(Node {
                previous: state.node.clone(),
                item: group,
                hand: 0,
                touch: None,
                extension: false,
                increment,
            }));
            out.push(ExpansionEvent::Transition(skipped, state.cost, None));
        }
        if item.forced {
            return out;
        }
        for &ci in &self.input.groups[group] {
            if self.cancellation.load(Ordering::Relaxed) {
                return vec![];
            }
            let c = &self.input.candidates[ci];
            let item = &self.input.items[c.item];
            for hand in 0..2 {
                if c.fixed.is_some_and(|h| h != hand) {
                    continue;
                }
                let old = state.hands[hand].as_ref();
                let latest = state
                    .hands
                    .iter()
                    .filter_map(|t| t.as_ref())
                    .map(|t| self.latest(t))
                    .min_by_key(|c| Reverse(c.item));
                let press_ok = latest.map_or(true, |l| {
                    let prev = &self.input.items[l.item];
                    let relaxed = item.parent.is_some()
                        || prev.parent.is_some()
                        || (b(&self.input.options, "full_arc_hold")
                            && (item.kind == "chain" || prev.kind == "chain"));
                    c.down >= l.down || relaxed
                });
                let mut base = zero();
                base[..9].copy_from_slice(&c.part_base[hand]);
                if let Some(t) = old {
                    if item.parent.is_some()
                        && c.down >= t.down
                        && self.item(self.end(t)).parent == item.parent
                    {
                        for new in self.extension_variants(t, ci) {
                            if let Some(s) = self.finish(
                                state,
                                ci,
                                hand,
                                new,
                                Some(t),
                                base,
                                false,
                                true,
                                &mut out,
                            ) {
                                out.push(ExpansionEvent::Transition(s, state.cost, old.cloned()));
                            }
                        }
                    }
                }
                let mut bridged = false;
                if self.multi("bridge_max_gap_ms") != 0.
                    && item.kind == "chain"
                    && item.parent.is_none()
                    && press_ok
                {
                    if let Some(t) = old {
                        if !t.active(c.down) {
                            if let Some((new, mut increment)) = self.bridge(t, ci) {
                                increment[0] = base[0];
                                increment[3] = base[3];
                                increment[7] = base[7];
                                increment[8] = base[8];
                                if b(&self.input.multi, "jump_slides") {
                                    increment[7] = (increment[7]
                                        - (self.ms(c.down)
                                            - self.ms(frame_after(item.start, self.input.offset)))
                                        .max(0) as f64
                                            / 1000.)
                                        .max(0.);
                                }
                                if let Some(s) = self.finish(
                                    state,
                                    ci,
                                    hand,
                                    new,
                                    Some(t),
                                    increment,
                                    false,
                                    true,
                                    &mut out,
                                ) {
                                    out.push(ExpansionEvent::Transition(
                                        s,
                                        state.cost,
                                        old.cloned(),
                                    ));
                                    bridged = true;
                                }
                            }
                        }
                    }
                }
                if !old.is_some_and(|t| t.active(c.down))
                    && !bridged
                    && press_ok
                    && !(item.parent.is_some() && self.ms(c.down) > item.source_end)
                {
                    let mut new = self.singles[ci].clone();
                    if self.opt("acceleration_weight") != 0. {
                        if let Some(t) = old {
                            new = self.touch(
                                new.parts.clone(),
                                new.bridges.clone(),
                                new.down,
                                new.path.clone(),
                                new.over,
                                Some(Arrival {
                                    up: t.up(),
                                    path: t.path[t.path.len().saturating_sub(2)..].to_vec(),
                                }),
                                None,
                            );
                        }
                    }
                    if let Some(mut increment) = self.new_cost(state, hand, &new) {
                        increment[0] = base[0];
                        increment[3] = base[3];
                        increment[7] = base[7];
                        increment[8] = base[8];
                        if let Some(s) = self.finish(
                            state, ci, hand, new, None, increment, false, false, &mut out,
                        ) {
                            out.push(ExpansionEvent::Transition(s, state.cost, None));
                        }
                    }
                }
                if b(&self.input.multi, "share") && matches!(item.kind.as_str(), "chain" | "hold") {
                    if let Some(t) = old {
                        if t.active(c.down)
                            && !(item.parent.is_some()
                                && self.item(self.end(t)).parent == item.parent)
                            && self.may_join(t, ci)
                            && self.may_share(t, ci)
                        {
                            if let Some(new) = self.share(t, ci) {
                                if let Some(s) = self.finish(
                                    state,
                                    ci,
                                    hand,
                                    new,
                                    Some(t),
                                    base,
                                    true,
                                    false,
                                    &mut out,
                                ) {
                                    out.push(ExpansionEvent::Transition(
                                        s,
                                        state.cost,
                                        old.cloned(),
                                    ));
                                }
                            }
                        }
                    }
                }
            }
        }
        if let Some(crossing) = &self.crossing {
            out.into_iter()
                .flat_map(|event| match event {
                    ExpansionEvent::Transition(state, before, previous)
                        if state.node.as_ref().unwrap().touch.is_some() =>
                    {
                        let node = state.node.as_ref().unwrap();
                        // Python prices the contact before testing arm routes,
                        // including contacts whose every arm route is rejected.
                        // Retain that encounter order for prefix-dependent caches.
                        let mut events = vec![ExpansionEvent::Price(
                            node.touch.as_ref().unwrap().clone(),
                            previous.clone(),
                        )];
                        events.extend(
                            crossing
                                .step(
                                    state.arms.as_ref().unwrap(),
                                    node.touch.as_ref().unwrap(),
                                    node.hand,
                                    node.extension,
                                )
                                .into_iter()
                                .map(|route| {
                                    ExpansionEvent::Routed(
                                        state.clone(),
                                        before,
                                        previous.clone(),
                                        Arc::new(route),
                                    )
                                }),
                        );
                        events
                    }
                    _ => vec![event],
                })
                .collect()
        } else {
            out
        }
    }
    fn touch_key(&self, t: &Touch, f0: i32, key: &mut Vec<u64>) {
        let end = self.end(t);
        let c = &self.input.candidates[end.candidate];
        let item = &self.input.items[c.item];
        let parts: Vec<_> = t
            .parts
            .iter()
            .filter(|p| p.stop > f0 || (self.item(p).kind == "hold" && p.stop > f0 - 25))
            .collect();
        key.push(parts.len() as u64);
        for p in parts {
            key.extend([p.candidate as u64, p.start as u64, p.stop as u64]);
        }
        key.extend([
            t.up() as u64,
            bits(t.last()[0]),
            bits(t.last()[1]),
            c.item as u64,
            c.up as u64,
            end.stop as u64,
            u64::from(c.held_to_end),
            if item.continuous {
                u64::MAX
            } else {
                c.down as u64
            },
        ]);
        let latest = self.latest(t);
        key.extend([
            latest.down as u64,
            latest.item as u64,
            if t.down >= f0 {
                t.down as u64
            } else {
                u64::MAX
            },
        ]);
        let tail = &t.path[((f0 - t.down).max(0) as usize).min(t.path.len())..];
        key.push(tail.len() as u64);
        for p in tail {
            key.extend([bits(p[0]), bits(p[1])]);
        }
        let bridges: Vec<_> = t.bridges.iter().filter(|s| s[1] > f0).collect();
        key.push(bridges.len() as u64);
        for s in bridges {
            key.extend([s[0] as u64, s[1] as u64]);
        }
        if self.opt("acceleration_weight") != 0. {
            for p in &t.path[t.path.len().saturating_sub(2)..] {
                key.extend([bits(p[0]), bits(p[1])]);
            }
            key.push(u64::MAX - 1);
            if let Some(a) = &t.arrival {
                key.push(a.up as u64);
                for p in &a.path {
                    key.extend([bits(p[0]), bits(p[1])]);
                }
            }
            key.push(u64::MAX - 2);
        }
    }
    fn key(&self, s: &State, f0: i32) -> Vec<u64> {
        let mut key = vec![];
        if self.opt("crossing_pass_s") != 0. {
            // This rule reads the other hand's earlier release. Python keeps
            // the complete raw state rather than merging future suffixes.
            key.extend(
                s.hands
                    .iter()
                    .map(|t| t.as_ref().map_or(u64::MAX, |t| t.uid as u64)),
            );
            if self.multi("jack_weight") != 0. {
                key.extend(s.runs.iter().map(|x| *x as u64));
            }
            if self.multi("arc_takeover_weight") != 0. {
                key.extend(
                    s.owned
                        .iter()
                        .map(|t| t.as_ref().map_or(u64::MAX, |t| t.uid as u64)),
                );
            }
            if b(&self.input.multi, "tick_contacts") {
                key.extend(s.locks.iter().map(|v| *v as u64));
            }
            if let Some(arms) = &s.arms {
                key.extend(arms.key());
            }
            return key;
        }
        for t in &s.hands {
            if let Some(t) = t {
                key.push(1);
                self.touch_key(t, f0, &mut key);
            } else {
                key.push(0);
            }
        }
        if self.multi("jack_weight") != 0. {
            key.extend(s.runs.iter().map(|x| *x as u64));
        }
        if self.multi("arc_takeover_weight") != 0. {
            for t in &s.owned {
                if let Some(t) = t {
                    if self.ms(t.up()) + self.window(t) > self.ms(f0) {
                        key.extend([
                            1,
                            t.up() as u64,
                            bits(t.last()[0]),
                            bits(t.last()[1]),
                            self.window(t) as u64,
                        ]);
                        continue;
                    }
                }
                key.push(0);
            }
        }
        if b(&self.input.multi, "tick_contacts") {
            key.extend(s.locks.iter().map(|v| {
                if *v >= self.ms(f0) {
                    *v as u64
                } else {
                    u64::MAX
                }
            }));
        }
        if let Some(arms) = &s.arms {
            key.extend(arms.key());
        }
        key
    }
    fn apply_event(&self, event: ExpansionEvent) -> Option<State> {
        match event {
            ExpansionEvent::Routed(state, before, previous, route) => {
                let mut state = self
                    .apply_event(ExpansionEvent::Transition(state, before, previous))
                    .unwrap();
                let old = state.arms.as_ref().unwrap();
                let extra = route.extra() - old.extra();
                state.cost[1] = route.seconds();
                state.cost[6] += extra;
                let node = Arc::make_mut(state.node.as_mut().unwrap());
                node.increment[1] = route.seconds() - old.seconds();
                node.increment[6] += extra;
                state.arms = Some(route);
                Some(state)
            }
            ExpansionEvent::Price(touch, previous) => {
                self.measures(&touch);
                if let Some(previous) = previous {
                    self.measures(&previous);
                }
                None
            }
            ExpansionEvent::Arrival(touch) => {
                if let Some(base) = &touch.arrival_base {
                    if touch.arrival_initialized.set(()).is_ok() {
                        // Python with_arrival copies these fields from the
                        // physical base touch at its first ordered encounter.
                        if let Some(prefix) = base.prefix.get() {
                            let _ = touch.prefix.set(prefix.clone());
                        }
                        let inherited = base
                            .measures
                            .get()
                            .map_or([None; 2], |v| [Some(v[0]), Some(v[2])]);
                        let _ = touch.inherited_measures.set(inherited);
                    }
                }
                None
            }
            ExpansionEvent::Prefix(touch, prefix) => {
                // This event is emitted before validation, including branches
                // rejected later. A later event must not replace the first
                // matching prefix, or invalidate an already priced touch.
                let _ = touch.prefix.set(prefix);
                None
            }
            ExpansionEvent::Transition(mut state, before, previous) => {
                let node = Arc::make_mut(state.node.as_mut().unwrap());
                if let Some(touch) = &node.touch {
                    let now = self.measures(touch);
                    let old = previous
                        .as_deref()
                        .map(|t| *self.measures(t))
                        .unwrap_or([0.; 5]);
                    if self.opt("acceleration_weight") != 0. {
                        node.increment[35] += now[2] - old[2] + now[4] - old[4];
                    }
                    if self.opt("note_center_weight") != 0. {
                        node.increment[36] = now[3] - old[3];
                    }
                    if b(&self.input.options, "prefer_arc_trace")
                        && self.opt("arc_trace_weight") != 0.
                    {
                        node.increment[6] += now[0] - old[0];
                        node.increment[33] = now[1] - old[1];
                    }
                    state.cost = plus(&before, &node.increment);
                }
                Some(state)
            }
        }
    }
    fn sorted(&self, states: &mut [State]) {
        states.sort_by(|a, b| {
            if self.less(&a.cost, &b.cost) {
                std::cmp::Ordering::Less
            } else if self.less(&b.cost, &a.cost) {
                std::cmp::Ordering::Greater
            } else {
                std::cmp::Ordering::Equal
            }
        });
    }
    fn finished(&self, state: &State) -> Vec<State> {
        let Some(crossing) = &self.crossing else {
            return vec![state.clone()];
        };
        let arms = state.arms.as_ref().unwrap();
        crossing
            .finish(arms)
            .into_iter()
            .map(|route| {
                let mut next = state.clone();
                next.cost[1] = route.seconds();
                next.cost[6] += route.extra() - arms.extra();
                next.arms = Some(Arc::new(route));
                next
            })
            .collect()
    }
    pub fn advance(&mut self) -> Result<(), String> {
        if self.cancellation.load(Ordering::Relaxed) {
            return Err("native search cancelled".into());
        }
        if self.layer >= self.input.groups.len() {
            return Ok(());
        }
        let k = self.layer;
        self.point_cache.reset_layer(self.states.len());
        let batches: Vec<Vec<ExpansionEvent>> =
            if self.states.len() < 32 || self.pool.current_num_threads() == 1 {
                self.states
                    .iter()
                    .enumerate()
                    .map(|(i, s)| self.expand(s, k, i))
                    .collect()
            } else {
                // Monotone round-robin ownership keeps the earliest unfinished
                // state running even when later states wait on a new point key.
                // Generic work stealing could queue that earlier state behind all
                // blocked workers, so use one broadcast task per persistent worker.
                let mut indexed: Vec<_> = self
                    .pool
                    .broadcast(|context| {
                        (context.index()..self.states.len())
                            .step_by(context.num_threads())
                            .map(|i| (i, self.expand(&self.states[i], k, i)))
                            .collect::<Vec<_>>()
                    })
                    .into_iter()
                    .flatten()
                    .collect();
                indexed.sort_by_key(|(i, _)| *i);
                indexed.into_iter().map(|(_, events)| events).collect()
            };
        if self.cancellation.load(Ordering::Relaxed) {
            return Err("native search cancelled".into());
        }
        let mut following: Vec<State> = vec![];
        let mut positions: HashMap<Vec<u64>, usize> = HashMap::new();
        for event in batches.into_iter().flatten() {
            let Some(state) = self.apply_event(event) else {
                continue;
            };
            if self.cancellation.load(Ordering::Relaxed) {
                return Err("native search cancelled".into());
            }
            let key = self.key(
                &state,
                if self.earliest[k] == i32::MAX {
                    0
                } else {
                    self.earliest[k]
                },
            );
            if let Some(&i) = positions.get(&key) {
                if self.less(&state.cost, &following[i].cost) {
                    following[i] = state;
                }
            } else {
                positions.insert(key, following.len());
                following.push(state);
            }
        }
        if following.is_empty() {
            let item = &self.input.items[k];
            return Err(format!(
                "no candidate path at item {}: {} ({} at {} ms)",
                k, item.oid, item.kind, item.start
            ));
        }
        if self.crossing.is_some() && k + 1 == self.input.groups.len() {
            self.final_alternatives = following.clone();
        }
        let max = self.multi("max_states") as usize;
        let pruned = max != 0 && following.len() > max;
        if pruned {
            following.sort_by(|a, b| {
                if self.less(&a.cost, &b.cost) {
                    std::cmp::Ordering::Less
                } else if self.less(&b.cost, &a.cost) {
                    std::cmp::Ordering::Greater
                } else {
                    std::cmp::Ordering::Equal
                }
            });
            let item = &self.input.items[k];
            if b(&self.input.options, "full_point_window")
                && !item.continuous
                && item.start - item.earliest_down > 50
            {
                let mut schedules = HashSet::new();
                let mut chosen = HashSet::new();
                let mut distinct = vec![];
                let best = [following[0].cost[32], following[0].cost[34]];
                for (i, state) in following.iter().enumerate() {
                    if [state.cost[32], state.cost[34]] != best {
                        break;
                    }
                    let timing = state
                        .hands
                        .each_ref()
                        .map(|t| t.as_ref().map(|t| (t.down, t.up())));
                    if schedules.insert(timing) {
                        chosen.insert(i);
                        distinct.push(state.clone());
                        if distinct.len() == max {
                            break;
                        }
                    }
                }
                distinct.extend(
                    following
                        .into_iter()
                        .enumerate()
                        .filter(|(i, _)| !chosen.contains(i))
                        .map(|(_, s)| s),
                );
                following = distinct;
            }
            if self.crossing.is_some() && b(&self.input.multi, "allow_misses") && max >= 4 {
                // Preserve ranked branches that can keep playing if the next
                // target is skipped. Pending routes may still become legal
                // when the other hand moves, so they keep most of the beam.
                let reserve = max / 4;
                let ready: Vec<_> = following
                    .iter()
                    .enumerate()
                    .filter(|(_, s)| {
                        crate::arms::CrossingGraph::can_stop_in_place(s.arms.as_ref().unwrap())
                    })
                    .map(|(i, _)| i)
                    .take(reserve)
                    .collect();
                let mut chosen: HashSet<_> = ready.into_iter().collect();
                for i in 0..following.len() {
                    if chosen.len() == max {
                        break;
                    }
                    chosen.insert(i);
                }
                following = following
                    .into_iter()
                    .enumerate()
                    .filter_map(|(i, state)| chosen.contains(&i).then_some(state))
                    .collect();
            } else {
                following.truncate(max);
            }
        }
        // Nothing from an interrupted layer becomes the next frontier.
        if self.cancellation.load(Ordering::Relaxed) {
            return Err("native search cancelled".into());
        }
        if self.crossing.is_some()
            && b(&self.input.multi, "allow_misses")
            && k + 1 < self.input.groups.len()
        {
            let mut ordered = following.clone();
            self.sorted(&mut ordered);
            let ticks = self.input.items[k + 1..]
                .iter()
                .map(|i| i.score_weight as f64)
                .sum::<f64>();
            let reds = self.input.items[k + 1..].iter().filter(|i| i.red).count() as f64;
            for mut state in ordered {
                state.cost[32] += ticks;
                state.cost[34] += reds;
                if self.completed_prefix.as_ref().is_some_and(|(best, _)| {
                    [
                        state.cost[32],
                        state.cost[34],
                        state.cost[38],
                        state.cost[39],
                    ] > [best.cost[32], best.cost[34], best.cost[38], best.cost[39]]
                }) {
                    break;
                }
                let routes = self.finished(&state);
                let found = !routes.is_empty();
                for route in routes {
                    if self
                        .completed_prefix
                        .as_ref()
                        .is_none_or(|(best, _)| self.less(&route.cost, &best.cost))
                    {
                        self.completed_prefix = Some((route, k + 1));
                    }
                }
                if found {
                    break;
                }
            }
        }
        if self.crossing.is_some() && k + 1 == self.input.groups.len() {
            let mut ordered = following.clone();
            self.sorted(&mut ordered);
            let mut best: Option<State> = None;
            for state in &ordered {
                for route in self.finished(state) {
                    if best
                        .as_ref()
                        .is_none_or(|b| self.less(&route.cost, &b.cost))
                    {
                        best = Some(route);
                    }
                }
            }
            if best.is_none() {
                for state in &self.final_alternatives {
                    if following.iter().any(|s| match (&s.node, &state.node) {
                        (Some(a), Some(b)) => Arc::ptr_eq(a, b),
                        _ => false,
                    }) {
                        continue;
                    }
                    for route in self.finished(state) {
                        if best
                            .as_ref()
                            .is_none_or(|b| self.less(&route.cost, &b.cost))
                        {
                            best = Some(route);
                        }
                    }
                }
                if let Some((prefix, stop)) = &self.completed_prefix {
                    if best
                        .as_ref()
                        .is_none_or(|b| self.less(&prefix.cost, &b.cost))
                    {
                        let mut state = prefix.clone();
                        for item in *stop..self.input.items.len() {
                            let mut increment = zero();
                            increment[32] = self.input.items[item].score_weight as f64;
                            increment[34] = f64::from(self.input.items[item].red);
                            state.node = Some(Arc::new(Node {
                                previous: state.node.clone(),
                                item,
                                hand: 0,
                                touch: None,
                                extension: false,
                                increment,
                            }));
                        }
                        best = Some(state);
                    }
                }
            }
            let Some(best) = best else {
                return Err("no candidate path through the final contacts".into());
            };
            self.final_alternatives = vec![best];
        }
        // Every newly constructed touch includes a candidate from the
        // current layer. Older touches survive through their Arc owners
        // (the beam, backpointers and single-candidate table).
        *self.interned[k].get_mut().unwrap() = HashMap::new();
        // Extension and share keys include the current candidate, and
        // pair prices can be recomputed from immutable touch geometry.
        self.extensions.get_mut().unwrap().clear();
        self.shares.get_mut().unwrap().clear();
        self.pairs.get_mut().unwrap().clear();
        self.pruned += usize::from(pruned);
        self.visited += following.len();
        self.states = following;
        self.layer += 1;
        Ok(())
    }
    pub fn result(&self) -> serde_json::Value {
        let frontier = if self.layer == self.input.groups.len() && self.crossing.is_some() {
            &self.final_alternatives
        } else {
            &self.states
        };
        let best = frontier
            .iter()
            .min_by(|a, b| {
                if self.less(&a.cost, &b.cost) {
                    std::cmp::Ordering::Less
                } else if self.less(&b.cost, &a.cost) {
                    std::cmp::Ordering::Greater
                } else {
                    std::cmp::Ordering::Equal
                }
            })
            .unwrap();
        let mut node = best.node.clone();
        let mut steps = vec![];
        while let Some(n) = node {
            steps.push(serde_json::json!({"item":n.item,"hand":n.hand,"touch":n.touch,"extension":n.extension,"increment":n.increment.to_vec()}));
            node = n.previous.clone();
        }
        steps.reverse();
        serde_json::json!({"arms":best.arms,"steps":steps,"cost":best.cost.to_vec(),"states_visited":self.visited,"pruned_items":self.pruned,"point_cache":self.point_cache.snapshot()})
    }

    pub fn debug_snapshot(&self) -> serde_json::Value {
        let states: Vec<_> = self
            .states
            .iter()
            .map(|state| {
                serde_json::json!({
                    "hands":state.hands,"owned":state.owned,"runs":state.runs,"locks":state.locks,
                    "cost":state.cost.to_vec(),"weighted":self.weighted(&state.cost)
                })
            })
            .collect();
        serde_json::json!({"layer":self.layer,"visited":self.visited,"pruned":self.pruned,
                          "states":states,"best_route":self.result()})
    }
}
