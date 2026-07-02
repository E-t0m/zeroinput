# zeroinput load predictor — specification

Functional specification of the zeroinput load predictor (`predictor.py`, VERSION 108). This is the authoritative description of the intended behaviour. The German version is `predictor_spec_de.md`.

## Purpose

The predictor produces an `offset` (in watts) that zeroinput adds to its demand calculation. It improves zero-feed-in control for two load patterns the plain control loop handles poorly:

1. **Cyclic loads** (e.g. a washing-machine motor or a thermostat-cycled heating blanket) that alternate between two power levels. The predictor learns the levels and holds the inverter at the lower one, so it does not chase the cycle and overshoot into feed-in.
2. **Recurring short high-load surges** ("peaks"), where the inverter's legally limited ramp rate means that ramping up feeds in more energy than the surge could ever save. The predictor recognizes the repetition and sits the surges out instead of following them.

In the fresh state the predictor never raises the feed-in risk: as long as nothing has been learned (and no override is active), the offset is 0 and is never actively applied.

## Motivation

Avoiding overshoot into feed-in is worthwhile because the battery is rarely full: PV energy is therefore almost never a true surplus but could be stored and used later. What instead flows into the grid — often unpaid — is wasted energy. Every avoided feed-in raises the share of energy actually self-consumed and thus lowers grid draw; that is the predictor's measurable effect. A calmer operating point may also spare the inverters.

## Interface

zeroinput requires:

- `update(Ls_read, last2_send) -> offset` — called once per control cycle.
- `reload_conf(conf)` — hot-reload of `load_prediction` (bool) and `min_spread_w` (int).
- `status(predictive_offset) -> str` — readable status line.
- attributes `enabled`, `offset`, `ramp_override_by_predictor`.
- logging infrastructure: `_log_path`, `_log_fh`, `_log_open()`, `verbose`.

`Ls_read` is the meter value (positive = grid draw, negative = feed-in). `last2_send` is the inverter send value from two cycles ago. `est_load = Ls_read + last2_send` is the estimated actual house load.

## Time model: cycles rather than seconds

All time quantities are counted in **cycles** — one `update()` call is one cycle. The control loop runs at roughly one cycle per second, so the constant values can still be read naturally as seconds, but the count is exact and independent of loop-time jitter. Wall-clock time is used only for the log timestamp.

## Constants

Common:

| Constant | Value | Meaning |
|---|---|---|
| `NEAR_ZERO_W` | 50 | magnitude of `Ls_read` ≤ this value counts as "near zero" (calm) |

k-means:

| Constant | Value | Meaning |
|---|---|---|
| `min_spread_w` | config | minimum spread between LOW and HIGH |
| `MAX_SPREAD_W` | 400 | maximum spread for a valid cyclic pattern |
| `TRANSITIONS_MIN` | 4 | phase changes before the offset becomes active |
| `MIN_HIST` | 10 | samples before k-means computes at all |
| `MAX_HIST` | 60 | length of the history buffer (samples) |
| `KMEANS_TIMEOUT_N` | 120 | cycles without a real transition → discard learned levels |
| `JUMP_W` | 400 | `Ls_read` below −this value (without a preceding peak) = load gone |

Peaks & override:

| Constant | Value | Meaning |
|---|---|---|
| `MIN_PEAK_W` | 400 | `Ls_read` above this = peak; at/below = peak ended |
| `PEAK_SHORT_MAX_N` | 8 | < this value = short peak; ≥ = long peak |
| `PEAK_WINDOW_N` | 120 | window for the two peaks; also the override timeout |
| `PEAK_LIFETIME_N` | 120 | how long a finished short peak is counted |
| `OVERRIDE_DELAY_N` | 12 | cycles after the 2nd peak ends until the override activates |
| `BASE_CYCLES` | 10 | non-peak cycles for averaging the hold target |

## Mechanism 1: k-means (cyclic load)

![Rectangle signal: k-means learns low/high and holds at LOW during the HIGH phase](screenshots/mechanism_1_kmeans.png)

The reliable base. k-means clusters the load history into two levels, LOW and HIGH. It only computes once at least `MIN_HIST` (10) samples are present. A result is valid only if the spread (`HIGH − LOW`) lies within `[min_spread_w, MAX_SPREAD_W]` **and** the distribution is genuinely two-level: if either cluster group holds less than 15 % (or more than 85 %) of the values, it is deemed unimodal and discarded — then there are no valid levels.

If k-means yields no valid result (unimodal history or spread out of bounds), the stored levels are discarded at once (LOW/HIGH, phase and transition counter reset). Stale levels must not persist once the cyclic pattern is gone — otherwise the offset would keep holding at a LOW that no longer exists and produce permanent grid draw. The history is kept, so that when the cycling returns the levels are relearned without delay.

The current phase follows from the midpoint of the two levels: if `est_load` is below `(LOW + HIGH) / 2` the phase is LOW, otherwise HIGH. A **transition** is a change of this assignment between LOW and HIGH. After `TRANSITIONS_MIN` (4) transitions the offset becomes active and holds the inverter at LOW (`offset = LOW − est_load`). There is no start-up delay; the history builds immediately.

### down-abort

If `Ls_read < −JUMP_W` (strong feed-in: a load has dropped out) **without a preceding peak**, k-means is reset at once — the learned levels are discarded and learning starts over. The "preceding peak" exception distinguishes a genuine load drop from the inertial feed-in spike that follows every peak (see below). This rule always applies, even while an override is active.

### k-means timeout

If `KMEANS_TIMEOUT_N` (120) cycles pass without a real LOW↔HIGH transition, the learned levels are discarded (the cyclic load is evidently over). A peak does **not** count as a transition and does not reset this timer — only a real phase change does. The timeout discards **only** the levels; if an override is running it persists (falling back to the hold target from the base-load average).

## Mechanism 2: peaks & override

![Ls_read with two short peaks that activate the override and one long peak that ends it](screenshots/mechanism_2_peaks.png)

This mechanism handles recurring short high-load surges (peaks), e.g. from a cycling appliance. The **override** is a signal from the predictor to zeroinput to suspend its ramp mode (flag `ramp_override_by_predictor`): normally zeroinput drives large load steps through a ramp of its own and overwrites the predictor offset while doing so. For a recognized cycling appliance this ramp would corrupt the offset on every peak — the override suppresses it, so the predictor can hold the offset calmly on the base load and sit the peaks out.

### Peak detection and duration

A peak runs while `Ls_read > MIN_PEAK_W` and ends once `Ls_read ≤ MIN_PEAK_W` — the same rule in all cases. The peak duration is the number of cycles from start to end.

- **long peak** (≥ `PEAK_SHORT_MAX_N`, 8 cycles): classified in real time as soon as the threshold is reached, while the peak is still running. A long peak triggers a k-means reset (and ends an active override).
- **short peak** (< `PEAK_SHORT_MAX_N` cycles): only provable at the peak's end. A short peak counts toward override activation.

The value of `PEAK_SHORT_MAX_N` should be adapted to your own installation and expectations.

### Peak state across the reset

The physical peak state — whether a peak is currently running and whether it has already been classified as long — describes the real load and follows `Ls_read` alone. It is therefore **not** cleared by a k-means reset, but ends only when the load falls below `MIN_PEAK_W`.

Otherwise the reset triggered by a long peak — which fires while the load is still high — would end the running peak artificially; the next cycle would begin a new peak whose tail, as it decays, would be counted as an additional short peak. Together with a single further surge this phantom peak could arm the override falsely. Because the peak state is preserved across the reset, no phantom peak arises: only two **genuine** short surges activate the override.

### Peak handling versus k-means

- Every peak sample in the history is replaced by the level of the phase in which the peak began (LOW or HIGH); the history length stays unchanged. The rectangle signal is held flat across the peak. Rare phase changes during a short peak are ignored (a short peak is much shorter than a phase).
- **Override not yet active (phase A):** a peak pauses the offset (learned levels remain, the transition counter restarts; after 4 renewed transitions the offset resumes). If no levels are learned yet, a peak resets k-means.
- **Override active:** a short peak pauses nothing more — the offset continues unchanged, the peak is only kept out of learning.

### Override activation

Two short peaks within `PEAK_WINDOW_N` (120 cycles) prime the override. After the second peak ends (`Ls_read ≤ 400`) the predictor waits `OVERRIDE_DELAY_N` (12 cycles); then the override activates. A fresh peak aborts a pending priming.

### Override hold target

While the override is active, the offset holds:

- at the k-means LOW if one is known (`offset = LOW − est_load`, identical to the normal k-means offset — the override changes only the peak behaviour, not the formula);
- otherwise at the moving average of `est_load` over the last `BASE_CYCLES` (10) non-peak cycles (the base load). `est_load` is the measured total load; the average is advanced every non-peak cycle regardless of whether `Ls_read` is already near zero. This way the hold target follows a changed base load (a new "zero line") instead of freezing at an old value while the offset has not yet driven `Ls_read` to zero.

### Override end

The override ends on a long peak (≥ 10 cycles) or after `PEAK_WINDOW_N` (120 cycles) without any peak; the predictor then falls back to normal k-means operation.

## Reset semantics

A k-means reset clears the learned levels, the history, the phase and the transition counter. By default a reset **also** ends the override: it clears the override flag, the pending priming, the base-load buffer and the list of short peaks (`short_peaks`). The running peak state (in-peak, long/short) is however preserved and continues to follow `Ls_read` alone (see "Peak state across the reset"). This way an old peak history cannot trigger an unwanted re-activation of the override after the reset, and at the same time the reset produces no phantom peak. This applies to the reset by long peak and to the down-abort. The only exception is the k-means timeout, which discards only the levels and leaves the override, base-load buffer and peak history untouched (a running override continues).

The down-abort suspension (`peak_after`) is condition-based, not time-based: after a peak ends it stays in effect until `Ls_read` is near zero again, so the peak's inertial feed-in spike cannot trigger a false down-abort. A renewed drop below −`JUMP_W` from the calm state is a genuine load drop.

## Fresh start and activation

In the fresh state the offset is 0 and is never actively applied until something is learned or an override activates. When `load_prediction` switches from off to on via config, the whole state is cleared for a fresh learning start (this happens not on every reload, only on the off→on transition).

## Status line

`status()` reports the version, the learned levels and the phase (or `learning hist=N`), an `OVERRIDE` marker when an override is active, a running `peak Nc` while a peak runs, and the list of live short peaks as `[duration/remaining]` per peak, e.g. `[4c/92c, 5c/103c]` — duration and remaining lifetime in cycles. The trailing `offset=N` is the value delivered this cycle.

## Log

The predictor log is intended as a diagnostic and analysis interface, not as an operational display: live monitoring of the installation is done via volkszähler, while this log with its high level of detail serves later troubleshooting (e.g. when an excerpt is pulled for analysis). It therefore records every internal state per cycle.

The predictor appends one tab-separated line per cycle to `LOG_FILE` (default `/tmp/predictor.log`, configurable via `predictor_log`). On opening, a version banner and the column header are written; `# RESET` / `# ENABLE` marker lines record state changes (the `# RESET` line names the predictor version). Columns: timestamp, time, Ls_read, last2_send, est_load, history length, LOW, HIGH, spread, phase, transition counter, offset, override flag, remaining override cycles, in-peak flag, peak age (cycles), number of short peaks.
