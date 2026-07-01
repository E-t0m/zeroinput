#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""
ve_aggregator.py — VE.Direct Aggregator client module
Version: 2.0

Provides a single class VEDirect that combines:
  - readtext:  continuous parsing of the aggregated VE.Direct stream
  - sendhex:   SET and HEX command interface (readtext_sendhex firmware only)

Usage:
    from ve_aggregator import VEDirect

    vd = VEDirect('/dev/ttyACM3')
    vd.start()

    # read device data
    data = vd.get_all()           # {'SER#': {field: value, ...}}
    mppt = vd.get('0xA060:HQ2529K6QK4')  # single device or None

    # set charge power (readtext_sendhex only)
    vd.set_watts('0xA053', 500)   # single device by PID
    vd.set_watts('ALL', 1500)     # all devices

    # send arbitrary HEX command
    vd.hex_cmd('0xA053', ':154')  # restore text mode
    vd.hex_cmd('ALL', ':154')

    # read replies
    while True:
        reply = vd.get_reply()    # blocks until reply available
        print(reply)              # 'OK 0xA053 500W 19.5A'

    vd.stop()

    # or use as context manager:
    with VEDirect('/dev/ttyACM3') as vd:
        ...

Dependencies:
    pyserial

See also: vedirect_deaggregator.py — splits the aggregated stream into
individual virtual serial ports for Venus OS / Cerbo GX.
"""

VERSION = '2.0'

from serial      import Serial
from threading   import Thread, Event, Lock
from queue       import Queue, Empty
from time        import time, sleep


# ── VE.Direct field conversion ────────────────────────────────────────────────

_STRING_FIELDS = {
	'PID', 'SER#', 'FW', 'Checksum', 'MODE', 'WARN', 'ALARM',
	'RELAY', 'LOAD', 'AR', 'OR', 'ERR', 'CS', 'MPPT', 'MON',
	'Alarm', 'Relay', 'BMV',
}

def _parse_value(name, raw):
	"""Convert raw VE.Direct string value to Python type."""
	if name in _STRING_FIELDS:
		return raw
	try:
		v = int(raw)
		if name in ('V', 'VS', 'VM', 'VPV'):   return v / 1000.0   # mV -> V
		if name in ('I', 'IL'):                 return v / 1000.0   # mA -> A
		if name in ('CE',):                     return v / 1000.0   # mAh -> Ah
		return v
	except (ValueError, TypeError):
		pass
	try:
		return float(raw)   # e.g. TEMP field from DS18B20 pseudo-block
	except (ValueError, TypeError):
		return raw


def _check_checksum(raw_bytes):
	"""
	Validate VE.Direct block checksum.
	The sum of all bytes in the block (including the Checksum byte) must be 0 mod 256.
	Returns True if valid, False if invalid.
	"""
	return sum(raw_bytes) % 256 == 0


def parse_block(raw_bytes):
	"""
	Parse a complete VE.Direct text block into a dict.
	Returns dict with field names as keys, or None if block has no PID or invalid checksum.
	"""
	if not _check_checksum(raw_bytes):
		return None
	fields = {}
	try:
		text = raw_bytes.decode('ascii', errors='replace')
	except Exception:
		return None
	for line in text.splitlines():
		line = line.strip()
		if not line or line.startswith('Checksum'):
			continue
		if '\t' not in line:
			continue
		name, _, raw = line.partition('\t')
		name = name.strip(); raw = raw.strip()
		if name:
			fields[name] = _parse_value(name, raw)
	return fields if 'PID' in fields else None


# ── Reply types ───────────────────────────────────────────────────────────────

class Reply:
	"""Parsed reply from readtext_sendhex firmware."""
	__slots__ = ('raw', 'ok', 'pid', 'watts', 'amps', 'error',
	             'is_hex', 'hex_response')

	def __init__(self, line):
		self.raw          = line.strip()
		self.ok           = False
		self.pid          = None
		self.watts        = None
		self.amps         = None
		self.error        = None
		self.is_hex       = False
		self.hex_response = None
		self._parse()

	def _parse(self):
		parts = self.raw.split()
		if not parts: return
		if parts[0] == 'OK' and len(parts) >= 4:
			# OK <pid> <watts>W <amps>A
			self.ok    = True
			self.pid   = parts[1]
			try: self.watts = int(parts[2].rstrip('W'))
			except Exception: pass
			try: self.amps  = float(parts[3].rstrip('A'))
			except Exception: pass
		elif parts[0] == 'ERR' and len(parts) >= 3:
			self.pid   = parts[1]
			self.error = ' '.join(parts[2:])
		elif parts[0] == 'HEX_REPLY' and len(parts) >= 3:
			self.is_hex       = True
			self.pid          = parts[1]
			self.hex_response = parts[2]

	def __repr__(self):
		return f'Reply({self.raw!r})'

	def __str__(self):
		return self.raw


# ── Main class ────────────────────────────────────────────────────────────────

class VEDirect:
	"""
	Unified async client for the VE.Direct Aggregator.

	Runs two background threads:
	  _reader  — reads bytes from serial, parses blocks, queues replies
	  _sender  — sends SET/HEX commands from queue, enforces hysteresis

	Thread-safe: all public methods can be called from any thread.
	"""

	def __init__(self, port, baud=19200, on_block=None, on_alive=None, hysteresis_w=50,
	             device_timeout=5.0, pid_timeout=10.0):
		"""
		port           — serial device, e.g. '/dev/ttyUSB0'
		baud           — must match BAUD_OUT in firmware (default 19200)
		hysteresis_w   — minimum watt change before SET is re-sent (default 50)
		device_timeout — devices not seen within this time are excluded from
		                 get_all() by default (seconds, default 5.0)
		pid_timeout    — stale entry age for last_sent hysteresis (seconds)
		"""
		self.port           = port
		self.baud           = baud
		self.hysteresis_w   = hysteresis_w
		self.device_timeout = device_timeout
		self.pid_timeout    = pid_timeout

		self._data       = {}        # {ser: {field: value, 'ts': float}}
		self._data_lock  = Lock()

		self._cmd_queue  = Queue()   # (cmd_str,) — raw command lines
		self._reply_queue= Queue()   # Reply objects

		self._last_sent  = {}        # {ser: (watts, timestamp)}
		self._last_alive = 0.0       # timestamp of last ALIVE signal or data block
		self._firmware   = None      # firmware identification string (from WHO response)
		self._on_block   = on_block  # optional callback(ser, fields) per block
		self._on_alive   = on_alive  # optional callback() on each ALIVE keepalive

		self._stop       = Event()
		self._ser        = None
		self._ser_lock   = Lock()
		self._t_reader   = None
		self._t_sender   = None

	# ── lifecycle ─────────────────────────────────────────────────────────────

	def start(self):
		"""Start background threads and probe firmware type."""
		self._stop.clear()
		self._last_alive = time()
		self._firmware   = None
		print(f've_aggregator v{VERSION} — opening {self.port} at {self.baud} baud')
		self._t_reader = Thread(target=self._reader, daemon=True, name='vd-reader')
		self._t_sender = Thread(target=self._sender, daemon=True, name='vd-sender')
		self._t_reader.start()
		self._t_sender.start()
		# probe firmware type after brief settle
		def _probe():
			from time import sleep as _sleep
			_sleep(1.0)
			self._cmd_queue.put('WHO\n')
		Thread(target=_probe, daemon=True).start()
		return self

	def stop(self):
		"""Stop background threads and close serial port."""
		self._stop.set()
		self._cmd_queue.put(None)    # unblock sender
		if self._t_reader: self._t_reader.join(timeout=3)
		if self._t_sender: self._t_sender.join(timeout=3)
		with self._ser_lock:
			if self._ser:
				try: self._ser.close()
				except Exception: pass
				self._ser = None

	def __enter__(self):
		return self.start()

	def __exit__(self, *_):
		self.stop()

	# ── read interface ────────────────────────────────────────────────────────

	def get_all(self, max_age=None):
		"""
		Return dict of all known devices: {ser: {field: value, ...}}

		max_age — exclude devices older than this (seconds).
		          Defaults to self.device_timeout. Pass 0 for no filter.
		"""
		if max_age is None:
			max_age = self.device_timeout
		now = time()
		with self._data_lock:
			if max_age <= 0:
				return dict(self._data)
			return {ser: dict(d) for ser, d in self._data.items()
			        if now - d.get('ts', 0) <= max_age}

	def get(self, identifier, max_age=None):
		"""
		Return data for a single device by SER# or PID, or None.

		identifier -- SER# (e.g. 'HQ2529K6QK4') or PID (e.g. '0xA060').
		             SER# is preferred; PID is ambiguous when multiple
		             devices share the same PID.
		max_age    -- return None if older than this (seconds).
		"""
		if max_age is None:
			max_age = self.device_timeout
		with self._data_lock:
			# 1. exact key match by SER#
			d = self._data.get(str(identifier))
			for v in self._data.values():
				if v.get('SER#') == identifier:
					d = v
					break
		# 2. search by SER# field
		if d is None:
			for v in self._data.values():
				if v.get('SER#') == identifier:
					d = v
					break
		if d is None:
			return None
		if max_age > 0 and time() - d.get('ts', 0) > max_age:
			return None
		return dict(d)

	def is_alive(self, timeout=15.0):
		"""
		Return True if MCU is alive — received data or ALIVE signal within timeout.
		timeout — seconds since last activity (default 15s, firmware sends every 10s)
		"""
		return time() - self._last_alive < timeout

	def keys(self, max_age=None):
		"""Return set of all active device keys (PID:SER# or PID)."""
		return set(self.get_all(max_age=max_age).keys())

	def ser_numbers(self, max_age=None):
		"""Return list of SER# strings for all active devices."""
		return [d.get('SER#', '') for d in self.get_all(max_age=max_age).values()
		        if d.get('SER#')]

	def combined(self, max_age=None):
		"""
		Merge all device data into one dict.
		  Vbat  — averaged across all devices
		  PPV   — summed
		  I     — summed
		  Other fields taken from first device seen
		"""
		data = self.get_all(max_age=max_age)
		result = {}
		vbat_vals = []; ppv_sum = 0.0; i_sum = 0.0
		for fields in data.values():
			for k, v in fields.items():
				if k == 'ts': continue
				if k == 'V':   vbat_vals.append(v)
				elif k == 'PPV': ppv_sum += v
				elif k == 'I':   i_sum   += v
				elif k not in result: result[k] = v
		if vbat_vals: result['Vbat'] = round(sum(vbat_vals)/len(vbat_vals), 3)
		if ppv_sum:   result['PPV']  = ppv_sum
		if i_sum:     result['I']    = round(i_sum, 3)
		return result

	# ── command interface (readtext_sendhex firmware only) ────────────────────

	def set_watts(self, ser, watts):
		"""
		Limit charge power of an MPPT charger.

		ser   -- SER# string, e.g. 'HQ2529K6QK4', or 'ALL'
		watts -- target watts (int >= 0). 0 stops charging.

		Applies hysteresis: command is suppressed if value changed by
		less than self.hysteresis_w since last sent value.
		Replies are queued and readable via get_reply() / get_replies().
		"""
		ser   = 'ALL' if str(ser).upper() == 'ALL' else str(ser)
		watts = max(0, int(watts))

		# hysteresis check
		last = self._last_sent.get(ser)
		if last is not None:
			last_w, last_t = last
			if (abs(watts - last_w) < self.hysteresis_w and
				time() - last_t < self.pid_timeout):
				return

		self._last_sent[ser] = (watts, time())
		self._cmd_queue.put(f'SET {ser} {watts}\n')

	def hex_cmd(self, ser, hex_str):
		"""
		Send arbitrary VE.Direct HEX string to a device.

		ser     -- SER# string or 'ALL'
		hex_str -- HEX command, e.g. ':154' or ':154\n'

		No hysteresis. Replies readable via get_reply() / get_replies().
		Devices return to text mode on their own after HEX commands.
		"""
		ser     = 'ALL' if str(ser).upper() == 'ALL' else str(ser)
		hex_str = hex_str.strip()
		self._cmd_queue.put(f'HEX {ser} {hex_str}\n')

	def restore_text_mode(self, ser='ALL'):
		"""Send :154 to device(s) -- only needed if explicitly requested."""
		self.hex_cmd(ser, ':154')

	# ── reply interface ───────────────────────────────────────────────────────

	def get_reply(self, timeout=3.0):
		"""
		Wait for and return the next Reply object, or None on timeout.
		Blocks up to timeout seconds.
		"""
		try:
			return self._reply_queue.get(timeout=timeout)
		except Empty:
			return None

	def get_replies(self, timeout=3.0, inter_reply=0.5):
		"""
		Collect all Reply objects received within timeout seconds.
		After each reply, waits up to inter_reply seconds for more before stopping.
		Returns list of Reply objects (may be empty).
		"""
		replies = []
		deadline = time() + timeout
		while time() < deadline:
			r = self.get_reply(timeout=max(0, deadline - time()))
			if r is None: break
			replies.append(r)
			# wait briefly for additional replies (e.g. SET ALL sends one per device)
			t_next = time() + inter_reply
			while time() < t_next and time() < deadline:
				r2 = self.get_reply(timeout=min(inter_reply, max(0, deadline - time())))
				if r2 is None: break
				replies.append(r2)
				t_next = time() + inter_reply   # reset window on each new reply
		return replies

	def drain_replies(self):
		"""Return all currently queued replies without waiting."""
		replies = []
		while True:
			try:
				replies.append(self._reply_queue.get_nowait())
			except Empty:
				break
		return replies

	# ── background threads ────────────────────────────────────────────────────

	def _open_serial(self):
		"""Open serial port, return True on success."""
		try:
			with self._ser_lock:
				if self._ser is None:
					self._ser = Serial(self.port, self.baud, timeout=0.1)
			return True
		except Exception:
			sleep(2)
			return False

	def _reader(self):
		"""Background thread: read bytes, parse blocks and reply lines."""
		buf      = bytearray()   # current VE.Direct block accumulator
		line_buf = bytearray()   # current line accumulator (for ALIVE/reply detection)

		while not self._stop.is_set():
			if not self._open_serial():
				continue
			try:
				with self._ser_lock:
					chunk = self._ser.read(256)
			except Exception:
				with self._ser_lock:
					try: self._ser.close()
					except Exception: pass
					self._ser = None
				buf.clear(); line_buf.clear()
				continue

			for b in chunk:
				c = chr(b)

				# accumulate line buffer (strip \r, end on \n)
				if c == '\r':
					pass
				elif c == '\n':
					line = line_buf.decode('ascii', errors='replace').strip()
					line_buf.clear()
					if line == 'ALIVE':
						self._last_alive = time()
						if self._on_alive: self._on_alive()
						buf.clear()
						continue
					if line.startswith(('OK ', 'ERR ', 'HEX_REPLY ')):
						self._reply_queue.put(Reply(line))
						buf.clear()
						continue
					# block end: Checksum line
					if line.startswith('Checksum\t') and buf:
						buf.append(b)   # include the \n
						self._handle_block(bytes(buf))
						buf.clear()
						continue
				else:
					line_buf.append(b)

				# accumulate block bytes — skip \r and \n before first data byte
				if buf or c not in ('\r', '\n'):
					buf.append(b)

				if len(buf) > 1024:
					buf.clear()

	def _handle_block(self, raw):
		"""Parse block — either a VE.Direct data block, a reply line or ALIVE."""
		try:
			text = raw.decode('ascii', errors='replace').strip()
		except Exception:
			return

		# ALIVE keepalive from firmware — update connection timestamp
		if text == 'ALIVE':
			self._last_alive = time()
			if self._on_alive: self._on_alive()
			return

		# firmware identification reply
		if text.startswith('READTEXT ') or text.startswith('SENDHEX '):
			self._firmware = text
			print(f'firmware: {text}')
			return

		# single-line replies from sendhex firmware
		if text.startswith(('OK ', 'ERR ', 'HEX_REPLY ')):
			self._reply_queue.put(Reply(text))
			return

		# VE.Direct data block
		block = parse_block(raw)
		if block:
			pid  = str(block.get('PID', ''))
			ser  = str(block.get('SER#', ''))
			key  = ser if ser else pid
			block['ts'] = time()
			with self._data_lock:
				self._data[key] = block
			if self._on_block and ser:
				self._on_block(ser, dict(block))
			self._last_alive = time()

	def _sender(self):
		"""Background thread: send queued commands over serial."""
		while not self._stop.is_set():
			try:
				cmd = self._cmd_queue.get(timeout=1)
			except Empty:
				continue
			if cmd is None:
				break
			if not self._open_serial():
				# re-queue command
				self._cmd_queue.put(cmd)
				continue
			try:
				with self._ser_lock:
					self._ser.write(cmd.encode())
					self._ser.flush()
			except Exception:
				with self._ser_lock:
					try: self._ser.close()
					except Exception: pass
					self._ser = None
				# re-queue command
				self._cmd_queue.put(cmd)


# ── convenience ───────────────────────────────────────────────────────────────

def iter_devices(data):
	"""Iterate over get_all() result, yielding (pid, fields)."""
	for pid, fields in data.items():
		yield pid, fields


# ── example ───────────────────────────────────────────────────────────────────

if __name__ == '__main__':

	PORT = '/dev/ttyACM3'

	print(f'Connecting to {PORT}...\n')

	with VEDirect(PORT, hysteresis_w=50) as vd:

		# wait for first data
		sleep(2)

		# print all device data once per second
		for _ in range(5):
			sleep(1)
			data = vd.get_all()
			for pid, fields in iter_devices(data):
				age = round(time() - fields.get('ts', 0), 1)
				print(f'  {pid}  V={fields.get("V","?")}V  '
				      f'I={fields.get("I","?")}A  '
				      f'PPV={fields.get("PPV","?")}W  '
				      f'age={age}s')
			c = vd.combined()
			print(f'  combined: Vbat={c.get("Vbat","?")}V  '
			      f'PPV={c.get("PPV","?")}W  I={c.get("I","?")}A\n')

		# send SET command (readtext_sendhex firmware required)
		print('Setting all devices to 1500W...')
		vd.set_watts('ALL', 1500)
		for r in vd.get_replies(timeout=3):
			print(f'  {r}')

		# send HEX command
		print('Sending HEX ping...')
		vd.hex_cmd('ALL', ':154')
		for r in vd.get_replies(timeout=2):
			print(f'  {r}')
