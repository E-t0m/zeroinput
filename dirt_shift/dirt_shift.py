#!/usr/bin/python3
# -*- coding: utf-8 -*-
# indent size 4, mode Tabs
#
# dirt_shift.py — shift battery discharge into grid hours of high CO2 intensity
#
# Motivation: the CO2 intensity of the German grid mix varies over the day. It is
# higher in the evening and at night (PV gone, evening load high, fossil peaking)
# and in the early morning until enough PV is available; it is lower around midday.
# Where one can choose WHEN the battery covers the load instead of the grid, doing
# so during the high-intensity hours avoids the most CO2. The available battery
# content is therefore directed into those hours, most strongly into the highest-
# intensity ones.
#
# CO2 intensity is a property of the GRID and does not depend on the installation.
# The installation (PV, consumption, battery content) only determines the AMOUNT
# of energy available and required overnight.
#
# This is a standalone tool in the same family as tib_zero_tas.py and reuses its
# proven volkszähler queries (get_average/basic_load, get_vz_bat_cap) and the
# timer.txt interface to zeroinput. It is an alternative to the price-driven
# tibber tool (both write the same timer.txt — do not run them together). No grid
# data is fetched — the intensity profile is derived from the computed sun
# position plus fixed load bounds.
#
# Concept (see comments at each step):
#   1. intensity profile: three zones from the clean (low-intensity) window, a
#      hybrid of sun position and fixed clock bounds (see _green_bounds):
#        high   (red)    — outside the clean window: evening, night, early morning
#        medium (yellow) — transition band on both edges of the clean window
#        low    (green)  — midday
#   2. discharge by zone: high -> no limit; medium -> single-inverter cap;
#      low -> no battery discharge (pvpt only).
#   3. night reserve = reserve_pct (~90%) * basic_load over the coming high-
#      intensity (red) window, protected from build_reserve_after on.
#   4. build_reserve_after (~13:30): before it, discharge freely into whatever is
#      high-intensity now; from it, surplus above the reserve may still go out,
#      but once content drops to the reserve, discharge stops until the night red
#      window begins.
#   5. pvpt (direct PV pass-through) is always granted, independent of all this.
#   6. runs every 1/4h, re-writing timer.txt with fresh battery content and zone.

from json import load as json_load
from json import dump as json_dump
from os.path import join, dirname
from datetime import datetime, timedelta
from requests import get
from sys import argv as sys_argv

if '-h' in sys_argv or '--help' in sys_argv:
	print(' -v\t\tverbose console output\n', '-html\t\thtml header/footer\n',
	      '-debug\t\tmore output\n', '-avgnew\t\tforce a fresh 7-day average query')
	exit(0)

def write_free_timer(path):
	"""On any hard error, write an 'all allowed' timer so zeroinput is never
	blocked by a stale/missing dirt_shift limit: full discharge, full pvpt,
	practically unlimited energy, daily-repeating from midnight."""
	try:
		with open(path, 'w') as fo:
			fo.write('# %s  (dirt_shift FALLBACK — config/data error, no limit)\n'
			         % datetime.now().strftime('%Y-%m-%dT%H:%M:%S'))
			fo.write('0000-00-00 00:00:00 100 100 99999\n')
	except Exception:
		pass


def die(msg, timer_path=None):
	"""Hard abort. If the timer path is known, leave an all-allowed timer."""
	print('dirt_shift: %s' % msg)
	if timer_path:
		write_free_timer(timer_path)
	exit(1)


try:
	with open(join(dirname(__file__), 'dirt_shift.conf'), 'r') as fi:
		conf = json_load(fi)
except Exception:
	# dirt_shift.conf itself is missing/broken — the timer path is unknown,
	# so only a plain abort is possible.
	print('dirt_shift: error reading config file dirt_shift.conf')
	exit(1)

# pull shared values from zeroinput.conf (read-only, never duplicated here):
#   single_inverter_threshold  — keep night discharge on stage 1
#   discharge_t_file           — the timer file zeroinput reads (we write it)
#   cell_count                 — battery cell count for the empty-voltage anchor
try:
	_zi_path = join(dirname(__file__), conf['zeroinput_conf'])
	with open(_zi_path, 'r') as fi:
		_zi = json_load(fi)
	conf['single_inverter_threshold'] = _zi['single_inverter_threshold']
	conf['cell_count'] = _zi.get('cell_count', 16)		# default 16S, as in zeroinput
	conf['timer.txt'] = join(dirname(_zi_path), _zi['discharge_t_file']) \
		if not _zi['discharge_t_file'].startswith('/') else _zi['discharge_t_file']
except Exception as e:
	# zeroinput.conf unreadable or missing the needed keys — timer path unknown.
	print('dirt_shift: cannot read zeroinput.conf (%s): %s' % (conf.get('zeroinput_conf', '?'), e))
	exit(1)

verbose = '-v' in sys_argv
avgnew  = '-avgnew' in sys_argv
html    = '-html' in sys_argv
if '-debug' in sys_argv: verbose = True; debug = True
else: debug = False

if verbose and html:
	print('<!DOCTYPE html><html><head><meta charset="UTF-8"><style>body {font-size:200%;'
	      'color:#BBBBBB;background-color:#111111;} pre {margin:0px;}</style></head><body><pre>\n')


# ── volkszähler queries (reused from tib_zero_tas.py, PV added) ────────────────

def get_average(n_days):
	"""Hourly 7-day averages from volkszähler.
	Returns dict with 'basic_load' (real home consumption Wh/h) and 'PV' (Wh/h)."""
	hours = {}
	counted_days = 0
	keys = ['Inverter', 'Import', 'Auto', 'PV']
	for key in keys: hours[key] = [0.0] * 24

	if verbose: print('query volkszähler for %i day consumption+PV data:' % n_days)
	uuid2key = {conf['vz_chans'][k]: k for k in keys}			# O(1) uuid -> channel name

	# per-day weighting (percent), chronological: index 0 = oldest day, -1 = yesterday.
	# The loop runs day=0 (yesterday) .. day=n_days-1 (oldest), so weights are
	# indexed in reverse: loop day -> weights[n_days-1-day].
	weights_pct = conf.get('day_weights_pct')
	if not weights_pct or len(weights_pct) != n_days:
		if weights_pct is not None and verbose:
			print('day_weights_pct length %s != average_days %i — using equal weights'
			      % (len(weights_pct) if weights_pct else 0, n_days))
		weights_pct = [100] * n_days

	weight_sum = 0.0
	for day in range(0, n_days):
		begin = (datetime.today() - timedelta(days=day, hours=24)).replace(minute=0, second=0, microsecond=0)
		end   = (datetime.today() - timedelta(days=day, hours=0 )).replace(minute=0, second=0, microsecond=0)
		beginstamp = str(int(begin.timestamp())).ljust(13, '0')
		endstamp   = str(int(end.timestamp())).ljust(13, '0')
		url = 'http://' + conf['vz_host_port'] + '/data.json?from=' + beginstamp + '&to=' + endstamp + '&group=hour'
		for key in keys: url += '&uuid[]=' + conf['vz_chans'][key]

		if verbose:
			print(day, '\tbegin', begin, '\tend', end, '\t', end='')
		jresp = get(url=url).json()

		# a complete day reports 26 rows: 24 hourly values + the average + the
		# consumption summary row that volkszähler appends per channel.
		if jresp['data'][0]['rows'] == 26:				# only complete days
			counted_days += 1
			w = weights_pct[n_days - 1 - day] * 0.01	# this day's weight factor
			weight_sum += w
			for row in jresp['data']:
				chan_n = uuid2key.get(row['uuid'])
				if chan_n is None: continue
				for value in row['tuples']:
					tval = datetime.fromtimestamp(value[0] / 1000)
					if tval > end: continue				# drop next-day spill
					hours[chan_n][tval.hour] += value[1] * w
		if verbose: print('rows:', jresp['data'][0]['rows'], ':', counted_days)

	if counted_days == 0:
		die('no complete days returned by volkszähler', conf['timer.txt'])

	for i in range(0, 24):
		for key in keys: hours[key][i] /= weight_sum			# weighted mean over the complete days

	hours['basic_load'] = [0.0] * 24
	for i in range(0, 24):
		hours['basic_load'][i] = (hours['Import'][i] + abs(hours['Inverter'][i])
		                     - hours['Auto'][i])		# real consumption Wh; Auto is a separately metered plannable load and removed. Demand-driven loads (e.g. air conditioning) stay in: they are part of the load to cover and captured by the 7-day average.

	if debug:
		print('\nhour\tbasic_load\tPV')
		for i in range(0, 24): print('%i\t%.0f\t%.0f' % (i, hours['basic_load'][i], hours['PV'][i]))
	return hours


def read_average():
	"""Cached hourly averages (basic_load + PV), refreshed once per hour."""
	vz_in = {}
	if not avgnew:
		try:
			with open(join(dirname(__file__), 'dirt_avg_cache.json'), 'r') as fi:
				vz_in = json_load(fi)
		except Exception:
			vz_in['timestamp'] = 1000000.123456
	if avgnew or datetime.fromtimestamp(vz_in['timestamp']).strftime('%Y-%m-%d %H') != datetime.now().strftime('%Y-%m-%d %H'):
		avg = get_average(conf['average_days'])
		vz_in['basic_load'] = avg['basic_load']
		vz_in['PV']    = avg['PV']
		vz_in['timestamp'] = datetime.now().timestamp()
		with open(join(dirname(__file__), 'dirt_avg_cache.json'), 'w') as fo:
			json_dump(vz_in, fo)
	else:
		if verbose: print('using cached averages from',
		                  datetime.fromtimestamp(vz_in['timestamp']).strftime('%Y-%m-%d %H:%M'))
	return vz_in


def get_vz_bat_cap():
	"""Reconstruct real battery energy content (Wh) by integrating PV and
	Inverter since the last known 'empty' state (voltage <= 3.0625 V/cell, i.e.
	49 V at 16 cells, scaled by cell_count). Reused from
	tib_zero_tas.py. Returns (latest_voltage, content_Wh)."""
	if verbose: print(datetime.now().strftime('%Y-%m-%d %H:%M'), 'query volkszähler for energy content:')
	days_back = 0
	latest_voltage = 0.0					# guard: set on the first (days_back==0) query below
	end = datetime.today().replace(microsecond=0)
	endstamp = str(int(end.timestamp())).ljust(13, '0')

	while True:
		begin = (datetime.today() - timedelta(days=days_back)).replace(hour=0, minute=0, second=0, microsecond=0)
		beginstamp = str(int(begin.timestamp())).ljust(13, '0')
		url = 'http://' + conf['vz_host_port'] + '/data.json?from=' + beginstamp + '&to=' + endstamp + '&uuid[]=' + conf['vz_chans']['Vbat']
		try:
			jresp = get(url=url).json()
			tuples = jresp['data'][0]['tuples']
		except Exception:
			die('battery voltage data unusable', conf['timer.txt'])
		if not tuples:
			die('no battery voltage data returned by volkszähler', conf['timer.txt'])
		if days_back == 0: latest_voltage = tuples[-1][1]
		_empty_v = 3.0625 * conf.get('cell_count', 16)		# 16S original: 49 V (empty-battery anchor)
		if jresp['data'][0].get('min') and jresp['data'][0]['min'][1] <= _empty_v: break	# empty battery anchor
		if days_back >= conf['max_days_empty_battery']:
			if verbose: print(days_back, '\tno empty battery state found')
			break
		days_back += 1

	min_v = 999
	min_ts = None
	for ts, v, s in jresp['data'][0]['tuples']:
		if v <= min_v: min_ts = ts; min_v = v
	if min_ts is None:
		die('no usable battery voltage samples', conf['timer.txt'])

	begin = datetime.fromtimestamp(min_ts / 1000)
	end   = datetime.today().replace(microsecond=0)
	beginstamp = str(min_ts).ljust(13, '0')
	endstamp   = str(int(end.timestamp())).ljust(13, '0')
	url = 'http://' + conf['vz_host_port'] + '/data.json?from=' + beginstamp + '&to=' + endstamp
	for key in ['Inverter', 'PV']: url += '&uuid[]=' + conf['vz_chans'][key]

	try:
		jresp = get(url=url).json()
	except Exception:
		die('battery capacity data unusable', conf['timer.txt'])

	vz_bat_cap = 0.0
	for row in jresp['data']:
		if row['uuid'] == conf['vz_chans']['PV']:
			vz_bat_cap += abs(row['consumption']) * conf['PV_to_bat_efficiency'] * 0.01
		elif row['uuid'] == conf['vz_chans']['Inverter']:
			vz_bat_cap += row['consumption'] / (conf['bat_to_AC_efficiency'] * 0.01)		# AC output -> battery energy removed (loss divided back in)

	vz_bat_cap *= conf['bat_to_AC_efficiency'] * 0.01
	if verbose: print('min voltage %.1f V, latest %.1f V, battery content %.0f Wh' % (min_v, latest_voltage, vz_bat_cap))
	return latest_voltage, int(vz_bat_cap)


# ── dirt_shift core logic (verified separately) ───────────────────────────────

def sun_times(d):
	"""Sunrise/sunset (local clock hours, float) for the configured location on
	date d. Plain astronomical approximation (no library, ~1-2 min accurate,
	irrelevant against the hour-scale offsets). Equation of time neglected."""
	import math, time as _t
	lat = conf.get('latitude', 51.0)
	lon = conf.get('longitude', 10.0)
	is_dst = _t.localtime(_t.mktime(d.timetuple())).tm_isdst
	tz = (-_t.altzone if is_dst else -_t.timezone) / 3600.0		# local UTC offset incl. DST
	N = d.timetuple().tm_yday
	decl = math.radians(23.45) * math.sin(math.radians(360.0 / 365.0 * (N - 81)))
	lat_r = math.radians(lat)
	cosH = -math.tan(lat_r) * math.tan(decl)
	if cosH >= 1:   return None, None				# polar night: sun never rises
	if cosH <= -1:  return 0.0, 24.0				# polar day: sun never sets
	H = math.degrees(math.acos(cosH))
	solar_noon = 12.0 - lon / 15.0 + tz
	return solar_noon - H / 15.0, solar_noon + H / 15.0


def _green_bounds(now):
	"""Start and end (local clock hours) of the clean (green) grid window for the
	date of 'now', as a hybrid of sun position and fixed clock bounds:
	  green_start = max(sunrise + green_morning_offset_h, green_earliest)
	  green_end   = min(sunset  - red_evening_offset_h,   green_latest)
	The grid only turns clean once enough PV is up relative to the load. In summer
	the load (clock-driven: morning ramp-up, evening rise) bounds the window, so
	the fixed hours dominate; in winter the later sunrise / earlier sunset
	dominates and the green window shrinks. Returns (green_start, green_end) or
	None for a polar night (no green at all)."""
	sr, ss = sun_times(now)
	if sr is None:
		return None									# polar night: never green
	gm = conf.get('green_morning_offset_h', 3.5)
	re = conf.get('red_evening_offset_h', 3.0)
	ge = conf.get('green_earliest', 9.0)
	gl = conf.get('green_latest', 17.5)
	return max(sr + gm, ge), min(ss - re, gl)


def dirtiness_zone(now):
	"""Grid CO2-intensity zone at 'now', from the clean (low-intensity) window plus
	a transition band on both edges. Returns 'red' (high intensity, no discharge
	limit), 'yellow' (transition, single-inverter cap) or 'green' (low intensity,
	no battery discharge)."""
	gb = _green_bounds(now)
	if gb is None:           return 'red'			# polar night: continuously high intensity
	green_start, green_end = gb
	yw = conf.get('yellow_width_h', 1.0)
	h = now.hour + now.minute / 60.0
	if green_start + yw <= h <= green_end - yw:		return 'green'
	if green_start <= h < green_start + yw:			return 'yellow'
	if green_end - yw < h <= green_end:				return 'yellow'
	return 'red'


def red_window_demand(basic_load, now):
	"""Wh the basic_load draws over the coming contiguous high-intensity (red)
	window — from the evening clean-window end through the night to next morning's
	clean-window start. Sizes the night reserve."""
	gb = _green_bounds(now)
	if gb is None:
		return sum(basic_load)						# polar night: whole day is red
	green_start, green_end = gb
	red_start = int(green_end % 24)					# evening: grid turns red
	red_end   = int(green_start % 24)				# next morning: grid turns green
	demand, h = 0.0, red_start
	while h != red_end:
		demand += basic_load[h]; h = (h + 1) % 24
	return demand


def main():
	vz = read_average()
	basic_load = vz['basic_load']
	_voltage, content = get_vz_bat_cap()

	single_inv = conf['single_inverter_threshold']
	now = datetime.now()
	zone = dirtiness_zone(now)

	# the night reserve: basic_load demand over the coming contiguous high-intensity
	# (red) window, scaled by reserve_pct. Protected from build_reserve_after on.
	reserve = conf['reserve_pct'] * 0.01 * red_window_demand(basic_load, now)

	# decide this run's discharge mode from the CO2-intensity zone and the reserve:
	#   red    -> high intensity: discharge with no limit (full output)
	#   yellow -> transition: discharge capped to a single inverter
	#   green  -> low intensity: no battery discharge (pvpt only)
	# build_reserve_after: before that time the battery may run down freely into
	# whatever is high-intensity now (incl. the morning red window) — no reserve is
	# held. From that time on the night reserve is protected: any surplus above it
	# may still go out into high-intensity hours, but once content has dropped to
	# the reserve, discharge stops until the night red window itself begins (where
	# the reserve is then spent, red = no limit / yellow = single inverter).
	after = conf['build_reserve_after']
	hh, mm = (int(x) for x in after.split(':'))
	protect_reserve = (now.hour, now.minute) >= (hh, mm)

	if zone == 'green':
		mode = 'stop'										# low intensity: never discharge
	elif protect_reserve and zone != 'red' and content <= reserve:
		mode = 'stop'										# hold the reserve for the night red window
	elif zone == 'red':
		mode = 'free'										# high intensity: no limit
	else:
		mode = 'limit'										# yellow: single-inverter cap

	if verbose:
		sr, ss = sun_times(now)
		srs = '%05.2f' % sr if sr is not None else 'n/a'
		sss = '%05.2f' % ss if ss is not None else 'n/a'
		print('\ndirt_shift  %s' % now.strftime('%Y-%m-%d %H:%M:%S'))
		print('sun rise %s set %s   zone %s   content %d Wh   night reserve(%d%%) %.0f Wh' % (
			srs, sss, zone, content, conf['reserve_pct'], reserve))
		print('build_reserve_after %s -> reserve %s   => discharge mode: %s' % (
			after, 'protected' if protect_reserve else 'open', mode.upper()))

	if not conf['disable_zeroinput_timer']:
		write_timer(mode, now, single_inv)

	if verbose:
		print('done.', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
		if html: print('\n</pre></body></html>')
	return 0


def write_timer(mode, now, single_inv):
	"""Write timer.txt in the zeroinput format:
	  date time | discharge_W  ac_W  energy_Wh   (<=100 = percent, >100 = watt)
	Daily-repeating with the 0000-00-00 date scheme. pvpt (ac 100%) is always
	granted; only battery discharge is steered by 'mode':
	  free  -> no discharge limit (high-intensity hours)  '100 100 99999'
	  limit -> single-inverter cap (transition)           '<single_inv> 100 99999'
	  stop  -> no discharge, pvpt only                    '000 100 000'
	dirt_shift is optional and must never block normal operation, so the plan is
	a short chain re-written every run:
	  - the current 1/4h slot in the chosen mode;
	  - if the mode limits/stops discharge, an 'all allowed' line 30 min later as
	    a failsafe — renewed every run while the script lives, self-lifting after
	    30 min if it dies."""
	FREE  = '100 100 99999'								# full discharge, full pvpt
	LIMIT = '%3d 100 99999' % single_inv				# single-inverter cap
	STOP  = '000 100 000'								# pvpt only, no discharge
	payload = {'free': FREE, 'limit': LIMIT, 'stop': STOP}[mode]

	lines = []
	t = now.replace(second=0, microsecond=0, minute=(now.minute // 15) * 15)
	lines.append((t.hour, t.minute, payload))
	if mode != 'free':									# failsafe: lift any limit/stop after 30 min
		t2 = t + timedelta(minutes=30)
		lines.append((t2.hour, t2.minute, FREE))

	with open(conf['timer.txt'], 'w') as fo:
		fo.write('# %s  (dirt_shift)\n' % datetime.now().strftime('%Y-%m-%dT%H:%M:%S'))
		fo.write('# 0000-00-00 for daily repeating, space or tab separated\n')
		fo.write('#                   battery discharge W if > 100, percentage if <= 100\n')
		fo.write('# date     time     |   ac inverter power W if > 100, percentage if <= 100\n')
		fo.write('# |        |        |   |   energy limit in Wh\n')
		for hh, mm, p in lines:
			line = '0000-00-00 %02d:%02d:00 %s' % (hh, mm, p)
			fo.write(line + '\n')
			if debug: print(line)


exit(main())
