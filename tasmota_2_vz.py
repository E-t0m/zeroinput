#!/usr/bin/python3
# -*- coding: utf-8 -*-

import requests
from time import time, sleep

debug = False

url = 'http://111.222.333.444/cm?cmnd=status%2010'
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
		for j in range(0,20):
			with open('/tmp/vz/soyo.log','a') as fo:
				fo.write('%i: klima_p = %i\n'	% ( time(), power ) )
			if debug:
				print('%i: klima_p = %i\n'	% ( time(), power ) )
				z += 1
				print(i,j,z)
			sleep(1)
	
exit(0)
