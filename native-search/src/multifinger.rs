//! The occupied-finger handover transition. Both occupations may change
//! geometry before the giver is free to press a new note.
use super::*;

impl Engine {
    pub(super) fn takeovers(
        &self,
        state: &State,
        slot: usize,
        ci: usize,
        base: Cost,
    ) -> Option<ExpansionEvent> {
        let partner_slot = (slot + 2) % 4;
        let giver = state.hands[slot].as_ref()?;
        let receiver = state.hands[partner_slot].as_ref()?;
        if receiver
            .parts
            .iter()
            .any(|p| matches!(self.item(p).kind.as_str(), "tap" | "sky"))
        {
            return None;
        }
        let c = &self.input.candidates[ci];
        let item = &self.input.items[c.item];
        let point_press =
            self.multi("tap_interval_ms") != 0. && matches!(item.kind.as_str(), "tap" | "sky");
        if point_press
            && state.last_point_press[slot].is_some_and(|earlier| {
                f64::from(c.down - earlier) * 1000. < self.multi("tap_interval_ms") * 60.
            })
        {
            return None;
        }
        let variants: Vec<_> = [1, 2, 3, 4, 6, 8, 12, 18, 24]
            .into_iter()
            .filter(|&lead| lead >= self.opt("min_air_frames") as i32)
            .filter_map(|lead| self.takeover_at(state, slot, ci, base, point_press, c.down - lead))
            .collect();
        if variants.is_empty() {
            None
        } else {
            Some(ExpansionEvent::Takeovers(
                variants,
                state.cost,
                giver.clone(),
            ))
        }
    }

    fn takeover_at(
        &self,
        state: &State,
        slot: usize,
        ci: usize,
        base: Cost,
        point_press: bool,
        release: i32,
    ) -> Option<State> {
        let hand = slot % 2;
        let partner_slot = (slot + 2) % 4;
        let giver = state.hands[slot].as_ref()?;
        let receiver = state.hands[partner_slot].as_ref()?;
        if release <= giver.down {
            return None;
        }
        let moving: Vec<_> = giver.parts.iter().filter(|p| p.stop > release).collect();
        if moving.is_empty()
            || moving.iter().any(|p| self.item(p).kind != "hold")
            || receiver
                .parts
                .iter()
                .any(|p| p.stop > release - 2 && self.item(p).kind == "hold")
        {
            return None;
        }
        let mut receiver_parts = receiver.parts.clone();
        receiver_parts.extend(moving.into_iter().map(|p| Part {
            start: p.start.max(release - 2),
            ..p.clone()
        }));
        let receiver_new = self.compose(receiver_parts, receiver.bridges.clone(), None, None)?;
        let giver_parts = giver
            .parts
            .iter()
            .filter(|p| p.start < release)
            .map(|p| Part {
                stop: p.stop.min(release),
                ..p.clone()
            })
            .collect();
        let bridges = giver
            .bridges
            .iter()
            .copied()
            .filter(|span| span[1] <= release)
            .collect();
        let giver_new = self.compose(giver_parts, bridges, None, Some(&receiver_new))?;
        if giver_new.up() != release {
            return None;
        }
        let new = self.paired_press(ci, &receiver_new)?;
        if self.touches_clash(&receiver_new, &giver_new)
            || self.touches_clash(&receiver_new, &new)
            || self.touches_clash(&new, &giver_new)
        {
            return None;
        }
        let before = self.finger_overlap(giver, receiver).unwrap_or([0.; 2]);
        let after_old = self.finger_overlap(&giver_new, &receiver_new)?;
        let after_new = self.finger_overlap(&new, &receiver_new)?;
        let mut out = state.clone();
        out.hands[slot] = Some(giver_new.clone());
        let mut increment = self.new_cost(&out, slot, &new)?;
        for i in [0, 3, 7, 8] {
            increment[i] = base[i];
        }
        increment[10] = 1.;
        increment[11] = after_old[0] + after_new[0] - before[0];
        increment[12] = after_old[1] + after_new[1] - before[1];
        increment[13] = 1.;
        increment[15] = f64::from(self.touch_steals(&new, &receiver_new));
        for other in [1 - hand, 3 - hand]
            .into_iter()
            .filter_map(|i| state.hands[i].as_deref())
        {
            if self.touches_clash(&receiver_new, other) || self.touches_clash(&new, other) {
                return None;
            }
            if self.touch_steals(&receiver_new, other) || self.touch_steals(&new, other) {
                increment[15] = 1.;
            }
            self.add_opponent_delta(hand, other, Some(&new), None, &mut increment);
            self.add_opponent_delta(
                hand,
                other,
                Some(&receiver_new),
                Some(receiver),
                &mut increment,
            );
            self.add_opponent_delta(hand, other, Some(&giver_new), Some(giver), &mut increment);
        }
        if self.multi("accel_max_m_s2") != 0. {
            increment[30] +=
                new.over + receiver_new.over - receiver.over + giver_new.over - giver.over;
        }
        if b(&self.input.multi, "geometry_features") || self.multi("second_held_weight") != 0. {
            increment[24] = f64::from(
                receiver_new
                    .parts
                    .iter()
                    .any(|p| matches!(self.item(p).kind.as_str(), "hold" | "chain")),
            );
        }
        increment[16] = self.charge_jack(state, &mut out, hand, new.down);
        if point_press {
            out.last_point_press[slot] = Some(new.down);
        }
        out.hands[slot] = Some(new.clone());
        out.hands[partner_slot] = Some(receiver_new.clone());
        self.update_owned(&mut out, hand, self.ms(new.down));
        out.cost = plus(&state.cost, &increment);
        out.node = Some(Arc::new(Node {
            previous: state.node.clone(),
            item: self.input.candidates[ci].item,
            slot,
            touch: Some(new),
            adjusted_partner: Some((receiver_new, receiver.clone())),
            shortened: Some(giver_new),
            extension: false,
            increment,
        }));
        Some(out)
    }
}
