#!/usr/bin/python3
# -*- coding: utf-8 -*-
# zeroinput - load predictor module v2.0
# PLAN C: k-means low-level stabilisation
# Always aims for low_level regardless of phase.
# Avoids export while stabilising inverter output during cyclic loads.
from time import time, strftime, localtime

def avg(lst):
	return sum(lst) / len(lst) if lst else 0

# ---------------------------------------------------------------------------
# Predictor configuration – edit here, not in zeroinput.conf
# ---------------------------------------------------------------------------
VERSION			= 10
LOG_FILE		= '/tmp/predictor.log'	# '' = no log
MIN_SPREAD_W	= 150	# W: minimum spread between LOW and HIGH centroid
STARTUP_S		= 10	# s: observation time after start before offset becomes active
SHORT_PEAK_MAX	= 8		# s: peaks longer than this are not counted as short cyclic peaks
MAX_HIST		= 60	# history buffer size (samples)
TRANSITIONS_MIN	= 4		# phase transitions required before offset becomes active
# ---------------------------------------------------------------------------

# Log columns: name -> description
# Order determines column order in the log file.
LOG_COLUMNS = [
	('ts',			'Unix timestamp (float)'),
	('hms',			'Time HH:MM:SS'),
	('Ls_read',		'Meter reading W (input)'),
	('last2_send',	'Last demand W (input)'),
	('est_load',	'Estimated load W (Ls_read + last2_send)'),
	('hist_len',	'Number of values in history'),
	('low',			'k-means LOW centroid W'),
	('high',		'k-means HIGH centroid W'),
	('spread',		'Spread HIGH-LOW W'),
	('phase',		'Current phase (low/high)'),
	('trans',		'Phase transitions counted'),
	('offset',		'Predictive offset W (output)'),
	('override',	'ramp_override active (0/1)'),
	('pause_rem',	'Remaining override time s'),
	('rise_cnt',	'Consecutive cycles above 400W'),
	('in_peak',		'Peak currently running (0/1)'),
	('ls_hi_age',	'Seconds since Ls_read > LS_OVERRIDE_THR'),
	('peak_cnt',	'Number of short peaks in peak_dur_hist'),
]

_LOG_HEADER = '\t'.join(col for col, _ in LOG_COLUMNS)


class LoadPredictor:
	"""Binary load prediction via k-means.
	Detects LOW/HIGH load levels and always aims for low_level.
	In LOW-phase: Ls_read ≈ 0W.
	In HIGH-phase: load draws the difference from the grid.
	Disabled automatically when load is unimodal (spread < MIN_SPREAD_W).
	Module-level configuration (LOG_FILE, MIN_SPREAD_W, STARTUP_S, SHORT_PEAK_MAX)
	is defined at the top of this file.
	Only one key is read from zeroinput.conf:
		load_prediction : true / false  (default true)
	"""


	def __init__(self, conf, verbose=False):
		self.verbose		= verbose
		self.enabled		= bool(conf.get('load_prediction', True))
		self.MIN_SPREAD		= int(conf.get('min_spread_w', MIN_SPREAD_W))
		self.startup_end	= time() + STARTUP_S
		self.history		= []
		self.MAX_HIST		= MAX_HIST
		self.low_level		= None
		self.high_level		= None
		self.transition_cnt	= 0
		self.current_phase	= None
		self.offset			= 0
		# ramp override via peak detection
		self.ramp_override_by_predictor	= False
		self.peak_start		= None		# timestamp of current peak start
		self.high_rise_cnt	= 0			# consecutive cycles above threshold
		self.PEAK_RISE_MIN	= 2			# cycles above threshold before peak is counted
		self.OVERRIDE_DELAY	= 10		# s: wait before setting override ON
		self.peak_dur_hist	= []		# [(timestamp, duration_s)] of last peaks
		self.PEAK_WINDOW	= 120		# s: both trigger peaks must be within this window
		self.PAUSE_AFTER	= 2			# short peaks required to activate override
		self.PAUSE_DURATION	= 120		# s: override duration (extended on each new peak)
		self.LONG_PEAK_MIN	= 10		# s: peak longer than this cancels override
		self.SHORT_PEAK_MAX	= SHORT_PEAK_MAX
		self.pause_until	= 0			# timestamp until override active
		self._inter_peak_buf= []		# last2_send samples between peaks for override target
		self._override_target= None		# mean inter-peak baseline used when low_level unknown
		# sustained load detection during override
		self.high_ls_since	= None		# time() when Ls_read first exceeded threshold
		self.LS_OVERRIDE_THR= 200		# W: Ls_read threshold to cancel override
		self._ls_hi_dip_cnt	= 0			# consecutive cycles below LS_OVERRIDE_THR during override
		# logging
		self._log_path		= LOG_FILE
		self._log_fh		= None
		self._log_open()
		if verbose:
			print('load prediction %s, min_spread_w %i  (predictor v%i)' % (
				'enabled' if self.enabled else 'DISABLED', self.MIN_SPREAD, VERSION))

	def __del__(self):
		if self._log_fh:
			try:	self._log_fh.close()
			except Exception: pass

	def _log_open(self):
		"""Open log file and write header. Column names with descriptions are
		printed to stdout on startup if verbose."""
		if not self._log_path:
			return
		try:
			self._log_fh = open(self._log_path, 'a')
			if self.verbose:
				print('predictor v%i log: %s' % (VERSION, self._log_path))
				print('predictor log columns:')
				for col, desc in LOG_COLUMNS:
					print('  %-14s %s' % (col, desc))
			self._log_fh.write('# predictor v%i log started %s\n' % (VERSION, strftime('%Y-%m-%d %H:%M:%S')))
			self._log_fh.write('# ' + _LOG_HEADER + '\n')
			self._log_fh.flush()
		except Exception as e:
			print('predictor log open failed: %s' % e)
			self._log_fh = None

	def _log(self, now, Ls_read, last2_send, est_load):
		"""Write one row of all relevant state variables to the log file."""
		if not self._log_fh:
			return
		low		= self.low_level  if self.low_level  is not None else ''
		high	= self.high_level if self.high_level is not None else ''
		spread	= (self.high_level - self.low_level) if (self.low_level is not None and self.high_level is not None) else ''
		phase		= self.current_phase if self.current_phase else ''
		pause_rem	= max(0, int(self.pause_until - now)) if self.ramp_override_by_predictor else 0
		in_peak		= 1 if self.peak_start is not None else 0
		ls_hi_age	= int(now - self.high_ls_since) if self.high_ls_since is not None else ''
		peak_cnt	= len(self.peak_dur_hist)
		row = '%f\t%s\t%i\t%i\t%i\t%i\t%s\t%s\t%s\t%s\t%i\t%i\t%i\t%i\t%i\t%i\t%s\t%i' % (
			now,
			strftime('%H:%M:%S', localtime(now)),
			Ls_read,
			last2_send,
			est_load,
			len(self.history),
			low, high, spread, phase,
			self.transition_cnt,
			self.offset,
			1 if self.ramp_override_by_predictor else 0,
			pause_rem,
			self.high_rise_cnt,
			in_peak,
			ls_hi_age,
			peak_cnt,
		)
		try:
			self._log_fh.write(row + '\n')
			self._log_fh.flush()
		except Exception as e:
			print('predictor log write failed: %s' % e)

	def reload_conf(self, conf):
		"""Called when zeroinput.conf changes.
		STARTUP_S, SHORT_PEAK_MAX and LOG_FILE are defined in predictor.py and
		only change via module reload (reload_predictor_if_changed).
		load_prediction and min_spread_w are hot-reloadable from conf."""
		self.enabled    = bool(conf.get('load_prediction', True))
		self.MIN_SPREAD = int(conf.get('min_spread_w', MIN_SPREAD_W))
		if self.verbose: print('predictor v%i reloaded: enabled=%s min_spread=%i SHORT_PEAK_MAX=%is' % (
			VERSION, self.enabled, self.MIN_SPREAD, self.SHORT_PEAK_MAX))

	def _reset(self):
		"""Full reset – clears history and learning state, starts cool-down."""
		if self._log_fh:
			try:
				self._log_fh.write('# RESET %s\n' % strftime('%Y-%m-%d %H:%M:%S'))
				self._log_fh.flush()
			except Exception: pass
		self.history.clear()
		self.low_level					= None
		self.high_level					= None
		self.transition_cnt				= 0
		self.current_phase				= None
		self.offset						= 0
		self.peak_dur_hist				= []
		self.high_rise_cnt				= 0
		self.peak_start					= None
		self.ramp_override_by_predictor	= False
		self._inter_peak_buf			= []
		self._override_target			= None
		self.high_ls_since				= None
		self._ls_hi_dip_cnt				= 0
		self.startup_end				= time() + self.MAX_HIST	# cool-down
		if self.verbose: print('predictor v%i reset — re-learning' % VERSION)

	def _track_peak(self, Ls_read):
		"""Track peaks via Ls_read. Updates ramp_override_by_predictor."""
		now = time()
		if Ls_read > 400:
			self.high_rise_cnt += 1
			if self.peak_start is None and self.high_rise_cnt >= self.PEAK_RISE_MIN:
				self.peak_start = now
		else:
			self.high_rise_cnt = 0

		if self.peak_start is not None and Ls_read <= 400:
			dur = now - self.peak_start
			self.peak_start = None
			if dur >= self.LONG_PEAK_MIN:
				# long peak: not a cycling load, clear peak history
				self.peak_dur_hist = []
				if self.ramp_override_by_predictor:
					self.ramp_override_by_predictor = False
					if self.verbose: print('predictor: long peak %.0fs → ramp_override OFF' % dur)
			else:
				# short peak: add to history, check for override activation
				self.peak_dur_hist.append((now, dur))
				self.peak_dur_hist = self.peak_dur_hist[-10:]
				if self.ramp_override_by_predictor:
					self.pause_until = now + self.PAUSE_DURATION
					if self.verbose: print('predictor: short peak %.0fs → ramp_override extended' % dur)
				else:
					recent = [(ts, d) for ts, d in self.peak_dur_hist[-self.PAUSE_AFTER:]
						if d < self.SHORT_PEAK_MAX and now - ts <= self.PEAK_WINDOW]
					if (len(recent) >= self.PAUSE_AFTER
							and now - recent[0][0] >= self.OVERRIDE_DELAY):
						self._override_target = int(sum(self._inter_peak_buf) / len(self._inter_peak_buf)) \
							if self._inter_peak_buf else None
						self.ramp_override_by_predictor = True
						self.pause_until = now + self.PAUSE_DURATION
						if self.verbose: print('predictor: %i short peaks → ramp_override ON for %is  target=%s W%s' % (
							self.PAUSE_AFTER, self.PAUSE_DURATION,
							self._override_target if self._override_target is not None else '?',
							' (no low_level)' if self.low_level is None else ''))

		# timeout
		if self.ramp_override_by_predictor and now >= self.pause_until:
			self.ramp_override_by_predictor = False
			if self.verbose: print('predictor: ramp_override timeout → OFF')

	def status(self, predictive_offset=0):
		"""Return a status string for display in zeroinput."""
		now = time()
		self.peak_dur_hist = [(ts, d) for ts, d in self.peak_dur_hist if now - ts <= self.PEAK_WINDOW]
		def _age(ts): return '%is' % int(now - ts)
		peaks = ['%.0fs/%s' % (d, _age(ts)) for ts, d in self.peak_dur_hist[-5:]]
		peak_str = '  peaks [%s]' % ', '.join(peaks) if peaks else ''
		override = '  ramp_override ON (%is)' % max(0, int(self.pause_until - now)) if self.ramp_override_by_predictor else ''
		if self.low_level is not None:
			return 'predictor offset %+d W  low %d W  high %d W%s%s' % (
				predictive_offset, self.low_level, self.high_level, peak_str, override)
		return 'predictor learning  hist %i/%i  transitions %i/%i%s%s' % (
			len(self.history), self.MAX_HIST, self.transition_cnt, TRANSITIONS_MIN, peak_str, override)

	def _kmeans2(self, values):
		if len(values) < 10: return None, None
		s		= sorted(values)
		mid		= len(s) // 2
		c_low	= s[mid // 2]				# median of lower half as initial centroid
		c_high	= s[mid + mid // 2]			# median of upper half as initial centroid
		for _ in range(10):
			low_grp		= [v for v in s if abs(v - c_low) <= abs(v - c_high)]
			high_grp	= [v for v in s if abs(v - c_low)  > abs(v - c_high)]
			if not low_grp or not high_grp: return None, None
			new_low		= avg(low_grp)
			new_high	= avg(high_grp)
			if abs(new_low - c_low) < 1 and abs(new_high - c_high) < 1: break
			c_low, c_high = new_low, new_high
		# reject unimodal distributions: both groups must contain at least 15% of values
		ratio = len(low_grp) / len(values)
		if ratio < 0.15 or ratio > 0.85: return None, None
		return c_low, c_high

	def update(self, Ls_read, last2_send):
		"""Call once per loop cycle. Returns predictive_offset in W."""
		if not self.enabled:
			self.offset = 0
			return 0
		now = time()
		if now < self.startup_end:
			self.offset = 0
			return 0

		est_load = int(Ls_read + last2_send)

		# peak tracking
		self._track_peak(Ls_read)

		# override active: hold at low_level if known, otherwise hold inter-peak mean
		if self.ramp_override_by_predictor:
			if Ls_read > self.LS_OVERRIDE_THR:
				self._ls_hi_dip_cnt = 0
				if self.high_ls_since is None:
					self.high_ls_since = now
				elif now - self.high_ls_since >= self.LONG_PEAK_MIN:
					if self.verbose: print('predictor: sustained load → reset, re-learning')
					self._reset()
			else:
				# tolerate brief dips below threshold – only reset timer after 3 consecutive cycles
				self._ls_hi_dip_cnt += 1
				if self._ls_hi_dip_cnt >= 3:
					self.high_ls_since = None
			if self.low_level is not None:
				self.offset = self.low_level - est_load
			elif self._override_target is not None:
				self.offset = self._override_target - est_load	# hold inter-peak baseline
			else:
				self.offset = -Ls_read		# fallback: neutralise spike
			self._log(now, Ls_read, last2_send, est_load)
			return self.offset

		if Ls_read > 400:
			# skip peak samples — transient values distort LOW/HIGH k-means clustering
			self._log(now, Ls_read, last2_send, est_load)
			return self.offset

		# accumulate inter-peak baseline samples (only when Ls_read is calm)
		self._inter_peak_buf.append(last2_send)
		if len(self._inter_peak_buf) > 30:
			self._inter_peak_buf = self._inter_peak_buf[-30:]

		self.history.append(est_load)
		if len(self.history) > self.MAX_HIST:
			self.history = self.history[-self.MAX_HIST:]

		self.offset = 0
		if len(self.history) >= 10:
			low, high = self._kmeans2(self.history)
			if low is not None:
				spread = high - low
				if spread >= self.MIN_SPREAD:
					self.low_level		= int(low)
					self.high_level		= int(high)
					midpoint			= (self.low_level + self.high_level) / 2
					new_phase			= 'low' if est_load < midpoint else 'high'
					if new_phase != self.current_phase:
						self.transition_cnt += 1
						self.current_phase	= new_phase
						self.peak_dur_hist	= []	# peaks from prior phase are irrelevant
						if self.verbose: print('predictor: phase -> %s  transitions %i/%i' % (
							new_phase, self.transition_cnt, TRANSITIONS_MIN))
					if self.transition_cnt >= TRANSITIONS_MIN:
						self.offset = self.low_level - est_load
					else:
						self.offset = 0
						if self.verbose: print('predictor: learning  transitions %i/%i  spread %i W  low %i W  high %i W' % (
							self.transition_cnt, TRANSITIONS_MIN, int(spread), self.low_level, self.high_level))
				else:
					self.low_level		= None
					self.high_level		= None
					self.current_phase	= None
					self.transition_cnt	= 0
			else:
				# unimodal distribution — clear levels so stale low_level doesn't persist
				self.low_level		= None
				self.high_level		= None
				self.current_phase	= None
				self.transition_cnt	= 0
		self._log(now, Ls_read, last2_send, est_load)
		return self.offset
