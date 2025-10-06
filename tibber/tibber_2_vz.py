#!/usr/bin/python3
# -*- coding: utf-8 -*-

from json import load as json_load
from time import time, strftime, sleep
from datetime import datetime
from os.path import abspath, join, dirname

debug = False
vz_output_file = '/tmp/vz/soyo.log'		# the file vzlogger reads for imports

prices = dict()
try:
	with open(join(dirname(__file__),'tibber_prices.json'),'r') as fi:	tibber_response = json_load(fi) 		# read known prices from file
	for i in tibber_response['data']['viewer']['homes'][0]['currentSubscription']['priceInfo']['today']:	prices[i['startsAt'][0:16]] = i['total']
	for i in tibber_response['data']['viewer']['homes'][0]['currentSubscription']['priceInfo']['tomorrow']:	prices[i['startsAt'][0:16]] = i['total']
except: 
	if debug: print('error processing: tibber_prices.json')
	exit(1)

try:
	qnow = datetime.now()
	qnow = qnow.replace(minute=(qnow.minute//15)*15, second=0, microsecond=0).strftime('%Y-%m-%dT%H:%M')
	qpr = prices[qnow]
except:
	if debug: print('hour price not found!\nSTOP')
	exit(1)

for i in range(0,60):	# repeat 60 seconds
	ostr = '%i: tibber = %f\n'	% ( time(), qpr*100 )
	with open(vz_output_file,'a') as fo: fo.write(ostr)
	if debug: print(i,ostr)
	sleep(1)

exit(0)
