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
# and battery content, and writes the same timer.txt interface zeroinput reads
# for discharge control. No grid data is fetched — the intensity profile is
# derived from the computed sun position plus fixed load bounds.
#
# Concept (see comments at each step):
#   1. intensity profile: three zones from the clean (low-intensity) window, a
#      hybrid of sun position and fixed clock bounds (see _green_bounds):
#        high   (red)    — outside the clean window: evening, night, early morning
#        medium (yellow) — transition band on both edges of the clean window
#        low    (green)  — midday
#   2. red reserve = reserve_pct (~90%) * basic_load over every red hour
#      between now and the next PV surplus phase (the point the battery
#      genuinely refills from).
#   3. the reserve is protected as soon as the forecasted PV yield remaining
#      today (empirical PV curve x shortwave-radiation forecast) can no longer
#      close the gap to it — a bright day can push this later than a fixed
#      clock time would, a dull day earlier. Without a usable forecast,
#      build_reserve_after (~13:30) is used as a fallback clock cutoff. While
#      the reserve is not protected, discharge is free regardless of zone —
#      this runs the battery down (even through a clean/green midday) to make
#      room for the day's remaining PV yield. Once protected, the zone decides:
#      high -> no limit; medium -> capped at yellow_cap (unless content has
#      dropped to the reserve, then stop); low -> no battery discharge
#      (pvpt only).
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
	date d, for the configured location. Same approximation as sun_times (no
	library, equation of time neglected) — kept consistent with the rest of
	dirt_shift's sun-position math, and more than accurate enough against the
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
	aerosol data, so it is computable offline, consistent with the rest of
	dirt_shift's sun-position math. It is the denominator against which the
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
	the same location as the sun-position zones. shortwave_radiation is the
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
	"""Today's and tomorrow's SMARD-derived zones (see _smard_zones_for_date),
	instead of the sun-position heuristic. Returns (today, tomorrow), each
	either a {'zones':..., 'ratio':...} dict or None. Tomorrow's day-ahead
	data commonly is not published yet earlier in the day — that is expected
	and not treated as an error, tomorrow is simply None then. Returns
	(None, None) if SMARD is disabled or today's own query fails (the caller
	then falls back to dirtiness_zone(), the sun-position heuristic)."""
	if not conf.get('smard_enabled', False):
		return None, None
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
	from tomorrow (day-ahead data not yet published, or entirely absent)
	falls back to today's classification for that same hour. With
	smard_enabled false (default), this returns None on every call without
	trying the network, printing a one-line note under -v so 'disabled' is
	distinguishable from 'enabled but failing' (which prints its own message
	from _smard_zones_for_date). Either way, the caller falls back to
	dirtiness_zone()."""
	if not conf.get('smard_enabled', False):
		if verbose: print('SMARD disabled (smard_enabled=false) — using sun-position zones')
		return None
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
			vz_in['timestamp'] = datetime.now().timestamp()
			with open(join(dirname(__file__), 'dirt_smard_cache.json'), 'w') as fo:
				json_dump(vz_in, fo)
		elif verbose:
			print('SMARD zone refresh failed — keeping previous zones')
	elif verbose:
		print('using cached SMARD zones from',
		      datetime.fromtimestamp(last_ts).strftime('%Y-%m-%d %H:%M') if last_ts else 'never')

	today = vz_in.get('today')
	if today is None:
		return None
	tomorrow = vz_in.get('tomorrow')
	if tomorrow is None:
		return today									# no tomorrow data at all: today's classification stands for the whole rolling window
	now_hour = datetime.now().hour
	zones = [today['zones'][h] if h >= now_hour else tomorrow['zones'][h] for h in range(24)]
	ratio = [today['ratio'][h] if h >= now_hour else tomorrow['ratio'][h] for h in range(24)]
	return {'zones': zones, 'ratio': ratio}


def get_vz_bat_cap():
	"""Reconstruct real battery energy content (Wh) by integrating PV and
	Inverter since the last known 'empty' state (voltage <= 3.0625 V/cell, i.e.
	49 V at 16 cells, scaled by cell_count). Returns (latest_voltage,
	content_Wh)."""
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
	limit), 'yellow' (transition, capped at yellow_cap) or 'green' (low intensity,
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


def effective_zone(now, grid_data):
	"""The zone actually used for this run's decision: the SMARD-derived zone
	for the current hour if available, otherwise the sun-position heuristic
	(dirtiness_zone). Keeps the fallback in one place so main() does not need
	to know which source produced the zone. grid_data is the
	{'zones':..., 'ratio':...} dict from read_smard_zones(), or None."""
	if grid_data is not None:
		return grid_data['zones'][now.hour]
	return dirtiness_zone(now)


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
	share), negative = cleaner than average / renewable surplus. Silently
	does nothing if no UUID is configured. Checks the HTTP response status
	(raise_for_status) so a rejected write — wrong/unknown UUID, server
	error — is caught here rather than passing as silent success; under -v,
	prints a one-line confirmation on success and the error on failure.
	Never raises further — a failed write must not abort the run."""
	uuid = conf.get('vz_dirtiness_uuid', '')
	if not uuid:
		return
	try:
		url = 'http://%s/data/%s.json' % (conf['vz_host_port'], uuid)
		resp = post(url=url, params={'value': value, 'ts': int(time() * 1000)}, timeout=10)
		resp.raise_for_status()
		if verbose: print('dirtiness value written: %g' % value)
	except Exception as e:
		if verbose: print('dirtiness write failed:', e)


def _zone_array(now, grid_data):
	"""The 24-hour zone array actually driving this run's decisions: SMARD's if
	available, otherwise the sun-position zone computed per hour. Used to keep
	_bridge_hours and red_window_demand consistent with whichever zone source
	effective_zone() draws on for the current hour, instead of always deriving
	window boundaries from the sun position regardless of source."""
	if grid_data is not None:
		return grid_data['zones']
	return [dirtiness_zone(now.replace(hour=h, minute=0, second=0, microsecond=0)) for h in range(24)]


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
	hour); every full hour after that counts whole. Without expected_pv, no
	surplus boundary exists — the window then runs the full rolling 24 h,
	matching red_window_demand's own no-forecast fallback. Empty if the
	current hour is itself a surplus hour."""
	hours, h, first = [], now.hour, True
	for _ in range(24):
		pv = expected_pv[h] if expected_pv is not None else 0.0
		if expected_pv is not None and pv > basic_load[h]:
			break
		hours.append((h, (60 - now.minute) / 60.0 if first else 1.0))
		first = False
		h = (h + 1) % 24
	return hours


def _sum_hours(values, hours):
	"""Wh total of a 24-value hourly array over the (hour, fraction) pairs
	from _bridge_hours. 0.0 if values is unavailable."""
	if values is None:
		return 0.0
	return sum(values[h] * frac for h, frac in hours)


def red_window_demand(basic_load, now, zones, expected_pv=None):
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
	_zone_array, read_smard_zones, read_radiation_forecast): an hour before
	now.hour is really tomorrow's occurrence of that hour, with tomorrow's
	own classification/value where available. A deficit red hour and the
	(rare) surplus red hour offset each other within the window; only the
	final total is floored at zero. Zero if no red hour lies before the
	surplus point. Without expected_pv (no forecast), no surplus point can be
	detected and no netting done — the plain basic_load sum over all red
	hours of the rolling day is used as the conservative fallback."""
	demand = 0.0
	h = now.hour
	for _ in range(24):
		pv = expected_pv[h] if expected_pv is not None else 0.0
		if expected_pv is not None and pv > basic_load[h]:
			break										# PV surplus hour: the battery refills from here on
		if zones[h] == 'red':
			demand += basic_load[h] - pv
		h = (h + 1) % 24
	return max(0.0, demand)


def reserve_build_hour(content, basic_load, now, zones, expected_pv):
	"""Predicted hour-of-day at which the red reserve starts being BUILT —
	i.e. the first hour, scanning forward from now up to (not into) the next
	red hour, at which the same projection the discharge decision uses
	(content + remaining PV - remaining load until the next red hour, vs the
	reserve target at that hour) would fall short, switching protection on
	and beginning to retain energy. The forecast-based counterpart to the
	build_reserve_after clock fallback. Current content is held constant
	across the scan (the real trajectory depends on actual consumption and
	yield; every quarter-hour run re-evaluates with fresh content anyway), so
	this is an indication, not a commitment. If protection is already active
	now, this returns the current hour. Returns None if no hour before the
	next red span is predicted to need protection — e.g. when the PV surplus
	phase runs right up to the red start, so the reserve is built implicitly
	by charging during surplus, never by restricting discharge."""
	for i in range(24):
		fake = now.replace(hour=(now.hour + i) % 24, minute=now.minute if i == 0 else 0)
		if zones[fake.hour] == 'red':
			return None									# red span reached: the reserve is spent there, not built
		target = conf['reserve_pct'] * 0.01 * red_window_demand(basic_load, fake, zones, expected_pv)
		if target <= 0:
			continue
		bridge = _bridge_hours(fake, zones, basic_load, expected_pv)
		proj = content + _sum_hours(expected_pv, bridge) - _sum_hours(basic_load, bridge)
		if proj < target:
			return fake.hour
	return None


def _hourly_debug_table(now, pv_curve, radiation, expected_pv, grid_data):
	"""Print one aligned table (hour 0-23) combining the PV reference curve,
	the shortwave-radiation forecast, the clear-sky index derived from it
	(see scaled_pv_curve/clear_sky_ghi), expected PV, and the CO2-intensity
	zone/dirtiness — SMARD's if available, otherwise the sun-position zone for
	that hour (see _zone_array, the same array _bridge_hours/red_window_demand
	use) — so everything the discharge decision draws on is visible at a
	glance, in one place instead of five separate lists. 'dirt%' is
	(1 - ratio) * 100 (see write_dirtiness_to_vz): 0 at ratio 1 (renewables
	exactly cover load), negative on a renewable surplus (ratio > 1), rising
	toward 100 as the renewable share drops toward 0. The current hour is
	marked with '*'."""
	source = 'SMARD' if grid_data is not None else 'sun-position (SMARD unavailable)'
	zones = _zone_array(now, grid_data)
	print('hourly data (zone source: %s)' % source)
	print('%-3s %8s %8s %5s %8s %6s %-8s' % ('hr', 'PV_curve', 'rad_Wm2', 'clr%', 'exp_PV', 'dirt%', 'zone'))
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
		if grid_data is not None:
			ratio_h = grid_data['ratio'][h]
			dirt_s = ('%.0f' % ((1.0 - ratio_h) * 100)) if ratio_h is not None else '-'
		else:
			dirt_s = '-'
		marker = '*' if h == now.hour else ' '
		print('%2d%s %8s %8s %5s %8s %6s %-8s' % (h, marker, pv, rad_s, clr, exp, dirt_s, zones[h]))



def main():
	vz = read_average()
	basic_load = vz['basic_load']
	now = datetime.now()
	pv_curve = read_pv_curve()					# not yet used in the discharge decision — cached for future use
	radiation = read_radiation_forecast()				# ditto
	expected_pv = scaled_pv_curve(pv_curve, radiation, now)
	_voltage, content = get_vz_bat_cap()

	yellow_cap = conf['yellow_cap']
	grid_data = read_smard_zones()
	zone = effective_zone(now, grid_data)
	zone_source = 'SMARD' if grid_data is not None else 'sun-position'
	if debug:
		_hourly_debug_table(now, pv_curve, radiation, expected_pv, grid_data)

	if grid_data is not None:
		r = grid_data['ratio'][now.hour]
		if r is not None:
			write_dirtiness_to_vz((1.0 - r) * 100)

	zones = _zone_array(now, grid_data)

	# the red reserve: basic_load demand over every red hour between now and
	# the next PV surplus phase (see red_window_demand), net of expected PV
	# during those hours, scaled by reserve_pct.
	reserve = conf['reserve_pct'] * 0.01 * red_window_demand(basic_load, now, zones, expected_pv)

	# decide this run's discharge mode. While the reserve is not (yet) protected,
	# discharge is free regardless of zone — this runs the battery down (even
	# through a clean/green midday) to make room for the day's remaining PV
	# yield. Once the reserve is protected, the CO2-intensity zone and the
	# reserve decide:
	#   red    -> high intensity: discharge with no limit (full output)
	#   yellow -> transition: discharge capped to yellow_cap (Watt), unless the
	#             reserve is being protected and content has dropped to it
	#   green  -> low intensity: no battery discharge (pvpt only)
	# Once content has dropped to the reserve, discharge stops (outside red)
	# until the next red span itself begins (where the reserve is then spent).
	#
	# protect_reserve is driven by a full energy-balance projection, not a fixed
	# clock time: current content plus the forecasted PV yield (curve x clear-
	# sky-scaled radiation) remaining until the coming red window starts, minus
	# the basic_load still due in that same span. If that projection would fall
	# short of the reserve, protection starts now — a bright day with little
	# load left can push this later than a fixed clock time would, a dull day
	# or a lot of remaining consumption earlier. This is independent of the
	# plain current content (like the old clock cutoff was): whether content
	# has already dropped to the reserve right now is the separate check
	# below, which is what actually distinguishes yellow's 'limit' (protected,
	# but current content is still above the reserve — bleed the surplus)
	# from 'stop' (content has already reached/dropped below it).
	# build_reserve_after (config) is used only as a fallback clock cutoff when
	# no forecast is available at all (PV curve never successfully computed); a
	# curve without a radiation forecast still counts as a forecast
	# (scaled_pv_curve then just returns it unscaled).
	if expected_pv is not None:
		bridge = _bridge_hours(now, zones, basic_load, expected_pv)
		remaining_pv   = _sum_hours(expected_pv, bridge)
		remaining_load = _sum_hours(basic_load, bridge)
		projected = content + remaining_pv - remaining_load
		protect_reserve = projected < reserve
		build_h = reserve_build_hour(content, basic_load, now, zones, expected_pv)
		build_s = ' (>%02d:00)' % build_h if build_h is not None else ''
		reserve_basis = ('forecast: content %.0f + remaining PV %.0f - remaining load %.0f '
		                  '= projected %.0f Wh vs %.0f Wh reserve target%s'
		                  % (content, remaining_pv, remaining_load, projected, reserve, build_s))
	else:
		after = conf['build_reserve_after']
		hh, mm = (int(x) for x in after.split(':'))
		protect_reserve = (now.hour, now.minute) >= (hh, mm)
		reserve_basis = 'clock fallback: build_reserve_after %s (no PV forecast available)' % after

	if not protect_reserve:
		mode = 'free'										# reserve not (yet) protected: run the battery down regardless of zone
	elif zone == 'green':
		mode = 'stop'										# low intensity: never discharge
	elif zone != 'red' and content <= reserve:
		mode = 'stop'										# hold the reserve for the next red span
	elif zone == 'red':
		mode = 'free'										# high intensity: no limit
	else:
		mode = 'limit'										# yellow: capped at yellow_cap

	if verbose:
		sr, ss = sun_times(now)
		srs = '%05.2f' % sr if sr is not None else 'n/a'
		sss = '%05.2f' % ss if ss is not None else 'n/a'
		print('\ndirt_shift  %s' % now.strftime('%Y-%m-%d %H:%M:%S'))
		print('sun rise %s set %s   zone %s (%s)   content %d Wh   red reserve(%d%%) %.0f Wh' % (
			srs, sss, zone, zone_source, content, conf['reserve_pct'], reserve))
		print('%s -> reserve %s   => discharge mode: %s' % (
			reserve_basis, 'protected' if protect_reserve else 'open', mode.upper()))

	if not conf['disable_zeroinput_timer']:
		write_timer(mode, now, yellow_cap)

	if verbose:
		print('done.', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
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
