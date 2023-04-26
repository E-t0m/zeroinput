#!/usr/bin/python3
# -*- coding: utf-8 -*-
# indent size 4, mode Tabs

serial_port			= '/dev/rs485'

# data pipe from vzlogger, set as log in /etc/vzlogger.conf, "verbosity": 15 required, use mkfifo to create it before vzlogger starts!
vzlogger_log_file	= '/tmp/vz/vzlogger.fifo'
persistent_vz_file	= '/var/log/vzlogger.log'

number_of_gti		= 1		# number of soyo gti units
max_gti_power		= 900	# W, the maximum power of one gti
max_bat_discharge	= 600	# W, maximum power taken from the battery
max_night_input		= 300	# W, maximum input power at night

zero_shift			= -2 	# shift the power meters zero, 0 = disable, +x = export energy, -x = import energy
bat_voltage_const	= 0.77	# V/kW battery load/charge current, 0 = disable voltage correction
							# the battery voltage constant depends on the battery connection cable size and length
							# compare eSmart3 battery voltage with BMS the voltage

temp_alarm_enabled = True	# True = enable or False = disable the alarm for the battery temperature
temp_int_alarm_threshold	= 45 # °C
temp_bat_alarm_threshold	= 40 # °C
temp_alarm_interval			= 30 # seconds
temp_int_alarm_command = 'echo internal_temp_alarm'		# execute this command on interal temperature alarm
temp_bat_alarm_command = 'echo battery_temp_alarm'		# execute this command on battery temperature alarm

from sys import argv

if '-test-alarm' in argv:
	print('test alarm command:')
	from os import system
	system(temp_alarm_command)
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

import esmart	# https://github.com/E-t0m/esmart_mppt/blob/ed1e1d91912831a1ee5f26eaf59ace57098c4eac/esmart.py
import serial
from time import sleep, strftime, time
from datetime import timedelta, datetime

def handle_data(d):	# display the esmart data
	if not verbose: return
	battery_cur		= d['chg_cur'] - (d['load_cur']*number_of_gti)
	battery_power	= d['chg_power'] - (d['load_power']*number_of_gti)
	system("clear")
	print("%s\t\t SOC %3i\t Mode %s"			% (strftime("%H:%M:%S"),	d['soc'],		esmart.DEVICE_MODE[d['chg_mode']]))
	print("PV\t %5.1f V\t %5.1f A\t %i W" 		% (d['pv_volt'],			d['chg_cur'],	d['chg_power']))
	print("Battery\t %5.1f V\t %5.1f A\t %i W"	% (d['bat_volt'],			battery_cur,	battery_power))
	print("Load\t %5.1f V\t %5.1f A\t %i W"		% (d['load_volt'],			d['load_cur']*number_of_gti,	d['load_power']*number_of_gti))
	print("Temp\t int %i °C\tBat %i °C"			% (d['int_temp'],			d['bat_temp']))

def set_soyo_demand(ser,power):	# create the packet for soyosource gti
	pu = power >> 8
	pl = power & 0xFF
	cs = 264 - pu - pl
	if cs >= 256: cs = 8
	
	ser.write( bytearray([0x24,0x56,0x00,0x21,pu,pl,0x80,cs]) )
	ser.flush()
	return(0)

def close_values(a,b,tol):	# check if values a and b are within tolerance
	if a > b * (1 - 0.01*tol) and a < b *(1 + 0.01*tol): return(1)
	return(0)

def avg(inlist):	# return the average of a list variable
	if len(inlist) == 0: return(0)
	return( sum(inlist) / len(inlist) )

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
pv_history		= [0]* 24
extra_history	= [0]* 8
send_history	= [0]* 4

bat_power_static	= -20	# W static reduction on low battery
pv_red_factor		= 0.85	# PV reduction on low battery in % / 100
max_input_power		= max_gti_power * number_of_gti

temp_bat_alarm_time	= datetime.now()
temp_int_alarm_time	= datetime.now()
timeout_repeat		= datetime.now()
vz_in				= open(vzlogger_log_file,'r')
esm					= esmart.esmart()
esm.set_callback(handle_data)

while True:		# infinite loop, stop the script with ctl+c
	esm.open(serial_port)	# prepare to read from esmart
	for i in [1,2]:	# poll 2 times
		if datetime.now() > timeout_repeat or pv_cont != 0: # after battery protection timeout or at day time
			esm.tick()	# request data from esmart3
			if verbose:	print('%i eSmart3'%i)
		elif verbose: 	print('. eSmart3')	# don't send but sleep
		sleep(0.3)
	
	esm.close()
	d = esm.export()	# get esmart values
	
	if temp_alarm_enabled:
		if d['bat_temp'] > temp_bat_alarm_threshold:
			if verbose: print('\nTEMPERATURE ALARM battery:',d['bat_temp'],'°C')
			if temp_bat_alarm_time + timedelta(seconds = temp_alarm_interval) < datetime.now():
				system(temp_bat_alarm_command)
				if verbose: print('\nTEMPERATURE ALARM: command sent')
				temp_bat_alarm_time = datetime.now()
	
		if d['int_temp'] > temp_int_alarm_threshold:
			if verbose: print('\nTEMPERATURE ALARM internal:',d['int_temp'],'°C')
			if temp_int_alarm_time + timedelta(seconds = temp_alarm_interval) < datetime.now():
				system(temp_int_alarm_command)
				if verbose: print('\nTEMPERATURE ALARM: command sent')
				temp_int_alarm_time = datetime.now()
	
	main_log = False; Ls_read = 99999; Ls_ts = 99999
	last2_send	= last_send		# dedicated history
	last_send	= send_power	# variables
	block_saw_detection = False	# enable saw detection
	
	while True:		# loop over vzlogger.log fifo
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
			if abs( int(str(time())[:10]) - int(str(Ls_ts)[:10]) ) > 1:	continue
			
			break	# stop reading the vzlogger pipe
	
	send_power = int( Ls_read + last2_send + zero_shift )	# underpower by conversion efficiency is measured with the readings
	
	# high change of power consumption, on rise: no active power limitation, sufficient bat_voltage
	if (Ls_read < -400) or (Ls_read > 400 and not adjusted_power and bat_cont > 51.0):
		if	ramp_cnt == 0:
			ramp_cnt = 5										# in script cycles
			ramp_power = send_power
	
	if ramp_cnt > 0:											# within ramp countdown
		block_saw_detection = True								# disable saw detection
		send_power = ramp_power
		if verbose: print('\nramp mode %i'%ramp_cnt)
		ramp_cnt -= 1

	status_text = ''
	
	if bat_voltage_const != 0:									# battery voltage correction
		battery_power = d['chg_power'] - (d['load_power'] * number_of_gti)
		bat_corr = round(0.001 * battery_power * bat_voltage_const, 1)
		bat_history = bat_history[1:] + [d['bat_volt'] - bat_corr]
		if verbose: print('\nvoltage correction',round(bat_history[-1],1),'dif',bat_corr,'V')
	else:
		bat_history = bat_history[1:] + [d['bat_volt']]
	
	if 0 in bat_history:	bat_cont = d['bat_volt']
	else:					bat_cont = avg(bat_history)			# average of the previous battery voltages
	
	if debug: print('bat_history\t',bat_history,'\nbat_cont\t',bat_cont)
	
	pv_history = pv_history[1:]+ [d['chg_power']]
	sort_pv = pv_history[:]
	sort_pv.sort()
	pv_cont = int(avg(sort_pv[-6:]))	# average on high pass of the PV power, to remove the gap on mppt tracker restart
	if debug: print('pv_history\t', pv_history,'\nsort_pv\t\t',sort_pv,'\npv_cont\t\t',pv_cont)
	
	if datetime.now() < timeout_repeat:		# battery protection timeout
		send_power = 0
		if verbose: 
			system('clear')
			print(strftime("%H:%M:%S"),'\nbattery protection timeout until', timeout_repeat.strftime('%H:%M:%S'))
			print('latest battery data:',d['bat_volt'],'V')
	else:
		adjusted_power = False
		if no_input:		# disabled power input by command line option
			send_power 		= 0
		
		elif bat_cont < 48:	# set a new timeout
			adjusted_power = True
			send_power		= 0
			send_history	= [0]*4
			extra_history	= [0]*8
			timeout_repeat = datetime.now() + timedelta(minutes=1) 	# wait a minute
		
		elif bat_cont >= 48 and bat_cont <= 50:		# limit to pv power, by battery voltage
			if send_power > pv_cont:
				# variant A
				bat_p_percent = (bat_cont - 47.1 ) **4.326				# curve without steps, uses the higher precision of the bat_cont float variable
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
					if pv_cont != 0:			status_text	+=	', PV %i W (%i%%)' % (int(pv_cont*pv_red_factor), int(pv_red_factor*100))
					if bat_power != 0:			status_text	+=	', bat %i W (%.1f%%)' % (bat_power, bat_p_percent)
					if bat_power_static != 0:	status_text	+=	', static %i W' % bat_power_static
				
				if	send_power > int( pv_cont*pv_red_factor + bat_power + bat_power_static):
					send_power = int( pv_cont*pv_red_factor + bat_power + bat_power_static)
					adjusted_power = True
					if verbose: 				status_text	=	' limited' + status_text
		
		if pv_cont == 0 and send_power > max_night_input: 		# night limit
			send_power = max_night_input
			adjusted_power = True
			if verbose: status_text = 'night limit'
		
		if pv_cont != 0 and 		bat_cont > 53.0:			# give some free power to the world = pull down the zero line
				free_power = int((	bat_cont - 53.0)*10 *0.5)	# 0.5 W / 0.1 V, max depends on esmart "saturation charging voltage"
				send_power += free_power
				if verbose: status_text = 'export '+str(free_power)+' W'
		else:	free_power = 0
		
		if send_power > 		max_bat_discharge + d['chg_power']:		# battery discharge limit
			send_power = int(	max_bat_discharge + d['chg_power'])
			adjusted_power = True
			if verbose: status_text	= 'battery current limit'
		
		send_history = send_history[1:]+[send_power]			# build a send_power history
		
		if verbose: 
			print(	'input history', send_history,			# show saw tooth values
					'\n\t1/2 ', round((1-(send_history[-1] / (0.01+send_history[-2])))*100,1),'%',
					'\n\t3/4 ', round((1-(send_history[-3] / (0.01+send_history[-4])))*100,1),'%')
		
		if block_saw_detection:
			if verbose: print('\tdisabled saw detection')
		else:
			if not close_values(send_history[-1],send_history[-2],3) and not close_values(send_history[-3],send_history[-4],3):
				send_power = int(avg(send_history))					# break the swing up by using the average
				if verbose: print('\tsaw stop',send_power)
				send_history[-1] = send_power
			else:
				if verbose: print('\tno saw detected')
		
		if send_power	< 10:	# keep it positive with a little gap on bottom
			send_power	= 0		# disable input
			adjusted_power = True
			status_text	= 'MIN power limit'
			
		if send_power	> max_input_power:		# the limit of the gti
			send_power	= max_input_power
			adjusted_power = True
			status_text	= 'MAX power limit'
		
	with open('/tmp/vz/soyo.log','w') as fo:	# send some values to vzlogger
		fo.write('%i: soyosend = %i\n'	% ( time(), -1*send_power ) )		# the keywords have to be created 
		fo.write('%i: pv_w = %i\n'		% ( time(), -1*d['chg_power'] ) )	# as channels in vzlogger to make it work there!
		fo.write('%i: pv_u = %f\n'		% ( time(), d['pv_volt']  ) )
		fo.write('%i: bat_v = %f\n'		% ( time(), bat_cont ) ) # d['bat_volt'] ) )
		fo.write('%i: int_temp = %i\n'	% ( time(), d['int_temp'] ) )
		fo.write('%i: bat_temp = %i\n'	% ( time(), d['bat_temp'] ) )
		fo.write('%i: panel_w = %i\n'	% ( time(), -1*free_power ) )
	
	if verbose: 
		print('\ninterval %.2f s'	% (time()-last_runtime))
		print('meter %i W'		% (Ls_read), '' if zero_shift == 0 else '(%i W zero shift)' %(zero_shift))	# show the meter readings, and zero shift
		print('input %i W %s'	% (send_power, status_text))	# show the input data
		if no_input: print('input DISABLED by command line')
	
	last_runtime = time()
	ser = serial.Serial(serial_port, 4800)
	
	for i in [1,2]:	# poll 2 times
		if send_power != 0:
			set_soyo_demand(ser,int(1.0 * send_power / number_of_gti))
			if verbose: print('%i soyo'%i)
		elif verbose: print('. soyo')		# dont send, but sleep
		
		sleep(0.15)
	
	ser.close()

