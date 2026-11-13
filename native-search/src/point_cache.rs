//! Preserve the reference's lazy parent/frame geometry-cache semantics.
//!
//! Split scoring jobs can select different source segments for the same key.
//! A cache miss therefore waits for earlier frontier states, while immutable
//! cache hits remain available to every worker without an ordering barrier.
use crate::model::XY;
use std::cell::Cell;
use std::collections::BTreeMap;
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc, Condvar, Mutex,
};
use std::time::Duration;

thread_local! {
    static STATE_INDEX: Cell<usize> = const { Cell::new(0) };
}

#[derive(Default)]
struct Data {
    points: BTreeMap<(String, i32), XY>,
    finished: Vec<bool>,
    next: usize,
}

pub struct PointCache {
    data: Mutex<Data>,
    changed: Condvar,
    cancellation: Arc<AtomicBool>,
}

pub struct StateGuard<'a> {
    cache: &'a PointCache,
    index: usize,
    previous: usize,
}

impl PointCache {
    pub fn new(cancellation: Arc<AtomicBool>) -> Self {
        Self {
            data: Mutex::new(Data::default()),
            changed: Condvar::new(),
            cancellation,
        }
    }

    pub fn reset_layer(&self, states: usize) {
        let mut data = self.data.lock().unwrap();
        data.finished = vec![false; states];
        data.next = 0;
    }

    pub fn enter(&self, index: usize) -> StateGuard<'_> {
        StateGuard {
            cache: self,
            index,
            previous: STATE_INDEX.with(|slot| slot.replace(index)),
        }
    }

    pub fn get(&self, parent: &str, frame: i32, raw: XY) -> XY {
        let key = (parent.to_owned(), frame);
        let index = STATE_INDEX.with(Cell::get);
        let mut data = self.data.lock().unwrap();
        loop {
            if let Some(value) = data.points.get(&key) {
                return *value;
            }
            if self.cancellation.load(Ordering::Relaxed) {
                // The entire interrupted layer is discarded by the caller.
                return raw;
            }
            if data.next >= index {
                data.points.insert(key, raw);
                self.changed.notify_all();
                return raw;
            }
            data = self
                .changed
                .wait_timeout(data, Duration::from_millis(100))
                .unwrap()
                .0;
        }
    }

    pub fn snapshot(&self) -> Vec<(String, i32, XY)> {
        self.data
            .lock()
            .unwrap()
            .points
            .iter()
            .map(|((parent, frame), xy)| (parent.clone(), *frame, *xy))
            .collect()
    }
}

impl Drop for StateGuard<'_> {
    fn drop(&mut self) {
        STATE_INDEX.with(|slot| slot.set(self.previous));
        if std::thread::panicking() {
            // A worker that unwinds will not visit its remaining assigned
            // states. Wake other workers instead of stranding their misses.
            self.cache.cancellation.store(true, Ordering::Relaxed);
        }
        let mut data = self.cache.data.lock().unwrap();
        data.finished[self.index] = true;
        while data.next < data.finished.len() && data.finished[data.next] {
            data.next += 1;
        }
        self.cache.changed.notify_all();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::mpsc;
    use std::thread;

    #[test]
    fn reversed_misses_use_the_first_request_in_frontier_order() {
        let cancelled = Arc::new(AtomicBool::new(false));
        let cache = PointCache::new(cancelled.clone());
        cache.reset_layer(3);
        thread::scope(|scope| {
            let cache = &cache;
            let first = cache.enter(0);
            let (started_tx, started_rx) = mpsc::channel();
            let (result_tx, result_rx) = mpsc::channel();
            let later = scope.spawn(move || {
                let _state = cache.enter(2);
                started_tx.send(()).unwrap();
                result_tx.send(cache.get("snake", 99, [2., 2.])).unwrap();
            });
            started_rx.recv_timeout(Duration::from_secs(1)).unwrap();
            // State 2 arrived first, but may not choose the source segment.
            let premature = result_rx.recv_timeout(Duration::from_millis(30));
            drop(first);
            let earlier = scope.spawn(move || {
                let _state = cache.enter(1);
                cache.get("snake", 99, [1., 1.])
            });
            let result = result_rx.recv_timeout(Duration::from_secs(1));
            if result.is_err() {
                cancelled.store(true, Ordering::Relaxed);
            }
            assert_eq!(earlier.join().unwrap(), [1., 1.]);
            later.join().unwrap();
            assert!(matches!(premature, Err(mpsc::RecvTimeoutError::Timeout)));
            assert_eq!(result.unwrap(), [1., 1.]);
        });
        assert_eq!(cache.snapshot(), vec![("snake".into(), 99, [1., 1.])]);
    }

    #[test]
    fn cached_hits_do_not_wait_for_earlier_states_or_new_layers() {
        let cache = PointCache::new(Arc::new(AtomicBool::new(false)));
        assert_eq!(cache.get("snake", 9, [1., 2.]), [1., 2.]);
        cache.reset_layer(2);
        thread::scope(|scope| {
            let cache = &cache;
            let first = cache.enter(0);
            let (tx, rx) = mpsc::channel();
            let later = scope.spawn(move || {
                let _state = cache.enter(1);
                tx.send(cache.get("snake", 9, [3., 4.])).unwrap();
            });
            let result = rx.recv_timeout(Duration::from_secs(1));
            // Always release the earlier state before asserting so a broken
            // cache-hit fast path fails the test instead of hanging it.
            drop(first);
            later.join().unwrap();
            assert_eq!(result.unwrap(), [1., 2.]);
        });
    }

    #[test]
    fn cancellation_releases_a_waiting_miss_without_publishing_it() {
        let cancelled = Arc::new(AtomicBool::new(false));
        let cache = PointCache::new(cancelled.clone());
        cache.reset_layer(2);
        thread::scope(|scope| {
            let cache = &cache;
            let first = cache.enter(0);
            let (started_tx, started_rx) = mpsc::channel();
            let (result_tx, result_rx) = mpsc::channel();
            let later = scope.spawn(move || {
                let _state = cache.enter(1);
                started_tx.send(()).unwrap();
                result_tx.send(cache.get("snake", 10, [3., 4.])).unwrap();
            });
            started_rx.recv_timeout(Duration::from_secs(1)).unwrap();
            let premature = result_rx.recv_timeout(Duration::from_millis(30));
            cancelled.store(true, Ordering::Relaxed);
            let result = result_rx.recv_timeout(Duration::from_secs(1));
            drop(first);
            later.join().unwrap();
            assert!(matches!(premature, Err(mpsc::RecvTimeoutError::Timeout)));
            assert_eq!(result.unwrap(), [3., 4.]);
        });
        assert!(cache.snapshot().is_empty());
    }

    #[test]
    fn unwinding_an_earlier_worker_cancels_and_wakes_waiters() {
        let cancelled = Arc::new(AtomicBool::new(false));
        let cache = PointCache::new(cancelled.clone());
        cache.reset_layer(2);
        thread::scope(|scope| {
            let cache = &cache;
            let (entered_tx, entered_rx) = mpsc::channel();
            let (panic_tx, panic_rx) = mpsc::channel();
            let first = scope.spawn(move || {
                std::panic::catch_unwind(|| {
                    let _state = cache.enter(0);
                    entered_tx.send(()).unwrap();
                    panic_rx.recv().unwrap();
                    panic!("simulated expansion failure");
                })
            });
            entered_rx.recv_timeout(Duration::from_secs(1)).unwrap();
            let (started_tx, started_rx) = mpsc::channel();
            let (result_tx, result_rx) = mpsc::channel();
            let later = scope.spawn(move || {
                let _state = cache.enter(1);
                started_tx.send(()).unwrap();
                result_tx.send(cache.get("snake", 11, [5., 6.])).unwrap();
            });
            started_rx.recv_timeout(Duration::from_secs(1)).unwrap();
            let premature = result_rx.recv_timeout(Duration::from_millis(30));
            panic_tx.send(()).unwrap();
            let result = result_rx.recv_timeout(Duration::from_secs(1));
            if result.is_err() {
                cancelled.store(true, Ordering::Relaxed);
            }
            assert!(first.join().unwrap().is_err());
            later.join().unwrap();
            assert!(matches!(premature, Err(mpsc::RecvTimeoutError::Timeout)));
            assert_eq!(result.unwrap(), [5., 6.]);
        });
        assert!(cancelled.load(Ordering::Relaxed));
        assert!(cache.snapshot().is_empty());
    }
}
