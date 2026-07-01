#!/usr/bin/python3
# -*- coding: utf-8 -*-
# zeroinput - VE.Bus MK2/MK3 low-level interface v2.2
#
# Direct control of a Victron MultiPlus-II in ESS mode 3 via an MK2/MK3-USB
# adapter (no GX device required). Sets the ESS power setpoint over the VE.Bus
# MK2 protocol by writing the ESS-assistant RAM variable.
#
# Derived from martiby/multiplus2 (vebus.py, MIT License, Martin Steppuhn),
# adapted to the zeroinput code style (tabs) and driver model. Protocol basis:
# "Interfacing with VE-Bus products - MK2 Protocol 3.14".
#
# Frame: <Length> 0xFF <Command> <Data...> <Checksum>
#   Length excludes itself and the checksum; checksum = (256 - sum(frame)) & 0xFF
#
# IMPORTANT sign convention of THIS low-level module (matches Victron MK3):
#   set_power(positive) -> CHARGE  (grid -> battery)
#   set_power(negative) -> FEED    (battery -> grid/load)
# The frame stores -power. The zeroinput driver wrapper flips this so that the
# higher layers can keep the Soyosource convention (positive = feed-in).

import struct
import time

try:
	import serial
	_SERIAL_OK = True
except ImportError:
	_SERIAL_OK = False


class VEBus:

	def __init__(self, port, verbose=False):
		self.port                 = port
		self.verbose              = verbose
		self.ess_setpoint_ram_id  = None		# resolved by scan_ess_assistant()
		self.serial               = None
		if _SERIAL_OK:
			self.open_port()

	def _log(self, msg):
		if self.verbose: print('vebus %s: %s' % (self.port, msg))

	def open_port(self):
		if not _SERIAL_OK:
			self._log('pyserial not available')
			return
		try:
			self.serial = serial.Serial(self.port, 2400, timeout=0)
		except Exception as e:
			self.serial = None
			self._log('open_port failed: %s' % e)

	def close(self):
		if self.serial is not None:
			try: self.serial.close()
			except Exception: pass
		self.serial = None

	# --- frame helpers ---

	@staticmethod
	def _fmt_hex(data):
		return ' '.join('%02X' % b for b in data)

	def build_frame(self, cmd, data):
		frame = bytes((len(data) + 2, 0xFF))
		if isinstance(cmd, str):
			frame += bytes((ord(cmd),))
		frame += bytes(data) if isinstance(data, (list, tuple)) else data
		checksum = 256 - sum(frame) & 0xFF
		return frame + bytes((checksum,))

	def send_frame(self, cmd, data):
		frame = self.build_frame(cmd, data)
		self.serial.reset_input_buffer()
		self.serial.write(frame)

	def receive_frame(self, head, timeout=0.5):
		"""Read until a frame starting with `head` (bytes or list of byte-patterns)
		is complete. Returns the frame bytes or raises on timeout/garbage."""
		rx = bytes()
		tout = time.perf_counter() + timeout
		while time.perf_counter() < tout:
			rx += self.serial.read(500)
			time.sleep(0.010)
			if isinstance(head, (list, tuple)):
				p = -1
				for h in head:
					p = rx.find(h)
					if p >= 0: break
			else:
				p = rx.find(head)
			if p >= 0:
				flen = rx[p] + 2				# full package length
				if (len(rx) - p) >= flen:
					return rx[p:p + flen]
		if rx:
			raise Exception('invalid rx frame %s' % self._fmt_hex(rx))
		raise Exception('receive timeout, no data')

	# --- connection / setup ---

	def get_version(self):
		"""Read MK2 version; also serves as a connection check. Returns int or None."""
		if self.serial is None: self.open_port()
		if self.serial is None: return None
		try:
			self.send_frame('V', [])
			rx = self.receive_frame(b'\x07\xFF', timeout=0.5)
			_cmd, mk2_version = struct.unpack('<BI', rx[2:7])
			self._log('mk2_version=%s' % mk2_version)
			return mk2_version
		except IOError:
			self.serial = None
			self._log('serial port failed')
		except Exception:
			return None

	def init_address(self):
		"""Init device address. A single Multiplus on the bus is address 0x00."""
		if self.serial is None: self.open_port()
		if self.serial is None: return False
		addr = 0x00
		try:
			self.send_frame('A', [0x01, addr])
			rx = self.receive_frame(b'\x04\xFF\x41')
			if rx[4] == addr:
				self._log('init_address %d ok' % addr)
				return True
			raise Exception('init_address failed')
		except IOError:
			self.serial = None
			self._log('serial port failed')
		except Exception as e:
			self._log('init_address: %s' % e)
		return False

	def scan_ess_assistant(self):
		"""Walk the assistant RAM records (from ID 128) to locate the ESS
		assistant and store the setpoint RAM-ID. Returns True/False."""
		if self.serial is None: self.open_port()
		if self.serial is None: return False
		ramid = 128
		for _ in range(8):
			try:
				data = struct.pack('<BH', 0x30, ramid)		# read ram id
				self.send_frame('X', data)
				rx = self.receive_frame(b'\x07\xFF\x58')
				ram = rx[4] + rx[5] * 256
				self._log('scan ramid=%d value=0x%04X' % (ramid, ram))
				if ram & 0xFFF0 == 0x0050:					# ESS assistant marker
					self.ess_setpoint_ram_id = ramid + 1
					self._log('ess assistant at ramid=%d, setpoint id=%d' % (
						ramid, self.ess_setpoint_ram_id))
					return True
				ramid += 1 + (ram & 0x000F)					# skip other assistants
			except IOError:
				self.serial = None
				self._log('serial port failed')
				return False
			except Exception as e:
				self._log('scan_ess_assistant: %s' % e)
				return False
		self._log('ess assistant not found')
		return False

	# --- control / monitoring ---

	def set_power(self, power):
		"""Set ESS power setpoint. POSITIVE = CHARGE, NEGATIVE = FEED (Victron MK3
		convention). Writes RAM only (flag 0x02) to avoid EEPROM wear. ~110 ms.
		Returns True/False."""
		if self.ess_setpoint_ram_id is None:
			self._log('set_power: ess_setpoint_ram_id not resolved')
			return False
		if self.serial is None: self.open_port()
		if self.serial is None: return False
		try:
			# cmd 0x37 = WriteViaID, flag 0x02 = RAM only, id, signed setpoint
			data = struct.pack('<BBBh', 0x37, 0x02, self.ess_setpoint_ram_id, -power)
			self.send_frame('X', data)
			rx = self.receive_frame([b'\x05\xFF\x58', b'\x03\xFF\x58'])
			if rx[3] == 0x87:
				self._log('set_power %dW ok' % power)
				return True
			raise Exception('invalid response')
		except IOError:
			self.serial = None
			self._log('serial port failed')
		except Exception as e:
			self._log('set_power %d: %s' % (power, e))
		return False

	def read_snapshot(self):
		"""Read a measurement snapshot. Returns dict or None.
		Keys: inv_p (W, +charge/-feed), out_p (AC out W), bat_u (V), bat_i (A),
		bat_p (W), soc (%)."""
		if self.serial is None: self.open_port()
		if self.serial is None: return None
		try:
			self.send_frame('X', [0x38])
			frame = self.receive_frame(b'\x0D\xFF\x58')
			if frame[3] != 0x99:
				raise Exception('invalid response')
			inv_p, out_p, bat_u, bat_i, soc = struct.unpack('<hhhhh', frame[4:14])
			r = {
				'inv_p': -inv_p,
				'out_p': out_p,
				'bat_u': round(bat_u / 100, 2),
				'bat_i': round(bat_i / 10, 1),
				'bat_p': round(bat_u / 100 * bat_i / 10),
				'soc':   round(soc / 2, 1),
			}
			self._log('snapshot %s' % r)
			return r
		except IOError:
			self.serial = None
			self._log('serial port failed')
		except Exception as e:
			self._log('read_snapshot: %s' % e)
		return None

	def send_snapshot_request(self):
		"""Trigger a measurement snapshot (no response). Pair with read_snapshot()."""
		if self.serial is None: self.open_port()
		if self.serial is None: return
		try:
			ids = [15, 16, 4, 5, 13]
			self.send_frame('F', [0x06] + ids)
		except IOError:
			self.serial = None
			self._log('serial port failed')
		except Exception as e:
			self._log('send_snapshot_request: %s' % e)
