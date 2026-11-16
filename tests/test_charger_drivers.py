#!/usr/bin/python3
# -*- coding: utf-8 -*-
# smoke test for charger_drivers.py — no real hardware required.
# Exercises: esmart instantiation and set_port, build_chargers discovery,
# combine_charger_data aggregation, display_mppt_data rendering,
# check_temp_alarms triggering, set_victron_power routing.

import io
import sys
from os.path import abspath, dirname, join
sys.path.insert(0, dirname(dirname(abspath(__file__))))	# the modules live in the repository root
from unittest.mock import MagicMock, patch

import charger_drivers as cd

# ── helpers ────────────────────────────────────────────────────────────────────

def make_conf(**charger_entries):
	return {
		'chargers': charger_entries,
		'inverters': {
			'base': {'type': 'soyosource', 'port': '/dev/ttyACM0', 'stage': [1,2],
			         'count': 1, 'max_power': 900, 'min_power': 10},
		},
		'alarms': {},
	}

def ok(label): print('OK  %s' % label)

# ── 1. esmart set_port stores name from conf ───────────────────────────────────

def test_esmart_set_port():
	conf = make_conf(**{'/dev/ttyACM5': {'name': 'my esmart', 'mppt_type': 'eSmart3', 'pvp': 1200}})
	e = cd.esmart(verbose=False)
	e.set_port('/dev/ttyACM5', conf)
	assert e.port == '/dev/ttyACM5', 'port wrong'
	assert e.name == 'my esmart',    'name not stored from conf'
	e2 = cd.esmart()
	e2.set_port('/dev/ttyACM5')     # no conf → falls back to port string
	assert e2.name == '/dev/ttyACM5'
	ok('esmart.set_port')

# ── 2. build_chargers: discovery ──────────────────────────────────────────────

def test_build_chargers():
	conf = make_conf(**{
		'/dev/ttyACM0': {'name': 'esmart 60', 'mppt_type': 'eSmart3', 'pvp': 2350},
		'/dev/ttyACM2': {'name': 'VE 150',    'mppt_type': 'victron', 'pvp': 1720},
	})
	# patch Serial so no real port is opened
	from serial import SerialException
	with patch('charger_drivers.Serial', side_effect=SerialException('no hw')):
		e3, vd, ts, handles, modbus_handles = cd.build_chargers(conf, verbose=False)
	assert '/dev/ttyACM0' in e3,    'esmart3_devs missing'
	assert '/dev/ttyACM2' in vd,    'victron_devs missing'
	assert ts == [],                 'no temp sensors expected'
	assert len(handles) == 1,        'one esmart handle expected'
	assert modbus_handles == [],     'no modbus handles expected'
	ok('build_chargers')

# ── 3. combine_charger_data: aggregation and PVperc ───────────────────────────

def test_combine_charger_data():
	conf = make_conf(**{
		'/dev/ttyACM0': {'name': 'esmart A', 'mppt_type': 'eSmart3', 'pvp': 1000},
		'/dev/ttyACM1': {'name': 'esmart B', 'mppt_type': 'eSmart3', 'pvp': 1000},
	})
	# inject fake mppt_data
	cd.mppt_data.clear()
	cd.mppt_data['combined'] = {}
	cd.mppt_data['/dev/ttyACM0'] = {'PPV': 400, 'Vbat': 52.0, 'Ibat': 4.0, 'Pload': 300, 'ts': 0}
	cd.mppt_data['/dev/ttyACM1'] = {'PPV': 600, 'Vbat': 53.0, 'Ibat': 6.0, 'Pload': 200, 'ts': 0}
	cd.combine_charger_data(conf, verbose=False)
	c = cd.mppt_data['combined']
	assert c['PPV']  == 1000,     'PPV sum wrong: %s' % c['PPV']
	assert c['Vbat'] == 52.5,     'Vbat avg wrong: %s' % c['Vbat']
	assert c['Ibat'] == 10.0,     'Ibat sum wrong: %s' % c['Ibat']
	assert c['Pload'] == 500,     'Pload real sum wrong: %s' % c['Pload']  # 300+200, no projection
	assert c.get('PVperc') == 50, 'PVperc wrong: %s' % c.get('PVperc')
	ok('combine_charger_data')

# ── 3b. combine_charger_data: PPV decay on PORT error ─────────────────────────

def test_ppv_decay():
	conf = make_conf(**{'/dev/ttyACM0': {'name': 'e', 'mppt_type': 'eSmart3', 'pvp': 1000}})
	cd.mppt_data.clear(); cd.mppt_data['combined'] = {}; cd._ppv_decay.clear()
	# live
	cd.mppt_data['/dev/ttyACM0'] = {'PPV': 500, 'Vbat': 52.0, 'Ibat': 5.0, 'Pload': 0, 'CS': 1, 'ts': 0}
	cd.combine_charger_data(conf)
	assert cd.mppt_data['combined']['PPV'] == 500
	# PORT error → decay ×0.9 per cycle
	cd.mppt_data['/dev/ttyACM0'] = {'CS': 'PORT'}
	cd.combine_charger_data(conf)
	assert abs(cd.mppt_data['combined']['PPV'] - 450) < 0.1
	cd.combine_charger_data(conf)
	assert abs(cd.mppt_data['combined']['PPV'] - 405) < 0.1
	# recovery → live value, decay reset
	cd.mppt_data['/dev/ttyACM0'] = {'PPV': 480, 'Vbat': 52.0, 'Ibat': 4.5, 'Pload': 0, 'CS': 1, 'ts': 0}
	cd.combine_charger_data(conf)
	assert cd.mppt_data['combined']['PPV'] == 480
	ok('combine_charger_data: PPV decay')

# ── 4. display_mppt_data: runs without crash, produces output ─────────────────

def test_display_mppt_data():
	conf = make_conf(**{
		'/dev/ttyACM0': {'name': 'esmart A', 'mppt_type': 'eSmart3', 'pvp': 1000},
	})
	cd.mppt_data.clear()
	cd.mppt_data['combined'] = {'PPV': 400, 'Vbat': 52.0, 'Ibat': 4.0, 'Pload': 300}
	cd.mppt_data['/dev/ttyACM0'] = {'CS': 2, 'PPV': 400, 'Vbat': 52.0, 'Ibat': 4.0,
	                                 'Pload': 300, 'Iload': 5.8, 'ts': __import__('time').time(),
	                                 'int_temp': 35, 'ext_temp': 28}
	buf = io.StringIO()
	with patch('sys.stdout', buf):
		cd.display_mppt_data(conf, verbose=True)
	out = buf.getvalue()
	assert 'esmart A' in out, 'device name missing from output'
	assert 'BULK'     in out, 'CS=2 should render as BULK'
	ok('display_mppt_data')

# ── 5. check_temp_alarms: fires when threshold crossed ───────────────────────

def test_check_temp_alarms():
	conf = make_conf(**{'/dev/ttyACM0': {'name': 'esmart A', 'mppt_type': 'eSmart3'}})
	conf['alarms'] = {'esmart A': {'ext_hi': 40, 'ext_hi_cmd': 'true &', 'ext_hi_interval': 0}}
	cd.mppt_data['/dev/ttyACM0'] = {'ext_temp': 45}  # above threshold
	fired = []
	with patch('charger_drivers.system', side_effect=lambda cmd: fired.append(cmd)):
		cd.check_temp_alarms(
			conf, {}, ['/dev/ttyACM0'], [], verbose=False)
	assert fired == ['true &'], 'alarm command not fired: %s' % fired
	ok('check_temp_alarms')

# ── 6. set_victron_power: routing to AGG vs conventional ─────────────────────

def test_set_victron_power():
	# conventional path: puts value in queue
	from queue import Queue
	cd._victron_cmd_queues['/dev/ttyACM2'] = Queue(maxsize=1)
	conf = make_conf(**{'/dev/ttyACM2': {'name': 'VE 150', 'mppt_type': 'victron'}})
	cd.set_victron_power('/dev/ttyACM2', 500, conf)
	assert cd._victron_cmd_queues['/dev/ttyACM2'].get_nowait() == 500
	del cd._victron_cmd_queues['/dev/ttyACM2']

	# AGG path: delegates to bridge.set_watts
	bridge_mock = MagicMock()
	cd._vedirect_instances['virt'] = bridge_mock
	conf2 = make_conf(**{
		'ser123': {'name': 'K4', 'mppt_type': 'victron', '_agg_port': 'virt', '_ser': 'ser123'}
	})
	cd.set_victron_power('ser123', 300, conf2)
	bridge_mock.set_watts.assert_called_once_with('ser123', 300)
	del cd._vedirect_instances['virt']
	ok('set_victron_power')

# ── 7. Modbus chargers (epever / renogy / morningstar) ───────────────────────

def _mb_reply(unit, func, regs):
	p = bytes([unit, func, 2*len(regs)])
	for r in regs: p += bytes([(r >> 8) & 0xFF, r & 0xFF])
	return p + cd._modbus_crc16(p)

class _BankSerial:
	"""Fake serial answering Modbus reads from a {start_addr: [regs]} bank."""
	def __init__(self, banks): self.banks = banks; self._out = b''
	def reset_input_buffer(self): pass
	def flush(self): pass
	def write(self, d):
		unit, func = d[0], d[1]
		start = (d[2] << 8) | d[3]; count = (d[4] << 8) | d[5]
		regs = self.banks.get(start)
		self._out = _mb_reply(unit, func, regs[:count]) if regs and len(regs) >= count else b''
	def read(self, n):
		c = self._out[:n]; self._out = self._out[n:]; return c

def test_modbus_core():
	assert cd._modbus_crc16(bytes([1,3,0,0,0,1])) == bytes([0x84,0x0a])
	assert cd._s16(0xFFFF) == -1 and cd._s16(100) == 100
	ok('modbus core (crc16, s16)')

def test_epever():
	e = cd.EpeverCharger('/dev/ep', 1)
	e.ser = _BankSerial({0x3100:[1337,9,24,0,1280,50,120,0],
	                     0x3110:[2500,2045], 0x311A:[85], 0x3201:[0x000C]})
	e.read(); d = cd.mppt_data['/dev/ep']
	assert abs(d['VPV']-13.37)<0.01 and abs(d['Vbat']-12.80)<0.01
	assert d['SOC']==85 and d['CS']==8
	ok('EpeverCharger')

def test_renogy():
	r = cd.RenogyCharger('/dev/rg', 1)
	r.ser = _BankSerial({0x0100:[100,139,9,0x4822,0,0,0,431,9,4]})
	r.read(); d = cd.mppt_data['/dev/rg']
	assert d['SOC']==100 and abs(d['Vbat']-13.9)<0.01 and abs(d['VPV']-43.1)<0.01
	assert d['PPV']==4 and d['int_temp']==72 and d['ext_temp']==34
	# negative battery temperature (sign bit)
	r.ser = _BankSerial({0x0100:[100,139,9,(48<<8)|(0x80|37),0,0,0,431,9,4]})
	r.read(); assert cd.mppt_data['/dev/rg']['ext_temp']==-37
	ok('RenogyCharger (incl. negative temp)')

def test_morningstar():
	m = cd.MorningstarCharger('/dev/ms', 1)
	m.ser = _BankSerial({0x0000:[123,57410,80,0], 0x0018:[3504,0,0,4000,1000,0],
	                     0x0023:[45,0x80,25], 0x003A:[0,600], 0x0032:[5]})
	m.read(); d = cd.mppt_data['/dev/ms']
	vscale = 123.876/32768.0
	assert abs(d['Vbat']-3504*vscale)<0.02 and d['CS']==1
	assert d['int_temp']==45 and d['ext_temp']==25
	ok('MorningstarCharger')

# ── run all ───────────────────────────────────────────────────────────────────

if __name__ == '__main__':
	test_esmart_set_port()
	test_build_chargers()
	test_combine_charger_data()
	test_ppv_decay()
	test_display_mppt_data()
	test_check_temp_alarms()
	test_set_victron_power()
	test_modbus_core()
	test_epever()
	test_renogy()
	test_morningstar()
	print('\nAll charger_drivers smoke tests passed.')
