#!/usr/bin/python3
# -*- coding: utf-8 -*-

serial_port		= '/dev/rs485'
# data pipe from vzlogger, set as log in /etc/vzlogger.conf, "verbosity": 15 required, use mkfifo to create it before vz starts!
vzlogger_log_file	= '/home/vzlogger/vzlogger.fifo'
persistent_vz_file	= '/var/log/vzlogger.log'
number_of_gti		= 2

from sys import argv

if '-v' in argv:
	verbose = True
	from os import system
	print("start", argv)
else: 
	verbose = False

import esmart
import serial
from time import sleep, strftime, time
from datetime import timedelta, datetime

def handle_data(d):	# display the esmart data
	if not verbose: return
	battery_cur	= d['chg_cur'] - d['load_cur']
	battery_power	= d['chg_power'] - d['load_power']
	system("clear")
	print("%s\t SOC %i\t Mode %s"			% (strftime("%H:%M:%S"),		d['soc'],	esmart.DEVICE_MODE[d['chg_mode']]))
	print("PV\t %.1f V\t %.1f A\t %i W" 		% (d['pv_volt'],			d['chg_cur'],	d['chg_power']))
	print("Battery\t %.1f V\t %.1f A\t %i W"	% (d['bat_volt'],			battery_cur,	battery_power))
	print("Load\t %.1f V\t %.1f A\t %i W"		% (d['load_volt'],			d['load_cur'],	d['load_power']))
	print("Temp\t int %i °C\tBat %i °C"			% (d['int_temp'],			d['bat_temp']))

def set_soyo_demand(ser,power):	# create the packet for soyosource gti
	pu = power >> 8
	pl = power & 0xFF
	cs = 264 - pu - pl
	if cs >= 256: cs = 8
	
	ser.write( bytearray([0x24,0x56,0x00,0x21,pu,pl,0x80,cs]) )
	ser.flush()
	return(0)

def close_values(a,b,tol): # check if values a and b are within tolerance
	if a > b * (1 - 0.01*tol) and a < b *(1 + 0.01*tol): return(1)
	return(0)

def avg(inlist):	# return the average of a list variable
	if len(inlist) == 0: return(0)
	return( sum(inlist) / len(inlist) )

send_power	= 0
last_send	= 0
last_runtime 	= 0
bat_cont	= 0
bat_history	= [0]*8	# history vars with *n interval steps
extra_history	= [0]*8
send_history	= [0]*4
pv_red		= 0.85	# PV reduction on low battery in % / 100
powercurve	= [1,3,5,8,10,12,14,17,20,23,26,30,34,39,45,51,60,70,80,90,100]	# in %

timeout_repeat	= datetime.now()
vz_in		= open(vzlogger_log_file,'r')
esm		= esmart.esmart()
esm.set_callback(handle_data)

while True:	# infinite loop, stop the script with ctl+c
		esm.open(serial_port)	# prepare to read from esmart
		if verbose: print('esmart tick')
		for i in range(1,3):	# poll 2 times
			if verbose: print(i)
			esm.tick()
			sleep(0.3)
		esm.close()
		d = esm.export()	# get esmart values
		

		main_log = False; Ls_read = 99999; Ls_ts = 99999
		last2_send = last_send; last_send = send_power
		
		while True:	# loop over vzlogger.log fifo
			l = vz_in.readline()
			
			if '[main] vzlogger' in l: 
				main_log = True
				vzout = open(persistent_vz_file,'a')
				vzout.write('REDIRECTED by zeroinput.py from /home/vzlogger/vzlogger.fifo\n')
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
		
		send_power = int( Ls_read + last2_send )	# underpower by conversion efficiency is measured with the readings
		
		status_text = ''
		bat_history = bat_history[1:] + [ d['bat_volt']]
		if 0 in bat_history: bat_cont = d['bat_volt']
		else: bat_cont = avg(bat_history)
		
		if datetime.now() < timeout_repeat:	# battery protection timeout
			send_power = 0
			if verbose: print('\nbattery protection timeout until', timeout_repeat.strftime('%H:%M:%S'))

		else:
			if bat_cont < 48:	# set a new timeout
				send_power 	= 0
				send_history	= [0]*4
				extra_history	= [0]*8
				timeout_repeat = datetime.now() + timedelta(minutes=1) 	# wait a minute
			
			elif bat_cont >= 48 and bat_cont <= 50:	# limit to pv power, by battery voltage
				if send_power > d['chg_power']:
					bat_p_index = int(bat_cont*10-480)
					bat_power = int(powercurve[ bat_p_index ] * 12)	# maximum 1200 W at 50 V
					extra_history = extra_history[1:]+[bat_power]
					if 0 in extra_history: pass	# bat_power remains at last calculated value
					else: bat_power = avg(extra_history)
					
					if verbose: status_text	= ' PV %i W (%i%%), bat %i W (%i%%), -20 W' % (int(d['chg_power']*pv_red), int(pv_red*100), bat_power, powercurve[bat_p_index])
					
					if	send_power > int( d['chg_power']*pv_red + bat_power ):
						send_power = int( d['chg_power']*pv_red + bat_power -20 )	# lower the input 20 W for slow fade
						if verbose: status_text =' limited ' + status_text
			
			if d['chg_power'] == 0 and send_power > 200: # night limit
				send_power = 200
				if verbose: status_text = 'night limit'
			
			if bat_cont > 55.5:	# give some free power to the world if bat > 55.5 V
				free_power = int((bat_cont - 55.5)*10 *50)	# 50 W / 0.1 V, max total 750 W
				send_power += free_power
				if verbose: status_text = 'over export '+str(free_power)+' W'
			else: free_power = 0
			
			if send_power > 1200 + d['chg_power']:	# battery discharge limit 1200 W
				send_power = int(1200 + d['chg_power'])
				if verbose: status_text	= 'battery current limit'
			
			send_history = send_history[1:]+[send_power]	# build a send_power history
			
			if verbose: print(	'\ninput history', send_history,	# show saw tooth values
						'\n\t1/2 ', round((1-(send_history[-1] / (0.01+send_history[-2])))*100,1),'%',
						'\n\t3/4 ', round((1-(send_history[-3] / (0.01+send_history[-4])))*100,1),'%')
			
			if not close_values(send_history[-1],send_history[-2],4) and not close_values(send_history[-3],send_history[-4],4):
				send_power = int(avg(send_history))
				if verbose: print('\tsaw stop',send_power)
				send_history[-1] = send_power
			else:
				if verbose: print('\tno saw detected')
							
			if send_power < 6:	# keep it positive with a little gap on bottom
				send_power = 0	# disable input
				status_text = 'MIN power limit'
				
			if send_power	> 900 * number_of_gti:	# the limit of the gti
				send_power = 900 * number_of_gti
				status_text = 'MAX power limit'
			
		with open('/tmp/vz/soyo.log','w') as fo:	# send some values to vzlogger
			fo.write('%i: soyosend = %i\n'		% ( time(), -1*send_power ) )	# the keywords have to be created 
			fo.write('%i: pv_w = %i\n'		% ( time(), -1*d['chg_power'] ) )	# as channels in vzlogger to make it work there!
			fo.write('%i: pv_u = %f\n'		% ( time(), d['pv_volt']  ) )
			fo.write('%i: bat_v = %f\n'		% ( time(), d['bat_volt'] ) )
			fo.write('%i: int_temp = %i\n'	% ( time(), d['int_temp'] ) )
			fo.write('%i: bat_temp = %i\n'	% ( time(), d['bat_temp'] ) )
			fo.write('%i: panel_w = %i\n'	% ( time(), -1*free_power ) )
		
		if verbose: 
				print('interval %.2f s'	% (time()-last_runtime))
				print('meter %i W' 	% (Ls_read))	# show the meter readings
				print('input %i W %s'	% (send_power, status_text))	# show the input data
		
		last_runtime = time()
		ser = serial.Serial(serial_port, 4800)
		
		for i in range(1,3):	# poll 2 times
			set_soyo_demand(ser,int(1.0 * send_power / number_of_gti))
			if verbose: print(i)
			sleep(0.15)
		
		ser.close()

