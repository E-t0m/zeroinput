#!/usr/bin/python3
# -*- coding: utf-8 -*-
# zeroinput - webconfig HTTP server v2.0
# started as thread in zeroinput.py when conf['webconfig_port'] > 0
import json
import subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler
from os.path import join, dirname, abspath

BASE_DIR = dirname(abspath(__file__))

def _read(path):
	try:
		with open(path, 'r') as f: return f.read()
	except: return None

def _write(path, content):
	try:
		with open(path, 'w') as f: f.write(content)
		return True
	except: return False

def _syntax_check(code):
	import tempfile, os
	tmp = tempfile.mktemp(suffix='.py')
	try:
		with open(tmp, 'w') as f: f.write(code)
		r = subprocess.run(['python3', '-m', 'py_compile', tmp], capture_output=True, text=True)
		return r.stderr.replace(tmp, '<file>') if r.returncode != 0 else None
	finally:
		try: os.unlink(tmp)
		except: pass

class WebconfigHandler(BaseHTTPRequestHandler):

	def log_message(self, fmt, *args): pass

	def _send_json(self, code, data):
		body = json.dumps(data).encode()
		self.send_response(code)
		self.send_header('Content-Type',				'application/json')
		self.send_header('Content-Length',				len(body))
		self.send_header('Access-Control-Allow-Origin',	'*')
		self.end_headers()
		self.wfile.write(body)

	def _send_html(self, body):
		self.send_response(200)
		self.send_header('Content-Type',	'text/html; charset=utf-8')
		self.send_header('Content-Length',	len(body))
		self.end_headers()
		self.wfile.write(body)

	def do_OPTIONS(self):
		self.send_response(200)
		self.send_header('Access-Control-Allow-Origin',	'*')
		self.send_header('Access-Control-Allow-Methods',	'GET, POST, OPTIONS')
		self.send_header('Access-Control-Allow-Headers',	'Content-Type')
		self.end_headers()

	def _timer_path(self):
		raw = _read(join(BASE_DIR, 'zeroinput.conf'))
		try:
			p = json.loads(raw).get('discharge_t_file', 'timer.txt')
			return p if p.startswith('/') else join(BASE_DIR, p)
		except: return join(BASE_DIR, 'timer.txt')

	def do_GET(self):
		path = self.path.split('?')[0]

		if path in ('/', '/zeroinput_webconfig.html'):
			try:
				with open(join(BASE_DIR, 'zeroinput_webconfig.html'), 'rb') as f:
					body = f.read()
				self._send_html(body)
			except Exception as e:
				self._send_json(500, {'error': str(e)})

		elif path == '/api/conf':
			content = _read(join(BASE_DIR, 'zeroinput.conf'))
			self._send_json(200 if content else 500,
				{'content': content} if content else {'error': 'read failed'})

		elif path == '/api/predictor':
			content = _read(join(BASE_DIR, 'predictor.py'))
			self._send_json(200 if content is not None else 500,
				{'content': content} if content is not None else {'error': 'read failed'})

		elif path == '/api/restart':
			import subprocess
			try:
				r = subprocess.run(['sudo', 'systemctl', 'restart', 'zeroinput'],
					capture_output=True, text=True, timeout=10)
				if r.returncode == 0:
					self._send_json(200, {'ok': True})
				else:
					self._send_json(500, {'error': r.stderr.strip() or 'restart failed'})
			except Exception as e:
				self._send_json(500, {'error': str(e)})
			return

		elif path == '/api/timer':
			content = _read(self._timer_path()) or ''
			self._send_json(200, {'content': content})

		elif path == '/api/status':
			self._send_json(200, {'status': 'ok'})

		elif path == '/api/flags':
			self._send_json(200, {'web_stats': self.web_stats})

		else:
			self._send_json(404, {'error': 'not found'})

	def do_POST(self):
		path = self.path.split('?')[0]

		if path == '/api/restart':
			import subprocess
			try:
				r = subprocess.run(['sudo', 'systemctl', 'restart', 'zeroinput'],
					capture_output=True, text=True, timeout=10)
				if r.returncode == 0:
					self._send_json(200, {'ok': True})
				else:
					self._send_json(500, {'error': r.stderr.strip() or 'restart failed'})
			except Exception as e:
				self._send_json(500, {'error': str(e)})
			return

		length = int(self.headers.get('Content-Length', 0))
		try:
			body = json.loads(self.rfile.read(length))
		except Exception as e:
			self._send_json(400, {'error': 'invalid JSON: %s' % e})
			return

		if path == '/api/conf':
			updates = body.get('updates', {})
			if not updates:
				self._send_json(400, {'error': 'no updates provided'})
				return
			# read current file and replace values in-place via regex
			import re
			raw = _read(join(BASE_DIR, 'zeroinput.conf'))
			if raw is None:
				self._send_json(500, {'error': 'read failed'})
				return
			for k, v in updates.items():
				enc = json.dumps(v, ensure_ascii=False)
				if isinstance(v, (list, dict)):
					# use bracket counter to find exact end of array/object
					open_b, close_b = ('[',']') if isinstance(v, list) else ('{','}')
					m = re.search('"' + re.escape(k) + r'"\s*:\s*', raw)
					if m:
						start = m.end()
						depth = 0
						for idx in range(start, len(raw)):
							if raw[idx] == open_b: depth += 1
							elif raw[idx] == close_b:
								depth -= 1
								if depth == 0:
									raw = raw[:m.start()] + '"' + k + '": ' + enc + raw[idx+1:]
									break
				else:
					pat = r'("' + re.escape(k) + r'"\s*:\s*)([^,\n\r\t}]+)'
					raw = re.sub(pat, lambda m: m.group(1) + enc, raw, count=1)
			try: json.loads(raw)
			except Exception as e:
				self._send_json(400, {'error': 'result invalid JSON: %s' % e})
				return
			ok = _write(join(BASE_DIR, 'zeroinput.conf'), raw)
			self._send_json(200 if ok else 500, {'ok': ok} if ok else {'error': 'write failed'})

		elif path == '/api/predictor':
			content = body.get('content', '')
			err = _syntax_check(content)
			if err:
				self._send_json(400, {'error': err})
				return
			ok = _write(join(BASE_DIR, 'predictor.py'), content)
			self._send_json(200 if ok else 500, {'ok': ok} if ok else {'error': 'write failed'})

		elif path == '/api/timer':
			ok = _write(self._timer_path(), body.get('content', ''))
			self._send_json(200 if ok else 500, {'ok': ok} if ok else {'error': 'write failed'})

		else:
			self._send_json(404, {'error': 'not found'})


def start(port, stop_event, web_stats=False):
	"""Start webconfig HTTP server. Stops when stop_event is set."""
	WebconfigHandler.web_stats = web_stats
	try:
		server = HTTPServer(('0.0.0.0', port), WebconfigHandler)
		server.timeout = 1.0
		print('webconfig server on port %i' % port)
		while not stop_event.is_set():
			server.handle_request()
		server.server_close()
		print('webconfig server stopped')
	except Exception as e:
		print('webconfig server error: %s' % e)
		import traceback; traceback.print_exc()
