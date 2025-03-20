#!/usr/bin/python3
# -*- coding: utf-8 -*-
# indent size 4, mode Tabs

from serial import Serial
from json import load as json_load
from os.path import abspath, join, dirname
from time import strftime, time, localtime, sleep
from datetime import timedelta, datetime
from threading import Thread, Event
from traceback import print_exc
import sys

if '-h' in sys.argv or '--help' in sys.argv:
	print(' -v\t\tverbose mode with console output\n','-web\t\toutput to html file\n','-no-input\tdisable power input\n','-test-alarm\texecute alarm command and stop')
	exit(0)

try:
	with open(join(dirname(__file__),'zeroinput.conf'),'r') as fi: conf = json_load(fi)		# read configuration from file
except Exception as e:
	print(e)
	print_exc()
	print('error reading config file')
	exit(1)

mppt_data = {'combined':{}}; victron_devs = []; esmart3_devs = []; soyosource_devs= []
for dev in conf['rs485']:
	if 'mppt_type' in conf['rs485'][dev] and conf['rs485'][dev]['mppt_type'] == 'victron':
										victron_devs.append(dev)
										mppt_data[dev] = {}
	if 'mppt_type' in conf['rs485'][dev] and conf['rs485'][dev]['mppt_type'] == 'eSmart3':
										esmart3_devs.append(dev)
										mppt_data[dev] = {}
	if 'inverter' in conf['rs485'][dev] and conf['rs485'][dev]['inverter'] == 'soyosource':
										soyosource_devs.append(dev)

if '-test-alarm' in sys.argv:
	print('test alarm command:')
	from os import system
	system(conf['rs485']['/dev/ttyACM0']['alarm']['int_cmd'])	# change this to your needs
	exit(0)

no_input = True if '-no-input' in sys.argv else False
web_stats = True if '-web' in sys.argv else False
verbose = False

if '-v' in sys.argv:
	verbose = True
	con_stats = True
	from os import system
	print('start', sys.argv)
else: con_stats = False

if web_stats:
	verbose = True
	from io import StringIO as io_StringIO
	output_buffer = io_StringIO()
	sys.stdout = output_buffer		# comment here for DEBUGGING


def display_mppt_data():			# display the mppt charger data
	if not verbose: return
	global mppt_data
	print('{:12s}  {:10s} {:>5s}  {:>6s}  {:>6s}  {:>5s}  {:>5s}  {:>4s}  {:>4s} {:<8s}'.format('port','name','W PV','V bat','I bat','mode','Pload','Tint','Text',''))	# header line
	
	for port in mppt_data:
		if 'CS' in mppt_data[port].keys():
			if 		conf['rs485'][port]['mppt_type'] == 'victron': 
				mppt_dev_mode = '' if mppt_data[port]['CS'] > 14 else ['OFF','','FAULT','BULK','ABSORB','FLOAT','','EQUAL','','','START','','RECOND','','EXTCON'][mppt_data[port]['CS']]
			elif	conf['rs485'][port]['mppt_type'] == 'eSmart3': 
				mppt_dev_mode = '' if mppt_data[port]['CS'] > 4 else ['WAIT','MPPT','BULK','FLOAT','PRE'][mppt_data[port]['CS']]
		else: mppt_dev_mode = ''
			
		print('{:12s}  {:10s} {:>5s}  {:>6s}  {:>6s}   {:<6s} {:>4s} {:>3s} {:>3s} {:<8s}'.format(
			'all' if port == 'combined' else port,
			'combined' if port == 'combined' else conf['rs485'][port]['name'],
			'%5i'  % mppt_data[port]['PPV']		if 'PPV'  in mppt_data[port].keys() else '',
			'%3.2f'% mppt_data[port]['Vbat']	if 'Vbat' in mppt_data[port].keys() else '',
			'%3.2f'% mppt_data[port]['Ibat']	if 'Ibat' in mppt_data[port].keys() else '', 
			mppt_dev_mode,
			'%4i' % mppt_data[port]['Pload']	if ('Pload' in mppt_data[port].keys() and mppt_data[port]['Pload'] > 0) else '',
			str(mppt_data[port]['int_temp']).rjust(5)	if 'int_temp' in mppt_data[port].keys() else '',
			str(mppt_data[port]['ext_temp']).rjust(5)	if 'ext_temp' in mppt_data[port].keys() else '',
			str(conf['rs485'][port]['temp_display']).ljust(8) if (port != 'combined' and 'temp_display' in conf['rs485'][port].keys()) else '') )
	return(0)


def combine_charger_data():			# combine all mppt charger data to a summary
	global mppt_data
	d = {'PPV':0,'Vbat':0,'Ibat':0,'Pload':0}
	
	for name in d.keys():
		valcnt = 0
		for dev in mppt_data.keys():
			if dev == 'combined': continue
			if name in mppt_data[dev].keys():
				valcnt += 1
				d[name] += mppt_data[dev][name]
		if name == 'Vbat'	and valcnt > 0: d[name] /= valcnt	# the average
		if name == 'Pload'	and valcnt > 0: d[name] = d[name] * n_active_inverters		# try a projection for one to a esmart3 connected inverter, amounting to the device accuracy
	
	mppt_data['combined'] = d
	return(0)


def set_soyo_demand(ser,power):		# create and send the packet for soyosource gti
	pu = power >> 8
	pl = power & 0xFF
	cs = 264 - pu - pl
	if cs > 255: 
		if power > 250:	cs -= 256
		else:			cs -= 255
	
	ser.write( bytearray([0x24,0x56,0x00,0x21,pu,pl,0x80,cs]) )
	ser.flush()
	return(0)


def close_values(a,b,tol):			# check if values a and b are within tolerance
	if a > b * (1 - 0.01*tol) and a < b *(1 + 0.01*tol): return(1)
	return(0)


def avg(inlist):					# return the average of a list variable
	if len(inlist) == 0: return(0)
	return( sum(inlist) / len(inlist) )


class discharge_times():			# handle timer.txt file
	def __init__(self):
		self.interval	= 10	# seconds
		self.stamp		= datetime.now().replace(second=0, microsecond=0)
		self.active		= False
		self.battery	= 100
		self.inverter	= 100
		self.energy		= conf['max_input_power']
		self.update()
	
	def update(self):
		if self.stamp + timedelta(seconds = self.interval) < datetime.now():
			self.stamp = datetime.now()
			times = []; states = []
			try:
				with open(conf['discharge_t_file'],'r') as fi:
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
						if False: print(times[i].strftime('%Y-%m-%d %H:%M'),'\tbattery perc:',self.battery, '\tinput perc:',self.inverter)
					else: break
			except:
				self.active		= False	# indicates a invalid timer file!
				self.battery	= 100
				self.inverter	= 100
				self.energy		= 9999


class esmart:						# eSmart3 MPPT charger lib by skagmo.com 2018: https://github.com/skagmo/esmart_mppt | adapted for zeroinput
	def __init__(self):
		self.state = 0 # STATE_START
		self.data = []
		self.port = ""
		self.timeout = 0
	
	def __del__(self):	self.close()
	
	def set_port(self, port):	self.port = port
	
	def open(self):	self.ser = Serial(self.port,9600,timeout=0.1)
	
	def close(self):
		try:
			self.ser.close()
			self.ser = False
		except AttributeError:
			pass
	
	def send(self, pl):	self.ser.write(self.pack(pl))
	
	def parse(self, data):
		global mppt_data
		for c in data:
			if (self.state == 0):		# STATE_START
				if (c == 0xaa):			# Start character detected
					self.state = 1		# STATE_DATA
					self.data = []
					self.target_len = 255
			elif (self.state == 1):		# STATE_DATA
				self.data.append(c)
				if (len(self.data) == 5): self.target_len = 6 + self.data[4]	# Received enough of the packet to determine length
				if (len(self.data) == self.target_len):		# Received whole packet
					self.state = 0		# STATE_START
					if (self.data[2] == 3): 
						msg_type = self.data[3]	# Source 3 is MPPT device
						if (self.data[3] == 0):	# Type 0 packet contains most data
							if verbose: print('REC',self.port,':',conf['rs485'][self.port]['name'],'' if 'ts' not in mppt_data[self.port].keys() else 'delay %1.2f s'%(time()- mppt_data[self.port]['ts']) )
							mppt_data[self.port] = {}  # reset all values
							mppt_data[self.port]['CS']		= int.from_bytes(self.data[7:9],	byteorder='little')
							mppt_data[self.port]['VPV']		= int.from_bytes(self.data[9:11],	byteorder='little') / 10.0
							mppt_data[self.port]['Vbat']	= int.from_bytes(self.data[11:13],	byteorder='little') / 10.0
							mppt_data[self.port]['Ibat']	= int.from_bytes(self.data[13:15],	byteorder='little') / 10.0
							mppt_data[self.port]['Vload']	= int.from_bytes(self.data[17:19],	byteorder='little') / 10.0
							mppt_data[self.port]['Iload']	= int.from_bytes(self.data[19:21],	byteorder='little') / 10.0
							mppt_data[self.port]['PPV']		= int.from_bytes(self.data[21:23],	byteorder='little')
							mppt_data[self.port]['Pload']	= int.from_bytes(self.data[23:25],	byteorder='little')
							mppt_data[self.port]['ext_temp']= self.data[25] if self.data[25] < 200 else self.data[25] - 256
							mppt_data[self.port]['int_temp']= self.data[27] if self.data[27] < 200 else self.data[27] - 256
							mppt_data[self.port]['ts']		= time()
	
	def esmart_status_request(self):
		try:
			while (self.ser.inWaiting()): self.parse(self.ser.read(100))		# Send poll packet to request data every x seconds
			if (time() - self.timeout) > 1:
				self.ser.write(b"\xaa\x01\x01\x01\x00\x03\x00\x00\x1e\x32")		# request status message
				self.timeout = time()
		except IOError:
			print("Serial port error, fixing")
			self.ser.close()
			opened = 0
			while not opened:
				try:
					self.ser = Serial(self.port,38400,timeout=0)
					if self.ser.read(100):	opened = 1
					else:					self.ser.close()
				except serial.serialutil.SerialException:
					time.sleep(0.5)
					self.ser.close()
			print("Error fixed")


def handle_victron_data(serialport, stop_event: Event):													# reads serial data of victron devices
	global mppt_data
	rec_buf = {}
	victron_debug = False
	try:
		ser = Serial(port=serialport, baudrate=19200, bytesize=8, parity='N', stopbits=1, timeout=2, xonxoff=0, rtscts=0)
		ser.reset_input_buffer()
		while True:
			if stop_event.is_set(): 
				ser.close()
				return(0)
			data = b''; char = ''
			while char != b'\n':
				char = ser.read()
				data += char
			if victron_debug: print('victron raw data:',data)
			if data:
				snv = str(data)[2:].split('\\t')
				if len(snv) == 2: name,val = snv
				else: continue
				val = val[:-5]
				if name == 'V': name = 'Vbat'
				if name == 'I': name = 'Ibat'
				if name == 'PID':										# begin new dataset with PID
																		mppt_data[serialport] = rec_buf
																		if verbose: print('REC',serialport,':',conf['rs485'][serialport]['name'],'' if 'ts' not in mppt_data[serialport].keys() \
																						else 'delay %1.2f s'%(time()- mppt_data[serialport]['ts']) )
																		rec_buf = {'ts':time()}
																		if 'PPV' in mppt_data[serialport].keys(): rec_buf['PPV'] = mppt_data[serialport]['PPV'] # keep old PPV for continuity, overwrite below
				if name in ['PID','SER#','OR','LOAD','Checksum']:		rec_buf[name] = val					# add as string
				elif name in ['Vbat','Ibat','VPV'] and val.isnumeric():	rec_buf[name] = 0.001* int(val)		# add as float
				elif val.isnumeric():									rec_buf[name] = int(val)			# add as int
				else: 
					if victron_debug: print('victron ELSE',data,'\n')
					pass	# there seems to be a transmission error, ignore it
			continue
	except Exception as e:
		stop_event.set()							# tell all threads to stop
		print(e)
		print_exc()
		ser.close()
		return(1)


if __name__ =="__main__":
	try:
		stop_event = Event(); victron_threads = []
		for port in victron_devs:
			victron_threads.append( Thread(target=handle_victron_data, args=(port, stop_event)) )		# victron reader threads
			victron_threads[-1].start()
		
		max_input_power	 = conf['max_input_power']
		n_active_inverters = 0
		send_power		= 0
		last_send		= 0
		last2_send		= 0
		ramp_cnt		= 0
		ramp_power		= 0
		dropped_first_up_ramp	= False
		bat_cont		= 0				# continous bat voltage
		pv_cont			= 0				# continous pv voltage
		in_pc			= 0				# input power counter
		adjusted_power	= False
		bat_history		= [0]* 5		# history vars with *n interval steps
		pv_history		= [0]* 20
		send_history	= [0]* 4
		long_send_history	= [0]* conf['multi_inverter_wait']
		long_meter_history	= [0]* 100
		zero_shift = conf['zero_shifting']
		last_runtime 		= time()
		temp_ext_alarm_time	= datetime.now()
		temp_int_alarm_time	= datetime.now()
		timeout_repeat		= datetime.now()
		vz_in				= open(conf['vzlogger_log_file'],'r')
		
		
		if conf['discharge_timer']: timer = discharge_times()											# set up timer
		if verbose: print('zeroinput starts\n')
		
		esmart_handles = []
		for port in esmart3_devs:																		# set up esmart3 devices
			esmart_handles.append( { 'obj': esmart() } )
			esmart_handles[-1]['obj'].set_port(port)
			esmart_handles[-1]['obj'].open()
			for i in [1,2]:												# request status 2 times
				if verbose: print(i)
				esmart_handles[-1]['obj'].esmart_status_request()
				sleep(0.20)
			esmart_handles[-1]['obj'].close()
		if verbose: print('reading power meter data\n')
		
		while True:																						# infinite loop, stop the script with ctl+c
			
			main_log = False; Ls_read = 99999; Ls_ts = 99999
			last2_send	= last_send		# dedicated history
			last_send	= send_power	# variables
			block_saw_detection = False	# allow saw detection
			
			combine_charger_data()																		# update charger summary
			
			if con_stats or web_stats:
				if con_stats: system('clear')
				
				if web_stats:
					with open(join(dirname(__file__),'zeroinput.html'),'w') as webfile: 
						webfile.write("""<!DOCTYPE html><html><head><meta http-equiv="refresh" content="1" ><style>body {font-size: 200%;color: #BBBBBB;background-color: #111111;}</style></head><body><pre>\n""")
						webfile.write(output_buffer.getvalue())
						webfile.write('\n</pre></body></html>')
					
					if con_stats:
						output_buffer.seek(0)
						print(output_buffer.getvalue(), file=sys.__stdout__)
				
					output_buffer.seek(0)
					output_buffer.truncate(0)
				
				display_mppt_data()																		# display charger data
				if conf['discharge_timer']:
					if timer.active:	print('\ntimer active: bat discharge %i'%timer.battery,'W,' if timer.battery > 100 else '%,','energy %.0f/%i Wh,'%(in_pc/3600,timer.energy),'inverter %i'%timer.inverter,'W' if timer.inverter > 100 else '%','\n')
					else:				print('\ntimer.txt enabled but not active! no valid timer file set?\n')
			
			while True:																					# loop over vzlogger.log fifo
				l = vz_in.readline()
				if '[main] vzlogger' in l: 
					main_log = True
					vzout = open(conf['persistent_vz_file'],'a')
					vzout.write('REDIRECTED by zeroinput.py from'+ conf['vzlogger_log_file'] +'\n')
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
				
				if Ls_read != 99999 and Ls_ts !=99999:													# check if Ls has input and timestamp
				
					# if the reading is older than 1 second, continue reading the vzlogger data
					if abs( int(str(time())[:10]) - int(str(Ls_ts)[:10]) ) > 1: continue
					
					break	# stop reading the vzlogger pipe
				sleep(0.001)
			
			send_power = int( Ls_read + last2_send + zero_shift )										# calculate the power demand
			
			# high change of power consumption, on rise: no active power limitation, sufficient bat_voltage
			if (Ls_read < -400) or (Ls_read > 400 and not adjusted_power and bat_cont > 51.0):
				if not dropped_first_up_ramp and Ls_read > 400: 										# don't delay down ramps
					dropped_first_up_ramp = True
					if verbose: print('DROPPED first Ramp')
				else:
					if	ramp_cnt == 0:
						ramp_cnt = 2 + n_active_inverters												# counted in script cycles
						ramp_power = send_power
			
			if ramp_cnt > 0:																			# within ramp countdown
				block_saw_detection = True																# disable saw detection
				send_power = ramp_power
				if verbose: print('ramp mode %i'%ramp_cnt)
				ramp_cnt -= 1
				
				if ramp_cnt == 0:
					dropped_first_up_ramp = False
			
			status_text = ''
			
			if conf['bat_voltage_const'] != 0:															# battery voltage correction
				if mppt_data['combined']['Pload'] == 0:		battery_power = mppt_data['combined']['PPV'] - send_history[-1]					# no inverter connected to an eSmart3
				else:										battery_power = mppt_data['combined']['PPV'] - mppt_data['combined']['Pload']	# asuming one inverter connected as load to an eSmart3 
				bat_corr = round(0.001 * battery_power * conf['bat_voltage_const'], 1)
				bat_history = bat_history[1:] + [mppt_data['combined']['Vbat'] - bat_corr]
				if verbose and bat_corr: print('voltage correction',round(bat_history[-1],1),'V, dif',bat_corr,'V')
			else:
				bat_history = bat_history[1:] + [mppt_data['combined']['Vbat']]
			
			if 0 in bat_history:	bat_cont = mppt_data['combined']['Vbat'] 
			else:					bat_cont = avg(bat_history)											# average of the previous battery voltages
			
			pv_history = pv_history[1:]+ [mppt_data['combined']['PPV']]
			pv_cont = int(avg(sorted(pv_history)[-5:]))													# average on high pass of the PV power, removing the gap on mppt tracker restart
			pv_power = 0
			
			if no_input:																				# disabled power input by command line option
				send_power = 0
				if verbose: print('input DISABLED by command line')
			
			elif datetime.now() < timeout_repeat:														# battery protection timeout
				send_power = 0
				if verbose: print('battery protection timeout until', timeout_repeat.strftime('%H:%M:%S'))
			
			else:
				adjusted_power = False
				
				if bat_cont <= 48 or (conf['discharge_timer'] and										# set a new battery timeout
					( (not timer.battery or (timer.battery and (in_pc/3600) > timer.energy) ) and (not timer.inverter) )):
					adjusted_power = True
					send_power		= 0																	# disable input
					send_history	= [0]*4																# reset history
					timeout_repeat = datetime.now() + timedelta(minutes = 1)							# repeat in one minute
				
				else:
					pv_bat_minus = 0 if bat_cont > 49 else (49-bat_cont)*50 * n_active_inverters		# reduction by battery voltage in relation to the base consumption of the inverter(s)
					avg_pv		= avg(pv_history[-3:])													# use a shorter span than pv_cont
					pv_eff		= avg_pv-(avg_pv * conf['PV_to_AC_efficiency'] * 0.01)					# efficiency gap
					pv_p_minus	= pv_bat_minus + pv_eff													# pv reduction
					pv_power	= max(0,int(avg_pv - pv_p_minus))										# remaining PV power
					bat_power_by_voltage = conf['max_bat_discharge']									# unlimited bat discharge so far
					
					if conf['discharge_timer'] and not timer.battery:									# disabled battery discharge, pass through pv power
						if	send_power > pv_power:
							send_power = pv_power
							adjusted_power = True
							if verbose and pv_cont:	status_text	+= ((' limited, PV -%i W' % round(pv_p_minus)) if pv_p_minus else ' ') + ', no battery discharge'
					
					elif bat_cont >= 48 and bat_cont <= 51:												# limit battery power, pass through pv power
						bat_power_percent_by_voltage	= (bat_cont - 46.93 ) **3.281					# powercurve between 48-51 V, results in 1-100%
						bat_power_by_voltage			= int(0.01 * max_input_power * bat_power_percent_by_voltage)	# 100% above 51 V
						
						if verbose: status_text = ', Bat %i W (%.1f%%)'	% (bat_power_by_voltage, bat_power_percent_by_voltage) + ', PV %i W (-%i W)'% (pv_power, pv_p_minus) if pv_power  != 0 else ''
					
					if conf['free_power_export'] and bat_cont >= 54.5:										# give some free power to the world = "pull down the zero line" (not zero shift!)
						free_power = int( (1.0/(57-54.5)) * (bat_cont - 54.5) * conf['max_input_power'] )	# full energy input at maximum bat voltage: depends on mppt chargers "saturation charging voltage", usually 57 V
						if free_power > 0:
							send_power += free_power
							adjusted_power = True
							if verbose: status_text += ', free export by voltage %i W' % free_power
					else: free_power = 0
				
					if conf['discharge_timer']:															# active timer, battery limit
						if timer.battery == 0:		bat_discharge = 0 
						elif timer.battery <= 100:	bat_discharge = int(conf['max_bat_discharge'] *0.01 *timer.battery)	# <= 100 as percentage
						else:						bat_discharge = timer.battery										# > 100 as W
					else:							bat_discharge = conf['max_bat_discharge']							# bat discharge by configuration
					
					if bat_discharge > bat_power_by_voltage:											# bat timer limited to voltage power
													bat_discharge = bat_power_by_voltage
					
					if send_power  >	pv_power +	bat_discharge:										# battery discharge limit
						send_power =	pv_power +	bat_discharge
						adjusted_power = True
						if verbose: status_text += ', battery discharge limit %i W'%bat_discharge
				
				send_history = send_history[1:]+[send_power]											# update send_power history
				
				if block_saw_detection:
					if verbose: print('disabled saw detection')
				else:
					if not close_values(send_history[-1],send_history[-2],3) and not close_values(send_history[-3],send_history[-4],3):
						send_power = int(avg(send_history))						# break the swing up by using the average
						if verbose: print('saw stop',send_power)
						send_history[-1] = send_power
					else:
						if verbose: print('no saw detected')
				
				if conf['discharge_timer']:																# active timer, inverter input limit
					if timer.inverter <= 100:	max_input = int( max_input_power *0.01 *timer.inverter)	# <= 100 as percentage 
					else:						max_input = timer.inverter 								# > 100 as W
					max_input += free_power																# add free power to timer limit
					
					if (in_pc/3600) > timer.energy and timer.battery != 0:								# 	hourly energy limit exceeded
											max_input = 0
											status_text	+= ', hourly energy limit exceeded'
					
					if max_input > max_input_power:
											max_input = max_input_power
				
				else:						max_input = max_input_power 								# the limit of the gti(s) by configuration
				
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
			
				if verbose:																				# show saw tooth values
					print(	'input history', send_history, '\t1:2 {: 4.1f} %\t 3:4 {: 4.1f} %'.format( 
							 round((1-(send_history[-1] / (0.01+send_history[-2])))*100,1), round((1-(send_history[-3] / (0.01+send_history[-4])))*100,1) ) )
			
			with open('/tmp/vz/soyo.log','w') as fo:													# send some values to volkszähler
				fo.write('%i: soyosend = %i\n'		% ( time(),	-send_power ) )							# the keywords have to be created as channels in vzlogger.conf to make it work there!
				fo.write('%i: zero_shift_w = %i\n'	% ( time(),	-zero_shift ) )
				fo.write('%i: bat_v = %f\n'			% ( time(),	bat_cont ) )
				
				if 'PPV'		in mppt_data['combined'].keys():		fo.write('%i: pv_w = %i\n'		% ( time(),	-mppt_data['combined']['PPV'] ) )
				if 'PPV'		in mppt_data['/dev/ttyACM0'].keys():	fo.write('%i: pv_w0 = %i\n'		% ( time(),	-mppt_data['/dev/ttyACM0']['PPV'] ) )
				if 'PPV'		in mppt_data['/dev/ttyACM1'].keys():	fo.write('%i: pv_w1 = %i\n'		% ( time(),	-mppt_data['/dev/ttyACM1']['PPV'] ) )
				if 'PPV'		in mppt_data['/dev/ttyACM2'].keys():	fo.write('%i: pv_w2 = %i\n'		% ( time(),	-mppt_data['/dev/ttyACM2']['PPV'] ) )
				
				if 'int_temp'	in mppt_data['/dev/ttyACM0'].keys():	fo.write('%i: int_temp1 = %i\n'	% ( time(),	mppt_data['/dev/ttyACM0']['int_temp'] ) )
				if 'int_temp'	in mppt_data['/dev/ttyACM1'].keys():	fo.write('%i: int_temp0 = %i\n'	% ( time(),	mppt_data['/dev/ttyACM1']['int_temp'] ) )
				if 'ext_temp'	in mppt_data['/dev/ttyACM0'].keys():	fo.write('%i: out_temp = %i\n'	% ( time(),	mppt_data['/dev/ttyACM0']['ext_temp'] ) )
				if 'ext_temp'	in mppt_data['/dev/ttyACM1'].keys():	fo.write('%i: bat_temp = %i\n'	% ( time(),	mppt_data['/dev/ttyACM1']['ext_temp'] ) )
				
				#if 'VPV'	in mppt_data['/dev/ttyACM0'].keys():	fo.write('%i: pv_u0 = %i\n'		% ( time(),	-mppt_data['/dev/ttyACM0']['VPV'] ) )
				#if 'VPV'	in mppt_data['/dev/ttyACM1'].keys():	fo.write('%i: pv_u1 = %i\n'		% ( time(),	-mppt_data['/dev/ttyACM1']['VPV'] ) )
				#if 'VPV'	in mppt_data['/dev/ttyACM2'].keys():	fo.write('%i: pv_u2 = %i\n'		% ( time(),	-mppt_data['/dev/ttyACM2']['VPV'] ) )
				
			if verbose: 
				if send_power == 0: print('\nmeter {:4d} W'.format(Ls_read),end='')						# show the meter readings, and zero shift
				else: 
					print('\nmeter {:4d} W ({}shift {} W '.format(Ls_read,'auto ' if conf['zero_shifting'] == 0 else '',abs(zero_shift)),end='' )
					if conf['zero_shifting'] <= 0: print('import)',end='')
					else: print('export)',end='')
				print(', interval %.2f s, %s'% (time()-last_runtime,strftime('%H:%M:%S')))
				print('inverter {:4d} W{}\n'.format(send_power, status_text))							# show the input data
			
			if conf['temp_alarm_enabled']:
				for port in esmart3_devs:
					if 'int_temp' in mppt_data[port].keys():
						if mppt_data[port]['int_temp'] > conf['rs485'][port]['alarm']['temp_int']:
							if verbose: print('\nTEMPERATURE ALARM internal temp', conf['rs485'][port]['name'], ':', mppt_data[port]['int_temp'],'°C\n')
							if temp_int_alarm_time + timedelta(seconds = conf['temp_alarm_interval']) < datetime.now():
								temp_int_alarm_time = datetime.now()
								system(conf['rs485'][port]['alarm']['int_cmd'])
					if 'ext_temp' in mppt_data[port].keys():
						if mppt_data[port]['ext_temp'] > conf['rs485'][port]['alarm']['temp_ext']:
							if verbose: print('\nTEMPERATURE ALARM external temp', conf['rs485'][port]['name'], ':', conf['rs485'][port]['temp_display'] ,':', mppt_data[port]['ext_temp'],'°C\n')
							if temp_int_alarm_time + timedelta(seconds = conf['temp_alarm_interval']) < datetime.now():
								temp_int_alarm_time = datetime.now()
								system(conf['rs485'][port]['alarm']['ext_cmd'])
			
			last_runtime = time()
			long_send_history = long_send_history[1:]+[send_power]										# provide a long send_power history
			
			long_meter_history = long_meter_history[1:]+[Ls_read if (send_power and not adjusted_power and not ramp_cnt) else 0]	# provide a long meter history, without ramp and power based adjusting
			
			if conf['zero_shifting'] == 0 and not adjusted_power:										# auto zero shift, follows the meter
				zero_shift = -abs(int(avg(sorted(long_meter_history)[4:14])))							# sort meter history, ignore 4 lowest, average of 5 to 15, negate
			else: zero_shift = conf['zero_shifting']
			
			
			for i in [1,2]:																				# send power demand two times to the inverters
				if send_power != 0:
					if conf['total_number_of_inverters'] == 1 or (sorted(long_send_history)[-4] <= conf['single_inverter_threshold']):	# filter 3 spikes before switching to all inverters
						open_soyosource = Serial(conf['basic_load_inverter_port'], 4800)
						set_soyo_demand(open_soyosource,send_power)										# ONE inverter is used for basic load
						open_soyosource.close()
						soyo_demands = '%i x %i W'%(1,send_power)
						n_active_inverters = 1
					else:																				# ALL inverters are used for higher demands
						for port in soyosource_devs: 
							open_soyosource = Serial(port, 4800)										# open the serial port for sending soyosource power demand
							set_soyo_demand(open_soyosource,int(1.0 * send_power / conf['total_number_of_inverters']))
							open_soyosource.close()
						soyo_demands = '%i x %i W'%(conf['total_number_of_inverters'],(1.0 * send_power / conf['total_number_of_inverters']))
						n_active_inverters = conf['total_number_of_inverters']
					if verbose: print('%i:  power request %s'%(i,soyo_demands))
				else:
					n_active_inverters = 0
					if verbose: print('. power request')												# don't send power request
				sleep(0.05)																				# wait
			
			for charger in esmart_handles: charger['obj'].open()										# open the esmart ports
			
			for i in [1,2]:																				# poll 2 times
				if datetime.now() > timeout_repeat or pv_cont != 0:										# after battery protection timeout or at day time
					for charger in esmart_handles:
						charger['obj'].esmart_status_request()											# request esmart3 device status
						if verbose:	print('%i:  %s : %s status request'%(i,charger['obj'].port,conf['rs485'][charger['obj'].port]['name']))
				elif verbose: 	print('. eSmart3 status')												# don't send but sleep
				sleep(0.22)
			
			for charger in esmart_handles: charger['obj'].close()
			
			
			if datetime.now().minute == 0 and datetime.now().second == 0: in_pc = 0						# reset battery energy counter every full hour
			else:	in_pc += max(0, send_power - pv_power)												# only count energy from battery
		
			if conf['discharge_timer']: timer.update()
			if stop_event.is_set(): break
			continue
	
	except KeyboardInterrupt:
		print("zeroinput interrupted.",file=sys.__stdout__)
	
	except Exception as e:
		print(e,file=sys.__stdout__)
		print_exc(file=sys.__stdout__)
	
	finally:
		if verbose: print('stop threads',file=sys.__stdout__)
		stop_event.set()																				# tell all threads to stop
		for i in victron_threads: i.join()
		for port in conf['rs485']: Serial(port).close()													# close all serial ports
		if verbose: print(sys.argv[0],"done.",file=sys.__stdout__)
	
exit(0)
