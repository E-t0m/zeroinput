#!/usr/bin/python3
# -*- coding: utf-8 -*-
# unit test for webconfig.py — cross-origin protection of the HTTP API
import sys, json, threading
from os.path import abspath, dirname
from http.server import HTTPServer
from http.client import HTTPConnection
sys.path.insert(0, dirname(dirname(abspath(__file__))))	# the modules live in the repository root

import webconfig

webconfig.WebconfigHandler.web_stats = False
server = HTTPServer(('127.0.0.1', 0), webconfig.WebconfigHandler)
port = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()

def request(method, path, headers=None, body=None):
	c = HTTPConnection('127.0.0.1', port, timeout=5)
	c.request(method, path, body=body, headers=headers or {})
	r = c.getresponse()
	data = r.read()
	c.close()
	return r.status, dict(r.getheaders()), data

failures = 0
def check(name, cond):
	global failures
	print(('ok   ' if cond else 'FAIL ') + name)
	if not cond: failures += 1

host = '127.0.0.1:%i' % port
# an empty "updates" never reaches the file, so no conf is written by this test
body = json.dumps({'updates': {}})

st, hd, _ = request('GET', '/api/status')
check('GET /api/status works', st == 200)
check('no CORS header on responses', 'Access-Control-Allow-Origin' not in hd)

st, _, _ = request('OPTIONS', '/api/conf', {'Origin': 'http://evil.example',
	'Access-Control-Request-Method': 'POST', 'Access-Control-Request-Headers': 'content-type'})
check('CORS preflight is not answered positively', st >= 400)

st, _, _ = request('POST', '/api/conf', {'Content-Type': 'text/plain', 'Host': host}, body)
check('POST with text/plain is rejected (415)', st == 415)

st, _, _ = request('POST', '/api/conf', {'Host': host}, body)
check('POST without Content-Type is rejected (415)', st == 415)

st, _, _ = request('POST', '/api/conf', {'Content-Type': 'application/json',
	'Host': host, 'Origin': 'http://evil.example'}, body)
check('POST from foreign Origin is rejected (403)', st == 403)

st, _, _ = request('POST', '/api/restart?service=foo', {'Content-Type': 'text/plain', 'Host': host})
check('POST /api/restart with text/plain is rejected (415)', st == 415)

st, _, _ = request('GET', '/api/restart?service=zeroinput')
check('GET /api/restart does not restart anything (404)', st == 404)

st, _, _ = request('POST', '/api/restart?service=foo', {'Content-Type': 'application/json',
	'Host': host, 'Origin': 'http://' + host}, '{}')
check('same-origin POST /api/restart reaches the handler (unknown service → 400)', st == 400)

st, _, _ = request('POST', '/api/conf', {'Content-Type': 'application/json; charset=utf-8',
	'Host': host, 'Origin': 'http://' + host}, body)
check('same-origin JSON POST reaches the handler (no updates → 400)', st == 400)

server.shutdown()
print('%i failure(s)' % failures)
sys.exit(1 if failures else 0)
