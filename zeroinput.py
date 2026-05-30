#!/usr/bin/python3
# -*- coding: utf-8 -*-
# zeroinput v2.0
# indent size 4, mode Tabs

from serial import Serial, SerialException
from json import load as json_load
from time import strftime, time, sleep
from datetime import timedelta, datetime
from threading import Thread, Event
from queue import Queue, Empty
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

mppt_data = {'combined':{}}; victron_devs = []; esmart3_devs = []; soyosource_devs= []; temp_sensor_devs = []

def _expand_victron_agg(conf):
	"""Expand victron_agg ports into synthetic per-device entries in conf['rs485'].
	Each entry uses SER# as port key and carries _agg_port + _ser for the write path.
	Devices with type='temp' become mppt_type='temp_sensor'.
	The original victron_agg entry is removed."""
	for port in list(conf['rs485'].keys()):
		dev = conf['rs485'][port]
		if dev.get('mppt_type') != 'victron_agg': continue
		port_name = dev.get('name', port)
		for ser, dev_info in dev.get('devices', {}).items():
			name      = dev_info['name'] if isinstance(dev_info, dict) else dev_info
			dev_type  = dev_info.get('type', 'mppt') if isinstance(dev_info, dict) else 'mppt'
			pvp       = dev_info.get('pvp', 0)       if isinstance(dev_info, dict) else 0
			if dev_type == 'temp':
				conf['rs485'][ser] = {
					'name':       name,
					'mppt_type':  'temp_sensor',
					'_agg_port':  port,
					'_ser':       ser,
					'_port_name': port_name,
				}
			else:
				conf['rs485'][ser] = {
					'name':       name,
					'mppt_type':  'victron',
					'_agg_port':  port,
					'_ser':       ser,
					'_pvp':       pvp,
					'_port_name': port_name,
				}
		del conf['rs485'][port]
	return conf

conf = _expand_victron_agg(conf)

# check unique device names
_names = [v['name'] for v in conf['rs485'].values() if 'name' in v]
if len(_names) != len(set(_names)):
	print('ERROR: duplicate device names in rs485 config: %s' % _names)
	exit(1)

for dev in conf['rs485']:
	if 'mppt_type' in conf['rs485'][dev] and conf['rs485'][dev]['mppt_type'] == 'victron':
		# for aggregated devices use the physical port for the thread, not the synthetic SER# key
		_thread_port = conf['rs485'][dev].get('_agg_port', dev)
		if _thread_port not in victron_devs:
			victron_devs.append(_thread_port)
		mppt_data[dev] = {}
	if 'mppt_type' in conf['rs485'][dev] and conf['rs485'][dev]['mppt_type'] == 'eSmart3':
										esmart3_devs.append(dev)
										mppt_data[dev] = {}
	if 'mppt_type' in conf['rs485'][dev] and conf['rs485'][dev]['mppt_type'] == 'temp_sensor':
										temp_sensor_devs.append(dev)
										mppt_data[dev] = {}
	if 'inverter' in conf['rs485'][dev] and conf['rs485'][dev]['inverter'] == 'soyosource':
										soyosource_devs.append(dev)

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


_MPPT_FMT = '{:<12s}  {:<10s}  {:>4s}  {:>4s}  {:>5s}  {:>5s}  {:<4s}  {:>5s}  {:>5s}  {:>3s}  {:>4s}  {:>4s}  {:<8s}'	# display_mppt_data column layout

def _drain_rec_msgs():
	"""Print all deferred REC messages queued by reader threads."""
	while not _rec_msgs.empty():
		try: print(_rec_msgs.get_nowait())
		except Empty: break

def display_mppt_data():			# display the mppt charger data
	if not verbose: return
	global mppt_data
	print(_MPPT_FMT.format('port','name','W PV','%PVp','V bat','I bat','mode','Pload','Iload','age','Tint','Text',''))	# header line
	
	for port in list(mppt_data.keys()):		# snapshot keys to avoid race during iteration
		port_data = mppt_data.get(port, {})	# snapshot reference — atomic under GIL
		if port != 'combined' and conf['rs485'].get(port, {}).get('mppt_type') == 'temp_sensor':
			continue						# displayed separately below
		if 'CS' in port_data:
			cs = port_data['CS']
			# CS from VEDirect stream arrives as string — cast to int if possible
			if isinstance(cs, str) and cs.lstrip('-').isdigit(): cs = int(cs)
			if port != 'combined' and conf['rs485'][port].get('mppt_type') == 'victron':
				if   cs == 'PORT':                             mppt_dev_mode = 'PORT ERROR'
				elif isinstance(cs, int) and cs <= 14:         mppt_dev_mode = ['OFF','','FAULT','BULK','ABSORB','FLOAT','','EQUAL','','','START','','RECOND','','EXTCON'][cs]
				else:                                          mppt_dev_mode = ''
			elif port != 'combined' and conf['rs485'][port].get('mppt_type') == 'eSmart3':
				if   cs == 'PORT':                             mppt_dev_mode = 'PORT ERROR'
				elif isinstance(cs, int) and cs <= 4:          mppt_dev_mode = ['WAIT','MPPT','BULK','FLOAT','PRE'][cs]
				else:                                          mppt_dev_mode = ''
			else: mppt_dev_mode = ''
		else: mppt_dev_mode = ''

		# load string: ON shows current, OFF shows 'OFF', eSmart3 derives from Iload
		load_str = ''
		if 'LOAD' in port_data:					# Victron: explicit ON/OFF + IL
			on = port_data['LOAD'] == 'ON'
			load_str = ('%.1f' % port_data.get('IL', 0)) if on else 'OFF'
		elif 'Iload' in port_data:				# eSmart3: derive from Iload
			il = port_data['Iload']
			load_str = ('%.1f' % il) if il > 0 else ''

		# %PVp: PPV as percentage of configured pvp
		# for combined: use sum of all configured pvp values across all devices
		if port == 'combined':
			pvp_val = sum(
				d.get('pvp', 0) or d.get('_pvp', 0)
				for d in conf['rs485'].values()
				if d.get('mppt_type') in ('victron', 'eSmart3')
			) or None
		else:
			pvp_val = conf['rs485'][port].get('pvp') or conf['rs485'][port].get('_pvp') or None
		ppv     = port_data.get('PPV')
		pvp_str = ('%i%%' % int(ppv / pvp_val * 100)) if (pvp_val and ppv is not None and pvp_val > 0) else ''

		# all numeric values passed without padding — the format string handles alignment
		print(_MPPT_FMT.format(
			'all' if port == 'combined' else port,
			'combined' if port == 'combined' else conf['rs485'][port]['name'],
			'%i'   % ppv				if ppv  is not None else '',
			pvp_str,
			'%.2f' % port_data['Vbat']		if 'Vbat' in port_data else '',
			'%.2f' % port_data['Ibat']		if 'Ibat' in port_data else '',
			mppt_dev_mode,
			('%i' % port_data['Pload']) if ('Pload' in port_data and port_data['Pload'] > 0) else '',
			load_str,
			('%.0fs' % (time() - port_data['ts'])) if 'ts' in port_data else '',
			str(port_data['int_temp'])	if 'int_temp' in port_data else '',
			str(port_data['ext_temp'])	if 'ext_temp' in port_data else '',
			str(conf['rs485'][port]['temp_display']) if (port != 'combined' and 'temp_display' in conf['rs485'][port]) else '') )

	# show unconfigured devices seen on AGG ports
	for bridge in _vedirect_instances.values():
		known     = set(bridge._ser_to_key.keys())
		# get display name of the AGG port from any of its configured devices
		_any_key  = next(iter(bridge._ser_to_key.values()), None)
		port_name = conf['rs485'][_any_key]['_port_name'] if _any_key else bridge._physical
		for ser in bridge._vd.get_all().keys():
			if ser not in known:
				print('{:12s}  {:10s}  UNCONFIGURED'.format(ser[:12], port_name[:10]))

	# show AGG temperature sensors
	for port in list(mppt_data.keys()):
		if port == 'combined': continue
		if conf['rs485'].get(port, {}).get('mppt_type') != 'temp_sensor': continue
		port_data = mppt_data.get(port, {})
		temp = port_data.get('ext_temp')
		age  = ('%.0fs' % (time() - port_data['ts'])) if 'ts' in port_data else ''
		print(_MPPT_FMT.format(
			conf['rs485'][port].get('_port_name', port)[:12],
			conf['rs485'][port]['name'],
			'', '', '', '', 'TEMP', '', '', age, '',
			('%.1f' % temp) if temp is not None else '', ''))

def combine_charger_data():			# combine all mppt charger data to a summary
	global mppt_data
	d = {'PPV':0,'Vbat':0,'Ibat':0,'Pload':0}
	
	for name in d.keys():
		valcnt = 0
		for dev in list(mppt_data.keys()):		# snapshot keys to avoid race during iteration
			if dev == 'combined': continue
			dev_data = mppt_data.get(dev, {})	# snapshot reference — atomic under GIL
			if name not in dev_data: continue
			# Pload projection assumes load = one inverter per port;
			# only include ports with a configured inverter — Victron load port
			# Pload (IL*Vbat) without an inverter is a DC load, not an inverter
			if name == 'Pload' and not conf['rs485'].get(dev, {}).get('inverter'):
				continue
			valcnt += 1
			d[name] += dev_data[name]
		if name == 'Vbat'	and valcnt > 0: d[name] /= valcnt	# the average
		if name == 'Pload'	and valcnt > 0: d[name] = d[name] * n_active_inverters		# project one eSmart3 reading to all inverters

	# PVp: total PV power as percentage of sum of all configured peak powers
	pvp_sum = sum(
		dev.get('pvp', 0) or dev.get('_pvp', 0)
		for dev in conf['rs485'].values()
		if dev.get('mppt_type') in ('victron', 'eSmart3')
	)
	if pvp_sum > 0:
		d['PVperc'] = int(d['PPV'] / pvp_sum * 100)

	mppt_data['combined'] = d
	return(0)


def set_soyo_demand(ser,power):		# create and send the packet for soyosource gti
	pu = power >> 8
	pl = power & 0xFF
	cs = 264 - pu - pl
	if cs > 255: 
		if power > 250:	cs -= 256
		else:			cs -= 255
	
	ser.write( bytearray([0x24,0x56,0x00,0x21,pu,pl,0x80,cs]) )
	ser.flush()
	return(0)


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
	global verbose
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
		for port, dev in conf.get('rs485', {}).items():
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
	"""Write a vzlogger.conf example based on current rs485 and vz_channels config."""
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
	'rs485', 'vzlogger_log_file',
	'persistent_vz_file', 'webconfig_port',
	# total_number_of_inverters: hot-reloadable — predictor no longer needs it
	# and zeroinput reads it directly from conf each cycle.
}

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


def send_to_inverters(power_demand, long_send_history, n_active_inverters):
	"""Send power demand to soyosource inverters. Returns n_active_inverters.
	Each port is opened once; the demand packet is sent twice (50ms apart)
	for reliability — Soyosource inverters occasionally ignore single packets."""
	if power_demand == 0:
		if verbose:
			print('. power request')
			_drain_rec_msgs()
		return 0

	single = (conf['total_number_of_inverters'] == 1 or
	          sorted(long_send_history)[-4] <= conf['single_inverter_threshold'])
	ports  = [conf['basic_load_inverter_port']] if single else soyosource_devs
	demand = power_demand if single else int(power_demand / conf['total_number_of_inverters'])

	for port in ports:
		try:
			with Serial(port, 4800) as ser:
				set_soyo_demand(ser, demand)
				sleep(0.05)
				set_soyo_demand(ser, demand)
		except SerialException as e:
			if verbose: print('%s: %s' % (port, e))

	n_active_inverters = 1 if single else conf['total_number_of_inverters']
	if verbose:
		print('power request %s %i W' % (
			'1x' if single else '%ix' % conf['total_number_of_inverters'], demand))
		_drain_rec_msgs()
	return n_active_inverters


def poll_chargers(esmart_handles, timeout_repeat, pv_cont):
	"""Poll all esmart3 chargers."""
	for charger in esmart_handles: charger['obj'].open()
	for i in [1,2]:
		if datetime.now() > timeout_repeat or pv_cont != 0:
			for charger in esmart_handles:
				charger['obj'].esmart_status_request()
				if verbose: print('%i:  %s : %s status request' % (i, charger['obj'].port, conf['rs485'][charger['obj'].port]['name']))
		elif verbose: print('. eSmart3 status')
		sleep(0.22)
	for charger in esmart_handles: charger['obj'].close()


def check_temp_alarms(alarm_last):
	"""Check and trigger temperature alarms for eSmart3 and AGG temp_sensor devices.
	Alarm thresholds are read from conf['alarms'][device_name]. Each sensor (int/ext)
	supports an independent high and low alarm: it fires when its threshold AND command
	are configured and the temperature crosses it (temp > hi  or  temp < lo). Thresholds
	may be negative or zero; only a missing key disables that alarm. Each alarm has its
	own interval (default 300 s). eSmart3 devices expose int_temp + ext_temp; temp_sensor
	devices expose ext_temp only.
	alarm_last: {device_name: {'int_hi','int_lo','ext_hi','ext_lo': datetime}}"""
	def fire(a, data, key, sub, label, name, times):
		if key not in data: return
		temp = data[key]
		for bound, cmp in (('hi', temp.__gt__), ('lo', temp.__lt__)):
			thr_key = '%s_%s' % (sub, bound)			# e.g. int_hi
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
	for port in esmart3_devs + temp_sensor_devs:
		name = conf['rs485'][port].get('name')
		a    = alarms.get(name, {})
		if not a or port not in mppt_data: continue
		times = alarm_last.setdefault(name, {
			'int_hi': datetime.min, 'int_lo': datetime.min,
			'ext_hi': datetime.min, 'ext_lo': datetime.min})
		fire(a, mppt_data[port], 'int_temp', 'int', 'internal', name, times)
		fire(a, mppt_data[port], 'ext_temp', 'ext', 'external', name, times)
	return alarm_last


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


class esmart:						# eSmart3 MPPT charger lib by skagmo.com 2018: https://github.com/skagmo/esmart_mppt | adapted for zeroinput
	def __init__(self):
		self.state = 0 # STATE_START
		self.data = []
		self.port = ""
		self.timeout = 0
	
	def __del__(self):	self.close()
	
	def set_port(self, port):	self.port = port
	
	def open(self):
		try:
			self.ser = Serial(self.port, 9600, timeout=0.1)
		except SerialException as e:
			if verbose: print("Could not open port %s: %s" % (self.port, e))
			mppt_data[self.port] = {'CS': 'PORT'}	# zero all values on port error
			self.ser = None
	def close(self):
		try:
			self.ser.close()
			self.ser = False
		except AttributeError:
			pass
	
	def send(self, pl):	self.ser.write(self.pack(pl))
	
	def parse(self, data):
		global mppt_data
		for c in data:
			if (self.state == 0):		# STATE_START
				if (c == 0xaa):			# Start character detected
					self.state = 1		# STATE_DATA
					self.data = []
					self.target_len = 255
			elif (self.state == 1):		# STATE_DATA
				self.data.append(c)
				if (len(self.data) == 5): self.target_len = 6 + self.data[4]	# Received enough of the packet to determine length
				if (len(self.data) == self.target_len):		# Received whole packet
					self.state = 0		# STATE_START
					# validate checksum: sum of all bytes (0xaa header + data) mod 256 == 0
					if (0xaa + sum(self.data)) & 0xFF != 0:
						if verbose: print('esmart checksum error on %s — packet discarded' % self.port)
						continue
					if (self.data[2] == 3): 
						if (self.data[3] == 0):	# Type 0 packet contains most data
							ts = time()
							if verbose: _rec_msgs.put('REC %s : %s%s' % (
								self.port, conf['rs485'][self.port]['name'],
								'' if 'ts' not in mppt_data[self.port] else '  delay %.2fs' % (ts - mppt_data[self.port]['ts'])))
							# build new dict locally, then replace atomically to avoid race with main loop
							_new = {}
							_new['CS']		= int.from_bytes(self.data[7:9],	byteorder='little')
							_new['VPV']		= int.from_bytes(self.data[9:11],	byteorder='little') / 10.0
							_new['Vbat']	= int.from_bytes(self.data[11:13],	byteorder='little') / 10.0
							_new['Ibat']	= int.from_bytes(self.data[13:15],	byteorder='little') / 10.0
							_new['Vload']	= int.from_bytes(self.data[17:19],	byteorder='little') / 10.0
							_new['Iload']	= int.from_bytes(self.data[19:21],	byteorder='little') / 10.0
							_new['PPV']		= int.from_bytes(self.data[21:23],	byteorder='little')
							_new['Pload']	= int.from_bytes(self.data[23:25],	byteorder='little')
							_new['ext_temp']= self.data[25] if self.data[25] < 200 else self.data[25] - 256
							_new['int_temp']= self.data[27] if self.data[27] < 200 else self.data[27] - 256
							_new['ts']		= ts
							mppt_data[self.port] = _new	# atomic reference replacement
	
	def esmart_status_request(self):
		if self.ser is None: return
		try:
			while self.ser.inWaiting():
				self.parse(self.ser.read(100))
			
			if (time() - self.timeout) > 1:
				self.ser.write(b"\xaa\x01\x01\x01\x00\x03\x00\x00\x1e\x32")								# send status request
				self.timeout = time()
		except IOError:
			reconnect_delay = 0.5
			reconnect_attempts = 20
			if verbose: print("Serial port error, fixing", self.port)
			try:
				self.ser.close()
			except Exception:
				pass
			for attempt in range(1, reconnect_attempts + 1):
				try:
					self.ser = Serial(self.port, 9600, timeout=0.1)
					if verbose: print("Error fixed after %i attempt(s)" % attempt)
					break
				except SerialException as e:
					if verbose: print("Attempt %i failed: %s" % (attempt, e))
					sleep(reconnect_delay)
			else:
				if verbose: print("Could not reopen port after %i attempts: %s" % (reconnect_attempts, self.port))
				mppt_data[self.port] = {'CS': 'PORT'}	# zero all values on port error
	
	
def _map_victron_fields(src):
	"""Map raw VE.Direct field names to zeroinput's mppt_data field names.
	Handles V→Vbat, I→Ibat, mV/mA→V/A conversion, CS cast, Pload derivation.
	src: dict of {field: value} as parsed from VE.Direct stream."""
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


def _victron_rec_msg(port_name, dev_name, old):
	"""Build a deferred REC message string with delay if previous timestamp known."""
	now = time()
	delay = ('  delay %.2fs' % (now - old['ts'])) if 'ts' in old else ''
	return 'REC %s : %s%s' % (port_name, dev_name, delay), now
_vedirect_instances  = {}	# {physical_port: VEDirectBridge}  — AGG ports
_victron_cmd_queues  = {}	# {device_key: Queue(maxsize=1)}    — conventional ports
_rec_msgs            = Queue()	# deferred REC messages printed after power request


class VEDirectBridge:
	"""Wraps ve_aggregator.VEDirect and writes parsed blocks into zeroinput's mppt_data
	via the on_block callback — no patching, no double parsing."""

	def __init__(self, physical_port):
		try:
			from ve_aggregator import VEDirect as _VEDirect
		except ImportError:
			raise ImportError('ve_aggregator.py not found')
		self._physical    = physical_port
		# build SER# → synthetic key map from conf
		self._ser_to_key  = {d['_ser']: k for k, d in conf['rs485'].items()
		                     if d.get('_agg_port') == physical_port}
		self._vd          = _VEDirect(physical_port, on_block=self._on_block)
		if verbose: print('VEDirectBridge: started on %s  devices: %s' % (
			physical_port, list(self._ser_to_key.keys())))

	def _on_block(self, ser, block):
		"""Called by ve_aggregator after every parsed block — mirror into mppt_data."""
		key = self._ser_to_key.get(ser)
		if not key: return
		dev      = conf['rs485'][key]
		old      = mppt_data.get(key, {})
		port_lbl = dev.get('_port_name', self._physical)
		# temperature sensor block (DS18B20 via firmware)
		if dev.get('mppt_type') == 'temp_sensor':
			temp_raw = block.get('TEMP')
			if temp_raw is not None:
				try:
					temp = float(temp_raw)
					_, now = _victron_rec_msg(port_lbl, dev['name'], old)	# msg unused, temp has its own format
					mppt_data[key] = {'ext_temp': temp, 'ts': now}
					if verbose: _rec_msgs.put('REC %s : %s  %.1f°C%s' % (
						port_lbl, dev['name'], temp,
						('  delay %.2fs' % (now - old['ts'])) if 'ts' in old else ''))
				except ValueError: pass
			return
		mapped       = _map_victron_fields(block)
		msg, now     = _victron_rec_msg(port_lbl, dev['name'], old)
		mapped['ts'] = now
		if verbose: _rec_msgs.put(msg)
		mppt_data[key] = mapped

	def start(self):   self._vd.start(); return self

	def stop(self):    self._vd.stop()

	def check_stale(self):
		"""Call once per main loop cycle. Marks devices not seen within
		device_timeout as CS='PORT' and zeroes all measurement values to prevent
		stale data from affecting combine_charger_data and set_victron_power."""
		active = set(self._vd.get_all().keys())		# filtered by device_timeout
		for ser, key in self._ser_to_key.items():
			if ser not in active and key in mppt_data:
				if mppt_data[key].get('CS') != 'PORT':		# only zero once on transition
					mppt_data[key] = {'CS': 'PORT'}			# atomic replace — all values gone

	def set_watts(self, ser, watts):
		self._vd.set_watts(ser, watts)

# ── VE.Direct HEX SET (conventional direct ports) ────────────────────────────
# Implements the same SET sequence as readtext_sendhex firmware:
# convert W→0.1A, write register 0x2015, verify by readback.

_HEX_ADDR        = 0x2015	# Charge Current Limit, 0.1A units, volatile
_HEX_TIMEOUT     = 0.4		# s: wait for HEX ACK / GET reply (matches firmware)
_VBAT_FALLBACK   = 24.0		# V: used when Vbat not yet known (matches firmware default)


def _hex_build_set(val_x10):
	"""VE.Direct HEX SET frame for register 0x2015. val_x10 in 0.1A units."""
	lo = _HEX_ADDR & 0xFF;  hi = (_HEX_ADDR >> 8) & 0xFF
	vlo = val_x10  & 0xFF;  vhi = (val_x10  >> 8) & 0xFF
	cs = (0x55 - 0x80 - lo - hi - 0x00 - vlo - vhi) & 0xFF
	return (':8%02X%02X00%02X%02X%02X\n' % (lo, hi, vlo, vhi, cs)).encode()


def _hex_build_get():
	"""VE.Direct HEX GET frame for register 0x2015 readback."""
	lo = _HEX_ADDR & 0xFF;  hi = (_HEX_ADDR >> 8) & 0xFF
	cs = (0x55 - 0x70 - lo - hi - 0x00) & 0xFF
	return (':7%02X%02X00%02X\n' % (lo, hi, cs)).encode()


def _hex_read_line(ser_obj, timeout):
	"""Read one line from serial within timeout. Returns stripped str or ''."""
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
	"""Extract register value from HEX GET response (:6 frame). Returns int or None."""
	# format: :6 LL HH FF VV WW CS  (all as hex nibbles, no spaces)
	if not line.startswith(':6') or len(line) < 14:
		return None
	try:
		val_lo = int(line[8:10],  16)
		val_hi = int(line[10:12], 16)
		return val_lo | (val_hi << 8)
	except Exception:
		return None


def _hex_exec_set(ser_obj, device_key, watts):
	"""Execute SET sequence on a conventional VE.Direct port.
	Matches firmware behaviour: W→0.1A conversion, write 0x2015, verify by GET."""
	vbat    = mppt_data.get(device_key, {}).get('Vbat') or _VBAT_FALLBACK
	reg_val = round(watts / vbat * 10)

	# 1. send SET frame
	ser_obj.write(_hex_build_set(reg_val))
	ser_obj.flush()

	# 2. wait for ACK (any HEX response line)
	deadline = time() + _HEX_TIMEOUT
	ack_ok = False
	while time() < deadline:
		line = _hex_read_line(ser_obj, deadline - time())
		if line.startswith(':'):
			ack_ok = True
			break

	if not ack_ok:
		if verbose: print('victron HEX set: ACK timeout for %s' % device_key)
		return

	# 3. send GET for verification
	ser_obj.write(_hex_build_get())
	ser_obj.flush()

	# 4. wait for GET reply
	deadline = time() + _HEX_TIMEOUT
	rb_val = None
	while time() < deadline:
		line = _hex_read_line(ser_obj, deadline - time())
		rb_val = _hex_parse_get_reply(line)
		if rb_val is not None:
			break

	# 5. compare
	if rb_val is None:
		if verbose: print('victron HEX set: verify timeout for %s' % device_key)
	elif rb_val != reg_val:
		if verbose: print('victron HEX set: verify mismatch set=%i rb=%i for %s' % (
			reg_val, rb_val, device_key))
	else:
		if verbose: print('victron HEX set: OK %s %iW %.1fA' % (
			device_key, watts, reg_val / 10.0))


def set_victron_power(device_key, watts):
	"""Set MPPT charge power limit. Works for both AGG and conventional ports."""
	dev = conf['rs485'].get(device_key, {})
	if '_agg_port' in dev:
		vd = _vedirect_instances.get(dev['_agg_port'])
		if vd:
			vd.set_watts(dev['_ser'], watts)
	else:
		q = _victron_cmd_queues.get(device_key)
		if q:
			try:	q.put_nowait(watts)		# drop if queue full (previous command pending)
			except Exception: pass


def handle_victron_data(serialport, stop_event: Event):												# reads serial data of victron devices (conventional, non-AGG ports only)
	global mppt_data
	rec_buf = {}
	victron_debug = False
	try:
		ser = Serial(port=serialport, baudrate=19200, bytesize=8, parity='N', stopbits=1, timeout=2, xonxoff=0, rtscts=0)
		ser.reset_input_buffer()
		while True:
			if stop_event.is_set(): 
				ser.close()
				return(0)
			data = b''; char = ''
			while char != b'\n':
				char = ser.read()
				data += char
			if victron_debug: print('victron raw data:',data)
			if data:
				snv = str(data)[2:].split('\\t')
				if len(snv) == 2: name,val = snv
				else: continue
				val = val[:-5]
				if name == 'V': name = 'Vbat'
				if name == 'I': name = 'Ibat'
				if name == 'PID':										# begin new dataset with PID
					ts = time()
					old = mppt_data.get(serialport, {})
					mppt_data[serialport] = rec_buf
					if verbose:
						msg, _ = _victron_rec_msg(serialport, conf['rs485'][serialport]['name'], old)
						_rec_msgs.put(msg)
					rec_buf = {'ts':ts}
					if 'PPV' in old: rec_buf['PPV'] = old['PPV']	# keep old PPV for continuity
				if name in ['PID','SER#','OR','LOAD','Checksum']:		rec_buf[name] = val					# add as string
				elif name in ['Vbat','Ibat','VPV','IL'] and val.isnumeric():	rec_buf[name] = 0.001* int(val)		# add as float (mV/mA → V/A)
				elif val.isnumeric():									rec_buf[name] = int(val)			# add as int
				else:
					if victron_debug: print('victron ELSE',data,'\n')
					pass	# there seems to be a transmission error, ignore it
				# derive Pload from IL * Vbat for Victron load port devices
				if name == 'Checksum' and 'IL' in rec_buf and 'Vbat' in rec_buf:
					rec_buf['Pload'] = int(rec_buf['IL'] * rec_buf['Vbat'])
				# after complete block: check for pending SET command
				if name == 'Checksum' and serialport in _victron_cmd_queues:
					try:
						watts = _victron_cmd_queues[serialport].get_nowait()
						_hex_exec_set(ser, serialport, watts)
					except Empty:
						pass
			continue
	except Exception as e:
		stop_event.set()							# tell all threads to stop
		print(e)
		print_exc()
		mppt_data[serialport] = {'CS': 'PORT'}	# zero all values on port error
		ser.close()
		return(1)


if __name__ =="__main__":
	try:
		stop_event = Event(); victron_threads = []
		if start_httpd and conf.get('webconfig_port', 0):
			try:
				import webconfig
				Thread(target=webconfig.start, args=(conf['webconfig_port'], stop_event, web_stats), daemon=True).start()
			except ImportError:
				print('webconfig.py not found – httpd disabled')

		# start victron reader threads / VEDirectBridge instances
		_agg_physical = {d['_agg_port'] for d in conf['rs485'].values() if '_agg_port' in d}
		for port in victron_devs:
			if port in _agg_physical:
				try:
					bridge = VEDirectBridge(port)
					_vedirect_instances[port] = bridge
					bridge.start()
					if verbose: print('VEDirectBridge started on %s' % port)
				except ImportError as e:
					print(e)
			else:
				# conventional port: dedicated reader thread + command queue
				_victron_cmd_queues[port] = Queue(maxsize=1)
				t = Thread(target=handle_victron_data, args=(port, stop_event))
				victron_threads.append(t)
				t.start()
		
		max_input_power	= conf['max_input_power']
		n_active_inverters = 0
		power_demand		= 0
		last_send		= 0
		last2_send		= 0
		ramp_cnt			= 0
		ramp_power			= 0
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
		alarm_last			= {}			# {port: {'int': datetime, 'ext': datetime}, 'inverter_fault': datetime}
		timeout_repeat		= datetime.now()
		vz_in				= open(conf['vzlogger_log_file'],'r')
		
		
		timer = None
		if conf['discharge_timer']: timer = discharge_times()										# set up timer
		if not conf.get('load_prediction', False):
			predictor = type('P', (), {'update': lambda *a,**k: 0, 'reload_conf': lambda *a,**k: None, 'status': lambda *a,**k: '', 'enabled': False, 'offset': 0, 'ramp_override_by_predictor': False})()
		else:
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
		
		esmart_handles = []
		for port in esmart3_devs:																		# set up esmart3 devices
			esmart_handles.append( { 'obj': esmart() } )
			esmart_handles[-1]['obj'].set_port(port)
			esmart_handles[-1]['obj'].open()
			for i in [1,2]:												# request status 2 times
				if verbose: print(i)
				esmart_handles[-1]['obj'].esmart_status_request()
				sleep(0.20)
			esmart_handles[-1]['obj'].close()
		if verbose: print('reading power meter data\n')
		
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
			
			if predictor.ramp_override_by_predictor:
				ramp_cnt				= 0		# discard stale ramp state
				dropped_first_up_ramp	= False	# reset on override entry
			else:
				# high change of power consumption, on rise: no active power limitation, sufficient bat_voltage
				if (Ls_read < -400) or (Ls_read > 400 and not adjusted_power and bat_voltage > 51.0):
					if not dropped_first_up_ramp and Ls_read > 400: 								# don't delay down ramps
						dropped_first_up_ramp = True
						if verbose: print('DROPPED first Ramp')
					else:
						if	ramp_cnt == 0:
							ramp_cnt = 2 + n_active_inverters											# counted in script cycles
							ramp_power = int(Ls_read + last2_send + zero_shift)						# without predictive_offset
				
				if ramp_cnt > 0:																			# within ramp countdown
					block_saw_detection = True																# disable saw detection
					power_demand = ramp_power
					if verbose: print('ramp mode %i'%ramp_cnt)
					ramp_cnt -= 1
					
					if ramp_cnt == 0:
						dropped_first_up_ramp = False
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
				
				if bat_voltage <= 48 or (conf['discharge_timer'] and										# set a new battery timeout
					( (not timer.battery or (timer.battery and (in_pc/3600) > timer.energy) ) and (not timer.inverter) )):
					adjusted_power = True
					power_demand		= 0																	# disable input
					send_history	= [0]*4																# reset history
					timeout_repeat = datetime.now() + timedelta(minutes = 1)							# repeat in one minute
				
				else:
					pv_bat_minus = 0 if bat_voltage > 49 else (49-bat_voltage)*50 * n_active_inverters		# reduction by battery voltage in relation to the base consumption of the inverter(s)
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
					
					elif bat_voltage >= 48 and bat_voltage <= 51:												# limit battery power, pass through pv power
						bat_power_percent_by_voltage	= (bat_voltage - 46.93 ) **3.281					# powercurve between 48-51 V, results in 1-100%
						bat_power_by_voltage			= int(0.01 * max_input_power * bat_power_percent_by_voltage)	# 100% above 51 V
						
						if verbose: status_text = ', Bat %i W (%.1f%%)'	% (bat_power_by_voltage, bat_power_percent_by_voltage) + ', PV %i W (-%i W)'% (pv_power, pv_p_minus) if pv_power  != 0 else ''
					
					if conf['free_power_export'] and bat_voltage >= 54.5:										# give some free power to the world = "pull down the zero line" (not zero shift!)
						free_power = int( (1.0/(57-54.5)) * (bat_voltage - 54.5) * conf['max_input_power'] )	# full energy input at maximum bat voltage: depends on mppt chargers "saturation charging voltage", usually 57 V
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
			# possible inverter fault: if all inverters are active and the ratio of
			# power_demand to pv_power significantly exceeds 1.0, one inverter is
			# likely not delivering — zeroinput ramps toward pv_power * n/(n-1).
			# only valid when battery is not discharging — battery discharge raises
			# power_demand above pv_power legitimately and would cause false alarms.
			# use avg of long_send_history to avoid false positives on transient ramps.
			if (pv_power > 100
					and n_active_inverters == conf['total_number_of_inverters']
					and conf['total_number_of_inverters'] > 1
					and bat_discharge == 0):			# no battery discharge active
				_avg_demand = avg(long_send_history)
				_threshold  = pv_power * conf['total_number_of_inverters'] / (conf['total_number_of_inverters'] - 1) * 0.85
				if _avg_demand > _threshold:
					if verbose:
						print('possible inverter fault: avg demand %iW pv %iW threshold %iW' % (
							int(_avg_demand), pv_power, int(_threshold)))
					a = conf.get('alarms', {}).get('inverter_fault', {})
					if a.get('cmd'):
						interval = a.get('interval', 300)
						last     = alarm_last.get('inverter_fault', datetime.min)
						if last + timedelta(seconds=interval) < datetime.now():
							alarm_last['inverter_fault'] = datetime.now()
							system(a['cmd'])
			if verbose and predictor.enabled:
				print(predictor.status(predictive_offset))
			if verbose: print_status(Ls_read, power_demand, zero_shift, status_text, last_runtime, send_history)
			alarm_last = check_temp_alarms(alarm_last)

			last_runtime = time()
			long_send_history = long_send_history[1:] + [power_demand]									# provide a long power_demand history
			long_meter_history = long_meter_history[1:] + [Ls_read if (power_demand and not adjusted_power and not ramp_cnt) else 0]

			n_active_inverters = send_to_inverters(power_demand, long_send_history, n_active_inverters)	# send to inverters
			poll_chargers(esmart_handles, timeout_repeat, pv_cont)										# poll chargers
			for bridge in _vedirect_instances.values(): bridge.check_stale()						# mark stale AGG devices

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

		for h in esmart_handles: h['obj'].close()
		if verbose: print(sys.argv[0],"done.",file=sys.__stdout__)
	
exit(0)
