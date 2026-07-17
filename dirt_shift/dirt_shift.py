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
from os.path import join, dirname
from datetime import datetime, timedelta
from time import time, sleep
from requests import get, post
from sys import argv as sys_argv

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

verbose = '-v' in sys_argv
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
		jresp = get(url=url).json()
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
	"""Today's hourly global horizontal irradiance forecast (shortwave_radiation,
	W/m^2) from Open-Meteo (no API key required for non-commercial use), for
	the configured location (latitude/longitude). shortwave_radiation is the
	direct plus diffuse component together — what a PV module actually
	receives, including on an overcast day. Today's and tomorrow's local 24
	hours are requested in one call. Returns (today, tomorrow), each a
	24-value list (an hour Open-Meteo did not report stays None), or
	(None, None) on any request/parse error — the caller then falls back to
	the unscaled PV reference curve."""
	try:
		url = ('https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s'
		       '&hourly=shortwave_radiation&forecast_days=2&timezone=auto'
		       % (conf.get('latitude', 51.0), conf.get('longitude', 10.0)))
		jresp = get(url=url, timeout=10).json()
		times = jresp['hourly']['time']			# 'YYYY-MM-DDTHH:MM', local time (timezone=auto)
		rad   = jresp['hourly']['shortwave_radiation']
		today_str = datetime.now().strftime('%Y-%m-%d')
		today, tomorrow = [None] * 24, [None] * 24
		for t, r in zip(times, rad):
			h = int(t[11:13])
			if not (0 <= h < 24):
				continue
			(today if t[:10] == today_str else tomorrow)[h] = r
		return today, tomorrow
	except Exception as e:
		if verbose: print('radiation forecast fetch failed:', e)
		return None, None


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

	last_ts = vz_in.get('timestamp')
	needs_refresh = (avgnew or last_ts is None or
	                  datetime.fromtimestamp(last_ts).strftime('%Y-%m-%d %H') != datetime.now().strftime('%Y-%m-%d %H'))

	if needs_refresh:
		today, tomorrow = get_radiation_forecast()
		if today is not None:
			vz_in['today'] = today
			vz_in['tomorrow'] = tomorrow
			vz_in['timestamp'] = datetime.now().timestamp()
			with open(join(dirname(__file__), 'dirt_weather_cache.json'), 'w') as fo:
				json_dump(vz_in, fo)
		elif verbose:
			print('radiation forecast refresh failed — keeping previous forecast')
	elif verbose:
		print('using cached radiation forecast from',
		      datetime.fromtimestamp(last_ts).strftime('%Y-%m-%d %H:%M') if last_ts else 'never')

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



def _smard_series(filter_id, today):
	"""Fetch one SMARD day-ahead series (hourly resolution) and return
	{local_hour: value} for points that fall on 'today' (a date) in local time.
	SMARD's API is two-step: an index of available batch-start timestamps, then
	the series for the most recent batch at/before now. Raises on any
	request/structure problem — the caller treats that as 'no data'."""
	base = 'https://www.smard.de/app/chart_data'
	idx = get(url='%s/%i/%s/index_hour.json' % (base, filter_id, SMARD_REGION), timeout=15)
	idx.raise_for_status()
	timestamps = idx.json()['timestamps']
	now_ms = int(datetime.now().timestamp() * 1000)
	batch = max(t for t in timestamps if t <= now_ms)

	resp = get(url='%s/%i/%s/%i_%s_hour_%i.json' % (base, filter_id, SMARD_REGION, filter_id, SMARD_REGION, batch),
	           timeout=15)
	resp.raise_for_status()
	series = resp.json()['series']

	result = {}
	for ts_ms, value in series:
		if value is None: continue
		t_local = datetime.fromtimestamp(ts_ms / 1000)
		if t_local.date() == today:
			result[t_local.hour] = float(value)
	return result


def _smard_zones_for_date(date):
	"""Classify one calendar date's SMARD-derived CO2-intensity zones
	('red'/'green') and the raw wind+solar/load ratio per hour, from real
	SMARD day-ahead data (Bundesnetzagentur; no API key needed). The day's
	median ratio splits its 24 hours in half: the cleaner half (ratio at or
	above the median) is green, the dirtier half is red — a self-adjusting
	cut that reflects the day's own spread instead of a fixed fraction, so
	even a day that is uniformly dirty still separates its relatively
	cleaner hours from its worst ones. Returns {'zones': 24-value list,
	'ratio': 24-value list (None where SMARD did not cover that hour)}, or
	None if the query/parse fails or too few hours are covered for that date
	(SMARD's day-ahead data for tomorrow, in particular, may simply not be
	published yet)."""
	try:
		renewable = _smard_series(SMARD_FILTER_WIND_SOLAR, date)
		load      = _smard_series(SMARD_FILTER_LOAD, date)

		hours = [h for h in range(24) if h in load and h in renewable]
		if len(hours) < 20:								# too few hours for a meaningful split
			raise ValueError('incomplete day (%i/24 hours)' % len(hours))

		ratio = {h: (renewable[h] / load[h] if load[h] > 0 else 0.0) for h in hours}
		sorted_ratios = sorted(ratio[h] for h in hours)
		n = len(sorted_ratios)
		median = (sorted_ratios[n // 2] if n % 2 else
		          (sorted_ratios[n // 2 - 1] + sorted_ratios[n // 2]) / 2.0)

		zones = [None] * 24
		for h in hours:
			zones[h] = 'green' if ratio[h] >= median else 'red'
		for h in range(24):									# hours SMARD didn't cover: treat as red (safe default)
			if zones[h] is None: zones[h] = 'red'
		ratio_list = [ratio.get(h) for h in range(24)]		# None where not covered
		if debug: print('SMARD ratio by hour (%s), median %.2f:' % (date, median), {h: round(ratio[h], 2) for h in hours})
		return {'zones': zones, 'ratio': ratio_list}
	except Exception as e:
		if verbose: print('SMARD zone fetch failed for %s:' % date, e)
		return None


def get_smard_zones():
	"""Today's and tomorrow's SMARD-derived zones (see _smard_zones_for_date).
	Returns (today, tomorrow), each either a {'zones':..., 'ratio':...} dict
	or None. Tomorrow's day-ahead data commonly is not published yet earlier
	in the day — that is expected and not treated as an error, tomorrow is
	simply None then. Returns (None, None) if today's own query fails."""
	today_local = datetime.now().date()
	today = _smard_zones_for_date(today_local)
	if today is None:
		return None, None
	tomorrow = _smard_zones_for_date(today_local + timedelta(days=1))
	return today, tomorrow


def read_smard_zones():
	"""Rolling 24-hour SMARD zones/ratio starting at the current hour: hours
	from now until midnight come from today's classification, hours after
	midnight come from tomorrow's — same rolling principle as
	read_radiation_forecast, so a caller summing forward from 'now' (see
	_bridge_hours/red_window_demand) always reads the classification for the
	calendar day each hour actually falls on. Built fresh on every call from
	the cached raw today/tomorrow dicts (see get_smard_zones), which are
	refetched together once per hour in their own cache file. An hour missing
	from tomorrow (day-ahead data not yet published, or entirely absent) uses
	today's classification for that same hour — that hour is then really
	still today's classification standing in for tomorrow, not tomorrow's own;
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

	last_ts = vz_in.get('timestamp')
	needs_refresh = (avgnew or last_ts is None or
	                  datetime.fromtimestamp(last_ts).strftime('%Y-%m-%d %H') != datetime.now().strftime('%Y-%m-%d %H'))

	backdate_ratio = None
	if needs_refresh:
		old_today = vz_in.get('today')
		if old_today is not None:
			prev_hour = (datetime.now().hour - 1) % 24
			backdate_ratio = old_today['ratio'][prev_hour]			# value that was valid up to this hour's boundary

		today, tomorrow = get_smard_zones()
		if today is not None:
			vz_in['today'] = today
			vz_in['tomorrow'] = tomorrow
			vz_in['fetch_date'] = datetime.now().strftime('%Y-%m-%d')
			vz_in['timestamp'] = datetime.now().timestamp()
			with open(join(dirname(__file__), 'dirt_smard_cache.json'), 'w') as fo:
				json_dump(vz_in, fo)
		elif verbose:
			print('SMARD zone refresh failed — trying previous data')
	elif verbose:
		print('using cached SMARD zones from',
		      datetime.fromtimestamp(last_ts).strftime('%Y-%m-%d %H:%M') if last_ts else 'never')

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
	if tomorrow is None:
		return {'zones': today['zones'], 'ratio': today['ratio'], 'stale': set(range(now_hour)),
		        'backdate_ratio': backdate_ratio}
	zones = [today['zones'][h] if h >= now_hour else tomorrow['zones'][h] for h in range(24)]
	ratio = [today['ratio'][h] if h >= now_hour else tomorrow['ratio'][h] for h in range(24)]
	return {'zones': zones, 'ratio': ratio, 'stale': set(), 'backdate_ratio': backdate_ratio}


def get_vz_bat_cap():
	"""Reconstruct real battery energy content (Wh) by integrating PV and
	Inverter since the last known 'empty' state (voltage <= 3.0625 V/cell, i.e.
	49 V at 16 cells, scaled by cell_count). Returns (latest_voltage,
	content_Wh)."""
	if verbose: print(datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'query volkszähler for energy content:')
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


def read_wallbox_marker():
	"""Whether dirt_shift itself is the reason the wallbox relay is currently
	on — a small persistent marker (see main()), separate from the relay's
	real state, since dirt_shift may only ever switch off a relay it
	switched on itself: a manual activation must never be undone by
	dirt_shift, no matter how dirty the grid gets. Returns False (foreign,
	untouched) if no marker file exists yet — including on the very first
	run ever, so an already-on relay found with no prior history is treated
	as foreign rather than assumed to be dirt_shift's own."""
	try:
		with open(join(dirname(__file__), 'dirt_wallbox_marker.json'), 'r') as fi:
			return bool(json_load(fi).get('dirt_shift_on', False))
	except Exception:
		return False


def write_wallbox_marker(on):
	"""Persist whether dirt_shift itself switched the wallbox relay on (see
	read_wallbox_marker). Best-effort: a failed write only means the next
	run re-derives the wrong assumption once, not a hard failure — never
	raises further."""
	try:
		with open(join(dirname(__file__), 'dirt_wallbox_marker.json'), 'w') as fo:
			json_dump({'dirt_shift_on': on}, fo)
	except Exception as e:
		if verbose: print('failed to write wallbox marker:', e)


def _dirt_median(all_dirt):
	"""Median of a 24-value dirt% list, ignoring hours with no SMARD coverage
	(None). None if no hour has a value at all. Shared by
	wallbox_should_be_on and main()'s -v reporting, so the displayed median
	is always the exact one the decision itself was made against."""
	valid = [d for d in all_dirt if d is not None]
	if not valid:
		return None
	n = len(valid)
	s = sorted(valid)
	return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def wallbox_should_be_on(dirt_now, all_dirt, mode):
	"""The dirt%-based half of the switch-on decision (see wallbox_decide,
	which combines this with the energy-based half and is what main()
	actually calls): the current hour's dirtiness must be within
	wallbox_median_fraction percent of the day's median dirt% AND at or
	below the absolute wallbox_absolute_max, AND the discharge mode must be
	'free' — which covers both a comfortably covered red reserve and,
	deliberately, the single dirtiest red hour itself (where 'free' is
	forced regardless of content, see main()): on a night dirty enough that
	no hour clears the two dirt% limits, the wallbox may still end up
	charging in that one reddest hour, since 'free' holds there too. This is
	an accepted consequence of reusing 'free' rather than a flaw —
	reserve_pct and the discharge limit (limit_discharge_rate plus the
	quarter-hour energy budget) already handle the reserve itself; the
	wallbox is not held to a stricter standard than the battery is.
	Precharge (see precharge_ac_pct) never enters this decision — the two
	are fully independent. False if dirt_now or every entry in all_dirt is
	unavailable (no SMARD coverage for that hour)."""
	median = _dirt_median(all_dirt)
	if dirt_now is None or median is None:
		return False
	return (dirt_now <= conf['wallbox_median_fraction'] * 0.01 * median
	        and dirt_now <= conf['wallbox_absolute_max']
	        and mode == 'free')


def wallbox_decide(dirt_now, all_dirt, mode, content, now, zones, basic_load, expected_pv):
	"""Whether the wallbox should be on this run — one formula, checked the
	same way regardless of whether dirt_shift currently owns the relay (see
	main(), which still uses the owner marker separately to decide whether
	an actual switch command is needed). Both paths run continuously side by
	side, not just at switch-on:

	  should_on = wallbox_should_be_on(dirt_now, all_dirt, mode) OR energy_ok

	dirt%-based path (wallbox_should_be_on): both dirt% limits AND
	mode == 'free'.

	Energy-based path: (content - wallbox_typical_power * 0.25) > a
	wallbox-specific reserve — reserve_pct * upcoming_red_demand(now, zones,
	basic_load, expected_pv), in place of the main reserve variable
	red_window_demand computes elsewhere in main(): that one deliberately
	reads 0 for as long as 'now' itself sits within an assumed PV-surplus
	stretch, which would make the wallbox's own energy check a rubber stamp
	exactly when it matters most — a running wallbox can draw far more than
	the day's actual remaining surplus (see upcoming_red_demand), quietly
	eating into stored content the main reserve figure is trusting will
	refill on its own. upcoming_red_demand's rough, always-available
	estimate closes that gap by never reading 0 just because 'now' happens
	to be a surplus hour. Compared directly against current content, with no
	projected future surplus added on top — deliberately conservative, since
	any assumed surplus between now and red is exactly what a running
	wallbox could itself consume before it ever reaches the battery. This
	replacement is scoped to the wallbox only; the main reserve variable and
	the rest of main()'s mode decision are untouched.

	Because both paths run continuously, a relay dirt_shift itself switched
	on via one path stays on as long as EITHER path still holds — it only
	switches off once both fail at the same time. This deliberately differs
	from a relay switched on manually: that one is left alone entirely,
	however dirty or energy-short it gets, for as long as dirt_shift never
	owns it (see the marker logic in main()) — a completely separate
	protection, not implemented via this function at all.

	Note this does NOT reliably subsume the old 'limit'/'stop' mode -> off
	rule via the energy path alone: when several red blocks lie before the
	next true surplus hour, upcoming_red_demand (only the first block) can
	be smaller than the main reserve (all of them summed), so the
	wallbox-specific energy check can in that case be looser than the main
	mode's own protection — an accepted trade-off of a deliberately rough,
	single-block estimate, not a guarantee. The dirt%-based path still
	requires mode == 'free', though, which 'limit'/'stop' both violate by
	definition — so a relay kept on purely via the dirt%-based path is
	unaffected by this gap; it only applies to the energy-based path.

	Precharge (see precharge_ac_pct) never enters this decision either way —
	the two remain fully independent."""
	wallbox_reserve = conf['reserve_pct'] * 0.01 * upcoming_red_demand(now, zones, basic_load, expected_pv)
	margin = conf['wallbox_typical_power'] * 0.25
	energy_ok = (content - margin) > wallbox_reserve
	return wallbox_should_be_on(dirt_now, all_dirt, mode) or energy_ok


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


def marginal_red_hour(now, zones, basic_load, expected_pv, ratio, content):
	"""The hour-of-day (integer) of the dirtiest red hour in the window (now
	up to the next PV-surplus hour, see _bridge_hours) that 'content' does
	NOT yet fully cover — used only by the optional precharge path (see
	main()) to find which red hour's dirt% the precharge spread (trigger 1)
	should be measured against. Candidates are scanned dirtiest-first (the
	same ranking dirtiest_hour itself would report first); 'content' is
	consumed against each hour's shortfall (basic_load[h] - expected_pv[h],
	floored at 0) in that order, exactly mirroring how the reserve itself is
	built from the same shortfalls. The hour where 'content' runs out is the
	answer — every dirtier hour before it is already covered by the current
	charge, so it is the dirtier of those that is not yet safe. This shifts
	toward cleaner red hours as content grows through successive precharge
	runs, and the trigger-1 spread shrinks accordingly, so precharging tapers
	off on its own once only comparatively clean red hours remain uncovered
	(no longer worth the round-trip loss). None if content already covers
	every red hour in the window (nothing left to precharge for) or the
	window has no red hour at all."""
	candidates = sorted(
		(h for h, _ in _bridge_hours(now, zones, basic_load, expected_pv) if zones[h] == 'red'),
		key=lambda h: ratio[h] if ratio[h] is not None else -1.0)		# dirtiest (lowest ratio) first
	rest = content
	for h in candidates:
		shortfall = max(0.0, basic_load[h] - expected_pv[h])
		if rest < shortfall:
			return h
		rest -= shortfall
	return None


def cleanest_green_hour(now, zones, basic_load, expected_pv, ratio):
	"""The hour-of-day (integer) of the single cleanest green hour between now
	and the next PV-surplus hour (see _bridge_hours) — the mirror image of
	dirtiest_hour, used only by the optional precharge path (see main()) to
	pick which green hour's ac_% may be capped below 100 to divert PV surplus
	into the battery instead of pvpt. 'Cleanest' is the highest ratio (lowest
	dirt%); ties go to the chronologically earliest hour in the window (scan
	order, not the raw hour-of-day number). Red hours in the window are never
	candidates. None if the window has no green hour at all."""
	candidates = [h for h, _ in _bridge_hours(now, zones, basic_load, expected_pv) if zones[h] == 'green']
	best_h, best_ratio = None, None
	for h in candidates:									# already in chronological scan order
		r = ratio[h] if ratio[h] is not None else -1.0	# an uncovered hour ranks as the dirtiest, i.e. last choice
		if best_ratio is None or r > best_ratio:
			best_h, best_ratio = h, r
	return best_h


def precharge_ac_pct(now, zones, basic_load, expected_pv, ratio, content, reserve):
	"""The ac_% to write for this run's timer.txt line — 100 (no restriction)
	unless the optional precharge path (precharge_enabled) is active and every
	one of its conditions holds; the one deliberate exception to dirt_shift's
	pvpt guarantee (see write_timer). Only ever considered while the current
	hour is green (main() only calls this then). Three conditions, all
	required:

	  1. Worth the round-trip loss: marginal_red_hour finds the dirtiest red
	     hour in the window that 'content' does not yet cover, and
	     cleanest_green_hour finds the cleanest green hour in the window; the
	     dirt% spread between them must exceed the round-trip loss
	     (100 - PV_to_bat_efficiency * bat_to_AC_efficiency / 100). As content
	     grows through successive precharge runs, marginal_red_hour shifts to
	     progressively cleaner red hours, shrinking the spread — precharging
	     tapers off on its own once only comparatively clean red hours remain
	     uncovered. No red hour left uncovered at all (marginal_red_hour is
	     None) means nothing to precharge for.

	  2. Natural surplus alone will not be enough: content plus the natural
	     surplus (max(0, expected_pv - basic_load)) of every OTHER green hour
	     in the window must still fall short of 'reserve'. If it would already
	     be enough without diverting anything extra, no precharge is needed.

	  3. 'now' matches the current candidate: no more than one hour is ever
	     capped within a single run — only if 'now' equals cleanest_green_hour
	     does the cap apply; every other green hour stays at ac_% 100 for this
	     run. This is not a single hour fixed for the whole day: since
	     cleanest_green_hour is re-derived from the current window on every
	     run (see marginal_red_hour's own tapering-off note above), an unused
	     or insufficient candidate hour is simply replaced by the next-best
	     remaining one on the following run — across several runs, precharge
	     can therefore throttle several different hours in sequence, one per
	     run, until condition 1 or 2 no longer holds.

	Where all three hold, the cap is continuous, not stepped: potential =
	min(expected_pv[now], basic_load[now]) is the most this hour could
	additionally divert into the battery (the same amount whether the hour is
	itself a net charging or discharging hour on its own); gap = reserve -
	content - (the same 'other green hours' surplus sum as condition 2) is
	what is still needed. ac_% = round(100 * (1 - clamp(gap / potential, 0,
	1))): 0 if potential does not cover the gap at all, closer to 100 the
	less is missing. No memory across runs — every call re-derives everything
	from the current 'content', exactly like the rest of dirt_shift."""
	if not conf.get('precharge_enabled', False):
		return 100
	mh = marginal_red_hour(now, zones, basic_load, expected_pv, ratio, content)
	if mh is None:
		return 100												# content already covers every red hour in the window
	ch = cleanest_green_hour(now, zones, basic_load, expected_pv, ratio)
	if ch is None or now.hour != ch:
		return 100												# not this run's candidate hour

	loss_threshold = 100.0 - conf['PV_to_bat_efficiency'] * conf['bat_to_AC_efficiency'] / 100.0
	dirt_mh = (1.0 - ratio[mh]) * 100.0 if ratio[mh] is not None else 100.0
	dirt_ch = (1.0 - ratio[ch]) * 100.0 if ratio[ch] is not None else 0.0
	if dirt_mh - dirt_ch <= loss_threshold:
		return 100												# not worth the round-trip loss

	other_surplus = sum(max(0.0, expected_pv[h] - basic_load[h])
	                     for h, _ in _bridge_hours(now, zones, basic_load, expected_pv)
	                     if zones[h] == 'green' and h != now.hour)
	gap = reserve - content - other_surplus
	if gap <= 0:
		return 100												# the other green hours' natural surplus would be enough

	potential = min(expected_pv[now.hour], basic_load[now.hour])
	if potential <= 0:
		return 0												# nothing left to divert this hour: cap fully
	fraction = max(0.0, min(1.0, gap / potential))
	return round(100.0 * (1.0 - fraction))


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
	print('%-3s %8s %8s %5s %8s %8s %8s %4s %6s %-8s' % ('hr', 'PV_curve', 'rad_Wm2', 'clr%', 'exp_PV', 'basic_ld', 'balance', 'chg', 'dirt%', 'zone'))
	for h in range(24):
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
			bal = '-' if exp == 0 else '%+d' % (expected_pv[h] - basic_load[h])	# exp_PV shows 0: balance would be -basic_load, redundant
			chg = 'L' if expected_pv[h] > basic_load[h] else 'D'
			if h == dh: chg = '!' + chg							# the dirtiest red hour in the window
			if h == cleanest_l: chg = '!' + chg					# the cleanest charging hour of the day
		else:
			bal, chg = '-', '-'
		ratio_h = ratio[h]
		dirt_s = ('%.0f' % ((1.0 - ratio_h) * 100)) if ratio_h is not None else '-'
		if h == now.hour and dirt_written is not None:
			dirt_s = ('*' if dirt_written else '!') + dirt_s	# volkszähler write of this hour's value
		if h in smard_stale: dirt_s = '.' + dirt_s				# still today's classification, no tomorrow value yet
		marker = '*' if h == now.hour else ' '
		print('%2d%s %8s %8s %5s %8s %8s %8s %4s %6s %-8s' % (h, marker, pv, rad_s, clr, exp, bl, bal, chg, dirt_s, zones[h]))
	if expected_pv is not None or basic_load is not None or any(r is not None for r in ratio):
		dash8 = '-' * 8
		print('%-3s %8s %8s %5s %8s %8s %8s' % ('', '', '', '', dash8, dash8, dash8))
		exp_sum = sum(expected_pv) if expected_pv is not None else None
		bl_sum  = sum(basic_load) if basic_load is not None else None
		bal_sum = (exp_sum - bl_sum) if (exp_sum is not None and bl_sum is not None) else None
		valid_ratio = [r for r in ratio if r is not None]
		dirt_avg = sum((1.0 - r) * 100.0 for r in valid_ratio) / len(valid_ratio) if valid_ratio else None
		print('%-3s %8s %8s %5s %8s %8s %8s %4s %6s' % (
			'', '', '', '',
			('%.0f' % exp_sum) if exp_sum is not None else '-',
			('%.0f' % bl_sum) if bl_sum is not None else '-',
			('%+.0f' % bal_sum) if bal_sum is not None else '-',
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
		die('SMARD zone data unavailable (fetch failed, no cache newer than one day)', conf['timer.txt'])
	_voltage, content = get_vz_bat_cap()

	r = grid_data['ratio'][now.hour]
	slot = now.replace(second=0, microsecond=0, minute=(now.minute // 15) * 15)
	slot_ms = int(slot.timestamp() * 1000)
	backdate_ratio = grid_data.get('backdate_ratio')
	if backdate_ratio is not None:
		write_dirtiness_to_vz((1.0 - backdate_ratio) * 100, slot_ms - 1000)	# flat step: previous hour's value, 1s before the boundary
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
	zones = grid_data['zones']
	pv_for_reserve = expected_pv if expected_pv is not None else [0.0] * 24
	reserve = conf['reserve_pct'] * 0.01 * red_window_demand(basic_load, now, zones, pv_for_reserve)
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
		mode = 'free' if content > reserve else 'stop'
		detail = ''
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

	if verbose:
		print('content %d Wh   reserve(%d%%) %.0f Wh%s   =>  mode: %s' % (
			content, conf['reserve_pct'], reserve, detail, mode.upper()))
		if ac_pct != 100:
			print('precharge: ac capped to %d%% this hour (see precharge_ac_pct)' % ac_pct)

	if not conf['disable_zeroinput_timer']:
		write_timer(mode, now, basic_load[now.hour], ac_pct, pv_for_reserve[now.hour])

	if conf.get('wallbox_enabled', False):
		dirt_now = (1.0 - r) * 100 if r is not None else None
		all_dirt = [(1.0 - x) * 100 if x is not None else None for x in grid_data['ratio']]
		marker = read_wallbox_marker()
		should_on = wallbox_decide(dirt_now, all_dirt, mode, content, now, zones, basic_load, pv_for_reserve)
		action = 'none'
		if should_on and not marker:
			action = 'switch on (verified)' if wallbox_switch(conf['wallbox_ip'], conf['wallbox_output'], True) else 'switch on FAILED'
			if action.endswith('(verified)'): write_wallbox_marker(True)
		elif not should_on and marker:
			action = 'switch off (verified)' if wallbox_switch(conf['wallbox_ip'], conf['wallbox_output'], False) else 'switch off FAILED'
			if action.endswith('(verified)'): write_wallbox_marker(False)
		if verbose:
			median = _dirt_median(all_dirt)
			pct_median = (dirt_now / median * 100.0) if (dirt_now is not None and median) else None
			margin = conf['wallbox_typical_power'] * 0.25
			wallbox_reserve = conf['reserve_pct'] * 0.01 * upcoming_red_demand(now, zones, basic_load, pv_for_reserve)
			print('wallbox: dirt%% %s (<%g)   %%median %s (<%g%%)' % (
				('%.0f' % dirt_now) if dirt_now is not None else '-', conf['wallbox_absolute_max'],
				('%.0f' % pct_median) if pct_median is not None else '-', conf['wallbox_median_fraction']))
			print('wallbox: content %.0f - reserve %.0f - margin %.0f = %.0f Wh' % (
				content, wallbox_reserve, margin, content - wallbox_reserve - margin))
			print('wallbox: should_on %s   marker(before) %s   action: %s' % (should_on, marker, action))

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
	  - an ENERGY budget for the current 1/4h slot only, round(0.25 *
	    max(0, reserve_pct * basic_load_now * 0.01 - expected_pv_now)) Wh —
	    the hour's own ordinary quarter-hour share, scaled by reserve_pct
	    (dirt_shift.conf) exactly like the reserve itself, and net of the
	    PV still expected in it (pvpt covers that part directly, so it need
	    not also come from the battery — the same reserve_pct scaling and
	    PV netting red_window_demand already applies to the reserve itself,
	    see main()). Not a config value of its own. A rate cap alone
	    would still let a load that is small but sustained drain the
	    battery over the full hour; an energy budget alone would still let
	    a brief high-power spike through before it is exhausted. Together
	    neither gets through.
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
	budget = round(0.25 * max(0.0, basic_load_now * conf['reserve_pct'] * 0.01 - expected_pv_now))
	FREE  = '100 100 -1'									# full discharge, full pvpt, no energy cap — the failsafe line, always fully unrestricted
	payload = {'free': '100 %3d -1' % ac_pct,
	           'limit': '%d %3d %d' % (rate, ac_pct, budget),
	           'stop': '000 %3d 000' % ac_pct}[mode]

	lines = []
	t = now.replace(second=0, microsecond=0, minute=(now.minute // 15) * 15)
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
