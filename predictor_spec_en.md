# zeroinput Load Predictor — Specification

Functional specification of the zeroinput load predictor (`predictor.py`, VERSION 106). This is the authoritative description of the intended behaviour. The German translation is `predictor_spec_de.md`.

## Purpose

The predictor returns an `offset` (in watts) that zeroinput adds to its demand calculation. It improves zero-feed-in regulation for two load patterns that the plain control loop handles poorly:

1. **Cyclic loads** (e.g. a washing-machine motor or a thermostat-cycled heating blanket) that alternate between two power levels. The predictor learns the levels and holds the inverter at the lower one, so the inverter does not chase the cycle and overshoot into feed-in.
2. **Recurring short high-load spikes** ("peaks") where the inverter's legally limited ramp rate makes its ramp-up export more energy than the spike could ever save. The predictor recognises the recurrence and rides the spikes out instead of chasing them.

The predictor never increases feed-in risk in the fresh state: until it has learned something (or an override is active), the offset is 0 and is never actively applied.

## Motivation

Avoiding overshoot into feed-in pays off because the battery is rarely full: PV energy is therefore almost never a true surplus, but could be stored and used later. What flows to the grid instead — often uncompensated — is wasted energy. Every avoided feed-in raises the share of self-generated energy actually used and thus lowers grid draw; this is the predictor's measurable effect. A steadier operating point might also be easier on the inverters.

## Interface

zeroinput requires:

- `update(Ls_read, last2_send) -> offset` — called once per control cycle.
- `reload_conf(conf)` — hot-reload of `load_prediction` (bool) and `min_spread_w` (int).
- `status(predictive_offset) -> str` — human-readable status line.
- attributes `enabled`, `offset`, `ramp_override_by_predictor`.
- logging infrastructure: `_log_path`, `_log_fh`, `_log_open()`, `verbose`.

`Ls_read` is the meter reading (positive = grid draw, negative = feed-in). `last2_send` is the inverter send value from two cycles earlier. `est_load = Ls_read + last2_send` is the estimated true household load.

## Timing: cycles, not seconds

All timing is counted in **cycles** — one `update()` call is one cycle. The control loop runs at roughly one cycle per second, so the constant values still read naturally as seconds, but the counting is exact and independent of loop-timing jitter. (An earlier seconds-based version mis-fired a threshold when loop cycles were not exactly one second apart.) Wall-clock time is used only for the log timestamp.

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
| `MIN_HIST` | 10 | samples before k-means computes at all |
| `MAX_HIST` | 60 | history buffer length (samples) |
| `KMEANS_TIMEOUT_N` | 120 | cycles without a real transition → drop learned levels |
| `JUMP_W` | 400 | `Ls_read` below −this (without a preceding peak) = load dropped |

peaks & override:

| Constant | Value | Meaning |
|---|---|---|
| `MIN_PEAK_W` | 400 | `Ls_read` above this = peak; at/below = peak ended |
| `PEAK_SHORT_MAX_N` | 10 | < this = short peak; ≥ this = long peak |
| `PEAK_WINDOW_N` | 120 | window for the two peaks; also override timeout |
| `PEAK_LIFETIME_N` | 120 | how long a finished short peak is counted |
| `OVERRIDE_DELAY_N` | 12 | cycles after the 2nd peak ends before override activates |
| `BASE_CYCLES` | 10 | non-peak cycles averaged for the hold target |

## Mechanism 1: k-means (cyclic load)

The reliable base. k-means clusters the load history into two levels, LOW and HIGH. It only computes once at least `MIN_HIST` (10) samples are available. A result is valid only if the spread (`HIGH − LOW`) lies in `[min_spread_w, MAX_SPREAD_W]` **and** the distribution is genuinely two-level: if either cluster group holds less than 15 % (or more than 85 %) of the values, it is treated as unimodal and rejected — there are then no valid levels.

When k-means yields no valid result (unimodal history, or spread out of range), the stored levels are dropped at once (LOW/HIGH, phase and transition counter reset). Stale levels must not linger once the cyclic pattern is gone — otherwise the offset would keep holding on a LOW that no longer exists and cause continuous grid draw. The history is kept, so the moment the load pings between two levels again a fresh result is learned without delay.

The current phase follows from the midpoint of the two levels: if `est_load` is below `(LOW + HIGH) / 2` the phase is LOW, otherwise HIGH. A **transition** is a change of this assignment between LOW and HIGH. After `TRANSITIONS_MIN` (4) transitions the offset activates and holds the inverter at LOW (`offset = LOW − est_load`). There is no startup delay; the history fills immediately.

### down-abort

If `Ls_read < −JUMP_W` (strong feed-in: a load has dropped) **without a preceding peak**, k-means resets immediately — learned levels are discarded and learning restarts. The "preceding peak" exception is what distinguishes a real load drop from the inertia feed-in spike that follows every peak (see below). This rule applies at all times, including while an override is active.

### k-means timeout

If `KMEANS_TIMEOUT_N` (120) cycles pass without a real LOW↔HIGH transition, the learned levels are dropped (the cyclic load is evidently over). A peak does **not** count as a transition and does not reset this timer — only a genuine phase change does. The timeout drops the levels **only**; if an override is running it keeps going (it then falls back to the base-load hold target).

## Mechanism 2: peaks & override

This mechanism handles recurring short high-load spikes (peaks), e.g. from a cycling appliance. The **override** is a signal from the predictor to zeroinput to suspend its ramp mode (flag `ramp_override_by_predictor`): normally zeroinput rides out large load steps with a ramp of its own, overriding the predictor offset in the process. For a recognised cycling load this ramp would distort the offset on every peak — the override suppresses it so the predictor can hold the offset steady on the base load and ride the peaks out.

### Peak detection and duration

A peak runs while `Ls_read > MIN_PEAK_W` and ends as soon as `Ls_read ≤ MIN_PEAK_W` — the same rule in all cases. Peak duration is the number of cycles from start to end.

- **long peak** (≥ `PEAK_SHORT_MAX_N`, 10 cycles): classified in real time the moment the threshold is reached, while the peak is still running. A long peak triggers a k-means reset (and ends any active override).
- **short peak** (< 10 cycles): only provable at the peak's end. A short peak counts toward override activation.

### Peak handling vs. k-means

- Each peak sample is replaced in the history by the level of the phase the peak began in (LOW or HIGH); the history length is unchanged. The rectangle signal is held flat across the peak. Rare phase changes during a short peak are ignored (a short peak is much shorter than a phase).
- **Override not yet active (phase A):** a peak pauses the offset (learned levels are kept, the transition counter restarts; after 4 fresh transitions the offset resumes). If no levels are learned yet, a peak resets k-means.
- **Override active:** a short peak no longer pauses anything — the offset keeps running unchanged, the peak is only kept out of learning.

### Override activation

Two short peaks within `PEAK_WINDOW_N` (120 cycles) arm the override. After the second peak ends (`Ls_read ≤ 400`), the predictor waits `OVERRIDE_DELAY_N` (12 cycles); then the override becomes active. A fresh peak cancels a pending arm.

### Override hold target

While the override is active the offset holds:

- on the k-means LOW if one is known (`offset = LOW − est_load`, identical to the normal k-means offset — the override changes only the peak behaviour, not the formula);
- otherwise on the running mean of `est_load` over the last `BASE_CYCLES` (10) non-peak cycles (the base load). `est_load` is the measured total load; the mean is advanced every non-peak cycle regardless of whether `Ls_read` is already near zero. The hold target thus follows a changed base load (a new "zero") instead of freezing on a stale value while the offset has not yet driven `Ls_read` to zero.

### Override end

The override ends on a long peak (≥ 10 cycles) or after `PEAK_WINDOW_N` (120 cycles) without any peak, then the predictor falls back to normal k-means operation.

## Reset semantics

A k-means reset clears the learned levels, history, phase and transition counter. By default a reset **also** ends the override: it clears the override flag, the pending arm, the base-load buffer, the short-peak list (`short_peaks`) and the running peak state — a full restart. A stale peak history therefore cannot trigger an unwanted re-activation of the override after the reset. This applies to the long-peak reset and the down-abort. The single exception is the k-means timeout, which drops the levels only and leaves the override, base-load buffer and peak history untouched (a running override continues).

The down-abort suspension (`peak_after`) is condition-based, not timed: after a peak ends it stays in effect until `Ls_read` is near zero again, so the peak's inertia feed-in spike does not trigger a false down-abort. A subsequent drop below −`JUMP_W` from the calm state is a real load drop.

## Fresh start and enable

On a fresh start the offset is 0 and is never actively applied until something is learned or an override activates. When `load_prediction` switches from off to on via config, all state is cleared for a fresh learning start (this does not happen on every reload, only on the off→on transition).

## Status line

`status()` reports the version, the learned levels and phase (or `learning hist=N`), an `OVERRIDE` marker when active, a running `peak Nc` while a peak is in progress, and the live short-peak list as `[duration/remaining]` per peak, e.g. `[4c/92c, 5c/103c]` — duration and remaining lifetime in cycles. The trailing `offset=N` is the value returned this cycle.

## Log

The predictor log is meant as a diagnostic and analysis interface, not an operational display: live monitoring of the plant is done through volkszaehler, while this log, with its high level of detail, serves later fault-finding (for instance when an excerpt is taken for analysis). It therefore records every internal state per cycle.

The predictor appends a tab-separated line per cycle to `LOG_FILE` (`/tmp/predictor.log` by default, configurable via `predictor_log`). A version banner and the column header are written when the log is opened; `# RESET` / `# ENABLE` marker lines record state changes (the `# RESET` line names the predictor version). Columns: timestamp, time, Ls_read, last2_send, est_load, history length, LOW, HIGH, spread, phase, transition count, offset, override flag, override remaining cycles, in-peak flag, peak age (cycles), short-peak count.
