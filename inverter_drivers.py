#!/usr/bin/python3
# -*- coding: utf-8 -*-
# zeroinput - inverter driver abstraction v2.1
#
# A driver represents ONE entry from conf['inverters'] = ONE group of identical
# units sharing a single port (count >= 1). The staging logic computes a
# per-unit power value and calls set_power() with it; the driver translates that
# into the type-specific protocol and sends exactly one command per group.
#
# To add a new inverter type:
#   1. subclass InverterDriver
#   2. implement set_power(), sleep() and optionally read_status()
#   3. register the class in DRIVER_TYPES at the bottom
#   4. add entries with the new "type" to conf['inverters']
# No change to zeroinput.py staging logic is required.

from serial import Serial, SerialException
from time import sleep as _sleep


class InverterDriver:
	"""Base class. One instance = one group of identical units on one port.

	Attributes (populated from conf['inverters'][id]):
		id          free identifier (dict key in conf)
		name        display name
		port        serial path (one sender per port)
		stage       lowest power stage at which this group participates (1 = always)
		count       number of identical units sharing the port (one shared packet)
		min_power   minimum useful power PER SINGLE UNIT [W]; below it the unit sleeps
		max_power   maximum power PER SINGLE UNIT [W]
	"""

	def __init__(self, dev_id, cfg, verbose=False):
		self.id        = dev_id
		self.name      = cfg.get('name', dev_id)
		self.port      = cfg.get('port', '')
		# stage: list of stages the unit runs in (no upward/downward implication).
		# [1,2] = both, [1] = base load only, [2] = stage 2 only. A bare number
		# n is read as [n]. Stage 2 does NOT automatically include stage 1.
		_stage = cfg.get('stage', [1])
		self.stages    = [int(_stage)] if isinstance(_stage, (int, float)) else [int(s) for s in _stage]
		self.count     = int(cfg.get('count', 1))
		self.min_power = int(cfg.get('min_power', 0))
		self.max_power = int(cfg.get('max_power', 0))
		self.verbose   = verbose

	# --- group capacity helpers (used by the staging logic) ---

	def group_capacity(self):
		"""Total max power of the whole group [W]."""
		return self.count * self.max_power

	# --- lifecycle (override if the driver needs setup/teardown) ---

	def start(self):
		"""Called once at startup. Open persistent handles here if needed."""
		pass

	def stop(self):
		"""Called once at shutdown."""
		pass

	# --- control interface (must be implemented by subclasses) ---

	def set_power(self, watts_per_unit):
		"""Send one command to the group, instructing each unit to deliver
		watts_per_unit [W]. The staging logic guarantees
		min_power <= watts_per_unit <= max_power."""
		raise NotImplementedError

	def sleep(self):
		"""Put the group into its zero/idle state (0 W / passthrough)."""
		raise NotImplementedError

	# --- monitoring (optional) ---

	def read_status(self):
		"""Return a dict of measured values, or None if the type cannot report.
		Recommended keys: 'Pac', 'Vbat', 'Ibat', 'soc', 'state', 'temp'."""
		return None


class SoyosourceDriver(InverterDriver):
	"""Soyosource GTI / GTN limiter protocol over RS485 at 4800 baud.
	Stateless: opens the port, sends the demand packet twice (50 ms apart) for
	reliability, closes the port. All identical units on the port receive the
	same broadcast packet (no addressing)."""

	BAUD = 4800

	@staticmethod
	def _packet(power):
		"""Build the 8-byte Soyosource limiter packet for a per-unit power."""
		pu = power >> 8
		pl = power & 0xFF
		cs = 264 - pu - pl
		if cs > 255:
			if power > 250: cs -= 256
			else:           cs -= 255
		return bytearray([0x24, 0x56, 0x00, 0x21, pu, pl, 0x80, cs])

	def _send(self, power):
		pkt = self._packet(power)
		try:
			with Serial(self.port, self.BAUD) as ser:
				ser.write(pkt); ser.flush()
				_sleep(0.05)
				ser.write(pkt); ser.flush()
		except SerialException as e:
			if self.verbose: print('%s (%s): %s' % (self.name, self.port, e))

	def set_power(self, watts_per_unit):
		self._send(int(watts_per_unit))

	def sleep(self):
		# Soyosource interprets a 0 W demand as idle; one packet is enough.
		self._send(0)


class VictronMK3Driver(InverterDriver):
	"""Victron MultiPlus-II via MK2/MK3-USB (VE.Bus, no GX required).

	Active ESS power setpoint over the VE.Bus MK2 protocol (see vebus.py).

	SIGN CONVENTION (this driver, matching the Soyosource side):
	  set_power(positive watts) = FEED-IN  (battery -> grid/load)
	The underlying vebus.VEBus uses the opposite Victron convention
	(positive = charge), so the wrapper negates before calling it. An optional
	cfg['mk3_ess_sign'] (default 1) allows flipping if a particular wiring /
	CT placement reports the wrong direction.

	The setpoint must be refreshed < 60 s; zeroinput's ~1 s cycle guarantees
	this because send_to_inverters calls set_power()/sleep() every cycle.

	Startup performs: open port -> get_version (connection check) ->
	init_address -> scan_ess_assistant (resolve the setpoint RAM-ID). If any
	step fails the driver stays inactive (set_power becomes a logged no-op) so
	the rest of zeroinput keeps running."""

	def __init__(self, dev_id, cfg, verbose=False):
		super().__init__(dev_id, cfg, verbose)
		self._sign  = int(cfg.get('mk3_ess_sign', 1))	# 1: positive=feed-in
		self._bus   = None
		self._ready = False

	def start(self):
		try:
			import vebus
		except ImportError:
			if self.verbose:
				print('%s: vebus module not found — MK3 disabled' % self.name)
			return
		self._bus = vebus.VEBus(self.port, verbose=self.verbose)
		ver = self._bus.get_version()			# soft connection probe (may be None on some fw)
		if not self._bus.init_address():		# init_address is the real connectivity gate
			print('%s: no VE.Bus response on %s — MK3 inactive' % (self.name, self.port))
			return
		if not self._bus.scan_ess_assistant():
			print('%s: ESS assistant not found (is ESS configured in '
			      'VEConfigure?) — MK3 inactive' % self.name)
			return
		self._ready = True
		if self.verbose:
			print('%s: MK3 ready (mk2_version=%s, ess_ram_id=%s)' % (
				self.name, ver, self._bus.ess_setpoint_ram_id))

	def stop(self):
		if self._bus is not None:
			try:
				self._bus.set_power(0)		# leave in passthrough
				self._bus.close()
			except Exception:
				pass

	def set_power(self, watts_per_unit):
		"""watts_per_unit > 0 = feed-in (zeroinput/Soyosource convention).
		Negated to the Victron convention before writing."""
		if not self._ready:
			if self.verbose:
				print('%s: set_power %iW ignored (MK3 inactive)' % (self.name, watts_per_unit))
			return
		feed = int(watts_per_unit) * self._sign
		# vebus.set_power: positive = charge, negative = feed -> negate feed
		self._bus.set_power(-feed)

	def sleep(self):
		if not self._ready:
			return
		self._bus.set_power(0)				# 0 W = passthrough

	def read_status(self):
		if not self._ready:
			return None
		self._bus.send_snapshot_request()
		snap = self._bus.read_snapshot()
		if snap is None:
			return None
		# map to the common monitoring keys; Pac as feed-in positive
		return {
			'Pac':  -snap.get('inv_p', 0),	# flip to feed-in positive
			'Vbat': snap.get('bat_u'),
			'Ibat': snap.get('bat_i'),
			'soc':  snap.get('soc'),
			'out_p': snap.get('out_p'),
		}


DRIVER_TYPES = {
	'soyosource':  SoyosourceDriver,
	'victron_mk3': VictronMK3Driver,
}


def build_inverters(inverters_cfg, verbose=False):
	"""Instantiate one driver per entry in conf['inverters'].
	Returns a list of started InverterDriver instances.
	Validates: one sender per port (multiple identical units allowed via
	count, but two different inverter entries must not share a port)."""
	drivers = []
	ports_seen = {}
	for dev_id, cfg in inverters_cfg.items():
		typ = cfg.get('type')
		cls = DRIVER_TYPES.get(typ)
		if cls is None:
			print('inverter %s: unknown type %r — skipped' % (dev_id, typ))
			continue
		port = cfg.get('port', '')
		if port in ports_seen:
			print('ERROR: inverters %r and %r share port %s — only one sender '
			      'per port allowed (use count for identical units)' % (
			      ports_seen[port], dev_id, port))
			raise SystemExit(1)
		ports_seen[port] = dev_id
		drv = cls(dev_id, cfg, verbose)
		drv.start()
		drivers.append(drv)
	return drivers
