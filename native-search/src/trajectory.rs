//! Match the reference's NumPy reduction and flight sampling order.
//! Pairwise reduction follows NumPy loops_utils.h.src (LICENSE-NUMPY).
use crate::model::{Arrival, XY};
use crate::numeric::{dist, pow};

pub fn pairwise(values: &[f64]) -> f64 {
    let n = values.len();
    if n < 8 {
        return values.iter().fold(-0.0, |sum, value| sum + value);
    }
    if n <= 128 {
        let mut accumulators: [f64; 8] = values[..8].try_into().unwrap();
        let stop = n - n % 8;
        for chunk in values[8..stop].chunks_exact(8) {
            for j in 0..8 {
                accumulators[j] += chunk[j];
            }
        }
        let a = accumulators;
        let sum = ((a[0] + a[1]) + (a[2] + a[3])) + ((a[4] + a[5]) + (a[6] + a[7]));
        return values[stop..].iter().fold(sum, |sum, value| sum + value);
    }
    let half = (n / 2) / 8 * 8;
    pairwise(&values[..half]) + pairwise(&values[half..])
}

pub fn energy(path: &[XY], rest: bool) -> f64 {
    if path.len() < 2 {
        return 0.;
    }
    let mut points = Vec::with_capacity(path.len() + usize::from(rest) * 2);
    if rest {
        points.push(path[0]);
    }
    points.extend_from_slice(path);
    if rest {
        points.push(*path.last().unwrap());
    }
    let squared: Vec<f64> = points
        .windows(3)
        .flat_map(|w| {
            std::array::from_fn::<_, 2, _>(|j| {
                let value = ((w[2][j] - w[1][j]) - (w[1][j] - w[0][j])) * 3600.;
                value * value
            })
        })
        .collect();
    // NumPy's add reduction begins with the additive identity.
    (0. + pairwise(&squared)) / 1500.
}

fn terminal(path: &[XY]) -> f64 {
    pow(dist(path[path.len() - 1], path[path.len() - 2]), 2.) * 216000. / 25.
}

pub fn held(path: &[XY], prefix: Option<(&[XY], f64)>) -> f64 {
    if let Some((old_path, old_energy)) = prefix.filter(|(p, _)| p.len() >= 2) {
        old_energy - terminal(old_path)
            + energy(&path[old_path.len() - 2..], false)
            + terminal(path)
    } else {
        energy(path, true)
    }
}

pub fn transfer(arrival: &Arrival, down: i32, path: &[XY], speed_max: f64) -> f64 {
    let frames = down - (arrival.up - 1);
    if frames <= 0 {
        return 0.;
    }
    let previous = *arrival.path.last().unwrap();
    let current = path[0];
    // np.linspace(0, 1, frames+1): multiply by its rounded step, then
    // overwrite the endpoint. Division for every sample is not equivalent.
    let step = 1. / frames as f64;
    let samples: Vec<_> = (0..=frames)
        .map(|i| {
            let t = if i == frames { 1. } else { i as f64 * step };
            (t, pow(t, 3.) * (10. - 15. * t + 6. * t * t))
        })
        .collect();
    let mut flight = vec![];
    for linear in [0., 0.25, 0.5, 0.75, 1.] {
        flight = samples
            .iter()
            .map(|&(t, eased)| {
                let u = (1. - linear) * eased + linear * t;
                [
                    previous[0] + u * (current[0] - previous[0]),
                    previous[1] + u * (current[1] - previous[1]),
                ]
            })
            .collect();
        let peak = flight
            .windows(2)
            .map(|w| {
                let dx = w[1][0] - w[0][0];
                let dy = w[1][1] - w[0][1];
                (dx * dx + dy * dy).sqrt()
            })
            .fold(0., f64::max);
        if peak * 60. <= speed_max + 1e-4 {
            break;
        }
    }
    let before = arrival.path[arrival.path.len().saturating_sub(2)];
    let after = path[usize::from(path.len() > 1)];
    let rests = (pow(dist(before, previous), 2.) + pow(dist(current, after), 2.)) * 216000. / 25.;
    flight.insert(0, before);
    flight.push(after);
    energy(&flight, false) - rests
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn numpy_reference_bits() {
        let values: serde_json::Value =
            serde_json::from_str(include_str!("../tests/trajectory_reference.json")).unwrap();
        for row in values["energies"].as_array().unwrap() {
            let path: Vec<XY> = serde_json::from_value(row["path"].clone()).unwrap();
            assert_eq!(
                energy(&path, row["rest"].as_bool().unwrap()).to_bits(),
                row["bits"].as_u64().unwrap()
            );
        }
        for row in values["prefixes"].as_array().unwrap() {
            let path: Vec<XY> = serde_json::from_value(row["path"].clone()).unwrap();
            let old = row["old"].as_u64().unwrap() as usize;
            assert_eq!(
                held(&path, Some((&path[..old], row["energy"].as_f64().unwrap()))).to_bits(),
                row["bits"].as_u64().unwrap()
            );
        }
        for row in values["flights"].as_array().unwrap() {
            let path: Vec<XY> = serde_json::from_value(row["path"].clone()).unwrap();
            let arrival = Arrival {
                up: row["up"].as_i64().unwrap() as i32,
                path: serde_json::from_value(row["previous"].clone()).unwrap(),
            };
            assert_eq!(
                transfer(
                    &arrival,
                    row["down"].as_i64().unwrap() as i32,
                    &path,
                    row["speed"].as_f64().unwrap()
                )
                .to_bits(),
                row["bits"].as_u64().unwrap()
            );
        }
    }
}
