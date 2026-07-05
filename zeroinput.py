#!/usr/bin/python3
# -*- coding: utf-8 -*-
# zeroinput v2.2
# indent size 4, mode Tabs

import input_power_staging as staging
from inverter_drivers import build_inverters
import charger_drivers as cd

# expose mppt_data as a local alias for the many read sites below
mppt_data = cd.mppt_data
from json import load as json_load
from time import strftime, time, sleep
from datetime import timedelta, datetime
from threading import Thread, Event
from traceback import print_exc
from os.path import abspath, join, dirname, getmtime, exists
from os import system
import sys

if '-h' in sys.argv or '--help' in sys.argv:
	print(' -v\t\tverbose mode with console output\n','-web\t\toutput to html file\n','-no-input\tdisable power input\n','-test-alarm\texecute alarm command and stop\n','-httpd\t\tstart webconfig HTTP server (requires webconfig_port in conf)')
	exit(0)

try:
	with open(join(dirname(__file__),'zeroinput.conf'),'r') as fi: conf = json_load(fi)		# read configuration from file
except Exception as e:
	print(e)
	print_exc()
	print('error reading config file')
	exit(1)

inverter_drivers = []		# list of InverterDriver instances, built at startup from conf['inverters']

# chargers and inverters are configured in separate conf blocks.
# charger discovery, device lists and mppt_data are owned by charger_drivers.

conf = cd.expand_victron_agg(conf)

# check unique device names across chargers
# charger device lists are built at startup in __main__ via cd.build_chargers().
# esmart3_devs, victron_devs, temp_sensor_devs, esmart_handles and modbus_handles
# are populated there and used throughout this file.
esmart3_devs     = []
victron_devs     = []
temp_sensor_devs = []
esmart_handles   = []
modbus_handles   = []

if '-test-alarm' in sys.argv:
	print('test alarm command:')
	_alarms = conf.get('alarms', {})
	_first  = next((a for a in _alarms.values() if a.get('int_hi_cmd') or a.get('ext_hi_cmd')), {})
	_cmd    = _first.get('int_hi_cmd') or _first.get('ext_hi_cmd')
	if _cmd: system(_cmd)
	else:    print('no alarm command configured')
	exit(0)

no_input	= True if '-no-input' in sys.argv else False
start_httpd	= True if '-httpd'    in sys.argv else False
web_stats = True if '-web' in sys.argv else False
verbose = False

if '-v' in sys.argv:
	verbose = True
	con_stats = True
	print('start', sys.argv)
else: con_stats = False

if web_stats:
	verbose = True
	from io import StringIO as io_StringIO
	output_buffer = io_StringIO()
	sys.stdout = output_buffer		# comment here for DEBUGGING


# ── charger functions delegated to charger_drivers module ─────────────────────
# Thin wrappers over the charger_drivers API that supply conf and verbose.

def _drain_rec_msgs():
	cd.drain_rec_msgs()

def display_mppt_data():
	cd.display_mppt_data(conf, verbose)

def combine_charger_data():
	cd.combine_charger_data(conf, verbose)
	# mppt_data is already a reference to cd.mppt_data

def close_values(a,b,tol):			# check if values a and b are within tolerance
	if a > b * (1 - 0.01*tol) and a < b *(1 + 0.01*tol): return(1)
	return(0)


def avg(inlist):					# return the average of a list variable
	if len(inlist) == 0: return(0)
	return( sum(inlist) / len(inlist) )


def display_stats(in_pc, timer):
	"""Console / web output at start of each cycle."""
	if not (con_stats or web_stats):
		return
	if con_stats: system('clear')
	if web_stats:
		with open(join(dirname(__file__),'zeroinput.html'),'w') as webfile:
			webfile.write("""<!DOCTYPE html><html><head><meta charset="UTF-8"><meta http-equiv="refresh" content="1" ><style>body {font-size: 200%;color: #BBBBBB;background-color: #111111;}</style></head><body><pre>\n""")
			webfile.write(output_buffer.getvalue())
			webfile.write("""\n</pre></body></html>""")
		if con_stats:
			output_buffer.seek(0)
			print(output_buffer.getvalue(), file=sys.__stdout__)
		output_buffer.seek(0)
		output_buffer.truncate(0)
	display_mppt_data()
	if timer:
		if timer.active:	print('\ntimer active: bat discharge %i'%timer.battery,'W,' if timer.battery > 100 else '%,','energy %.0f/%i Wh,'%(in_pc/3600,timer.energy),'inverter %i'%timer.inverter,'W' if timer.inverter > 100 else '%','\n')
		else:				print('\ntimer.txt enabled but not active! no valid timer file set?\n')


def read_meter(vz_in):
	"""Read Ls_read and Ls_ts from vzlogger fifo. Returns (Ls_read, Ls_ts)."""
	Ls_read = 99999; Ls_ts = 99999
	main_log = False
	while True:
		l = vz_in.readline()
		if '[main] vzlogger' in l:
			main_log = True
			vzout = open(conf['persistent_vz_file'],'a')
			vzout.write('REDIRECTED by zeroinput.py from'+ conf['vzlogger_log_file'] +'\n')
			if verbose: print('\nvzlogger restart')
		elif 'Startup done.' in l:
			main_log = False
			vzout.close()
		if main_log: vzout.write(l)
		if '1-0:16.7.0' in l and 'value=' in l and 'ts=' in l:
			try:
				Ls_read = int(round(float(l.split('value=')[1].split()[0])))
				Ls_ts	= int(l.split('ts=')[1].split()[0].rstrip('\n'))
			except (ValueError, IndexError):
				pass
		if Ls_read != 99999 and Ls_ts != 99999:
			if abs( int(str(time())[:10]) - int(str(Ls_ts)[:10]) ) > 1: continue
			break
		sleep(0.001)
	return Ls_read, Ls_ts


def write_vz_log(power_demand, zero_shift, bat_voltage, mppt_data, conf):
	"""Write current values to volkszaehler log file.
	Channel mapping is read from conf['vz_channels']."""
	def device(name):
		for port, dev in conf.get('chargers', {}).items():
			if dev.get('name') == name and port in mppt_data:
				return mppt_data[port]
		return {}

	direct = {'power_demand': power_demand, 'zero_shift': zero_shift, 'bat_voltage': bat_voltage}

	with open('/tmp/vz/output_to_vz.log', 'w') as fo:
		ts = time()
		for ch in conf.get('vz_channels', []):
			dev, key, vz_ch, factor = ch
			if dev is None:
				if key in direct:
					fo.write('%i: %s = %g\n' % ( ts, vz_ch, direct[key] * factor ))
			elif dev == 'combined':
				if key in mppt_data.get('combined', {}):
					fo.write('%i: %s = %i\n' % ( ts, vz_ch, mppt_data['combined'][key] * factor ))
			else:
				d = device(dev)
				if key in d:
					val = d[key] * factor
					fo.write('%i: %s = %s\n' % ( ts, vz_ch, ('%g' % val) ))



def write_vzlogger_conf_example(conf):
	"""Write a vzlogger.conf example based on current chargers and vz_channels config."""
	vz_ch_list = conf.get('vz_channels', [])
	path = join(dirname(abspath(__file__)), 'vzlogger.conf.example')
	with open(path, 'w') as f:
		f.write('{\n')
		f.write('\t"verbosity": 15,\n')
		f.write('\t"log": "%s",\n' % conf.get('vzlogger_log_file', '/tmp/vz/vzlogger.fifo'))
		f.write('\t"retry": 0,\n')
		f.write('\t"daemon": true,\n')
		f.write('\t"local": { "enabled": false },\n')
		f.write('\t"meters": [\n')
		f.write('\t\t{\n')
		f.write('\t\t\t"enabled": true,\n')
		f.write('\t\t\t"protocol": "sml",\n')
		f.write('\t\t\t"device": "/dev/lesekopf",\n')
		f.write('\t\t\t"channels": [\n')
		f.write('\t\t\t\t{\n')
		f.write('\t\t\t\t\t"uuid": "-- create channel in volkszaehler and insert uuid here --",\n')
		f.write('\t\t\t\t\t"identifier": "1-0:16.7.0*255",\n')
		f.write('\t\t\t\t\t"api": "volkszaehler",\n')
		f.write('\t\t\t\t\t"middleware": "http://localhost/middleware.php"\n')
		f.write('\t\t\t\t}\n')
		f.write('\t\t\t]\n')
		f.write('\t\t},\n')
		f.write('\t\t{\n')
		f.write('\t\t\t"enabled": true,\n')
		f.write('\t\t\t"protocol": "file",\n')
		f.write('\t\t\t"path": "/tmp/vz/output_to_vz.log",\n')
		f.write('\t\t\t"channels": [\n')
		for i, ch in enumerate(vz_ch_list):
			dev, key, vz_ch, factor = ch
			comma = ',' if i < len(vz_ch_list) - 1 else ''
			f.write('\t\t\t\t{\n')
			f.write('\t\t\t\t\t"uuid": "-- create channel '+ vz_ch +' in volkszaehler and insert uuid here --",\n')
			f.write('\t\t\t\t\t"identifier": "%s",\n' % vz_ch)
			f.write('\t\t\t\t\t"api": "volkszaehler",\n')
			f.write('\t\t\t\t\t"middleware": "http://localhost/middleware.php"\n')
			f.write('\t\t\t\t}%s\n' % comma)
		f.write('\t\t\t]\n')
		f.write('\t\t}\n')
		f.write('\t]\n')
		f.write('}\n')
	print('vzlogger.conf.example written to %s' % path)


RUNTIME_NO_RELOAD = {
	'chargers', 'inverters', 'vzlogger_log_file',
	'persistent_vz_file', 'webconfig_port',
	# chargers/inverters are structural: drivers and reader threads are built
	# once at startup, so changes require a restart (use /api/restart).
}

HEAT_FAIL_FRACTION = 0.5	# heat-protect cap fraction of max_input_power when the selected sensor has no reading
STAGE_HOLD_CYCLES = 5		# hold power_demand this many cycles after a stage 2->1 transition: the inverters need ~4-5 s to settle after the hard switch, and reacting to that transient would make the control loop fight its own transition

# battery voltage thresholds, expressed PER CELL so they scale with conf['cell_count'].
# The original code was hard-wired to 16S LiFePO4 (51.2 V nominal); the 16S value is
# noted next to each constant. cell_voltage(x) below multiplies by the configured cell
# count, so at cell_count == 16 every threshold equals the original 16S figure exactly.
VC_DERATE_LO   = 48.0  / 16		# 16S original: 48.0 V  (discharge derating lower bound, 0%)
VC_DERATE_HI   = 51.0  / 16		# 16S original: 51.0 V  (discharge derating upper bound, 100%)
VC_DERATE_OFF  = 46.93 / 16		# 16S original: 46.93 V (derating power-curve offset)
VC_RAMP_OK     = 51.0  / 16		# 16S original: 51.0 V  (ramp: enough voltage for an up-ramp)
VC_EXPORT      = 54.5  / 16		# 16S original: 54.5 V  (free-power-export threshold)
VC_SATURATION  = 57.0  / 16		# 16S original: 57.0 V  (MPPT saturation / full-charge voltage)
VC_INV_BASELOAD = 49.0 / 16		# 16S original: 49.0 V  (below this, subtract inverter base load by voltage)
DERATE_EXP     = 3.281			# power-curve exponent (acts on total pack voltage, unchanged)

def cell_voltage(per_cell):
	"""Scale a per-cell threshold to the configured pack size. cell_count defaults
	to 16, so an unconfigured (16S) system behaves exactly as the original code."""
	return per_cell * conf.get('cell_count', 16)

# stage-transition demand hold (owned by send_to_inverters)
_hold_cnt          = 0		# remaining hold cycles (0 = no hold in progress)
_hold_power_demand = 0		# power_demand frozen at the 2->1 transition
_last_stage        = 1		# stage used on the previous cycle


def reload_conf_if_changed(conf, conf_path, conf_mtime, predictor):
	"""Check if conf file changed, reload whitelisted keys and log_to_vz module.
	Returns (conf, conf_mtime)."""
	try:
		new_mtime = getmtime(conf_path)
		if new_mtime == conf_mtime:
			return conf, conf_mtime
	except Exception as e:
		print('config mtime check failed: %s' % e)
		return conf, conf_mtime

	# conf reload
	try:
		with open(conf_path, 'r') as fi:
			new_conf = json_load(fi)
		changed = []
		for key in new_conf:
			if key not in RUNTIME_NO_RELOAD and new_conf[key] != conf.get(key):
				conf[key] = new_conf[key]
				changed.append('%s=%s' % (key, new_conf[key]))
		predictor.reload_conf({'load_prediction': conf.get('load_prediction', True), 'min_spread_w': conf.get('min_spread_w', 150)})
		# hot-reload predictor log on/off
		new_log_path = PREDICTOR_LOG_FILE if conf.get('predictor_log', True) else ''
		if hasattr(predictor, '_log_path') and predictor._log_path != new_log_path:
			if predictor._log_fh: predictor._log_fh.close(); predictor._log_fh = None
			predictor._log_path = new_log_path
			predictor._log_open()
			print('predictor log: %s' % (new_log_path or 'disabled'))
		if changed:
			print('config reloaded: %s' % ', '.join(changed))
			if any('vz_channels' in c for c in changed):
				write_vzlogger_conf_example(conf)	# rewrite example if channels changed
		else:
			if verbose: print('config reloaded: no changes')
		conf_mtime = new_mtime
	except Exception as e:
		print('config reload failed: %s' % e)
		print_exc()

	return conf, conf_mtime


def reload_predictor_if_changed(predictor, conf, predictor_mtime, verbose):
	"""Hotplug predictor.py if file changed. Returns (predictor, predictor_mtime)."""
	_pred_path = join(dirname(abspath(__file__)), 'predictor.py')
	if not exists(_pred_path):
		return predictor, predictor_mtime
	try:
		new_mtime = getmtime(_pred_path)
		if new_mtime == predictor_mtime:
			return predictor, predictor_mtime
		import importlib
		import predictor as _pred_mod
		importlib.reload(_pred_mod)
		predictor = _pred_mod.LoadPredictor({'load_prediction': conf.get('load_prediction', True), 'min_spread_w': conf.get('min_spread_w', 150)}, verbose)
		predictor_mtime = new_mtime
		print('predictor reloaded from file')
	except Exception as e:
		print('predictor reload failed: %s' % e)
	return predictor, predictor_mtime


def check_saw(power_demand, send_history, block_saw_detection):
	"""Detect and break saw-tooth oscillation. Returns (possibly corrected) power_demand."""
	if block_saw_detection:
		if verbose: print('disabled saw detection')
		return power_demand
	if not close_values(send_history[-1],send_history[-2],3) and not close_values(send_history[-3],send_history[-4],3):
		power_demand = int(avg(send_history))
		if verbose: print('saw stop', power_demand)
		send_history[-1] = power_demand
	else:
		if verbose: print('no saw detected')
	return power_demand


def heat_protect_cap(mppt_data, max_input_power):
	"""Heat protection: linear power cap driven by one selected temperature sensor.
	Below conf['heat_temp_low'] the full max_input_power is allowed; at/above
	conf['heat_temp_high'] the cap is zero (inverter off, so it does not keep
	running pointlessly while overheating); linear in between. The sensor is the
	charger whose config has heat_protect: true (exactly one valid) — any charger
	that carries a temperature (temp_sensor, eSmart3, Modbus, AGG sub-sensor) is
	eligible. The reading is ext_temp, falling back to int_temp. With none selected
	the protection is off (returns max_input_power). If the selected sensor has no
	fresh reading, the cap defaults to HEAT_FAIL_FRACTION * max_input_power as a
	safe fallback. Returns (cap_W, status_text or '')."""
	port = None
	for p, c in conf.get('chargers', {}).items():
		if c.get('heat_protect'):
			port = p
			break
	if port is None:
		return max_input_power, ''							# protection disabled (no sensor selected)

	t_lo = conf.get('heat_temp_low', 60)
	t_hi = conf.get('heat_temp_high', 80)

	pd = mppt_data.get(port, {})
	temp = pd.get('ext_temp', pd.get('int_temp'))		# ext preferred, int as fallback

	if temp is None:
		# No reading available. Charger aggregation already holds a failed sensor's
		# last temperature for a short while (see port_error in charger_drivers);
		# once that hold expires the value is gone and this safe fallback applies.
		cap = int(HEAT_FAIL_FRACTION * max_input_power)
		return cap, ', heat protect: no sensor reading, cap %i W' % cap

	if temp <= t_lo:
		return max_input_power, ''							# no derating
	if temp >= t_hi:
		return 0, ', heat protect %.1f°C: inverter off' % temp	# fully off when too hot
	frac = (temp - t_lo) / (t_hi - t_lo) if t_hi > t_lo else 1.0	# 0 at t_lo, 1 at t_hi
	cap = int(max_input_power * (1.0 - frac))			# linear down to 0 at t_hi
	return cap, ', heat protect %.1f°C: cap %i W' % (temp, cap)


def update_battery(bat_history, send_history):
	"""Update battery voltage history with optional correction. Returns (bat_history, bat_voltage)."""
	if conf['bat_voltage_const'] != 0:
		if mppt_data['combined']['Pload'] == 0:	battery_power = mppt_data['combined']['PPV'] - send_history[-1]
		else:									battery_power = mppt_data['combined']['PPV'] - mppt_data['combined']['Pload']
		bat_corr = round(0.001 * battery_power * conf['bat_voltage_const'], 1)
		bat_history = bat_history[1:] + [mppt_data['combined']['Vbat'] - bat_corr]
		if verbose and bat_corr: print('voltage correction',round(bat_history[-1],1),'V, dif',bat_corr,'V')
	else:
		bat_history = bat_history[1:] + [mppt_data['combined']['Vbat']]
	if 0 in bat_history:	bat_voltage = mppt_data['combined']['Vbat']
	else:					bat_voltage = avg(bat_history)
	return bat_history, bat_voltage


def update_zero_shift(zero_shift, long_meter_history, adjusted_power):
	"""Recalculate zero_shift from meter history. Returns new zero_shift."""
	if conf['zero_shifting'] == 0 and not adjusted_power:
		nz = sorted([x for x in long_meter_history if x != 0])
		if len(nz) >= 6:
			skip	= max(1, len(nz) // 10)
			use		= max(3, len(nz) // 4)
			target	= -abs(int(avg(nz[skip:skip+use])))
			return max(zero_shift - 5, min(zero_shift + 5, target))
	return conf['zero_shifting']


def send_to_inverters(power_demand, long_send_history, n_active_inverters, ramp_active=False):
	"""Distribute power_demand across the configured inverter groups and send.

	Two stages (see input_power_staging.py):
	  stage 1: the stage-1 group(s) alone carry the base load.
	  stage 2: every eligible group shares the load in equal per-unit parts;
	           smaller units saturate at their max_power and the surplus flows
	           to the still-open units, so the largest unit (e.g. the MultiPlus)
	           rises last.
	Each group is one driver instance that sends exactly one command per cycle
	(a Soyosource group broadcasts one packet to all its identical units; an
	MK3 group writes one ESS setpoint). Returns the number of active units.

	Stage 2->1 demand hold: the hard switch to the stage-1 allocation produces a
	brief output transient while the inverters settle (~4-5 s measured). During
	STAGE_HOLD_CYCLES cycles after the transition, the freshly computed
	power_demand is deliberately bypassed and the value frozen at the transition
	is distributed instead, so the control loop upstream does not fight its own
	transition (lowering the demand during, then ramping it back up after). A
	running ramp or a load rise that pulls active_stage back to 2 discards the
	hold at once, so a genuine load change is served immediately."""
	global _hold_cnt, _hold_power_demand, _last_stage
	if power_demand == 0:
		for drv in inverter_drivers:
			drv.sleep()
		if verbose:
			print('. power request')
			_drain_rec_msgs()
		_hold_cnt = 0
		_last_stage = 1
		return 0

	stage = staging.active_stage(long_send_history, conf['single_inverter_threshold'])

	if stage == 1 and _last_stage == 2 and not ramp_active:
		_hold_power_demand = power_demand
		_hold_cnt = STAGE_HOLD_CYCLES

	if ramp_active or stage != 1:
		_hold_cnt = 0

	if _hold_cnt > 0:
		_hold_cnt -= 1
		demand = _hold_power_demand
		if verbose: print('stage hold %i  demand held %iW' % (_hold_cnt, demand))
	else:
		demand = power_demand

	_last_stage = stage

	alloc = staging.distribute(demand, inverter_drivers, stage,
	                           conf['single_inverter_threshold'])

	for drv in inverter_drivers:
		w = alloc.get(drv.id, 0)
		if w > 0: drv.set_power(w)
		else:     drv.sleep()

	n_active_inverters = staging.count_active_units(alloc, inverter_drivers)
	if verbose:
		parts = ['%s=%iW' % (d.id, alloc.get(d.id, 0))
		         for d in inverter_drivers if alloc.get(d.id, 0) > 0]
		countdown = staging.cycles_until_stage1(long_send_history, conf['single_inverter_threshold'])
		cd_str = '' if countdown is None else ', %ic' % countdown
		print('power request stage %i  %s  (%i units%s)' % (
			stage, ' '.join(parts) if parts else 'none', n_active_inverters, cd_str))
		_drain_rec_msgs()
	return n_active_inverters


def poll_chargers(esmart_handles, timeout_repeat, pv_cont):
	cd.poll_chargers(esmart_handles, conf, timeout_repeat, pv_cont, verbose, modbus_handles)


def check_temp_alarms(alarm_last):
	return cd.check_temp_alarms(conf, alarm_last, esmart3_devs, temp_sensor_devs, verbose)


def print_status(Ls_read, power_demand, zero_shift, status_text, last_runtime, send_history):
	"""Print verbose status line."""
	if power_demand == 0:
		print('\nmeter {:4d} W'.format(Ls_read), end='')
	else:
		print('\nmeter {:4d} W ({}shift {} W '.format(Ls_read, 'auto ' if conf['zero_shifting'] == 0 else '', abs(zero_shift)), end='')
		if conf['zero_shifting'] <= 0:	print('import)', end='')
		else:							print('export)', end='')
	print(', interval %.2f s, %s' % (time()-last_runtime, strftime('%H:%M:%S')))
	print('inverter {:4d} W{}\n'.format(power_demand, status_text))


class discharge_times():			# handle timer.txt file
	def __init__(self):
		self.last_set_timer = False
		self.active		= False
		self.battery	= 100
		self.inverter	= 100
		self.energy		= conf['max_input_power']
		self.update()
	
	def update(self):
		times = []; states = []
		
		try:
			with open(conf['discharge_t_file'],'r') as fi:
				for i in fi:
					if i[0] == '#' or i == '\n': continue	# ignore empty lines
					if i[:10] == '0000-00-00': i = datetime.now().strftime('%Y-%m-%d') + i[10:] # set to today
					times.append(datetime.strptime(i[:19], '%Y-%m-%d %H:%M:%S'))
					states.append(str(i[19:]).replace('\n','').replace('\t',' ').split(' ')[1:])
			
			for i in range(0,len(times)):
				self.active = True	# successful file read
				
				if times[i] < datetime.now(): 
					if states[i][0] == '0':	self.battery = 0
					else:					self.battery = int(states[i][0])
					if states[i][1] == '0':	self.inverter = 0
					else:					self.inverter = int(states[i][1])
					if states[i][2] == '0':	self.energy = 0
					else:					self.energy = int(states[i][2])
				else: 
					break
		
		except:							# something went wrong, reading the timer file
			self.active		= False		# indicates a invalid timer file!
			self.battery	= 100
			self.inverter	= 100
			self.energy		= 9999
		
		try:
			if not times:
				return 0
			if self.last_set_timer != times[-1]:
				self.last_set_timer = times[-1]
				return 1			# indicates a energy counter reset
			else:
				return 0
		except:						# no problem with that
			return 0



def set_victron_power(device_key, watts):
	"""Delegate to charger_drivers — unified interface for AGG and conventional ports."""
	cd.set_victron_power(device_key, watts, conf)


if __name__ =="__main__":
	try:
		stop_event = Event(); victron_threads = []
		if start_httpd and conf.get('webconfig_port', 0):
			try:
				import webconfig
				Thread(target=webconfig.start, args=(conf['webconfig_port'], stop_event, web_stats), daemon=True).start()
			except ImportError:
				print('webconfig.py not found – httpd disabled')

		# build charger device lists, initialise mppt_data, warm up eSmart handles
		_cd_result = cd.build_chargers(conf, verbose)
		esmart3_devs, victron_devs, temp_sensor_devs, esmart_handles, modbus_handles = _cd_result

		# start Victron reader threads / VEDirectBridge instances
		victron_threads = cd.start_victron_threads(victron_devs, conf, stop_event, verbose)
		
		max_input_power	= conf['max_input_power']
		n_active_inverters = 0
		power_demand		= 0
		last_send		= 0
		last2_send		= 0
		last_Ls_read	= 0				# meter reading of the previous cycle (for the ramp trigger on change)
		ramp_cnt			= 0
		ramp_power			= 0
		ramp_direction		= 0				# +1 up-ramp, -1 down-ramp, 0 none
		free_power		= 0
		dropped_first_up_ramp	= False
		bat_voltage		= 0				# continous bat voltage
		pv_cont			= 0				# continous pv voltage
		in_pc			= 0				# input power counter
		adjusted_power	= False
		bat_history		= [0]* 5		# history vars with *n interval steps
		pv_history		= [0]* 20
		send_history	= [0]* 4
		long_send_history	= [0]* conf['multi_inverter_wait']
		long_meter_history	= [0]* 30
		zero_shift = conf['zero_shifting']
		last_runtime 		= time()
		alarm_last			= {}			# {port: {'int': datetime, 'ext': datetime}} for temperature alarms
		timeout_repeat		= datetime.now()
		vz_in				= open(conf['vzlogger_log_file'],'r')
		
		
		timer = None
		if conf['discharge_timer']: timer = discharge_times()										# set up timer
		try:
			from predictor import LoadPredictor, LOG_FILE as PREDICTOR_LOG_FILE
			predictor = LoadPredictor({'load_prediction': conf.get('load_prediction', True), 'min_spread_w': conf.get('min_spread_w', 150)}, False)	# load prediction init (verbose suppressed until log path is set)
			if predictor._log_fh: predictor._log_fh.close(); predictor._log_fh = None
			predictor._log_path = PREDICTOR_LOG_FILE if conf.get('predictor_log', True) else ''
			predictor.verbose = verbose					# set verbose before log open so header is gated correctly
			predictor._log_open()						# opens log and prints header+columns only if log enabled and verbose
		except ImportError:
			print('predictor.py not found – load prediction disabled')
			conf['load_prediction'] = False
			predictor = type('P', (), {'update': lambda *a,**k: 0, 'reload_conf': lambda *a,**k: None, 'status': lambda *a,**k: '', 'enabled': False, 'offset': 0, 'ramp_override_by_predictor': False})()
		conf_path	= join(dirname(__file__), 'zeroinput.conf')
		conf_mtime		= getmtime(conf_path)
		_pred_path		= join(dirname(__file__), 'predictor.py')
		predictor_mtime	= getmtime(_pred_path) if exists(_pred_path) else 0
		if verbose: print('zeroinput starts\n')
		write_vzlogger_conf_example(conf)												# write vzlogger.conf.example on startup
		
		# build inverter drivers from conf['inverters'] (one driver per group)
		inverter_drivers = build_inverters(conf.get('inverters', {}), verbose)
		_s1_ok, _s1_cap = staging.check_stage1_capacity(
			inverter_drivers, conf['single_inverter_threshold'])
		if not _s1_ok:
			print('WARNING: stage-1 inverter capacity %i W is below '
			      'single_inverter_threshold %i W — base load cannot be fully '
			      'served by stage 1 alone' % (_s1_cap, conf['single_inverter_threshold']))
		# coverage-gap check: power ranges no inverter combination can deliver
		_gaps = staging.find_coverage_gaps(
			inverter_drivers, conf['single_inverter_threshold'], conf['max_input_power'])
		if _gaps:
			bar = '!' * 72
			print('\n' + bar, file=sys.__stdout__)
			print('!! POWER COVERAGE GAP — the inverter configuration cannot deliver',
			      file=sys.__stdout__)
			print('!! the following power range(s). Regulation will jump across them:',
			      file=sys.__stdout__)
			for a, b in _gaps:
				print('!!     %i W  ...  %i W   (%i W uncovered)' % (a, b, b - a),
				      file=sys.__stdout__)
			print('!! Fix min_power / max_power / stage so the range is seamless.',
			      file=sys.__stdout__)
			print(bar + '\n', file=sys.__stdout__)
		if verbose:
			print('inverters: %s' % ', '.join(
				'%s(%s x%i stages%s %i-%iW)' % (d.id, d.__class__.__name__,
				 d.count, d.stages, d.min_power, d.max_power) for d in inverter_drivers))
		if verbose: print('reading power meter data\n')

		# wait for real battery voltage data before entering the main loop —
		# without this, the first cycle sees combined Vbat=0 (no charger data
		# received/polled yet), which is misread as a 0V under-voltage and
		# falsely triggers the 1-minute battery-protection timeout on every
		# restart. Works for all charger types: combine_charger_data() picks up
		# whatever build_chargers() already polled synchronously (eSmart3,
		# Modbus) and whatever the AGG/Victron reader threads have delivered
		# so far.
		_vbat_wait_start = time()
		_vbat_wait_timeout = 10		# seconds
		while True:
			combine_charger_data()
			if mppt_data['combined'].get('Vbat', 0) > 0:
				break
			if time() - _vbat_wait_start > _vbat_wait_timeout:
				print('WARNING: no battery voltage data after %ds, starting anyway' % _vbat_wait_timeout)
				break
			sleep(0.2)

		while True:																						# infinite loop, stop the script with ctl+c

			last2_send	= last_send		# dedicated history
			last_send	= power_demand	# variables
			block_saw_detection = False	# allow saw detection

			combine_charger_data()																		# update charger summary
			conf, conf_mtime = reload_conf_if_changed(conf, conf_path, conf_mtime, predictor)	# reload conf if changed
			predictor, predictor_mtime = reload_predictor_if_changed(predictor, conf, predictor_mtime, verbose)	# hotplug predictor
			if len(long_send_history) != conf['multi_inverter_wait']:						# resize if multi_inverter_wait changed
				n = conf['multi_inverter_wait']
				long_send_history = (long_send_history[-n:] if len(long_send_history) > n
									else [0] * (n - len(long_send_history)) + long_send_history)
			if conf['zero_shifting'] != 0: zero_shift = conf['zero_shifting']					# apply new zero_shift if not in auto mode
			max_input_power = conf['max_input_power']											# apply new max_input_power
			if conf['discharge_timer'] and timer is None: timer = discharge_times()				# init timer if newly enabled
			if not conf['discharge_timer']: timer = None										# disable timer if turned off
			display_stats(in_pc, timer if conf['discharge_timer'] else None)							# console / web output

			Ls_read, Ls_ts = read_meter(vz_in)															# read power meter
			_predictor_active = predictor.enabled and (predictor.offset != 0 or predictor.ramp_override_by_predictor)
			if _predictor_active and conf['zero_shifting'] == 0:
				zero_shift = 0
				if verbose: print('predictor active: zero_shift paused')
			else:
				zero_shift = update_zero_shift(zero_shift, long_meter_history, adjusted_power)	# update zero shift
			
			predictive_offset = predictor.update(Ls_read, last2_send)		# load prediction
			power_demand = int( Ls_read + last2_send + zero_shift + predictive_offset )	# calculate the power demand
			
			if predictor.enabled and predictor.ramp_override_by_predictor:
				ramp_cnt				= 0		# discard stale ramp state
				ramp_direction			= 0
				dropped_first_up_ramp	= False	# reset on override entry
			else:
				ls_change = Ls_read - last_Ls_read		# meter change since last cycle, not the absolute
				                                        # value: the system need not have been at zero before

				# abort an in-progress ramp if the meter now moves the opposite direction —
				# without this, e.g. a strong down-step during a running up-ramp would be
				# held back until the up-ramp countdown finishes, causing unnecessary export.
				if ramp_cnt > 0 and (
						(ls_change < -400 and ramp_direction > 0) or
						(ls_change >  400 and ramp_direction < 0)):
					ramp_cnt = 0
					if verbose: print('ABORTED ramp - opposite direction detected')

				# high change of power consumption, on rise: no active power limitation, sufficient bat_voltage
				if (ls_change < -400) or (ls_change > 400 and not adjusted_power and bat_voltage > cell_voltage(VC_RAMP_OK)):	# 16S original: 51.0 V
					if not dropped_first_up_ramp and ls_change > 400: 								# don't delay down ramps
						dropped_first_up_ramp = True
						if verbose: print('DROPPED first Ramp')
					else:
						if	ramp_cnt == 0:
							ramp_cnt = 2 + round(min(max_input_power, abs(ls_change)) / (400 * max(1, n_active_inverters)))		# counted in script cycles; 400 W/s ramp rate per unit, capped to max_input_power
							ramp_power = int(Ls_read + last2_send + zero_shift)						# without predictive_offset
							ramp_direction = 1 if ls_change > 0 else -1
				
				if ramp_cnt > 0:																			# within ramp countdown
					block_saw_detection = True																# disable saw detection
					power_demand = ramp_power
					if verbose: print('ramp mode %i'%ramp_cnt)
					ramp_cnt -= 1
					
					if ramp_cnt == 0:
						dropped_first_up_ramp = False
						ramp_direction = 0
			status_text = ''
			
			bat_history, bat_voltage = update_battery(bat_history, send_history)						# update battery voltage
			pv_history = pv_history[1:] + [mppt_data['combined']['PPV']]
			pv_cont = int(avg(sorted(pv_history)[-5:]))													# average on high pass of the PV power
			pv_power = 0
			
			if no_input:																				# disabled power input by command line option
				power_demand = 0
				if verbose: print('input DISABLED by command line')
			
			elif datetime.now() < timeout_repeat:														# battery protection timeout
				power_demand = 0
				if verbose: print('battery protection timeout until', timeout_repeat.strftime('%H:%M:%S'))
			
			else:
				adjusted_power = False
				
				if bat_voltage <= cell_voltage(VC_DERATE_LO) or (conf['discharge_timer'] and					# 16S original: 48 V; set a new battery timeout
					( (not timer.battery or (timer.battery and (in_pc/3600) > timer.energy) ) and (not timer.inverter) )):
					adjusted_power = True
					power_demand		= 0																	# disable input
					send_history	= [0]*4																# reset history
					timeout_repeat = datetime.now() + timedelta(minutes = 1)							# repeat in one minute
				
				else:
					_v_bl = cell_voltage(VC_INV_BASELOAD)												# 16S original: 49 V
					_v_bl_slope = 50 * 16 / conf.get('cell_count', 16)									# 16S original: 50 W/V; scaled so the per-cell derating slope is unchanged
					pv_bat_minus = 0 if bat_voltage > _v_bl else (_v_bl-bat_voltage)*_v_bl_slope * n_active_inverters	# reduction by battery voltage in relation to the base consumption of the inverter(s)
					avg_pv		= avg(pv_history[-3:])													# use a shorter span than pv_cont
					pv_eff		= avg_pv-(avg_pv * conf['PV_to_AC_efficiency'] * 0.01)					# efficiency gap
					pv_p_minus	= pv_bat_minus + pv_eff													# pv reduction
					pv_power	= max(0,int(avg_pv - pv_p_minus))										# remaining PV power
					bat_power_by_voltage = conf['max_bat_discharge']									# unlimited bat discharge so far
					
					if conf['discharge_timer'] and not timer.battery:									# disabled battery discharge, pass through pv power
						if	power_demand > pv_power:
							power_demand = pv_power
							adjusted_power = True
							if verbose and pv_cont:	status_text	+= ((' limited, PV -%i W' % round(pv_p_minus)) if pv_p_minus else ' ') + ', no battery discharge'
					
					elif bat_voltage >= cell_voltage(VC_DERATE_LO) and bat_voltage <= cell_voltage(VC_DERATE_HI):	# 16S original: 48-51 V, limit battery power, pass through pv power
						bat_power_percent_by_voltage	= (bat_voltage - cell_voltage(VC_DERATE_OFF)) **DERATE_EXP	# 16S original offset 46.93 V; powercurve over the derating band, results in 1-100%
						bat_power_by_voltage			= int(0.01 * max_input_power * bat_power_percent_by_voltage)	# 100% above the upper bound
						
						if verbose: status_text = ', Bat %i W (%.1f%%)'	% (bat_power_by_voltage, bat_power_percent_by_voltage) + ', PV %i W (-%i W)'% (pv_power, pv_p_minus) if pv_power  != 0 else ''
					
					if conf['free_power_export'] and bat_voltage >= cell_voltage(VC_EXPORT):						# 16S original: 54.5 V; give some free power to the world = "pull down the zero line" (not zero shift!)
						free_power = int( (1.0/(cell_voltage(VC_SATURATION)-cell_voltage(VC_EXPORT))) * (bat_voltage - cell_voltage(VC_EXPORT)) * conf['max_input_power'] )	# 16S original: 57 V saturation, 54.5 V export; full energy input at maximum bat voltage, depends on mppt chargers "saturation charging voltage"
						if free_power > 0:
							power_demand += free_power
							adjusted_power = True
							if verbose: status_text += ', free export by voltage %i W' % free_power
					else: free_power = 0
				
					if conf['discharge_timer']:															# active timer, battery limit
						if timer.battery == 0:		bat_discharge = 0 
						elif timer.battery <= 100:	bat_discharge = int(conf['max_bat_discharge'] *0.01 *timer.battery)	# <= 100 as percentage
						else:						bat_discharge = timer.battery										# > 100 as W
					else:							bat_discharge = conf['max_bat_discharge']							# bat discharge by configuration
					
					if bat_discharge > bat_power_by_voltage:											# battery timer limited to voltage power
													bat_discharge = bat_power_by_voltage
					
					if power_demand  >	pv_power +	bat_discharge:										# battery discharge limit
						power_demand =	pv_power +	bat_discharge
						adjusted_power = True
						if verbose: status_text += ', battery discharge limit %i W'%bat_discharge
				
				send_history = send_history[1:]+[power_demand]											# update power_demand history
				power_demand = check_saw(power_demand, send_history, block_saw_detection)
				
				if conf['discharge_timer']:																# active timer, inverter input limit
					if timer.inverter <= 100:	max_input = int( max_input_power *0.01 *timer.inverter)	# <= 100 as percentage 
					else:						max_input = timer.inverter 								# > 100 as W
					max_input += free_power																# add free power to timer limit
					
					if (in_pc/3600) > timer.energy and timer.battery != 0:								# hourly battery discharge limit exceeded
											max_input = pv_power
											status_text	+= ', battery discharge limit exceeded'
					
					if max_input > max_input_power:
											max_input = max_input_power
				
				else:						max_input = max_input_power 								# the limit of the gti(s) by configuration
				
				heat_cap, heat_status = heat_protect_cap(mppt_data, max_input_power)				# heat protection: linear cap by selected temp sensor
				if heat_cap < max_input:
					max_input = heat_cap
					if verbose: status_text += heat_status
				
				if power_demand	< 10:			# keep it positive with a little gap on bottom
					power_demand	= 0				# disable input
					adjusted_power = True
					send_history[-1] = power_demand
					status_text	+= ', inverter MIN power limit'
				
				if power_demand	> max_input:
					power_demand	= max_input
					adjusted_power = True
					send_history[-1] = power_demand
					status_text	+= ', inverter MAX power limit %i W'%max_input
			
				if verbose:																				# show saw tooth values
					print(	'input history', send_history, '\t1:2 {: 4.1f} %\t 3:4 {: 4.1f} %'.format( 
								round((1-(send_history[-1] / (0.01+send_history[-2])))*100,1), round((1-(send_history[-3] / (0.01+send_history[-4])))*100,1) ) )
			
			write_vz_log(power_demand, zero_shift, bat_voltage, mppt_data, conf)												# log to volkszaehler
			if verbose and predictor.enabled:
				print(predictor.status(predictive_offset))
			if verbose: print_status(Ls_read, power_demand, zero_shift, status_text, last_runtime, send_history)
			alarm_last = check_temp_alarms(alarm_last)

			last_runtime = time()
			last_Ls_read = Ls_read																		# remember meter reading for next cycle's change-based ramp trigger
			long_send_history = long_send_history[1:] + [power_demand]									# provide a long power_demand history
			long_meter_history = long_meter_history[1:] + [Ls_read if (power_demand and not adjusted_power and not ramp_cnt) else 0]

			n_active_inverters = send_to_inverters(power_demand, long_send_history, n_active_inverters, ramp_cnt > 0)	# send to inverters
			poll_chargers(esmart_handles, timeout_repeat, pv_cont)										# poll chargers
			cd.check_stale()																				# mark stale AGG devices

			in_pc += max(0, power_demand - pv_power)														# count energy only from battery
			if conf['discharge_timer'] and timer.update(): in_pc = 0									# reset battery discharge energy counter

			if stop_event.is_set(): break
			continue
	
	except KeyboardInterrupt:
		print("zeroinput interrupted.",file=sys.__stdout__)
	
	except Exception as e:
		print(e,file=sys.__stdout__)
		print_exc(file=sys.__stdout__)
	
	finally:
		if verbose: print('stop threads',file=sys.__stdout__)
		stop_event.set()																				# tell all threads to stop
		for t in victron_threads: t.join()
		for bridge in cd._vedirect_instances.values():
			try: bridge.stop()
			except Exception: pass

		for drv in inverter_drivers:
			try: drv.stop()
			except Exception: pass
		for h in esmart_handles: h['obj'].close()
		if verbose: print(sys.argv[0],"done.",file=sys.__stdout__)
	
exit(0)
