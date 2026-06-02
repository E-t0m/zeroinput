# zeroinput Load Predictor — Specification

Functional specification of the zeroinput load predictor (`predictor.py`, VERSION 102).
This is the authoritative description of the intended behaviour. The German translation is
`predictor_spec_de.md`.

## Purpose

The predictor returns an `offset` (in watts) that zeroinput adds to its demand calculation.
It improves zero-feed-in regulation for two load patterns that the plain control loop
handles poorly:

1. **Cyclic loads** (e.g. a washing-machine motor) that alternate between two power levels.
   The predictor learns the levels and holds the inverter at the lower one, so the inverter
   does not chase the cycle and overshoot into feed-in.
2. **Recurring short high-load spikes** ("peaks") whose ramp-up would, through inverter
   inertia, export more energy than the spike could ever save. The predictor recognises the
   recurrence and rides the spikes out instead of chasing them.

The predictor never increases feed-in risk in the fresh state: until it has learned
something (or an override is active), the offset is 0 and is never actively applied.

## Interface

zeroinput requires:

- `update(Ls_read, last2_send) -> offset` — called once per control cycle.
- `reload_conf(conf)` — hot-reload of `load_prediction` (bool) and `min_spread_w` (int).
- `status(predictive_offset) -> str` — human-readable status line.
- attributes `enabled`, `offset`, `ramp_override_by_predictor`.
- logging infrastructure: `_log_path`, `_log_fh`, `_log_open()`, `verbose`.

`Ls_read` is the meter reading (positive = grid draw, negative = feed-in). `last2_send` is
the inverter send value from two cycles earlier. `est_load = Ls_read + last2_send` is the
estimated true household load.

## Timing: cycles, not seconds

All timing is counted in **cycles** — one `update()` call is one cycle. The control loop
runs at roughly one cycle per second, so the constant values still read naturally as
seconds, but the counting is exact and independent of loop-timing jitter. (An earlier
seconds-based version mis-fired a threshold when loop cycles were not exactly one second
apart.) Wall-clock time is used only for the log timestamp.

## Constants

Shared:

| Constant | Value | Meaning |
|---|---|---|
| `NEAR_ZERO_W` | 50 | `|Ls_read|` ≤ this counts as "near zero" (quiet) |

k-means:

| Constant | Value | Meaning |
|---|---|---|
| `min_spread_w` | config | minimum spread between LOW and HIGH |
| `MAX_SPREAD_W` | 400 | maximum spread for a valid cyclic pattern |
| `TRANSITIONS_MIN` | 4 | phase transitions before the offset activates |
| `MAX_HIST` | 60 | history buffer length (samples) |
| `KMEANS_TIMEOUT_N` | 120 | cycles without a real transition → drop learned levels |
| `JUMP_W` | 400 | `Ls_read` below −this (without a preceding peak) = load dropped |

peaks & override:

| Constant | Value | Meaning |
|---|---|---|
| `MIN_PEAK_W` | 400 | `Ls_read` above this = peak; at/below = peak ended |
| `PEAK_SHORT_MAX_N` | 13 | < this = short peak; ≥ this = long peak |
| `PEAK_WINDOW_N` | 120 | window for the two peaks; also override timeout |
| `PEAK_LIFETIME_N` | 120 | how long a finished short peak is counted |
| `OVERRIDE_DELAY_N` | 15 | cycles after the 2nd peak ends before override activates |
| `QUIET_CYCLES` | 10 | quiet cycles averaged for the hold target |

## Mechanism 1: k-means (cyclic load)

The reliable base. k-means clusters the load history into two levels, LOW and HIGH. A result
is valid only if the spread (`HIGH − LOW`) lies in `[min_spread_w, MAX_SPREAD_W]`.

A **transition** is a detected change between the LOW and HIGH phase. After
`TRANSITIONS_MIN` (4) transitions the offset activates and holds the inverter at LOW
(`offset = LOW − est_load`). There is no startup delay; the history fills immediately.

### down-abort

If `Ls_read < −JUMP_W` (strong feed-in: a load has dropped) **without a preceding peak**,
k-means resets immediately — learned levels are discarded and learning restarts. The
"preceding peak" exception is what distinguishes a real load drop from the inertia feed-in
spike that follows every peak (see below). This rule applies at all times, including while
an override is active.

### k-means timeout

If `KMEANS_TIMEOUT_N` (120) cycles pass without a real LOW↔HIGH transition, the learned
levels are dropped (the cyclic load is evidently over). A peak does **not** count as a
transition and does not reset this timer — only a genuine phase change does. The timeout
drops the levels **only**; if an override is running it keeps going (it then falls back to
the quiet-buffer hold target).

## Mechanism 2: peaks & override

### Peak detection and duration

A peak runs while `Ls_read > MIN_PEAK_W` and ends as soon as `Ls_read ≤ MIN_PEAK_W` — the
same rule in all cases. Peak duration is the number of cycles from start to end.

- **long peak** (≥ `PEAK_SHORT_MAX_N`, 13 cycles): classified in real time the moment the
  threshold is reached, while the peak is still running. A long peak triggers a k-means
  reset (and ends any active override).
- **short peak** (< 13 cycles): only provable at the peak's end. A short peak counts toward
  override activation.

### Peak handling vs. k-means

- Each peak sample is replaced in the history by the level of the phase the peak began in
  (LOW or HIGH); the history length is unchanged. The rectangle signal is held flat across
  the peak. Rare phase changes during a short peak are ignored (a short peak is much shorter
  than a phase).
- **Override not yet active (phase A):** a peak pauses the offset (learned levels are kept,
  the transition counter restarts; after 4 fresh transitions the offset resumes). If no
  levels are learned yet, a peak resets k-means.
- **Override active:** a short peak no longer pauses anything — the offset keeps running
  unchanged, the peak is only kept out of learning.

### Override activation

Two short peaks within `PEAK_WINDOW_N` (120 cycles) arm the override. After the second peak
ends (`Ls_read ≤ 400`), the predictor waits `OVERRIDE_DELAY_N` (15 cycles); then the override
becomes active. A fresh peak cancels a pending arm.

### Override hold target

While the override is active the offset holds:

- on the k-means LOW if one is known (`offset = LOW − est_load`, identical to the normal
  k-means offset — the override changes only the peak behaviour, not the formula);
- otherwise on the mean of `(last2_send + Ls_read)` over the last `QUIET_CYCLES` (10) quiet
  cycles (`|Ls_read|` ≤ `NEAR_ZERO_W`). In a quiet cycle `last2_send + Ls_read` is the
  measured total load, with `Ls_read` as the metered correction — no self-reference.

### Override end

The override ends on a long peak (≥ 13 cycles) or after `PEAK_WINDOW_N` (120 cycles) without
any peak, then the predictor falls back to normal k-means operation.

## Reset semantics

A k-means reset clears the learned levels, history, phase and transition counter. By default
a reset **also** ends the override (clears the override flag, pending arm, and quiet buffer)
— a full restart. This applies to the long-peak reset and the down-abort. The single
exception is the k-means timeout, which drops the levels only and lets a running override
continue.

The down-abort suspension (`peak_after`) is condition-based, not timed: after a peak ends it
stays in effect until `Ls_read` is near zero again, so the peak's inertia feed-in spike does
not trigger a false down-abort. A subsequent drop below −`JUMP_W` from the calm state is a
real load drop.

## Fresh start and enable

On a fresh start the offset is 0 and is never actively applied until something is learned or
an override activates. When `load_prediction` switches from off to on via config, all state
is cleared for a fresh learning start (this does not happen on every reload, only on the
off→on transition).

## Status line

`status()` reports the version, the learned levels and phase (or `learning hist=N`), an
`OVERRIDE` marker when active, a running `peak Nc` while a peak is in progress, and the live
short-peak list as `[duration/remaining]` per peak, e.g. `[4c/92c, 5c/103c]` — duration and
remaining lifetime in cycles. The trailing `offset=N` is the value returned this cycle.

## Log

The predictor appends a tab-separated line per cycle to `LOG_FILE` (`/tmp/predictor.log` by
default, configurable via `predictor_log`). A version banner and the column header are
written when the log is opened; `# RESET` / `# ENABLE` marker lines record state changes.
Columns: timestamp, time, Ls_read, last2_send, est_load, history length, LOW, HIGH, spread,
phase, transition count, offset, override flag, override remaining cycles, in-peak flag,
peak age (cycles), short-peak count.
