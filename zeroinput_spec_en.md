# zeroinput – Functional Specification
*v2.0*

## Purpose

zeroinput controls one or more Soyosource Grid-Tie Inverters to achieve zero feed-in (self-consumption optimisation). The electricity meter is continuously read; the inverter output is adjusted each cycle so that the meter reads as close to zero as possible — neither importing nor exporting.

---

## Core control loop

Each cycle (~1 s):

1. Read meter value `Ls_read` (W) from vzlogger FIFO (`1-0:16.7.0`)
2. Calculate power demand:
   `power_demand = Ls_read + last_demand + zero_shift + predictive_offset`
3. Apply limits (battery, PV, timer, min/max)
4. Send demand to inverters (twice per cycle, 50 ms apart)
5. Poll MPPT chargers
6. Log values to Volkszähler

---

## Inverter control

**Single-inverter mode** — when `power_demand ≤ single_inverter_threshold` or only one inverter is configured: demand is sent in full to `basic_load_inverter_port`.

**Multi-inverter mode** — above the threshold: `power_demand / total_number_of_inverters` is sent to each configured RS485 port. Inverters wired in parallel on one port each respond to the same packet and each feed that fraction independently — so `total_number_of_inverters` must equal the total physical inverter count.

The transition between modes is hysteresis-controlled via `multi_inverter_wait` (history of recent demand values).

---

## Zero-shift

A configurable offset applied to the power demand target:

- **Manual** (`zero_shifting ≠ 0`): fixed import (`< 0`) or export (`> 0`) bias
- **Automatic** (`zero_shifting = 0`): derived from the recent meter history; slowly tracks the actual zero crossing to compensate for meter or timing offsets. Paused while the load predictor is active.

---

## Battery management

- **Voltage curve** (48–51 V): battery discharge power is limited by a power curve; full discharge allowed above 51 V.
- **Undervoltage protection**: below 48 V the inverter is disabled for 1 minute.
- **Voltage correction** (`bat_voltage_const`): compensates voltage drop under load using a configurable factor.
- **Free export** (`free_power_export`): at battery voltages above 54.5 V, excess energy is deliberately fed into the grid; scales linearly to `max_input_power` at the MPPT float voltage (~57 V).

---

## PV pass-through

Available PV power is estimated from a rolling average of recent MPPT output, minus an efficiency gap (`PV_to_AC_efficiency`). Power demand is capped at `PV_power + allowed_battery_discharge` to avoid drawing more from the battery than intended.

---

## Saw-tooth prevention

Oscillation in the send history (alternating high/low demand) is detected by comparing consecutive pairs. When saw-tooth behaviour is confirmed, the average of the last four values replaces the current demand.

---

## Ramp handling

Large sudden meter changes (> 400 W) trigger a ramp mode: the demand is held at the step value for `2 + n_active_inverters` cycles before normal regulation resumes. The first up-ramp after a stable period is dropped to filter out brief, low-significance load spikes — such as a refrigerator compressor starting — that would otherwise trigger a full ramp response unnecessarily.

---

## Load predictor (`predictor.py`)

Detects cyclic loads (washing machine, dishwasher, oven) using k-means clustering on estimated load history:

- Identifies two stable load levels: **LOW** and **HIGH**
- Once confirmed (≥ 4 phase transitions), applies a predictive offset to hold the inverter at LOW level regardless of current phase — the HIGH load draws its additional power directly from the grid
- **Peak detection**: short but high repeated Ls_read **load surges** trigger `ramp_override`, which holds the inverter at LOW level and ignores the surge entirely. The reason: these surges rise and fall faster than the inverter can ramp — by the time the inverter reaches the target, the load is already gone, producing significant export. It is better not to respond at all.
- Resets automatically on sustained high load (> `LONG_PEAK_MIN` s above threshold)
- `STARTUP_S`, `SHORT_PEAK_MAX`, `LOG_FILE` are module-level constants in `predictor.py`, hot-reloaded on file change via `reload_predictor_if_changed`
- `min_spread_w` and `load_prediction` and `predictor_log` are conf keys, hot-reloadable from `zeroinput.conf` without predictor module reload

The predictor design is intentionally open and modular: zeroinput only requires a `LoadPredictor` class with `update(Ls_read, last2_send)`, `reload_conf(conf)`, `status()`, and the attributes `enabled`, `offset`, and `ramp_override_by_predictor`. Custom prediction strategies can be implemented by replacing `predictor.py` without touching zeroinput itself.

---

## Discharge timer

Optional time-based control via `timer.txt`. Each rule sets:
- **battery** — max discharge power (W or % of `max_bat_discharge`)
- **inverter** — max feed-in power (W or % of `max_input_power`)
- **energy_Wh** — total energy budget per timer period; once exceeded, battery discharge stops and only PV pass-through continues

Rules activate in order; `0000-00-00` as date applies daily.

---

## MPPT charger support

**eSmart3** — polled via RS485 each cycle (status request, parse response). Checksum validated (`(0xaa + sum(data)) & 0xFF == 0`) — corrupt packets discarded. Supports per-device temperature monitoring and alarms, load port data (`Iload`, `Vload`, `Pload`), and `pvp` (PV peak power W) for `%PVp` display. Multiple devices supported.

**Victron MPPT (conventional)** — read via VE.Direct serial protocol in a dedicated background thread per device. `IL` (load current) and `LOAD` (ON/OFF) parsed for load port display. `Pload = IL × Vbat` derived when available. `pvp` stored for `%PVp` display. Port failure sets `CS='PORT'` for `PORT ERROR` display. One thread per port.

**Victron MPPT (aggregator)** — multiple MPPTs on a single RS485 port via `readtext_sendhex` firmware ([VE.Direct Aggregator](https://github.com/E-t0m/ve.direct-aggregator), Arduino Mega 2560 / Teensy 4.1). Handled by `VEDirectBridge`, which wraps `ve_aggregator.VEDirect` using the `on_block` callback — parsed blocks are delivered directly into `mppt_data` at block rate, no patching, no double parsing, no polling thread. [`ve_aggregator.py`](https://github.com/E-t0m/ve.direct-aggregator) must be in the same directory as `zeroinput.py`. Devices identified by SER# (`mppt_type: victron_agg`). Per-device `pvp` in `devices[ser]['pvp']`. Devices with `type: temp` in conf become `mppt_type: temp_sensor` — DS18B20 temperature blocks (field `TEMP`) written as `ext_temp` into `mppt_data`; shown in a separate row below the main table. `check_stale()` called each loop — atomically replaces `mppt_data[key]` with `{'CS': 'PORT'}` for devices not seen within `device_timeout`, zeroing all measurement values to prevent stale data affecting `combine_charger_data` and `set_victron_power`. Unconfigured SER# shown as `UNCONFIGURED` in display. Two background threads per physical port. Multiple aggregator ports supported.

**Combined data** — PPV, Vbat, Ibat, Pload aggregated across all devices. Vbat averaged. Pload summed only from ports with `inverter: soyosource` (Victron DC loads excluded unless inverter configured), projected × `n_active_inverters`.

---

## MPPT power control

`set_victron_power(device_key, watts)` — unified interface for both AGG and conventional Victron ports.

**AGG path** — `VEDirect.set_watts(ser, watts)` → firmware sends `SET <SER#> <watts>` → converts W→A (`reg = round(watts / Vbat × 10)`, register `0x2015`, 0.1A), writes and verifies. `VBAT_FALLBACK = 24V` until first Vbat received.

**Conventional path** — zeroinput replicates firmware sequence after each complete VE.Direct block: SET HEX frame → ACK (400ms timeout) → GET readback → compare. Commands queued in `_victron_cmd_queues[port]` (maxsize=1).

---

## display_mppt_data

Header: `port  name  W PV  %PVp  V bat  I bat  mode  Pload  Iload  age  Tint  Text`

Layout defined in module constant `_MPPT_FMT` (used by header, data rows, and temp-sensor rows). REC output deferred via `_drain_rec_msgs()` helper.

- `%PVp` — `PPV / pvp × 100`; for `combined` uses sum of all device `pvp` values; empty if `pvp` not configured
- `Iload` — Victron `LOAD=ON`: current A; `LOAD=OFF`: `OFF`; eSmart3: current if `Iload > 0`, else empty
- `mode` — Victron: OFF/FAULT/BULK/ABSORB/FLOAT/EQUAL/START/RECOND/EXTCON; eSmart3: WAIT/MPPT/BULK/FLOAT/PRE; both: `PORT ERROR` when `CS='PORT'`
- Unconfigured AGG devices: `<SER#>  <port_name>  UNCONFIGURED`
- REC messages (verbose) deferred to after `power request` line via `_rec_msgs` queue

---

## Inverter fault alarm

`inverter_fault_alarm: {cmd, interval}` — triggers in pure PV mode (`bat_discharge == 0`, `pv_power > 100W`, all inverters active) when `avg(long_send_history) > pv_power × n/(n-1) × 0.85`. A missing inverter causes zeroinput to ramp demand toward `pv_power × n/(n-1)` — this ratio is the detection signal. Fires verbose warning regardless; executes `cmd` at most every `interval` seconds.

---

## Predictor

`predictor_log: true/false` (conf, hot-reloadable) — controls `/tmp/predictor.log` and verbose column header output on startup. Default `true`.

`min_spread_w` (conf, hot-reloadable) — minimum spread between LOW and HIGH k-means centroid for prediction to activate. Default `150` W. If the load spread is too small, prediction deactivates and zeroinput reverts to reactive control.

`_kmeans2` rejects unimodal distributions: both groups must contain ≥ 15% of history values, otherwise `None, None` returned and predictor deactivates. Prevents false offsets during load transitions.

`MAX_HIST = 60` and `TRANSITIONS_MIN = 4` are module-level constants in `predictor.py`.
## Temperature alarms

Per eSmart3 device, independently for internal and external sensor:
- Threshold (°C) and shell command configured per alarm
- Alarm fires when temperature exceeds threshold **and** a command is set
- Individual repeat interval per alarm (`int_interval`, `ext_interval`)
- No global enable flag — an alarm is active when it is configured

---

## Data logging

zeroinput writes its own values (feed-in power, zero-shift, battery voltage, PV power, temperatures) back to vzlogger via a file-based channel (`/tmp/vz/output_to_vz.log`). vzlogger picks these up and logs them to the Volkszähler database alongside all other channels, providing a unified view of the installation.

Channel mapping is defined in `vz_channels` (editable in the web interface).

---

## Configuration and hot-reload

`zeroinput.conf` is watched for changes each cycle. Most keys take effect immediately on save. Keys requiring restart: `rs485`, `basic_load_inverter_port`, `vzlogger_log_file`, `persistent_vz_file`.

`predictor.py` is watched separately; changes (including configuration variables) are applied by reloading the module without restarting zeroinput.

---

## Web interface (`webconfig.py`)

HTTP server started with `-httpd`. Provides:
- **zeroinput.conf tab** — live editing of all hot-reloadable keys; path keys show restart-required notice
- **rs485 tab** — structured editor for RS485 port and device configuration including alarm thresholds, commands and intervals; warns that restart is required on save
- **vz channels tab** — table editor for Volkszähler channel mapping
- **timer.txt tab** — text editor for discharge rules; shows notice when timer is disabled in conf
- **status tab** — live HTML status page (only with `-web`)

---

## Output modes

| Flag | Effect |
|---|---|
| `-v` | Verbose console output each cycle |
| `-web` | Write HTML status page (`zeroinput.html`) each cycle |
| `-httpd` | Start web configuration server |
| `-no-input` | Disable all feed-in |
| `-test-alarm` | Execute alarm command and exit |
