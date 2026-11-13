//! Static wrist catalogue estimate. Input arrays are prepared by the Python
//! reference, so native evaluation never rebuilds glove geometry or changes
//! its 5 mm quantization. No Python objects are used by worker threads.
use serde::Deserialize;
use std::collections::HashMap;
use std::sync::Mutex;

pub type XY = [f64; 2];

#[derive(Deserialize)]
pub struct WristInput {
    pub poses: Vec<[XY; 2]>,
    pub centres: [Vec<Vec<[f64; 3]>>; 2],
    pub radius: Vec<Vec<f64>>,
    pub floor: Vec<f64>,
    pub free_deg: f64,
}

struct ProxyPose {
    deltas: Vec<([f64; 3], f64)>,
    floor_penalty: f64,
}

pub struct WristEstimate {
    poses: Vec<[XY; 2]>,
    proxies: Vec<ProxyPose>,
    free_deg: f64,
    cache: Mutex<HashMap<[i64; 4], f64>>,
}

impl WristEstimate {
    pub fn new(input: WristInput) -> Result<Self, String> {
        let n = input.poses.len();
        if n == 0
            || input.floor.len() != n
            || input.centres.iter().any(|hand| hand.len() != n)
            || !input.free_deg.is_finite()
            || input.free_deg < 0.0
        {
            return Err("invalid wrist catalogue shape or free angle".into());
        }
        let left_count = input.centres[0][0].len();
        let right_count = input.centres[1][0].len();
        if left_count == 0
            || right_count == 0
            || input.radius.len() != left_count
            || input.radius.iter().any(|row| row.len() != right_count)
            || input.centres[0].iter().any(|pose| pose.len() != left_count)
            || input.centres[1]
                .iter()
                .any(|pose| pose.len() != right_count)
        {
            return Err("invalid wrist proxy geometry shape".into());
        }
        let finite = input
            .poses
            .iter()
            .flatten()
            .flatten()
            .chain(input.floor.iter())
            .chain(input.radius.iter().flatten())
            .chain(input.centres.iter().flatten().flatten().flatten())
            .all(|x| x.is_finite());
        if !finite || input.radius.iter().flatten().any(|r| *r < 0.0) {
            return Err("nonfinite wrist geometry or negative radius".into());
        }
        let proxies = (0..n)
            .map(|i| {
                let mut deltas = Vec::with_capacity(left_count * right_count);
                for (a, left) in input.centres[0][i].iter().enumerate() {
                    for (b, right) in input.centres[1][i].iter().enumerate() {
                        deltas.push((
                            [left[0] - right[0], left[1] - right[1], left[2] - right[2]],
                            input.radius[a][b],
                        ));
                    }
                }
                let floor = (-input.floor[i] - 0.00005).max(0.0);
                ProxyPose {
                    deltas,
                    floor_penalty: 4.0 * (floor * floor),
                }
            })
            .collect();
        Ok(Self {
            poses: input.poses,
            proxies,
            free_deg: input.free_deg,
            cache: Mutex::new(HashMap::new()),
        })
    }

    pub fn frame(&self, left: XY, right: XY) -> f64 {
        let key =
            [left[0], left[1], right[0], right[1]].map(|x| (x / 0.005).round_ties_even() as i64);
        if let Some(value) = self.cache.lock().unwrap().get(&key).copied() {
            return value;
        }
        let q = [
            [key[0] as f64 * 0.005, key[1] as f64 * 0.005],
            [key[2] as f64 * 0.005, key[3] as f64 * 0.005],
        ];
        let delta = [q[0][0] - q[1][0], q[0][1] - q[1][1]];
        let mut overlaps = Vec::with_capacity(self.proxies.len());
        let mut minimum = f64::INFINITY;
        for proxy in &self.proxies {
            let mut clearance = f64::INFINITY;
            for (d, radius) in &proxy.deltas {
                let x = d[0] + delta[0];
                let y = d[1] + delta[1];
                let distance = (x * x + y * y + d[2] * d[2]).sqrt() - radius;
                clearance = clearance.min(distance);
            }
            let penetration = (0.001 - clearance).max(0.0);
            let overlap = penetration * penetration + proxy.floor_penalty;
            minimum = minimum.min(overlap);
            overlaps.push(overlap);
        }
        let natural = natural_angles(q);
        let mut result = f64::INFINITY;
        for (pose, overlap) in self.poses.iter().zip(overlaps) {
            if overlap <= minimum + 1e-12 {
                let mut strain = 0.0;
                for hand in 0..2 {
                    let pitch = pose[hand][0] - natural[hand][0];
                    let yaw = (pose[hand][1] - natural[hand][1] + 180.0).rem_euclid(360.0) - 180.0;
                    for angle in [pitch, yaw] {
                        let value = (angle.abs() - self.free_deg).max(0.0) / 90.0;
                        strain += value * value;
                    }
                }
                result = result.min(strain);
            }
        }
        let mut cache = self.cache.lock().unwrap();
        // Capacity affects reuse only; every miss uses the same calculation.
        if cache.len() >= 65536 {
            cache.clear();
        }
        cache.insert(key, result);
        result
    }
}

fn natural_angles(points: [XY; 2]) -> [XY; 2] {
    const SHOULDERS: [XY; 2] = [[-0.18, -0.38], [0.18, -0.38]];
    const HOME: [XY; 2] = [[-0.085, -0.068], [0.085, -0.068]];
    const YAW: [f64; 2] = [-18.0, 18.0];
    std::array::from_fn(|h| {
        let ray = [
            SHOULDERS[h][0] - points[h][0],
            SHOULDERS[h][1] - points[h][1],
        ];
        let home = [SHOULDERS[h][0] - HOME[h][0], SHOULDERS[h][1] - HOME[h][1]];
        [
            10.0,
            YAW[h] + ray[0].atan2(-ray[1]).to_degrees() - home[0].atan2(-home[1]).to_degrees(),
        ]
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture() -> WristInput {
        WristInput {
            poses: vec![[[10.0, -18.0], [10.0, 18.0]]],
            centres: [vec![vec![[0.0; 3]]], vec![vec![[0.0; 3]]]],
            radius: vec![vec![0.01]],
            floor: vec![0.0],
            free_deg: 30.0,
        }
    }

    #[test]
    fn home_pose_is_free_and_quantization_uses_even_ties() {
        let estimate = WristEstimate::new(fixture()).unwrap();
        assert_eq!(estimate.frame([-0.085, -0.068], [0.085, -0.068]), 0.0);
        estimate.frame([0.0025, -0.0025], [0.0075, -0.0075]);
        assert!(estimate.cache.lock().unwrap().contains_key(&[0, 0, 2, -2]));
    }

    #[test]
    fn checks_array_shapes_before_evaluation() {
        let mut input = fixture();
        input.radius.clear();
        assert!(WristEstimate::new(input).is_err());
    }

    #[test]
    fn cache_is_safe_for_parallel_queries() {
        let estimate = WristEstimate::new(fixture()).unwrap();
        std::thread::scope(|s| {
            for _ in 0..4 {
                s.spawn(|| {
                    for _ in 0..20 {
                        assert_eq!(estimate.frame([-0.085, -0.068], [0.085, -0.068]), 0.0);
                    }
                });
            }
        });
        assert_eq!(estimate.cache.lock().unwrap().len(), 1);
    }
}
