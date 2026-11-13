//! CPython 3.13 numeric compatibility for beam-ranking ties.
//!
//! Adapted from CPython Modules/mathmodule.c (vector_norm/dl_mul/dl_fast_sum)
//! and Python/bltinmodule.c (float sum), Python Software Foundation License;
//! see LICENSE-PSF. No Python runtime or objects are accessed here.

/// Neumaier summation used by Python's built-in sum for floating-point inputs.
/// Do not substitute this for explicit Python += loops: their order is semantic.
pub fn py_sum(values: impl IntoIterator<Item = f64>) -> f64 {
    let mut iter = values.into_iter();
    let Some(mut total) = iter.next() else {
        return 0.;
    };
    // Python's default start is integer zero; the first float is added to it.
    total = 0.0 + total;
    let mut correction = 0.0;
    for value in iter {
        let next = total + value;
        correction += if total.abs() >= value.abs() {
            (total - next) + value
        } else {
            (value - next) + total
        };
        total = next;
    }
    if correction != 0.0 && correction.is_finite() {
        total + correction
    } else {
        total
    }
}

#[cfg_attr(not(target_os = "windows"), link(name = "m"))]
extern "C" {
    #[link_name = "pow"]
    fn system_pow(base: f64, exponent: f64) -> f64;
}

/// Python float ** dispatches to libm pow, which can differ from x*x by an ulp.
pub fn pow(base: f64, exponent: f64) -> f64 {
    // Safety: the platform C math function takes/returns plain scalar doubles.
    // Keep the exponent dynamic so LLVM cannot replace pow(x, 2) with x*x.
    unsafe { system_pow(base, std::hint::black_box(exponent)) }
}

fn power_of_two(exponent: i32) -> f64 {
    if exponent >= -1022 {
        f64::from_bits(((exponent + 1023) as u64) << 52)
    } else {
        f64::from_bits(1u64 << (exponent + 1074))
    }
}

/// Faithfully rounded 2D norm with the same operation order as math.hypot.
pub fn hypot(x: f64, y: f64) -> f64 {
    let mut values = [x.abs(), y.abs()];
    let max = values[0].max(values[1]);
    if max.is_infinite() {
        return f64::INFINITY;
    }
    if values.iter().any(|x| x.is_nan()) {
        return f64::NAN;
    }
    if max == 0. {
        return max;
    }
    let bits = max.to_bits();
    let encoded = ((bits >> 52) & 0x7ff) as i32;
    let exponent = if encoded != 0 {
        encoded - 1022
    } else {
        63 - bits.leading_zeros() as i32 - 1073
    };
    if exponent < -1023 {
        return f64::MIN_POSITIVE
            * hypot(values[0] / f64::MIN_POSITIVE, values[1] / f64::MIN_POSITIVE);
    }
    let scale = power_of_two(-exponent);
    let mut total = 1.;
    let mut product_error = 0.;
    let mut sum_error = 0.;
    for value in &mut values {
        *value *= scale;
        let square = *value * *value;
        let residual = value.mul_add(*value, -square);
        let next = total + square;
        sum_error += (total - next) + square;
        product_error += residual;
        total = next;
    }
    let mut root = (total - 1.0 + (product_error + sum_error)).sqrt();
    let square = -root * root;
    let residual = (-root).mul_add(root, -square);
    let next = total + square;
    sum_error += (total - next) + square;
    product_error += residual;
    total = next;
    let remainder = total - 1.0 + (product_error + sum_error);
    root += remainder / (2.0 * root);
    root / scale
}

pub fn dist(a: [f64; 2], b: [f64; 2]) -> f64 {
    hypot(a[0] - b[0], a[1] - b[1])
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn interpreter_bit_exact_reference() {
        let v: serde_json::Value =
            serde_json::from_str(include_str!("../tests/numeric_reference.json")).unwrap();
        for row in v["norms"].as_array().unwrap() {
            let numbers: Vec<f64> = row["input"]
                .as_array()
                .unwrap()
                .iter()
                .map(|v| f64::from_bits(u64::from_str_radix(v.as_str().unwrap(), 16).unwrap()))
                .collect();
            let expected = u64::from_str_radix(row["output"].as_str().unwrap(), 16).unwrap();
            assert_eq!(
                hypot(numbers[0], numbers[1]).to_bits(),
                expected,
                "{:?}",
                numbers
            );
        }
        for row in v["powers"].as_array().unwrap() {
            let numbers: Vec<f64> = row["input"]
                .as_array()
                .unwrap()
                .iter()
                .map(|v| f64::from_bits(u64::from_str_radix(v.as_str().unwrap(), 16).unwrap()))
                .collect();
            let expected = u64::from_str_radix(row["output"].as_str().unwrap(), 16).unwrap();
            assert_eq!(
                pow(numbers[0], numbers[1]).to_bits(),
                expected,
                "{:?}",
                numbers
            );
        }
        for row in v["sums"].as_array().unwrap() {
            let numbers: Vec<f64> = row["input"]
                .as_array()
                .unwrap()
                .iter()
                .map(|v| f64::from_bits(u64::from_str_radix(v.as_str().unwrap(), 16).unwrap()))
                .collect();
            let expected = u64::from_str_radix(row["output"].as_str().unwrap(), 16).unwrap();
            assert_eq!(py_sum(numbers.clone()).to_bits(), expected, "{:?}", numbers);
        }
    }
}
