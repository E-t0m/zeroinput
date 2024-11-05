#!/usr/bin/python3
# -*- coding: utf-8 -*-
# indent size 4, mode Tabs
from json import load as json_load
from json import dump as json_dump
from os.path import abspath, join, dirname
from datetime import datetime, timedelta
from requests import get
from sys import argv
import syslog

try:
	with open(join(dirname(__file__),'tib_zero_tas.conf'),'r') as fi: conf = json_load(fi)	# read configuration from file
except:
	print('error reading config file')
	exit(1)

if '-h' in argv or '-help' in argv: 
	print('[ -v verbose ]','[ -debug ]',' o outside calculation phase, | between the thresholds, > below lower threshold, < above upper threshold')
	exit(0)

verbose = True if '-v' in argv else False				# use -v for output to the console
if '-debug' in argv: verbose = True; debug = True		# use -debug for more output
else: debug = False

def tasmota_timer(dev,time,action):						# set tasmota timer
	try:
		res = get('http://'+dev['ip']+'/cm?cmnd=Timer'+dev['timer_id']+'{\"Enable\":1,\"Mode\":0,\"Time\":\"'+time+'\",\"Window\":\"0\",\"Days\":\"SMTWTFS\",\"Repeat\":0,\"Output\":'+dev['output']+',\"Action\":'+action+'}' ).status_code
	except:
		res = 1
	return(res)

def tasmota_switch(dev,action):							# switch tasmota device on / off
	try:
		res = get('http://'+dev['ip']+'/cm?cmnd=Power'+dev['output']+'%20'+action ).status_code
	except:
		res = 1
	return(res)

def get_average(n_days):								# gets hourly averages from volkszähler database, see profiler.py for further statistical usage
	hours = {}
	for key in conf['vz_chans'].keys(): hours[key] = [0.0]*24
	
	if verbose: print('query volkszähler:')
	for day in range(0,n_days):
		begin	= (datetime.today() - timedelta(days=day,hours=24)).replace(minute=0, second=0,microsecond=0)
		end		= (datetime.today() - timedelta(days=day,hours=1 )).replace(minute=50,second=0,microsecond=0)
		beginstamp	= str(int(begin.timestamp())).ljust(13,'0')
		endstamp	= str(int(end.timestamp())).ljust(13,'0')
		url = 'http://'+conf['vz_host_port']+'/data.json?from='+beginstamp+'&to='+endstamp+'&group=hour'	# asumes volkszähler at localhost:8080
		for uuid in conf['vz_chans'].values(): url += '&uuid[]='+uuid
	
		if verbose: 
			print(day, '\tbegin',begin,beginstamp,'\tend',end,endstamp,'\t',end='')
			begin_request = datetime.now()
		jresp = get(url=url).json()
		if verbose: print('request duration:', datetime.now()-begin_request)
		
		for row in jresp['data']:
			for key in conf['vz_chans']:
				if conf['vz_chans'][key] == row['uuid']: chan_n = key
			for value in row['tuples']:
				tval = datetime.fromtimestamp(value[0]/1000)
				hours[chan_n][tval.hour] += value[1]
		
		if verbose:
			if debug: print("\nday\thour\t%s"%(list(conf['vz_chans'])))	# show summary
			for i in range(0,24): 
				hourline = ''
				for key in conf['vz_chans']: hourline += '% 8.2f\t'%hours[key][i]
				if debug: print('%i\t%i\t%s'%(day,i,hourline))
	
	for i in range(0,24):								# calculate hourly averages
		for key in conf['vz_chans']: hours[key][i] /= n_days
	
	hours['B+E'] = [0.0]*24
	for i in range(0,24): hours['B+E'][i] = hours['Bezug'][i] + abs(hours['Erzeug'][i]) - hours['Auto'][i]		# total consumption - Auto [Wh]
			
	if debug:									# show average values
		headerline = ''
		for chan in hours.keys(): headerline += ','+chan
		print("\n%s\t%i day\tAVERAGE\nhour%s"%(datetime.now().strftime('%Y-%m-%d\t%H:%M:%S'),n_days,headerline))
		for i in range(0,24): 
			hourline = ''
			for key in hours.keys(): hourline += '%.0f\t'%hours[key][i]
			print('%i\t%s'%(i,hourline))
	
	return(hours)

def read_average():										# read cached data or start a query
	vz_in = dict()
	try:
		with open(join(dirname(__file__),'avg_cache.json'),'r') as fi:
			vz_in = json_load(fi)						# read known averages from file
	except: 
		vz_in['timestamp'] = 1000000.123456				# a very old timestamp
	
	if datetime.fromtimestamp(vz_in['timestamp']).strftime('%Y-%m-%d %H') != datetime.now().strftime('%Y-%m-%d %H'):
		vz_in['B+E'] = get_average(7)['B+E']			# query volkszähler for hourly averages of 7 days
		vz_in['timestamp'] = datetime.now().timestamp()
		with open(join(dirname(__file__),'avg_cache.json'),'w') as fo:
			json_dump(vz_in,fo)							# write current averages to file
	else:
		if verbose: print('using cached volkszähler averages from',datetime.fromtimestamp(vz_in['timestamp']).strftime('%Y-%m-%d %H:%M'))
	return(vz_in)

def get_voltage():										# get the latest battery voltage from volkszähler
	time_resp = str(get(url='http://'+conf['vz_host_port']+'/data.json?uuid[]='+conf['vz_voltage_uuid']+'&from=now&to=now&options=raw').json()['data'][0]['from'])	# timecode for latest known voltage
	vz_voltage = get(url='http://'+conf['vz_host_port']+'/data.json?uuid[]='+conf['vz_voltage_uuid']+'&from='+time_resp+'&to='+time_resp+'&options=raw').json()['data'][0]['tuples'][0][1] # latest known voltage
	bat_cap = int(vz_voltage*1000-49700)				# simple abstraction for low voltage of 16s LiFePo4 battery capacity
	if verbose: print('latest voltage',vz_voltage,'V, bat efficiency',conf['bat_efficiency'],'%, remaining bat capacity',bat_cap if bat_cap > 0 else 0,'Wh')
	return(vz_voltage, bat_cap)

def main():
	prices = dict()
	try:
		with open(join(dirname(__file__),'tibber_prices.json'),'r') as fi:	tibber_response = json_load(fi) 	# read known prices from file
	except:
		print('error reading price file')
		exit(1)
	for i in tibber_response['data']['viewer']['homes'][0]['currentSubscription']['priceInfo']['today']:	prices[i['startsAt'][0:13]] = i['total']
	for i in tibber_response['data']['viewer']['homes'][0]['currentSubscription']['priceInfo']['tomorrow']:	prices[i['startsAt'][0:13]] = i['total']
	
	price_avg = 0
	future_stop = (datetime.now() + timedelta(days=0 if datetime.now().hour < 10 else 1)).replace(hour=10, minute=0, second=0, microsecond=0) # don't calculate for next day time
	future_prices = dict()								# prices to come
	for i in prices:
		tib_time = datetime.strptime(i+':59:59', '%Y-%m-%dT%H:%M:%S')
		if datetime.now() < tib_time and tib_time < future_stop:	# define calculation phase
			price_avg += prices[i]
			future_prices[i] = prices[i]
	
	vz_in = read_average()								# get average consumption data
	vz_voltage, bat_cap = get_voltage()					# get voltage and remaining battery capacity

	s_fupri = dict(sorted(future_prices.items(), key=lambda item: item[1], reverse=True))	# sort future prices by descending price
	
	cap_p = dict(); pv_pt = dict(); sum_p = 0; j = 0
	lowest_price_timed = list(s_fupri.values())[0]		# set to the highest price
	if verbose and sum_p < bat_cap: print('%s\t%s\t%s\t%s\t%s'%('date time','price','set','average','sum'))	# show table header if there is a table

	for i in s_fupri:									# iterate over relevant hours
		cur_p = int(vz_in['B+E'][int(i[-2:])])			# get the average power of the current hour
		
		if sum_p < bat_cap:								# as long as there is energy to dispose
			sum_p += cur_p
			
			if 	 j == 0: cap_p[i] = '9999'				# most expensive price gets "unlimited"
			elif j == 1: cap_p[i] = '%.f'%(cur_p * 1.2)	# 2nd expensive price gets 120%
			elif j == 2: cap_p[i] = '%.f'%(cur_p * 1.2)	# 3rd expensive price gets 110%
			else:		 cap_p[i] = '%.f'%(cur_p)		# all other get the 7d average amount
			j += 1
			lowest_price_timed = s_fupri[i]				# the lowest price with input
			if verbose: print('%s %.2f\t%s\t%i\t%i'%(i,s_fupri[i]*100,cap_p[i],cur_p,sum_p))
		else:			 
			cap_p[i] = '0'								# battery capacity was reached
			if debug: print('%s %.2f\t%i\t%s'%(i,s_fupri[i]*100,cur_p,cap_p[i]))
	
	if debug: print('lowest price timed %.2f'%(lowest_price_timed*100),'with',conf['bat_efficiency'],'%% = %.2f'%(lowest_price_timed*conf['bat_efficiency']))
	for i in future_prices:
		pv_pt[i] = False if (lowest_price_timed*conf['bat_efficiency'] > future_prices[i]*100) else True	# pass through PV power if the current price is higher than loss_of_load% of the lowest timed input price
		if debug: print(i,'%.2f'%(future_prices[i]*100),['<','>'][pv_pt[i]],'%.2f'%(lowest_price_timed*conf['bat_efficiency']),['','PVpt'][pv_pt[i]])
	
	price_avg = price_avg / len(future_prices) *100		# average price
	price_min = min(future_prices.values()) *100		# minimum price
	price_max = max(future_prices.values())	*100		# maximum price
	price_spread = (price_max-price_min)				# price spread
	price_lt = price_avg #- (price_spread * 0.5 )		# lower threshold - set the factor to your needs
	price_ut = price_avg - (price_spread * 0.1 )		# upper threshold - set the factor to your needs
	
	if verbose:	print('tibber price avg: %.2f'%price_avg,'min: %.2f'%(price_min),'max: %.2f'%(price_max),'spread: %.2f'%price_spread,'(%.f %%)'%(price_spread/price_max*100), \
						'lt: %.2f'%price_lt,'ht: %.2f,'%price_ut,'%i%%lpt %.2f'%(conf['bat_efficiency'],lowest_price_timed*conf['bat_efficiency']))
	if price_lt > conf['timer_max_price']: 
		price_lt = conf['timer_max_price']
		if verbose: print('set lt to max: %.2f'%price_lt)
	
	tib_hour_now = datetime.now().strftime('%Y-%m-%dT%H')
	
	timer_is_set =(len(conf['tasmota_dev'])+1)*[conf['disable_timer']]	# \ 
	hot_on	= conf['disable_hotswitch']									# - True disables hot switching and timers!
	hot_off	= conf['disable_hotswitch']									# /
	
	for cur_p_time in prices:							# iterate over all prices
		
		cur_price = prices[cur_p_time]*100				# current tibber price in ¢
		cur_timer = cur_p_time[-2:]+':00'				# current time for tasmota timer format
		calc_time = True if cur_p_time in future_prices else False	# checks for calculation phase
		msg = 'now ' if tib_hour_now == cur_p_time else ''
		
		if not calc_time:
			if not verbose: continue
			p_char = 'o'
		else:											# current and future hours to calculate
			if cur_price < price_lt: 					# lower threshold
				p_char = '>'
				
				if tib_hour_now == cur_p_time:
					if hot_on: pass 
					else:
						msg += 'hot on:'
						hot_on = True
						msg += ' 1' if tasmota_switch( conf['tasmota_dev']['auto1_on'],'1') == 200 else ' 1FAIL'
						msg += ' 2' if tasmota_switch( conf['tasmota_dev']['auto2_on'],'1') == 200 else ' 2FAIL'
						if not verbose: syslog.syslog(syslog.LOG_INFO, msg)
				else:
					if not timer_is_set[1] and not timer_is_set[2]:
						msg += ' T on:'; timer_is_set[1] = True; timer_is_set[2] = True
						msg += ' 1' if tasmota_timer( conf['tasmota_dev']['auto1_on'],cur_timer,'1') == 200 else ' 1FAIL'
						msg += ' 2' if tasmota_timer( conf['tasmota_dev']['auto2_on'],cur_timer,'1') == 200 else ' 2FAIL'
						if not verbose: syslog.syslog(syslog.LOG_INFO, msg +' at '+ cur_timer)
			
			else:										# middle and upper
				p_char = '|'
				
				if tib_hour_now == cur_p_time:
					if hot_off: pass 
					else:
						msg += 'hot off:'
						hot_off = True
						msg += ' 1' if tasmota_switch( conf['tasmota_dev']['auto1_off'],'0') == 200 else ' 1FAIL'
						msg += ' 2' if tasmota_switch( conf['tasmota_dev']['auto2_off'],'0') == 200 else ' 2FAIL'
						if not verbose: syslog.syslog(syslog.LOG_INFO, msg)
				else:
					if not timer_is_set[3] and not timer_is_set[4]:
						msg += 'T off:'; timer_is_set[3] = True; timer_is_set[4] = True
						msg += ' 3' if tasmota_timer( conf['tasmota_dev']['auto1_off'],cur_timer,'0') == 200 else ' 3FAIL'
						msg += ' 4' if tasmota_timer( conf['tasmota_dev']['auto2_off'],cur_timer,'0') == 200 else ' 4FAIL'
						if not verbose: syslog.syslog(syslog.LOG_INFO, msg +' at '+ cur_timer)
				
				if price_ut < cur_price:				# upper threshold
					p_char = '<'
		
		if verbose: print(str(msg).ljust(20),cur_p_time,'%2.2f %4s %4s'%(cur_price,(cap_p[cur_p_time] if calc_time else ' '),(['','PVpt'][pv_pt[cur_p_time]] if calc_time else '')),str(p_char).rjust(int(cur_price)))
	
	# write a timer file for zeroinput
	with open(conf['timer.txt'],'w') as fo:
		fo.write('# 0000-00-00 for daily repeating, space or tab separated\n#                   battery discharge W if > 100, percentage if <= 100\n# date     time     |   ac inverter power W if > 100, percentage if <= 100\n# |        |        |   |   energy limit in Wh\n') #fileheader
		for i in future_prices:
			file_form = i.replace('T',' ') + ':00:00 '
			
			if	 cap_p[i] != '0':	file_form += '100 100 '+cap_p[i]	# discharge battery up to the given limit (with PV pass through)
			elif pv_pt[i]:			file_form += '0 100 0'				# full PV pass through, no battery discharge
			else:					file_form += '0 0 0'				# disable input for that hour
			
			fo.write(file_form+'\n')
			if debug: print(str(file_form).ljust(40))
	
	if verbose: print('done.')
	return(0)

exit(main())
