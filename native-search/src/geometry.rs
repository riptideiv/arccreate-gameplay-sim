//! Native ArcCreate camera-envelope judgement; input is immutable per search.
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};
type XY = [f64; 2];
pub type Bindings = BTreeMap<i32, BTreeSet<i32>>;
const HALF_X: f64 = 135.315277 / 1148.;
const HALF_Y: f64 = 172.058334 / 802.;
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Camera {
    pub width: f64,
    pub height: f64,
    pub position: [f64; 3],
    pub forward: [f64; 3],
    pub scale: f64,
    pub bases: Vec<[[f64; 3]; 2]>,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct ScorePoint {
    pub first: i32,
    pub targets: Vec<XY>,
    #[serde(default)]
    pub rectangles: Vec<[f64; 4]>,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct ScoreItem {
    pub native: bool,
    pub colour: i32,
    pub points: Vec<ScorePoint>,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Guard {
    pub weight: f64,
    pub targets: BTreeMap<i32, Vec<XY>>,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Geometry {
    pub camera: Camera,
    pub offset: i64,
    pub regions: BTreeMap<i32, Vec<(i32, f64, f64)>>,
    pub grace: BTreeSet<i32>,
    pub guards: BTreeMap<i32, Guard>,
    pub scores: Vec<ScoreItem>,
    pub lock_grace: Vec<i32>,
    pub arc_notes: Vec<(i32, i64, i64, Option<f64>)>,
}
fn dot(a: [f64; 3], b: [f64; 3]) -> f64 {
    crate::numeric::py_sum((0..3).map(|j| a[j] * b[j]))
}
impl Camera {
    pub fn project(&self, point: XY, view: usize) -> XY {
        let p = [
            point[0] / 100. - self.position[0],
            point[1] / 100. - self.position[1],
            -self.position[2],
        ];
        let [r, u] = self.bases[view];
        let z = dot(p, self.forward);
        [dot(p, r) * self.scale / z, dot(p, u) * self.scale / z]
    }
    pub fn unproject(&self, xy: XY, view: usize) -> XY {
        let [r, u] = self.bases[view];
        let mut ray = [0.; 3];
        for j in 0..3 {
            ray[j] = self.forward[j] + r[j] * xy[0] / self.scale + u[j] * xy[1] / self.scale;
        }
        let distance = -self.position[2] / ray[2];
        [
            (self.position[0] + distance * ray[0]) * 100.,
            (self.position[1] + distance * ray[1]) * 100.,
        ]
    }
    pub fn collides(&self, xy: XY, target: XY, certain: bool, inset: f64) -> bool {
        let mut any = false;
        for view in 0..self.bases.len() {
            let mut p = self.unproject(xy, view);
            if target[1] <= 550. {
                p[1] = p[1].min(550.);
            }
            let mut hit = (p[0] - target[0]).abs() <= 190. - inset
                && (p[1] - target[1]).abs() <= 250. - inset;
            if !hit {
                let a = self.project(p, view);
                let b = self.project(target, view);
                let margin = if inset != 0. { 0.001 } else { 0. };
                hit = (a[0] - b[0]).abs() <= HALF_X * self.width - margin
                    && (a[1] - b[1]).abs() <= HALF_Y * self.height - margin;
            }
            if certain && !hit {
                return false;
            }
            if !certain && hit {
                return true;
            }
            any |= hit;
        }
        if certain {
            true
        } else {
            any
        }
    }
}
impl Geometry {
    pub fn frame_ms(&self, frame: i32) -> i64 {
        ((frame as f64) * 1000. / 60.).round_ties_even() as i64 - self.offset
    }
    pub fn graced(&self, frame: i32) -> bool {
        self.grace.contains(&frame)
    }
    pub fn guard_path(&self, colour: i32, path: &mut [XY], down: i32, fixed_prefix: usize) {
        for (k, p) in path.iter_mut().enumerate().skip(fixed_prefix) {
            let Some(g) = self.guards.get(&(down + k as i32)) else {
                continue;
            };
            if g.weight == 0. {
                continue;
            }
            let Some(targets) = g.targets.get(&colour) else {
                continue;
            };
            // Strict replacement retains the first target in an exact tie.
            let mut nearest = None;
            let mut distance = f64::INFINITY;
            for q in targets {
                let d = crate::numeric::dist(*p, *q);
                if d < distance {
                    distance = d;
                    nearest = Some(q);
                }
            }
            if let Some(q) = nearest {
                p[0] += (q[0] - p[0]) * g.weight;
                p[1] += (q[1] - p[1]) * g.weight;
            }
        }
    }
    pub fn bindings(&self, path: &[XY], down: i32) -> Bindings {
        let mut found = Bindings::new();
        let mut owners = BTreeSet::new();
        for (k, xy) in path.iter().enumerate() {
            let f = down + k as i32;
            if self.graced(f) {
                owners.clear();
                continue;
            }
            if owners.is_empty() {
                if let Some(regions) = self.regions.get(&f) {
                    for &(c, x, y) in regions {
                        if self.camera.collides(*xy, [x, y], false, 0.) {
                            owners.insert(c);
                        }
                    }
                }
            }
            for &c in &owners {
                found.entry(c).or_default().insert(f);
            }
        }
        found
    }
    pub fn covers(&self, item: usize, path: &[XY], down: i32) -> bool {
        self.check(item, path, down, None)
    }
    pub fn native_valid(&self, item: usize, path: &[XY], down: i32, bindings: &Bindings) -> bool {
        !self.scores[item].native || self.check(item, path, down, Some(bindings))
    }
    fn check(&self, item: usize, path: &[XY], down: i32, bindings: Option<&Bindings>) -> bool {
        let item = &self.scores[item];
        item.points.iter().all(|point| {
            let first = down.max(point.first);
            let end = (down + path.len() as i32).min(point.first + point.targets.len() as i32);
            (first..end).any(|f| {
                if let Some(bound) = bindings {
                    if !self.graced(f)
                        && !(bound.get(&item.colour).is_some_and(|fs| fs.contains(&f))
                            && bound.values().filter(|fs| fs.contains(&f)).count() == 1)
                    {
                        return false;
                    }
                }
                let xy = path[(f - down) as usize];
                if let Some(r) = point.rectangles.get((f - point.first) as usize) {
                    if xy[0] < r[0] - 1e-9
                        || xy[0] > r[2] + 1e-9
                        || xy[1] < r[1] - 1e-9
                        || xy[1] > r[3] + 1e-9
                    {
                        return false;
                    }
                }
                self.camera.collides(
                    path[(f - down) as usize],
                    point.targets[(f - point.first) as usize],
                    true,
                    8.,
                )
            })
        })
    }
    pub fn expiry(&self, colour: i32, up: i32) -> i64 {
        let now = self.frame_ms(up);
        let active: Vec<_> = self
            .arc_notes
            .iter()
            .filter(|(c, a, b, _)| *c == colour && *a <= now && now <= *b)
            .collect();
        if active.is_empty() {
            return -1;
        }
        let interval = active
            .iter()
            .map(|(_, _, _, i)| i.unwrap_or(f64::INFINITY))
            .fold(f64::NEG_INFINITY, f64::max);
        let duration = if interval.is_finite() {
            (interval * 4.).round_ties_even().min(1000.) as i64
        } else {
            1000
        };
        let mut end = active.iter().map(|(_, _, b, _)| *b).max().unwrap();
        loop {
            let next = self
                .arc_notes
                .iter()
                .filter(|(c, a, b, _)| *c == colour && *a <= end + 1 && *b > end)
                .map(|(_, _, b, _)| *b)
                .max();
            match next {
                Some(value) => end = value,
                None => break,
            }
        }
        let mut unlock = (now + duration).min(end);
        let i = self.lock_grace.partition_point(|f| *f < up);
        if let Some(&f) = self.lock_grace.get(i) {
            unlock = unlock.min(self.frame_ms(f) - 1);
        }
        unlock
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn python_camera_binding_and_guard_reference() {
        let v: serde_json::Value =
            serde_json::from_str(include_str!("../tests/geometry_reference.json")).unwrap();
        let g: Geometry = serde_json::from_value(v["geometry"].clone()).unwrap();
        let path: Vec<XY> = serde_json::from_value(v["path"].clone()).unwrap();
        let expected: Bindings = serde_json::from_value(v["bindings"].clone()).unwrap();
        assert_eq!(g.bindings(&path, 0), expected);
        for p in v["probes"].as_array().unwrap() {
            let xy: XY = serde_json::from_value(p["xy"].clone()).unwrap();
            let q: XY = serde_json::from_value(p["target"].clone()).unwrap();
            assert_eq!(
                g.camera.collides(xy, q, false, 0.),
                p["any"].as_bool().unwrap()
            );
            assert_eq!(
                g.camera.collides(xy, q, true, 8.),
                p["certain"].as_bool().unwrap()
            );
        }
        let mut guarded = path;
        g.guard_path(0, &mut guarded, 0, 12);
        let expected: Vec<XY> = serde_json::from_value(v["guarded"].clone()).unwrap();
        for (a, b) in guarded.iter().zip(&expected) {
            for j in 0..2 {
                assert!((a[j] - b[j]).abs() < 1e-14);
            }
        }
        assert_eq!(g.expiry(0, 10), 499); // Grace starts at frame 30, clearing the lock.
        assert_eq!(g.expiry(0, 150), -1);
    }
}
