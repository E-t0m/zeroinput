#!/usr/bin/python3
# -*- coding: utf-8 -*-

from requests import get
from datetime import timedelta, datetime

chans = {		'erzeug':'your-channel-id',
				'bezug':'your-channel-id',
				'tibber':'your-channel-id',
				#'pv':'your-channel-id',
				'auto':'your-channel-id',
				
		}
n_days = 3

def get_average(n_days):
	hours = {}
	for key in chans.keys(): hours[key] = [0.0]*24
	
	for day in range(0,n_days):
		begin	= (datetime.today() - timedelta(days=day,hours=24)).replace(minute=0,second=0,microsecond=0)
		end		= (datetime.today() - timedelta(days=day,hours=1 )).replace(minute=50)
		beginstamp	= str(int(begin.timestamp())).ljust(13,'0')
		endstamp	= str(int(end.timestamp())).ljust(13,'0')
		url = 'http://127.0.0.0:8080/data.json?from='+beginstamp+'&to='+endstamp+'&group=hour'	# asumes volkszähler at localhost:8080
		for uuid in chans.values(): url += '&uuid[]='+uuid
	
		if True: 
			print('\n',day, '\tbegin\t',begin,beginstamp,'\n',day,'\tend\t',end,endstamp )
			begin_request = datetime.now()
		jresp = get(url=url).json()
		print('request duration:', datetime.now()-begin_request)
		
		#print('chan\t\tday\thour\tval')
		for row in jresp['data']:
			#print('\n',row['uuid'])
			for key in chans:
				if chans[key] == row['uuid']: chan_n = key
			
			for value in row['tuples']:
				tval = datetime.fromtimestamp(value[0]/1000)
				#print('%s\t\t%i\t%i\t% .2f'%(chan_n,tval.day,tval.hour,value[1])) # show hourly values
				hours[chan_n][tval.hour] += value[1]
		
		if True:
			print("\nday\thour\t%s"%(list(chans)))		# show summary
			for i in range(0,24): 
				hourline = ''
				for key in chans: hourline += '% 8.2f\t'%hours[key][i]
				print('%i\t%i\t%s'%(day,i,hourline))
	
	for i in range(0,24):								# calculate hourly averages
		for key in chans: 
			hours[key][i] /= n_days
	
	if True:											# calculate new "channels"
		hours['bezug+erzeug'] = [0.0]*24
		hours['b+e-auto'] = [0.0]*24
		for i in range(0,24):
			hours['bezug+erzeug'][i] = hours['bezug'][i]+abs(hours['erzeug'][i])
			hours['b+e-auto'][i] = hours['bezug+erzeug'][i] - hours['auto'][i]
	
	if True:											# show average values
		headerline = ''
		for chan in hours.keys(): headerline += ','+chan
		print("\n%i day AVERAGE\nhour%s"%(n_days,headerline))
		
		for i in range(0,24): 
			hourline = ''
			
			for key in hours.keys(): 
				hourline += '%.0f\t'%hours[key][i]
			print('%i\t%s'%(i,hourline))
	
	return(hours)


print(n_days,'days to go')
avg_day = get_average(n_days)

exit(0)

