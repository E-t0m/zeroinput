#!/usr/bin/python3
# -*- coding: utf-8 -*-

import argparse
import requests
from time import time, sleep

# --- Konfiguration ---------------------------------------------------------

CONFIG = {
	'auto': {
		'url':       'http://192.168.178.77/cm?cmnd=status%2010',
		'sleep':     1,
		'sum_power': True,
	},
	'pumpe': {
		'url':       'http://192.168.178.88/cm?cmnd=status%2010',
		'sleep':     3,
		'sum_power': False,
	},
	'garten': {
		'url':       'http://192.168.178.99/cm?cmnd=status%2010',
		'sleep':     5,
		'sum_power': True,
	}
}

# --- Argumente ---------------------------------------------------------

parser = argparse.ArgumentParser(description='Tasmota-Leistungswert auslesen und an vzlogger-Log anhaengen')
parser.add_argument('name', choices=CONFIG.keys(), help='Welcher Sensor ausgelesen werden soll')
parser.add_argument('--debug', action='store_true', help='Debug-Ausgaben aktivieren')
args = parser.parse_args()

cfg = CONFIG[args.name]
debug = args.debug

sleep(cfg['sleep']) # shift network traffic

# --- Hilfsfunktionen ---------------------------------------------------------

def read_power(url):
	for i in range(0,3):
		try:
			resp = requests.get(url=url, timeout=5)
			resp.raise_for_status()
			data = resp.json()
			return data['StatusSNS']['ENERGY']['Power']
		except Exception as e:
			if debug: print('sleep', url, e)
			sleep(15)
	return None

def write_log(*lines):
	with open('/tmp/vz/output_to_vz.log','a') as fo:
		for line in lines:
			fo.write(line)

# --- Ein Geraet, optional 2-Kanal-Summe ---------------------------------------------------------

success = False

power = read_power(cfg['url'])
if power is not None:
	if cfg['sum_power']: power = sum(power)

	for j in range(1,4):
		write_log('%i: %s_p = %i\n'	% ( time(), args.name, power ) )
		if debug: print('%i %i: %s_p = %i\n'	% (j, time(), args.name, power ) )
		
		if j >= 3: break
		sleep(15)

	success = True

exit(0 if success else 1)
