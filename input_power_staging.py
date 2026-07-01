#!/usr/bin/python3
# -*- coding: utf-8 -*-
# zeroinput - input_power_staging: staged power distribution v2.2
#
# Pure functions (no I/O) so they can be unit-tested in isolation.
# Driver objects passed in need: .id .stages .count .min_power .max_power
# and .group_capacity(). .stages is a list of stage numbers the unit runs in.
#
# TWO stages only:
#   stage 1: groups with stage == 1 carry the base load up to
#            single_inverter_threshold (ideally a single small inverter).
#   stage 2: ALL eligible groups (stage <= 2) share the load in EQUAL
#            per-unit parts, independent of each unit's max_power. As demand
#            grows, the shared per-unit value rises until it reaches the
#            max_power of the smallest units; those saturate and the remaining
#            load is re-split equally among the still-unsaturated units. The
#            largest unit (e.g. the MultiPlus) therefore saturates last and
#            effectively becomes the top stage on its own - by capacity, not
#            by a special rule.


def active_stage(long_send_history, single_threshold):
	"""Return 1 or 2 from the smoothed demand history.
	Mirrors the original single/multi hysteresis: the 4th-largest recent value
	must exceed the threshold to advance; the whole multi_inverter_wait-length
	history must drop back to/below it to return to stage 1."""
	if len(long_send_history) >= 4:
		recent_high = sorted(long_send_history)[-4]
	else:
		recent_high = max(long_send_history) if long_send_history else 0
	return 1 if recent_high <= single_threshold else 2


def cycles_until_stage1(long_send_history, single_threshold):
	"""Conditional countdown: cycles until active_stage would return to 1 IF
	every future value stays <= single_threshold. Returns None when already at
	stage 1 (or about to be). The estimate resets/grows as new high values
	enter the history, since stage-down needs the whole window to settle."""
	if active_stage(long_send_history, single_threshold) == 1:
		return None
	over_idx = [i for i, v in enumerate(long_send_history) if v > single_threshold]
	if len(over_idx) <= 3:
		return None
	# stage 1 once only 3 over-threshold values remain in the window; that
	# happens after the 4th-oldest over-threshold value has scrolled out
	return over_idx[len(over_idx) - 4] + 1


def _equal_share_with_saturation(demand, units):
	"""Core stage-2 math. units: list of (key, max_power) for SINGLE units
	(a group of count N contributes N entries with the same max_power).
	Returns a list of per-unit watts (float) aligned with `units`.

	Equal split: every unsaturated unit gets the same watts. Units whose equal
	share would exceed their max_power saturate at max_power; their surplus is
	redistributed equally among the rest. Iterate until stable."""
	n = len(units)
	assigned = [0.0] * n
	saturated = [False] * n
	remaining = float(demand)

	for _ in range(n + 1):
		open_idx = [i for i in range(n) if not saturated[i]]
		if not open_idx or remaining <= 1e-9:
			break
		share = remaining / len(open_idx)
		newly = []
		for i in open_idx:
			cap = units[i][1]
			if share >= cap:
				newly.append(i)
		if newly:
			for i in newly:
				assigned[i] = units[i][1]
				remaining  -= units[i][1]
				saturated[i] = True
			continue
		# no new saturation: the equal share fits everyone still open
		for i in open_idx:
			assigned[i] = share
		remaining = 0.0
		break
	return assigned


def distribute(power_demand, drivers, stage, single_threshold):
	"""Return {driver_id: per_unit_watts} for the given stage.

	A group is eligible if `stage` is in its .stages list (explicit membership,
	no upward/downward implication). This allows a hard handover, e.g. a Soyo
	with stages=[1] and an MP2 with stages=[2]: in stage 2 the Soyo is off and
	the MP2 carries the whole load."""
	result = {d.id: 0 for d in drivers}
	if power_demand <= 0:
		return result

	eligible = [d for d in drivers if stage in d.stages]
	if not eligible:
		return result

	units = []		# (driver, max_power)
	for d in eligible:
		for _ in range(d.count):
			units.append((d, d.max_power))

	unit_caps = [(idx, u[1]) for idx, u in enumerate(units)]
	assigned = _equal_share_with_saturation(power_demand, unit_caps)

	per_driver = {}
	pos = 0
	for d in eligible:
		vals = assigned[pos:pos + d.count]
		pos += d.count
		per_driver[d.id] = sum(vals) / d.count if d.count else 0.0

	sleepers = [d for d in eligible if 0 < per_driver[d.id] < d.min_power]
	if sleepers:
		freed = sum(per_driver[d.id] * d.count for d in sleepers)
		survivors = [d for d in eligible if d not in sleepers]
		for d in sleepers:
			per_driver[d.id] = 0.0
		if survivors and freed > 0:
			units2 = []
			for d in survivors:
				for _ in range(d.count):
					units2.append((d, d.max_power))
			base = sum(per_driver[d.id] * d.count for d in survivors)
			caps2 = [(idx, u[1]) for idx, u in enumerate(units2)]
			assigned2 = _equal_share_with_saturation(base + freed, caps2)
			pos = 0
			for d in survivors:
				vals = assigned2[pos:pos + d.count]
				pos += d.count
				per_driver[d.id] = sum(vals) / d.count if d.count else 0.0

	for d in eligible:
		v = per_driver.get(d.id, 0.0)
		if v < d.min_power:
			result[d.id] = 0
		else:
			result[d.id] = max(d.min_power, min(int(round(v)), d.max_power))
	return result


def count_active_units(allocation, drivers):
	"""Sum of count over groups that received non-zero power."""
	by_id = {d.id: d for d in drivers}
	return sum(by_id[i].count for i, w in allocation.items()
	           if w > 0 and i in by_id)


def fade_blend(alloc_from, alloc_to, t, drivers, t_out=None):
	"""Linearly blend two per-driver allocations for a stage cross-fade.

	alloc_from / alloc_to — {driver_id: per_unit_watts} (e.g. stage-2 and
	                        stage-1 results of distribute() for the same demand).
	t                     — blend factor 0.0..1.0 for units that ramp UP
	                        (alloc_to > alloc_from, i.e. the stage-1 unit).
	t_out                 — separate factor for units that ramp DOWN
	                        (alloc_to < alloc_from, the stage-2 units). Defaults
	                        to t. Running the fade-out a few cycles behind the
	                        fade-in (t_out < t) lets the rising unit catch up
	                        before the others drop, avoiding a feed-in dip — at
	                        the cost of brief over-feed (clamped to max_power).
	Per-unit watts are interpolated, then the same min_power/max_power clamp as
	distribute() is applied: a unit below its min_power is switched off (0).
	This is stateless — the caller owns the fade countdown(s)."""
	t = max(0.0, min(1.0, t))
	t_out = t if t_out is None else max(0.0, min(1.0, t_out))
	result = {}
	for d in drivers:
		frm = alloc_from.get(d.id, 0)
		to  = alloc_to.get(d.id, 0)
		tt  = t_out if to < frm else t			# down-faders use the delayed factor
		v = (1.0 - tt) * frm + tt * to
		if v < d.min_power:
			result[d.id] = 0
		else:
			result[d.id] = max(d.min_power, min(int(round(v)), d.max_power))
	return result


def check_stage1_capacity(drivers, single_threshold):
	"""Startup sanity check: can the stage-1 groups alone cover
	single_inverter_threshold? Returns (ok, stage1_capacity)."""
	cap = sum(d.group_capacity() for d in drivers if 1 in d.stages)
	return (cap >= single_threshold, cap)


def delivered_power(power_demand, drivers, single_threshold):
	"""Total watts actually delivered for a requested power_demand, following the
	real path: stage 1 up to single_threshold, stage 2 above. Mirrors what
	send_to_inverters would produce (sum over groups of per_unit * count).
	Returns (delivered_watts, active_unit_count)."""
	stage = active_stage([power_demand], single_threshold)
	alloc = distribute(power_demand, drivers, stage, single_threshold)
	by_id = {d.id: d for d in drivers}
	deliv  = sum(w * by_id[i].count for i, w in alloc.items() if i in by_id)
	active = count_active_units(alloc, drivers)
	return deliv, active


def find_coverage_gaps(drivers, single_threshold, max_input_power):
	"""Sweep power_demand from the smallest min_power up to max_input_power in
	1 W steps along the real control path and report genuine coverage gaps.

	In stage 2 the equal-share split means one extra watt of demand spreads over
	all active units and rounds, so delivered power inherently moves in steps of
	about the active-unit count — that is the control RESOLUTION, not a gap. A
	gap is a jump LARGER than that: delivered power skips a band no setting can
	reach. The per-step threshold is therefore (active units + 1 W rounding).

	Returns a list of (from_w, to_w) tuples — the skipped delivered-power band.
	Empty list = seamless coverage. Any returned gap is an error; gapless
	coverage is required. The band below the smallest min_power is exempt (the
	system is effectively off there; disable feed-in via max_input_power=0 or the
	timer)."""
	if not drivers or max_input_power <= 0:
		return []
	min_powers = [d.min_power for d in drivers if d.min_power > 0]
	start = min(min_powers) if min_powers else 1
	gaps = []
	prev_deliv = 0
	prev_active = 0
	first_step = True
	for demand in range(start, int(max_input_power) + 1):
		deliv, active = delivered_power(demand, drivers, single_threshold)
		# allowed step = inherent resolution (active units) + 1 W rounding.
		# use the larger of the active counts on either side of the step so a
		# transition into more units is not mis-flagged.
		allowed = max(prev_active, active) + 1
		# exempt the very first rise from 0 to the first deliverable value:
		# the band below the smallest min_power is off by definition.
		if first_step:
			if deliv > 0:
				first_step = False
		elif deliv - prev_deliv > allowed:
			gaps.append((prev_deliv, deliv))
		prev_deliv = deliv
		prev_active = active
	return gaps
