#!/usr/bin/python3
# -*- coding: utf-8 -*-
# indent size 4, mode Tabs

debug = False
timescale = "QUARTER_HOURLY"	# or "HOURLY"

from json import load as json_load
from json import dump as json_dump
from time import strftime
from datetime import timedelta, datetime
from os.path import abspath, join, dirname
import syslog

today_present = False; today_date = None
tomorrow_present = False; tomorrow_date = None
today = datetime.now().strftime('%Y-%m-%d')

timeslots = 96 if timescale == 'QUARTER_HOURLY' else 24

if debug: print('timescale:',timescale,'timeslots:',timeslots)

try:
	with open(join(dirname(__file__),'tibber_prices.json'),'r') as fi:
		tibber_response = json_load(fi)		# read known prices from file
except:
	if debug: print('error reading: tibber_prices.json')
else:
	try:
		if len(tibber_response['data']['viewer']['homes'][0]['currentSubscription']['priceInfo']['today']) == timeslots:
			today_present = True
			today_date = tibber_response['data']['viewer']['homes'][0]['currentSubscription']['priceInfo']['today'][0]['startsAt'][0:10]
			if debug: print('today data is present:',today_date)
	except: pass
	try:
		if len(tibber_response['data']['viewer']['homes'][0]['currentSubscription']['priceInfo']['tomorrow']) == timeslots:
			tomorrow_present = True
			tomorrow_date = tibber_response['data']['viewer']['homes'][0]['currentSubscription']['priceInfo']['tomorrow'][-1]['startsAt'][0:10] 
			if debug: print('tomorrow data is present:',tomorrow_date)
	except: pass

if datetime.now().hour < 13:
	if 	( today_present 	and today_date		== today ) or \
		( tomorrow_present	and tomorrow_date	== today ):
		if debug: print('next day data is not yet available\ndone.')
		exit(0)
else:
	if 	( today_present 	and today_date		== today ) and \
		( tomorrow_present	and tomorrow_date	== (datetime.now()+timedelta(days=1)).strftime('%Y-%m-%d') ):
		if debug: print('done.')
		exit(0)

# fetch new prices from server
import requests
query = """
{	viewer {
	homes {
	currentSubscription {
			priceInfo(resolution: """+timescale+""") {
		today {
			total
			startsAt }
		tomorrow {
			total
			startsAt }
}}}}}	"""
try:
	with open(join(dirname(__file__),'tibber_personal_token.json'),'r') as fi: 
		tibber_personal_token = json_load(fi)['tibber_personal_token']	# get personal token from file
except:
	if debug: print('error reading: tibber_personal_token.json')
	syslog.syslog(syslog, "error reading: tibber_personal_token.json")
	exit(1)
else:
	if debug: print('successful read: tibber_personal_token.json')

if True:	# False for sandbox data
	if debug: print('fetching data from tibber server:')
	tibber_response = requests.post( "https://api.tibber.com/v1-beta/gql", json={"query":query}, headers={"Authorization":'Bearer ' + tibber_personal_token, "Content-Type": "application/json"} ).json()
else:
	if debug: print('sandbox response:')
	tibber_response = {"data": {"viewer": {"homes": [{"currentSubscription": {"priceInfo": {"today": [{"total": 0.334, "startsAt": "2025-10-01T00:00:00.000+02:00"}, {"total": 0.3217, "startsAt": "2025-10-01T00:15:00.000+02:00"}, {"total": 0.3143, "startsAt": "2025-10-01T00:30:00.000+02:00"}, {"total": 0.3135, "startsAt": "2025-10-01T00:45:00.000+02:00"}, {"total": 0.319, "startsAt": "2025-10-01T01:00:00.000+02:00"}, {"total": 0.3143, "startsAt": "2025-10-01T01:15:00.000+02:00"}, {"total": 0.3131, "startsAt": "2025-10-01T01:30:00.000+02:00"}, {"total": 0.312, "startsAt": "2025-10-01T01:45:00.000+02:00"}, {"total": 0.3136, "startsAt": "2025-10-01T02:00:00.000+02:00"}, {"total": 0.3131, "startsAt": "2025-10-01T02:15:00.000+02:00"}, {"total": 0.3125, "startsAt": "2025-10-01T02:30:00.000+02:00"}, {"total": 0.3125, "startsAt": "2025-10-01T02:45:00.000+02:00"}, {"total": 0.3159, "startsAt": "2025-10-01T03:00:00.000+02:00"}, {"total": 0.3159, "startsAt": "2025-10-01T03:15:00.000+02:00"}, {"total": 0.3173, "startsAt": "2025-10-01T03:30:00.000+02:00"}, {"total": 0.319, "startsAt": "2025-10-01T03:45:00.000+02:00"}, {"total": 0.3146, "startsAt": "2025-10-01T04:00:00.000+02:00"}, {"total": 0.3179, "startsAt": "2025-10-01T04:15:00.000+02:00"}, {"total": 0.3148, "startsAt": "2025-10-01T04:30:00.000+02:00"}, {"total": 0.3176, "startsAt": "2025-10-01T04:45:00.000+02:00"}, {"total": 0.3122, "startsAt": "2025-10-01T05:00:00.000+02:00"}, {"total": 0.3199, "startsAt": "2025-10-01T05:15:00.000+02:00"}, {"total": 0.328, "startsAt": "2025-10-01T05:30:00.000+02:00"}, {"total": 0.3413, "startsAt": "2025-10-01T05:45:00.000+02:00"}, {"total": 0.3274, "startsAt": "2025-10-01T06:00:00.000+02:00"}, {"total": 0.3517, "startsAt": "2025-10-01T06:15:00.000+02:00"}, {"total": 0.3705, "startsAt": "2025-10-01T06:30:00.000+02:00"}, {"total": 0.3929, "startsAt": "2025-10-01T06:45:00.000+02:00"}, {"total": 0.4034, "startsAt": "2025-10-01T07:00:00.000+02:00"}, {"total": 0.4279, "startsAt": "2025-10-01T07:15:00.000+02:00"}, {"total": 0.4264, "startsAt": "2025-10-01T07:30:00.000+02:00"}, {"total": 0.4149, "startsAt": "2025-10-01T07:45:00.000+02:00"}, {"total": 0.4502, "startsAt": "2025-10-01T08:00:00.000+02:00"}, {"total": 0.3825, "startsAt": "2025-10-01T08:15:00.000+02:00"}, {"total": 0.3517, "startsAt": "2025-10-01T08:30:00.000+02:00"}, {"total": 0.3253, "startsAt": "2025-10-01T08:45:00.000+02:00"}, {"total": 0.3779, "startsAt": "2025-10-01T09:00:00.000+02:00"}, {"total": 0.3369, "startsAt": "2025-10-01T09:15:00.000+02:00"}, {"total": 0.3234, "startsAt": "2025-10-01T09:30:00.000+02:00"}, {"total": 0.3105, "startsAt": "2025-10-01T09:45:00.000+02:00"}, {"total": 0.3347, "startsAt": "2025-10-01T10:00:00.000+02:00"}, {"total": 0.3227, "startsAt": "2025-10-01T10:15:00.000+02:00"}, {"total": 0.3086, "startsAt": "2025-10-01T10:30:00.000+02:00"}, {"total": 0.2912, "startsAt": "2025-10-01T10:45:00.000+02:00"}, {"total": 0.3113, "startsAt": "2025-10-01T11:00:00.000+02:00"}, {"total": 0.2977, "startsAt": "2025-10-01T11:15:00.000+02:00"}, {"total": 0.2896, "startsAt": "2025-10-01T11:30:00.000+02:00"}, {"total": 0.2849, "startsAt": "2025-10-01T11:45:00.000+02:00"}, {"total": 0.2931, "startsAt": "2025-10-01T12:00:00.000+02:00"}, {"total": 0.2895, "startsAt": "2025-10-01T12:15:00.000+02:00"}, {"total": 0.2898, "startsAt": "2025-10-01T12:30:00.000+02:00"}, {"total": 0.2895, "startsAt": "2025-10-01T12:45:00.000+02:00"}, {"total": 0.29, "startsAt": "2025-10-01T13:00:00.000+02:00"}, {"total": 0.2925, "startsAt": "2025-10-01T13:15:00.000+02:00"}, {"total": 0.2945, "startsAt": "2025-10-01T13:30:00.000+02:00"}, {"total": 0.2962, "startsAt": "2025-10-01T13:45:00.000+02:00"}, {"total": 0.2887, "startsAt": "2025-10-01T14:00:00.000+02:00"}, {"total": 0.2955, "startsAt": "2025-10-01T14:15:00.000+02:00"}, {"total": 0.3007, "startsAt": "2025-10-01T14:30:00.000+02:00"}, {"total": 0.3085, "startsAt": "2025-10-01T14:45:00.000+02:00"}, {"total": 0.2896, "startsAt": "2025-10-01T15:00:00.000+02:00"}, {"total": 0.3007, "startsAt": "2025-10-01T15:15:00.000+02:00"}, {"total": 0.3167, "startsAt": "2025-10-01T15:30:00.000+02:00"}, {"total": 0.3297, "startsAt": "2025-10-01T15:45:00.000+02:00"}, {"total": 0.2859, "startsAt": "2025-10-01T16:00:00.000+02:00"}, {"total": 0.3185, "startsAt": "2025-10-01T16:15:00.000+02:00"}, {"total": 0.3346, "startsAt": "2025-10-01T16:30:00.000+02:00"}, {"total": 0.3508, "startsAt": "2025-10-01T16:45:00.000+02:00"}, {"total": 0.3136, "startsAt": "2025-10-01T17:00:00.000+02:00"}, {"total": 0.3498, "startsAt": "2025-10-01T17:15:00.000+02:00"}, {"total": 0.3844, "startsAt": "2025-10-01T17:30:00.000+02:00"}, {"total": 0.4443, "startsAt": "2025-10-01T17:45:00.000+02:00"}, {"total": 0.3868, "startsAt": "2025-10-01T18:00:00.000+02:00"}, {"total": 0.4499, "startsAt": "2025-10-01T18:15:00.000+02:00"}, {"total": 0.5511, "startsAt": "2025-10-01T18:30:00.000+02:00"}, {"total": 0.6653, "startsAt": "2025-10-01T18:45:00.000+02:00"}, {"total": 0.698, "startsAt": "2025-10-01T19:00:00.000+02:00"}, {"total": 0.6598, "startsAt": "2025-10-01T19:15:00.000+02:00"}, {"total": 0.595, "startsAt": "2025-10-01T19:30:00.000+02:00"}, {"total": 0.5132, "startsAt": "2025-10-01T19:45:00.000+02:00"}, {"total": 0.4714, "startsAt": "2025-10-01T20:00:00.000+02:00"}, {"total": 0.3967, "startsAt": "2025-10-01T20:15:00.000+02:00"}, {"total": 0.3628, "startsAt": "2025-10-01T20:30:00.000+02:00"}, {"total": 0.3404, "startsAt": "2025-10-01T20:45:00.000+02:00"}, {"total": 0.3674, "startsAt": "2025-10-01T21:00:00.000+02:00"}, {"total": 0.3564, "startsAt": "2025-10-01T21:15:00.000+02:00"}, {"total": 0.3345, "startsAt": "2025-10-01T21:30:00.000+02:00"}, {"total": 0.3274, "startsAt": "2025-10-01T21:45:00.000+02:00"}, {"total": 0.3414, "startsAt": "2025-10-01T22:00:00.000+02:00"}, {"total": 0.3327, "startsAt": "2025-10-01T22:15:00.000+02:00"}, {"total": 0.3254, "startsAt": "2025-10-01T22:30:00.000+02:00"}, {"total": 0.317, "startsAt": "2025-10-01T22:45:00.000+02:00"}, {"total": 0.3245, "startsAt": "2025-10-01T23:00:00.000+02:00"}, {"total": 0.3209, "startsAt": "2025-10-01T23:15:00.000+02:00"}, {"total": 0.3203, "startsAt": "2025-10-01T23:30:00.000+02:00"}, {"total": 0.3135, "startsAt": "2025-10-01T23:45:00.000+02:00"}], "tomorrow": []}}}]}}}

if debug: print(tibber_response)

if 'Response \[200\]' in tibber_response:
	if debug: print('<Response [200]> from tibber server, try again later')
	syslog.syslog(syslog.LOG_INFO, "<Response [200]> from server")
	exit(1)

try:
	with open(join(dirname(__file__),'tibber_prices.json'),'w') as fo: json_dump(tibber_response,fo)	# write response to file
except:
	if debug: print('error writing: tibber_prices.json')
	syslog.syslog(syslog, "error writing: tibber_prices.json")
	exit(1)

if debug: print('prices written. done.')
syslog.syslog(syslog.LOG_INFO, "price data updated")
exit(0)
