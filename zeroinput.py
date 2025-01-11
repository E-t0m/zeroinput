#!/usr/bin/python3
# -*- coding: utf-8 -*-
# indent size 4, mode Tabs

import esmart	# https://raw.githubusercontent.com/E-t0m/esmart_mppt/master/esmart.py
import serial
from time import sleep, strftime, time
from datetime import timedelta, datetime
from copy import deepcopy
from sys import argv

# data pipe from vzlogger, set as log in /etc/vzlogger.conf, "verbosity": 15 required, use mkfifo to create it before vzlogger starts!
vzlogger_log_file	= '/tmp/vz/vzlogger.fifo'
persistent_vz_file	= '/var/log/vzlogger.log'

# rs485 ports for mppt chargers and Soyosource GTI
rs485_ports =	[	'/dev/rs485a',
					'/dev/rs485b',
#					'/dev/rs485c',		# as much you need
				]

# esmart charger devices, one by line.
esmarts	=	[	{'port':rs485_ports[0],	'name':'esmart 40',	'temp_sensor_display':'bat'}, 
#				{'port':rs485_ports[1],	'name':'esmart 60',	'temp_sensor_display':'out'},
#				{'port':rs485_ports[2],	'name':'esmart x',	'temp_sensor_display':'place'},		# as much you need
			]

number_of_gti			= 1		# number of inverters
awo_gti_port			= 0		# port of the inverter thats always on | as in rs485_ports: first index is 0, like in "esmarts" above
single_dev_threshold	= 600	# W, threshold to switch between single and multiple soyosource inverters
multi_dev_wait			= 90	# s, wait before switch back to single inverter

conf_max_gti_power		= 900	# W, the maximum power of one gti, usable as power limit
conf_max_bat_discharge	= 900 * number_of_gti	# W, maximum power taken from the battery

conf_zero_shift			= 0		# shift the power meters zero, +x = export energy, -x = import energy, 0 = automatic leveling based 0

PV_to_AC_efficiency		= 89	# %
bat_voltage_const		= 0.19	# V/kW, battery load/charge power, 0 = disable voltage correction
								# the battery voltage constant depends on the battery connection cable size and length
								# compare the displayed voltage with the BMS voltage for fine tuning of your equipment

discharge_timer			= False	# True = enable or False = disable, intended discharging of the battery, controlled by timestamps in:
discharge_t_file		= '/home/vzlogger/timer.txt'

temp_alarm_enabled		= False	# True = enable or False = disable the alarm for the battery temperature
temp_alarm_interval		= 90	# seconds

# threshold and commands for temperature alarms, same order as in "esmarts" above
alarms =[	{'temp_int':45,	'int_cmd':'mpg321 /home/vzlogger/voice/regler.mp3 &',	'temp_ext':35,	'ext_cmd':'./alarm_akku.sh &'},
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
	print('start', argv)
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
	
	print('%s\t SOC %3i\t Mode %s'		% (str(d['name']).ljust(10),d['soc'],			esmart.DEVICE_MODE[d['chg_mode']]	))
	
	if d['chg_power']:
		print('PV\t %5.1f V\t %5.1f A\t %i W' 	% (d['pv_volt'],	d['chg_cur'],		d['chg_power']	))
	
	print('Battery\t %5.1f V\t %5.1f A\t %i W'	%(d['bat_volt'],	battery_cur,		battery_power	))
	
	if d['load_power']:
		print('Load\t %5.1f V\t %5.1f A\t %i W'	% (d['load_volt'],	d['load_cur'],		d['load_power']	))
	
	print('Temp\t int %i °C\t%s %i °C\n'		% (d['int_temp'],	d['ext_temp_name'],	d['ext_temp']	))
	return

def combine_chargers(esm_chg):	# add up chargers to common values
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

def avg(inlist):			# return the average of a list variable
	if len(inlist) == 0: return(0)
	return( sum(inlist) / len(inlist) )

class discharge_times():
	def __init__(self):
		self.interval	= 10	# seconds
		self.stamp		= datetime.now().replace(second=0, microsecond=0)
		self.active		= False
		self.battery	= 100
		self.inverter	= 100
		self.energy		= 0
		self.update()
	
	def update(self):
		if self.stamp + timedelta(seconds = self.interval) < datetime.now():
			self.stamp = datetime.now()
			times = []; states = []
			try:
				with open(discharge_t_file,'r') as fi:
					for i in fi:
						if i[0] == '#' or i == '\n': continue	# ignore empty lines
						if i[:10] == '0000-00-00': i = datetime.now().strftime('%Y-%m-%d') + i[10:] # set to today
						times.append(datetime.strptime(i[:16], '%Y-%m-%d %H:%M'))
						states.append(str(i[16:]).replace('\n','').replace('\t',' ').split(' ')[1:])
				
				for i in range(0,len(times)):
					self.active = True	# successful file read
					
					if times[i] < datetime.now(): 
						if states[i][0] == '0':	self.battery = 0
						else:					self.battery = int(states[i][0])
						if states[i][1] == '0':	self.inverter = 0
						else:					self.inverter = int(states[i][1])
						if states[i][2] == '0':	self.energy = 0
						else:					self.energy = int(states[i][2])
						if debug: print(times[i].strftime('%Y-%m-%d %H:%M'),'\tbattery perc:',self.battery, '\tinput perc:',self.inverter)
					else: break
			except:
				self.active		= False	# indicates a invalid timer file!
				self.battery	= 100
				self.inverter	= 100
				self.energy		= 9999

max_input_power	= conf_max_gti_power * number_of_gti
send_power		= 0
last_send		= 0
last2_send		= 0
ramp_cnt		= 0
ramp_power		= 0
thres_switch	= 0
last_runtime	= 0
bat_cont		= 0
pv_cont			= 0
in_pc			= 0				# input power counter
adjusted_power	= False
bat_history		= [0]* 5		# history vars with *n interval steps
pv_history		= [0]* 20
send_history	= [0]* 4
long_send_history	= [0]* multi_dev_wait
long_meter_history	= [0]* 100
zero_shift = conf_zero_shift
temp_ext_alarm_time	= datetime.now()
temp_int_alarm_time	= datetime.now()
timeout_repeat		= datetime.now()
vz_in				= open(vzlogger_log_file,'r')
esm_n 				= len(esmarts)
dropped_first_up_ramp	= False

if discharge_timer: timer = discharge_times()
if verbose: print('zeroinput starts\n')

for charger in esmarts:
	charger['dev'] = esmart.esmart()
	charger['dev'].set_name(charger['name'])
	charger['dev'].set_ext_temp_name(charger['temp_sensor_display'])
	charger['dev'].set_callback(handle_data)
	charger['dev'].open(charger['port'])
	charger['dev'].tick()
	sleep(0.3)
	charger['dev'].tick()
	if verbose:	print('.')

d = combine_chargers(esmarts)	# initially combine chargers data to one general
for charger in esmarts: charger['dev'].close()

while True:						# infinite loop, stop the script with ctl+c
	
	main_log = False; Ls_read = 99999; Ls_ts = 99999
	last2_send	= last_send		# dedicated history
	last_send	= send_power	# variables
	block_saw_detection = False	# allow saw detection
	
	while True:					# loop over vzlogger.log fifo
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
		
		if '1-0:16.7.0' in l:	# read the sum L1+L2+L3, can be negative
			try: Ls_read = int( round( float( l[l.index('value=')+6:-1+l.index('ts=')]) ,4) )
			except: pass
			else:
				try: Ls_ts = int( l[l.index('ts=')+3:-1] )
				except: pass
		
		if Ls_read != 99999 and Ls_ts !=99999:							# check if Ls has input and timestamp
		
			# if the reading is older than 1 second, continue reading the vzlogger data
			if abs( int(str(time())[:10]) - int(str(Ls_ts)[:10]) ) > 1: continue
			
			break	# stop reading the vzlogger pipe
		sleep(0.0001)
		
	if verbose:
		system('clear')
		print('%s, voltage' % (strftime('%H:%M:%S')),d['bat_volt'],'V, PV power',d['chg_power'],'W, load power',d['load_power'],'W')
		if discharge_timer:
			if timer.active:	print('timer active: bat discharge %i'%timer.battery,'W,' if timer.battery > 100 else '%,','inverter %i'%timer.inverter,'W,' if timer.inverter > 100 else '%,','energy %.0f/%i Wh'%(in_pc/3600,timer.energy),'\n')
			else:				print('timer.txt enabled but not active! no valid timestamp file set?\n')
	
	send_power = int( Ls_read + last2_send + zero_shift )				# calculate the power demand
	
	# high change of power consumption, on rise: no active power limitation, sufficient bat_voltage
	if (Ls_read < -400) or (Ls_read > 400 and not adjusted_power and bat_cont > 51.0):
		if not dropped_first_up_ramp and Ls_read > 400: 				# don't delay down ramps
			dropped_first_up_ramp = True
			if verbose: print('DROPPED first Ramp')
		else:
			if	ramp_cnt == 0:
				ramp_cnt = 2 + number_of_gti							# in script cycles
				ramp_power = send_power
	
	if ramp_cnt > 0:													# within ramp countdown
		block_saw_detection = True										# disable saw detection
		send_power = ramp_power
		if verbose: print('ramp mode %i'%ramp_cnt)
		ramp_cnt -= 1
		
		if ramp_cnt == 0:
			dropped_first_up_ramp = False
	
	status_text = ''
	
	if bat_voltage_const != 0:											# battery voltage correction
		battery_power = d['chg_power'] - (d['load_power'] * number_of_gti)
		bat_corr = round(0.001 * battery_power * bat_voltage_const, 1)
		bat_history = bat_history[1:] + [d['bat_volt'] - bat_corr]
		if verbose and bat_corr: print('voltage correction',round(bat_history[-1],1),'V, dif',bat_corr,'V')
	else:
		bat_history = bat_history[1:] + [d['bat_volt']]
	
	if 0 in bat_history:	bat_cont = d['bat_volt']
	else:					bat_cont = avg(bat_history)					# average of the previous battery voltages
	
	if debug: print('bat_history\t',bat_history,'\nbat_cont\t',bat_cont)
	
	pv_history = pv_history[1:]+ [d['chg_power']]
	pv_cont = int(avg(sorted(pv_history)[-5:]))							# average on high pass of the PV power, removing the gap on mppt tracker restart
	pv_power = 0
	
	if debug: print('pv_history\t', pv_history,'\nsort_pv\t\t',sort_pv,'\npv_cont\t\t',pv_cont)
	
	if no_input:														# disabled power input by command line option
		send_power = 0
		if verbose: print('input DISABLED by command line')
	
	elif datetime.now() < timeout_repeat:								# battery protection timeout
		send_power = 0
		if verbose: print('battery protection timeout until', timeout_repeat.strftime('%H:%M:%S'))
	
	else:
		adjusted_power = False
		
		if bat_cont < 48 or ( (not timer.battery or (timer.battery and (in_pc/3600) > timer.energy) ) and (not timer.inverter or not pv_cont) ):	# set a new battery timeout
			adjusted_power = True
			send_power		= 0											# disable input
			send_history	= [0]*4										# reset history
			timeout_repeat = datetime.now() + timedelta(minutes = 1)	# repeat in one minute
		
		else:
			pv_bat_minus = 0 if bat_cont > 49 else (49-bat_cont)*50 * number_of_gti	# reduction by battery voltage in relation to the base consumption of the inverter(s)
			avg_pv		= avg(pv_history[-3:])							# use a shorter span than pv_cont
			pv_eff		= avg_pv-(avg_pv * PV_to_AC_efficiency * 0.01)	# efficiency gap
			pv_p_minus	= pv_bat_minus + pv_eff							# pv reduction
			pv_power	= max(0,int(avg_pv - pv_p_minus))				# remaining PV power
			bat_power_by_voltage = conf_max_bat_discharge				# unlimited bat discharge so far
			
			if discharge_timer and not timer.battery:					# disabled battery discharge, pass through pv power
				if	send_power > pv_power:
					send_power = pv_power
					adjusted_power = True
					if verbose and pv_cont:	status_text	+= ((' limited, PV -%i W' % round(pv_p_minus)) if pv_p_minus else ' ') + ', no battery discharge'
			
			elif bat_cont >= 48 and bat_cont <= 51:								# limit battery power, pass through pv power
				bat_power_percent_by_voltage	= (bat_cont - 46.93 ) **3.281	# powercurve between 48-51 V, results in 1-100%
				bat_power_by_voltage			= int(0.01 * max_input_power * bat_power_percent_by_voltage)	# 100% above 51 V
				
				if verbose: status_text = ', Bat %i W (%.1f%%)'	% (bat_power_by_voltage, bat_power_percent_by_voltage) + ', PV %i W (-%i W)'% (pv_power, pv_p_minus) if pv_power  != 0 else ''
			
			if bat_cont > 55.0:											# give some free power to the world = "pull down the zero line" (not zero shift!)
				free_power = int((bat_cont - 55.0)*10 *0.5)				# 0.5 W / 0.1 V, maximum depends on mppt chargers "saturation charging voltage", usually 57 V
				send_power += free_power
				if verbose and free_power > 0: status_text += ', free export by voltage %i W' % free_power
			else: free_power = 0
		
			if discharge_timer:											# active timer, battery limit
				if timer.battery == 0:		bat_discharge = 0 
				elif timer.battery <= 100:	bat_discharge = int(conf_max_bat_discharge *0.01 *timer.battery)	# <= 100 as percentage
				else:						bat_discharge = timer.battery										# > 100 as W
			else:							bat_discharge = conf_max_bat_discharge								# bat discharge by configuration
			
			if bat_discharge > bat_power_by_voltage:													# bat timer limited to voltage power
											bat_discharge = bat_power_by_voltage
			
			if send_power  >	pv_power +	bat_discharge:				# battery discharge limit
				send_power =	pv_power +	bat_discharge
				adjusted_power = True
				if verbose: status_text += ', battery discharge limit %i W'%bat_discharge
		
		send_history = send_history[1:]+[send_power]					# update send_power history
		
		if block_saw_detection:
			if verbose: print('disabled saw detection')
		else:
			if not close_values(send_history[-1],send_history[-2],3) and not close_values(send_history[-3],send_history[-4],3):
				send_power = int(avg(send_history))						# break the swing up by using the average
				if verbose: print('saw stop',send_power)
				send_history[-1] = send_power
			else:
				if verbose: print('no saw detected')
		
		if discharge_timer:												# active timer, inverter input limit
			if timer.inverter <= 100:	max_input = int( max_input_power *0.01 *timer.inverter)	# <= 100 as percentage 
			else:						max_input = timer.inverter								# > 100 as W
			
			if (in_pc/3600) > timer.energy and timer.battery != 0:							# 	hourly energy limit exceeded
									max_input = 0
									status_text	+= ', hourly energy limit exceeded'
			
			if max_input > max_input_power:
									max_input = max_input_power
		
		else:						max_input = max_input_power 							# the limit of the gti(s) by configuration
		
		if send_power	< 10:			# keep it positive with a little gap on bottom
			send_power	= 0				# disable input
			adjusted_power = True
			send_history[-1] = send_power
			status_text	+= ', inverter MIN power limit'
		
		if send_power	> max_input:
			send_power	= max_input
			adjusted_power = True
			send_history[-1] = send_power
			status_text	+= ', inverter MAX power limit %i W'%max_input
	
		if verbose:						# show saw tooth values
			print(	'input history', send_history, '\t 1:2 %.1f %%\t3:4 %.1f %%' % 
					 (round((1-(send_history[-1] / (0.01+send_history[-2])))*100,1), round((1-(send_history[-3] / (0.01+send_history[-4])))*100,1) ) )
	
	with open('/tmp/vz/soyo.log','w') as fo:							# send some values to vzlogger
		fo.write('%i: soyosend = %i\n'	% ( time(),	-send_power ) )		# the keywords have to be created as channels in vzlogger.conf to make it work there!
		fo.write('%i: zero_shift_w = %i\n'	% ( time(),	-zero_shift ) )
		fo.write('%i: bat_v = %f\n'		% ( time(),	bat_cont ) )
		fo.write('%i: pv_w = %i\n'		% ( time(),	-d['chg_power'] ) )
#		fo.write('%i: pv_w0 = %i\n'		% ( time(),	-esmarts[0]['dev'].fields['chg_power'] ) )
#		fo.write('%i: pv_w1 = %i\n'		% ( time(),	-esmarts[1]['dev'].fields['chg_power'] ) )
#		fo.write('%i: pv_u0 = %f\n'		% ( time(),	esmarts[0]['dev'].fields['pv_volt'] ) )
#		fo.write('%i: pv_u1 = %f\n'		% ( time(),	esmarts[1]['dev'].fields['pv_volt'] ) )
		fo.write('%i: bat_temp = %i\n'	% ( time(),	esmarts[0]['dev'].fields['ext_temp'] ) )
		fo.write('%i: out_temp = %i\n'	% ( time(),	esmarts[1]['dev'].fields['ext_temp'] ) )
		fo.write('%i: int_temp0 = %i\n'	% ( time(),	esmarts[0]['dev'].fields['int_temp'] ) )
		fo.write('%i: int_temp1 = %i\n'	% ( time(),	esmarts[1]['dev'].fields['int_temp'] ) )
	
	if verbose: 
		if send_power == 0: print('\nmeter {:4d} W'.format(Ls_read),end='')	# show the meter readings, and zero shift
		else: 
			print('\nmeter {:4d} W ({}shift {} W '.format(Ls_read,'auto ' if conf_zero_shift == 0 else '',abs(zero_shift)),end='' )
			if conf_zero_shift <= 0: print('import)',end='')
			else: print('export)',end='')
		print(', interval %.2f s'% (time()-last_runtime))
		print('inverter {:4d} W{}'.format(send_power, status_text))		# show the input data
	
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
	long_send_history = long_send_history[1:]+[send_power]				# provide a long send_power history
	
	long_meter_history = long_meter_history[1:]+[Ls_read if (send_power and not adjusted_power and not ramp_cnt) else 0]	# provide a long meter history, without ramp and power based adjusting
	
	if conf_zero_shift == 0 and not adjusted_power:									# auto zero shift, follows the meter
		zero_shift = -abs(int(avg(sorted(long_meter_history)[4:14])))				# sort meter history, ignore 4 lowest, average of 5 to 15, negate
		if debug: print('[4:14] meter history',sorted(long_meter_history)[4:14])
	else: zero_shift = conf_zero_shift

	open_ser_ports = []
	for port in rs485_ports: open_ser_ports.append(serial.Serial(port, 4800))		# open the serial ports for sending soyosource power demand
	
	for j in [1,2]:		# poll 2 times
		if send_power != 0:
			
			if number_of_gti == 1 or (sorted(long_send_history)[-4] < single_dev_threshold):	# filter 3 spikes before switching to all inverters
				set_soyo_demand(open_ser_ports[awo_gti_port],send_power)						# one inverter is used
				soyo_demands = '%ix %i W'%(1,send_power)
			else:																				# all inverters are used
				for port in open_ser_ports: set_soyo_demand(port,int(1.0 * send_power / number_of_gti))
				soyo_demands = '%ix %i W'%(number_of_gti,(1.0 * send_power / number_of_gti))
				
			if verbose: print('%i soyo %s'%(j,soyo_demands))
		
		elif verbose: print('. soyo')			# don't send power request
		sleep(0.20)								# wait
	
	for port in open_ser_ports: port.close()
	
	for charger in esmarts: charger['dev'].open(charger['port'])		# open the port for reading esmart data
	
	for i in [1,2]:								# poll 2 times
		if datetime.now() > timeout_repeat or pv_cont != 0:				# after battery protection timeout or at day time
			for charger in esmarts:
				charger['dev'].tick()
			
			if verbose:	print('%i eSmart3'%i)
		elif verbose: 	print('. eSmart3')		# don't send but sleep
		if i < esm_n: 	sleep(0.35)				# don't wait after last tick
	
	for charger in esmarts: charger['dev'].close()
	
	if datetime.now().minute == 0 and datetime.now().second == 0: in_pc = 0		# reset battery energy counter every full hour
	else:
		in_pc += max(0, send_power - pv_power)									# only count energy from battery
		
	d = combine_chargers(esmarts)				# combine chargers data to one general
	
	if discharge_timer: timer.update()
	if debug: sleep(1)


