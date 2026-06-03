#!/usr/bin/env python3
# predictor.py — zeroinput load predictor (rectangle-signal view)
#
# Two mechanisms (see predictor_concept_de.md):
#   1. k-means: learns a cyclic load (low/high), holds the inverter at LOW. Reliable base.
#   2. peaks & override: handle recurring short high-load spikes whose ramp-up would export
#      more (via inverter inertia) than it saves.
#
# All timing is counted in CYCLES (one update() call = one cycle), not wall-clock seconds,
# so non-uniform loop timing cannot skew the thresholds. The loop runs ~1 cycle/second, so
# the constant values are still readable as "seconds".
#
# Drop-in for zeroinput: provides update(), reload_conf(), status(), and the attributes
# enabled, offset, ramp_override_by_predictor, plus the _log_* logging infrastructure.

from time import time, strftime


def avg(xs):
	return sum(xs) / len(xs) if xs else 0


# --- module constants --------------------------------------------------------------------
VERSION			= 104		# rectangle-signal rewrite, cycle-based timing
LOG_FILE		= '/tmp/predictor.log'	# '' = no log

NEAR_ZERO_W		= 50		# W: |Ls_read| <= this counts as "near zero" (quiet)

# k-means
MAX_SPREAD_W	= 400		# W: max spread (high-low) for a valid cyclic pattern
TRANSITIONS_MIN	= 4			# phase transitions before the offset becomes active
MIN_HIST		= 10		# samples needed before k-means runs
MAX_HIST		= 60		# history buffer size (samples)
KMEANS_TIMEOUT_N	= 120	# cycles: no real low<->high transition this long -> drop levels

# abort threshold (on Ls_read)
JUMP_W			= 400		# W: Ls_read below -JUMP_W (without a preceding peak) = load dropped

# peaks & override
MIN_PEAK_W		= 400		# W: Ls_read above this = peak; at/below = peak ended
PEAK_SHORT_MAX_N	= 13	# cycles: < this = short peak; >= this = long peak
PEAK_WINDOW_N	= 120		# cycles: window in which two short peaks must fall to arm override
PEAK_LIFETIME_N	= 120		# cycles: how long a finished short peak is counted (display + arming)
OVERRIDE_DELAY_N	= 15	# cycles: wait after 2nd peak ends (Ls<400) before override active
BASE_CYCLES		= 10		# non-peak cycles averaged for the hold target when no k-means low
# -----------------------------------------------------------------------------------------

# Log columns: name -> description. Order determines column order in the log file.
LOG_COLUMNS = [
	('ts',			'Unix timestamp'),
	('hms',			'HH:MM:SS'),
	('Ls_read',		'meter reading (W, + = grid draw, - = feed-in)'),
	('last2_send',	'inverter send value two cycles ago (W)'),
	('est_load',	'Ls_read + last2_send (W)'),
	('hist',		'history buffer length'),
	('low',			'k-means LOW centroid (W)'),
	('high',		'k-means HIGH centroid (W)'),
	('spread',		'high - low (W)'),
	('phase',		'current cyclic phase'),
	('trans',		'phase transition count'),
	('offset',		'predictive offset returned (W)'),
	('override',	'override active flag'),
	('ovr_rem',		'override remaining cycles'),
	('in_peak',		'peak active flag'),
	('peak_age',	'cycles since current peak started'),
	('peak_cnt',	'short peaks counted in window'),
]
_LOG_HEADER = '\t'.join(col for col, _ in LOG_COLUMNS)


class LoadPredictor:
	def __init__(self, conf, verbose=False):
		self.verbose	= verbose
		self.enabled	= bool(conf.get('load_prediction', True))
		self.MIN_SPREAD	= int(conf.get('min_spread_w', 150))

		self._init_state()

		# logging
		self._log_path	= LOG_FILE
		self._log_fh	= None
		self._log_open()

	def _init_state(self):
		self.cycle			= 0			# monotonic cycle counter (one update() = one cycle)

		# k-means state
		self.history		= []
		self.low_level		= None
		self.high_level		= None
		self.current_phase	= None		# 'low' | 'high' | None
		self.transition_cnt	= 0
		self.offset			= 0
		self.last_transition_cycle	= 0	# for k-means timeout

		# peak state
		self.in_peak			= False		# currently in a peak (Ls_read > MIN_PEAK_W)
		self.peak_start_cycle	= None		# cycle when current peak crossed MIN_PEAK_W
		self.peak_is_long		= False		# current peak already classified long
		self.peak_after			= False		# a peak just ended; down-abort suspended until calm
		self.short_peaks		= []		# list of (end_cycle, duration_cycles) of short peaks

		# override state
		self.ramp_override_by_predictor	= False
		self.override_until_cycle		= 0		# cycle until which override stays active
		self.override_arm_cycle			= None	# cycle when override should go active
		self.base_buf					= []	# est_load of recent non-peak cycles (base load)

	# --- logging -------------------------------------------------------------------------
	def _log_open(self):
		if not self._log_path:
			self._log_fh = None
			return
		try:
			self._log_fh = open(self._log_path, 'a')
			self._log_fh.write('# predictor v%i  %s\n' % (VERSION, strftime('%Y-%m-%d %H:%M:%S')))
			self._log_fh.write(_LOG_HEADER + '\n')
			self._log_fh.flush()
		except Exception as e:
			print('predictor log open failed: %s' % e)
			self._log_fh = None

	def _log(self, Ls_read, last2_send, est_load):
		if not self._log_fh:
			return
		spread = (self.high_level - self.low_level) if self.low_level is not None else ''
		ovr_rem = max(0, self.override_until_cycle - self.cycle) if self.ramp_override_by_predictor else 0
		peak_age = (self.cycle - self.peak_start_cycle) if (self.in_peak and self.peak_start_cycle is not None) else 0
		row = [
			'%.6f' % time(),
			strftime('%H:%M:%S'),
			int(Ls_read), int(last2_send), int(est_load),
			len(self.history),
			self.low_level if self.low_level is not None else '',
			self.high_level if self.high_level is not None else '',
			spread,
			self.current_phase or '',
			self.transition_cnt,
			int(self.offset),
			1 if self.ramp_override_by_predictor else 0,
			ovr_rem,
			1 if self.in_peak else 0,
			peak_age,
			len(self.short_peaks),
		]
		try:
			self._log_fh.write('\t'.join(str(c) for c in row) + '\n')
			self._log_fh.flush()
		except Exception:
			pass

	# --- config --------------------------------------------------------------------------
	def reload_conf(self, conf):
		was_enabled		= self.enabled
		self.enabled	= bool(conf.get('load_prediction', True))
		self.MIN_SPREAD	= int(conf.get('min_spread_w', 150))
		# fresh learning start when load_prediction switches off -> on
		if self.enabled and not was_enabled:
			self._init_state()
			if self._log_fh:
				try:
					self._log_fh.write('# ENABLE %s\n' % strftime('%Y-%m-%d %H:%M:%S'))
					self._log_fh.flush()
				except Exception:
					pass
		if self.verbose:
			print('predictor v%i reloaded: enabled=%s min_spread=%i' % (
				VERSION, self.enabled, self.MIN_SPREAD))

	# --- k-means -------------------------------------------------------------------------
	def _kmeans2(self, values):
		if len(values) < MIN_HIST:
			return None, None
		s	= sorted(values)
		mid	= len(s) // 2
		c_low	= s[mid // 2]
		c_high	= s[mid + mid // 2]
		for _ in range(10):
			low_grp		= [v for v in s if abs(v - c_low) <= abs(v - c_high)]
			high_grp	= [v for v in s if abs(v - c_low)  > abs(v - c_high)]
			if not low_grp or not high_grp:
				return None, None
			new_low		= avg(low_grp)
			new_high	= avg(high_grp)
			if abs(new_low - c_low) < 1 and abs(new_high - c_high) < 1:
				break
			c_low, c_high = new_low, new_high
		ratio = len(low_grp) / len(values)
		if ratio < 0.15 or ratio > 0.85:
			return None, None
		return c_low, c_high

	def _reset_kmeans(self, end_override=True):
		"""Drop learned levels; relearn from scratch.
		end_override=True also clears override state (full restart). The k-means timeout
		passes False: it only drops stale levels while a running override keeps going."""
		self.history			= []
		self.low_level			= None
		self.high_level			= None
		self.current_phase		= None
		self.transition_cnt		= 0
		self.offset				= 0
		self.last_transition_cycle	= self.cycle
		if end_override:
			self.ramp_override_by_predictor	= False
			self.override_arm_cycle			= None
			self.override_until_cycle		= 0
			self.base_buf					= []
			self.short_peaks				= []
			self.in_peak					= False
			self.peak_is_long				= False
			self.peak_after					= False
		if self._log_fh:
			try:
				self._log_fh.write('# RESET %s%s\n' % (
					strftime('%Y-%m-%d %H:%M:%S'),
					'' if end_override else ' (levels only)'))
				self._log_fh.flush()
			except Exception:
				pass

	# --- peak tracking -------------------------------------------------------------------
	def _track_peak(self, Ls_read):
		"""Update peak state. Returns '' | 'short_end' | 'long'.
		Peak runs while Ls_read > MIN_PEAK_W; ends when Ls_read <= MIN_PEAK_W (all cases)."""
		event = ''
		if not self.in_peak:
			if Ls_read > MIN_PEAK_W:
				self.in_peak			= True
				self.peak_start_cycle	= self.cycle
				self.peak_is_long		= False
			return event

		# peak in progress: classify long in real time at PEAK_SHORT_MAX_N
		if not self.peak_is_long and (self.cycle - self.peak_start_cycle) >= PEAK_SHORT_MAX_N:
			self.peak_is_long = True
			event = 'long'
			return event

		# peak ends when Ls_read drops to/below the peak threshold
		if Ls_read <= MIN_PEAK_W:
			dur = self.cycle - self.peak_start_cycle
			self.in_peak = False
			self.peak_after = True		# suspend down-abort until Ls_read is near zero again
			if not self.peak_is_long:
				event = 'short_end'
				self.short_peaks.append((self.cycle, dur))
		return event

	def _purge_window(self):
		cutoff = self.cycle - PEAK_LIFETIME_N
		self.short_peaks = [(c, d) for (c, d) in self.short_peaks if c >= cutoff]

	# --- main ----------------------------------------------------------------------------
	def update(self, Ls_read, last2_send):
		"""Call once per loop cycle. Returns predictive offset in W."""
		if not self.enabled:
			self.offset = 0
			return 0

		self.cycle += 1
		est_load = int(Ls_read + last2_send)

		had_peak = self.in_peak			# was in a peak at start of this cycle
		event = self._track_peak(Ls_read)
		self._purge_window()

		# clear the post-peak down-abort suspension once Ls_read is calm again
		if self.peak_after and not self.in_peak and abs(Ls_read) <= NEAR_ZERO_W:
			self.peak_after = False

		# -------- long peak: reset k-means (also ends override) --------------------------
		if event == 'long':
			self._reset_kmeans(end_override=True)
			self._log(Ls_read, last2_send, est_load)
			return self.offset

		# -------- override timeout: no peak for the whole window -------------------------
		if self.ramp_override_by_predictor and self.cycle >= self.override_until_cycle:
			self.ramp_override_by_predictor = False
			self.override_arm_cycle = None

		# -------- override arming / activation -------------------------------------------
		# need two short peaks within the window; after the 2nd ends (Ls<400) wait the delay
		if (not self.ramp_override_by_predictor and len(self.short_peaks) >= 2
				and not self.in_peak):
			if self.override_arm_cycle is None:
				self.override_arm_cycle = self.cycle + OVERRIDE_DELAY_N
			elif self.cycle >= self.override_arm_cycle:
				self.ramp_override_by_predictor = True
				self.override_until_cycle = self.cycle + PEAK_WINDOW_N
				self.override_arm_cycle = None
		# a fresh peak cancels a pending arm
		if self.in_peak:
			self.override_arm_cycle = None

		# extend override window on each new short peak while active
		if self.ramp_override_by_predictor and event == 'short_end':
			self.override_until_cycle = self.cycle + PEAK_WINDOW_N

		# -------- down-abort: load dropped without a preceding peak ----------------------
		if Ls_read < -JUMP_W and not (had_peak or self.in_peak or self.peak_after):
			self._reset_kmeans(end_override=True)
			self._log(Ls_read, last2_send, est_load)
			return self.offset

		# -------- peak in progress: hold / pause -----------------------------------------
		if self.in_peak:
			# replace this peak sample in history with the level of the phase it began in
			# (rectangle signal held flat across the peak); history length unchanged.
			if self.low_level is not None:
				fill = self.low_level if self.current_phase != 'high' else self.high_level
				self.history.append(fill)
				if len(self.history) > MAX_HIST:
					self.history = self.history[-MAX_HIST:]

			if self.ramp_override_by_predictor:
				self.offset = self._hold_offset(est_load)
			elif self.low_level is None:
				# no levels to protect yet — nothing to reset; the peak is simply
				# observed (kept out of history above) and counted on its end.
				self.offset = 0
			else:
				# phase A: keep levels, pause offset, relearn transitions
				self.transition_cnt	= 0
				self.offset			= 0
			self._log(Ls_read, last2_send, est_load)
			return self.offset

		# -------- non-peak cycle: feed base-load buffer & k-means ------------------------
		# (reached only when no peak is running — the peak block above returns early)
		self.base_buf.append(est_load)
		if len(self.base_buf) > BASE_CYCLES:
			self.base_buf = self.base_buf[-BASE_CYCLES:]

		self.history.append(est_load)
		if len(self.history) > MAX_HIST:
			self.history = self.history[-MAX_HIST:]

		# k-means: maintain levels
		low, high = self._kmeans2(self.history)
		if low is not None:
			spread = high - low
			if self.MIN_SPREAD <= spread <= MAX_SPREAD_W:
				self.low_level	= int(low)
				self.high_level	= int(high)

		# k-means timeout: no real transition for KMEANS_TIMEOUT_N -> drop levels only
		# (override, if running, keeps going and falls back to the quiet-buffer target)
		if (self.cycle - self.last_transition_cycle) >= KMEANS_TIMEOUT_N:
			if self.low_level is not None or self.history:
				self._reset_kmeans(end_override=False)

		# phase + offset from stored levels
		if self.low_level is not None:
			midpoint	= (self.low_level + self.high_level) / 2
			new_phase	= 'low' if est_load < midpoint else 'high'
			if new_phase != self.current_phase:
				if self.current_phase is not None:
					self.last_transition_cycle = self.cycle		# a real low<->high transition
				if self.transition_cnt < TRANSITIONS_MIN:
					self.transition_cnt += 1
				self.current_phase = new_phase
			if self.ramp_override_by_predictor:
				self.offset = self._hold_offset(est_load)
			elif self.transition_cnt >= TRANSITIONS_MIN:
				self.offset = self.low_level - est_load
			else:
				self.offset = 0
		elif self.ramp_override_by_predictor:
			self.offset = self._hold_offset(est_load)
		else:
			self.offset = 0

		self._log(Ls_read, last2_send, est_load)
		return self.offset

	def _hold_offset(self, est_load):
		"""Override hold target: k-means low if known, else the running mean of the base
		load (est_load of recent non-peak cycles). The base mean follows the base load
		even while the offset has not yet driven Ls_read to zero, so a changed base line
		(new 'zero') is tracked instead of frozen."""
		if self.low_level is not None:
			return self.low_level - est_load
		if self.base_buf:
			return int(avg(self.base_buf)) - est_load
		return 0

	# --- status --------------------------------------------------------------------------
	def status(self, predictive_offset=0):
		if not self.enabled:
			return 'predictor: disabled'
		parts = ['predictor v%i' % VERSION]
		if self.low_level is not None:
			parts.append('low=%i high=%i' % (self.low_level, self.high_level))
			parts.append('phase=%s trans=%i/%i' % (
				self.current_phase or '-', self.transition_cnt, TRANSITIONS_MIN))
		else:
			parts.append('learning hist=%i' % len(self.history))
		if self.ramp_override_by_predictor:
			parts.append('OVERRIDE')
		if self.in_peak:
			parts.append('peak %ic' % (self.cycle - self.peak_start_cycle))
		if self.short_peaks:
			peaks = ', '.join('%ic/%ic' % (d, max(0, PEAK_LIFETIME_N - (self.cycle - c)))
				for (c, d) in self.short_peaks)
			parts.append('[%s]' % peaks)
		parts.append('offset=%i' % int(predictive_offset))
		return '  '.join(parts)
