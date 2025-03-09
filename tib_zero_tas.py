#!/usr/bin/python3
# -*- coding: utf-8 -*-
# indent size 4, mode Tabs
from json import load as json_load
from json import dump as json_dump
from os.path import abspath, join, dirname
from datetime import datetime, timedelta
from time import mktime
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
	keys = ['Inverter','Import','Auto','Lader','Klima']	# use this channels for calculation
	for key in keys: hours[key] = [0.0]*24
	
	if verbose: print('query volkszähler for %i day consumption data:'%n_days)
	for day in range(0,n_days):
		begin	= (datetime.today() - timedelta(days=day,hours=24)).replace(minute=0,second=0,microsecond=0)
		end		= (datetime.today() - timedelta(days=day,hours=0 )).replace(minute=0,second=0,microsecond=0)
		beginstamp	= str(int(begin.timestamp())).ljust(13,'0')
		endstamp	= str(int(end.timestamp())).ljust(13,'0')
		url = 'http://'+conf['vz_host_port']+'/data.json?from='+beginstamp+'&to='+endstamp+'&group=hour'
		
		for key in keys: url += '&uuid[]='+conf['vz_chans'][key]
		
		if verbose: 
			print(day, '\tbegin',begin,beginstamp,'\tend',end,endstamp,'\t',end='')
			begin_request = datetime.now()
		jresp = get(url=url).json()
		if verbose: print('request duration:', datetime.now()-begin_request,'\trows:',jresp['data'][0]['rows'])
		
		for row in jresp['data']:
			for key in keys:
				if conf['vz_chans'][key] == row['uuid']: chan_n = key
			for value in row['tuples']:
				tval = datetime.fromtimestamp(value[0]/1000)
				if tval > end: continue					# drop next day values sometimes sent by vz
				hours[chan_n][tval.hour] += value[1]
		
		if verbose:
			if debug: print("\nday\thour\t%s"%(keys))	# show summary
			for i in range(0,24): 
				hourline = ''
				for key in keys: hourline += '% 8.2f\t'%hours[key][i]
				if debug: print('%i\t%i\t%s'%(day,i,hourline))
	
	for i in range(0,24):								# calculate hourly averages
		for key in keys: hours[key][i] /= n_days
	
	hours['IILAK'] = [0.0]*24
	for i in range(0,24): hours['IILAK'][i] = hours['Import'][i] + abs(hours['Inverter'][i]) - hours['Auto'][i] - hours['Lader'][i] - hours['Klima'][i]		# total consumption - Auto - Lader - Klima [Wh]
			
	if debug:											# show average values
		headerline = ''
		for chan in hours.keys(): headerline += ','+chan
		print("\n%s\t%i day\tAVERAGE\nhour%s"%(datetime.now().strftime('%Y-%m-%d\t%H:%M:%S'),n_days,headerline))
		for i in range(0,24): 
			hourline = ''
			for key in hours.keys(): hourline += '%.0f\t'%hours[key][i]
			print('%i\t%s'%(i,hourline))
	
	return(hours)

def read_average():										# read cached data or start a query
	vz_in = {}
	try:
		with open(join(dirname(__file__),'avg_cache.json'),'r') as fi:
			vz_in = json_load(fi)						# read known averages from file
	except: 
		vz_in['timestamp'] = 1000000.123456				# a very old timestamp
	
	if datetime.fromtimestamp(vz_in['timestamp']).strftime('%Y-%m-%d %H') != datetime.now().strftime('%Y-%m-%d %H'):
		vz_in['IILAK'] = get_average(conf['average_days'])['IILAK']			# query volkszähler for hourly averages
		vz_in['timestamp'] = datetime.now().timestamp()
		with open(join(dirname(__file__),'avg_cache.json'),'w') as fo:
			json_dump(vz_in,fo)							# write current averages to file
	else:
		if verbose: print('using cached volkszähler averages from',datetime.fromtimestamp(vz_in['timestamp']).strftime('%Y-%m-%d %H:%M'))
	return(vz_in)

def get_vz_bat_cap():									# get battery energy content and voltage
	if verbose: print(datetime.now().strftime('%Y-%m-%d %H:%M'),'query volkszähler for energy content:')
	
	days_back = 0
	while True:
		begin	= (datetime.today() - timedelta(days=days_back)).replace(hour=0,minute=0,second=0,microsecond=0)
		end		= datetime.today().replace(microsecond=0)
		beginstamp	= str(int(begin.timestamp())).ljust(13,'0')
		endstamp	= str(int(end.timestamp())).ljust(13,'0')
		url = 'http://'+conf['vz_host_port']+'/data.json?from='+beginstamp+'&to='+endstamp		+'&uuid[]='+conf['vz_chans']['Vbat']
		
		if verbose: 
			print(days_back, '\tbegin',begin,beginstamp,'\tend',end,endstamp,'\t',end='')
			begin_request = datetime.now()
		jresp = get(url=url).json()
		if verbose: print('request duration:', datetime.now()-begin_request,'\trows:',jresp['data'][0]['rows'])
		
		if days_back == 0: latest_voltage = jresp['data'][0]['tuples'][-1][1]
		
		if jresp['data'][0]['min'][1] <= 48.5: break	# voltage < 48.5V considers a empty battery
		days_back += 1
	
	min_v = 999
	for ts,v,s in jresp['data'][0]['tuples']: 			# find latest minimum voltage with timestamp
		if v <= min_v: 
			min_ts = ts; min_v = v
	
	begin	= datetime.fromtimestamp(min_ts/1000)
	end		= datetime.today().replace(microsecond=0)
	beginstamp	= str(min_ts).ljust(13,'0')				# use minimum timestamp
	endstamp	= str(int(end.timestamp())).ljust(13,'0')
	url = 'http://'+conf['vz_host_port']+'/data.json?from='+beginstamp+'&to='+endstamp+'&group=hour'
	
	for key in ['Inverter','PV','Lader']: url += '&uuid[]='+conf['vz_chans'][key]
	
	if verbose: 
		print(days_back, '\tbegin',begin,beginstamp,'\tend',end,endstamp,'\t',end='')
		begin_request = datetime.now()
	
	try: jresp = get(url=url).json()
	except: 
		if verbose: print('bat cap data was unusable')
		jresp = []
	
	if verbose: print('request duration:', datetime.now()-begin_request,'\trows:',jresp['data'][0]['rows'])
	
	vz_bat_cap = 0000
	for row in jresp['data']:
		
		if row['uuid'] == conf['vz_chans']['PV']:
			vz_bat_cap += abs(row['consumption']) *conf['PV_to_bat_efficiency']*0.01
		
		if row['uuid'] == conf['vz_chans']['Lader']:
			vz_bat_cap += row['consumption'] *conf['AC_to_bat_efficiency']*0.01
		
		elif row['uuid'] == conf['vz_chans']['Inverter']:
			vz_bat_cap += row['consumption'] *(1 +1 -conf['bat_to_AC_efficiency']*0.01)		# negative values get subtracted
	
	vz_bat_cap *= conf['bat_to_AC_efficiency']*0.01		# effective available AC energy input power
	
	if verbose: print('minimum voltage %.1f V,'%min_v,'latest voltage %.1f V,'%latest_voltage,'remaining battery content %.f Wh'%vz_bat_cap)
	return(latest_voltage,int(vz_bat_cap))


def main():
	prices = {}
	try:
		with open(join(dirname(__file__),'tibber_prices.json'),'r') as fi:	tibber_response = json_load(fi) 	# read known prices from file
	except:
		print('error reading price file')
		exit(1)
	for i in tibber_response['data']['viewer']['homes'][0]['currentSubscription']['priceInfo']['today']:	prices[i['startsAt'][0:13]] = i['total']
	for i in tibber_response['data']['viewer']['homes'][0]['currentSubscription']['priceInfo']['tomorrow']:	prices[i['startsAt'][0:13]] = i['total']
	
	price_avg = 0
	
	if conf['calc_after_noon']:
										calc_stop_time = (datetime.now() + timedelta(days=9)).replace(hour=12, minute=0, second=0, microsecond=0) # calculate for the complete tibber price window
	else:
		if datetime.now().hour < 12:	calc_stop_time = (datetime.now() + timedelta(days=0)).replace(hour=12, minute=0, second=0, microsecond=0) # today, don't calculate battery discharge after this time
		else:							calc_stop_time = (datetime.now() + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0) # tomorrow
	
	future_prices = {}									# prices to come
	for i in prices:
		tib_time = datetime.strptime(i+':59:59', '%Y-%m-%dT%H:%M:%S')
		if tib_time > datetime.now() and tib_time < calc_stop_time:		# define the calculation phase
			price_avg += prices[i]
			future_prices[i] = prices[i]
	
	vz_in = read_average()								# get average consumption data
	vz_voltage, vz_bat_cap = get_vz_bat_cap()			# get voltage and remaining battery content
	
	s_fupri = dict(sorted(future_prices.items(), key=lambda item: item[1], reverse=True))	# sort future prices descending 
	
	cap_p = {}; pv_pt = {}; sum_p = 0; j = 0
	
	highest_price_time = list(s_fupri.keys())[0]		# the time of the highest price
	lowest_price_timed = s_fupri[highest_price_time]	# set to the highest price
	
	if verbose and sum_p < vz_bat_cap: print('%s\t%s\t%s\t%s'%('date time     price','set','average','sum'))	# show table header if there is a table
	
	for i in s_fupri:									# iterate over relevant hours
		cur_avg_energy = int(vz_in['IILAK'][int(i[-2:])])	# get the average power of the current hour
		
		if sum_p < vz_bat_cap and not conf['disable_battery_discharge']:	# as long as there is energy to dispose
			sum_p += cur_avg_energy
			
			if mktime(datetime.strptime(i,'%Y-%m-%dT%H').timetuple()) < mktime(datetime.strptime(highest_price_time,'%Y-%m-%dT%H').timetuple()):	# before peak price
				cap_p[i] = cur_avg_energy * ((1.1-j*0.1)+1 if j < 11 else 1) # decending from 200% for 2nd to 100% for 11th hour of the 7day average amount
			else:
				cap_p[i] = conf['max_inverter_power']	# maximum power, as there is no reason to keep energy back after the preak price
			
			if cap_p[i] > conf['max_inverter_power']: cap_p[i] = conf['max_inverter_power']
			cap_p[i] = '%.f'%(cap_p[i])
			j += 1
			lowest_price_timed = s_fupri[i]				# the lowest price with input
			if verbose: print('%s %.2f\t%s\t%i\t%i'%(i,s_fupri[i]*100,cap_p[i],cur_avg_energy,sum_p))
		else:			 
			cap_p[i] = '0'								# battery content was reached
			if debug: print('%s %.2f\t%i\t%s'%(i,s_fupri[i]*100,cur_avg_energy,cap_p[i]))
	
	price_avg = price_avg / len(future_prices) *100		# average price
	price_min = min(future_prices.values()) *100		# minimum price
	price_max = max(future_prices.values())	*100		# maximum price
	price_spread = (price_max-price_min)				# price spread
	price_lt = price_avg #- (price_spread * 0.5 )		# lower threshold - set the factor to your needs
	price_ut = price_avg - (price_spread * 0.1 )		# upper threshold - set the factor to your needs
	
	charge_bat_to_ac_eff = conf['bat_to_AC_efficiency'] * conf['AC_to_bat_efficiency'] * 0.01
	max_price_for_charge = lowest_price_timed * charge_bat_to_ac_eff - conf['battery_charge_profit']
	
	
	if verbose:	print('tibber price avg: %.2f'%price_avg,'min: %.2f'%(price_min),'max: %.2f'%(price_max),'spread: %.2f'%price_spread,'(%.f %%)'%(price_spread/price_max*100), \
						'lt: %.2f'%price_lt,'ht: %.2f'%price_ut,
						'\npvpt\t > %.2f ¢, %i%%lpt'%(lowest_price_timed*conf['bat_to_AC_efficiency'],conf['bat_to_AC_efficiency']),
						'\ncharge\t < %.2f ¢, %i%%lpt - %i ¢ profit'%(max_price_for_charge,charge_bat_to_ac_eff,conf['battery_charge_profit']))
	if price_lt > conf['timer_max_price']: 
		price_lt = conf['timer_max_price']
		if verbose: print('set timer lt to max: %.2f'%price_lt)
	
	if debug: print('lowest price timed %.2f'%(lowest_price_timed*100),'with',conf['bat_to_AC_efficiency'],'%% = %.2f'%(lowest_price_timed*conf['bat_to_AC_efficiency']))
	for i in future_prices:
		if conf['disable_pvpt'] or (lowest_price_timed*conf['bat_to_AC_efficiency'] > future_prices[i]*100 or future_prices[i]*100 < price_lt):
				pv_pt[i] = False
		else:	pv_pt[i] = True							# pass through PV power if the current price is higher than loss_of_load% of the lowest timed input price
		
		if debug: print(i,'%.2f'%(future_prices[i]*100),['<','>'][pv_pt[i]],'%.2f'%(lowest_price_timed*conf['bat_to_AC_efficiency']),['','PVpt'][pv_pt[i]])
	
	tib_hour_now = datetime.now().strftime('%Y-%m-%dT%H')
	
	timer_is_set =(len(conf['tasmota_dev'])+1)*[conf['disable_tasmota_timer']]	# True disables timers!
	
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
					
					if not timer_is_set[1]:
						if not 'T on' in msg: msg += ' T on:'
						
						if tasmota_timer( conf['tasmota_dev']['auto_on'],cur_timer,'3') == 200: msg += ' A1'; timer_is_set[1] = True
						else: msg += ' A1FAIL'
					
					if False: #not timer_is_set[5] and not pv_pt[cur_p_time]:			# don't charge the battery when inverter is active
						if not 'T on' in msg: msg += ' T on:'
						
						if tasmota_timer( conf['tasmota_dev']['charger_on'],cur_timer,'1') == 200: msg += ' 5'; timer_is_set[5] = True
						else: msg+= ' 5FAIL'
						
						if not verbose: syslog.syslog(syslog.LOG_INFO, msg +' at '+ cur_timer)
			
			else:										# middle and upper
				p_char = '|'
				
				if tib_hour_now == cur_p_time:
					
					if not timer_is_set[2]:
						if not 'T off' in msg: msg += 'T off:' 
						
						if tasmota_timer( conf['tasmota_dev']['auto_off'],cur_timer,'3') == 200: msg += ' A0'; timer_is_set[2] = True
						else: msg += ' A0FAIL'
					
					if False: #not timer_is_set[6]:
					
						if tasmota_timer( conf['tasmota_dev']['charger_off'],cur_timer,'0') == 200: msg += ' 6'; timer_is_set[6] = True
						else: msg += ' 6FAIL'
						if not verbose: syslog.syslog(syslog.LOG_INFO, msg +' at '+ cur_timer)
				
				if price_ut < cur_price:				# upper threshold
					p_char = '<'
		
		if verbose: print(str(msg).ljust(20),cur_p_time,'%2.2f %1s %4s %4s'%(cur_price,
			'¢' if max_price_for_charge > cur_price else ' ',
			(cap_p[cur_p_time] if calc_time else ' '),
			(['','PVpt'][pv_pt[cur_p_time]] if calc_time else '')),str(p_char).rjust(int(cur_price)))
	
	if conf['disable_zeroinput_timer']:
		if verbose: print('disabled zeroinput timer')
	else:
		with open(conf['timer.txt'],'w') as fo: # write a timer file for zeroinput
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
