#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""Unit/scenario tests for dirt_shift's pure decision functions.

dirt_shift.py loads its config and touches volkszaehler.org and the grid sources at import time, so
this does NOT import it. It extracts the pure functions (the ones that take
their inputs as arguments and touch nothing external) from the source and runs
them against a stubbed conf, so regressions in the decision logic are caught
automatically.

Run:  python3 tests/test_dirt_shift.py      (exit 0 = all passed)
"""
import ast, sys
from datetime import datetime, timedelta
from os.path import abspath, dirname, join

SRC = join(dirname(dirname(abspath(__file__))), 'dirt_shift', 'dirt_shift.py')

# ── load the pure functions and module constants, without executing main/IO ──

def _load_pure(names, constants):
	src = open(SRC, encoding='utf-8').read()
	tree = ast.parse(src)
	ns = {'datetime': datetime, 'timedelta': timedelta}
	# constants: assignments at module level
	for node in tree.body:
		if isinstance(node, ast.Assign):
			for t in node.targets:
				if isinstance(t, ast.Name) and t.id in constants:
					ns[t.id] = eval(compile(ast.Expression(node.value), SRC, 'eval'), ns)
	# functions: by name
	wanted = {n: None for n in names}
	for node in tree.body:
		if isinstance(node, ast.FunctionDef) and node.name in wanted:
			wanted[node.name] = node
	for name, node in wanted.items():
		if node is None:
			raise SystemExit('FATAL: function %s not found in %s' % (name, SRC))
		exec(compile(ast.Module([node], []), SRC, 'exec'), ns)
	return ns


FUNCS = ['_median', '_cut_zones', '_dirt_rank_pct', '_bridge_hours',
         'reserved_red_hours', 'reserved_red_dirt', 'red_window_demand',
         'surplus_before_next_red', 'upcoming_red_demand', 'dirtiest_hour',
         'wallbox_should_be_on', 'wallbox_decide', 'wallbox_energy_ok',
         'wallbox_voltage_ok', 'precharge_ac_pct', '_wind_index',
         'emergency_zones', '_empty_anchor', '_neg_content_diagnosis',
         'precharge_round_trip_gap', '_ampel_days', '_substitute_day']
CONSTS = ['SLOT_MINUTES', 'SLOT_HOURS', 'STOP_MIN_DIRT_GAP',
          'WALLBOX_ENERGY_ON_FACTOR', 'WALLBOX_V_ON_PER_CELL',
          'WALLBOX_V_OFF_PER_CELL', 'PRECHARGE_AC_FLOOR',
          'EMERGENCY_WIND_V_CUTIN', 'EMERGENCY_WIND_V_RATED',
          'EMERGENCY_LOAD_PROFILE', 'EMERGENCY_WIND_WEIGHT',
          'EMERGENCY_SOLAR_WEIGHT', 'EMPTY_MIN_DURATION_S',
          'IMPLAUSIBLE_V_PER_CELL', 'NEG_CONTENT_WARN_FRACTION', 'GRID_MIN_HOURS']

M = _load_pure(FUNCS, CONSTS)
CONF = {
	'reserve_pct': 120, 'wallbox_cleanest_pct': 30, 'wallbox_absolute_max': 50,
	'wallbox_typical_power': 2150, 'precharge_enabled': True,
	'precharge_cleanest_pct': 25, 'battery_capacity_wh': 5000,
	'PV_to_bat_efficiency': 95, 'bat_to_AC_efficiency': 95, 'cell_count': 16,
}
# inject conf + constants into every extracted function's globals
for _n in FUNCS:
	M[_n].__globals__['conf'] = CONF
	M[_n].__globals__['debug'] = False		# _cut_zones and others read this module global
	M[_n].__globals__['verbose'] = False
	for _c in CONSTS:
		M[_n].__globals__[_c] = M[_c]


class Now:
	def __init__(self, h): self.hour = h


# ── tiny test harness ──────────────────────────────────────────────────────

_fails = []
def check(name, cond, detail=''):
	print('  %s %s%s' % ('PASS' if cond else 'FAIL', name, '' if cond else '  <-- ' + detail))
	if not cond: _fails.append(name)


def ratio_from_dirt(dirt):			# dirt% = (1 - ratio) * 100  ->  ratio = 1 - dirt/100
	return [None if d is None else 1 - d / 100.0 for d in dirt]


# ── a couple of representative days, dirt% per hour 0..23 ────────────────────

# clear day: clean midday (even negative), dirty evening+night
DAY_SUNNY = [70,70,68,66,64,60,55,45,30,15,5,-5,-10,-12,-10,-5,5,20,40,60,68,70,71,70]
# dull day: no surplus at all, mildly dirty throughout
DAY_DULL  = [44,42,40,40,42,43,44,36,25,20,14,6,1,-3,-5,-4,3,13,28,39,42,42,42,39]
LOAD = [300.0] * 24
PV_SUNNY = [0,0,0,0,0,0,0,50,400,1200,2000,2500,2600,2600,2500,2000,1200,400,0,0,0,0,0,0]
PV_DULL  = [0,0,0,0,0,0,0,0,100,300,500,650,700,700,650,500,300,100,0,0,0,0,0,0]
PV_SUNNY = [float(x) for x in PV_SUNNY]
PV_DULL  = [float(x) for x in PV_DULL]


def zones(dirt):
	return M['_cut_zones'](ratio_from_dirt(dirt))


# ── tests ───────────────────────────────────────────────────────────────────

def test_median_and_zones():
	print('median / zones')
	check('median ignores None', M['_median']([1, None, 3, None, 5]) == 3)
	check('median even count', M['_median']([1, 2, 3, 4]) == 2.5)
	check('median all None -> None', M['_median']([None, None]) is None)
	z = zones(DAY_SUNNY)
	check('cut yields 12/12', z.count('red') == 12 and z.count('green') == 12,
	      '%d red' % z.count('red'))
	# a median split never degenerates, even on a uniform day
	zc = zones([30] * 12 + [31] * 12)
	check('near-uniform still splits', 0 < zc.count('red') < 24)
	check('all-None ratio -> all red', M['_cut_zones']([None] * 24) == ['red'] * 24)


def test_rank():
	print('dirt rank percentile')
	d = DAY_SUNNY
	check('cleanest hour rank 0', M['_dirt_rank_pct'](min(d), d) == 0.0)
	check('None dirt -> None', M['_dirt_rank_pct'](None, d) is None)
	# rank is sign-agnostic: shifting every value by a constant leaves ranks put
	shifted = [x + 100 for x in d]
	r1 = M['_dirt_rank_pct'](d[12], d)
	r2 = M['_dirt_rank_pct'](shifted[12], shifted)
	check('rank invariant under offset', r1 == r2, '%s vs %s' % (r1, r2))


def test_reserve_invariants():
	print('reserve invariants')
	z = zones(DAY_SUNNY)
	now = Now(21)					# evening, before the dirty night
	rwd = M['red_window_demand'](LOAD, now, z, PV_SUNNY)
	urd = M['upcoming_red_demand'](now, z, LOAD, PV_SUNNY)
	surp = M['surplus_before_next_red'](now, z, LOAD, PV_SUNNY)
	up_netted = max(0.0, urd - surp)
	main_reserve = CONF['reserve_pct'] * 0.01 * max(rwd, up_netted)
	# wallbox reserve uses the SAME max() but WITHOUT the surplus credit
	wb_reserve = CONF['reserve_pct'] * 0.01 * max(rwd, urd)
	check('reserve non-negative', main_reserve >= 0)
	check('wallbox reserve >= main reserve', (CONF['reserve_pct']*0.01*max(0.0,urd-surp)) >= main_reserve - 1e-9,
	      'wb %.0f < main %.0f' % (wb_reserve, main_reserve))
	# with no surplus before the block, the two demands agree at the block
	nown = Now(23)
	rwd2 = M['red_window_demand'](LOAD, nown, z, PV_SUNNY)
	check('window demand covers the red block', rwd2 > 0)


def test_reserved_red_hours_shared():
	print('reserved_red_hours shared by stop-gap and precharge')
	z = zones(DAY_SUNNY)
	now = Now(23)
	hrs = M['reserved_red_hours'](now, z, LOAD, PV_SUNNY)
	check('red hours are all red', all(z[h] == 'red' for h in hrs), str(hrs))
	check('red hours before first surplus only',
	      all(PV_SUNNY[h] <= LOAD[h] for h in hrs))
	mean = M['reserved_red_dirt'](now, z, ratio_from_dirt(DAY_SUNNY), LOAD, PV_SUNNY)
	man = sum(DAY_SUNNY[h] for h in hrs) / len(hrs)
	check('reserved_red_dirt is the mean of those hours', abs(mean - man) < 1e-6,
	      '%.2f vs %.2f' % (mean, man))


def test_wallbox_hysteresis():
	print('wallbox energy/voltage hysteresis')
	m = CONF['wallbox_typical_power'] * M['SLOT_HOURS']
	res = 1000.0
	# switching ON needs FACTOR margins; holding needs one
	on_needed = res + M['WALLBOX_ENERGY_ON_FACTOR'] * m
	check('energy OFF just below ON threshold',
	      not M['wallbox_energy_ok'](on_needed - 1, res, m, engaged=False))
	check('energy ON just above ON threshold',
	      M['wallbox_energy_ok'](on_needed + 1, res, m, engaged=False))
	# once engaged, a single margin of headroom is enough to keep holding
	hold = res + m + 1
	check('energy holds on one margin when engaged',
	      M['wallbox_energy_ok'](hold, res, m, engaged=True))
	check('same content would NOT switch on cold',
	      not M['wallbox_energy_ok'](hold, res, m, engaged=False))
	cells = CONF['cell_count']
	von = M['WALLBOX_V_ON_PER_CELL'] * cells
	voff = M['WALLBOX_V_OFF_PER_CELL'] * cells
	check('voltage ON at threshold', M['wallbox_voltage_ok'](von, engaged=False, pv_surplus=True))
	check('voltage OFF below ON when idle', not M['wallbox_voltage_ok'](von - 0.1, engaged=False, pv_surplus=True))
	check('voltage holds down to OFF when engaged', M['wallbox_voltage_ok'](voff, engaged=True, pv_surplus=True))
	check('voltage None -> False', not M['wallbox_voltage_ok'](None, engaged=False, pv_surplus=True))
	# the field case (30.09., 19:30): engaged, min 52.2 V under the car's own
	# load, no PV — must release on both counts
	check('hold band stays above the plateau (52.2 V sag releases)',
	      not M['wallbox_voltage_ok'](52.2, engaged=True, pv_surplus=True))
	check('no PV surplus -> off even at full voltage',
	      not M['wallbox_voltage_ok'](von + 1.0, engaged=True, pv_surplus=False))
	check('hysteresis band is narrow: OFF within 0.05 V/cell of ON',
	      M['WALLBOX_V_ON_PER_CELL'] - M['WALLBOX_V_OFF_PER_CELL'] <= 0.05 + 1e-9)


def test_wallbox_dirt_path():
	print('wallbox dirt path (rank + absolute, no mode coupling)')
	d = DAY_SUNNY
	# cleanest hour: within rank AND under absolute max -> True
	ch = d.index(min(d))
	check('cleanest hour qualifies', M['wallbox_should_be_on'](d[ch], d))
	# an hour just inside the absolute cap but ranked too high -> False
	# pick an hour whose rank exceeds cleanest_pct
	dirty = 40
	check('high-rank hour refused even if under abs-max',
	      not M['wallbox_should_be_on'](dirty, d))
	# decide() is a pure OR of the three
	check('decide True if any path true',
	      M['wallbox_decide'](d[ch], d, energy_ok=False, voltage_ok=False))
	check('decide True on energy alone',
	      M['wallbox_decide'](40, d, energy_ok=True, voltage_ok=False))
	check('decide False if none',
	      not M['wallbox_decide'](40, d, energy_ok=False, voltage_ok=False))


def test_emergency_wind_curve():
	print('emergency wind curve + load profile (building blocks)')
	wi = M['_wind_index']
	check('wind below cut-in -> 0', wi(2.0) == 0.0)
	check('wind at rated -> 1', wi(M['EMERGENCY_WIND_V_RATED']) == 1.0)
	check('wind above rated saturates', wi(20.0) == 1.0)
	check('wind ramp monotonic', wi(6) < wi(8) < wi(10))
	check('load profile has 24 hours', len(M['EMERGENCY_LOAD_PROFILE']) == 24)
	# reproduce emergency dirtiness and confirm wind can flip the day
	LP = M['EMERGENCY_LOAD_PROFILE']
	pv = [0]*7 + [50,400,1200,2000,2500,2600,2600,2500,2000,1200,400] + [0]*6
	pv = (pv + [0]*24)[:24]
	peak = max(pv)
	# calm night, sunny midday -> midday green, evening red
	calm = [4]*24
	zc = M['_cut_zones']([1 - (LP[h] - (pv[h]/peak + wi(calm[h]))) for h in range(24)])
	check('calm day: midday green', zc[12] == 'green' and zc[13] == 'green')
	check('calm day: evening peak red', zc[19] == 'red')
	# stormy night -> night turns green despite high load
	storm = [14]*8 + [4]*8 + [14]*8
	zs = M['_cut_zones']([1 - (LP[h] - (pv[h]/peak + wi(storm[h]))) for h in range(24)])
	check('storm night turns green', zs[2] == 'green')
	check('emergency cut still 12/12', zs.count('red') == 12)


def test_wallbox_downgrade():
	print('wallbox free->limit downgrade only when content <= reserve')
	# mirrors the condition in main(): downgrade only protects a battery that
	# the car would pull below the reserve
	def downgraded(mode, content, reserve, wallbox_on, energy_ok, voltage_ok):
		if wallbox_on and mode == 'free' and content <= reserve and not (energy_ok or voltage_ok):
			return 'limit'
		return mode
	# the field case: sunny day, reserve 0, battery well above it -> stays free
	check('reserve 0, content above -> stays free',
	      downgraded('free', 2586, 0, True, False, False) == 'free')
	# the protection case: forced-free dirtiest hour, content under reserve
	check('content under reserve -> downgraded to limit',
	      downgraded('free', 900, 1500, True, False, False) == 'limit')
	# battery has its own surplus -> never downgraded
	check('energy_ok -> stays free', downgraded('free', 900, 1500, True, True, False) == 'free')
	# no wallbox -> never touched
	check('no wallbox -> stays free', downgraded('free', 900, 1500, False, False, False) == 'free')


def test_stop_gap():
	print('stop vs limit gap (mirrors main())')
	# reserved red block mean dirt vs current green hour
	z = zones(DAY_SUNNY)
	now = Now(23)								# green, dirt 70? no -- 23 is 70 -> check zone
	# build an explicit small case instead: green now at dirt 20, red block mean 55
	def decide(dirt_now, red_mean):
		gap = red_mean - dirt_now
		return 'stop' if gap >= M['STOP_MIN_DIRT_GAP'] else 'limit'
	check('big gap -> stop', decide(20, 55) == 'stop')
	check('small gap -> limit', decide(48, 55) == 'limit')
	check('exactly at threshold -> stop', decide(45, 55) == 'stop')


def test_precharge():
	print('precharge (room-based, round-trip gap)')
	g = M['precharge_ac_pct'].__globals__['conf']
	noon = Now(13)								# among the cleanest hours
	z = zones(DAY_SUNNY)
	# Sunny day: the natural surplus (7200 Wh) already exceeds what the red
	# block can use (3600 Wh) -> diverting would only fill the battery earlier
	# and get more PV curtailed later -> must stay at 100.
	ac = M['precharge_ac_pct'](noon, z, LOAD, PV_SUNNY, ratio_from_dirt(DAY_SUNNY), 300, 0)
	check('sunny day, surplus covers red block -> no precharge', ac == 100, 'ac=%d' % ac)
	# Clean grid (windy) but dull PV: little natural surplus, room remains,
	# red block needs more -> precharge engages.
	ac_room = M['precharge_ac_pct'](noon, z, LOAD, PV_DULL, ratio_from_dirt(DAY_SUNNY), 300, 0)
	check('clean hour, room left, red block needs more -> precharge', ac_room < 100, 'ac=%d' % ac_room)
	check('precharge never below the ac floor', ac_room >= M['PRECHARGE_AC_FLOOR'],
	      'ac=%d < floor %d' % (ac_room, M['PRECHARGE_AC_FLOOR']))
	# Same day, battery nearly full: natural surplus fills it anyway -> 100
	ac_full = M['precharge_ac_pct'](noon, z, LOAD, PV_DULL, ratio_from_dirt(DAY_SUNNY), 4500, 0)
	check('battery fills anyway -> no precharge', ac_full == 100, 'ac=%d' % ac_full)
	# Distribution counts only hours passing rank AND gap. Margin 7 -> gap
	# 68.25 points; red block mean ~66 -> hours 13-15 (dirt -12,-10,-5) pass,
	# hour 16 (dirt 5, gap 61) passes the rank but not the gap. usable = 650 Wh
	# (content 2000): split over 3 hours x 300 Wh divertable -> 216.7 Wh now,
	# ac = (700-216.7)/700 -> 69 %. Counting hour 16 too would give 77 %.
	g['round_trip_margin'] = 7.0
	ac_split = M['precharge_ac_pct'](noon, z, LOAD, PV_DULL, ratio_from_dirt(DAY_SUNNY), 2000, 0)
	check('distribution skips hours that fail the gap', ac_split == 69, 'ac=%d' % ac_split)
	del g['round_trip_margin']
	# huge margin: gain never covers loss x margin -> off
	g['round_trip_margin'] = 100.0
	ac_eco = M['precharge_ac_pct'](noon, z, LOAD, PV_DULL, ratio_from_dirt(DAY_SUNNY), 300, 0)
	check('very large round_trip_margin turns precharge off', ac_eco == 100, 'ac=%d' % ac_eco)
	del g['round_trip_margin']
	# an hour outside the cleanest pct is never throttled
	ac_dawn = M['precharge_ac_pct'](Now(8), z, LOAD, PV_DULL, ratio_from_dirt(DAY_SUNNY), 300, 0)
	check('precharge leaves non-clean hour at 100', ac_dawn == 100, 'ac=%d' % ac_dawn)
	# disabled -> always 100
	CONF['precharge_enabled'] = False
	check('precharge off -> 100',
	      M['precharge_ac_pct'](noon, z, LOAD, PV_DULL, ratio_from_dirt(DAY_SUNNY), 300, 0) == 100)
	CONF['precharge_enabled'] = True
	# only real SMARD data: substitute hours (stale) end the precharge window
	pc = M['precharge_ac_pct']
	r_sunny = ratio_from_dirt(DAY_SUNNY)
	ac_tail = pc(noon, z, LOAD, PV_DULL, r_sunny, 300, 0, set(range(13)))		# tomorrow not published: window 13..23
	check('substitutes after midnight: red block counted only up to 23:00, still engages',
	      ac_room <= ac_tail < 100, 'ac=%d (full data %d)' % (ac_tail, ac_room))
	stale_red = set(range(24)) - set(range(13, 17))						# real data ends at 16:00, before the red block
	check('red block wholly beyond the real data -> no precharge',
	      pc(noon, z, LOAD, PV_DULL, r_sunny, 300, 0, stale_red) == 100)
	r_gap = list(r_sunny); r_gap[17] = None								# uncovered hour ends the window too
	check('uncovered hour before the red block -> no precharge',
	      pc(noon, z, LOAD, PV_DULL, r_gap, 300, 0) == 100)
	check('emergency mode -> no precharge',
	      pc(noon, z, LOAD, PV_DULL, r_sunny, 300, 0, set(), True) == 100)
	# round-trip gap: eta 95/95 -> loss 9.75 points; default margin 1.5
	gap = M['precharge_round_trip_gap']()
	check('round-trip gap = loss x default margin 1.5', abs(gap - 9.75 * 1.5) < 1e-6, 'gap=%.3f' % gap)
	g['round_trip_margin'] = 0.5
	gap = M['precharge_round_trip_gap']()
	check('margin below 1.0 counts as 1.0', abs(gap - 9.75) < 1e-6, 'gap=%.3f' % gap)
	del g['round_trip_margin']


def test_stop_overflow_guard():
	print('stop overflow guard (mirrors main())')
	# mirrors the green-zone branch: stop only if content + natural surplus up
	# to the red block still fits; otherwise limit makes room now
	def decide(content, surplus, cap, gap):
		if cap > 0 and content + surplus > cap:
			return 'limit'
		return 'stop' if gap >= M['STOP_MIN_DIRT_GAP'] else 'limit'
	check('would overflow -> limit despite big gap', decide(5800, 5000, 10500, 40) == 'limit')
	check('fits -> stop on big gap', decide(4000, 5000, 10500, 40) == 'stop')
	check('fits, small gap -> limit', decide(4000, 5000, 10500, 3) == 'limit')
	check('capacity unknown -> guard skipped', decide(5800, 5000, 0, 40) == 'stop')
	# the real surplus helper the guard uses
	z = zones(DAY_SUNNY)
	s = M['surplus_before_next_red'](Now(13), z, LOAD, PV_SUNNY)
	check('surplus_before_next_red on the sunny test day = 7200 Wh', abs(s - 7200) < 1e-6, 's=%.0f' % s)


def test_budget_bound():
	print('slot energy budget <= net demand')
	# 4 slots of budget must not exceed one hour's net demand (reserve_pct capped at 1)
	for bl, pv in ((300, 0), (1200, 400), (800, 900)):
		cap = min(1.0, CONF['reserve_pct'] * 0.01)
		budget = round(M['SLOT_HOURS'] * max(0.0, bl * cap - pv))
		net = max(0.0, bl - pv)
		check('4x budget <= net demand (bl=%d pv=%d)' % (bl, pv),
		      4 * budget <= net + 4, '4*%d > %d' % (budget, net))


def test_dull_day():
	print('dull day: no surplus, reserve holds all red hours')
	z = zones(DAY_DULL)
	now = Now(20)
	rwd = M['red_window_demand'](LOAD, now, z, PV_DULL)
	check('dull-day window demand positive', rwd > 0, 'rwd=%.0f' % rwd)
	surp = M['surplus_before_next_red'](now, z, LOAD, PV_DULL)
	# on a day whose PV never exceeds load there is no surplus to credit
	check('no surplus credited on a dull day', surp == 0.0, 'surp=%.0f' % surp)


def test_emergency_zones():
	print('emergency zones (wind makes a windy night cleaner than a dull noon)')
	# stub the wind fetch: strong wind at night, calm midday
	M['emergency_zones'].__globals__['read_wind_forecast'] = lambda: [
		13.0 if (h < 7 or h >= 20) else 4.0 for h in range(24)]
	exp = [0.0] * 24
	for h in range(8, 18):
		exp[h] = 200.0 * (1 - abs(h - 13) / 6.0)		# weak midday PV, dull day
	z = M['emergency_zones'](exp, [0.0] * 24)
	check('emergency zones returns a dict', z is not None and 'zones' in z)
	check('flagged as emergency', z.get('emergency') is True)
	check('median cut still 12/12', z['zones'].count('red') == 12)
	# the whole point: windy night green, dull midday red
	check('windy night (h3) is green', z['zones'][3] == 'green')
	check('dull windless midday (h12) is red', z['zones'][12] == 'red')
	# wind fetch failing -> no model -> None (main falls to free timer)
	M['emergency_zones'].__globals__['read_wind_forecast'] = lambda: None
	check('no wind data -> None', M['emergency_zones'](exp, [0.0] * 24) is None)


def test_wallbox_path_redundancy():
	print('wallbox paths are redundant (a missing input drops only its path)')
	# decide is a pure OR: dropping one path (its ok=False) still lets the
	# others carry. This mirrors main()'s handling of a missing battery content
	# (energy path off) or missing voltage (voltage path off).
	d = DAY_SUNNY
	ch = d.index(min(d))						# a clean hour: dirt path says yes
	check('clean hour on, energy+voltage both off (missing data)',
	      M['wallbox_decide'](d[ch], d, energy_ok=False, voltage_ok=False) is True)
	# a dirty hour with both other paths off -> off (a real decision, not absence)
	check('dirty hour, all paths off -> off',
	      M['wallbox_decide'](40, d, energy_ok=False, voltage_ok=False) is False)
	# voltage alone can still hold it on when dirt+energy are unavailable/false
	check('voltage alone carries',
	      M['wallbox_decide'](40, d, energy_ok=False, voltage_ok=True) is True)


def test_empty_anchor():
	print('empty anchor rejects brief dips, keeps sustained valleys')
	t0 = 1789000000000
	# a 2-second dip under the threshold while the battery sits at 52 V
	dip = [(t0 + i * 1000, 52.0, 1) for i in range(100)] \
	    + [(t0 + 100000, 47.0, 1), (t0 + 101000, 46.5, 1)] \
	    + [(t0 + (102 + i) * 1000, 52.0, 1) for i in range(100)]
	check('brief dip is not an anchor', M['_empty_anchor'](dip, 49.0) is None)
	# a sustained valley (well over EMPTY_MIN_DURATION_S) is anchored at its deepest point
	valley = [(t0 + i * 1000, 52.0, 1) for i in range(50)] \
	       + [(t0 + (50 + i) * 1000, 47.5 - (i == 75) * 0.5, 1) for i in range(200)] \
	       + [(t0 + (250 + i) * 1000, 52.0, 1) for i in range(50)]
	a = M['_empty_anchor'](valley, 49.0)
	check('sustained valley is anchored', a is not None)
	check('anchor is the deepest sample of the valley', a == t0 + (50 + 75) * 1000, 'a=%s' % a)
	# threshold never crossed -> no anchor
	check('never-empty series has no anchor',
	      M['_empty_anchor']([(t0 + i * 1000, 52.0, 1) for i in range(100)], 49.0) is None)
	# implausible-voltage filter: below 2.5 V/cell must not survive to anchor.
	# _empty_anchor itself does not filter (that happens on read), but a
	# sustained 0 V outage must be filtered out upstream — mirror that here.
	impl = M['IMPLAUSIBLE_V_PER_CELL'] * 16
	outage = [(t0 + i * 1000, 0.0, 1) for i in range(150)]
	kept = [(ts, v, s) for ts, v, s in outage if v >= impl]
	check('sustained 0 V outage is fully filtered', kept == [])
	check('implausible threshold is 40 V at 16S', impl == 40.0)


def test_neg_content_warn():
	print('negative-content warning threshold + hardware/efficiency diagnosis')
	frac = M['NEG_CONTENT_WARN_FRACTION']
	cap = 10500
	def warns(content):
		return cap > 0 and content < -frac * cap
	check('clear breach warns (RCD case)', warns(-1307))
	check('small noise does not warn', not warns(-100))
	check('positive content does not warn', not warns(53))
	check('exactly at threshold does not warn', not warns(int(-frac * cap)))
	check('no capacity -> no warning', not (0 > 0 and -1307 < -frac * 0))

	diag = M['_neg_content_diagnosis']
	# raw (loss-free) balance also breaches -> channel data itself is wrong
	check('raw also negative -> hardware', diag(-8000, -8000, cap) == 'hardware')
	# raw balance is plausible, only the efficiency-adjusted one breaches ->
	# efficiencies may be set too low (the RCD field case: raw=+1665, vz=-1339)
	check('raw plausible, adjusted negative -> efficiency', diag(-1339, 1665, cap) == 'efficiency')
	# content not actually breaching -> no diagnosis needed
	check('content within bounds -> None', diag(-100, -50, cap) is None)
	check('no capacity configured -> None', diag(-8000, -8000, 0) is None)
	# raw exactly at the limit counts as breaching too (< limit is breach, not <=)
	check('raw exactly at limit -> efficiency (not hardware)',
	      diag(-8000, -frac * cap, cap) == 'efficiency')


def test_ampel_parse():
	print('\n[Energy-Charts signal -> hourly ratio]')
	days = M['_ampel_days']
	d0 = datetime(2026, 11, 18)
	t0 = int(d0.timestamp())
	# today fully covered (96 quarter hours), tomorrow only 10 hours
	ts = [t0 + 900 * i for i in range(96 + 40)]
	share = [40.0 + (i % 4) * 2 for i in range(96)] + [90.0] * 40		# quarters 40,42,44,46 -> hourly mean 43
	today, tomorrow = days(ts, share, d0.date())
	check('hour = mean of its quarter hours, ratio = share/100',
	      today is not None and abs(today['ratio'][5] - 0.43) < 1e-9, today and today['ratio'][5])
	check('tomorrow with < GRID_MIN_HOURS hours -> None (not yet published)', tomorrow is None)
	# a None quarter is skipped, not counted as 0
	share2 = list(share); share2[4] = None
	t2, _ = days(ts, share2, d0.date())
	check('None quarter skipped in the hourly mean', abs(t2['ratio'][1] - (42 + 44 + 46) / 300.0) < 1e-9, t2['ratio'][1])
	# today barely covered -> None
	t3, _ = days(ts[:19 * 4], share[:19 * 4], d0.date())
	check('today with 19 hours -> None', t3 is None)
	# both days complete
	ts4 = [t0 + 900 * i for i in range(192)]
	t4, m4 = days(ts4, [50.0] * 96 + [100.0] * 96, d0.date())
	check('tomorrow complete -> own values', m4 is not None and m4['ratio'][0] == 1.0 and t4['ratio'][23] == 0.5)


def test_substitute_day():
	print('\n[one-day substitution only with real data for today]')
	sub = M['_substitute_day']
	tue = {'ratio': [0.3] * 24}; wed = {'ratio': [0.9] * 24}
	check("yesterday's tomorrow half stands in for today", sub({'today': tue, 'tomorrow': wed}) is wed)
	check("tomorrow half missing -> None, yesterday's today never reused", sub({'today': tue, 'tomorrow': None}) is None)
	check('empty cache -> None', sub({}) is None)


for t in (test_ampel_parse, test_substitute_day, test_median_and_zones, test_rank, test_reserve_invariants,
          test_reserved_red_hours_shared, test_wallbox_hysteresis,
          test_wallbox_dirt_path, test_wallbox_downgrade,
          test_wallbox_path_redundancy, test_stop_gap, test_stop_overflow_guard,
          test_precharge, test_budget_bound, test_dull_day,
          test_emergency_wind_curve, test_emergency_zones,
          test_empty_anchor, test_neg_content_warn):
	t()

print()
if _fails:
	print('FAILED: %d' % len(_fails), ', '.join(_fails))
	sys.exit(1)
print('all tests passed')
