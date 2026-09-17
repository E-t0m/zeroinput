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
# This is a standalone tool. It queries volkszähler for basic_load/PV averages
# and battery content, derives the hourly CO2-intensity profile from SMARD
# day-ahead grid data (a prerequisite — no other zone source exists), and
# writes the same timer.txt interface zeroinput reads for discharge control.
#
# Concept (see comments at each step):
#   1. intensity profile: SMARD's forecasted wind+solar generation and load
#      give an hourly renewables/load ratio; each day's hours are split at
#      their own median into two zones (see _smard_zones_for_date):
#        red   (dirtier half of the day)
#        green (cleaner half of the day)
#      If the SMARD fetch fails, cached data substitutes for one more day;
#      beyond that dirt_shift aborts, leaving an all-allowed timer.
#   2. red reserve = reserve_pct (~90%) * basic_load over every red hour
#      between now and the next PV surplus phase (the point the battery
#      genuinely refills from).
#   3. green: charge, never discharge, until content exceeds the reserve;
#      then free discharge until content drops back to it.
#      red: free discharge if content already covers the reserve. If it
#      falls short, the single dirtiest red hour in the window is served
#      unrestricted (see dirtiest_hour) — every other red hour in the window
#      is capped by two independent limits at once instead: a discharge-rate
#      cap (limit_discharge_rate, Watt, config) and a fixed quarter-hour
#      energy budget (1/4 * (reserve_pct * basic_load - expected PV) for
#      that hour, Wh — scaled by reserve_pct like the reserve itself, net
#      of the PV still expected in it). Together they
#      force both a brief high-power spike (e.g. EV charging) and a smaller
#      but sustained excess load onto the grid, preserving content for the
#      dirtiest hour.
#   4. pvpt (direct PV pass-through) is always granted, independent of all this.
#   5. runs every 1/4h, re-writing timer.txt with fresh battery content and zone.

from json import load as json_load
from json import dump as json_dump
from os.path import join, dirname, isabs
from datetime import datetime, timedelta
from time import time, sleep
from requests import get, post
from sys import argv as sys_argv
import sys

if '-h' in sys_argv or '--help' in sys_argv:
	print(' -v\t\tverbose console output\n', '-html\t\thtml header/footer\n',
	      '-debug\t\tmore output\n', '-avgnew\t\tforce a fresh 7-day average, PV curve, radiation forecast and SMARD query')
	exit(0)

PV_CURVE_DAYS       = 14	# days of history for the PV reference curve (see get_pv_curve); independent of average_days
PV_CURVE_PERCENTILE = 95	# percentile of the daily hourly PV values used as the reference (close to the peak without one record day skewing it)
PV_CURVE_REFRESH_HOUR = 4	# local hour from which the once-daily curve refresh may run (quiet, pre-sunrise; no same-day PV data yet to compete with)
CLEAR_SKY_A          = 1098.0	# Haurwitz (1945) clear-sky GHI model coefficient, W/m^2
CLEAR_SKY_B          = 0.059	# Haurwitz clear-sky GHI model exponent coefficient
SMARD_REGION        = 'DE'		# SMARD region code (see the SMARD API's region parameter)
SMARD_FILTER_WIND_SOLAR = 5097	# 'Prognostizierte Erzeugung: Wind und Photovoltaik' (day-ahead, combined)
SMARD_FILTER_LOAD       = 411	# 'Prognostizierter Verbrauch' (day-ahead) — less firmly confirmed than the generation filter, but the fallback below covers a wrong/broken value
SLOT_MINUTES = 15	# length of one timer slot in minutes — the cadence dirt_shift is meant to run at (cron 0,15,30,45) and the granularity of every quantity below that is 'per slot': the timer line's own timestamp, the energy budget's share of an hour, the wallbox's typical-draw margin and its voltage window. Changing it here changes all of them together; the cron entry has to be changed to match
VZ_TIMEOUT_S = 30	# HTTP timeout (seconds) on every volkszähler request. Without one, requests.get blocks forever if volkszähler accepts the connection but never answers (overloaded, wedged), and dirt_shift hangs with no error — while cron keeps starting a fresh hung process every 15 minutes. 30 s is generous for a slow answer yet well under the run interval, so a stuck query fails the run cleanly instead of hanging it
SLOT_HOURS   = SLOT_MINUTES / 60.0	# same value as a fraction of an hour, for the Wh/W conversions

WALLBOX_V_ON_PER_CELL  = 3.375	# per-cell voltage the battery must not have dropped below over WALLBOX_V_WINDOW_MIN for the voltage path to switch the wallbox ON (54.0 V at 16S) — a full battery under no meaningful discharge load
WALLBOX_V_OFF_PER_CELL = 3.25	# per-cell voltage the voltage path releases at once engaged (52.0 V at 16S) — the lower half of the hysteresis, without which the wallbox's own load would drop the voltage below the ON threshold and switch itself off again on the very next run
WALLBOX_V_WINDOW_MIN   = SLOT_MINUTES	# minutes of battery voltage history the ON/OFF thresholds are checked against (the minimum over that span, not the latest sample): 'never dropped below' captures a full battery AND the absence of a real discharge load in one test, which a momentary reading taken mid-spike would not. One slot, so each run judges the span it is itself deciding for
STOP_MIN_DIRT_GAP = 10.0	# dirt% points the red hours being reserved for must be dirtier than the current green hour before 'stop' (hold the battery, import for the house now) is worth it; below that the battery simply covers the house ('limit'). Shifting 1 Wh from now to a later hour pays off in proportion to the dirt% difference between them, so holding back across a gap inside forecast noise buys nothing and costs a grid import now. A difference, not a fraction: dirt% is signed and goes negative on windy days, where a fraction of the median would flip its own meaning
WALLBOX_ENERGY_ON_FACTOR = 2	# multiple of 'margin' (one slot of wallbox_typical_power, SLOT_HOURS) the energy path demands as headroom to switch the wallbox ON, dropped back to a single margin once it is itself holding — the hysteresis of that path. Without it, switching on with only a few Wh to spare is self-defeating: the wallbox draws roughly one margin plus house load per run, so the next run 15 min later finds the check failed and switches straight back off. Expressed as a factor rather than a fixed Wh value so it scales with the actual wallbox power — the flapping period is the run interval, so the headroom that matters is measured in run-lengths of charging.
PRECHARGE_AC_FLOOR = 20	# lowest ac_% precharge may ever write. A precharge cap only ever reduces pvpt by the watts it diverts, but this is a hard floor regardless: a timer line must never drop the inverter feed so far that zeroinput treats it as no valid feed ('timer.txt enabled but not active'). 20 leaves the inverter clearly feeding while still allowing a strong diversion
EMPTY_MIN_DURATION_S = 120	# how long battery voltage must STAY at/below the empty threshold to count as a real empty state and anchor the content integration. A brief dip — a load spike, an inverter start, a momentary outage — pulls the voltage under the threshold for a second or two while the battery is nowhere near empty; anchoring there would start the integration from a false zero and read content far too low ever after. A genuine empty state persists (the battery really is flat), a dip recovers at once, so the distinguishing feature is duration
IMPLAUSIBLE_V_PER_CELL = 2.5	# any battery voltage below this per cell (2.5 V = 40 V at 16S, the LiFePO4 discharge cut-off where every BMS disconnects) cannot be a real state — it is a sensor fault, a dropped connection, or a read error. Such samples are discarded on read, so they can neither be mistaken for an empty-battery anchor (even a sustained one, which the duration test alone would accept) nor pull latest_voltage down
NEG_CONTENT_WARN_FRACTION = 0.05	# fraction of battery_capacity_wh below zero at which a negative reconstructed content raises a warning. A content below empty is physically impossible, so a clear breach means more discharge was counted than charge since the last empty state — an inverter logging demand it did not deliver (tripped RCD, blown fuse, failed unit), or an anchor too many days back with accumulated efficiency drift. Small negatives just above zero are normal integration noise right after an empty state and must not cry wolf, hence a fraction of capacity rather than a raw 0

# ── emergency zones (SMARD unavailable beyond the one-day cache) ──────────────
# When real grid data cannot be had at all, zones are MODELLED from weather
# instead of left to the free timer: renewables = solar (the radiation forecast
# already fetched) + wind (fetched here), load = a fixed normalised profile of
# the GERMAN grid load (not the house — see EMERGENCY_LOAD_PROFILE). Only the
# SHAPE across the day matters, since the median cut ranks hours; absolute
# levels are irrelevant. Always clearly flagged; only ever a fallback.
EMERGENCY_LOAD_PROFILE = [			# normalised German grid load by local hour (0..23), ~0.6 night trough to 1.0 evening peak. Weekday shape; weekends are flatter but the ranking is similar enough for a fallback. Empirical, not site-specific
	0.62, 0.58, 0.56, 0.55, 0.57, 0.63, 0.75, 0.88, 0.95, 0.97, 0.98, 0.99,
	0.97, 0.94, 0.92, 0.91, 0.93, 0.97, 1.00, 0.98, 0.92, 0.83, 0.74, 0.67]
EMERGENCY_WIND_V_CUTIN  = 3.0	# m/s (100 m): below this a turbine produces nothing
EMERGENCY_WIND_V_RATED  = 12.0	# m/s (100 m): at/above this the fleet is treated as saturated (index 1.0); the cubic ramp runs between cut-in and rated
EMERGENCY_WIND_WEIGHT   = 1.0	# how much a saturated wind index counts against a clear-sky solar index of 1.0 — roughly equal annual shares in the German mix, so 1.0. Grid load is normalised to peak 1.0, generation to the same scale
EMERGENCY_SOLAR_WEIGHT  = 1.0	# clear-sky solar index at local noon in high summer ~1.0; wind and solar therefore both range 0..~1 and sum into a generation index compared against the 0..1 load profile

def write_free_timer(path):
	"""On any hard error, write an 'all allowed' timer so zeroinput is never
	blocked by a stale/missing dirt_shift limit: full discharge, full pvpt,
	practically unlimited energy. Dated with today's real date — once the day
	is over, zeroinput's timer parser (which applies every already-past line
	in file order, stopping only at the first future one) simply keeps this
	line's values as the last one it saw, so the free state persists on its
	own without needing to be rewritten daily."""
	try:
		with open(path, 'w') as fo:
			fo.write('# %s  (dirt_shift FALLBACK — config/data error, no limit)\n'
			         % datetime.now().strftime('%Y-%m-%dT%H:%M:%S'))
			fo.write('%s 00:00:00 100 100 -1\n' % datetime.now().strftime('%Y-%m-%d'))
	except Exception:
		pass


def die(msg, timer_path=None):
	"""Hard abort. If the timer path is known, leave an all-allowed timer so
	zeroinput is never blocked by a stale limit. Deliberately does NOT touch
	the wallbox relay: a run that cannot decide (missing data) must leave the
	relay exactly as it is — see main(), where a missing input only drops its
	own wallbox path rather than aborting, so die() is reached only when the
	battery side itself cannot be steered."""
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


class _Tee:
	"""File-like object duplicating every write to the console and an
	optional logfile — this is the only place logging is handled, so every
	existing print() (-v, -debug, -html, error messages, all of it) is
	captured automatically without touching any call site. A write that
	fails against the logfile (disk full, permission lost mid-run, ...)
	falls back to the console alone rather than aborting the run — an
	unwritable logfile must never be the reason timer.txt doesn't get
	written."""
	def __init__(self, console, logfile):
		self.console, self.logfile = console, logfile
	def write(self, data):
		self.console.write(data)
		try: self.logfile.write(data)
		except Exception: pass
	def flush(self):
		self.console.flush()
		try: self.logfile.flush()
		except Exception: pass


if conf.get('logfile'):
	try:
		_log_path = conf['logfile'] if isabs(conf['logfile']) else join(dirname(__file__), conf['logfile'])
		_log_fh = open(_log_path, 'a', encoding='utf-8')
		_log_fh.write('\n=== %s ===\n' % datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
		sys.stdout = _Tee(sys.stdout, _log_fh)			# from here on every print() is mirrored to the logfile — appended, never rotated or truncated by dirt_shift itself
	except Exception as e:
		print('dirt_shift: warning: cannot open logfile %r (%s) — continuing without it' % (conf['logfile'], e))

# pull shared values from zeroinput.conf (read-only, never duplicated here):
#   discharge_t_file           — the timer file zeroinput reads (we write it)
#   cell_count                 — battery cell count for the empty-voltage anchor
# (the discharge cap for a non-priority red hour combines limit_discharge_rate
# from dirt_shift.conf with a fixed quarter-hour energy budget, 1/4 *
# (reserve_pct * basic_load - expected PV) — unrelated to any inverter
# staging threshold in zeroinput.conf.)
try:
	_zi_path = join(dirname(__file__), conf['zeroinput_conf'])
	with open(_zi_path, 'r') as fi:
		_zi = json_load(fi)
	conf['cell_count'] = _zi.get('cell_count', 16)		# default 16S, as in zeroinput
	conf['timer.txt'] = join(dirname(_zi_path), _zi['discharge_t_file']) \
		if not _zi['discharge_t_file'].startswith('/') else _zi['discharge_t_file']
except Exception as e:
	# zeroinput.conf unreadable or missing the needed keys — timer path unknown.
	print('dirt_shift: cannot read zeroinput.conf (%s): %s' % (conf.get('zeroinput_conf', '?'), e))
	exit(1)

verbose = ('-v' in sys_argv) or bool(conf.get('logfile'))	# a configured logfile is itself the request for the verbose record — no need to also remember -v on every cron line
avgnew  = '-avgnew' in sys_argv
html    = '-html' in sys_argv
if '-debug' in sys_argv: verbose = True; debug = True
else: debug = False

if verbose and html:
	print('<!DOCTYPE html><html><head><meta charset="UTF-8"><style>body {font-size:200%;'
	      'color:#BBBBBB;background-color:#111111;} pre {margin:0px;}</style></head><body><pre>\n')


# ── volkszähler queries ─────────────────────────────────────────────────────

def get_average(n_days):
	"""Hourly 7-day average basic_load (real home consumption Wh/h) from volkszähler."""
	hours = {}
	counted_days = 0
	keys = ['Inverter', 'Import', 'Auto']
	for key in keys: hours[key] = [0.0] * 24

	if verbose: print('query volkszähler for %i day consumption data:' % n_days)
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
		jresp = get(url=url, timeout=VZ_TIMEOUT_S).json()

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
		print('\nhour\tbasic_load')
		for i in range(0, 24): print('%i\t%.0f' % (i, hours['basic_load'][i]))
	return hours


def get_pv_curve(n_days):
	"""Empirical PV reference curve: for each hour of the day, the
	PV_CURVE_PERCENTILE percentile of that hour's PV power across the last
	n_days days (hourly volkszähler query, same pattern as get_average but a
	single channel, unweighted, and keeping every day's value instead of
	collapsing them into one mean). Because it is built from the installation's
	own measured output, it automatically reflects the real roof geometry
	(several sections with different orientation, seasonal shading) without any
	panel configuration. Returns a 24-value list, or None if no complete day was
	available (the caller then keeps the previously cached curve)."""
	daily = []			# one 24-value list per complete day
	if verbose: print('query volkszähler for %i day PV curve:' % n_days)

	for day in range(0, n_days):
		begin = (datetime.today() - timedelta(days=day, hours=24)).replace(minute=0, second=0, microsecond=0)
		end   = (datetime.today() - timedelta(days=day, hours=0 )).replace(minute=0, second=0, microsecond=0)
		beginstamp = str(int(begin.timestamp())).ljust(13, '0')
		endstamp   = str(int(end.timestamp())).ljust(13, '0')
		url = ('http://' + conf['vz_host_port'] + '/data.json?from=' + beginstamp
		       + '&to=' + endstamp + '&group=hour&uuid[]=' + conf['vz_chans']['PV'])

		if verbose:
			print(day, '\tbegin', begin, '\tend', end, '\t', end='')
		jresp = get(url=url, timeout=VZ_TIMEOUT_S).json()
		row = jresp['data'][0]

		if row['rows'] == 26:						# only complete days (24 hours + average + consumption)
			day_hours = [0.0] * 24
			for value in row['tuples']:
				tval = datetime.fromtimestamp(value[0] / 1000)
				if tval > end: continue			# drop next-day spill
				day_hours[tval.hour] = abs(value[1])	# this installation logs PV as negative (see get_vz_bat_cap)
			daily.append(day_hours)
		if verbose: print('rows:', row['rows'], ':', len(daily))

	if not daily:
		if verbose: print('PV curve: no complete days available')
		return None

	curve = [0.0] * 24
	for h in range(24):
		values = sorted(day[h] for day in daily)
		idx = min(len(values) - 1, int(round(PV_CURVE_PERCENTILE / 100.0 * (len(values) - 1))))
		curve[h] = values[idx]

	if debug:
		print('\nhour\tPV curve (p%i over %i days)' % (PV_CURVE_PERCENTILE, len(daily)))
		for h in range(24): print('%i\t%.0f' % (h, curve[h]))
	return curve


def read_average():
	"""Cached hourly basic_load average, refreshed once per hour."""
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
		vz_in['timestamp'] = datetime.now().timestamp()
		with open(join(dirname(__file__), 'dirt_avg_cache.json'), 'w') as fo:
			json_dump(vz_in, fo)
	else:
		if verbose: print('using cached averages from',
		                  datetime.fromtimestamp(vz_in['timestamp']).strftime('%Y-%m-%d %H:%M'))
	return vz_in


def read_pv_curve():
	"""Cached PV reference curve (see get_pv_curve), in its own cache file and
	on its own schedule: refreshed once a day from PV_CURVE_REFRESH_HOUR on,
	independent of the hourly basic_load/PV averages in read_average(). Returns
	the 24-value curve, or None if it has never been successfully computed."""
	vz_in = {}
	if not avgnew:
		try:
			with open(join(dirname(__file__), 'dirt_pv_curve_cache.json'), 'r') as fi:
				vz_in = json_load(fi)
		except Exception:
			pass

	now = datetime.now()
	last_ts   = vz_in.get('timestamp')
	last_date = datetime.fromtimestamp(last_ts).date() if last_ts else None
	needs_refresh = (avgnew or last_date is None
	                  or (last_date < now.date() and now.hour >= PV_CURVE_REFRESH_HOUR))

	if needs_refresh:
		curve = get_pv_curve(PV_CURVE_DAYS)
		if curve is not None:
			vz_in['curve'] = curve
			vz_in['timestamp'] = now.timestamp()
			with open(join(dirname(__file__), 'dirt_pv_curve_cache.json'), 'w') as fo:
				json_dump(vz_in, fo)
		elif verbose:
			print('PV curve refresh failed (no complete days) — keeping previous curve')
	elif verbose:
		print('using cached PV curve from',
		      datetime.fromtimestamp(last_ts).strftime('%Y-%m-%d %H:%M') if last_ts else 'never')

	return vz_in.get('curve')


def solar_elevation_deg(d, hour):
	"""Solar elevation angle (degrees) at local clock hour 'hour' (float) on
	date d, for the configured location. Plain astronomical approximation (no
	library, equation of time neglected) — more than accurate enough against the
	hourly resolution of the radiation forecast it feeds (see clear_sky_ghi).
	Negative for a sun below the horizon."""
	import math, time as _t
	lat = conf.get('latitude', 51.0)
	lon = conf.get('longitude', 10.0)
	is_dst = _t.localtime(_t.mktime(d.timetuple())).tm_isdst
	tz = (-_t.altzone if is_dst else -_t.timezone) / 3600.0		# local UTC offset incl. DST
	N = d.timetuple().tm_yday
	decl = math.radians(23.45) * math.sin(math.radians(360.0 / 365.0 * (N - 81)))
	lat_r = math.radians(lat)
	solar_noon = 12.0 - lon / 15.0 + tz
	H = math.radians(15.0 * (hour - solar_noon))
	elevation = math.asin(math.sin(lat_r) * math.sin(decl) + math.cos(lat_r) * math.cos(decl) * math.cos(H))
	return math.degrees(elevation)


def clear_sky_ghi(d, hour):
	"""Modelled clear-sky global horizontal irradiance (W/m^2) at local clock
	hour 'hour' (float) on date d, for the configured location — the Haurwitz
	(1945) clear-sky model: GHI = CLEAR_SKY_A * cos(z) * exp(-CLEAR_SKY_B /
	cos(z)) for zenith angle z while the sun is above the horizon, else 0.
	Needs only the solar position (see solar_elevation_deg), no turbidity/
	aerosol data, so it is computable offline. It is the denominator against which the
	Open-Meteo shortwave_radiation forecast is compared to get a clear-sky
	index (0..1) for scaling the empirical PV curve (see scaled_pv_curve)."""
	import math
	elev = solar_elevation_deg(d, hour)
	if elev <= 0:
		return 0.0
	cos_z = math.cos(math.radians(90.0 - elev))
	return CLEAR_SKY_A * cos_z * math.exp(-CLEAR_SKY_B / cos_z)


def get_radiation_forecast():
	"""Today's and tomorrow's hourly global horizontal irradiance forecast
	(shortwave_radiation, W/m^2) AND wind speed (wind_speed_100m, m/s, ~turbine
	hub height) from Open-Meteo (no API key required for non-commercial use),
	for the configured location. shortwave_radiation is direct plus diffuse —
	what a PV module actually receives, including overcast. Both variables come
	in ONE request (Open-Meteo returns them together), so the wind the emergency
	zones need is fetched and cached alongside the radiation the PV scaling
	needs, rather than in a separate call. Returns (rad_today, rad_tomorrow,
	wind_today, wind_tomorrow), each a 24-value list (an unreported hour stays
	None), or (None, None, None, None) on any error."""
	try:
		url = ('https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s'
		       '&hourly=shortwave_radiation,wind_speed_100m&wind_speed_unit=ms&forecast_days=2&timezone=auto'
		       % (conf.get('latitude', 51.0), conf.get('longitude', 10.0)))
		jresp = get(url=url, timeout=10).json()
		times = jresp['hourly']['time']			# 'YYYY-MM-DDTHH:MM', local time (timezone=auto)
		rad   = jresp['hourly']['shortwave_radiation']
		wind  = jresp['hourly']['wind_speed_100m']
		today_str = datetime.now().strftime('%Y-%m-%d')
		rt, rm, wt, wm = [None] * 24, [None] * 24, [None] * 24, [None] * 24
		for t, r, w in zip(times, rad, wind):
			h = int(t[11:13])
			if not (0 <= h < 24):
				continue
			if t[:10] == today_str:
				rt[h], wt[h] = r, w
			else:
				rm[h], wm[h] = r, w
		return rt, rm, wt, wm
	except Exception as e:
		if verbose: print('radiation forecast fetch failed:', e)
		return None, None, None, None


def read_radiation_forecast():
	"""Rolling 24-hour shortwave-radiation forecast starting at the current
	hour: hours from now until midnight come from today's forecast, hours
	after midnight come from tomorrow's — so a caller summing forward from
	'now' (see _bridge_hours/red_window_demand) always reads the forecast for
	the calendar day each hour actually falls on, instead of today's value
	being reused for what is really tomorrow morning. Built fresh on every
	call from the cached raw today/tomorrow arrays (see get_radiation_forecast),
	which are refetched together once per hour in their own cache file,
	independent of the averages and the PV curve. An hour missing from
	tomorrow's forecast (not yet published, or the fetch failed) falls back
	to today's value for that same hour — that hour is then really still
	today's data standing in for tomorrow, not tomorrow's own forecast; the
	set of such hours is returned alongside the array (see _hourly_debug_table,
	which marks them with '.'). Returns (None, set()) if no forecast has ever
	been fetched successfully."""
	vz_in = {}
	if not avgnew:
		try:
			with open(join(dirname(__file__), 'dirt_weather_cache.json'), 'r') as fi:
				vz_in = json_load(fi)
		except Exception:
			pass

	last_ts = vz_in.get('attempt') or vz_in.get('timestamp')	# see read_smard_zones: counted from the last attempt so a failure is retried next hour, not next run
	needs_refresh = (avgnew or last_ts is None or
	                  datetime.fromtimestamp(last_ts).strftime('%Y-%m-%d %H') != datetime.now().strftime('%Y-%m-%d %H'))

	if needs_refresh:
		today, tomorrow, wind_today, wind_tomorrow = get_radiation_forecast()
		vz_in['attempt'] = datetime.now().timestamp()	# recorded either way, so a failure is retried next hour and not next run
		if today is not None:
			vz_in['today'] = today
			vz_in['tomorrow'] = tomorrow
			vz_in['wind_today'] = wind_today		# cached alongside radiation, same source and validity — used by the emergency zones
			vz_in['wind_tomorrow'] = wind_tomorrow
			vz_in['timestamp'] = datetime.now().timestamp()
			if verbose: print('radiation forecast refreshed')	# said out loud, so a silent absence of any line cannot be mistaken for a cache hit
		elif verbose:
			print('radiation forecast refresh failed — keeping previous forecast')
		try:
			with open(join(dirname(__file__), 'dirt_weather_cache.json'), 'w') as fo:
				json_dump(vz_in, fo)
		except Exception as e:
			if verbose: print('failed to write radiation cache:', e)
	elif verbose:
		_ok_ts = vz_in.get('timestamp')
		# also show how far the forecast reaches. The weather cache stores no
		# date, only 'today'/'tomorrow' 24-value lists, but their meaning is
		# fixed: 'today' is the day of the last fetch, 'tomorrow' the day after.
		# Within the later populated half, the last non-None hour gives the exact
		# clock time it runs out at.
		_tom = vz_in.get('tomorrow')
		if _ok_ts:
			_base = datetime.fromtimestamp(_ok_ts)
			if _tom and any(v is not None for v in _tom):
				_day, _rat = _base + timedelta(days=1), _tom
			else:
				_day, _rat = _base, (vz_in.get('today') or [])
			_last = max((h for h, v in enumerate(_rat) if v is not None), default=None)
			_until = _day.strftime('%Y-%m-%d') + (' %02d:00' % _last if _last is not None else '')
			print('using cached radiation forecast from %s, data until %s' % (
				_base.strftime('%Y-%m-%d %H:%M'), _until))
		else:
			print('using cached radiation forecast from never')

	today = vz_in.get('today')
	if today is None:
		return None, set()
	tomorrow = vz_in.get('tomorrow') or [None] * 24
	now_hour = datetime.now().hour
	stale = {h for h in range(now_hour) if tomorrow[h] is None}
	return [today[h] if h >= now_hour or tomorrow[h] is None else tomorrow[h] for h in range(24)], stale


def scaled_pv_curve(pv_curve, radiation, now):
	"""Scale the empirical PV reference curve by a clear-sky index derived from
	the Open-Meteo shortwave_radiation forecast: each hour's expected PV is
	the reference value times min(1.0, forecast_radiation / clear_sky_ghi),
	where clear_sky_ghi is the modelled clear-sky irradiance for the same
	hour (hour-centred, see clear_sky_ghi). 'radiation' is the rolling
	24-hour array from read_radiation_forecast, so an hour before now.hour
	is really tomorrow's occurrence of that hour — clear_sky_ghi is evaluated
	against tomorrow's date for those hours accordingly, today's date for the
	rest. The index is capped at 1.0: brief cloud-edge radiation enhancement
	above the clear-sky model is a real but minor effect, not modelled here,
	to keep the forecast conservative. An hour with no radiation data, or
	where clear_sky_ghi is 0 (sun below the horizon — the empirical pv_curve
	should already be ~0 there), falls back to the plain reference value for
	that hour. Returns None if pv_curve itself is None; returns pv_curve
	unchanged if radiation is None (forecast unavailable)."""
	if pv_curve is None:
		return None
	if radiation is None:
		return pv_curve
	result = [0.0] * 24
	for h in range(24):
		r = radiation[h] if h < len(radiation) else None
		if r is None:
			result[h] = pv_curve[h]
			continue
		d = now if h >= now.hour else now + timedelta(days=1)
		csghi = clear_sky_ghi(d, h + 0.5)			# hour-centred sun position
		if csghi <= 0:
			result[h] = pv_curve[h]
			continue
		index = min(1.0, max(0.0, r / csghi))
		result[h] = pv_curve[h] * index
	return result



SMARD_BATCH_TRIES = 3	# how many published batches back _smard_series will look for a given date before giving up

def _smard_series(filter_id, today):
	"""Fetch one SMARD day-ahead series (hourly resolution) and return
	{local_hour: value} for points that fall on 'today' (a date) in local time.
	SMARD's API is two-step: an index of available batch-start timestamps, then
	the series for one batch. Raises on any request/structure problem — the
	caller treats that as 'no data'.

	Batches are weekly, so the newest one at/before now is NOT reliably the one
	holding the requested date: in the small hours of a Monday the week that
	just began is already indexed but not yet filled, while the values for that
	very day — published day-ahead over the weekend — sit in the batch before
	it. Taking the newest batch alone therefore returned an empty day every
	Monday night. This walks back up to SMARD_BATCH_TRIES batches and keeps the
	first that actually covers the date, which costs an extra request only in
	the cases that used to fail outright."""
	base = 'https://www.smard.de/app/chart_data'
	idx = get(url='%s/%i/%s/index_hour.json' % (base, filter_id, SMARD_REGION), timeout=15)
	idx.raise_for_status()
	now_ms = int(datetime.now().timestamp() * 1000)
	candidates = sorted((t for t in idx.json()['timestamps'] if t <= now_ms), reverse=True)[:SMARD_BATCH_TRIES]

	result = {}
	for batch in candidates:
		resp = get(url='%s/%i/%s/%i_%s_hour_%i.json' % (base, filter_id, SMARD_REGION, filter_id, SMARD_REGION, batch),
		           timeout=15)
		resp.raise_for_status()
		found = {}
		for ts_ms, value in resp.json()['series']:
			if value is None: continue
			t_local = datetime.fromtimestamp(ts_ms / 1000)
			if t_local.date() == today:
				found[t_local.hour] = float(value)
		if len(found) > len(result):
			result = found
		if len(result) >= 24:							# full day found, no reason to look further back
			break
	return result


SMARD_LOAD_FALLBACK_DAYS = 7	# how many days back the load forecast may be borrowed from when SMARD has not published it for the requested date

def _smard_load_series(date):
	"""Load forecast for 'date', falling back to the most recent earlier day
	SMARD does have, and returning (series, date_actually_used).

	SMARD publishes the two day-ahead series independently, and the load
	forecast routinely lags the generation forecast by a day or more. Since a
	day is only usable when BOTH series cover it, that lag alone took the whole
	program out: generation for today complete, load missing, zones
	uncomputable, and dirt_shift falling back to an all-allowed timer while
	the data it actually needs most — the renewables curve — was sitting there.

	Substituting an older day is defensible here because the zone cut only
	reads the SHAPE of renewables/load across the window, never its absolute
	level: the median splits the 24 values wherever they happen to lie. Load
	profiles repeat closely from day to day, so the shape survives; an unusual
	day (holiday, heatwave) shifts the split slightly, which is a far smaller
	error than having no zones at all.

	Walks back a day at a time and stops at the first day with a usable series,
	so the substitute is always the freshest one available."""
	for back in range(SMARD_LOAD_FALLBACK_DAYS + 1):
		d = date - timedelta(days=back)
		series = _smard_series(SMARD_FILTER_LOAD, d)
		if len(series) >= 20:
			return series, d
	return {}, None


def _smard_zones_for_date(date):
	"""Classify one calendar date's SMARD-derived CO2-intensity zones
	('red'/'green') and the raw wind+solar/load ratio per hour, from real
	SMARD day-ahead data (Bundesnetzagentur; no API key needed). The day's
	Returns {'ratio': 24-value list (None where SMARD did not cover that
	hour)}, or None if the query/parse fails or too few hours are covered for
	that date (SMARD's day-ahead data for tomorrow, in particular, may simply
	not be published yet). A day needs BOTH series to cover it, so a missing
	load forecast is borrowed from the most recent day that has one (see
	_smard_load_series) rather than sinking the whole date.

	No classification happens here: the red/green median cut is taken once
	over the ASSEMBLED rolling 24 hours instead (see read_smard_zones).
	Cutting per calendar day made 'red' mean 'dirty for that day', which is
	not comparable across the midnight boundary the rolling window crosses
	every evening — a clean day's own median can classify an hour as green
	that is absolutely dirtier than the hours the next day calls red, and the
	battery was then held back for hours cleaner than the one it refused to
	serve."""
	try:
		renewable = _smard_series(SMARD_FILTER_WIND_SOLAR, date)
		if len(renewable) < 20:							# no point hunting for a load profile this date cannot use
			raise ValueError('incomplete day (%i/24 generation hours)' % len(renewable))
		load, load_date = _smard_load_series(date)

		hours = [h for h in range(24) if h in load and h in renewable]
		if len(hours) < 20:								# too few hours for a meaningful split
			raise ValueError('incomplete day (%i/24 hours)' % len(hours))
		if verbose and load_date != date:				# reported only once the day is actually usable
			print('SMARD load forecast for %s unavailable — using profile from %s' % (date, load_date))

		ratio = {h: (renewable[h] / load[h] if load[h] > 0 else 0.0) for h in hours}
		ratio_list = [ratio.get(h) for h in range(24)]		# None where not covered
		if debug: print('SMARD ratio by hour (%s):' % date, {h: round(ratio[h], 2) for h in hours})
		return {'ratio': ratio_list}
	except Exception as e:
		if verbose: print('SMARD zone fetch failed for %s:' % date, e)
		return None


def get_smard_zones():
	"""Today's and tomorrow's SMARD-derived zones (see _smard_zones_for_date).
	Returns (today, tomorrow), each either a {'ratio':...} dict
	or None. Tomorrow's day-ahead data commonly is not published yet earlier
	in the day — that is expected and not treated as an error, tomorrow is
	simply None then. Returns (None, None) if today's own query fails."""
	today_local = datetime.now().date()
	today = _smard_zones_for_date(today_local)
	if today is None:
		return None, None
	tomorrow = _smard_zones_for_date(today_local + timedelta(days=1))
	return today, tomorrow


def _median(values):
	"""Median of a list, ignoring None entries. None if nothing is left.
	Used by the zone cut over the rolling window (_cut_zones)."""
	valid = [v for v in values if v is not None]
	if not valid:
		return None
	n = len(valid)
	s = sorted(valid)
	return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def _cut_zones(ratio):
	"""Split a 24-value ratio list into red/green at its own median: the
	cleaner half (ratio at or above the median) is green, the dirtier half
	red. A self-adjusting cut that reflects the window's own spread instead
	of a fixed threshold, so even a uniformly dirty stretch still separates
	its relatively cleaner hours from its worst ones — and, being a median,
	it always yields 12 of each, never degenerating to all-green or all-red
	however clean or dirty the window is overall.

	Applied to the ASSEMBLED rolling window, not per calendar day. Cutting
	each day separately makes 'red' mean 'dirty relative to that day', which
	says nothing across the midnight boundary the window crosses every
	evening: on a day whose midday was exceptionally clean the median sits
	high, so a genuinely dirty late-evening hour still falls below it and
	counts green, while the next day's calmer hours — absolutely cleaner —
	count red. The battery was then stopped in the dirtier hour to save
	charge for cleaner ones, the exact inverse of the intent. One cut over
	the 24 hours actually being decided about removes that. The wallbox's own
	threshold looks at the same rolling window, ranked rather than cut (see
	_dirt_rank_pct).

	Hours SMARD did not cover (None) count as red, the safe default."""
	median = _median(ratio)
	if median is None:
		return ['red'] * 24
	if debug: print('rolling median ratio %.2f over %d covered hours'
	                % (median, sum(1 for r in ratio if r is not None)))
	return ['green' if (r is not None and r >= median) else 'red' for r in ratio]


def load_only_zones(basic_load, now):
	"""EMERGENCY fallback when SMARD is gone for longer than the one-day cache
	can bridge. Builds zones from the LOAD profile alone (from volkszähler,
	always on hand), with no grid-intensity data at all: the hours of highest
	expected load become red, the rest green, by the same rolling median cut.

	This is a bet, not a measurement, and deliberately a narrow one. The load
	double-peak (morning ~7-9, evening ~18-21) is the one part of the grid's
	daily shape that is human-driven and so predictable without weather data,
	and high load means high dirtiness at equal generation — so on a windless
	day these are reliably the dirtiest hours. It says NOTHING about wind: a
	windy night can be clean despite high load, and there this fallback will
	wrongly steer discharge into the evening peak. It is therefore a last
	resort before the free-timer, worthwhile mainly for a small/often-empty
	battery that can only cover part of the day and should spend its charge on
	the hours most likely to be dirty. The dirt% it reports is not a CO2 figure
	but the load expressed on the same 0-100 scale, purely so the rest of the
	pipeline (reserve, dirtiest_hour, the table) has something monotonic to
	work with; it must never be logged to volkszähler as real dirtiness.

	Returned dict carries emergency=True so the caller can label everything
	loudly and skip the dirtiness export."""
	rolling = [basic_load[(now.hour + k) % 24] for k in range(24)]	# chronological, like the SMARD window
	lo, hi = min(rolling), max(rolling)
	# map load onto a ratio-like 0..1 where HIGH load = LOW ratio (= dirty),
	# so _cut_zones' "green = ratio >= median" makes the high-load hours red
	span = (hi - lo) or 1.0
	ratio_chrono = [1.0 - (v - lo) / span for v in rolling]
	# unshuffle back to hour-indexed 0..23
	ratio = [None] * 24
	for k in range(24):
		ratio[(now.hour + k) % 24] = ratio_chrono[k]
	return {'zones': _cut_zones(ratio), 'ratio': ratio, 'stale': set(),
	        'backdate_ratio': None, 'emergency': True}


def read_smard_zones():
	"""Rolling 24-hour SMARD zones/ratio starting at the current hour: the
	ratio of each hour comes from the calendar day that hour actually falls
	on — from now until midnight today's, after midnight tomorrow's — the
	same rolling principle as read_radiation_forecast. The red/green cut is
	then taken ONCE over that assembled window (see _cut_zones), not per
	calendar day, so 'red' means dirty relative to the 24 hours actually
	being decided about rather than relative to whichever day an hour
	happens to belong to. Built fresh on every call from the cached raw
	today/tomorrow ratio dicts (see get_smard_zones), which are refetched
	together once per hour in their own cache file. An hour missing from
	tomorrow (day-ahead data not yet published, or entirely absent) uses
	today's ratio for that same hour — that hour is then really still
	today's figure standing in for tomorrow, not tomorrow's own;
	the returned dict's 'stale' key holds the set of such hours (see
	_hourly_debug_table, which marks them with '.'). SMARD is a prerequisite:
	if the fetch fails, the cached data may substitute for exactly one more
	day (the cache carries fetch_date; data fetched yesterday still passes —
	its 'tomorrow' half was the day-ahead forecast for what is now today).
	Anything older, or no cache at all, returns None — the caller then aborts
	hard, leaving the all-allowed free timer.
	The returned dict also carries 'backdate_ratio': the previous hour's
	ratio (from the cache as it stood just before this call's refresh), only
	ever non-None on the one run per hour that actually triggers a refresh —
	every later run within the same hour finds the cache already current and
	so has nothing to backdate. Purely for write_dirtiness_to_vz's backdated
	point (see main()), letting volkszähler show a flat step up to the hour
	boundary instead of interpolating a ramp between two hourly values; not
	used anywhere else."""
	vz_in = {}
	if not avgnew:
		try:
			with open(join(dirname(__file__), 'dirt_smard_cache.json'), 'r') as fi:
				vz_in = json_load(fi)
		except Exception:
			pass

	last_ts = vz_in.get('attempt') or vz_in.get('timestamp')	# attempt, not timestamp: a failed fetch must not make every run for the rest of the hour try again
	needs_refresh = (avgnew or last_ts is None or
	                  datetime.fromtimestamp(last_ts).strftime('%Y-%m-%d %H') != datetime.now().strftime('%Y-%m-%d %H'))

	backdate_ratio = None
	if needs_refresh:
		old_today = vz_in.get('today')
		if old_today is not None:
			prev_hour = (datetime.now().hour - 1) % 24
			backdate_ratio = old_today['ratio'][prev_hour]			# value that was valid up to this hour's boundary

		today, tomorrow = get_smard_zones()
		vz_in['attempt'] = datetime.now().timestamp()		# recorded either way, so a failure is retried next hour and not next run
		if today is not None:
			vz_in['today'] = today
			vz_in['tomorrow'] = tomorrow
			vz_in['fetch_date'] = datetime.now().strftime('%Y-%m-%d')
			vz_in['timestamp'] = datetime.now().timestamp()
			if verbose: print('SMARD zones refreshed')		# said out loud, so a silent absence of any line cannot be mistaken for a cache hit
		elif verbose:
			print('SMARD zone refresh failed — trying previous data')
		try:
			with open(join(dirname(__file__), 'dirt_smard_cache.json'), 'w') as fo:
				json_dump(vz_in, fo)
		except Exception as e:
			if verbose: print('failed to write SMARD cache:', e)
	_cache_notice = (not needs_refresh)			# print the 'using cached ... data until ...' line after the window is assembled

	today = vz_in.get('today')
	fetch_date = vz_in.get('fetch_date')
	if today is None or fetch_date is None:
		return None
	age_days = (datetime.now().date() - datetime.strptime(fetch_date, '%Y-%m-%d').date()).days
	if age_days > 1:
		if verbose: print('cached SMARD data is from %s — too old to substitute' % fetch_date)
		return None
	now_hour = datetime.now().hour
	if age_days == 1:
		# fetched yesterday, substituting once: yesterday's 'tomorrow' half was
		# the day-ahead forecast for what is now today.
		if verbose: print('SMARD data from %s substituting for one day' % fetch_date)
		today = vz_in.get('tomorrow') or today
		tomorrow = None
	else:
		tomorrow = vz_in.get('tomorrow')
	def _emit_cache_notice(ratio, stale):
		# 'data until' = the last hour the assembled window genuinely covers,
		# as a real timestamp. Read from ratio (which reflects any
		# substitution), but NOT counting 'stale' slots — those are today's
		# early hours reused to fill tomorrow morning, not real reach. So a
		# one-day substitution correctly reads as today 23:00, neither
		# understated to fetch_date nor overstated into tomorrow.
		if not (_cache_notice and verbose):
			return
		now_h = datetime.now().hour
		last_k = max((k for k in range(24)
		              if ratio[(now_h + k) % 24] is not None and (now_h + k) % 24 not in stale),
		             default=None)
		if last_k is None:
			until = '?'
		else:
			end = datetime.now().replace(minute=0, second=0, microsecond=0) + timedelta(hours=last_k)
			until = end.strftime('%Y-%m-%d %H:00')
		_ok_ts = vz_in.get('timestamp')
		print('using cached SMARD zones from %s, data until %s' % (
			datetime.fromtimestamp(_ok_ts).strftime('%Y-%m-%d %H:%M') if _ok_ts else 'never', until))

	if tomorrow is None:
		_emit_cache_notice(today['ratio'], set(range(now_hour)))
		return {'zones': _cut_zones(today['ratio']), 'ratio': today['ratio'],
		        'stale': set(range(now_hour)), 'backdate_ratio': backdate_ratio}
	ratio = [today['ratio'][h] if h >= now_hour else tomorrow['ratio'][h] for h in range(24)]
	_emit_cache_notice(ratio, set())
	return {'zones': _cut_zones(ratio), 'ratio': ratio, 'stale': set(),
	        'backdate_ratio': backdate_ratio}


def read_wind_forecast():
	"""Rolling 24-hour wind-speed forecast (m/s at ~100 m) from the weather
	cache, same shape and rolling convention as read_radiation_forecast: hours
	from now to midnight from 'wind_today', after midnight from 'wind_tomorrow'.
	Cached together with the radiation in one Open-Meteo call (see
	get_radiation_forecast / read_radiation_forecast), so the emergency zones
	need no separate network request. Returns a 24-value list (None where
	unreported) or None if the cache has no wind data at all."""
	vz_in = {}
	try:
		with open(join(dirname(__file__), 'dirt_weather_cache.json'), 'r') as fi:
			vz_in = json_load(fi)
	except Exception:
		return None
	wt = vz_in.get('wind_today')
	if wt is None:
		return None
	wm = vz_in.get('wind_tomorrow') or [None] * 24
	now_hour = datetime.now().hour
	return [wt[h] if h >= now_hour else wm[h] for h in range(24)]


def _wind_index(v):
	"""Turbine-fleet output proxy from wind speed at hub height (m/s), 0..1.
	Zero below cut-in, a cubic ramp to rated (power ~ v^3), flat 1.0 above —
	the standard turbine shape, deliberately simple. Not calibrated to real
	GW; only the daily shape feeds the median cut."""
	if v is None or v <= EMERGENCY_WIND_V_CUTIN:
		return 0.0
	if v >= EMERGENCY_WIND_V_RATED:
		return 1.0
	span = EMERGENCY_WIND_V_RATED - EMERGENCY_WIND_V_CUTIN
	return ((v - EMERGENCY_WIND_V_CUTIN) / span) ** 3


def emergency_zones(expected_pv, basic_load):
	"""Zones MODELLED from the weather forecast, for when SMARD gives nothing
	even from cache — the alternative to the free timer, not to real data.

	dirtiness(h) = load_index(h) - generation_index(h), where generation is
	modelled solar + modelled wind and load is a fixed normalised profile of
	the NATIONWIDE grid (EMERGENCY_LOAD_PROFILE), not the house: the CO2 mix
	is a national quantity. The solar shape is taken from expected_pv (the
	radiation-scaled PV curve already computed), reused only for its
	across-the-day SHAPE, normalised to its own peak. Wind comes from
	get_wind_forecast (cached alongside the radiation, one Open-Meteo call).
	Both terms are 0..~1 and the median cut ranks the
	result, so absolute scaling is irrelevant.

	Returns the same dict shape read_smard_zones returns (zones/ratio/stale/
	backdate_ratio) so main() is agnostic, plus emergency=True. ratio is a
	pseudo-ratio (1 - dirtiness, offset into a positive-ish range) purely so
	the existing dirt%/median machinery and the -v output keep working; its
	absolute value is meaningless, only the ordering is. None if the wind
	fetch fails too — then nothing can be modelled and main() falls through to
	the free timer."""
	wind = read_wind_forecast()
	if wind is None:
		return None

	pv_peak = max([p for p in expected_pv if p is not None] or [0.0]) or 1.0
	dirt = [None] * 24
	for h in range(24):								# h is the real hour-of-day (ratio is indexed by hour, like SMARD's)
		solar_i = (expected_pv[h] / pv_peak) if expected_pv[h] is not None else 0.0
		wind_i  = _wind_index(wind[h]) or 0.0		# an unreported wind hour contributes nothing rather than breaking the sum
		gen = EMERGENCY_SOLAR_WEIGHT * solar_i + EMERGENCY_WIND_WEIGHT * wind_i
		dirt[h] = EMERGENCY_LOAD_PROFILE[h] - gen	# higher = dirtier

	# pseudo-ratio so the dirt% = (1 - ratio) * 100 machinery still reads right:
	# map dirtiness d to ratio = 1 - d, which keeps 'dirtier -> lower ratio ->
	# higher dirt%' exactly as with real data. The median cut only needs order.
	ratio = [1.0 - d for d in dirt]
	return {'zones': _cut_zones(ratio), 'ratio': ratio, 'stale': set(),
	        'backdate_ratio': None, 'emergency': True}


def _empty_anchor(tuples, empty_v):
	"""Timestamp of the deepest voltage inside a run that STAYS at/below
	empty_v for at least EMPTY_MIN_DURATION_S — a real empty state, not a
	momentary dip. None if no run qualifies. Scans the raw samples, so a
	one- or two-second excursion under the threshold (load spike, inverter
	start, brief outage) is ignored rather than mistaken for the battery
	being flat and anchoring the integration from a false zero."""
	best_ts, best_v = None, None
	i, n = 0, len(tuples)
	while i < n:
		if tuples[i][1] <= empty_v:
			j = i
			while j < n and tuples[j][1] <= empty_v:
				j += 1
			if (tuples[j - 1][0] - tuples[i][0]) / 1000.0 >= EMPTY_MIN_DURATION_S:
				for ts, v, _s in tuples[i:j]:			# deepest point of this qualifying valley
					if best_v is None or v < best_v:
						best_v, best_ts = v, ts
			i = j
		else:
			i += 1
	return best_ts


def _neg_content_diagnosis(vz_bat_cap, raw_cap, battery_capacity_wh):
	"""Whether a negative reconstructed battery content is severe enough to
	warn about, and which of the two plausible causes it points to. Returns
	None (no warning), 'hardware' (raw_cap — the same channel data with NO
	efficiency loss applied — also breaches the limit, so no efficiency
	retuning could explain it away: the channel data itself must be wrong),
	or 'efficiency' (raw_cap is within bounds, so the configured efficiencies
	may simply be set lower than the installation's real losses — though a
	smaller channel fault remains possible too, see the message text).

	Efficiencies only ever push content DOWN from raw_cap, never up (both
	PV_to_bat_efficiency and bat_to_AC_efficiency are <= 100%), which is what
	makes raw_cap breaching the limit a firm 'not efficiency' signal rather
	than a guess."""
	if battery_capacity_wh <= 0:
		return None
	limit = -NEG_CONTENT_WARN_FRACTION * battery_capacity_wh
	if vz_bat_cap >= limit:
		return None
	return 'hardware' if raw_cap < limit else 'efficiency'


def get_vz_bat_cap():
	"""Reconstruct real battery energy content (Wh) by integrating PV and
	Inverter since the last known 'empty' state (voltage <= 3.0625 V/cell, i.e.
	49 V at 16 cells, scaled by cell_count). Returns (latest_voltage,
	content_Wh), or (None, None) if the data needed is unavailable — the
	caller then frees the battery (it cannot be steered without content) but
	still runs the wallbox paths that do not depend on content (see main()),
	rather than aborting the whole run."""
	if verbose: print(datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'query volkszähler for energy content:')
	days_back = 0
	latest_voltage = 0.0					# guard: set on the first (days_back==0) query below
	end = datetime.today().replace(microsecond=0)
	endstamp = str(int(end.timestamp())).ljust(13, '0')

	_empty_v = 3.0625 * conf.get('cell_count', 16)		# 16S original: 49 V (empty-battery anchor)
	anchor_ts = None
	while True:
		begin = (datetime.today() - timedelta(days=days_back)).replace(hour=0, minute=0, second=0, microsecond=0)
		beginstamp = str(int(begin.timestamp())).ljust(13, '0')
		url = 'http://' + conf['vz_host_port'] + '/data.json?from=' + beginstamp + '&to=' + endstamp + '&uuid[]=' + conf['vz_chans']['Vbat']
		try:
			jresp = get(url=url, timeout=VZ_TIMEOUT_S).json()
			tuples = jresp['data'][0]['tuples']
		except Exception:
			print('dirt_shift: battery voltage data unusable'); return None, None
		# drop physically impossible samples (sensor fault / dropped link) before
		# anything reads them — below 2.5 V/cell every BMS has disconnected, so
		# such a value is never a real state and must not anchor or skew anything.
		_impl_v = IMPLAUSIBLE_V_PER_CELL * conf.get('cell_count', 16)
		tuples = [(ts, v, s) for ts, v, s in tuples if v >= _impl_v]
		jresp['data'][0]['tuples'] = tuples
		if not tuples:
			print('dirt_shift: no battery voltage data returned by volkszähler'); return None, None
		if days_back == 0: latest_voltage = tuples[-1][1]
		anchor_ts = _empty_anchor(tuples, _empty_v)		# deepest point of a real (sustained) empty valley, not a momentary dip
		if anchor_ts is not None: break
		if days_back >= conf['max_days_empty_battery']:
			if verbose: print(days_back, '\tno empty battery state found')
			break
		days_back += 1

	if anchor_ts is None:
		# no sustained empty state within the search window: anchor at the
		# deepest sample as a last resort (integration then unbounded, as before)
		min_ts, min_v = None, 999
		for ts, v, s in jresp['data'][0]['tuples']:
			if v <= min_v: min_ts, min_v = ts, v
		anchor_ts = min_ts
	if anchor_ts is None:
		print('dirt_shift: no usable battery voltage samples'); return None, None
	min_ts = anchor_ts
	# voltage at the anchor, for the -v line below — set here so it is defined
	# whichever branch found the anchor (the sustained-valley path returns only
	# a timestamp).
	min_v = next((v for ts, v, s in jresp['data'][0]['tuples'] if ts == anchor_ts), latest_voltage)

	begin = datetime.fromtimestamp(min_ts / 1000)
	end   = datetime.today().replace(microsecond=0)
	beginstamp = str(min_ts).ljust(13, '0')
	endstamp   = str(int(end.timestamp())).ljust(13, '0')
	url = 'http://' + conf['vz_host_port'] + '/data.json?from=' + beginstamp + '&to=' + endstamp
	for key in ['Inverter', 'PV']: url += '&uuid[]=' + conf['vz_chans'][key]

	try:
		jresp = get(url=url, timeout=VZ_TIMEOUT_S).json()
	except Exception:
		print('dirt_shift: battery capacity data unusable'); return None, None

	vz_bat_cap = 0.0
	raw_cap = 0.0		# same balance with NO efficiency applied at all (loss-free reference) — see the warning below
	for row in jresp['data']:
		if row['uuid'] == conf['vz_chans']['PV']:
			vz_bat_cap += abs(row['consumption']) * conf['PV_to_bat_efficiency'] * 0.01		# PV energy into the battery, charge loss applied
			raw_cap += abs(row['consumption'])
		elif row['uuid'] == conf['vz_chans']['Inverter']:
			vz_bat_cap -= abs(row['consumption']) / (conf['bat_to_AC_efficiency'] * 0.01)	# inverter AC output ALWAYS removes battery energy (loss divided back in) — abs+minus so the direction does not depend on the channel's sign convention, matching basic_load's abs(Inverter)
			raw_cap -= abs(row['consumption'])

	vz_bat_cap *= conf['bat_to_AC_efficiency'] * 0.01
	vz_bat_cap = int(vz_bat_cap)						# truncate once, here, so the value printed below is the value returned — a %.0f of the float would round and disagree with it by 1 Wh
	# a content clearly below empty is impossible — warn (always, so it reaches
	# the cron log). raw_cap (the same channel data with NO efficiency loss
	# applied at all) tells the two plausible causes apart (see
	# _neg_content_diagnosis). An anchor too many days back (see
	# EMPTY_MIN_DURATION_S) makes either effect larger by giving it more
	# throughput to accumulate over.
	_diag = _neg_content_diagnosis(vz_bat_cap, raw_cap, conf.get('battery_capacity_wh', 0))
	if _diag == 'hardware':
		print('dirt_shift: WARNING battery content came out %d Wh (below empty, impossible; '
		      'even with NO conversion loss assumed at all, the channel balance is %d Wh — still '
		      'impossible). Efficiency settings cannot explain this: the channel data itself is '
		      'likely wrong, e.g. an inverter logging demand it did not deliver (tripped RCD, '
		      'blown fuse, failed unit). Check the inverters.' % (vz_bat_cap, int(raw_cap)))
	elif _diag == 'efficiency':
		print('dirt_shift: WARNING battery content came out %d Wh (below empty, impossible), '
		      'but with NO conversion loss assumed at all the same channel data gives %d Wh '
		      '(plausible). PV_to_bat_efficiency / bat_to_AC_efficiency may be set lower than '
		      'this installation\'s real losses — check them against a real empty-to-empty '
		      'cycle. Could also be an inverter fault (tripped RCD, blown fuse, failed unit); '
		      'check the inverters too.' % (vz_bat_cap, int(raw_cap)))
	if verbose: print('min voltage %.1f V, latest %.1f V, battery content %d Wh' % (min_v, latest_voltage, vz_bat_cap))
	return latest_voltage, vz_bat_cap


# ── dirt_shift core logic (verified separately) ───────────────────────────────

def write_dirtiness_to_vz(value, ts_ms=None):
	"""Best-effort: POST the current grid dirtiness value directly to
	volkszähler's middleware API, once per run — no local file, no vzlogger
	meter involved: POST http://{vz_host_port}/data/{vz_dirtiness_uuid}.json
	with the value and current timestamp. Requires vz_dirtiness_uuid
	configured (a real channel UUID, created in volkszähler beforehand); the
	host/port is the same vz_host_port dirt_shift already uses for its other
	volkszähler queries. Sign convention matches the installation's existing
	power channels (Import positive = drawing from the grid, Inverter
	negative = feeding in): positive = dirtier (below-average renewable
	share), negative = cleaner than average / renewable surplus. Checks the
	HTTP response status (raise_for_status) so a rejected write — wrong or
	unknown UUID, server error — is caught here rather than passing as silent
	success. Never raises further: a failed write must not abort the run.
	Returns True if the value reached volkszähler, False if the write was
	attempted and failed, None if no UUID is configured (nothing attempted).
	The debug table marks the written hour accordingly (see
	_hourly_debug_table). ts_ms is the volkszähler timestamp in milliseconds;
	defaults to the real current time, but main() also calls this with an
	explicit slot-boundary timestamp (and, once per hour, a second call 1s
	before the boundary with the previous hour's value) so volkszähler shows
	a flat step at the quarter-hour instead of interpolating a ramp between
	two hourly values — see read_smard_zones' 'backdate_ratio'."""
	uuid = conf.get('vz_dirtiness_uuid', '')
	if not uuid:
		return None
	try:
		url = 'http://%s/data/%s.json' % (conf['vz_host_port'], uuid)
		resp = post(url=url, params={'value': value, 'ts': ts_ms if ts_ms is not None else int(time() * 1000)}, timeout=10)
		resp.raise_for_status()
		return True
	except Exception as e:
		if verbose: print('dirtiness write failed:', e)
		return False


def tasmota_power(ip, output):
	"""Query a Tasmota device's current relay state — 'cmnd=Power{output}'
	with no value is a read, not a write (see tasmota_set_power for that).
	'output' is the relay number as Tasmota names it ('1', '2', ... — '' for
	a single-relay device without a number). Returns True (on) / False (off)
	/ None on any request or parse failure — the caller (wallbox_switch) then
	treats it the same as 'didn't take effect yet' and retries."""
	try:
		key = 'POWER%s' % output if output else 'POWER'
		resp = get(url='http://%s/cm?cmnd=Power%s' % (ip, output), timeout=10).json()
		return resp.get(key) == 'ON'
	except Exception as e:
		if verbose: print('tasmota status query failed:', e)
		return None


def tasmota_set_power(ip, output, on):
	"""Send a Tasmota Power Set command ('On'/'Off') for the given relay
	output — a plain HTTP GET, no retry or verification here (see
	wallbox_switch for that). Returns True if the HTTP request itself
	succeeded (status 200); says nothing about whether the relay actually
	changed — Tasmota accepts the command over HTTP independently of whether
	the physical relay responds."""
	try:
		action = 'On' if on else 'Off'
		resp = get(url='http://%s/cm?cmnd=Power%s%%20%s' % (ip, output, action), timeout=10)
		return resp.status_code == 200
	except Exception as e:
		if verbose: print('tasmota set command failed:', e)
		return False


def wallbox_switch(ip, output, on):
	"""Set the wallbox relay to 'on'/'off' and verify it actually took effect
	by re-querying Tasmota's own relay status (tasmota_power) — up to 3
	attempts, 30 s apart, since a relay set command failing silently (device
	briefly unreachable, WiFi hiccup) must not be mistaken for success: the
	owner marker (see main()) is only ever set after a verified state change,
	never after just sending the command. Returns True once the verified
	state matches 'on', False if all 3 attempts failed to produce it."""
	for attempt in range(3):
		tasmota_set_power(ip, output, on)
		actual = tasmota_power(ip, output)
		if actual == on:
			return True
		if attempt < 2:
			sleep(30)
	if verbose: print('wallbox switch to %s failed after 3 attempts' % ('on' if on else 'off'))
	return False


def recent_pv_power(minutes=15):
	"""Average PV power volkszähler reports over the last 'minutes' (the PV
	channel already configured in vz_chans). Used by write_timer to net the
	quarter-hour energy budget against what the array is ACTUALLY producing
	right now, rather than against expected_pv[hour] — an hourly mean.

	The distinction matters on the ramp hours at each end of the day, which
	are also, routinely, the marginal red hours the budget is there to guard.
	Around sunset the modelled hourly mean can sit close to basic_load while
	real output is already far below it, leaving a budget of a few Wh for a
	quarter hour in which the battery should be carrying the house — the
	shortfall then comes from the grid during a red hour, the exact inverse
	of the point of this program. A measurement has no such lag: it tracks
	the ramp, and passing clouds, by construction.

	Returns None on any failure, which makes write_timer fall back to the
	forecast — the previous behaviour, never a hard error. The forecast stays
	in charge of everything about the FUTURE (reserve, dirtiest hour,
	precharge); only the budget for the slot happening right now uses this."""
	end   = datetime.today().replace(microsecond=0)
	begin = end - timedelta(minutes=minutes)
	try:
		url = ('http://' + conf['vz_host_port'] + '/data.json?from='
		       + str(int(begin.timestamp())).ljust(13, '0') + '&to='
		       + str(int(end.timestamp())).ljust(13, '0') + '&uuid[]=' + conf['vz_chans']['PV'])
		row = get(url=url, timeout=VZ_TIMEOUT_S).json()['data'][0]
		if row.get('average') is not None:
			return abs(float(row['average']))		# sign convention varies by channel; production is a magnitude here
		tuples = row['tuples']
		return abs(sum(v for _ts, v, _s in tuples) / len(tuples))
	except Exception as e:
		if verbose: print('measured PV unavailable (%s) — budget falls back to the forecast' % e)
		return None


def battery_min_voltage(minutes):
	"""Lowest battery voltage volkszähler reports over the last 'minutes'
	(the Vbat channel already configured for get_vz_bat_cap). Used only by
	the wallbox voltage path — a failure returns None rather than dying,
	since an unavailable reading must only disable that one optional path,
	never the run itself."""
	end   = datetime.today().replace(microsecond=0)
	begin = end - timedelta(minutes=minutes)
	url = ('http://' + conf['vz_host_port'] + '/data.json?from='
	       + str(int(begin.timestamp())).ljust(13, '0') + '&to='
	       + str(int(end.timestamp())).ljust(13, '0') + '&uuid[]=' + conf['vz_chans']['Vbat'])
	try:
		row = get(url=url, timeout=VZ_TIMEOUT_S).json()['data'][0]
		if row.get('min'):
			return row['min'][1]
		return min(v for _ts, v, _s in row['tuples'])	# no aggregate min: derive it from the raw samples
	except Exception as e:
		if verbose: print('wallbox: battery voltage unavailable (%s) — voltage path skipped' % e)
		return None


def wallbox_energy_ok(content, wallbox_reserve, margin, engaged):
	"""The energy-based part of the wallbox switch-on decision (see
	wallbox_decide): whether enough battery content is left over the
	wallbox-specific reserve.

	Two thresholds, like the voltage path: switching ON demands
	WALLBOX_ENERGY_ON_FACTOR margins of headroom, holding only one. A single
	margin is exactly one run's worth of wallbox draw, so a switch-on decided
	on less than that is undone by its own consequence at the very next run —
	the wallbox would flap on and off at the quarter hour without ever
	charging anything worth the name.

	'engaged' tracks THIS path only (see read_wallbox_energy_engaged), not
	the shared owner marker: the lower holding threshold must not prop up a
	relay some other path switched on."""
	headroom = margin if engaged else WALLBOX_ENERGY_ON_FACTOR * margin
	return (content - headroom) > wallbox_reserve


def wallbox_voltage_ok(min_v, engaged):
	"""The voltage-based half of the wallbox switch-on decision (see
	wallbox_decide): True while the battery has not dropped below the
	threshold over the last WALLBOX_V_WINDOW_MIN minutes — a full battery
	still absorbing PV, where a wallbox can charge on surplus that would
	otherwise go to waste.

	Two thresholds, not one: 'engaged' selects the lower WALLBOX_V_OFF
	value once this path is itself what is holding the wallbox on. Without
	that hysteresis the path would oscillate at the quarter-hour, since the
	wallbox's own load is exactly what pulls the voltage back under the ON
	threshold it just cleared.

	'engaged' deliberately tracks THIS path only (see
	read_wallbox_voltage_engaged), not the shared owner marker: keying the
	lower threshold to the marker would let it hold on a relay the dirt%
	path switched on for a clean grid, long after that grid turned dirty.

	Both thresholds are per-cell and scaled by cell_count, like the
	empty-battery anchor in get_vz_bat_cap — a fixed pack voltage would
	silently mean something different on a battery that is not 16S."""
	if min_v is None:
		return False
	per_cell = WALLBOX_V_OFF_PER_CELL if engaged else WALLBOX_V_ON_PER_CELL
	return min_v >= per_cell * conf.get('cell_count', 16)


def _read_marker_file():
	"""Raw contents of the wallbox marker file (see read_wallbox_marker and
	read_wallbox_voltage_engaged, which each read one key out of it).
	Missing/unreadable file returns an empty dict, so every key falls back
	to its own default."""
	try:
		with open(join(dirname(__file__), 'dirt_wallbox_marker.json'), 'r') as fi:
			return json_load(fi)
	except Exception:
		return {}


def read_wallbox_energy_engaged():
	"""Whether the energy path itself is currently what holds the wallbox on
	— the hysteresis state for wallbox_energy_ok, kept separate from the
	ownership marker for the same reason voltage_on is. False when absent, so
	a first run starts on the stricter switch-on headroom."""
	return bool(_read_marker_file().get('energy_on', False))


def read_wallbox_voltage_engaged():
	"""Whether the voltage path itself is currently what holds the wallbox
	on — the hysteresis state for wallbox_voltage_ok, kept separate from the
	ownership marker on purpose (see wallbox_voltage_ok). False when absent,
	so a first run starts on the higher ON threshold."""
	return bool(_read_marker_file().get('voltage_on', False))


def read_wallbox_marker():
	"""Whether dirt_shift itself is the reason the wallbox relay is currently
	on — a small persistent marker (see main()), separate from the relay's
	real state, since dirt_shift may only ever switch off a relay it
	switched on itself: a manual activation must never be undone by
	dirt_shift, no matter how dirty the grid gets. Returns False (foreign,
	untouched) if no marker file exists yet — including on the very first
	run ever, so an already-on relay found with no prior history is treated
	as foreign rather than assumed to be dirt_shift's own."""
	return bool(_read_marker_file().get('dirt_shift_on', False))


def write_wallbox_marker(on=None, voltage_on=None, energy_on=None):
	"""Persist the wallbox marker file — the ownership flag (see
	read_wallbox_marker) and the per-path hysteresis states of the energy and
	voltage paths (see read_wallbox_energy_engaged /
	read_wallbox_voltage_engaged). Any may be omitted, in which case its
	stored value is kept: they are written at different points of a run and
	must not clobber one another. Best-effort: a failed write only means the
	next run re-derives the wrong assumption once, not a hard failure —
	never raises further."""
	data = _read_marker_file()
	if on is not None: data['dirt_shift_on'] = bool(on)
	if voltage_on is not None: data['voltage_on'] = bool(voltage_on)
	if energy_on is not None: data['energy_on'] = bool(energy_on)
	try:
		with open(join(dirname(__file__), 'dirt_wallbox_marker.json'), 'w') as fo:
			json_dump(data, fo)
	except Exception as e:
		if verbose: print('failed to write wallbox marker:', e)


def _dirt_rank_pct(dirt_now, all_dirt):
	"""Where dirt_now sits among the covered hours of the rolling window, as a
	percentile: 0 means nothing in the window is cleaner, 100 that nothing is
	dirtier. None if dirt_now is None or no hour has a value at all.

	A rank, not a share of the median, because dirt% is a signed quantity
	((1 - renewables/load) * 100) that goes NEGATIVE whenever renewable
	generation exceeds load — routinely so on a windy, sunny day. A
	'percent of the median' test silently assumes the median is positive:
	with a positive median, a factor below 1 lowers the bar under it (the
	intended 'be pickier than average'), but with a negative median the same
	factor RAISES it above the median instead, inverting the whole test and
	locking the wallbox out precisely on the cleanest days. Percentages of a
	signed quantity carry no stable meaning at all.

	A rank has none of that: it is defined on the ordering alone, so it is
	immune to sign, offset and scale. It also gives the threshold a meaning
	that can be set without knowing anything about the data — 'only the
	cleanest N percent of the window' — instead of a factor whose effect
	depends on where the median happens to land.

	Ties count as cleaner, so identical hours are treated alike rather than
	one of them being ranked out arbitrarily."""
	valid = [d for d in all_dirt if d is not None]
	if dirt_now is None or not valid:
		return None
	cleaner = sum(1 for d in valid if d < dirt_now)
	return 100.0 * cleaner / len(valid)


def wallbox_should_be_on(dirt_now, all_dirt):
	"""The dirt%-based part of the switch-on decision (see wallbox_decide,
	which combines it with the two battery-based parts): the current hour
	must rank among the cleanest wallbox_cleanest_pct percent of the rolling
	window (see _dirt_rank_pct) AND sit at or below the absolute
	wallbox_absolute_max.

	This path answers exactly one question — is now a good time to charge the
	car from the GRID? — and nothing about the battery enters it. Whether the
	battery could be hurt by the wallbox is a separate question with a
	separate answer: while the wallbox runs without the battery having any
	surplus of its own, main() caps battery discharge to the house's share
	(see the 'limit' downgrade there), so the car's draw is served by the
	grid by construction. Gating this path on the discharge mode instead
	used to get that backwards: 'free' — the one mode that leaves the
	battery fully exposed — permitted the wallbox, while 'stop', where the
	battery cannot be touched at all, forbade it.

	Precharge (see precharge_ac_pct) never enters this decision — the two
	are fully independent. False if dirt_now or every entry in all_dirt is
	unavailable (no SMARD coverage for that hour)."""
	rank = _dirt_rank_pct(dirt_now, all_dirt)
	if rank is None:
		return False
	return (rank <= conf['wallbox_cleanest_pct']
	        and dirt_now <= conf['wallbox_absolute_max'])

def wallbox_decide(dirt_now, all_dirt, energy_ok=False, voltage_ok=False):
	"""Whether the wallbox relay should be on right now — the single decision
	main() acts on, both for switching it on and for switching it off again.

	  should_on = wallbox_should_be_on(dirt_now, all_dirt)
	              OR energy_ok OR voltage_ok

	Each path names where the car's energy would come from, and that is the
	whole of what the decision is about:
	  - dirt path (wallbox_should_be_on): the grid is clean right now —
	    charge from the grid.
	  - energy path (wallbox_energy_ok): the battery holds more than its
	    reserve plus headroom — the car may draw on that surplus. Its
	    reserve is computed as main()'s own is, but WITHOUT the expected-
	    surplus credit main() grants: a running wallbox is exactly the
	    consumer that would eat that surplus before it reaches the battery.
	    Deliberately the stricter of the two, never the laxer — a lower bar
	    for the wallbox than for the battery is what once let one empty the
	    battery overnight.
	  - voltage path (wallbox_voltage_ok): the battery is full and still fed
	    by PV — surplus that would otherwise go to waste can go into the car.

	Protecting the battery from the car is NOT part of this decision. It is
	handled where battery discharge is actually controlled: main() downgrades
	a 'free' mode to 'limit' while the wallbox runs on the dirt path alone,
	so the battery covers the house's share and the grid covers the car. The
	two questions — good time to charge? / may the battery feed it? — are
	kept apart on purpose; folding the second into the first is what made
	the old mode gate point the wrong way.

	Because all paths run continuously, a relay dirt_shift itself switched
	on stays on as long as ANY path still holds, and switches off only once
	all of them fail at the same time. Manual activations are never touched
	(see read_wallbox_marker). energy_ok and voltage_ok are computed by
	main(), which persists their hysteresis state, and only combined here —
	so what main() reports in -v is by construction the value decided on.
	Precharge never enters this decision."""
	return wallbox_should_be_on(dirt_now, all_dirt) or energy_ok or voltage_ok

def _bridge_hours(now, zones, basic_load, expected_pv):
	"""Hours (as (hour, fraction) pairs), from now up to (not including) the
	first PV-surplus hour — the same boundary red_window_demand's scan stops
	at (see there): scanning forward from the current hour (wrapping past
	midnight), the first hour whose expected PV exceeds its basic_load ends
	the window, since the battery genuinely refills from there on. Every
	hour before that boundary is included, whether 'zones' classifies it red
	or not, so summing basic_load/expected_pv over these same hours gives an
	energy-balance projection directly comparable to a reserve target from
	red_window_demand — both span the identical window. The current hour
	counts only its remaining fraction (minutes left until the top of the
	hour); every full hour after that counts whole. Empty if the current hour
	is itself a surplus hour."""
	hours, h, first = [], now.hour, True
	for _ in range(24):
		if expected_pv[h] > basic_load[h]:
			break
		hours.append((h, (60 - now.minute) / 60.0 if first else 1.0))
		first = False
		h = (h + 1) % 24
	return hours


def reserved_red_hours(now, zones, basic_load, expected_pv):
	"""The red hours the reserve is being held for: those red_window_demand
	counts (forward from now up to the first surplus hour), or, if that yields
	none because now sits inside a surplus stretch, the next contiguous red
	block upcoming_red_demand would use. A list of hour indices, possibly
	empty. Shared by the 'stop' gap test and precharge, so both weigh the same
	block."""
	hours, h = [], now.hour
	for _ in range(24):
		if expected_pv[h] > basic_load[h]:
			break
		if zones[h] == 'red':
			hours.append(h)
		h = (h + 1) % 24
	if not hours:											# now is mid-surplus: fall back to the next red block
		h, in_block = now.hour, False
		for _ in range(24):
			if zones[h] == 'red':
				in_block = True
				hours.append(h)
			elif in_block:
				break
			h = (h + 1) % 24
	return hours


def reserved_red_dirt(now, zones, ratio, basic_load, expected_pv):
	"""Mean dirt% of the red hours the reserve is held for (see
	reserved_red_hours). None if none of them has SMARD coverage.

	This is what 'stop' in a green hour weighs itself against (see main()):
	holding the battery back now only pays in proportion to how much dirtier
	those hours are than this one. The mean rather than the dirtiest single
	hour, because the reserve is spent across the whole block, not in one
	hour — and because the dirtiest hour already gets 'free' on its own."""
	hours = reserved_red_hours(now, zones, basic_load, expected_pv)
	vals = [(1.0 - ratio[x]) * 100.0 for x in hours if ratio[x] is not None]
	return sum(vals) / len(vals) if vals else None


def red_window_demand(basic_load, now, zones, expected_pv):
	"""Wh the basic_load draws across every red hour between now and the next
	PV production phase — scanning forward from the current hour (wrapping
	past midnight), red hours accumulate their demand net of any PV still
	expected in them (pvpt covers that part directly, so it need not also be
	reserved from the battery), and the scan ends at the first hour whose
	expected PV exceeds its basic_load: from that surplus hour on the battery
	is genuinely refilling, so any later red span is covered by the coming
	yield, not by yesterday's charge — holding current content for it would
	only block storage room. Several separate red spans before that point
	(e.g. evening red and night red with a green gap between them) are all
	summed, since nothing refills the battery in between. On a day so
	dull that expected PV never exceeds load, no surplus hour exists and all
	red hours of the rolling 24 h are reserved for — correct, as no refill is
	coming. 'zones' is a rolling 24-hour array anchored at 'now' (see
	read_smard_zones, read_radiation_forecast): an hour before
	now.hour is really tomorrow's occurrence of that hour, with tomorrow's
	own classification/value where available. A deficit red hour and the
	(rare) surplus red hour offset each other within the window; only the
	final total is floored at zero. Zero if no red hour lies before the
	surplus point."""
	demand = 0.0
	h = now.hour
	for _ in range(24):
		pv = expected_pv[h]
		if pv > basic_load[h]:
			break										# PV surplus hour: the battery refills from here on
		if zones[h] == 'red':
			demand += basic_load[h] - pv
		h = (h + 1) % 24
	return max(0.0, demand)


def surplus_before_next_red(now, zones, basic_load, expected_pv):
	"""Expected PV surplus (expected_pv - basic_load, only where positive)
	over the hours between now and the start of the next red block — the
	charge the battery can be expected to gain on its own before that block
	begins. Stops at the first red hour, so surplus inside the red block
	itself is not counted; that share is already netted per hour by
	upcoming_red_demand. 0 when a red hour starts right away.

	This restores, as an arithmetic test, the assumption red_window_demand
	used to make outright: that a battery heading into a surplus stretch
	refills itself and needs no reserve held back beforehand. That
	assumption is right on a sunny day and was the basis of the intended
	daily cycle — discharge overnight and through the morning, charge from
	midday on, discharge again in the red evening. It only failed when a
	load emptied the battery faster than the surplus refilled it, so rather
	than dropping the assumption (which would keep the battery idle through
	a morning whose surplus covers the gap many times over), main() now
	subtracts this figure and keeps whatever demand the surplus does not
	cover.

	Deliberately NOT applied to the wallbox's own reserve check (see
	wallbox_decide): a running wallbox is precisely the consumer that would
	eat this surplus before it ever reaches the battery, so for that check
	the surplus must not be assumed available."""
	surplus, h = 0.0, now.hour
	for _ in range(24):
		if zones[h] == 'red':
			break
		surplus += max(0.0, expected_pv[h] - basic_load[h])
		h = (h + 1) % 24
	return surplus


def upcoming_red_demand(now, zones, basic_load, expected_pv):
	"""Rough advance estimate of the very next red span's total demand —
	summed even while 'now' itself sits within an ongoing PV-surplus
	stretch, unlike red_window_demand, which stops at the first surplus
	hour and so reads 0 for as long as 'now' remains inside one. Scans
	forward from now, skipping every hour before the next red hour without
	accumulating — surplus hours in between do NOT end the scan here, that
	is the whole point — then sums basic_load[h] - expected_pv[h] (floored
	at 0 per hour, still net of pvpt during the red hours themselves) for
	every red hour of that next contiguous red block, stopping at its end.
	Deliberately does not look past that first block into any further,
	separate red span later in the rolling day — a rough same-night
	estimate, not the full red_window_demand accounting. Meant to be
	compared against current battery content directly, with no projected
	future surplus added on top (see wallbox_decide) — the conservative
	choice, since any assumed surplus between now and red is exactly what a
	running wallbox could itself consume before it ever reaches the
	battery. Wraps past midnight. 0 if no red hour exists anywhere in the
	next 24 hours."""
	demand, h, in_block = 0.0, now.hour, False
	for _ in range(24):
		if zones[h] == 'red':
			in_block = True
			demand += max(0.0, basic_load[h] - expected_pv[h])
		elif in_block:
			break
		h = (h + 1) % 24
	return demand


def dirtiest_hour(now, zones, basic_load, expected_pv, ratio):
	"""The hour-of-day (integer) of the single dirtiest red hour between now
	and the next PV-surplus hour (see _bridge_hours) — the one hour served
	unrestricted when the reserve is running short (see main()); every other
	red hour in the window is capped to basic_load instead. 'Dirtiest' is
	the lowest ratio (highest dirt%, see write_dirtiness_to_vz); ties go to
	the chronologically earliest hour in the window (scan order, not the raw
	hour-of-day number — an hour before now.hour is really tomorrow's
	occurrence of it, so it is later in the window despite the smaller
	number). Green hours in the window are never candidates. None if the
	window has no red hour at all — e.g. the reserve is comfortable enough
	that main() never calls this, or a dull day's window is entirely red
	(then the first hour scanned is simply the answer whenever ratios tie
	throughout)."""
	candidates = [h for h, _ in _bridge_hours(now, zones, basic_load, expected_pv) if zones[h] == 'red']
	best_h, best_ratio = None, None
	for h in candidates:									# already in chronological scan order
		r = ratio[h] if ratio[h] is not None else -1.0	# an uncovered hour ranks as the dirtiest
		if best_ratio is None or r < best_ratio:
			best_h, best_ratio = h, r
	return best_h


def precharge_ac_pct(now, zones, basic_load, expected_pv, ratio, content, reserve):
	"""The ac_% to write for this run's timer line — 100 (no restriction)
	unless the optional precharge path (precharge_enabled) is active and this
	hour qualifies, in which case a value below 100 (but never below
	PRECHARGE_AC_FLOOR) diverts PV surplus that would otherwise go to pvpt
	into the battery instead. The one deliberate exception to dirt_shift's
	pvpt guarantee (see write_timer), spanning the window's very cleanest
	hours. Only ever considered while the current hour is green (main() calls
	this only then).

	Precharge earns its round-trip loss in exactly one situation: more PV
	surplus is coming than the battery can hold, so some of it will otherwise
	be thrown away. Only then is diverting a rescue rather than a detour —
	pvpt itself is lossless, the battery path is not, so shifting surplus into
	the battery that would have fit anyway just wastes the round-trip. The
	gate is therefore a genuine overflow test:

	  1. RANK — this hour must be among the cleanest precharge_cleanest_pct %
	     of the window (see _dirt_rank_pct), so only the cleanest hours are
	     ever throttled, and several can share the load across a run.

	  2. GAP — this hour's dirt% at least STOP_MIN_DIRT_GAP points below the
	     mean dirt% of the red hours charged for (see reserved_red_hours):
	     the clean/dirty spread must beat the round-trip loss. Difference, not
	     fraction, so it stays sign-safe.

	  3. OVERFLOW — the TOTAL expected charging surplus of every qualifying
	     clean hour, THIS ONE INCLUDED, must exceed the battery's free room
	     (battery_capacity_wh - content). Only the excess over the free room
	     would be lost, and only that excess is worth diverting. Counting the
	     current hour is what the earlier version got wrong: excluding it let
	     each big surplus hour exclude itself from its own overflow test and
	     so believe it was needed, throttling the sunniest hours on a day
	     where the battery fills itself several times over.

	The amount worth diverting is the overflow, spread across the qualifying
	hours in proportion to each one's surplus, and capped so pvpt is only ever
	reduced by the diverted watts — never below PRECHARGE_AC_FLOOR, so a
	precharge decision can never zero out the inverter feed and stall
	zeroinput. Self-limiting and memoryless: as content rises the free room
	shrinks, the overflow with it, and ac_% climbs back to 100."""
	if not conf.get('precharge_enabled', False):
		return 100

	all_dirt = [(1.0 - r) * 100.0 if r is not None else None for r in ratio]
	dirt_now = all_dirt[now.hour]
	rank = _dirt_rank_pct(dirt_now, all_dirt)
	if rank is None or rank > conf.get('precharge_cleanest_pct', 25):
		return 100											# 1. not among the window's cleanest hours

	red_hours = reserved_red_hours(now, zones, basic_load, expected_pv)
	red_vals = [all_dirt[h] for h in red_hours if all_dirt[h] is not None]
	if not red_vals:
		return 100											# no red block to charge for
	dirt_red = sum(red_vals) / len(red_vals)
	if dirt_now is None or dirt_red - dirt_now < STOP_MIN_DIRT_GAP:
		return 100											# 2. clean gap too small to beat the round-trip loss

	# 3. Total surplus of every qualifying clean hour from now up to the red
	# block, THIS hour included. Overflow is what will not fit — only that is
	# lost, and only that is worth diverting. Scans forward from now and stops
	# at the red block; _bridge_hours is unusable here because it ends at the
	# first surplus hour, which is often now itself.
	pc = conf.get('precharge_cleanest_pct', 25)
	this_surplus = max(0.0, expected_pv[now.hour] - basic_load[now.hour])
	total_surplus = 0.0
	h = now.hour
	for _ in range(24):
		if zones[h] == 'red':
			break
		rk = _dirt_rank_pct(all_dirt[h], all_dirt)
		if rk is not None and rk <= pc:
			total_surplus += max(0.0, expected_pv[h] - basic_load[h])
		h = (h + 1) % 24

	free_room = max(0.0, conf['battery_capacity_wh'] - content)
	overflow = total_surplus - free_room
	if overflow <= 0 or total_surplus <= 0 or this_surplus <= 0:
		return 100											# nothing overflows, or nothing to divert this hour

	# Divert this hour's share of the overflow (proportional to its surplus),
	# reducing pvpt by exactly those watts.
	divert = overflow * (this_surplus / total_surplus)
	pass_through = max(0.0, expected_pv[now.hour] - divert)
	frac = pass_through / expected_pv[now.hour] if expected_pv[now.hour] > 0 else 1.0
	return max(PRECHARGE_AC_FLOOR, round(100.0 * frac))



def _hourly_debug_table(now, pv_curve, radiation, expected_pv, basic_load, grid_data, dirt_written=None, radiation_stale=None):
	"""Print one aligned table (hour 0-23) combining the PV reference curve,
	the shortwave-radiation forecast, the clear-sky index derived from it
	(see scaled_pv_curve/clear_sky_ghi), expected PV, basic_load, whether
	that hour charges or discharges, and the SMARD-derived dirtiness/zone
	(the same rolling array _bridge_hours/red_window_demand/dirtiest_hour
	use) — so everything the discharge decision draws on is visible at a
	glance, in one place instead of six separate lists. 'balance' is
	expected_pv[h] - basic_load[h] (signed): positive means this hour's own
	generation exceeds its own consumption (a surplus/charging hour),
	negative means it falls short (a deficit/discharging hour) — the exact
	quantity 'chg' derives its L/D verdict from. Left blank ('-') whenever
	the displayed 'exp_PV' itself rounds to 0 (no meaningful PV, typically
	overnight) — even if the unrounded value is a nonzero fraction: the
	balance would then just restate -basic_load[h], already visible in
	'basic_ld', with a precision the displayed exp_PV doesn't itself carry.
	'chg' is 'L' (lädt)
	if expected_pv[h] > basic_load[h], else 'D' (discharge) — the exact
	boundary _bridge_hours/red_window_demand scan for (a PV-surplus hour
	ends their window). 'dirt%' is (1 - ratio) * 100 (see
	write_dirtiness_to_vz): 0 at ratio 1 (renewables exactly cover load),
	negative on a renewable surplus (ratio > 1), rising toward 100 as the
	renewable share drops toward 0. The current hour is marked with '*'. Its
	dirt% value carries a second marker for the volkszähler write of that
	same value (see write_dirtiness_to_vz, whose return value 'dirt_written'
	is passed here): '*' prefix if it reached volkszähler, '!' if the write
	failed, no prefix if no UUID is configured and nothing was attempted.
	The one red hour dirtiest_hour would pick in the window from now up to
	the next PV-surplus hour (see there) carries a leading '!' on its 'D'
	tag (`!D`) — that is the hour served unrestricted if the reserve falls short;
	every other red hour in the window would be capped instead. The cleanest
	'L' hour of the full 24-hour table (highest ratio among all charging
	hours) carries the same leading '!' on its 'L' tag (`!L`) — purely
	informational, not read by main()'s decision, since dirtiest_hour's own
	window never contains an 'L' hour to begin with (it ends at the first
	one it meets). An hour before now.hour (the rolling array's 'tomorrow'
	portion, see read_radiation_forecast/read_smard_zones) that is still
	really today's data standing in because no tomorrow-specific value was
	available yet carries a leading '.' — on 'rad_Wm2' if it is the radiation
	forecast that is stale for that hour (radiation_stale, from
	read_radiation_forecast), on 'dirt%' if it is the SMARD classification
	(grid_data['stale'], from read_smard_zones). The two are independent and
	can differ per hour, since they come from separate sources refreshed on
	separate schedules. A final line sums expected_pv and basic_load each
	across all 24 hours, their difference (the day's net balance), and the
	unweighted average dirt% across every SMARD-covered hour — a quick
	overall read on the day's expected yield, consumption, and cleanliness,
	rolling window included."""
	zones = grid_data['zones']
	ratio = grid_data['ratio']
	smard_stale = grid_data.get('stale', set())
	radiation_stale = radiation_stale or set()
	dh = None
	if expected_pv is not None and basic_load is not None:
		dh = dirtiest_hour(now, zones, basic_load, expected_pv, ratio)
	# purely informational (not used by main()'s decision, unlike dh above):
	# the cleanest charging hour of the day, marked the same way as dh — not
	# drawn from _bridge_hours' window, since that window ends at the first
	# surplus hour and so never contains an 'L' hour at all; this instead
	# looks across the full 24-hour table.
	cleanest_l = None
	if expected_pv is not None and basic_load is not None:
		best_ratio = None
		for h in range(24):
			if expected_pv[h] <= basic_load[h] or ratio[h] is None:
				continue
			if best_ratio is None or ratio[h] > best_ratio:
				cleanest_l, best_ratio = h, ratio[h]
	print('')
	# Rows run chronologically from the current hour, wrapping past midnight
	# into tomorrow: that is the order every calculation reads the rolling
	# window in (red_window_demand, upcoming_red_demand, dirtiest_hour all scan
	# forward from now.hour), and the wrap from 23 to 0 is where tomorrow
	# starts. Listed 0..23 instead, the rows ABOVE the current hour would be
	# tomorrow's forecast while looking exactly like this morning's history —
	# with nothing to say so once tomorrow's data has arrived and the '.' stale
	# prefix is gone.
	if grid_data.get('emergency'):
		print('*** EMERGENCY ZONES — modelled from weather (solar+wind) + load profile, NOT SMARD data ***')
	if grid_data.get('emergency'):
		print('*** zones below are MODELLED (SMARD unavailable) — solar+wind forecast vs. a fixed load profile, not real grid data ***')
	print('%-3s %8s %8s %5s %8s %8s %8s %4s %6s %-8s' % ('hr', 'PV_curve', 'rad_Wm2', 'clr%', 'exp_PV', 'basic_ld', 'balance', 'chg', 'dirt%', 'zone'))
	for k in range(24):
		h = (now.hour + k) % 24
		pv  = round(pv_curve[h]) if pv_curve is not None else '-'
		r   = radiation[h] if (radiation is not None and h < len(radiation)) else None
		if r is not None:
			d = now if h >= now.hour else now + timedelta(days=1)		# same rolling-date rule as scaled_pv_curve
			csghi = clear_sky_ghi(d, h + 0.5)
			rad_s = str(round(r))
			clr   = ('%d' % round(min(100.0, 100.0 * r / csghi))) if csghi > 0 else '-'
		else:
			rad_s, clr = '-', '-'
		if h in radiation_stale: rad_s = '.' + rad_s			# still today's forecast, no tomorrow value yet
		exp = round(expected_pv[h]) if expected_pv is not None else '-'
		bl  = round(basic_load[h]) if basic_load is not None else '-'
		if expected_pv is not None and basic_load is not None:
			bal = '-' if exp == 0 else '%+d' % (exp - bl)	# from the ROUNDED column values, so the row adds up as displayed: %d on the raw difference truncates toward zero and disagrees by 1 wherever rounding went the other way. exp_PV showing 0: balance would be -basic_load, redundant
			chg = 'L' if expected_pv[h] > basic_load[h] else 'D'
			if h == dh: chg = '!' + chg							# the dirtiest red hour in the window
			if h == cleanest_l: chg = '!' + chg					# the cleanest charging hour of the day
		else:
			bal, chg = '-', '-'
		ratio_h = ratio[h]
		dirt_s = ('%.0f' % ((1.0 - ratio_h) * 100)) if ratio_h is not None else '-'
		if h == now.hour and dirt_written is not None:
			dirt_s = ('*' if dirt_written else '!') + dirt_s	# volkszähler write of this hour's value
		if h in smard_stale: dirt_s = '.' + dirt_s				# still today's ratio, no tomorrow value yet
		print('%2d  %8s %8s %5s %8s %8s %8s %4s %6s %-8s' % (h, pv, rad_s, clr, exp, bl, bal, chg, dirt_s, zones[h]))	# the current hour is always the first row, no marker needed
	if expected_pv is not None or basic_load is not None or any(r is not None for r in ratio):
		dash8 = '-' * 8
		print('%-3s %8s %8s %5s %8s %8s %8s' % ('', '', '', '', dash8, dash8, dash8))
		# summed over the ROUNDED per-hour values, i.e. exactly the numbers in
		# the column above, so the table adds up for a reader checking it by
		# hand — summing the raw floats and rounding once at the end can land
		# a few Wh off the visible column.
		exp_sum = sum(round(x) for x in expected_pv) if expected_pv is not None else None
		bl_sum  = sum(round(x) for x in basic_load) if basic_load is not None else None
		bal_sum = (exp_sum - bl_sum) if (exp_sum is not None and bl_sum is not None) else None
		valid_ratio = [r for r in ratio if r is not None]
		dirt_avg = sum((1.0 - r) * 100.0 for r in valid_ratio) / len(valid_ratio) if valid_ratio else None
		print('%-3s %8s %8s %5s %8s %8s %8s %4s %6s' % (
			'', '', '', '',
			('%d' % exp_sum) if exp_sum is not None else '-',
			('%d' % bl_sum) if bl_sum is not None else '-',
			('%+d' % bal_sum) if bal_sum is not None else '-',
			'', ('Ø %.0f' % dirt_avg) if dirt_avg is not None else '-'))
	print('')


def main():
	vz = read_average()
	basic_load = vz['basic_load']
	now = datetime.now()
	pv_curve = read_pv_curve()
	radiation, radiation_stale = read_radiation_forecast()
	expected_pv = scaled_pv_curve(pv_curve, radiation, now)	# drives red_window_demand/dirtiest_hour below
	grid_data = read_smard_zones()					# before get_vz_bat_cap, so all cache notices print together
	if grid_data is None:
		# SMARD gives nothing, not even from cache: model zones from the weather
		# forecast rather than dropping straight to the free timer. Clearly a
		# fallback — flagged below and in the table — never mistaken for data.
		grid_data = emergency_zones(expected_pv, basic_load)
		if grid_data is not None:
			print('*** SMARD unavailable — MODELLED emergency zones (solar+wind forecast, load profile); not real grid data ***')
	if grid_data is None:
		die('SMARD zone data unavailable (fetch failed, no cache newer than one day, weather model unavailable)', conf['timer.txt'])
	_voltage, content = get_vz_bat_cap()
	# content is None means the battery energy could not be reconstructed
	# (volkszähler gap). The battery side genuinely cannot be steered without
	# it — reserve, mode and precharge all hinge on content — so it goes free.
	# But the wallbox side is redundant: its dirt path needs no content, so the
	# run continues to the wallbox block below rather than aborting, and only
	# the content-dependent wallbox path (energy) drops out there. If SMARD was
	# fine, this is the one place a missing battery reading no longer kills the
	# whole run.
	battery_steerable = content is not None

	r = grid_data['ratio'][now.hour]
	slot = now.replace(second=0, microsecond=0, minute=(now.minute // SLOT_MINUTES) * SLOT_MINUTES)
	slot_ms = int(slot.timestamp() * 1000)
	# The backdated value closes off the hour that just ended, so its timestamp
	# comes from the HOUR boundary and the write only happens on the run that
	# starts an hour. Deriving it from the slot instead (slot_ms - 1000) only
	# lands on the hour boundary while this fires exactly once per hour: any
	# extra run plants the PREVIOUS hour's value in the middle of the current
	# one, and the logged curve turns into a sawtooth between the two.
	backdate_ratio = grid_data.get('backdate_ratio')
	if backdate_ratio is not None and now.minute < SLOT_MINUTES:
		hour_ms = int(now.replace(minute=0, second=0, microsecond=0).timestamp() * 1000)
		write_dirtiness_to_vz((1.0 - backdate_ratio) * 100, hour_ms - 1000)	# flat step: previous hour's value, 1s before the hour boundary
	dirt_written = write_dirtiness_to_vz((1.0 - r) * 100, slot_ms) if r is not None else None
	if debug:
		_hourly_debug_table(now, pv_curve, radiation, expected_pv, basic_load, grid_data, dirt_written, radiation_stale)

	# decide this run's discharge mode. Two zones only (see _smard_zones_for_date):
	# 'green' (cleaner half of the day) and 'red' (dirtier half).
	#
	# the red reserve: basic_load demand over every red hour between now and
	# the next PV surplus phase (see red_window_demand), net of expected PV
	# during those hours, scaled by reserve_pct. Without a PV forecast (curve
	# never successfully computed, e.g. fresh install), a zero-PV day is
	# assumed instead — conservative, but keeps the reserve computable.
	#
	# upcoming_red_demand is taken as a floor under it, because the two have
	# complementary blind spots and neither dominates: red_window_demand
	# looks broadly but stops at the first surplus hour, so it reads 0 for as
	# long as 'now' sits inside a surplus stretch — on the assumption that
	# the coming yield will refill the battery by itself. That assumption
	# fails whenever a load (a running wallbox above all) drains the battery
	# faster than the surplus refills it: reserve 0 makes content > reserve
	# trivially true, mode goes free, and the battery is handed over
	# unlimited exactly when nothing is guarding it. upcoming_red_demand
	# still sees the next red block across that surplus and holds the line.
	# Conversely it looks only at that one contiguous block, while
	# red_window_demand sums several separate red spans (and, on a day with
	# no surplus at all, the whole rolling 24 h) — so it is the larger, and
	# correct, figure there. Taking the max keeps whichever is binding.
	zones = grid_data['zones']
	pv_for_reserve = expected_pv if expected_pv is not None else [0.0] * 24
	if not battery_steerable:
		# no content: the battery cannot be steered, so free it (write_free_timer
		# equivalent via the normal 'free' path) — but keep going for the wallbox.
		mode, detail, ac_pct = 'free', ' [battery content unavailable — free]', 100
		reserve = 0.0
		zone = zones[now.hour]
	else:
		_rw_demand = red_window_demand(basic_load, now, zones, pv_for_reserve)
		# upcoming_red_demand is the floor, but net of the surplus expected before
		# that red block even starts: the battery charges itself in the meantime,
		# and only what the surplus fails to cover has to be held back now. Without
		# this the floor would keep the battery idle right through a sunny morning
		# whose own yield covers the gap several times over — the very cycle
		# red_window_demand was built around (see surplus_before_next_red). The
		# wallbox check deliberately skips this subtraction.
		_up_raw = upcoming_red_demand(now, zones, basic_load, pv_for_reserve)
		_pv_before_red = surplus_before_next_red(now, zones, basic_load, pv_for_reserve)
		_up_demand = max(0.0, _up_raw - _pv_before_red)
		reserve = conf['reserve_pct'] * 0.01 * max(_rw_demand, _up_demand)
		zone = zones[now.hour]

		# green: charge, never discharge, as long as content has not yet reached
		# the reserve; once it has (content > reserve, strictly — sitting exactly
		# at the reserve still counts as not yet reached), free discharge resumes
		# until content drops back to the reserve.
		#
		# red: if content already covers the reserve, no restriction is needed —
		# free. If it falls short, the single dirtiest red hour in the window (see
		# dirtiest_hour) is served without limit — that is where the reserve, such
		# as it is, is spent — while every other red hour in the window is capped
		# by two independent limits at once (see write_timer): a discharge-rate
		# cap (limit_discharge_rate, Watt, config) and a fixed quarter-hour
		# energy budget (1/4 * (reserve_pct * basic_load[that hour] - expected
		# PV), Wh — scaled by reserve_pct and net of the PV still expected in
		# it, the same reserve_pct and netting red_window_demand already
		# applies to the reserve above). Anything above either
		# limit (e.g. EV charging) is forced onto the grid instead, preserving
		# content for the dirtiest hour. Since the window is rebuilt fresh every
		# run from the current 'now', a dirtiest hour already in the past simply
		# falls out of a later run's window — the next-dirtiest hour remaining
		# becomes free in its own right, without any extra bookkeeping.
		if zone == 'green':
			if content > reserve:
				mode, detail = 'free', ''
			else:
				# Hold the battery back ('stop', house on the grid now) only if the
				# red hours it is held for are meaningfully dirtier than this one —
				# otherwise let it cover the house ('limit'): a gap inside forecast
				# noise is not worth a grid import. A missing dirt value on either
				# side defaults to 'stop', the conservative side.
				_r_now = grid_data['ratio'][now.hour]
				_dirt_now = (1.0 - _r_now) * 100.0 if _r_now is not None else None
				_dirt_red = reserved_red_dirt(now, zones, grid_data['ratio'], basic_load, pv_for_reserve)
				_gap = (_dirt_red - _dirt_now) if (_dirt_now is not None and _dirt_red is not None) else None
				if _gap is None or _gap >= STOP_MIN_DIRT_GAP:
					mode = 'stop'
					detail = '' if _gap is None else ' [red block +%.0f dirt]' % _gap
				else:
					mode = 'limit'
					detail = ' [red block only +%.0f dirt < %g: limit, not stop]' % (_gap, STOP_MIN_DIRT_GAP)
			ac_pct = precharge_ac_pct(now, zones, basic_load, pv_for_reserve, grid_data['ratio'], content, reserve)
		else:
			if content >= reserve:
				mode = 'free'
				detail = ' (comfortable)'
			else:
				dh = dirtiest_hour(now, zones, basic_load, pv_for_reserve, grid_data['ratio'])
				mode = 'free' if dh is None or dh == now.hour else 'limit'
				detail = ' -> dirtiest  %s' % (('%02d:00' % dh) if dh is not None else '-')
			ac_pct = 100											# precharge only ever considered in green

	# ── wallbox, decided BEFORE the timer line is written ────────────────────
	# Its result feeds the discharge mode: while the wallbox runs on the dirt
	# path alone, a 'free' mode is downgraded to 'limit' so the battery keeps
	# covering the house's share and the car's draw lands on the grid. Deciding
	# the wallbox first is what lets that cap take effect in the same slot the
	# relay closes, rather than one run late.
	wallbox_on = False
	energy_ok = voltage_ok = False
	if conf.get('wallbox_enabled', False):
		dirt_now = (1.0 - r) * 100 if r is not None else None
		all_dirt = [(1.0 - x) * 100 if x is not None else None for x in grid_data['ratio']]
		marker = read_wallbox_marker()
		e_engaged = read_wallbox_energy_engaged()
		v_engaged = read_wallbox_voltage_engaged()
		# Each path is independent and redundant: a missing input drops only its
		# own path, the others still decide. energy needs content, voltage needs
		# a battery-voltage reading, dirt needs the (possibly modelled) zones.
		# Track which paths actually had the data to judge — if NONE did, the
		# relay is left exactly as it is (no switch either way), rather than
		# switching off on an absence of information.
		energy_ok = voltage_ok = False
		energy_available = content is not None
		if energy_available:
			# Same two estimates as the main reserve, but WITHOUT main()'s
			# surplus_before_next_red subtraction — deliberately stricter, not an
			# oversight: a running wallbox is exactly the consumer that would eat
			# that surplus before it ever reaches the battery, so it must not be
			# assumed available here. The asymmetry only ever runs this way round;
			# holding the wallbox to a LOWER bar than the battery is what let one
			# empty the battery overnight (see wallbox_decide).
			wallbox_reserve = conf['reserve_pct'] * 0.01 * max(
				red_window_demand(basic_load, now, zones, pv_for_reserve),
				upcoming_red_demand(now, zones, basic_load, pv_for_reserve))
			margin = conf['wallbox_typical_power'] * SLOT_HOURS
			energy_ok = wallbox_energy_ok(content, wallbox_reserve, margin, e_engaged)
		else:
			wallbox_reserve = margin = None
		min_v = battery_min_voltage(WALLBOX_V_WINDOW_MIN)
		voltage_available = min_v is not None
		voltage_ok = wallbox_voltage_ok(min_v, v_engaged)
		dirt_available = dirt_now is not None
		write_wallbox_marker(energy_on=energy_ok, voltage_on=voltage_ok)	# hysteresis states, tracked whether or not the relay itself changes
		should_on = wallbox_decide(dirt_now, all_dirt, energy_ok, voltage_ok)
		any_path_decidable = energy_available or voltage_available or dirt_available
		action = 'none'
		if not any_path_decidable:
			action = 'no data — relay left unchanged'
		elif should_on and not marker:
			action = 'switch on (verified)' if wallbox_switch(conf['wallbox_ip'], conf['wallbox_output'], True) else 'switch on FAILED'
			if action.endswith('(verified)'): write_wallbox_marker(True)
		elif not should_on and marker:
			action = 'switch off (verified)' if wallbox_switch(conf['wallbox_ip'], conf['wallbox_output'], False) else 'switch off FAILED'
			if action.endswith('(verified)'): write_wallbox_marker(False)
		# the state the relay is actually in for this slot, after switching:
		# on if we switched it on (verified) or it was ours and stays on
		wallbox_on = read_wallbox_marker()
		if verbose:
			rank_pct = _dirt_rank_pct(dirt_now, all_dirt)
			# each path reports its own verdict first, then the values it was reached from,
			# so the three lines read the same way and should_on below is just their OR
			print('wallbox: dirt_ok    %-5s   dirt%% %s (<%g)   rank %s%% (<%g)' % (
				wallbox_should_be_on(dirt_now, all_dirt),
				('%.0f' % dirt_now) if dirt_now is not None else '-', conf['wallbox_absolute_max'],
				('%.0f' % rank_pct) if rank_pct is not None else '-', conf['wallbox_cleanest_pct']))
			if energy_available:
				_headroom = margin if e_engaged else WALLBOX_ENERGY_ON_FACTOR * margin
				print('wallbox: energy_ok  %-5s   content %d - reserve %.0f - headroom %.0f = %.0f Wh (%s)' % (
					energy_ok, content, wallbox_reserve, _headroom,
					content - wallbox_reserve - _headroom, 'engaged' if e_engaged else 'idle'))
			else:
				print('wallbox: energy_ok  %-5s   (battery content unavailable — path skipped)' % energy_ok)
			_v_thr = (WALLBOX_V_OFF_PER_CELL if v_engaged else WALLBOX_V_ON_PER_CELL) * conf.get('cell_count', 16)
			print('wallbox: voltage_ok %-5s   min %s V over %d min (>=%.1f V, %s)' % (
				voltage_ok, ('%.1f' % min_v) if min_v is not None else '-',
				WALLBOX_V_WINDOW_MIN, _v_thr, 'engaged' if v_engaged else 'idle'))
			print('wallbox: should_on  %-5s   marker(before) %s   action: %s' % (should_on, marker, action))

	# ── battery-side cap while the wallbox runs on grid terms ─────────────────
	# 'free' hands the battery over unlimited — the one mode in which a running
	# wallbox can drain it. Unless the battery genuinely has surplus of its own
	# (energy path) or is full (voltage path), the car must not be what spends
	# it: cap discharge to the house's share for this slot, which write_timer
	# does for 'limit' (rate cap plus the per-slot energy budget), and let the
	# grid carry the car. 'stop' and 'limit' already protect the battery and
	# are left alone.
	# ...but only when the car would actually pull the battery BELOW the
	# reserve. In green, mode 'free' already means content > reserve, so this
	# only ever bites in the one red hour where 'free' is forced regardless of
	# content (the dirtiest hour) with content at or under the reserve — there
	# the car must go to the grid so the forced-free discharge is not spent on
	# it. Where content covers the reserve, the surplus above it may go to the
	# car and the battery still ends the slot above its reserve. Downgrading
	# regardless was wrong: with the reserve at 0 on a sunny day (the coming
	# yield refills the battery anyway) it stopped the battery from discharging
	# at all, even though nothing needed protecting.
	if battery_steerable and wallbox_on and mode == 'free' and content <= reserve and not (energy_ok or voltage_ok):
		mode = 'limit'
		detail += ' [free->limit: wallbox on grid terms]'

	if verbose:
		if battery_steerable:
			# name which of the two estimates the reserve came from, and show the
			# surplus already subtracted from the second — otherwise a reserve of 0
			# on a sunny morning is not answerable from the output. _up_raw is the
			# unclamped demand on purpose: reconstructing it from _up_demand would
			# print the surplus back at the reader whenever the clamp bit.
			_src = 'window' if _rw_demand >= _up_demand else 'upcoming'
			_cap = conf.get('battery_capacity_wh', 0)
			_soc = ('%d%%' % round(100.0 * content / _cap)) if _cap > 0 else '?'
			print('content %d Wh (%s)   reserve(%d%%) %.0f Wh [%s: %.0f/%.0f-%.0fpv]%s   =>  mode: %s' % (
				content, _soc, conf['reserve_pct'], reserve, _src, _rw_demand,
				_up_raw, _pv_before_red, detail, mode.upper()))
		else:
			print('content unavailable%s   =>  mode: %s' % (detail, mode.upper()))
		if ac_pct != 100:
			print('precharge: ac capped to %d%% this hour (see precharge_ac_pct)' % ac_pct)

	if not conf['disable_zeroinput_timer']:
		write_timer(mode, now, basic_load[now.hour], ac_pct, pv_for_reserve[now.hour])

	if verbose:
		print('dirt_shift done.', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
		if html: print('\n</pre></body></html>')
	return 0


def write_timer(mode, now, basic_load_now, ac_pct=100, expected_pv_now=0.0):
	"""Write timer.txt in the zeroinput format:
	  date time | discharge_W  ac_W  energy_Wh   (<=100 = percent, >100 = watt)
	Each line carries the real calendar date it was written for. pvpt (ac
	100%) is guaranteed EXCEPT for the one deliberate exception: the optional
	precharge path (precharge_enabled, see main()) may pass ac_pct < 100 for
	the single cleanest green hour in the window, to divert PV surplus that
	would otherwise go to pvpt into the battery instead. Outside that path,
	ac_pct is always 100. Battery discharge is steered by 'mode':
	  free  -> no discharge limit                            '100 <ac> -1'
	  limit -> two independent caps apply together (below)   '<rate> <ac> <budget>'
	  stop  -> no discharge, pvpt at ac_pct (100 outside precharge) '000 <ac> 000'
	'limit' combines two caps that each close a gap the other leaves open:
	  - a discharge-RATE cap, limit_discharge_rate (Watt, dirt_shift.conf) —
	    a fixed, installation-specific ceiling on instantaneous power. Set it
	    very high to make this cap effectively unrestricted, leaving only
	    the energy budget below in effect.
	  - an ENERGY budget for the current slot only, round(SLOT_HOURS *
	    max(0, min(1, reserve_pct * 0.01) * basic_load_now - pv_now)) Wh —
	    the hour's own ordinary quarter-hour share, scaled by reserve_pct
	    (dirt_shift.conf) like the reserve itself, and net of the PV that is
	    covering the house directly anyway (pvpt passes it through, so it
	    need not also come from the battery — the same reserve_pct scaling
	    and PV netting red_window_demand applies to the reserve, see
	    main()). Not a config value of its own.

	    pv_now is MEASURED (see recent_pv_power), not expected_pv_now: this
	    budget governs the quarter hour happening right now, and an hourly
	    forecast mean is too coarse for it on the ramp hours at each end of
	    the day — which are also, routinely, the marginal red hours. Around
	    sunset the mean can still sit near basic_load while real output has
	    already collapsed, yielding a budget of a few Wh exactly when the
	    battery should be carrying the house. expected_pv_now stays as the
	    fallback for when the measurement is unavailable.

	    The reserve_pct factor is capped at 1.0 HERE ONLY, unlike in the
	    reserve itself, where a value above 100 is meaningful and makes the
	    system more cautious (it holds back more than the computed demand).
	    For a cap the same value would invert that intent: above 100 the
	    budget would exceed the hour's actual net demand, so the cap would
	    stop restricting anything at all in exactly the situation it exists
	    for. Capping keeps 'more reserve_pct' monotonically more
	    conservative on both sides.

	    A rate cap alone would still let a load that is small but sustained
	    drain the battery over the full hour; an energy budget alone would
	    still let a brief high-power spike through before it is exhausted.
	    Together neither gets through.
	A single slot's budget is enough: dirt_shift rewrites timer.txt every 1/4h
	with a fresh budget for the new slot, and zeroinput's energy counter
	resets whenever the timer file's last line changes (see
	discharge_times.update() in zeroinput.py) — which happens on every run
	while 'limit' is in effect, since the failsafe line 30 min out (below)
	moves forward with 'now' each time. The energy_Wh field is -1 (a
	sentinel discharge_times.update() treats as 'no energy cap at all',
	skipping the budget check entirely) only in 'free' mode, where none is
	needed.
	dirt_shift is optional and must never block normal operation, so the plan is
	a short chain re-written every run:
	  - the current 1/4h slot in the chosen mode and ac_pct;
	  - if the mode limits/stops discharge, OR ac_pct is capped, an 'all
	    allowed' line (discharge and pvpt both unrestricted) 30 min later as
	    a failsafe — renewed every run while the script lives, self-lifting
	    after 30 min if it dies.
	Should dirt_shift stop running altogether, both lines eventually fall into
	the past; zeroinput's timer parser applies every already-past line in file
	order and only stops at the first future one, so once none is left in the
	future it simply keeps the values of the last line it saw — which is
	always the 'all allowed' failsafe line (or the single free-mode line) — so
	the file settles on the safe, unrestricted state on its own rather than
	re-arming the same limit every day."""
	ac_pct = max(0, min(100, round(ac_pct)))
	rate   = round(conf['limit_discharge_rate'])
	pv_now = recent_pv_power()					# measured; None on failure -> fall back to the hourly forecast
	if pv_now is None: pv_now = expected_pv_now
	budget = round(SLOT_HOURS * max(0.0, basic_load_now * min(1.0, conf['reserve_pct'] * 0.01) - pv_now))
	FREE  = '100 100 -1'									# full discharge, full pvpt, no energy cap — the failsafe line, always fully unrestricted
	# single-space separation, no fixed-width fields: '%3d' pads with spaces
	# (e.g. ac_pct 0 -> '  0'), and zeroinput's parser splits on single
	# whitespace, so padded fields produce empty tokens it rejects.
	payload = {'free': '100 %d -1' % ac_pct,
	           'limit': '%d %d %d' % (rate, ac_pct, budget),
	           'stop': '000 %d 000' % ac_pct}[mode]

	lines = []
	t = now.replace(second=0, microsecond=0, minute=(now.minute // SLOT_MINUTES) * SLOT_MINUTES)
	lines.append((t, payload))
	if mode != 'free' or ac_pct != 100:					# failsafe: lift any limit/stop/ac-cap after 30 min
		t2 = t + timedelta(minutes=30)
		lines.append((t2, FREE))

	with open(conf['timer.txt'], 'w') as fo:
		fo.write('# %s  (dirt_shift)\n' % datetime.now().strftime('%Y-%m-%dT%H:%M:%S'))
		fo.write('# real calendar date per line, space or tab separated\n')
		fo.write('#                   battery discharge W if > 100, percentage if <= 100\n')
		fo.write('# date     time     |   ac inverter power W if > 100, percentage if <= 100\n')
		fo.write('# |        |        |   |   energy limit in Wh, -1 = unlimited\n')
		for lt, p in lines:
			line = '%s %02d:%02d:00 %s' % (lt.strftime('%Y-%m-%d'), lt.hour, lt.minute, p)
			fo.write(line + '\n')
			if debug: print('timer:', line)


exit(main())
