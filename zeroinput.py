#!/usr/bin/python3
# -*- coding: utf-8 -*-
# indent size 4, mode Tabs
# Version: 1.13

import esmart	# https://raw.githubusercontent.com/E-t0m/esmart_mppt/master/esmart.py
import serial
from time import sleep, strftime, time
from datetime import timedelta, datetime
from copy import deepcopy
from sys import argv

# esmart charger devices, one by line. first port handles soyosource gti too.
esmarts	=	[	{'port':'/dev/ttyUSB1',	'name':'primary',	'temp_sensor_display':'bat'}, 
#				{'port':'/dev/ttyUSB2',	'name':'secondary',	'temp_sensor_display':'out'},
#				{'port':'/dev/ttyUSB3',	'name':'third',		'temp_sensor_display':'place'},
			]

# data pipe from vzlogger, set as log in /etc/vzlogger.conf, "verbosity": 15 required, use mkfifo to create it before vzlogger starts!
vzlogger_log_file	= '/tmp/vz/vzlogger.fifo'
persistent_vz_file	= '/var/log/vzlogger.log'

number_of_gti		= 1		# number of soyo gti units
max_gti_power		= 900	# W, the maximum power of one gti
max_bat_discharge	= 9999	# W, maximum power taken from the battery
max_night_input		= 9999	# W, maximum input power at night

zero_shift			= -2	# shift the power meters zero, 0 = disable, +x = export energy, -x = import energy
bat_voltage_const	= 0.18	# [V/kW] battery load/charge power, 0 = disable voltage correction
							# the battery voltage constant depends on the battery connection cable size and length
							# compare the displayed voltage with the BMS voltage for fine tuning of your equipment
pv_red_factor		= 79	# [%] PV reduction on low battery 
bat_power_static	= 15 * number_of_gti 	# W constant reduction on low battery

discharge_timer		= False	# True = enable or False = disable, stop and start discharging the battery controlled by timestamps in
discharge_t_file	= '/tmp/vz/timer.txt'
temp_alarm_enabled	= False	# True = enable or False = disable the alarm for the battery temperature
temp_alarm_interval	= 90	# seconds

# threshold and commands for temperature alarms
alarms =[	{'temp_int':45,	'int_cmd':'mpg321 /home/vzlogger/voice/regler.mp3 &',	'temp_ext':35,	'ext_cmd':'mpg321 /home/vzlogger/voice/akkubox.mp3 &'},
#			{'temp_int':45,	'int_cmd':'mpg321 /home/vzlogger/voice/regler.mp3 &',	'temp_ext':40,	'ext_cmd':'echo "heat outside" &'},
#			{'temp_int':45,	'int_cmd':'echo "command for internal temp alarm" &',	'temp_ext':35,	'ext_cmd':'echo "command for external temp alarm" &'}
		]

if '-test-alarm' in argv:
	print('test alarm command:')
	from os import system
	system(alarms[0]['int_cmd'])	# change to your needs
	exit(0)
elif '-v' in argv:
	verbose = True
	from os import system
	print("start", argv)
else: 
	verbose = False

if '-no-input' in argv:	no_input = True
else:					no_input = False

if '-debug' in argv:	debug = True
else:					debug = False

def handle_data(d):	# display the esmart data
	if not verbose: return
	battery_cur		= d['chg_cur'] - d['load_cur']
	battery_power	= d['chg_power'] - d['load_power']
	
	print("%s\t SOC %3i\t Mode %s"		% (str(d['name']).ljust(10),d['soc'],			esmart.DEVICE_MODE[d['chg_mode']]	))
	
	if d['chg_power']:
		print("PV\t %5.1f V\t %5.1f A\t %i W" 	% (d['pv_volt'],	d['chg_cur'],		d['chg_power']	))
	
	print("Battery\t %5.1f V\t %5.1f A\t %i W"	%(d['bat_volt'],	battery_cur,		battery_power	))
	
	if d['load_power']:
		print("Load\t %5.1f V\t %5.1f A\t %i W"	% (d['load_volt'],	d['load_cur'],		d['load_power']	))
	
	print("Temp\t int %i °C\t%s %i °C\n"		% (d['int_temp'],	d['ext_temp_name'],	d['ext_temp']	))

def combine_chargers(esm_chg):
	d = deepcopy(esm_chg[0]['dev'].fields)	# get primary esmart a values
	
	if esm_n == 1: return(d)
	
	for esm in esm_chg[1:]:
		e = esm['dev'].fields
		d['chg_power']	+= e['chg_power']	# add up charger values
		d['bat_volt']	+= e['bat_volt']
	
	d['load_power']	*= number_of_gti		# multiply for all gti, asuming one gti as load on gti 0
	d['bat_volt']	= round(d['bat_volt']/esm_n,1)
	return(d)

def set_soyo_demand(ser,power):	# create and send the packet for soyosource gti
	pu = power >> 8
	pl = power & 0xFF
	cs = 264 - pu - pl
	if cs > 255: 
		if power > 250:	cs -= 256
		else:			cs -= 255
	
	ser.write( bytearray([0x24,0x56,0x00,0x21,pu,pl,0x80,cs]) )
	ser.flush()
	return(0)

def close_values(a,b,tol):	# check if values a and b are within tolerance
	if a > b * (1 - 0.01*tol) and a < b *(1 + 0.01*tol): return(1)
	return(0)

def avg(inlist):	# return the average of a list variable
	if len(inlist) == 0: return(0)
	return( sum(inlist) / len(inlist) )

class discharge_times():
	def __init__(self):
		self.interval = 60	# seconds
		self.stamp	= datetime.now() - timedelta(seconds = self.interval)
		self.active		= False
		self.discharge	= True
		self.update()
	
	def update(self):
		if self.stamp + timedelta(seconds = self.interval) < datetime.now():
			self.stamp	= datetime.now()
			times = []; states = []
			try:
				with open(discharge_t_file,'r') as fi:
					for i in fi:
						times.append(datetime.strptime(i[:19], '%Y-%m-%d %H:%M:%S'))
						states.append(str(i[19:]))
				
				for i in range(0,len(times)):
					self.active = True	# successful file read
					if times[i] < datetime.now(): 
						self.discharge = True if 'on' in states[i] else False
					else: break
			except:
				self.active		= False	# indicates a invalid timer file!
				self.discharge	= False

max_input_power	= max_gti_power * number_of_gti
send_power		= 0
last_send		= 0
last2_send		= 0
ramp_cnt		= 0
ramp_power		= 0
last_runtime	= 0
bat_cont		= 0
pv_cont			= 0
adjusted_power	= False
bat_history		= [0]* 5	# history vars with *n interval steps
pv_history		= [0]* 20
extra_history	= [0]* 8
send_history	= [0]* 4

temp_ext_alarm_time	= datetime.now()
temp_int_alarm_time	= datetime.now()
timeout_repeat		= datetime.now()
vz_in				= open(vzlogger_log_file,'r')
esm_n = len(esmarts)
if discharge_timer: timer = discharge_times()

if verbose: print('zeroinput starts\n')
for i in esmarts:
	i['dev'] = esmart.esmart()
	i['dev'].set_name(i['name'])
	i['dev'].set_ext_temp_name(i['temp_sensor_display'])
	i['dev'].set_callback(handle_data)
	i['dev'].open(i['port'])
	i['dev'].tick()
	sleep(0.3)
	i['dev'].tick()
	if verbose:	print('.')

d = combine_chargers(esmarts)	# initially combine chargers data to one general
esmarts[0]['dev'].close()	# only close the primary esmart interface

while True:		# infinite loop, stop the script with ctl+c
	
	main_log = False; Ls_read = 99999; Ls_ts = 99999
	last2_send	= last_send		# dedicated history
	last_send	= send_power	# variables
	block_saw_detection = False	# allow saw detection
	
	while True:		# loop over vzlogger.log fifo
		if debug: print('reading vz ts:',Ls_ts)
		
		l = vz_in.readline()
		if '[main] vzlogger' in l: 
			main_log = True
			vzout = open(persistent_vz_file,'a')
			vzout.write('REDIRECTED by zeroinput.py from'+ vzlogger_log_file +'\n')
			if verbose: print('\nvzlogger restart')
		elif 'Startup done.' in l:
			main_log = False
			vzout.close()
		if main_log: vzout.write(l)
		
		if "1-0:16.7.0" in l:	# read the sum L1+L2+L3, can be negative
			try: Ls_read = int( round( float( l[l.index('value=')+6:-1+l.index('ts=')]) ,4) )
			except: pass
			else:
				try: Ls_ts = int( l[l.index('ts=')+3:-1] )
				except: pass
		
		if Ls_read != 99999 and Ls_ts !=99999:	# check if Ls has input and timestamp
		
			# if the reading is older than 1 second, continue reading the vzlogger data
			if abs( int(str(time())[:10]) - int(str(Ls_ts)[:10]) ) > 1: continue
			
			break	# stop reading the vzlogger pipe
		sleep(0.001)
		
	if verbose:
		system('clear')
		print('%s, voltage' % (strftime("%H:%M:%S")),d['bat_volt'],'V, PV power',d['chg_power'],'W, load power',d['load_power'], \
				'W\n' if not discharge_timer else 'W, discharge '+['off','on'][timer.discharge]+'\n')
		if discharge_timer and not timer.active: print('discharge timer enabled but not active! no valid timestamp file set?\n')
	send_power = int( Ls_read + last2_send + zero_shift )
	
	# high change of power consumption, on rise: no active power limitation, sufficient bat_voltage
	if (Ls_read < -400) or (Ls_read > 400 and not adjusted_power and bat_cont > 51.0):
		if	ramp_cnt == 0:
			ramp_cnt = 6										# in script cycles
			ramp_power = send_power
	
	if ramp_cnt > 0:											# within ramp countdown
		block_saw_detection = True								# disable saw detection
		send_power = ramp_power
		if verbose: print('ramp mode %i'%ramp_cnt)
		ramp_cnt -= 1

	status_text = ''
	
	if bat_voltage_const != 0:									# battery voltage correction
		battery_power = d['chg_power'] - (d['load_power'] * number_of_gti)
		bat_corr = round(0.001 * battery_power * bat_voltage_const, 1)
		bat_history = bat_history[1:] + [d['bat_volt'] - bat_corr]
		if verbose: 
			if bat_corr: print('voltage correction',round(bat_history[-1],1),'V, dif',bat_corr,'V')
	else:
		bat_history = bat_history[1:] + [d['bat_volt']]
	
	if 0 in bat_history:	bat_cont = d['bat_volt']
	else:					bat_cont = avg(bat_history)			# average of the previous battery voltages
	
	if debug: print('bat_history\t',bat_history,'\nbat_cont\t',bat_cont)
	
	pv_history = pv_history[1:]+ [d['chg_power']]
	sort_pv = pv_history[:]
	sort_pv.sort()
	pv_cont = int(avg(sort_pv[-5:]))	# average on high pass of the PV power, to remove the gap on mppt tracker restart
	if debug: print('pv_history\t', pv_history,'\nsort_pv\t\t',sort_pv,'\npv_cont\t\t',pv_cont)
	
	if datetime.now() < timeout_repeat:		# battery protection timeout
		send_power = 0
		if verbose: print('battery protection timeout until', timeout_repeat.strftime('%H:%M:%S'))
			
	else:
		adjusted_power = False
		if no_input:		# disabled power input by command line option
			send_power 		= 0
		
		elif bat_cont < 48:	# set a new timeout
			adjusted_power = True
			send_power		= 0
			send_history	= [0]*4
			extra_history	= [0]*8
			timeout_repeat = datetime.now() + timedelta(minutes = 1)	# wait a while
		
		elif discharge_timer and not timer.discharge:	# disable battery discharge
			if send_power > int(pv_cont*pv_red_factor*0.01):
				send_power = int(pv_cont*pv_red_factor*0.01)
				adjusted_power = True
				if verbose and pv_cont: 
					if pv_cont != 0:		status_text	+=	'limited, PV %i%%,' % pv_red_factor+' no battery discharge '
					
				
		elif bat_cont >= 48 and bat_cont <= 50:		# limit to pv power, by battery voltage
			if send_power > d['chg_power']:
				# variant A
				bat_p_percent = (bat_cont - 46.86 ) **4.025
				bat_power = int(0.01 * max_input_power * bat_p_percent)	# 100% above 50 V
				
				# variant B, with a given powercurve
				#powercurve = [0,2,3,4,5,6,7,13,17,21,26,30,34,39,45,52,60,70,80,90,100] # in %
				#bat_p_percent = powercurve[int(bat_cont*10-480)]
				#bat_power = int(0.01 * max_input_power * bat_p_percent)	# 100% above 50 V
				
				extra_history = extra_history[1:]+[bat_power]
				if 0 in extra_history:	pass		# bat_power remains at the latest calculated value
				else: 					bat_power = avg(extra_history)
				
				if verbose:
					status_text = ''
					if d['chg_power'] != 0:		status_text	+=	', PV %i W (%i%%)' % (d['chg_power']*pv_red_factor*0.01, pv_red_factor)
					if bat_power != 0:			status_text	+=	', Bat %i W (%.1f%%)' % (bat_power, bat_p_percent)
					if bat_power_static != 0 and pv_cont:	status_text	+=	', static %i W' % -bat_power_static
				
				if	send_power > int( d['chg_power']*pv_red_factor*0.01 + bat_power - (bat_power_static if pv_cont else 0)):
					send_power = int( d['chg_power']*pv_red_factor*0.01 + bat_power - (bat_power_static if pv_cont else 0))
					adjusted_power = True
					if verbose: 				status_text	=	' limited' + status_text
		
		if pv_cont == 0 and send_power > max_night_input: 		# night limit
			send_power = max_night_input
			adjusted_power = True
			if verbose: status_text += 'night limit'
		
		if pv_cont != 0 and 		bat_cont > 53.0:			# give some free power to the world = pull down the zero line
				free_power = int((	bat_cont - 53.0)*10 *0.2)	# 0.2 W / 0.1 V, max depends on esmart "saturation charging voltage"
				send_power += free_power
				if verbose and free_power > 0: status_text += 'export by voltage %i W' % free_power
		else:	free_power = 0
		
		if send_power > 		max_bat_discharge + d['chg_power']:		# battery discharge limit
			send_power = int(	max_bat_discharge + d['chg_power'])
			adjusted_power = True
			if verbose: status_text += 'battery current limit'
		
		send_history = send_history[1:]+[send_power]			# build a send_power history
		
		if verbose:	# show saw tooth values
			print(	'input history', send_history, '\t 1:2 %.1f %%\t3:4 %.1f %%' % 
					 (round((1-(send_history[-1] / (0.01+send_history[-2])))*100,1), round((1-(send_history[-3] / (0.01+send_history[-4])))*100,1) ) )
		
		if block_saw_detection:
			if verbose: print('\tdisabled saw detection')
		else:
			if not close_values(send_history[-1],send_history[-2],3) and not close_values(send_history[-3],send_history[-4],3):
				send_power = int(avg(send_history))					# break the swing up by using the average
				if verbose: print('saw stop',send_power)
				send_history[-1] = send_power
			else:
				if verbose: print('no saw detected')
		
		if send_power	< 10:	# keep it positive with a little gap on bottom
			send_power	= 0		# disable input
			adjusted_power = True
			status_text	+= 'MIN power limit'
			
		if send_power	> max_input_power:		# the limit of the gti
			send_power	= max_input_power
			adjusted_power = True
			status_text	+= 'MAX power limit'
	
	with open('/tmp/vz/soyo.log','w') as fo:	# send some values to vzlogger
		fo.write('%i: soyosend = %i\n'	% ( time(),	-send_power ) )		# the keywords have to be created 
		fo.write('%i: pv_w = %i\n'		% ( time(),	-d['chg_power'] ) )	# as channels in vzlogger to make it work there!
		fo.write('%i: panel_w = %i\n'	% ( time(),	-free_power ) )
		fo.write('%i: bat_v = %f\n'		% ( time(),	bat_cont ) )
		fo.write('%i: pv_wa = %i\n'		% ( time(),	-esmarts[0]['dev'].fields['chg_power'] ) )
#		fo.write('%i: pv_wb = %i\n'		% ( time(),	-esmarts[1]['dev'].fields['chg_power'] ) )
#		fo.write('%i: pv_u = %f\n'		% ( time(),	esmarts[1]['dev'].fields['pv_volt'] ) )
		fo.write('%i: bat_temp = %i\n'	% ( time(),	esmarts[0]['dev'].fields['ext_temp'] ) )
#		fo.write('%i: out_temp = %i\n'	% ( time(),	esmarts[1]['dev'].fields['ext_temp'] ) )
		fo.write('%i: int_temp = %i\n'	% ( time(),	esmarts[0]['dev'].fields['int_temp'] ) )

	if verbose: 
		print('\ninterval %.2f s, '	% (time()-last_runtime),end='')
		if 		zero_shift == 0:	print('meter %i W'				% (Ls_read) )					# show the meter readings, and zero shift
		elif	zero_shift  > 0:	print('meter %i W (%i W export)'% (Ls_read,abs(zero_shift)) )
		else:						print('meter %i W (%i W import)'% (Ls_read,abs(zero_shift)) )
		print('input %i W %s'	% (send_power, status_text))	# show the input data
		if no_input: print('input DISABLED by command line')
	
	if temp_alarm_enabled:
		for i in range(0,esm_n):
			if esmarts[i]['dev'].fields['int_temp'] > alarms[i]['temp_int']:
				if verbose: print('\nTEMPERATURE ALARM internal esmart',esmarts[i]['name'],esmarts[i]['dev'].fields['int_temp'],'°C')
				if temp_int_alarm_time + timedelta(seconds = temp_alarm_interval) < datetime.now():
					temp_int_alarm_time = datetime.now()
					system(alarms[i]['int_cmd'])
			
			if esmarts[i]['dev'].fields['ext_temp'] > alarms[i]['temp_ext']:
				if verbose: print('\nTEMPERATURE ALARM external esmart',esmarts[i]['name'],esmarts[i]['dev'].fields['ext_temp'],'°C')
				if temp_ext_alarm_time + timedelta(seconds = temp_alarm_interval) < datetime.now():
					temp_ext_alarm_time = datetime.now()
					system(alarms[i]['ext_cmd'])
	
	last_runtime = time()
	ser = serial.Serial(esmarts[0]['port'], 4800)

	for i in [1,2]:	# poll 2 times
		if send_power != 0:
			set_soyo_demand(ser,int(1.0 * send_power / number_of_gti))
			if verbose: print('%i soyo'%i)
		elif verbose: print('. soyo')		# dont send, but sleep
		sleep(0.20)
	
	ser.close()
	
	esmarts[0]['dev'].open(esmarts[0]['port'])	# open the primary esmart
	
	for i in [1,2]:	# poll 2 times
		if datetime.now() > timeout_repeat or pv_cont != 0: # after battery protection timeout or at day time
			for charger in esmarts:
				charger['dev'].tick()
			
			if verbose:	print('%i eSmart3'%i)
		elif verbose: 	print('. eSmart3')	# don't send but sleep
		if i < esm_n: 	sleep(0.35)	# don't wait after last tick
	
	esmarts[0]['dev'].close()
	
	d = combine_chargers(esmarts)		# combine chargers data to one general
	
	if discharge_timer: timer.update()
	if debug: sleep(1)

