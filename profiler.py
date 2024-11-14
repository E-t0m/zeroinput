#!/usr/bin/python3
# -*- coding: utf-8 -*-

from requests import get
from datetime import timedelta, datetime
from sys import argv

chans = {		'Wh Erzeug':	'your-vz-UUID-here',
				'Wh Bezug':		'your-vz-UUID-here',
				'¢/kWh tibber':	'your-vz-UUID-here',
				'Wh Auto':		'your-vz-UUID-here',
				'Wh Klima':		'your-vz-UUID-here',
				'Wh PV':		'your-vz-UUID-here',
		}

try:
	day_shift = int(argv[-1])	# get the number of days and days for backshift from command line
except:
	day_shift = 7

try:
	n_days = int(argv[-2])
except:
	n_days = day_shift
	day_shift = 0

def get_average(n_days,day_shift):
	hours = dict()
	for key in chans.keys(): hours[key] = [0.0]*24
	
	for day in range(0,n_days):
		begin	= (datetime.today() - timedelta(days=day+day_shift,hours=24)).replace(minute=0,second=0,microsecond=0)
		end		= (datetime.today() - timedelta(days=day+day_shift,hours=0 )).replace(minute=0,second=0,microsecond=0)
		beginstamp	= str(int(begin.timestamp())).ljust(13,'0')
		endstamp	= str(int(end.timestamp())).ljust(13,'0')
		url = 'http://127.0.0.0:8080/data.json?from='+beginstamp+'&to='+endstamp+'&group=hour'	# asumes volkszähler at localhost:8080
		for uuid in chans.values(): url += '&uuid[]='+uuid
	
		if True: 
			print('\n',day, '\tbegin',begin,beginstamp,'\tend',end,endstamp,'\t',end='')
			begin_request = datetime.now()
		jresp = get(url=url).json()
		print('request duration:', datetime.now()-begin_request,'\trows:',jresp['data'][0]['rows'])
		
		#print('chan\t\tday\thour\tval')
		for row in jresp['data']:
			#print('\n',row['uuid'])
			for key in chans:
				if chans[key] == row['uuid']: chan_n = key
			
			for value in row['tuples']:
				tval = datetime.fromtimestamp(value[0]/1000)
				if tval > end: continue												# drop next day values sometimes send from vz
				#print('%s\t\t%i\t%i\t% .2f'%(chan_n,tval.day,tval.hour,value[1]))	# show hourly values by channel
				hours[chan_n][tval.hour] += value[1]
		
		if True:
			print("\nday\thour\t%s"%(list(chans)))		# show daily summary
			for i in range(0,24): 
				hourline = ''
				for key in chans: hourline += '% 8.2f\t'%hours[key][i]
				print('%i\t%i\t%s'%(day,i,hourline))
	
	for i in range(0,24):								# calculate hourly averages
		for key in chans: 
			hours[key][i] /= n_days
	
	if True:											# calculate new "channels"
		hours['¢/h Kosten Bezug-Auto'] = [0.0]*24
		hours['¢/h Wert-Erzeug'] = [0.0]*24
		hours['Wh Bezug-Auto'] = [0.0]*24
		
		for i in range(0,24):
			hours['Wh Bezug-Auto'][i] = hours['Wh Bezug'][i] - hours['Wh Auto'][i]
			hours['¢/h Kosten Bezug-Auto'][i] = (hours['Wh Bezug-Auto'][i] * hours['¢/kWh tibber'][i]) / 1000	# Wh * ¢/kWh / 1000 = ¢/h
			hours['¢/h Wert-Erzeug'][i] = abs(hours['Wh Erzeug'][i]) * hours['¢/kWh tibber'][i] / 1000			# Wh * ¢/kWh / 1000 = ¢/h
		
	if True:											# show average values
		headerline = ''
		for chan in hours.keys(): headerline += ','+chan
		print("\n%s\t%i day\t%i shift\tAVERAGE\nhour%s"%(datetime.now().strftime('%Y-%m-%d\t%H:%M:%S'),n_days,day_shift,headerline))
		
		for i in range(0,24): 
			hourline = ''
			
			for key in hours.keys(): 
				hourline += '%.0f\t'%hours[key][i]
			print('%i\t%s'%(i,hourline))
		
		if True:		# summary 
			hourline = ''
			for key in hours.keys(): 
				hourline += '%.0f\t'%sum(hours[key])
			print('sum\t%s'%hourline)
			#print('\n',day, '\tbegin',begin,beginstamp,'\tend',end,endstamp)
	
	return(hours)


print(n_days,'days to go', 'shift '+str(day_shift) if day_shift else '')
avg_day = get_average(n_days,day_shift)

exit(0)



