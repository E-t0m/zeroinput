#!/usr/bin/python3
# -*- coding: utf-8 -*-

from requests import get
from datetime import timedelta, datetime

debug = False

begin	= (datetime.today() - timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0)
end		= (datetime.today() - timedelta(days=0)).replace(hour=0,minute=0,second=0,microsecond=0)
beginstamp	= str(int(begin.timestamp())).ljust(13,'0')
endstamp	= str(int(end.timestamp())).ljust(13,'0')

if debug:
	print('begin\t',begin )
	print('end\t',	end )

chans = dict(	pv_p	='your-id',
				soyo_p	='your-id',
				bezug_p	='your-id',
				klima_p	='your-id')

url = 'http://127.0.0.0:8080/data.json?from='+beginstamp+'&to='+endstamp
for key in chans.values(): url += '&uuid[]='+key

fdata = []

if debug: print(url)

if True:
	if debug: begin_request = datetime.now()
	resp = get(url=url)
	data = resp.json()
	if debug: print('request duration:', datetime.now()-begin_request )
	
	for i in data['data']:
		for j in chans.items():
			if i['uuid'] == j[1]: 
				fdata.append(abs(round(i['consumption'])))
				if debug: print(j[0].ljust(10),abs(i['consumption']))
				break
	
	with open('/home/vzlogger/daydata.log','a') as fo:
		fo.write(begin.strftime("%Y%m%d")+', '+str(fdata)[1:-1]+'\n')
	
	if debug: print(begin.strftime("%Y%m%d")+', '+str(fdata)[1:-1])
exit(0)
