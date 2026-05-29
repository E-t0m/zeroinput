#!/usr/bin/python3
# -*- coding: utf-8 -*-

import requests
from time import time, sleep

debug = False
sleep(7) # shift network traffic

url = 'http://192.168.178.104/cm?cmnd=status%2010'
z = 0
for i in range(0,3):
	resp = requests.get(url=url)
	data = resp.json()
	
	if debug: print('read')
	
	try: power = data['StatusSNS']['ENERGY']['Power']
	except:
		if debug: print('sleep')
		sleep(20)
		pass
	else:
		power = sum(power)	# 2 way power sensor in parallel
		for j in range(0,20):
			with open('/tmp/vz/output_to_vz.log','a') as fo:
				fo.write('%i: pool_p = %i\n'	% ( time(), power ) )
			if debug:
				print('%i: pool_p = %i\n'	% ( time(), power ) )
				z += 1
				print(i,j,z)
			sleep(1)
	
exit(0)
