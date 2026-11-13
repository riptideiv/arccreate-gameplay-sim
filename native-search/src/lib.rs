mod arms;
mod engine;
mod geometry;
mod model;
mod numeric;
mod point_cache;
mod trace;
mod trajectory;
mod wrist;
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use std::sync::{atomic::Ordering, mpsc};
use std::time::Duration;

#[pyclass]
struct Checkpoint {
    states: Vec<model::State>,
    layer: usize,
    visited: usize,
    pruned: usize,
}
#[pyclass]
struct Search {
    engine: engine::Engine,
}
#[pymethods]
impl Search {
    #[new]
    fn new(py: Python<'_>, prepared: &str, workers: usize) -> PyResult<Self> {
        if workers == 0 {
            return Err(PyValueError::new_err("workers must be positive"));
        }
        let input = serde_json::from_str(prepared)
            .map_err(|e| PyValueError::new_err(format!("invalid native input: {e}")))?;
        let engine = py
            .detach(|| engine::Engine::new(input, workers))
            .map_err(PyValueError::new_err)?;
        Ok(Self { engine })
    }
    fn checkpoint(&self) -> Checkpoint {
        Checkpoint {
            states: self.engine.states.clone(),
            layer: self.engine.layer,
            visited: self.engine.visited,
            pruned: self.engine.pruned,
        }
    }
    fn resume(&mut self, checkpoint: &Checkpoint) -> PyResult<()> {
        self.engine.restore(
            &checkpoint.states,
            checkpoint.layer,
            checkpoint.visited,
            checkpoint.pruned,
        );
        Ok(())
    }
    fn advance(&mut self, py: Python<'_>) -> PyResult<(usize, usize, usize)> {
        py.check_signals()?;
        let cancellation = self.engine.cancellation.clone();
        let engine = &mut self.engine;
        // The coordinator and its persistent Rayon pool never attach to
        // Python. Only the calling Python thread polls and handles signals.
        std::thread::scope(|scope| -> PyResult<()> {
            let (sender, mut receiver) = mpsc::sync_channel(1);
            let worker = scope.spawn(move || {
                let result = engine.advance();
                let _ = sender.send(result);
            });
            let mut interrupted = None;
            let outcome = loop {
                let receiver_ref = &mut receiver;
                let message =
                    py.detach(move || receiver_ref.recv_timeout(Duration::from_millis(100)));
                if interrupted.is_none() {
                    if let Err(error) = py.check_signals() {
                        cancellation.store(true, Ordering::Relaxed);
                        interrupted = Some(error);
                    }
                }
                match message {
                    Ok(result) => break result.map_err(PyValueError::new_err),
                    Err(mpsc::RecvTimeoutError::Timeout) => continue,
                    Err(mpsc::RecvTimeoutError::Disconnected) => {
                        break Err(PyRuntimeError::new_err(
                            "native coordinator terminated unexpectedly",
                        ));
                    }
                }
            };
            let joined = py.detach(move || worker.join());
            if let Some(error) = interrupted {
                return Err(error);
            }
            joined.map_err(|_| PyRuntimeError::new_err("native coordinator panicked"))?;
            outcome
        })?;
        py.check_signals()?;
        Ok((
            self.engine.layer,
            self.engine.states.len(),
            self.engine.visited,
        ))
    }
    fn result(&self, py: Python<'_>) -> PyResult<String> {
        if self.engine.layer != self.engine.input.groups.len() {
            return Err(PyValueError::new_err("search has not completed"));
        }
        py.detach(|| serde_json::to_string(&self.engine.result()))
            .map_err(|e| PyValueError::new_err(e.to_string()))
    }
    fn debug_snapshot(&self, py: Python<'_>) -> PyResult<String> {
        py.detach(|| serde_json::to_string(&self.engine.debug_snapshot()))
            .map_err(|e| PyValueError::new_err(e.to_string()))
    }
}
#[pyfunction]
fn wrist_frames(py: Python<'_>, input: &str, pairs: Vec<[[f64; 2]; 2]>) -> PyResult<Vec<f64>> {
    let input = serde_json::from_str(input)
        .map_err(|e| PyValueError::new_err(format!("invalid wrist input: {e}")))?;
    let wrist = wrist::WristEstimate::new(input).map_err(PyValueError::new_err)?;
    Ok(py.detach(|| pairs.iter().map(|p| wrist.frame(p[0], p[1])).collect()))
}
#[pyfunction]
fn implementation_id() -> String {
    format!(
        "arc-hand-order-native/{};schema=4",
        env!("CARGO_PKG_VERSION")
    )
}

#[pymodule]
fn arc_hand_order_native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Search>()?;
    m.add_function(wrap_pyfunction!(wrist_frames, m)?)?;
    m.add_function(wrap_pyfunction!(implementation_id, m)?)?;
    Ok(())
}
