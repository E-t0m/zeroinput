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
#      give an hourly renewables/load ratio; each day's hours are split by
#      percentile into three zones (see _smard_zones_for_date):
#        high   (red)    — the lowest-ratio (dirtiest) hours
#        medium (yellow) — the middle band
#        low    (green)  — the highest-ratio (cleanest) hours
#      If the SMARD fetch fails, cached data substitutes for one more day;
#      beyond that dirt_shift aborts, leaving an all-allowed timer.
#   2. red reserve = reserve_pct (~90%) * basic_load over every red hour
#      between now and the next PV surplus phase (the point the battery
#      genuinely refills from).
#   3. discharge is withheld during exactly the cleanest hours it takes to
#      cover that reserve: every non-red hour up to the next PV-surplus hour
#      is ranked by cleanliness, and their shortfalls accumulated
#      cleanest-first until the reserve target is reached — those hours are
#      protected (limit/stop), all others discharge freely (see
#      dirty_protect_hours). A red hour always discharges with no limit; that
#      is where the reserve is meant to be spent. Without any PV forecast
#      (curve never computed, e.g. fresh install), build_reserve_after
#      (~13:30) is used as a clock cutoff instead.
#   4. pvpt (direct PV pass-through) is always granted, independent of all this.
#   5. runs every 1/4h, re-writing timer.txt with fresh battery content and zone.

from json import load as json_load
from json import dump as json_dump
from os.path import join, dirname
from datetime import datetime, timedelta
from time import time
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
SMARD_GREEN_FRACTION = 0.3	# today's hours with the highest wind+solar/load ratio, classified green
SMARD_RED_FRACTION   = 0.3	# today's hours with the lowest wind+solar/load ratio, classified red (the rest is yellow)

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
			fo.write('%s 00:00:00 100 100 99999\n' % datetime.now().strftime('%Y-%m-%d'))
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
# (the yellow-zone discharge cap is dirt_shift's own 'yellow_cap' parameter,
# see dirt_shift.conf — unrelated to any inverter staging threshold in
# zeroinput.conf.)
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
	to today's value for that same hour. Returns None if no forecast has ever
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
		return None
	tomorrow = vz_in.get('tomorrow') or [None] * 24
	now_hour = datetime.now().hour
	return [today[h] if h >= now_hour or tomorrow[h] is None else tomorrow[h] for h in range(24)]


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
	('red'/'yellow'/'green') and the raw wind+solar/load ratio per hour, from
	real SMARD day-ahead data (Bundesnetzagentur; no API key needed). Hours
	are ranked by this ratio for the date and split by percentile —
	SMARD_GREEN_FRACTION highest = green, SMARD_RED_FRACTION lowest = red,
	the rest yellow — so the split adapts to each day's own shape rather than
	a fixed absolute threshold. Returns {'zones': 24-value list, 'ratio':
	24-value list (None where SMARD did not cover that hour)}, or None if the
	query/parse fails or too few hours are covered for that date (SMARD's
	day-ahead data for tomorrow, in particular, may simply not be published
	yet)."""
	try:
		renewable = _smard_series(SMARD_FILTER_WIND_SOLAR, date)
		load      = _smard_series(SMARD_FILTER_LOAD, date)

		hours = [h for h in range(24) if h in load and h in renewable]
		if len(hours) < 20:								# too few hours for a meaningful split
			raise ValueError('incomplete day (%i/24 hours)' % len(hours))

		ratio = {h: (renewable[h] / load[h] if load[h] > 0 else 0.0) for h in hours}
		ranked = sorted(hours, key=lambda h: ratio[h])		# lowest ratio (dirtiest) first
		n_red   = max(1, int(round(len(hours) * SMARD_RED_FRACTION)))
		n_green = max(1, int(round(len(hours) * SMARD_GREEN_FRACTION)))
		red_hours   = set(ranked[:n_red])
		green_hours = set(ranked[-n_green:])

		zones = [None] * 24
		for h in hours:
			zones[h] = 'red' if h in red_hours else 'green' if h in green_hours else 'yellow'
		for h in range(24):									# hours SMARD didn't cover: treat as red (safe default)
			if zones[h] is None: zones[h] = 'red'
		ratio_list = [ratio.get(h) for h in range(24)]		# None where not covered
		if debug: print('SMARD ratio by hour (%s):' % date, {h: round(ratio[h], 2) for h in hours})
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
	today's classification for that same hour. SMARD is a prerequisite: if the
	fetch fails, the cached data may substitute for exactly one more day (the
	cache carries fetch_date; data fetched yesterday still passes — its
	'tomorrow' half was the day-ahead forecast for what is now today).
	Anything older, or no cache at all, returns None — the caller then aborts
	hard, leaving the all-allowed free timer."""
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

	if needs_refresh:
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
	if age_days == 1:
		# fetched yesterday, substituting once: yesterday's 'tomorrow' half was
		# the day-ahead forecast for what is now today.
		if verbose: print('SMARD data from %s substituting for one day' % fetch_date)
		today = vz_in.get('tomorrow') or today
		tomorrow = None
	else:
		tomorrow = vz_in.get('tomorrow')
	if tomorrow is None:
		return today									# no tomorrow data: today's classification stands for the whole rolling window
	now_hour = datetime.now().hour
	zones = [today['zones'][h] if h >= now_hour else tomorrow['zones'][h] for h in range(24)]
	ratio = [today['ratio'][h] if h >= now_hour else tomorrow['ratio'][h] for h in range(24)]
	return {'zones': zones, 'ratio': ratio}


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

def write_dirtiness_to_vz(value):
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
	_hourly_debug_table)."""
	uuid = conf.get('vz_dirtiness_uuid', '')
	if not uuid:
		return None
	try:
		url = 'http://%s/data/%s.json' % (conf['vz_host_port'], uuid)
		resp = post(url=url, params={'value': value, 'ts': int(time() * 1000)}, timeout=10)
		resp.raise_for_status()
		return True
	except Exception as e:
		if verbose: print('dirtiness write failed:', e)
		return False


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
	(evening red, night red, morning red with non-red gaps between them) are
	all summed, since nothing refills the battery in between. On a day so
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


def dirty_protect_hours(now, zones, basic_load, expected_pv, ratio, reserve):
	"""The set of hours (hour-of-day integers) between now and the next
	PV-surplus hour whose discharge must be withheld so their accumulated
	shortfall covers the red reserve target — used only with SMARD active (a
	real per-hour dirtiness ranking, see write_dirtiness_to_vz's
	(1-ratio)*100 convention, is required). Candidate hours are every
	non-red hour in that window (see _bridge_hours; a red hour always
	discharges freely regardless, so it is never a candidate) — by
	construction every candidate is a deficit or exactly-balanced hour,
	since _bridge_hours itself already stops at the first true surplus hour.
	Candidates are ranked cleanest first (ratio descending) and each hour's
	shortfall (basic_load[h] - expected_pv[h], floored at 0) is accumulated
	in that order — every hour up to and including the one that brings the
	running total to at least 'reserve' is protected. The selection is not
	required to be contiguous: a clean hour late in the window can be
	protected while a dirtier hour earlier in the window stays free, if the
	dirty hour's own shortfall was not needed to reach the target. If the
	accumulated total across every candidate never reaches 'reserve', every
	candidate hour is protected — the selection then simply extends as far
	into the day's dirtier hours as the window allows, since nothing else is
	left to draw on. Empty if 'reserve' is 0 (nothing to protect) or the
	window has no non-red hour. An hour with no ratio value (a gap in
	SMARD's coverage) ranks as the dirtiest, so it is protected only as a
	last resort."""
	candidates = [(h, frac) for h, frac in _bridge_hours(now, zones, basic_load, expected_pv)
	              if zones[h] != 'red']
	if reserve <= 0 or not candidates:
		return set()
	ranked = sorted(candidates, key=lambda hf: ratio[hf[0]] if ratio[hf[0]] is not None else -1.0,
	                 reverse=True)								# cleanest (highest ratio) first
	protect, total = set(), 0.0
	for h, frac in ranked:
		protect.add(h)
		total += max(0.0, basic_load[h] - expected_pv[h]) * frac
		if total >= reserve:
			break
	return protect


def _hourly_debug_table(now, pv_curve, radiation, expected_pv, basic_load, grid_data, dirt_written=None):
	"""Print one aligned table (hour 0-23) combining the PV reference curve,
	the shortwave-radiation forecast, the clear-sky index derived from it
	(see scaled_pv_curve/clear_sky_ghi), expected PV, basic_load, whether
	that hour charges or discharges, and the SMARD-derived dirtiness/zone
	(the same rolling array _bridge_hours/red_window_demand/
	dirty_protect_hours use) — so everything the discharge decision draws on
	is visible at a glance, in one place instead of six separate lists.
	'chg' is 'L' (lädt) if expected_pv[h] > basic_load[h], else 'E'
	(entlädt) — the exact boundary _bridge_hours/red_window_demand scan for
	(a PV-surplus hour ends their window). 'dirt%' is (1 - ratio) * 100 (see
	write_dirtiness_to_vz): 0 at ratio 1 (renewables exactly cover load),
	negative on a renewable surplus (ratio > 1), rising toward 100 as the
	renewable share drops toward 0. The current hour is marked with '*'. Its
	dirt% value carries a second marker for the volkszähler write of that
	same value (see write_dirtiness_to_vz, whose return value 'dirt_written'
	is passed here): '*' prefix if it reached volkszähler, '!' if the write
	failed, no prefix if no UUID is configured and nothing was attempted."""
	zones = grid_data['zones']
	print('')
	print('%-3s %8s %8s %5s %8s %8s %3s %6s %-8s' % ('hr', 'PV_curve', 'rad_Wm2', 'clr%', 'exp_PV', 'basic_ld', 'chg', 'dirt%', 'zone'))
	for h in range(24):
		pv  = round(pv_curve[h]) if pv_curve is not None else '-'
		r   = radiation[h] if (radiation is not None and h < len(radiation)) else None
		if r is not None:
			csghi = clear_sky_ghi(now, h + 0.5)
			rad_s = round(r)
			clr   = ('%d' % round(min(100.0, 100.0 * r / csghi))) if csghi > 0 else '-'
		else:
			rad_s, clr = '-', '-'
		exp = round(expected_pv[h]) if expected_pv is not None else '-'
		bl  = round(basic_load[h]) if basic_load is not None else '-'
		if expected_pv is not None and basic_load is not None:
			chg = 'L' if expected_pv[h] > basic_load[h] else 'E'
		else:
			chg = '-'
		ratio_h = grid_data['ratio'][h]
		dirt_s = ('%.0f' % ((1.0 - ratio_h) * 100)) if ratio_h is not None else '-'
		if h == now.hour and dirt_written is not None:
			dirt_s = ('*' if dirt_written else '!') + dirt_s	# volkszähler write of this hour's value
		marker = '*' if h == now.hour else ' '
		print('%2d%s %8s %8s %5s %8s %8s %3s %6s %-8s' % (h, marker, pv, rad_s, clr, exp, bl, chg, dirt_s, zones[h]))
	print('')


def main():
	vz = read_average()
	basic_load = vz['basic_load']
	now = datetime.now()
	pv_curve = read_pv_curve()
	radiation = read_radiation_forecast()
	expected_pv = scaled_pv_curve(pv_curve, radiation, now)	# drives red_window_demand/dirty_protect_hours below
	grid_data = read_smard_zones()					# before get_vz_bat_cap, so all cache notices print together
	if grid_data is None:
		die('SMARD zone data unavailable (fetch failed, no cache newer than one day)', conf['timer.txt'])
	_voltage, content = get_vz_bat_cap()

	yellow_cap = conf['yellow_cap']
	r = grid_data['ratio'][now.hour]
	dirt_written = write_dirtiness_to_vz((1.0 - r) * 100) if r is not None else None
	if debug:
		_hourly_debug_table(now, pv_curve, radiation, expected_pv, basic_load, grid_data, dirt_written)

	# decide this run's discharge mode. A red hour always discharges with no
	# limit — that is where the reserve is meant to be spent. Outside a red
	# hour, dirty_protect_hours ranks every non-red hour between now and the
	# next PV-surplus hour by cleanliness and protects exactly as many of the
	# cleanest ones as it takes for their accumulated shortfall to cover the
	# red reserve target — a per-hour ranking across the whole window, so the
	# outcome rests on the ranking rather than on any single forecast value
	# (see the reserve_basis line for which hours were selected). The current
	# hour is protected if it is in that set.
	#
	# Without any PV forecast (curve never successfully computed, e.g. on a
	# fresh install), build_reserve_after (config) is used as a clock cutoff
	# instead, the reserve then being computed against a zero-PV day.
	#
	# Once protected, the zone and current content decide between 'limit'
	# (still above the reserve — bleed the surplus) and 'stop' (content has
	# already reached/dropped below it); green always stops once protected,
	# since it never discharges.
	zones = grid_data['zones']

	# the red reserve: basic_load demand over every red hour between now and
	# the next PV surplus phase (see red_window_demand), net of expected PV
	# during those hours, scaled by reserve_pct.
	pv_for_reserve = expected_pv if expected_pv is not None else [0.0] * 24
	reserve = conf['reserve_pct'] * 0.01 * red_window_demand(basic_load, now, zones, pv_for_reserve)

	if expected_pv is not None:
		protect_hours = dirty_protect_hours(now, zones, basic_load, expected_pv, grid_data['ratio'], reserve)
		protect_reserve = now.hour in protect_hours
		hours_s = ','.join('%02d' % h for h in sorted(protect_hours)) if protect_hours else '-'
		reserve_basis = ('dirt-ranked: %.0f Wh reserve target -> protected hours [%s]'
		                  % (reserve, hours_s))
	else:
		after = conf['build_reserve_after']
		hh, mm = (int(x) for x in after.split(':'))
		protect_reserve = (now.hour, now.minute) >= (hh, mm)
		reserve_basis = 'clock fallback: build_reserve_after %s (no PV forecast available)' % after

	zone = zones[now.hour]
	if not protect_reserve:
		mode = 'free'										# not protected (a red hour is never a candidate): run the battery down
	elif zone == 'green':
		mode = 'stop'										# low intensity: never discharge
	elif zone != 'red' and content <= reserve:
		mode = 'stop'										# hold the reserve for the next red span
	elif zone == 'red':
		mode = 'free'										# high intensity: no limit
	else:
		mode = 'limit'										# yellow: capped at yellow_cap

	if verbose:
		print('zone %s   content %d Wh   red reserve(%d%%) %.0f Wh' % (
			zone, content, conf['reserve_pct'], reserve))
		print('%s -> reserve %s   => discharge mode: %s' % (
			reserve_basis, 'protected' if protect_reserve else 'open', mode.upper()))

	if not conf['disable_zeroinput_timer']:
		write_timer(mode, now, yellow_cap)

	if verbose:
		print('dirt_shift done.', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
		if html: print('\n</pre></body></html>')
	return 0


def write_timer(mode, now, yellow_cap):
	"""Write timer.txt in the zeroinput format:
	  date time | discharge_W  ac_W  energy_Wh   (<=100 = percent, >100 = watt)
	Each line carries the real calendar date it was written for. pvpt
	(ac 100%) is always granted; only battery discharge is steered by 'mode':
	  free  -> no discharge limit (high-intensity hours)  '100 100 99999'
	  limit -> capped at yellow_cap W (transition)        '<yellow_cap> 100 99999'
	  stop  -> no discharge, pvpt only                    '000 100 000'
	The energy_Wh field stays unlimited (99999) even in 'limit' mode: the actual
	energy handed out is bounded by the slot's own Wh budget (computed elsewhere,
	via the reserve/red-window logic), not by this timer field. yellow_cap only
	limits the instantaneous power, so short spikes can still be served from the
	battery without the strict Wh contingent being exceeded for long.
	dirt_shift is optional and must never block normal operation, so the plan is
	a short chain re-written every run:
	  - the current 1/4h slot in the chosen mode;
	  - if the mode limits/stops discharge, an 'all allowed' line 30 min later as
	    a failsafe — renewed every run while the script lives, self-lifting after
	    30 min if it dies.
	Should dirt_shift stop running altogether, both lines eventually fall into
	the past; zeroinput's timer parser applies every already-past line in file
	order and only stops at the first future one, so once none is left in the
	future it simply keeps the values of the last line it saw — which is
	always the 'all allowed' failsafe line (or the single free-mode line) — so
	the file settles on the safe, unrestricted state on its own rather than
	re-arming the same limit every day."""
	FREE  = '100 100 99999'								# full discharge, full pvpt
	LIMIT = '%3d 100 99999' % yellow_cap				# yellow-zone power cap
	STOP  = '000 100 000'								# pvpt only, no discharge
	payload = {'free': FREE, 'limit': LIMIT, 'stop': STOP}[mode]

	lines = []
	t = now.replace(second=0, microsecond=0, minute=(now.minute // 15) * 15)
	lines.append((t, payload))
	if mode != 'free':									# failsafe: lift any limit/stop after 30 min
		t2 = t + timedelta(minutes=30)
		lines.append((t2, FREE))

	with open(conf['timer.txt'], 'w') as fo:
		fo.write('# %s  (dirt_shift)\n' % datetime.now().strftime('%Y-%m-%dT%H:%M:%S'))
		fo.write('# real calendar date per line, space or tab separated\n')
		fo.write('#                   battery discharge W if > 100, percentage if <= 100\n')
		fo.write('# date     time     |   ac inverter power W if > 100, percentage if <= 100\n')
		fo.write('# |        |        |   |   energy limit in Wh\n')
		for lt, p in lines:
			line = '%s %02d:%02d:00 %s' % (lt.strftime('%Y-%m-%d'), lt.hour, lt.minute, p)
			fo.write(line + '\n')
			if debug: print(line)


exit(main())
