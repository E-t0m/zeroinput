# zeroinput – Functional Specification
*v2.1*

## Purpose

zeroinput controls one or more battery grid-tie inverters to achieve zero feed-in (self-consumption optimisation). The electricity meter is continuously read; the inverter output is adjusted each cycle so that the meter reads as close to zero as possible — neither importing nor exporting.

As of v2.1 the inverter side is a generic, multi-type driver architecture: Soyosource limiter inverters and Victron MultiPlus (ESS) are supported, in any mix and any number, distributed across two power stages. The former single-type ("Soyosource only") model is gone.

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

Inverters are configured in the `inverters` block. Each entry is one **group** of identical units sharing a single port:

- `id` (the dict key) — free identifier
- `type` — `soyosource` | `victron_mk3`
- `port` — serial path; one sender per port, multiple identical units share it via `count` (they all receive one broadcast packet)
- `stage` — **list** of stages the group runs in: `[1,2]` both, `[1]` base only, `[2]` stage-2 only. A bare number `n` is read as `[n]`. Stage 2 does **not** automatically include stage 1, so a hard handover is possible (e.g. a Soyosource on `[1]` and a MultiPlus on `[2]`). An **empty list `[]` disables the inverter** — it stays in the config but never receives power and is not counted as active.
- `count` — number of identical units on the port (default 1)
- `max_power` / `min_power` — **per single unit**, in W. Below `min_power` a group sleeps.

**Two stages**, selected by the demand history with the same hysteresis as before (`single_inverter_threshold`, smoothed, `multi_inverter_wait` before falling back):

- **Stage 1** — only groups whose `stage` list contains 1 carry the base load.
- **Stage 2** — every eligible group shares `power_demand` in **equal per-unit parts**, independent of each unit's `max_power`. As demand grows, the shared per-unit value rises until the smallest units reach their `max_power`; those saturate and the remaining load is re-split equally among the still-open units. The largest unit (e.g. the MultiPlus) therefore saturates last and effectively becomes the top stage on its own — by capacity, not by a special rule.

Each group sends exactly one command per cycle: a Soyosource group broadcasts one packet (per-unit value) to all its units; a MultiPlus group writes one ESS setpoint. At startup zeroinput checks that the stage-1 groups alone can cover `single_inverter_threshold` and warns otherwise.

**Coverage-gap check.** At startup (and on saving the inverters config in the web interface) zeroinput sweeps the requested power from the smallest `min_power` up to `max_input_power` along the real control path and reports any power band that no inverter combination can deliver. In stage 2 the equal-share split moves delivered power in steps of about the active-unit count — that is the inherent control resolution and is not a gap; only jumps larger than that are flagged. Gapless coverage is required: a gap (e.g. a stage-1 unit ending at 900 W while the only stage-2 unit has `min_power` 1500 W) produces an unmissable warning. Feed-in is disabled via `max_input_power = 0` or the timer, not by leaving gaps.

The number of active units (sum of `count` over groups that received power) replaces the old `n_active_inverters` / `total_number_of_inverters` and feeds ramp handling and the Pload projection.

---

## Inverter drivers and supported hardware

Inverters are handled by a generic driver layer (`inverter_drivers.py`). Each type is a subclass of `InverterDriver` implementing `set_power(watts_per_unit)`, `sleep()`, optional `read_status()`, and `start()`/`stop()`. `build_inverters()` instantiates one driver per `inverters` entry and enforces one sender per port. Adding a new type means: new subclass, register it in `DRIVER_TYPES`, add entries with that `type` — the staging logic is untouched.

**Soyosource** (`type: soyosource`) — the limiter protocol over RS485 at 4800 baud. Stateless: opens the port, sends the demand packet twice (50 ms apart) for reliability, closes. All identical units on the port receive the same broadcast packet (no addressing). Supported models are the GTN limiter series with RS485:

- GTN-1000LIM (24/36/48/72/96 V) and the GTN-1200 / GTN-2000 limiter variants
- Display, WiFi, and OEM-dongle hardware variants all expose the same RS485 limiter command
- The waterproof **GTW** outdoor housing has **no** limiter/RS485 and is not controllable
- A larger unit such as the GTN-2000 is just an entry with `max_power: 2000`; no code change, only config (verify packet compatibility on real hardware first)

**Victron MultiPlus** (`type: victron_mk3`) — active ESS power setpoint over VE.Bus via an MK2/MK3-USB adapter (see *MultiPlus / VE.Bus control*). Requirements:

- A VE.Bus Multi, MultiPlus, MultiPlus-II, Multi Grid or Quattro with 2nd-generation microprocessor (26/27); all currently shipping VE.Bus inverter/chargers qualify. The Multi RS is excluded (no ESS).
- The **ESS assistant** must be configured in VEConfigure, with the switch fully ON (not "charger only"). Without it the assistant scan fails and the driver stays inactive.
- An MK2-USB / MK3-USB adapter (or a direct VE.Bus-RS485 connection). No GX device is required.
- Other VE.Bus ESS devices (Quattro, additional Multi variants) use the same `vebus.py` path with minimal change.

**Why only these two types.** Both deliver an *active watt setpoint at the 1 s control rate from a controllable (battery) source*. Other inverter families were evaluated and intentionally excluded:

- **Growatt** (MIC/MOD/MID PV strings): only a percentage power cap / export limit, no active setpoint. SPH/SPA hybrids do have a watt setpoint but write to EEPROM/flash and react slowly — unsafe at 1 s. Off-grid SPF is island-only.
- **Hoymiles HM/HMS** (micro): only a relative/absolute power *limit*, requires a DTU, and reacts in 18–90 s — incompatible with the 1 s loop.
- **APsystems EZ1** (micro): only `setMaxPower` (a cap) over fragile HTTP/WLAN; the manufacturer removed local control in firmware 1.1.2_b.
- **Deye / Sunsynk / Sol-Ark** (hybrid, all identical): a real watt setpoint over Modbus RTU, but the setpoint registers are undocumented as RAM vs flash; the only safe field-proven rate is ~1500 writes/day (~1 per minute), ~38× below zeroinput's per-second rate. Integrable only as a deliberately rate-limited slow stage, not as a fast regulating stage.

The common rule: PV-string, micro and off-grid inverters have an uncontrollable source and can only throttle; true watt-setpoint control needs a controllable source behind the inverter (DC battery for Soyosource, ESS battery for MultiPlus).

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

Large sudden meter changes (> 400 W) trigger a ramp mode: the demand is held at the step value for `2 + active unit count` cycles before normal regulation resumes. The first up-ramp after a stable period is dropped to filter out brief, low-significance load spikes — such as a refrigerator compressor starting — that would otherwise trigger a full ramp response unnecessarily.

---

## Load predictor (`predictor.py`)

Detects cyclic loads (washing machine, dishwasher, oven) using k-means clustering on estimated load history:

- Identifies two stable load levels: **LOW** and **HIGH**
- Once confirmed (≥ 4 phase transitions), applies a predictive offset to hold the inverter at LOW level regardless of current phase — the HIGH load draws its additional power directly from the grid
- **Peak detection**: short but high repeated Ls_read **load surges** trigger `ramp_override`, which holds the inverter at LOW level and ignores the surge entirely. The reason: these surges rise and fall faster than the inverter can ramp — by the time the inverter reaches the target, the load is already gone, producing significant export. It is better not to respond at all.
- Resets automatically on sustained high load (> `LONG_PEAK_MIN` s above threshold)
- `STARTUP_S`, `LONG_PEAK_MIN`, `LOG_FILE` are module-level constants in `predictor.py`, hot-reloaded on file change via `reload_predictor_if_changed`
- `min_spread_w` and `load_prediction` and `predictor_log` are conf keys, hot-reloadable from `zeroinput.conf` without predictor module reload

The predictor design is intentionally open and modular: zeroinput only requires a `LoadPredictor` class with `update(Ls_read, last2_send)`, `reload_conf(conf)`, `status()`, and the attributes `enabled`, `offset`, and `ramp_override_by_predictor`. Custom prediction strategies can be implemented by replacing `predictor.py` without touching zeroinput itself.

---

## Discharge timer

Optional time-based control via `timer.txt`. Each rule sets:
- **battery** — max discharge power (W or % of `max_bat_discharge`)
- **inverter** — max feed-in power (W or % of `max_input_power`)
- **energy_Wh** — total energy budget per timer period; once exceeded, battery discharge stops and only PV pass-through continues

Rules activate in order; `0000-00-00` as date applies daily.

`timer.txt` can be written by hand or generated by an external tool. The [tibber tools](https://github.com/E-t0m/zeroinput/tree/main/tibber) (optional, unmaintained) generate a `timer.txt` from Tibber dynamic price data, shifting inverter power, battery discharge and energy budgets into the most expensive price slots.

---

## MPPT charger support

**eSmart3** — polled via RS485 each cycle (status request, parse response). Checksum validated (`(0xaa + sum(data)) & 0xFF == 0`) — corrupt packets discarded. Supports per-device temperature monitoring and alarms, load port data (`Iload`, `Vload`, `Pload`), and `pvp` (PV peak power W) for `%PVp` display. Multiple devices supported.

**Victron MPPT (conventional)** — read via VE.Direct serial protocol in a dedicated background thread per device. `IL` (load current) and `LOAD` (ON/OFF) parsed for load port display. `Pload = IL × Vbat` derived when available. `pvp` stored for `%PVp` display. Port failure sets `CS='PORT'` for `PORT ERROR` display. One thread per port.

**Victron MPPT (aggregator)** — multiple MPPTs on a single RS485 port via `readtext_sendhex` firmware ([VE.Direct Aggregator](https://github.com/E-t0m/ve.direct-aggregator), Arduino Mega 2560 / Teensy 4.1). Handled by `VEDirectBridge`, which wraps `ve_aggregator.VEDirect` using the `on_block` callback — parsed blocks are delivered directly into `mppt_data` at block rate, no patching, no double parsing, no polling thread. [`ve_aggregator.py`](https://github.com/E-t0m/ve.direct-aggregator) must be in the same directory as `zeroinput.py`. Devices identified by SER# (`mppt_type: victron_agg`). Per-device `pvp` in `devices[ser]['pvp']`. Devices with `type: temp` in conf become `mppt_type: temp_sensor` — DS18B20 temperature blocks (field `TEMP`) written as `ext_temp` into `mppt_data`; shown in a separate row below the main table. `check_stale()` called each loop — atomically replaces `mppt_data[key]` with `{'CS': 'PORT'}` for devices not seen within `device_timeout`, zeroing all measurement values to prevent stale data affecting `combine_charger_data` and `set_victron_power`. Unconfigured SER# shown as `UNCONFIGURED` in display. Two background threads per physical port. Multiple aggregator ports supported.

**Combined data** — PPV, Vbat, Ibat, Pload aggregated across all devices. Vbat averaged. Pload summed only from charger ports that share a line with a Soyosource inverter (Victron DC loads excluded), projected × active unit count. Note: with mixed power classes (e.g. 900 W Soyosource + 2400 W MultiPlus) this single-reading projection is only approximate.

---

## MPPT power control

`set_victron_power(device_key, watts)` — unified interface for both AGG and conventional Victron ports.

**AGG path** — `VEDirect.set_watts(ser, watts)` → firmware sends `SET <SER#> <watts>` → converts W→A (`reg = round(watts / Vbat × 10)`, register `0x2015`, 0.1A), writes and verifies. `VBAT_FALLBACK = 24V` until first Vbat received.

**Conventional path** — zeroinput replicates firmware sequence after each complete VE.Direct block: SET HEX frame → ACK (400ms timeout) → GET readback → compare. Commands queued in `_victron_cmd_queues[port]` (maxsize=1).

---

## MultiPlus / VE.Bus control (`vebus.py`)

The MultiPlus is controlled directly over the VE.Bus MK2 protocol through an MK2/MK3-USB adapter at 2400 baud — no GX device. `vebus.py` is derived from martiby/multiplus2 (MIT), adapted to the zeroinput style.

Startup sequence per device: open port → version probe (soft) → `init_address` (the real connectivity gate) → `scan_ess_assistant`, which walks the assistant RAM records from ID 128 to locate the ESS assistant and resolve the setpoint RAM-ID automatically (robust across models, not hard-coded to 131). If any step fails the driver stays inactive and the rest of zeroinput keeps running.

Each cycle the driver writes the ESS power setpoint via `CommandWriteViaID` (0x37) with flag `0x02` (**RAM only**, no EEPROM wear). The setpoint must be refreshed < 60 s; zeroinput's ~1 s loop guarantees this. All VE.Bus control points are RAM, so per-second writes are safe by design — this is the key difference from EEPROM-based hybrids.

**Sign convention.** The driver works in zeroinput/Soyosource terms: `set_power(positive) = feed-in`. Internally it negates to the Victron convention (positive = charge). `mk3_ess_sign: -1` flips the direction if the wiring/CT placement reports it reversed. `sleep()` writes 0 W (passthrough). `read_status()` returns `Pac` (feed-in positive), `Vbat`, `Ibat`, `soc`, `out_p` for monitoring.

---

## display_mppt_data

Header: `port  name  W PV  %PVp  V bat  I bat  mode  Pload  Iload  age  Tint  Text`

Layout defined in module constant `_MPPT_FMT` (used by header, data rows, and temp-sensor rows). REC output deferred via `_drain_rec_msgs()` helper.

- `%PVp` — `PPV / pvp × 100`; for `combined` uses sum of all device `pvp` values; empty if `pvp` not configured. The combined value is also stored as `mppt_data['combined']['PVperc']` and exportable as a vzlogger channel.
- `Iload` — Victron `LOAD=ON`: current A; `LOAD=OFF`: `OFF`; eSmart3: current if `Iload > 0`, else empty
- `mode` — Victron: OFF/FAULT/BULK/ABSORB/FLOAT/EQUAL/START/RECOND/EXTCON; eSmart3: WAIT/MPPT/BULK/FLOAT/PRE; both: `PORT ERROR` when `CS='PORT'`
- Unconfigured AGG devices: `<SER#>  <port_name>  UNCONFIGURED`
- REC messages (verbose) deferred to after `power request` line via `_rec_msgs` queue

---

## Predictor

`predictor_log: true/false` (conf, hot-reloadable) — controls `/tmp/predictor.log` and verbose column header output on startup. Default `true`.

`min_spread_w` (conf, hot-reloadable) — minimum spread between LOW and HIGH k-means centroid for prediction to activate. Default `150` W. If the load spread is too small, prediction deactivates and zeroinput reverts to reactive control.

`_kmeans2` rejects unimodal distributions: both groups must contain ≥ 15% of history values, otherwise `None, None` returned. During learning (`transition_cnt < TRANSITIONS_MIN`) this resets the learned levels; once stable, learned levels are preserved to survive brief unimodal periods caused by good regulation.

A `UNIMODAL_TOLERANCE = 5` counter requires consecutive bad k-means results before learning restarts — single fluctuations do not discard progress.

`transition_cnt` is capped at `TRANSITIONS_MIN` — values above carry no meaning and would otherwise grow unbounded over days of operation.

A single peak threshold: peaks `< LONG_PEAK_MIN` are short (count toward override activation), peaks `>= LONG_PEAK_MIN` are long (clear peak history, cancel override). The former `SHORT_PEAK_MAX` constant is removed — there is no grey zone.

`MAX_HIST = 60` and `TRANSITIONS_MIN = 4` are module-level constants in `predictor.py`.
## Temperature alarms

Configured in the `alarms` conf block (separate from `chargers`), keyed by device name. Applies to eSmart3 devices (internal + external sensor) and AGG `temp_sensor` devices (external only).

Each sensor supports two independent alarms:
- **`int_hi` / `ext_hi`** — fires when `temp > threshold`
- **`int_lo` / `ext_lo`** — fires when `temp < threshold`

Each alarm has a `_cmd` (shell command) and `_interval` (repeat interval in seconds, default 300). An alarm is active only when both threshold and command are set. Thresholds may be negative or zero.

---

## Data logging

zeroinput writes its own values (feed-in power, zero-shift, battery voltage, PV power, temperatures) back to vzlogger via a file-based channel (`/tmp/vz/output_to_vz.log`). vzlogger picks these up and logs them to the Volkszähler database alongside all other channels, providing a unified view of the installation.

Channel mapping is defined in `vz_channels` (editable in the web interface).

---

## Configuration and hot-reload

`zeroinput.conf` is watched for changes each cycle. Most keys take effect immediately on save. Keys requiring restart: `chargers`, `inverters`, `vzlogger_log_file`, `persistent_vz_file`. The `chargers` and `inverters` blocks are structural — charger reader threads and inverter drivers are built once at startup, so changes need a restart (use the restart tab / `/api/restart`).

`predictor.py` is watched separately; changes (including configuration variables) are applied by reloading the module without restarting zeroinput.

---

## Web interface (`webconfig.py`)

HTTP server started with `-httpd`. Provides:
- **zeroinput.conf tab** — live editing of all hot-reloadable keys; path keys show restart-required notice. The `/api/conf` endpoint is structure-agnostic and edits the JSON in place, so the `chargers` and `inverters` blocks are editable here as raw values.
- **chargers tab** — structured editor for MPPT chargers and temperature sensors (eSmart3, Victron, Aggregator with SER# table). Restart required on save. PVp field hidden for `type: temp` devices.
- **inverters tab** — structured editor for feed-in inverters (type, port, stage checkboxes, count, max/min power, ESS sign). Validates for power coverage gaps before saving. Restart required on save.
- **alarms tab** — per-device temperature alarms (eSmart3: int\_hi/int\_lo/ext\_hi/ext\_lo; temp\_sensor: ext\_hi/ext\_lo). Device labels use `temp_display` name when set. Cards collapsed on load.
- **vz channels tab** — table editor for Volkszähler channel mapping
- **timer.txt tab** — text editor for discharge rules; shows notice when timer is disabled in conf
- **restart tab** — sends `sudo systemctl restart zeroinput`; shows warning about inverter output interruption and vzlogger restart if data stops
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
