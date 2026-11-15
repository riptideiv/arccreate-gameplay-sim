//! Contact-pinned travel and crossing topology, matching the Python planner.
use crate::model::{bits, Touch};
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet, VecDeque};
use std::sync::{Arc, Mutex};

type Point = [f64; 3];
const EPS: f64 = 1e-5;
const TIP: f64 = 0.006;
const RATE: f64 = 60.;
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Crossing {
    pub start_frame: f64,
    pub end_frame: Option<f64>,
    pub top: usize,
    pub entered_at: i32,
    pub exited_at: Option<i32>,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct FreeTravel {
    pub start: usize,
    pub end: usize,
    pub extra_distance_m: f64,
    pub curved: bool,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct HandPath {
    pub points: Vec<Point>,
    pub anchor: usize,
    pub locked: Vec<bool>,
    pub travel: Vec<FreeTravel>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ArmState {
    pub paths: [Arc<HandPath>; 2],
    pub routes: Vec<Crossing>,
}
type ProjectionKey = (usize, usize, bool, usize);
#[derive(Default)]
struct ProjectionCache {
    // Retain the source Arc so its address cannot be recycled while cached.
    values: HashMap<ProjectionKey, (Arc<HandPath>, Option<Arc<HandPath>>)>,
    order: VecDeque<ProjectionKey>,
}
pub struct CrossingGraph {
    lift: f64,
    speed: f64,
    idle: f64,
    requested: usize,
    homes: [Point; 2],
    projections: Mutex<ProjectionCache>,
}
fn delta(a: Point, b: Point) -> Point {
    std::array::from_fn(|j| a[j] - b[j])
}
fn norm(a: Point) -> f64 {
    (a[0] * a[0] + a[1] * a[1] + a[2] * a[2]).sqrt()
}
fn lengths(p: &[Point]) -> Vec<f64> {
    p.windows(2).map(|w| norm(delta(w[1], w[0]))).collect()
}
fn total_length(p: &[Point]) -> f64 {
    crate::trajectory::pairwise(&lengths(p))
}
fn order(routes: &[Crossing]) -> Option<usize> {
    routes
        .last()
        .filter(|r| r.end_frame.is_none())
        .map(|r| r.top)
}
fn prefix(routes: &[Crossing], frame: i32) -> Vec<Crossing> {
    let mut keep = vec![];
    for r in routes {
        if r.entered_at > frame {
            break;
        }
        let mut c = r.clone();
        if r.exited_at.is_none_or(|f| f > frame) {
            c.end_frame = None;
            c.exited_at = None;
            keep.push(c);
            break;
        }
        keep.push(c);
    }
    keep
}
fn travel(a: Point, b: Point, frames: usize, lift: f64, speed: f64) -> Vec<Point> {
    let mut path = vec![];
    for linear in [0., 0.25, 0.5, 0.75, 1.] {
        path = (0..=frames)
            .map(|i| {
                let t = if i == frames {
                    1.
                } else {
                    i as f64 * (1. / frames as f64)
                };
                let eased = t.powf(3.) * (10. - 15. * t + 6. * t * t);
                let u = (1. - linear) * eased + linear * t;
                let mut p = std::array::from_fn(|j| a[j] + u * (b[j] - a[j]));
                p[2] += (1. - linear)
                    * lift.min(0.001 * frames as f64)
                    * (std::f64::consts::PI * t).sin().powi(2);
                p
            })
            .collect();
        if lengths(&path).into_iter().fold(0., f64::max) * RATE <= speed + 0.0001 {
            break;
        }
    }
    path
}
fn span(
    a: Point,
    b: Point,
    frames: usize,
    home: Point,
    lift: f64,
    speed: f64,
    idle: f64,
) -> Vec<Point> {
    if frames == 0 {
        return vec![b];
    }
    let distances = [norm(delta(home, a)), norm(delta(b, home))];
    if frames >= 60 {
        let cap = idle.min(speed * 0.8);
        let pace = 0.30_f64
            .min(cap)
            .max(1.875 * (distances[0] + distances[1]) * RATE / (frames - 15) as f64);
        if pace <= cap {
            let counts = distances.map(|d| {
                if d < 1e-9 {
                    0
                } else {
                    8.max((1.875 * d / pace * RATE).ceil() as usize)
                }
            });
            let [out, back] = counts;
            if out + back + 15 <= frames {
                let mut points = if out > 0 {
                    travel(
                        a,
                        home,
                        out,
                        lift,
                        speed.min(idle.max(distances[0] * RATE / out as f64)),
                    )[..out]
                        .to_vec()
                } else {
                    vec![]
                };
                points.extend(std::iter::repeat_n(home, frames - out - back));
                points.extend(if back > 0 {
                    travel(
                        home,
                        b,
                        back,
                        lift,
                        speed.min(idle.max(distances[1] * RATE / back as f64)),
                    )
                } else {
                    vec![home]
                });
                return points;
            }
        }
    }
    let resting = a == home && b == home;
    let cap = if a == home || b == home {
        speed.min(idle.max(norm(delta(b, a)) * RATE / frames as f64))
    } else {
        speed
    };
    travel(a, b, frames, if resting { 0. } else { lift }, cap)
}
struct Pass {
    sample: usize,
    frame: f64,
    entering: bool,
    tops: Vec<usize>,
}
fn passes(left: &[Point], right: &[Point], start: usize, mut crossed: bool) -> Vec<Pass> {
    let mut out = vec![];
    for i in start + 1..left.len() {
        let dx = right[i][0] - left[i][0];
        if dx.abs() <= EPS {
            continue;
        }
        let entering = dx < 0.;
        if entering == crossed {
            continue;
        }
        crossed = entering;
        let before = right[i - 1][0] - left[i - 1][0];
        let d = dx - before;
        let u = if d != 0. {
            (-before / d).clamp(0., 1.)
        } else {
            0.
        };
        let at: [Point; 2] = std::array::from_fn(|h| {
            let p = if h == 0 { left } else { right };
            std::array::from_fn(|j| p[i - 1][j] + u * (p[i][j] - p[i - 1][j]))
        });
        let above = usize::from(at[1][1] > at[0][1]);
        let p = if above == 0 { left } else { right };
        let toward = (p[i][0] - p[i - 1][0])
            * if above == 0 { 1. } else { -1. }
            * if entering { 1. } else { -1. };
        let tops = if (at[1][1] - at[0][1]).abs() > EPS && toward > EPS {
            vec![above]
        } else {
            let preferred = usize::from(at[1][2] > at[0][2]);
            vec![preferred, 1 - preferred]
        };
        out.push(Pass {
            sample: i,
            frame: i as f64 - 1. + u,
            entering,
            tops,
        });
    }
    out
}
fn locked_overpass(left: &[Point], right: &[Point], lock: &[bool], start: f64, end: f64) -> bool {
    let limit = (2. * TIP - EPS).powi(2);
    let relative = |i: usize| [left[i][0] - right[i][0], left[i][1] - right[i][1]];
    for i in start.ceil() as usize..=end.floor() as usize {
        let r = relative(i);
        if lock[i] && r[0] * r[0] + r[1] * r[1] < limit {
            return true;
        }
    }
    for i in start.floor() as usize..end.ceil() as usize {
        if !(lock[i] && lock[i + 1]) {
            continue;
        }
        let a = relative(i);
        let b = relative(i + 1);
        let d = [b[0] - a[0], b[1] - a[1]];
        let u = (-(a[0] * d[0] + a[1] * d[1]) / (d[0] * d[0] + d[1] * d[1]).max(1e-16))
            .clamp((start - i as f64).max(0.), (end - i as f64).min(1.));
        if (a[0] + u * d[0]).powi(2) + (a[1] + u * d[1]).powi(2) < limit {
            return true;
        }
    }
    false
}
fn extend(
    routes: Vec<Crossing>,
    left: &[Point],
    right: &[Point],
    start: usize,
    locks: [&[bool]; 2],
) -> Vec<Vec<Crossing>> {
    let events = passes(left, right, start, order(&routes).is_some());
    let mut options = vec![routes];
    let mut cursor = start as f64;
    for event in events {
        if event.entering {
            options = options
                .into_iter()
                .flat_map(|history| {
                    event.tops.iter().map(move |&top| {
                        let mut h = history.clone();
                        h.push(Crossing {
                            start_frame: event.frame,
                            end_frame: None,
                            top,
                            entered_at: event.sample as i32,
                            exited_at: None,
                        });
                        h
                    })
                })
                .collect();
        } else {
            let mut next = vec![];
            let mut seen = HashSet::new();
            for mut history in options {
                if let Some(top) = order(&history) {
                    if event.tops.contains(&top)
                        && !locked_overpass(left, right, locks[top], cursor, event.frame)
                        && seen.insert(top)
                    {
                        let last = history.last_mut().unwrap();
                        last.end_frame = Some(event.frame);
                        last.exited_at = Some(event.sample as i32);
                        next.push(history);
                    }
                }
            }
            options = next;
            if options.is_empty() {
                break;
            }
        }
        cursor = event.frame;
    }
    options
        .into_iter()
        .filter(|h| {
            order(h).is_none_or(|top| {
                !locked_overpass(left, right, locks[top], cursor, (left.len() - 1) as f64)
            })
        })
        .collect()
}
fn curves(
    direct: &[Point],
    other: &[Point],
    lock: &[bool],
    swap: f64,
    speed: f64,
    hand: usize,
    entering: bool,
) -> Vec<Vec<Point>> {
    let last = (direct.len() - 1) as f64;
    let a = direct[0];
    let b = direct[direct.len() - 1];
    let line: Vec<Point> = (0..direct.len())
        .map(|i| std::array::from_fn(|j| a[j] + (b[j] - a[j]) * (i as f64 / last)))
        .collect();
    let mut bases: Vec<Vec<Point>> = [0., 0.5, 1.]
        .iter()
        .map(|&blend| {
            direct
                .iter()
                .zip(&line)
                .map(|(p, q)| std::array::from_fn(|j| p[j] + blend * (q[j] - p[j])))
                .collect()
        })
        .collect();
    for mode in 0..3 {
        let mut base = line.clone();
        for (i, p) in base.iter_mut().enumerate() {
            let t = i as f64 / last;
            let u = match mode {
                0 => t.powf(1.5),
                1 => t * t,
                _ => 1. - (1. - t).powf(1.5),
            };
            p[0] = a[0] + (b[0] - a[0]) * u;
        }
        bases.push(base);
    }
    let mut best: [Option<((f64, f64), Vec<Point>)>; 2] = [None, None];
    for base in bases {
        let d: Vec<f64> = base
            .iter()
            .zip(other)
            .map(|(a, b)| (a[0] - b[0]) * if hand == 0 { -1. } else { 1. })
            .collect();
        let edges: Vec<usize> = (0..d.len() - 1)
            .filter(|&i| (d[i + 1] < 0.) != (d[i] < 0.))
            .collect();
        let Some(&edge) = edges
            .iter()
            .filter(|&&i| (d[i + 1] < 0.) == entering)
            .min_by(|&&i, &&j| {
                ((i as f64 + 0.5 - swap).abs()).total_cmp(&(j as f64 + 0.5 - swap).abs())
            })
        else {
            continue;
        };
        let crossing_at = |i: usize| i as f64 - d[i] / (d[i + 1] - d[i]);
        let crossing = crossing_at(edge);
        if crossing <= 0. || crossing >= last {
            continue;
        }
        let fraction = crossing - edge as f64;
        let (start, end) = if entering {
            (
                crossing,
                edges
                    .iter()
                    .find(|&&i| i > edge)
                    .map_or((other.len() - 1) as f64, |&i| crossing_at(i)),
            )
        } else {
            (
                edges
                    .iter()
                    .rev()
                    .find(|&&i| i < edge)
                    .map_or(0., |&i| crossing_at(i)),
                crossing,
            )
        };
        let separation = (base[edge][1] - other[edge][1]) * (1. - fraction)
            + (base[edge + 1][1] - other[edge + 1][1]) * fraction;
        for power in [1.35, 1.08] {
            let bump: Vec<f64> = (0..direct.len())
                .map(|i| {
                    1. - (1.
                        - (i as f64 / crossing)
                            .min((last - i as f64) / (last - crossing))
                            .clamp(0., 1.))
                    .powf(power)
                })
                .collect();
            let height = bump[edge] * (1. - fraction) + bump[edge + 1] * fraction;
            if height <= EPS {
                continue;
            }
            for (width, clearances) in [&[0.010, 0.006, 0.002][..], &[0.020, 0.014][..]]
                .iter()
                .enumerate()
            {
                for &clearance in *clearances {
                    let depth = (separation + clearance) / height;
                    if depth <= 0. {
                        continue;
                    }
                    let mut path = base.clone();
                    for (p, &bump) in path.iter_mut().zip(&bump) {
                        p[1] -= depth * bump;
                    }
                    path[0] = a;
                    *path.last_mut().unwrap() = b;
                    let lengths = lengths(&path);
                    if lengths.iter().copied().fold(0., f64::max) * RATE > speed + 0.0001
                        || locked_overpass(&path[..other.len()], other, lock, start, end)
                    {
                        continue;
                    }
                    let rank = (-clearance, crate::trajectory::pairwise(&lengths));
                    if best[width].as_ref().is_none_or(|(r, _)| rank < *r) {
                        best[width] = Some((rank, path));
                    }
                    break;
                }
            }
        }
    }
    best.into_iter().flatten().map(|(_, p)| p).collect()
}
impl ArmState {
    pub fn through(&self) -> usize {
        self.paths.iter().map(|p| p.points.len()).min().unwrap() - 1
    }
    pub fn seconds(&self) -> f64 {
        let through = self.through();
        if through == 0 {
            return 0.;
        }
        let mut frames = 0.;
        for r in &self.routes {
            let first = r.start_frame.ceil() as usize;
            let stop = r
                .end_frame
                .map_or(through + 1, |e| (through + 1).min(e.ceil() as usize));
            if first < stop {
                frames += (stop - first) as f64
                    - 0.5 * (usize::from(first == 0) + usize::from(stop == through + 1)) as f64;
            }
        }
        frames / RATE
    }
    pub fn extra(&self) -> f64 {
        crate::numeric::py_sum(
            self.paths
                .iter()
                .flat_map(|p| p.travel.iter().map(|g| g.extra_distance_m)),
        )
    }
    pub fn key(&self) -> Vec<u64> {
        let anchor = self.paths.iter().map(|p| p.anchor).min().unwrap();
        let mut key = vec![order(&self.routes).map_or(2, |h| h) as u64];
        for r in self
            .routes
            .iter()
            .filter(|r| r.exited_at.is_none_or(|f| f > anchor as i32))
        {
            key.extend([
                bits(r.start_frame),
                r.end_frame.map_or(u64::MAX, bits),
                r.top as u64,
                r.entered_at as u64,
                r.exited_at.map_or(u64::MAX, |f| f as u64),
            ]);
        }
        key.push(u64::MAX - 1);
        for p in &self.paths {
            key.extend([p.anchor as u64, p.points.len() as u64]);
            for point in &p.points[anchor..] {
                key.extend(point.iter().map(|x| x.to_bits()));
            }
            key.extend(p.locked[anchor..].iter().map(|&v| u64::from(v)));
        }
        key
    }
}
impl CrossingGraph {
    pub fn new(lift: f64, speed: f64, idle: f64, requested: usize) -> Self {
        Self {
            projections: Mutex::new(ProjectionCache::default()),
            lift,
            speed,
            idle: idle.min(speed),
            requested,
            homes: [[-0.085, -0.068, TIP + lift], [0.085, -0.068, TIP + lift]],
        }
    }
    pub fn initial(&self) -> ArmState {
        ArmState {
            paths: self.homes.map(|home| {
                Arc::new(HandPath {
                    points: vec![home],
                    anchor: 0,
                    locked: vec![false],
                    travel: vec![],
                })
            }),
            routes: vec![],
        }
    }
    fn project(
        &self,
        previous: &Arc<HandPath>,
        touch: &Touch,
        new: bool,
        hand: usize,
    ) -> Option<Arc<HandPath>> {
        let key = (Arc::as_ptr(previous) as usize, touch.uid, new, hand);
        if let Some((_, value)) = self.projections.lock().unwrap().values.get(&key) {
            return value.clone();
        }
        let value = self.project_uncached(previous, touch, new, hand);
        let mut cache = self.projections.lock().unwrap();
        if !cache.values.contains_key(&key) {
            if cache.values.len() == 512 {
                let oldest = cache.order.pop_front().unwrap();
                cache.values.remove(&oldest);
            }
            cache.order.push_back(key);
            cache.values.insert(key, (previous.clone(), value.clone()));
        }
        value
    }
    fn project_uncached(
        &self,
        previous: &HandPath,
        touch: &Touch,
        new: bool,
        hand: usize,
    ) -> Option<Arc<HandPath>> {
        let anchor = if new {
            previous.points.len() - 1
        } else {
            previous.anchor
        };
        let down = touch.down.max(0) as usize;
        if down < anchor {
            return None;
        }
        let points: Vec<Point> = touch.path[(down as i32 - touch.down) as usize..]
            .iter()
            .map(|p| {
                [
                    (p[0] * 1000. * 100.).round_ties_even() / 100. / 1000.,
                    (p[1] * 1000. * 100.).round_ties_even() / 100. / 1000.,
                    TIP,
                ]
            })
            .collect();
        let mut travel: Vec<_> = previous
            .travel
            .iter()
            .filter(|g| g.end <= anchor)
            .cloned()
            .collect();
        let retained = previous.travel.iter().find(|g| {
            g.start == anchor && g.end == down && g.curved && previous.points[down] == points[0]
        });
        let gap = if retained.is_some() {
            previous.points[anchor..=down].to_vec()
        } else {
            span(
                previous.points[anchor],
                points[0],
                down - anchor,
                self.homes[hand],
                self.lift,
                self.speed,
                self.idle,
            )
        };
        if down - anchor >= 2 && previous.locked[anchor] {
            travel.push(retained.cloned().unwrap_or(FreeTravel {
                start: anchor,
                end: down,
                extra_distance_m: 0.,
                curved: false,
            }));
        }
        let mut locked = previous.locked[..anchor].to_vec();
        locked.extend(std::iter::repeat_n(false, gap.len() - 1));
        locked.extend(std::iter::repeat_n(true, points.len()));
        if down > anchor {
            locked[anchor] = previous.locked[anchor];
        }
        let mut joined = previous.points[..anchor].to_vec();
        joined.extend_from_slice(&gap[..gap.len() - 1]);
        joined.extend(points);
        Some(Arc::new(HandPath {
            points: joined,
            anchor,
            locked,
            travel,
        }))
    }
    fn checkpoint(
        previous: &ArmState,
        paths: &[Arc<HandPath>; 2],
        changed_from: [usize; 2],
    ) -> (i32, usize) {
        let through = paths.iter().map(|p| p.points.len()).min().unwrap() - 1;
        let shared = previous.through().min(through);
        let mut first = None;
        for (hand, (old, new)) in previous.paths.iter().zip(paths).enumerate() {
            if Arc::ptr_eq(old, new) {
                continue;
            }
            debug_assert!(
                (0..changed_from[hand].min(shared + 1))
                    .all(|i| old.points[i][..2] == new.points[i][..2]
                        && old.locked[i] == new.locked[i])
            );
            if changed_from[hand] > shared {
                continue;
            }
            if let Some(i) = (changed_from[hand]..=shared).find(|&i| {
                old.points[i][..2] != new.points[i][..2] || old.locked[i] != new.locked[i]
            }) {
                first = Some(first.map_or(i, |f: usize| f.min(i)));
            }
        }
        (first.map_or(shared as i32, |i| i as i32 - 1), through)
    }
    fn advance(
        &self,
        previous: &ArmState,
        paths: &[Arc<HandPath>; 2],
        (fixed, through): (i32, usize),
    ) -> Vec<ArmState> {
        let histories = if fixed < 0 {
            let points: [Vec<Point>; 2] = std::array::from_fn(|h| {
                let mut p = vec![self.homes[h]];
                p.extend_from_slice(&paths[h].points[..=through]);
                p
            });
            let locks: [Vec<bool>; 2] = std::array::from_fn(|h| {
                let mut p = vec![false];
                p.extend_from_slice(&paths[h].locked[..=through]);
                p
            });
            extend(vec![], &points[0], &points[1], 0, [&locks[0], &locks[1]])
                .into_iter()
                .map(|mut history| {
                    for c in &mut history {
                        c.start_frame = (c.start_frame - 1.).max(0.);
                        c.end_frame = c.end_frame.map(|f| f - 1.);
                        c.entered_at -= 1;
                        c.exited_at = c.exited_at.map(|f| f - 1);
                    }
                    history
                })
                .collect()
        } else {
            extend(
                prefix(&previous.routes, fixed),
                &paths[0].points[..=through],
                &paths[1].points[..=through],
                fixed as usize,
                [&paths[0].locked[..=through], &paths[1].locked[..=through]],
            )
        };
        histories
            .into_iter()
            .map(|routes| ArmState {
                paths: paths.clone(),
                routes,
            })
            .collect()
    }
    fn alternatives(
        &self,
        previous: &ArmState,
        paths: &[Arc<HandPath>; 2],
        (fixed, through): (i32, usize),
    ) -> Vec<([Arc<HandPath>; 2], usize, usize)> {
        let start = fixed.max(0) as usize;
        let crossed = if fixed < 0 {
            paths[0].points[0][0] > paths[1].points[0][0]
        } else {
            order(&prefix(&previous.routes, fixed)).is_some()
        };
        let mut tried = HashSet::new();
        let mut result = vec![];
        for event in passes(
            &paths[0].points[..=through],
            &paths[1].points[..=through],
            start,
            crossed,
        ) {
            if event.tops.len() != 1 {
                continue;
            }
            let h = event.tops[0];
            let path = &paths[h];
            let other = &paths[1 - h];
            for gap in path.travel.iter().rev() {
                let (a, b) = (gap.start, gap.end);
                if a as f64 >= event.frame {
                    continue;
                }
                if b as f64 <= event.frame {
                    break;
                }
                if !tried.insert((h, a, b))
                    || gap.curved
                    || path.locked[a + 1..b].iter().any(|&v| v)
                {
                    break;
                }
                let direct = &path.points[a..=b];
                if direct.contains(&self.homes[h]) {
                    break;
                }
                let stop = b.min(through) + 1;
                let distance = total_length(direct);
                for curved in curves(
                    direct,
                    &other.points[a..stop],
                    &other.locked[a..stop],
                    event.frame - a as f64,
                    self.speed,
                    h,
                    event.entering,
                ) {
                    let mut new = (**path).clone();
                    new.points[a..=b].copy_from_slice(&curved);
                    for g in &mut new.travel {
                        if g == gap {
                            g.extra_distance_m = (total_length(&curved) - distance).max(0.);
                            g.curved = true;
                        }
                    }
                    let mut selected = paths.clone();
                    selected[h] = Arc::new(new);
                    result.push((selected, h, a));
                }
                break;
            }
        }
        result
    }
    fn routes(
        &self,
        previous: &ArmState,
        paths: [Arc<HandPath>; 2],
        changed_from: [usize; 2],
    ) -> Vec<ArmState> {
        // Contact projection and detours only edit suffixes. Reuse their first
        // possible edit frame to avoid scanning the entire previously played chart.
        let mut pending = vec![(paths, changed_from)];
        let mut seen = HashSet::from([vec![]]);
        let mut result = vec![];
        let mut i = 0;
        while i < pending.len() {
            let (paths, changed_from) = pending[i].clone();
            i += 1;
            let checkpoint = Self::checkpoint(previous, &paths, changed_from);
            result.extend(self.advance(previous, &paths, checkpoint));
            if pending.len() >= 8 {
                continue;
            }
            for (alternative, hand, start) in self.alternatives(previous, &paths, checkpoint) {
                let mut key = vec![];
                for (h, p) in alternative.iter().enumerate() {
                    for g in p.travel.iter().filter(|g| g.curved) {
                        key.extend([h as u64, g.start as u64, (g.end - g.start + 1) as u64]);
                        for point in &p.points[g.start..=g.end] {
                            key.extend(point.iter().map(|x| x.to_bits()));
                        }
                    }
                }
                if seen.insert(key) {
                    let mut changed = changed_from;
                    changed[hand] = changed[hand].min(start);
                    pending.push((alternative, changed));
                    if pending.len() == 8 {
                        break;
                    }
                }
            }
        }
        result
    }
    pub fn step(
        &self,
        previous: &ArmState,
        touch: &Touch,
        hand: usize,
        extension: bool,
    ) -> Vec<ArmState> {
        let mut paths = previous.paths.clone();
        let mut changed_from = [usize::MAX; 2];
        if touch.up() > 0 {
            let Some(path) = self.project(&paths[hand], touch, !extension, hand) else {
                return vec![];
            };
            changed_from[hand] = path.anchor;
            paths[hand] = path;
        }
        self.routes(previous, paths, changed_from)
    }
    // Validate only the pending asymmetric tail. Padding both stationary
    // endpoints through the rest of the chart cannot introduce another pass.
    pub fn can_stop_in_place(state: &ArmState) -> bool {
        let through = state.through();
        let end = state.paths.iter().map(|p| p.points.len()).max().unwrap();
        if through + 1 == end {
            return true;
        }
        let points: [Vec<Point>; 2] = std::array::from_fn(|h| {
            let mut tail = state.paths[h].points[through..].to_vec();
            tail.resize(end - through, *tail.last().unwrap());
            tail
        });
        let locks: [Vec<bool>; 2] = std::array::from_fn(|h| {
            let mut tail = state.paths[h].locked[through..].to_vec();
            tail.resize(end - through, false);
            tail
        });
        // Extend needs the active layer; completed routes have no effect.
        let active = state
            .routes
            .last()
            .filter(|route| route.end_frame.is_none())
            .cloned()
            .into_iter()
            .collect();
        !extend(active, &points[0], &points[1], 0, [&locks[0], &locks[1]]).is_empty()
    }

    pub fn finish(&self, state: &ArmState) -> Vec<ArmState> {
        let frame = self
            .requested
            .max(state.paths.iter().map(|p| p.points.len()).max().unwrap());
        let paths = state.paths.clone().map(|p| {
            let mut path = (*p).clone();
            let last = *path.points.last().unwrap();
            path.points.resize(frame + 1, last);
            path.locked.resize(frame + 1, false);
            Arc::new(path)
        });
        self.routes(state, paths, [usize::MAX; 2])
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::Touch;
    use std::collections::BTreeMap;
    use std::sync::OnceLock;

    #[test]
    fn python_near_side_routes_and_finalization() {
        let cases: serde_json::Value =
            serde_json::from_str(include_str!("../tests/arm-route-reference.json")).unwrap();
        let graph = CrossingGraph::new(0.012, 1.5, 1., 34);
        for (index, case) in cases.as_array().unwrap().iter().enumerate() {
            let before: ArmState = serde_json::from_value(case["before"].clone()).unwrap();
            let actual = if case["finish"].as_bool() == Some(true) {
                graph.finish(&before)
            } else {
                let touch = Touch {
                    uid: index,
                    parts: vec![],
                    bridges: vec![],
                    down: case["down"].as_i64().unwrap() as i32,
                    path: serde_json::from_value(case["path"].clone()).unwrap(),
                    over: 0.,
                    arrival: None,
                    bindings: BTreeMap::new(),
                    measures: OnceLock::new(),
                    trace_jobs: vec![],
                    prefix: OnceLock::new(),
                    arrival_base: None,
                    arrival_initialized: OnceLock::new(),
                    inherited_measures: OnceLock::new(),
                };
                graph.step(
                    &before,
                    &touch,
                    case["hand"].as_u64().unwrap() as usize,
                    false,
                )
            };
            let expected: Vec<ArmState> = serde_json::from_value(case["expected"].clone()).unwrap();
            assert_eq!(actual.len(), expected.len(), "case {index}");
            for (a, b) in actual.iter().zip(&expected) {
                assert!(
                    (a.seconds() - b.seconds()).abs() < 1e-12,
                    "crossing cost {index}"
                );
                assert!(
                    (a.extra() - b.extra()).abs() < 1e-12,
                    "distance cost {index}"
                );
                assert_eq!(a.routes.len(), b.routes.len(), "routes {index}");
                for (x, y) in a.routes.iter().zip(&b.routes) {
                    assert_eq!(
                        (x.top, x.entered_at, x.exited_at),
                        (y.top, y.entered_at, y.exited_at)
                    );
                    assert!((x.start_frame - y.start_frame).abs() < 1e-12);
                }
                for (x, y) in a.paths.iter().zip(&b.paths) {
                    assert_eq!(x.locked, y.locked);
                    assert_eq!(x.anchor, y.anchor);
                    assert_eq!(x.points.len(), y.points.len());
                    for (p, q) in x.points.iter().zip(&y.points) {
                        for j in 0..3 {
                            assert!((p[j] - q[j]).abs() < 1e-12, "geometry {index}: {p:?} {q:?}");
                        }
                    }
                }
            }
        }
    }
}
