//! Regressions for geometry that converges before its first ordered price.
//! The Python reference memoizes full versus incremental costs at first use;
//! worker completion order must not choose that provenance.
use super::*;
use serde_json::json;

const PATH: [XY; 6] = [
    [0.08986468335912698, 0.05875411015304141],
    [-0.0486584047142608, 0.0699878025561986],
    [-0.0026429013934123613, 0.05100077025548022],
    [0.01282771972033614, -0.014084470762717238],
    [-0.02715947495605145, -0.014035545827977905],
    [-0.03724772683493485, -0.07652798988524195],
];
// Recorded with CPython 3.13 math.dist and built-in sum. The one-ULP
// difference is enough to change otherwise tied beam-state ranking.
const FULL_DISTANCE: u64 = 0x3fd6f8f07bbcf0c7;
const PREFIX_DISTANCE: u64 = 0x3fd6f8f07bbcf0c6;

fn fixture(workers: usize) -> Engine {
    let items: Vec<_> = (0..2)
        .map(|i| {
            json!({
                "kind":"chain", "oid":format!("job-{i}"), "parent":"snake", "colour":0,
                "start":0, "end":100, "first_tick":i*50, "last_tick":i*50,
                "score_weight":1, "red":false, "forced":false, "continuous":true,
                "native":false, "source_end":100, "latest_down":100,
                "points_start":0, "points":PATH, "trace_points":PATH,
                "polygons":vec![Vec::<XY>::new();6], "region_centres":PATH,
                "centre":[0.,0.], "trace_start":i*3, "trace":PATH[i*3..i*3+3],
                "trace_hash":i as i64, "trace_member":true,
                "guides_start":0,"guides":[],"contested":false
            })
        })
        .collect();
    let candidates: Vec<_> = (0..2)
        .map(|i| {
            json!({
                "id":i,"item":i,"down":i*3,"up":i*3+3,"path":PATH[i*3..i*3+3],
                "first":PATH[i*3],"held_to_end":true,"held_release":false,"untouched":0.,
                "part_base":vec![vec![0.;9];2],"nominal":[i*50,i*50+50],"fixed":null
            })
        })
        .collect();
    let input: Input = serde_json::from_value(json!({
        "options":{"scoring_arcs":true,"prefer_arc_trace":true,"arc_trace_weight":100.,
                   "speed_max_m_s":1000.,"distance_weight":1.},
        "multi":{},"offset":0,"items":items,"candidates":candidates,"groups":[],
        "trace_order":[0,1],"screen":[[-1.,-1.],[1.,-1.],[1.,1.],[-1.,1.]],
        "geometry":{"camera":{"width":1.,"height":1.,"position":[0.,0.,-1.],
                      "forward":[0.,0.,1.],"scale":1.,"bases":[]},
                    "offset":0,"regions":{},"grace":[],"guards":{},
                    "scores":[],"lock_grace":[],"arc_notes":[]},
        "wrist":null
    }))
    .unwrap();
    Engine::new(input, workers).unwrap()
}

fn parts() -> Vec<Part> {
    vec![
        Part {
            candidate: 0,
            start: 0,
            stop: 3,
        },
        Part {
            candidate: 1,
            start: 3,
            stop: 6,
        },
    ]
}

fn event(engine: &Engine, new: Arc<Touch>, old: Arc<Touch>) -> ExpansionEvent {
    let old_measures = engine.measures(&old);
    let mut before = zero();
    before[6] = old_measures[0];
    before[33] = old_measures[1];
    let state = State {
        arms: None,
        hands: [Some(new.clone()), None],
        runs: [0; 2],
        owned: [None, None],
        locks: [-1; 4],
        cost: before,
        node: Some(Arc::new(Node {
            previous: None,
            item: 1,
            hand: 0,
            touch: Some(new),
            extension: true,
            increment: zero(),
        })),
    };
    ExpansionEvent::Transition(state, before, Some(old))
}

fn convergent_prices(workers: usize, prefix_first: bool) -> Vec<Cost> {
    let engine = fixture(workers);
    let good = engine.singles[0].clone();
    let mut different = good.path.clone();
    different[0][0] += 0.001;
    let bad = engine.touch(good.parts.clone(), vec![], 0, different, 0., None, None);
    // Predecessors represent an already committed frontier: they were priced
    // before expansion. Only the convergent successor's first price is pending.
    engine.measures(&good);
    engine.measures(&bad);
    let batches: Vec<Vec<ExpansionEvent>> = engine.pool.install(|| {
        (0..64usize)
            .into_par_iter()
            .map(|index| {
                if index == 0 && workers > 1 {
                    std::thread::sleep(std::time::Duration::from_millis(3));
                }
                let use_prefix = (index % 2 == 0) == prefix_first;
                let old = if use_prefix {
                    good.clone()
                } else {
                    bad.clone()
                };
                let new = engine.touch(parts(), vec![], 0, PATH.to_vec(), 0., None, Some(&old));
                assert!(
                    new.measures.get().is_none(),
                    "workers must not price speculative geometry"
                );
                let mut events = vec![];
                if use_prefix {
                    events.push(ExpansionEvent::Prefix(new.clone(), old.clone()));
                }
                events.push(event(&engine, new, old));
                events
            })
            .collect()
    });
    let states: Vec<_> = batches
        .into_iter()
        .flatten()
        .filter_map(|e| engine.apply_event(e))
        .collect();
    let first = states[0].hands[0].as_ref().unwrap();
    assert!(states
        .iter()
        .all(|s| Arc::ptr_eq(s.hands[0].as_ref().unwrap(), first)));
    assert_eq!(
        engine.measures(first)[0].to_bits(),
        if prefix_first {
            PREFIX_DISTANCE
        } else {
            FULL_DISTANCE
        }
    );
    states.into_iter().map(|s| s.cost).collect()
}

#[test]
fn convergent_touch_costs_follow_encounter_order_not_worker_completion() {
    for prefix_first in [false, true] {
        let serial = convergent_prices(1, prefix_first);
        assert_eq!(convergent_prices(4, prefix_first), serial);
    }
}

#[test]
fn rejected_branch_prefix_is_retained_before_later_successful_price() {
    let engine = fixture(4);
    let old = engine.singles[0].clone();
    engine.measures(&old);
    let new = engine.touch(parts(), vec![], 0, PATH.to_vec(), 0., None, None);
    // The first branch establishes a prefix and is then rejected, so it has no
    // Transition. Python still retains its _native_prefix for subsequent use.
    assert!(engine
        .apply_event(ExpansionEvent::Prefix(new.clone(), old.clone()))
        .is_none());
    assert!(new.measures.get().is_none());
    engine
        .apply_event(event(&engine, new.clone(), old))
        .unwrap();
    assert_eq!(engine.measures(&new)[0].to_bits(), PREFIX_DISTANCE);
}

fn arrival_touch(engine: &Engine, base: &Arc<Touch>) -> Arc<Touch> {
    engine.touch(
        base.parts.clone(),
        base.bridges.clone(),
        base.down,
        base.path.clone(),
        base.over,
        Some(Arrival {
            up: -2,
            path: vec![[0., 0.], [0.01, 0.01]],
        }),
        None,
    )
}

#[test]
fn arrival_copies_existing_distance_and_held_price_but_not_deviation_cache() {
    let mut engine = fixture(1);
    engine.input.options["acceleration_weight"] = json!(5.);
    let old = engine.singles[0].clone();
    engine.measures(&old);
    let base = engine.touch(parts(), vec![], 0, PATH.to_vec(), 0., None, None);
    let full = *engine.measures(&base);
    assert_eq!(full[0].to_bits(), FULL_DISTANCE);
    engine.apply_event(ExpansionEvent::Prefix(base.clone(), old));
    let arrival = arrival_touch(&engine, &base);
    engine.apply_event(ExpansionEvent::Arrival(arrival.clone()));
    let values = *engine.measures(&arrival);
    assert_eq!(values[0].to_bits(), FULL_DISTANCE);
    assert_eq!(values[2].to_bits(), full[2].to_bits());
    assert!(arrival.prefix.get().is_some());

    // A fresh reference touch with that prefix recomputes deviation. Unlike
    // distance/held effort, Python stores deviation outside touch.__dict__.
    let reference = fixture(1);
    let previous = reference.singles[0].clone();
    let fresh = reference.touch(parts(), vec![], 0, PATH.to_vec(), 0., None, None);
    reference.apply_event(ExpansionEvent::Prefix(fresh.clone(), previous));
    assert_eq!(values[1].to_bits(), reference.measures(&fresh)[1].to_bits());
}

#[test]
fn arrival_does_not_copy_prices_added_after_its_ordered_creation() {
    let engine = fixture(1);
    let old = engine.singles[0].clone();
    engine.measures(&old);
    let base = engine.touch(parts(), vec![], 0, PATH.to_vec(), 0., None, None);
    let arrival = arrival_touch(&engine, &base);
    engine.apply_event(ExpansionEvent::Arrival(arrival.clone()));
    assert_eq!(arrival.inherited_measures.get(), Some(&[None, None]));
    assert_eq!(engine.measures(&base)[0].to_bits(), FULL_DISTANCE);
    engine.apply_event(ExpansionEvent::Prefix(base, old.clone()));
    engine.apply_event(ExpansionEvent::Prefix(arrival.clone(), old));
    assert_eq!(engine.measures(&arrival)[0].to_bits(), PREFIX_DISTANCE);
}

#[test]
fn rejected_arm_routes_still_establish_contact_prices() {
    let mut engine = fixture(1);
    engine.input.groups = vec![vec![0], vec![1]];
    engine.input.geometry.scores = (0..2)
        .map(|_| crate::geometry::ScoreItem {
            native: false,
            colour: 0,
            points: vec![],
        })
        .collect();
    let crossing = crate::arms::CrossingGraph::new(0.012, 1000., 1., 30);
    let mut arms = crossing.initial();
    for path in &mut arms.paths {
        let path = Arc::make_mut(path);
        path.points.resize(21, path.points[0]);
        path.locked.resize(21, false);
        path.anchor = 20;
    }
    engine.crossing = Some(crossing);
    let mut state = engine.states[0].clone();
    state.arms = Some(Arc::new(arms));
    engine.point_cache.reset_layer(1);
    let events = engine.expand(&state, 0, 0);
    assert!(events
        .iter()
        .any(|e| matches!(e, ExpansionEvent::Price(..))));
    assert!(!events
        .iter()
        .any(|e| matches!(e, ExpansionEvent::Routed(..))));
    for event in events {
        assert!(engine.apply_event(event).is_none());
    }
    assert!(engine.singles[0].measures.get().is_some());
}
