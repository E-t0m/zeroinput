#!/usr/bin/python3
# -*- coding: utf-8 -*-
# zeroinput - charger_drivers: MPPT charger abstraction v2.2
#
# Handles all charger-side I/O: eSmart3, Victron VE.Direct (conventional +
# aggregator), temperature sensors. Owns mppt_data, all reader threads and the
# VEDirectBridge instances. zeroinput.py calls the public API below and reads
# mppt_data directly (shared reference under GIL).
#
# Public API used by zeroinput.py:
#   build_chargers(conf, verbose)       -> (esmart3_devs, victron_devs, temp_sensor_devs,
#                                           esmart_handles, modbus_handles)
#   combine_charger_data(conf, verbose)
#   display_mppt_data(conf, verbose)
#   poll_chargers(esmart_handles, conf, timeout_repeat, pv_cont, verbose)
#   check_temp_alarms(conf, alarm_last, esmart3_devs, temp_sensor_devs, verbose)
#   set_victron_power(device_key, watts, conf)
#   start_victron_threads(victron_devs, conf, stop_event, verbose)
#                                       -> list of started Thread objects
#   check_stale()
#   drain_rec_msgs()
#
# mppt_data is a module-level dict, written by reader threads and the eSmart
# poller, read by zeroinput.py.  All mppt_data writes are atomic (reference
# replacement) and safe under the GIL without an explicit lock.

from serial import Serial, SerialException
from threading import Thread
from queue import Queue, Empty
from time import time, sleep
from datetime import datetime, timedelta
from os import system
from traceback import print_exc


# ── shared state ──────────────────────────────────────────────────────────────

mppt_data = {'combined': {}}				# {device_key: {field: value}}

_vedirect_instances	= {}					# {physical_port: VEDirectBridge}
_victron_cmd_queues	= {}					# {device_key: Queue(maxsize=1)}
_rec_msgs			= Queue()				# deferred REC messages
_ppv_decay			= {}					# {device_key: float} — last PPV × decay factor
_last_vbat			= None					# last plausible averaged battery voltage (held when all MPPTs are offline)
HEAT_SENSOR_HOLD_CYCLES = 20				# keep showing (and regulating on) a failed device's last temperature for this many cycles; a real sink/case temperature changes < 1 °C/min, so a brief gap is safe. After that the temperature is dropped so heat protection falls through to its safe fallback.
_temp_hold_cnt		= {}					# {port: cycles the temperature has been held through a PORT error}

_MPPT_FMT = '{:<12s}  {:<10s}  {:>4s}  {:>4s}  {:>5s}  {:>5s}  {:<6s}  {:>5s}  {:>5s}  {:>3s}  {:>4s}  {:>4s}  {:<8s}'


def port_error(port):
	"""Build the PORT-error record for a device that failed to read this cycle.
	The timestamp 'ts' of the last valid reading is preserved (when present), so
	the status table's 'age' column keeps counting how long the device has been
	silent instead of going blank the moment the port fails.

	The last known temperatures are preserved as well, but only for
	HEAT_SENSOR_HOLD_CYCLES cycles: a real sink/case temperature changes slower
	than ~1 °C/min, so holding briefly is safe and lets both the status table and
	heat protection keep using the last value. Once the hold expires the
	temperatures are dropped, so heat protection sees no reading and falls through
	to its safe fallback. A device that never produced a valid reading (cold
	start) has none of these, and the columns stay empty."""
	old = mppt_data.get(port, {})
	d = {'CS': 'PORT'}
	if 'ts' in old:
		d['ts'] = old['ts']

	has_temp = 'ext_temp' in old or 'int_temp' in old
	if has_temp:
		cnt = _temp_hold_cnt.get(port, 0)
		if cnt < HEAT_SENSOR_HOLD_CYCLES:
			_temp_hold_cnt[port] = cnt + 1
			for k in ('ext_temp', 'int_temp'):
				if k in old:
					d[k] = old[k]
		# else: hold expired -> temperatures dropped, cnt stays at the cap
	return d


# ── conf expansion ─────────────────────────────────────────────────────────────

def expand_victron_agg(conf):
	"""Expand victron_agg entries in conf['chargers'] into synthetic per-device
	entries keyed by SER#. The original victron_agg entry is removed."""
	chg = conf['chargers']
	for port in list(chg.keys()):
		dev = chg[port]
		if dev.get('mppt_type') != 'victron_agg':
			continue
		port_name = dev.get('name', port)
		for ser, dev_info in dev.get('devices', {}).items():
			name      = dev_info['name'] if isinstance(dev_info, dict) else dev_info
			dev_type  = dev_info.get('type', 'mppt') if isinstance(dev_info, dict) else 'mppt'
			pvp       = dev_info.get('pvp', 0)       if isinstance(dev_info, dict) else 0
			if dev_type == 'temp':
				chg[ser] = {
					'name':       name,
					'mppt_type':  'temp_sensor',
					'_agg_port':  port,
					'_ser':       ser,
					'_port_name': port_name,
				}
				if isinstance(dev_info, dict) and dev_info.get('heat_protect'):
					chg[ser]['heat_protect'] = True		# heat-protection trigger on an AGG sub-sensor
			else:
				chg[ser] = {
					'name':       name,
					'mppt_type':  'victron',
					'_agg_port':  port,
					'_ser':       ser,
					'_pvp':       pvp,
					'_port_name': port_name,
				}
		del chg[port]
	return conf


def build_chargers(conf, verbose=False):
	"""Discover charger devices from conf['chargers'].
	Returns (esmart3_devs, victron_devs, temp_sensor_devs, esmart_handles,
	         modbus_handles).
	Also initialises mppt_data entries for each device."""
	esmart3_devs     = []
	victron_devs     = []
	temp_sensor_devs = []
	modbus_devs      = []

	# validate unique device names
	names = [v['name'] for v in conf['chargers'].values() if 'name' in v]
	if len(names) != len(set(names)):
		print('ERROR: duplicate device names in chargers config: %s' % names)
		raise SystemExit(1)

	for dev in conf['chargers']:
		mtype = conf['chargers'][dev].get('mppt_type')
		if mtype == 'victron':
			_thread_port = conf['chargers'][dev].get('_agg_port', dev)
			if _thread_port not in victron_devs:
				victron_devs.append(_thread_port)
			mppt_data[dev] = {}
		elif mtype == 'eSmart3':
			esmart3_devs.append(dev)
			mppt_data[dev] = {}
		elif mtype == 'temp_sensor':
			temp_sensor_devs.append(dev)
			mppt_data[dev] = {}
		elif mtype in MODBUS_DRIVERS:
			modbus_devs.append(dev)
			mppt_data[dev] = {}

	# open and warm up esmart handles
	esmart_handles = []
	for port in esmart3_devs:
		h = {'obj': esmart(verbose)}
		h['obj'].set_port(port, conf)
		h['obj'].open()
		for i in [1, 2]:
			if verbose: print(i)
			h['obj'].esmart_status_request()
			sleep(0.20)
		h['obj'].close()
		esmart_handles.append(h)

	# build modbus charger handles (epever / renogy / morningstar)
	modbus_handles = []
	for port in modbus_devs:
		cfg  = conf['chargers'][port]
		drv  = MODBUS_DRIVERS[cfg['mppt_type']]
		unit = cfg.get('unit', 1)		# modbus slave address, default 1
		obj  = drv(port, unit=unit, verbose=verbose)
		obj.open()
		obj.read()
		obj.close()
		modbus_handles.append({'obj': obj, 'port': port})

	return (esmart3_devs, victron_devs, temp_sensor_devs, esmart_handles,
	        modbus_handles)


def start_victron_threads(victron_devs, conf, stop_event, verbose=False):
	"""Start one reader thread (or VEDirectBridge) per physical Victron port.
	Returns list of Thread objects."""
	threads = []
	for port in victron_devs:
		# check if this port is an AGG port
		is_agg = any(
			d.get('_agg_port') == port
			for d in conf['chargers'].values()
		)
		if is_agg:
			try:
				bridge = VEDirectBridge(port, conf, verbose)
				bridge.start()
				_vedirect_instances[port] = bridge
				if verbose: print('VEDirectBridge started on %s' % port)
			except ImportError as e:
				print('VEDirectBridge: %s' % e)
		else:
			_victron_cmd_queues[port] = Queue(maxsize=1)
			t = Thread(target=handle_victron_data,
			           args=(port, conf, stop_event, verbose), daemon=True)
			t.start()
			threads.append(t)
	return threads


# ── display ────────────────────────────────────────────────────────────────────

def drain_rec_msgs():
	"""Print all deferred REC messages queued by reader threads."""
	while not _rec_msgs.empty():
		try: print(_rec_msgs.get_nowait())
		except Empty: break


def display_mppt_data(conf, verbose=False):
	"""Print the MPPT charger data table (only when verbose)."""
	if not verbose: return
	print(_MPPT_FMT.format(
		'device', 'name', 'W PV', '%PVp', 'V bat', 'I bat',
		'mode', 'Pload', 'Iload', 'age', 'Tint', 'Text', ''))

	for port in list(mppt_data.keys()):
		port_data = mppt_data.get(port, {})
		if port != 'combined' and conf['chargers'].get(port, {}).get('mppt_type') == 'temp_sensor':
			continue
		if 'CS' in port_data:
			cs = port_data['CS']
			if isinstance(cs, str) and cs.lstrip('-').isdigit(): cs = int(cs)
			if port != 'combined' and conf['chargers'][port].get('mppt_type') == 'victron':
				if   cs == 'PORT':                             mppt_dev_mode = 'ErrorP'
				elif isinstance(cs, int) and cs <= 14:         mppt_dev_mode = ['OFF','','FAULT','BULK','ABSORB','FLOAT','','EQUAL','','','START','','RECOND','','EXTCON'][cs]
				else:                                          mppt_dev_mode = ''
			elif port != 'combined' and conf['chargers'][port].get('mppt_type') == 'eSmart3':
				if   cs == 'PORT':                             mppt_dev_mode = 'ErrorP'
				elif isinstance(cs, int) and cs <= 4:          mppt_dev_mode = ['WAIT','MPPT','BULK','FLOAT','PRE'][cs]
				else:                                          mppt_dev_mode = ''
			elif port != 'combined' and conf['chargers'][port].get('mppt_type') in MODBUS_DRIVERS:
				if   cs == 'PORT':                             mppt_dev_mode = 'ErrorP'
				elif isinstance(cs, int) and cs <= 8:          mppt_dev_mode = ['OFF','MPPT','BULK','','ABSORB','FLOAT','','FLOAT','EQUAL'][cs]
				else:                                          mppt_dev_mode = ''
			else: mppt_dev_mode = ''
		else: mppt_dev_mode = ''

		load_str = ''
		if 'LOAD' in port_data:
			on = port_data['LOAD'] == 'ON'
			load_str = ('%.1f' % port_data.get('IL', 0)) if on else 'OFF'
		elif 'Iload' in port_data:
			il = port_data['Iload']
			load_str = ('%.1f' % il) if il > 0 else ''

		if port == 'combined':
			pvp_val = sum(
				d.get('pvp', 0) or d.get('_pvp', 0)
				for d in conf['chargers'].values()
				if d.get('mppt_type') in ('victron', 'eSmart3')
			) or None
		else:
			pvp_val = conf['chargers'][port].get('pvp') or conf['chargers'][port].get('_pvp') or None
		ppv     = port_data.get('PPV')
		if ppv is None and port != 'combined' and _ppv_decay.get(port, 0) > 0:
			ppv = _ppv_decay[port]			# PORT error: show the decaying PPV contribution
		pvp_str = ('%i%%' % int(ppv / pvp_val * 100)) if (pvp_val and ppv is not None and pvp_val > 0) else ''

		# first column: for direct Victron devices show SER# (once known), else port path
		if port != 'combined' and conf['chargers'][port].get('mppt_type') == 'victron' \
				and '_agg_port' not in conf['chargers'][port]:
			port_label = port_data.get('SER#', port)
		elif port == 'combined':
			port_label = 'charger'
		else:
			port_label = port

		print(_MPPT_FMT.format(
			port_label,
			'combined' if port == 'combined' else conf['chargers'][port]['name'],
			'%i'   % ppv			if ppv is not None else '',
			pvp_str,
			'%.2f' % port_data['Vbat']	if 'Vbat' in port_data else '',
			'%.2f' % port_data['Ibat']	if 'Ibat' in port_data else '',
			mppt_dev_mode,
			('%i' % port_data['Pload']) if ('Pload' in port_data and port_data['Pload'] > 0) else '',
			load_str,
			('%.0fs' % (time() - port_data['ts'])) if 'ts' in port_data else '',
			str(port_data['int_temp'])	if 'int_temp' in port_data else '',
			str(port_data['ext_temp'])	if 'ext_temp' in port_data else '',
			str(conf['chargers'][port]['temp_display']) if (
				port != 'combined' and 'temp_display' in conf['chargers'][port]) else ''))

	for bridge in _vedirect_instances.values():
		known     = set(bridge._ser_to_key.keys())
		_any_key  = next(iter(bridge._ser_to_key.values()), None)
		port_name = conf['chargers'][_any_key]['_port_name'] if _any_key else bridge._physical
		for ser in bridge._vd.get_all().keys():
			if ser not in known:
				print('{:12s}  {:10s}  UNCONFIGURED'.format(ser[:12], port_name[:10]))

	for port in list(mppt_data.keys()):
		if port == 'combined': continue
		if conf['chargers'].get(port, {}).get('mppt_type') != 'temp_sensor': continue
		port_data = mppt_data.get(port, {})
		temp = port_data.get('ext_temp')
		age  = ('%.0fs' % (time() - port_data['ts'])) if 'ts' in port_data else ''
		print(_MPPT_FMT.format(
			conf['chargers'][port].get('_port_name', port)[:12],
			conf['chargers'][port]['name'],
			'', '', '', '', 'TEMP', '', '', age, '',
			('%.1f' % temp) if temp is not None else '', ''))


# ── data aggregation ───────────────────────────────────────────────────────────

def combine_charger_data(conf, verbose=False):
	"""Aggregate mppt_data from all charger devices into mppt_data['combined'].
	Pload is the real sum of every device's measured load (no projection).
	On PORT error the last known PPV is retained and reduced by 10% per cycle
	until the device recovers or the value reaches zero."""
	d = {'PPV': 0, 'Vbat': 0, 'Ibat': 0, 'Pload': 0}

	# PPV: live data resets decay; PORT error applies and steps decay
	for dev in list(mppt_data.keys()):
		if dev == 'combined': continue
		dev_data = mppt_data.get(dev, {})
		if dev_data.get('CS') == 'PORT':
			# device is in error — decay the last known PPV
			decayed = _ppv_decay.get(dev, 0) * 0.9
			_ppv_decay[dev] = decayed
			d['PPV'] += decayed
		elif 'PPV' in dev_data:
			# live data — use real value, reset decay store
			ppv = dev_data['PPV']
			_ppv_decay[dev] = ppv
			d['PPV'] += ppv
		if dev_data.get('CS') != 'PORT' and ('ext_temp' in dev_data or 'int_temp' in dev_data):
			_temp_hold_cnt[dev] = 0			# live temperature — reset the hold counter

	# Vbat / Ibat / Pload: straight sum/avg, no decay.
	# Vbat is filtered for plausibility: a momentarily disturbed charger can report
	# 0 V (or otherwise implausibly low). Such a reading must NOT drag the averaged
	# Vbat below the battery-protection threshold and trip a false timeout. A
	# LiFePO4 cell never runs below ~2.5 V in operation; anything under 2.0 V/cell
	# is impossible and is dropped from the average. Ibat/Pload are summed as before.
	_vbat_floor = 2.0 * conf.get('cell_count', 16)		# implausible-Vbat threshold (2.0 V/cell)
	_vbat_valcnt = 0									# how many chargers gave a plausible Vbat this cycle
	for name in ('Vbat', 'Ibat', 'Pload'):
		valcnt = 0
		for dev in list(mppt_data.keys()):
			if dev == 'combined': continue
			dev_data = mppt_data.get(dev, {})
			if name not in dev_data: continue
			if name == 'Vbat' and dev_data[name] < _vbat_floor: continue	# drop implausible reading
			valcnt += 1
			d[name] += dev_data[name]
		if name == 'Vbat':
			_vbat_valcnt = valcnt
			if valcnt > 0:
				d[name] /= valcnt

	# Hold the last plausible voltage when every MPPT is offline (e.g. at night,
	# no PV, chargers asleep). Reporting 0 V would be indistinguishable from a real
	# empty battery and would trip the undervoltage timeout, so the last known
	# voltage is carried forward instead. (A caller that needs to know whether the
	# chargers are actually online can inspect the per-device CS:'PORT' states in
	# mppt_data directly.)
	global _last_vbat
	if _vbat_valcnt > 0:
		_last_vbat = d['Vbat']
	else:
		d['Vbat'] = _last_vbat if _last_vbat is not None else 0

	pvp_sum = sum(
		dev.get('pvp', 0) or dev.get('_pvp', 0)
		for dev in conf['chargers'].values()
		if dev.get('mppt_type') in ('victron', 'eSmart3') or dev.get('mppt_type') in MODBUS_DRIVERS
	)
	if pvp_sum > 0:
		d['PVperc'] = int(d['PPV'] / pvp_sum * 100)

	mppt_data['combined'] = d


# ── eSmart3 + Modbus polling ─────────────────────────────────────────────────

def poll_chargers(esmart_handles, conf, timeout_repeat, pv_cont, verbose=False, modbus_handles=None):
	"""Poll all eSmart3 chargers (open → request × 2 → close), then all Modbus
	chargers (epever / renogy / morningstar)."""
	if esmart_handles:
		for charger in esmart_handles: charger['obj'].open()
		for i in [1, 2]:
			if datetime.now() > timeout_repeat or pv_cont != 0:
				for charger in esmart_handles:
					charger['obj'].esmart_status_request()
					if verbose: print('%i:  %s : %s status request' % (
						i, charger['obj'].port, conf['chargers'][charger['obj'].port]['name']))
			elif verbose: print('. eSmart3 status')
			sleep(0.22)
		for charger in esmart_handles: charger['obj'].close()

	# poll Modbus chargers (open → read → close), one read per cycle
	if modbus_handles:
		for h in modbus_handles:
			h['obj'].open()
			h['obj'].read()
			h['obj'].close()
			if verbose:
				print('modbus: %s : %s read' % (
					h['port'], conf['chargers'][h['port']]['name']))


# ── temperature alarms ─────────────────────────────────────────────────────────

def check_temp_alarms(conf, alarm_last, esmart3_devs, temp_sensor_devs, verbose=False):
	"""Check and trigger temperature alarms for eSmart3 and AGG temp_sensor devices."""
	def fire(a, data, key, sub, label, name, times):
		if key not in data: return
		temp = data[key]
		for bound, cmp in (('hi', temp.__gt__), ('lo', temp.__lt__)):
			thr_key = '%s_%s' % (sub, bound)
			cmd_key = '%s_%s_cmd' % (sub, bound)
			if thr_key in a and a.get(cmd_key) and cmp(a[thr_key]):
				if verbose: print('\nTEMPERATURE ALARM %s %s %s : %s °C (%s %i)\n' % (
					label, name, bound, temp, bound, a[thr_key]))
				tkey     = '%s_%s' % (sub, bound)
				interval = a.get('%s_%s_interval' % (sub, bound), 300)
				if times[tkey] + timedelta(seconds=interval) < datetime.now():
					times[tkey] = datetime.now()
					system(a[cmd_key])

	alarms = conf.get('alarms', {})
	# Modbus chargers (epever/renogy/morningstar) also expose int/ext temps
	modbus_devs = [p for p, c in conf['chargers'].items()
	               if c.get('mppt_type') in MODBUS_DRIVERS]
	for port in esmart3_devs + temp_sensor_devs + modbus_devs:
		name = conf['chargers'][port].get('name')
		a    = alarms.get(name, {})
		if not a or port not in mppt_data: continue
		times = alarm_last.setdefault(name, {
			'int_hi': datetime.min, 'int_lo': datetime.min,
			'ext_hi': datetime.min, 'ext_lo': datetime.min})
		fire(a, mppt_data[port], 'int_temp', 'int', 'internal', name, times)
		fire(a, mppt_data[port], 'ext_temp', 'ext', 'external', name, times)
	return alarm_last


def check_stale():
	"""Mark AGG devices not seen within device_timeout as stale (CS='PORT')."""
	for bridge in _vedirect_instances.values():
		bridge.check_stale()


# ── Victron power control ──────────────────────────────────────────────────────

def set_victron_power(device_key, watts, conf):
	"""Set MPPT charge power limit. Works for both AGG and conventional ports."""
	dev = conf['chargers'].get(device_key, {})
	if '_agg_port' in dev:
		vd = _vedirect_instances.get(dev['_agg_port'])
		if vd:
			vd.set_watts(dev['_ser'], watts)
	else:
		q = _victron_cmd_queues.get(device_key)
		if q:
			try:   q.put_nowait(watts)
			except Exception: pass


# ── eSmart3 class ──────────────────────────────────────────────────────────────

class esmart:
	"""eSmart3 MPPT charger driver.
	Original lib by skagmo.com 2018 (https://github.com/skagmo/esmart_mppt),
	adapted for zeroinput."""

	def __init__(self, verbose=False):
		self.state      = 0
		self.data       = []
		self.port       = ''
		self.name       = ''		# device name from conf — set via set_port(port, conf)
		self.timeout    = 0
		self.verbose    = verbose
		self.ser        = None

	def __del__(self): self.close()

	def set_port(self, port, conf=None):
		self.port = port
		if conf is not None:
			self.name = conf.get('chargers', {}).get(port, {}).get('name', port)
		else:
			self.name = port

	def open(self):
		try:
			self.ser = Serial(self.port, 9600, timeout=0.1)
		except SerialException as e:
			if self.verbose: print('Could not open port %s: %s' % (self.port, e))
			mppt_data[self.port] = port_error(self.port)
			self.ser = None

	def close(self):
		try:
			self.ser.close()
			self.ser = False
		except AttributeError:
			pass

	def parse(self, data):
		for c in data:
			if self.state == 0:
				if c == 0xaa:
					self.state      = 1
					self.data       = []
					self.target_len = 255
			elif self.state == 1:
				self.data.append(c)
				if len(self.data) == 5:
					self.target_len = 6 + self.data[4]
				if len(self.data) == self.target_len:
					self.state = 0
					if (0xaa + sum(self.data)) & 0xFF != 0:
						if self.verbose:
							print('esmart checksum error on %s — packet discarded' % self.port)
						continue
					if self.data[2] == 3:
						if self.data[3] == 0:
							ts = time()
							if self.verbose:
								msg, _ = _rec_msg(self.port, self.name, mppt_data[self.port])
								_rec_msgs.put(msg)
							_new = {}
							_new['CS']		= int.from_bytes(self.data[7:9],  byteorder='little')
							_new['VPV']		= int.from_bytes(self.data[9:11], byteorder='little') / 10.0
							_new['Vbat']	= int.from_bytes(self.data[11:13],byteorder='little') / 10.0
							_new['Ibat']	= int.from_bytes(self.data[13:15],byteorder='little') / 10.0
							_new['Vload']	= int.from_bytes(self.data[17:19],byteorder='little') / 10.0
							_new['Iload']	= int.from_bytes(self.data[19:21],byteorder='little') / 10.0
							_new['PPV']		= int.from_bytes(self.data[21:23],byteorder='little')
							_new['Pload']	= int.from_bytes(self.data[23:25],byteorder='little')
							_new['ext_temp']= self.data[25] if self.data[25] < 200 else self.data[25] - 256
							_new['int_temp']= self.data[27] if self.data[27] < 200 else self.data[27] - 256
							_new['ts']		= ts
							mppt_data[self.port] = _new

	def esmart_status_request(self):
		if self.ser is None: return
		try:
			while self.ser.inWaiting():
				self.parse(self.ser.read(100))
			if (time() - self.timeout) > 1:
				self.ser.write(b'\xaa\x01\x01\x01\x00\x03\x00\x00\x1e\x32')
				self.timeout = time()
		except IOError:
			if self.verbose: print('Serial port error, fixing', self.port)
			try: self.ser.close()
			except Exception: pass
			for attempt in range(1, 21):
				try:
					self.ser = Serial(self.port, 9600, timeout=0.1)
					if self.verbose: print('Error fixed after %i attempt(s)' % attempt)
					break
				except SerialException as e:
					if self.verbose: print('Attempt %i failed: %s' % (attempt, e))
					sleep(0.5)
			else:
				if self.verbose:
					print('Could not reopen port after 20 attempts: %s' % self.port)
				mppt_data[self.port] = port_error(self.port)


# ── Modbus RTU helper (self-contained, pyserial only) ─────────────────────────
# Minimal Modbus RTU master: build request, CRC16, parse response. Avoids a
# dependency on minimalmodbus/pymodbus so zeroinput stays pyserial-only, in line
# with the eSmart3 and VE.Direct drivers which also hand-roll their protocols.

def _modbus_crc16(frame):
	"""Modbus RTU CRC16 (low byte first). Returns 2-byte bytes object."""
	crc = 0xFFFF
	for b in frame:
		crc ^= b
		for _ in range(8):
			if crc & 1:
				crc = (crc >> 1) ^ 0xA001
			else:
				crc >>= 1
	return bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def _modbus_read(ser, unit, func, start, count, timeout=1.0):
	"""Issue a read (func 3=holding, 4=input) and return a list of `count`
	16-bit register values, or None on error/timeout/CRC mismatch."""
	req = bytes([unit, func, (start >> 8) & 0xFF, start & 0xFF,
	             (count >> 8) & 0xFF, count & 0xFF])
	req += _modbus_crc16(req)
	ser.reset_input_buffer()
	ser.write(req)
	ser.flush()
	expected = 5 + 2 * count			# unit+func+bytecount + data + crc
	deadline = time() + timeout
	buf = b''
	while time() < deadline and len(buf) < expected:
		chunk = ser.read(expected - len(buf))
		if not chunk:
			continue
		buf += chunk
	if len(buf) < expected:
		return None
	if buf[0] != unit or buf[1] != func:
		return None					# wrong slave or modbus exception (func|0x80)
	bytecount = buf[2]
	if bytecount != 2 * count:
		return None
	if _modbus_crc16(buf[:3 + bytecount]) != buf[3 + bytecount:5 + bytecount]:
		return None
	regs = []
	for i in range(count):
		hi = buf[3 + 2 * i]
		lo = buf[4 + 2 * i]
		regs.append((hi << 8) | lo)
	return regs


def _s16(v):
	"""Interpret a 16-bit register as signed."""
	return v - 0x10000 if v & 0x8000 else v


# ── Modbus MPPT charger base + per-model drivers ──────────────────────────────
# These controllers are polled synchronously (like eSmart3), not threaded.
# Each subclass defines baudrate, modbus function, and a read() that fills
# mppt_data[self.port] with the zeroinput field names (PPV, VPV, Vbat, Ibat,
# Pload, int_temp, ext_temp, CS, ts).

class ModbusCharger:
	"""Base for RS485/RS232 Modbus RTU MPPT chargers."""
	BAUDRATE = 9600
	BYTESIZE = 8
	PARITY   = 'N'
	STOPBITS = 1

	def __init__(self, port, unit=1, verbose=False):
		self.port    = port
		self.unit    = unit
		self.verbose = verbose
		self.ser     = None

	def open(self):
		try:
			self.ser = Serial(self.port, self.BAUDRATE, bytesize=self.BYTESIZE,
			                  parity=self.PARITY, stopbits=self.STOPBITS, timeout=0.3)
		except SerialException as e:
			if self.verbose: print('Could not open %s: %s' % (self.port, e))
			mppt_data[self.port] = port_error(self.port)
			self.ser = None

	def close(self):
		try:
			self.ser.close()
		except Exception:
			pass
		self.ser = None

	def read(self):
		"""Override: read registers and update mppt_data[self.port]."""
		raise NotImplementedError


class EpeverCharger(ModbusCharger):
	"""EPever Tracer-AN / Tracer-BN (and LS-B). Modbus RTU, input registers
	(func 4) from 0x3100, all values x100. 115200 8N1."""
	BAUDRATE = 115200

	def read(self):
		if self.ser is None:
			self.open()
			if self.ser is None: return
		try:
			# 0x3100..0x3107: PV V/I/P(L/H), Batt V/I/P(L/H)
			r = _modbus_read(self.ser, self.unit, 4, 0x3100, 8)
			# 0x3110..0x3111: battery temp, internal temp
			t = _modbus_read(self.ser, self.unit, 4, 0x3110, 2)
			# 0x311A battery SOC, 0x3201 charge status bitfield
			soc = _modbus_read(self.ser, self.unit, 4, 0x311A, 1)
			st  = _modbus_read(self.ser, self.unit, 4, 0x3201, 1)
			if r is None:
				mppt_data[self.port] = port_error(self.port)
				return
			d = {
				'VPV':  r[0] / 100.0,
				'PPV':  (r[2] | (r[3] << 16)) / 100.0,
				'Vbat': r[4] / 100.0,
				'Ibat': r[5] / 100.0,
				'ts':   time(),
			}
			if t:
				d['ext_temp'] = _s16(t[0]) / 100.0	# battery temp sensor
				d['int_temp'] = _s16(t[1]) / 100.0	# inside case
			if soc: d['SOC'] = soc[0]
			if st is not None:
				# D3-2: charging status 00 none,01 float,02 boost,03 equalize
				cs_map = {0: 0, 1: 7, 2: 5, 3: 8}	# map to eSmart-ish: float/bulk/equalize
				d['CS'] = cs_map.get((st[0] >> 2) & 0x03, 0)
			mppt_data[self.port] = d
		except Exception as e:
			if self.verbose: print('EPever read error on %s: %s' % (self.port, e))
			mppt_data[self.port] = port_error(self.port)


class RenogyCharger(ModbusCharger):
	"""Renogy Rover / Rover Elite / Adventurer / Wanderer. Modbus RTU,
	holding registers (func 3) 0x0100..0x0109. 9600 8N1."""
	BAUDRATE = 9600

	def read(self):
		if self.ser is None:
			self.open()
			if self.ser is None: return
		try:
			# 0x0100 SOC, 0x0101 Vbat(/10), 0x0102 Ibat(/100), 0x0103 temps,
			# 0x0104 loadV, 0x0105 loadI, 0x0106 loadP, 0x0107 PV V, 0x0108 PV I, 0x0109 PV P
			r = _modbus_read(self.ser, self.unit, 3, 0x0100, 10)
			if r is None:
				mppt_data[self.port] = port_error(self.port)
				return
			temp_reg  = r[3]
			ctrl_raw  = temp_reg >> 8
			batt_raw  = temp_reg & 0xFF
			# Renogy temps: bit7 is a sign flag, low 7 bits are the magnitude
			ctrl_temp = (-(ctrl_raw & 0x7F)) if (ctrl_raw & 0x80) else ctrl_raw
			batt_temp = (-(batt_raw & 0x7F)) if (batt_raw & 0x80) else batt_raw
			d = {
				'SOC':   r[0],
				'Vbat':  r[1] / 10.0,
				'Ibat':  r[2] / 100.0,
				'Pload': r[6],
				'VPV':   r[7] / 10.0,
				'PPV':   float(r[9]),
				'ext_temp': batt_temp,
				'int_temp': ctrl_temp,
				'ts':    time(),
			}
			mppt_data[self.port] = d
		except Exception as e:
			if self.verbose: print('Renogy read error on %s: %s' % (self.port, e))
			mppt_data[self.port] = port_error(self.port)


class MorningstarCharger(ModbusCharger):
	"""Morningstar TriStar MPPT 45/60. Modbus RTU, RAM registers (func 4 or 3),
	fixed-point scaling via V_PU (0x0000/1) and I_PU (0x0002/3). 9600 8N1/2.
	NOTE: EIA-485 is available only on TS-MPPT-60/M; the TS-MPPT-45 has RS-232
	only and cannot share an RS485 bus."""
	BAUDRATE = 9600

	def __init__(self, port, unit=1, verbose=False):
		super().__init__(port, unit, verbose)
		self._v_pu = None
		self._i_pu = None

	def _read_scaling(self):
		s = _modbus_read(self.ser, self.unit, 4, 0x0000, 4)
		if s is None:
			return False
		self._v_pu = s[0] + s[1] / 65536.0
		self._i_pu = s[2] + s[3] / 65536.0
		return True

	def read(self):
		if self.ser is None:
			self.open()
			if self.ser is None: return
		try:
			if self._v_pu is None:
				if not self._read_scaling():
					mppt_data[self.port] = port_error(self.port)
					return
			# 0x0018 Vb, 0x001B Va, 0x001C Ib, 0x001D Ia (read 0x0018..0x001D = 6 regs)
			r = _modbus_read(self.ser, self.unit, 4, 0x0018, 6)
			# 0x0023 T_hs, 0x0024 T_rts, 0x0025 T_batt
			t = _modbus_read(self.ser, self.unit, 4, 0x0023, 3)
			# 0x0032 charge_state, 0x003A power_out, 0x003B power_in
			p = _modbus_read(self.ser, self.unit, 4, 0x003A, 2)
			cs = _modbus_read(self.ser, self.unit, 4, 0x0032, 1)
			if r is None:
				mppt_data[self.port] = port_error(self.port)
				return
			vscale = self._v_pu / 32768.0
			iscale = self._i_pu / 32768.0
			pscale = self._v_pu * self._i_pu / 131072.0
			d = {
				'Vbat': _s16(r[0]) * vscale,	# 0x0018 adc_vb_f
				'VPV':  _s16(r[3]) * vscale,	# 0x001B adc_va_f
				'Ibat': _s16(r[4]) * iscale,	# 0x001C adc_ib_f
				'ts':   time(),
			}
			if p: d['PPV'] = p[1] * pscale		# 0x003B power_in
			if t:
				d['int_temp'] = _s16(t[0])		# heatsink
				d['ext_temp'] = _s16(t[2])		# battery
			if cs is not None:
				# Morningstar charge_state: 5 MPPT,6 absorption,7 float,8 equalize
				ms_map = {2: 0, 3: 0, 4: 2, 5: 1, 6: 4, 7: 7, 8: 8}
				d['CS'] = ms_map.get(cs[0], 0)
			mppt_data[self.port] = d
		except Exception as e:
			if self.verbose: print('Morningstar read error on %s: %s' % (self.port, e))
			mppt_data[self.port] = port_error(self.port)


# registry: mppt_type -> (driver class, default unit id)
MODBUS_DRIVERS = {
	'epever':      EpeverCharger,
	'renogy':      RenogyCharger,
	'morningstar': MorningstarCharger,
}


# ── Victron helper functions ───────────────────────────────────────────────────

def _map_victron_fields(src):
	"""Map raw VE.Direct field names to zeroinput mppt_data field names."""
	mapped = {}
	for f, v in src.items():
		if   f == 'V':  mapped['Vbat'] = v
		elif f == 'I':  mapped['Ibat'] = v
		elif f == 'CS': mapped['CS']   = int(v) if str(v).lstrip('-').isdigit() else v
		elif f == 'ts': mapped['ts']   = v
		else:           mapped[f]      = v
	if 'IL' in mapped and 'Vbat' in mapped:
		mapped['Pload'] = int(mapped['IL'] * mapped['Vbat'])
	return mapped


_REC_PORT_W = 24		# port column width (port + SER#: /dev/ttyUSB0 HQ2529AVWNQ = 24)
_REC_NAME_W = 14		# device name column width


def _rec_msg(port_label, dev_name, old, extra=''):
	"""Build a uniformly-columned REC message.
	  REC  <port_label:22>  <dev_name:14>  [delay]  [extra]
	port_label carries the SER# for both direct VE and AGG devices:
	  direct:  '/dev/ttyACM2 HQ2529K4Q'
	  agg:     '/dev/ttyUSB0 HQ2529K4Q'
	Returns (message_string, now_timestamp)."""
	now   = time()
	delay = ('delay %.2fs' % (now - old['ts'])) if 'ts' in old else ''
	parts = [delay, extra]
	tail  = '  '.join(p for p in parts if p)
	return ('REC  %-*s  %-*s  %s' % (
		_REC_PORT_W, port_label[:_REC_PORT_W],
		_REC_NAME_W, dev_name[:_REC_NAME_W],
		tail)).rstrip(), now

# ── VEDirectBridge ─────────────────────────────────────────────────────────────

class VEDirectBridge:
	"""Wraps ve_aggregator.VEDirect; writes parsed blocks into mppt_data."""

	def __init__(self, physical_port, conf, verbose=False):
		try:
			from ve_aggregator import VEDirect as _VEDirect
		except ImportError:
			raise ImportError('ve_aggregator.py not found — required for victron_agg')
		self._physical   = physical_port
		self._conf       = conf
		self._verbose    = verbose
		self._ser_to_key = {d['_ser']: k for k, d in conf['chargers'].items()
		                    if d.get('_agg_port') == physical_port}
		# AGG name from conf, shared by every device expanded from this port
		self._agg_name   = next((d['_port_name'] for d in conf['chargers'].values()
		                          if d.get('_agg_port') == physical_port), physical_port)
		self._vd         = _VEDirect(physical_port, on_block=self._on_block, on_alive=self._on_alive)
		if verbose: print('VEDirectBridge: started on %s  devices: %s' % (
			physical_port, list(self._ser_to_key.keys())))

	def _on_block(self, ser, block):
		key = self._ser_to_key.get(ser)
		if not key: return
		dev      = self._conf['chargers'][key]
		old      = mppt_data.get(key, {})
		port_lbl  = '%s %s' % (self._physical, ser)	# physical port + SER#
		if dev.get('mppt_type') == 'temp_sensor':
			temp_raw = block.get('TEMP')
			if temp_raw is not None:
				try:
					temp = float(temp_raw)
					_, now = _rec_msg(port_lbl, dev['name'], old)
					mppt_data[key] = {'ext_temp': temp, 'ts': now}
					if self._verbose:
						msg, _ = _rec_msg(port_lbl, dev['name'], old, '%.1f°C' % temp)
						_rec_msgs.put(msg)
				except ValueError: pass
			return
		mapped       = _map_victron_fields(block)
		msg, now     = _rec_msg(port_lbl, dev['name'], old)
		mapped['ts'] = now
		if self._verbose: _rec_msgs.put(msg)
		mppt_data[key] = mapped

	def _on_alive(self):
		"""Called by ve_aggregator on each ALIVE keepalive from the AGG MCU
		(sent roughly every 10s by the firmware) — confirms the MCU itself is
		reachable, independent of any individual MPPT device."""
		if self._verbose:
			msg, _ = _rec_msg(self._physical, 'ALIVE', {}, self._agg_name)
			_rec_msgs.put(msg)

	def start(self):  self._vd.start(); return self
	def stop(self):   self._vd.stop()

	def check_stale(self):
		active = set(self._vd.get_all().keys())
		for ser, key in self._ser_to_key.items():
			if ser not in active and key in mppt_data:
				if mppt_data[key].get('CS') != 'PORT':
					mppt_data[key] = port_error(key)

	def set_watts(self, ser, watts):
		self._vd.set_watts(ser, watts)


# ── VE.Direct HEX SET (conventional direct ports) ─────────────────────────────

_HEX_ADDR      = 0x2015
_HEX_TIMEOUT   = 0.4
_VBAT_FALLBACK = 24.0


def _hex_build_set(val_x10):
	lo = _HEX_ADDR & 0xFF;  hi = (_HEX_ADDR >> 8) & 0xFF
	vlo = val_x10  & 0xFF;  vhi = (val_x10  >> 8) & 0xFF
	cs = (0x55 - 0x80 - lo - hi - 0x00 - vlo - vhi) & 0xFF
	return (':8%02X%02X00%02X%02X%02X\n' % (lo, hi, vlo, vhi, cs)).encode()


def _hex_build_get():
	lo = _HEX_ADDR & 0xFF;  hi = (_HEX_ADDR >> 8) & 0xFF
	cs = (0x55 - 0x70 - lo - hi - 0x00) & 0xFF
	return (':7%02X%02X00%02X\n' % (lo, hi, cs)).encode()


def _hex_read_line(ser_obj, timeout):
	deadline = time() + max(0, timeout)
	buf = b''
	while time() < deadline:
		ser_obj.timeout = min(0.05, max(0.001, deadline - time()))
		c = ser_obj.read(1)
		if not c: continue
		buf += c
		if c == b'\n':
			return buf.decode('ascii', errors='replace').strip()
	return ''


def _hex_parse_get_reply(line):
	if not line.startswith(':6') or len(line) < 14:
		return None
	try:
		val_lo = int(line[8:10],  16)
		val_hi = int(line[10:12], 16)
		return val_lo | (val_hi << 8)
	except Exception:
		return None


def _hex_exec_set(ser_obj, device_key, watts, verbose=False):
	vbat    = mppt_data.get(device_key, {}).get('Vbat') or _VBAT_FALLBACK
	reg_val = round(watts / vbat * 10)
	ser_obj.write(_hex_build_set(reg_val)); ser_obj.flush()
	deadline = time() + _HEX_TIMEOUT
	ack_ok = False
	while time() < deadline:
		line = _hex_read_line(ser_obj, deadline - time())
		if line.startswith(':'):
			ack_ok = True; break
	if not ack_ok:
		if verbose: print('victron HEX set: ACK timeout for %s' % device_key)
		return
	ser_obj.write(_hex_build_get()); ser_obj.flush()
	deadline = time() + _HEX_TIMEOUT
	rb_val = None
	while time() < deadline:
		line = _hex_read_line(ser_obj, deadline - time())
		rb_val = _hex_parse_get_reply(line)
		if rb_val is not None: break
	if rb_val is None:
		if verbose: print('victron HEX set: verify timeout for %s' % device_key)
	elif rb_val != reg_val:
		if verbose: print('victron HEX set: verify mismatch set=%i rb=%i for %s' % (reg_val, rb_val, device_key))
	else:
		if verbose: print('victron HEX set: OK %s %iW %.1fA' % (device_key, watts, reg_val / 10.0))


def handle_victron_data(serialport, conf, stop_event, verbose=False):
	"""Reader thread for conventional (non-AGG) Victron VE.Direct ports."""
	rec_buf = {}
	try:
		ser = Serial(port=serialport, baudrate=19200, bytesize=8, parity='N',
		             stopbits=1, timeout=2, xonxoff=0, rtscts=0)
		ser.reset_input_buffer()
		while True:
			if stop_event.is_set():
				ser.close(); return 0
			data = b''; char = ''
			while char != b'\n':
				char = ser.read()
				data += char
			if data:
				snv = str(data)[2:].split('\\t')
				if len(snv) == 2: name, val = snv
				else: continue
				val = val[:-5]
				if name == 'V': name = 'Vbat'
				if name == 'I': name = 'Ibat'
				if name == 'PID':
					ts  = time()
					old = mppt_data.get(serialport, {})
					mppt_data[serialport] = rec_buf
					if verbose:
						ser_num = old.get('SER#', '')
						label   = ('%s %s' % (serialport, ser_num)) if ser_num else serialport
						msg, _ = _rec_msg(label, conf['chargers'][serialport]['name'], old)
						_rec_msgs.put(msg)
					rec_buf = {'ts': ts}
					if 'PPV' in old: rec_buf['PPV'] = old['PPV']
				if   name in ['PID', 'SER#', 'OR', 'LOAD', 'Checksum']:
					rec_buf[name] = val
				elif name in ['Vbat', 'Ibat', 'VPV', 'IL'] and val.isnumeric():
					rec_buf[name] = 0.001 * int(val)
				elif val.isnumeric():
					rec_buf[name] = int(val)
				if name == 'Checksum' and 'IL' in rec_buf and 'Vbat' in rec_buf:
					rec_buf['Pload'] = int(rec_buf['IL'] * rec_buf['Vbat'])
				if name == 'Checksum' and serialport in _victron_cmd_queues:
					try:
						watts = _victron_cmd_queues[serialport].get_nowait()
						_hex_exec_set(ser, serialport, watts, verbose)
					except Empty:
						pass
	except Exception as e:
		# A single unreadable port (missing device, unplugged USB, etc.) must not
		# take down the whole system. Mark just this device as a PORT error and end
		# this thread; the rest keep running. (Do NOT set stop_event — it is shared
		# by every reader thread and the webconfig server.)
		print(e); print_exc()
		mppt_data[serialport] = port_error(serialport)
		try: ser.close()
		except Exception: pass		# ser may not exist if Serial() failed to open
		return 1
