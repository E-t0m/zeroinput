# zeroinput – Functional Specification
*v2.3*

## Purpose

zeroinput controls one or more battery grid-tie inverters to achieve zero feed-in (self-consumption optimisation). The electricity meter is continuously read; the inverter output is adjusted each cycle so that the meter reads as close to zero as possible — neither importing nor exporting.

The inverter side is a generic, multi-type driver architecture: Soyosource limiter inverters and Victron MultiPlus-II (ESS) are supported, in any mix and any number, distributed across two power stages.

## Core control loop

Each cycle (~1 s):

1. Read meter value `Ls_read` (W) from vzlogger FIFO (`1-0:16.7.0`)
2. Calculate power demand:
   `power_demand = Ls_read + last_demand + zero_shift + predictive_offset`
3. Apply limits (battery, PV, timer, min/max)
4. Send demand to inverters (twice per cycle, 50 ms apart)
5. Poll MPPT chargers
6. Log values to [volkszaehler.org](https://volkszaehler.org/)

---

## Inverter control

Inverters are configured in the `inverters` block. Each entry is one **group** of identical units sharing a single port:

- `id` (the dict key) — free identifier
- `type` — `soyosource` | `victron_mk3`
- `port` — serial path; one sender per port, multiple identical units share it via `count` (they all receive one broadcast packet)
- `stage` — **list** of stages the group runs in: `[1,2]` both, `[1]` base only, `[2]` stage-2 only. A bare number `n` is read as `[n]`. Stage 2 does **not** automatically include stage 1, so a hard handover is possible (e.g. a Soyosource on `[1]` and a MultiPlus on `[2]`). An **empty list `[]` disables the inverter** — it stays in the config but never receives power and is not counted as active.
- `count` — number of identical units on the port (default 1)
- `max_power` / `min_power` — **per single unit**, in W. Below `min_power` a group sleeps.

**Two stages**, selected by the demand history with hysteresis (`single_inverter_threshold`, smoothed, `multi_inverter_wait` before falling back):

- **Stage 1** — only groups whose `stage` list contains 1 carry the base load.
- **Stage 2** — every eligible group shares `power_demand` in **equal per-unit parts**, independent of each unit's `max_power`. As demand grows, the shared per-unit value rises until the smallest units reach their `max_power`; those saturate and the remaining load is re-split equally among the still-open units. The largest unit (e.g. the MultiPlus) therefore saturates last and effectively becomes the top stage on its own — by capacity, not by a special rule.

Each group sends exactly one command per cycle: a Soyosource group broadcasts one packet (per-unit value) to all its units; a MultiPlus group writes one ESS setpoint. At startup zeroinput checks that the stage-1 groups alone can cover `single_inverter_threshold` and warns otherwise.

**Stage transitions.** When the stage drops from 2 to 1 (after the full `multi_inverter_wait` hysteresis), the demand switches directly to the stage-1 distribution in the same cycle; the single remaining stage-1 unit takes over the whole demand in one step. Because the inverters need a few seconds to settle after this hard switch and the meter briefly sees the transition transient, `power_demand` is held at its value from the moment of the transition for a few cycles — the distribution deliberately bypasses the freshly computed value so the control loop does not fight its own transition. A running ramp, or a load rise that pulls the stage back to 2, discards the hold at once, so a genuine load change is served immediately. Stage-up (1→2) is immediate, without the `multi_inverter_wait` delay and without a hold. While at stage 2, the verbose/web output appends `Nc` (N cycles) to the unit line — a conditional estimate of how many cycles remain until the stage would return to 1 *if every future value stays at or below the threshold*. It grows again whenever a new high value enters the history, since stage-down needs the whole window to settle.

**Coverage-gap check.** At startup (and on saving the inverters config in the web interface) zeroinput sweeps the requested power from the smallest `min_power` up to `max_input_power` along the real control path and reports any power band that no inverter combination can deliver. In stage 2 the equal-share split moves delivered power in steps of about the active-unit count — that is the inherent control resolution and is not a gap; only jumps larger than that are flagged. Gapless coverage is required: a gap (e.g. a stage-1 unit ending at 900 W while the only stage-2 unit has `min_power` 1500 W) produces an unmissable warning. Feed-in is disabled via `max_input_power = 0` or the timer, not by leaving gaps.

**Capacity check.** At startup and after every configuration reload that changes `max_input_power` or `single_inverter_threshold`, zeroinput compares `max_input_power` with the reachable inverter capacity: max(min(stage-1 capacity, `single_inverter_threshold`), stage-2 capacity), each as the sum of `count × max_power` of the groups in that stage; groups without a stage do not count. If `max_input_power` is higher, a warning is printed; the value is not changed. Background: the power history stores the requested power, not the delivered power, and battery derating by voltage, free export and the heat-protection fallback scale with `max_input_power` — a value that is too high distorts them. The web interface shows the same note after saving the inverters or the settings.

The number of active units (sum of `count` over groups that received power) feeds ramp handling.

---

## Inverter drivers and supported hardware

Inverters are handled by a generic driver layer (`inverter_drivers.py`). Each type is a subclass of `InverterDriver` implementing `set_power(watts_per_unit)`, `sleep()`, optional `read_status()`, and `start()`/`stop()`. `build_inverters()` instantiates one driver per `inverters` entry and enforces one sender per port. Adding a new type means: new subclass, register it in `DRIVER_TYPES`, add entries with that `type` — the staging logic is untouched.

**Soyosource** (`type: soyosource`) — the limiter protocol over RS485 at 4800 baud. Stateless: opens the port, sends the demand packet twice (50 ms apart) for reliability, closes. All identical units on the port receive the same broadcast packet (no addressing). Supported models are the GTN limiter series with RS485:

- GTN-1000LIM (24/36/48/72/96 V) and the GTN-1200 / GTN-2000 limiter variants
- Display, WiFi, and OEM-dongle hardware variants all expose the same RS485 limiter command
- The waterproof **GTW** outdoor housing has **no** limiter/RS485 and is not controllable
- A larger unit such as the GTN-2000 is just an entry with `max_power: 1600` (battery mode rating); no code change, only config (verify packet compatibility on real hardware first)

**Victron MultiPlus** (`type: victron_mk3`) — active ESS power setpoint over VE.Bus via an MK2/MK3-USB adapter (see *MultiPlus / VE.Bus control*). Requirements:

- A VE.Bus Multi, MultiPlus, MultiPlus-II, Multi Grid or Quattro with 2nd-generation microprocessor (26/27); all currently shipping VE.Bus inverter/chargers qualify. The Multi RS is excluded (no ESS).
- The **ESS assistant** must be configured in VEConfigure, with the switch fully ON (not "charger only"). Without it the assistant scan fails and the driver stays inactive.
- An MK2-USB / MK3-USB adapter (or a direct VE.Bus-RS485 connection). No GX device is required.
- Other VE.Bus ESS devices (Quattro, additional Multi variants) use the same `vebus.py` path with minimal change.

**Why only these two types.** Both deliver an *active watt setpoint at the 1 s control rate from a controllable (battery) source*. Other inverter families were evaluated and intentionally excluded:

- **Growatt** (MIC/MOD/MID PV strings): only a percentage power cap / export limit, no active setpoint. SPH/SPA hybrids do have a watt setpoint but write to EEPROM/flash and react slowly — unsafe at 1 s. Off-grid SPF is island-only.
- **Hoymiles HM/HMS** (micro): only a relative/absolute power *limit*, requires a DTU, and reacts in 18–90 s — incompatible with the 1 s loop.
- **APsystems EZ1** (micro): only `setMaxPower` (a power cap) over HTTP/WLAN, no active setpoint. The local API must be enabled permanently in the app (firmware 1.7.0 and later); older firmware writes every cap to flash.
- **Deye / Sunsynk / Sol-Ark** (hybrid, all identical): a real watt setpoint over Modbus RTU, but the setpoint registers are undocumented as RAM vs flash; the only safe field-proven rate is ~1500 writes/day (~1 per minute), ~38× below zeroinput's per-second rate. Integrable only as a deliberately rate-limited slow stage, not as a fast regulating stage.

The common rule: PV-string, micro and off-grid inverters have an uncontrollable source and can only throttle; true watt-setpoint control needs a controllable source behind the inverter (DC battery for Soyosource, ESS battery for MultiPlus).

---

## Zero-shift

A configurable offset applied to the power demand target:

- **Manual** (`zero_shifting ≠ 0`): fixed import (`< 0`) or export (`> 0`) bias
- **Automatic** (`zero_shifting = 0`): derived from the recent meter history; slowly tracks the actual zero crossing to compensate for meter or timing offsets. Paused while the load predictor is active.

---

## Battery management

- **Configurable cell count** (`cell_count`): the built-in battery voltage thresholds are stored per cell and scale with the number of LiFePO4 cells in series. Default is 16S (51.2 V nominal); 15S (48 V), 8S (24/28 V) and other values are possible. The voltages below are for 16S and scale accordingly; the free export voltages are an exception, they are configured as pack voltages.
- **Voltage curve** (48–51 V at 16S, i.e. 3.00–3.19 V/cell): battery discharge power is limited by a power curve; full discharge allowed above the upper bound.
- **Undervoltage protection**: below the lower bound (48 V at 16S) the inverter is disabled for 1 minute.
- **Voltage correction** (`bat_voltage_const`): compensates voltage drop under load using a configurable factor (V/kW).
- **Free export** (`free_power_export`): from the pack voltage `free_export_voltage_start` on, excess energy is deliberately fed into the grid; the power rises linearly up to `max_input_power` at the pack voltage `free_export_voltage_full`. Both values are voltages of the whole battery pack in volts and are compared directly with the measured battery voltage. Default: 54.5 V and 57.0 V; if the values are missing from the configuration, 3.40625 V and 3.5625 V per cell multiplied by `cell_count` apply. The full voltage should match the absorption/float voltage of the MPPT chargers. If the start voltage is not above 0 or not below the full voltage, these defaults apply and a warning is printed. If either value divided by `cell_count` lies outside 3.2–3.65 V per cell, it is still used, but reported with a warning. The web interface refuses to save values in the wrong order and asks for confirmation for values outside the cell range.
- **Vbat plausibility filter and voltage hold**: when averaging battery voltage across multiple chargers, readings below 2.0 V/cell are discarded, since a LiFePO4 cell never runs that low in operation. A disturbed charger reporting 0 V or an implausibly low value therefore has no effect on the averaged voltage. If no charger reports a plausible value in a given cycle — for example at night, when the MPPTs enter sleep for lack of PV input voltage and stop sending telemetry — the last plausible measured voltage is held and passed on. A missing measurement thus never becomes 0 V, which would look like an empty battery and trip the undervoltage protection. This logic is fully contained in the charger aggregation; the control loop always receives a plausible voltage. As long as no valid value has ever been seen (cold start), the startup wait phase applies.

**Startup wait for battery data.** Before entering the main loop, zeroinput calls `combine_charger_data()` repeatedly (every 0.2 s, up to 10 s) until `mppt_data['combined']['Vbat'] > 0`. Without this, the very first cycle would see `Vbat == 0` (no charger data polled/received yet) — indistinguishable from a real 0 V reading — and falsely trigger the 1-minute undervoltage timeout on every restart. This works for any charger type: synchronous readers (eSmart3, Modbus) already have data from `build_chargers()`'s warm-up read, while AGG/Victron reader threads deliver their first block within this window. If no charger ever reports `Vbat` within 10 s, zeroinput logs a warning and starts anyway.

---

## Heat protection

An optional heat protection caps `power_demand` linearly based on a selectable temperature sensor. Below `heat_temp_low` the full `max_input_power` is allowed; at/above `heat_temp_high` the inverter is switched off (cap 0), linear in between. This ensures the inverter does not keep running at reduced power while overheating, but shuts off.

The trigger is exactly one charger whose config carries `heat_protect: true`. Any temperature-carrying device is eligible (dedicated temp sensor, eSmart3, Modbus charger, aggregator sub-sensor); the reading is `ext_temp`, falling back to `int_temp`. With no sensor selected the protection is off. If the selected sensor briefly returns no reading, the last valid temperature is reused — a real device/heatsink temperature changes slowly enough that a short gap is harmless. Only on a sustained sensor dropout does a fixed fraction of the maximum power apply as a safe fallback (`HEAT_FAIL_FRACTION`, 50 % by default).

Configuration: `heat_temp_low`, `heat_temp_high` (global thresholds) and the sensor selection (`heat_protect` flag on one charger, chosen in the web interface).

---

## PV pass-through

Available PV power is estimated from a rolling average of recent MPPT output, minus an efficiency gap (`PV_to_AC_efficiency`). Power demand is capped at `PV_power + allowed_battery_discharge` to avoid drawing more from the battery than intended.

---

## Saw-tooth prevention

Oscillation in the send history (alternating high/low demand) is detected by comparing consecutive pairs. When saw-tooth behaviour is confirmed, the average of the last four values replaces the current demand.

---

## Ramp handling

Large sudden meter changes trigger a ramp mode: what counts is the **change since the previous cycle** (`Ls_read − last_Ls_read`), not the absolute value. When that change exceeds 400 W, the demand is held at the step value for `2 + round(min(|step|, max_input_power) / (400 × active unit count))` cycles before normal regulation resumes. Testing the change rather than the absolute value matters because the meter need not have been at zero before the step — a load drop that throws the meter from, say, +300 W (import) to −1100 W (export) is a 1400 W step, even though neither absolute value on its own cleanly crosses the threshold in the expected direction. The formula assumes each active inverter unit ramps its setpoint at roughly 400 W/s — a larger step or fewer active units takes more cycles to settle, more units share the ramp and settle faster. The step is capped to `max_input_power`, since `power_demand` cannot exceed it regardless of the meter step — without the cap, a step larger than the system can ever deliver would produce an unrealistically long hold. The minimum is 2 cycles (small steps, several units); applies to both up- and down-ramps. The first up-ramp after a stable period is dropped to filter out brief, low-significance load spikes — such as a refrigerator compressor starting — that would otherwise trigger a full ramp response unnecessarily.

A running ramp is aborted if the meter step now points in the opposite direction by more than 400 W (e.g. a strong down-step while an up-ramp is still counting down). Without this, the demand would stay held at the stale ramp value — causing unnecessary export or import — until the original ramp's countdown finished. On abort, a new ramp in the new direction starts in the same cycle.

---

## Load predictor (`predictor.py`)

The load predictor detects cyclic loads (washing machine, dishwasher, stove) and
recurring short high-load peaks and prevents the export they would otherwise cause.
It runs two mechanisms: k-means level stabilisation for cyclic loads and a
peak/override mechanism for recurring short peaks. A peak counts as short as long
as it does not exceed the length threshold; if it does, it is classified as a long
load and excluded from the peak mechanism. The override is only armed by two real
short peaks within the observation window, so a single long load does not trigger
an override.

It is an optional, exchangeable module. zeroinput only requires a `LoadPredictor`
class with `update(Ls_read, last2_send)`, `reload_conf(conf)`, `status()` and the
attributes `enabled`, `offset` and `ramp_override_by_predictor`. Own strategies can
be implemented by replacing `predictor.py`, without changing zeroinput itself.

If `predictor.py` is present, zeroinput always instantiates a real `LoadPredictor`
at startup — regardless of the initial value of `load_prediction` — and switches it
on/off only via `reload_conf`. The stub object (no-op `update`/`reload_conf` with
`enabled=False`) is only used if `predictor.py` is missing entirely
(`ImportError`). `load_prediction` is therefore fully hot-switchable in both
directions: switching on re-initialises the predictor's learning state
(`reload_conf` calls `_init_state()` on the off→on transition), switching off takes
effect immediately through the `enabled` check below.

zeroinput honours `ramp_override_by_predictor` only while `predictor.enabled` is
true. This guards against a stale override flag left over from before
`load_prediction` was switched off at runtime — without this `enabled` check such a
flag would force `ramp_cnt` to 0 every cycle and no ramp could run.

The complete description — both mechanisms, the spread window, the cycle-based
time model and all constants — is in
**[predictor_spec.md](predictor_spec.md)**.


---

## Discharge timer

Optional time-based control via `timer.txt`. Each rule sets:
- **battery** — max discharge power (W or % of `max_bat_discharge`)
- **inverter** — max feed-in power (W or % of `max_input_power`)
- **energy_Wh** — total energy budget per timer period; once exceeded, battery discharge stops and only PV pass-through continues

A negative `energy_Wh` (`-1`) means "no energy budget": the budget check is skipped entirely and only `battery` and `inverter` apply. The same holds without a readable `timer.txt`. `0` is the tightest budget, not a free pass: the energy counter is reset only when the last applied line changes, so a budget of `0` counts as exceeded from the first watt-second drawn from the battery and stays that way until the line changes. The status line shows consumption and budget as `energy <used>/<budget> Wh`, and as `energy <used>/unlimited Wh` for `-1`; the message on exceeding it reads `battery energy budget <n> Wh exceeded`, which distinguishes it from the power limit `battery discharge limit <n> W`.

Rules activate in order; `0000-00-00` as date applies daily.

`timer.txt` can be written by hand or generated by an external tool. The [tibber tools](../../tibber) (optional, testing) generate a `timer.txt` from Tibber dynamic price data, shifting inverter power, battery discharge and energy budgets into the most expensive price slots.

---

## MPPT charger support

Charger-side I/O lives in `charger_drivers.py` (separate from the inverter driver layer). It owns `mppt_data`, all reader threads, the `VEDirectBridge` instances and the Modbus charger drivers. `zeroinput.py` calls its public API (`build_chargers`, `poll_chargers`, `combine_charger_data`, `display_mppt_data`, `check_temp_alarms`, `set_victron_power`, `check_stale`) and reads `mppt_data` directly as a shared reference.

**eSmart3** — polled via RS485 each cycle (status request, parse response). Checksum validated (`(0xaa + sum(data)) & 0xFF == 0`) — corrupt packets discarded. Supports per-device temperature monitoring and alarms, load port data (`Iload`, `Vload`, `Pload`), and `pvp` (PV peak power W) for `%PVp` display. Multiple devices supported.

**Victron MPPT (conventional)** — read via VE.Direct serial protocol in a dedicated background thread per device. `IL` (load current) and `LOAD` (ON/OFF) parsed for load port display. `Pload = IL × Vbat` derived when available. `pvp` stored for `%PVp` display. Port failure sets `CS='PORT'` for `ErrorP` display. One thread per port.

**Victron MPPT (aggregator)** — multiple MPPTs on a single serial port via `readtext_sendhex` firmware ([VE.Direct Aggregator](https://github.com/E-t0m/ve.direct-aggregator), Arduino Mega 2560 / Teensy 4.1). VE.Direct is electrically a 3.3 V UART; the connection to the Pi is via USB-UART adapter. RS485 level converters can be used on either or both sides to extend cable length, but are not required. Handled by `VEDirectBridge`, which wraps `ve_aggregator.VEDirect` using the `on_block` callback — parsed blocks are delivered directly into `mppt_data` at block rate, no patching, no double parsing, no polling thread. [`ve_aggregator.py`](https://github.com/E-t0m/ve.direct-aggregator) must be in the same directory as `zeroinput.py`. Devices identified by SER# (`mppt_type: victron_agg`). Per-device `pvp` in `devices[ser]['pvp']`. Devices with `type: temp` in conf become `mppt_type: temp_sensor` — DS18B20 temperature blocks (field `TEMP`) written as `ext_temp` into `mppt_data`; shown in a separate row below the main table. `check_stale()` called each loop — atomically replaces `mppt_data[key]` with `{'CS': 'PORT'}` for devices not seen within `device_timeout`, zeroing all measurement values to prevent stale data affecting `combine_charger_data` and `set_victron_power`. Unconfigured SER# shown as `UNCONFIGURED` in display. The AGG firmware sends an `ALIVE` keepalive roughly every 10 s; `ve_aggregator.VEDirect` accepts an `on_alive` callback (called from both ALIVE detection points: the reader thread's line scanner and `_handle_block`) which `VEDirectBridge` uses to report each one as `REC <port> ALIVE <agg name>`, confirming the MCU itself is reachable independent of any individual MPPT device. Two background threads per physical port (VE.Direct reader, sender). Multiple aggregator ports supported.

**Modbus chargers (EPever / Renogy / Morningstar)** — polled synchronously each cycle (open → read → close), like eSmart3, not threaded. A self-contained Modbus RTU reader (CRC16, request/response framing) is implemented on top of pyserial, so no external Modbus library is required. Each type takes an optional `unit` (Modbus slave address, default 1).
- `epever` — Tracer-AN / Tracer-BN (and LS-B). Input registers (func 4) from 0x3100, values ×100, 115200 8N1. Reads PV V/P, battery V/I, battery + internal temperature, SOC, and charge status.
- `renogy` — Rover / Rover Elite / Adventurer / Wanderer. Holding registers (func 3) 0x0100–0x0109, 9600 8N1. Battery temperature uses a sign-flag byte (bit 7 = negative). Reads SOC, battery V/I, load power, PV V/P, controller + battery temperature.
- `morningstar` — TriStar MPPT 45/60. RAM registers with fixed-point scaling: V_PU (0x0000/1) and I_PU (0x0002/3) are read first, then applied as `n·V_PU·2⁻¹⁵` (voltage), `n·I_PU·2⁻¹⁵` (current), `n·V_PU·I_PU·2⁻¹⁷` (power). 9600 8N1. NOTE: EIA-485 is available only on the TS-MPPT-60/M; the TS-MPPT-45 is RS-232 only and cannot share an RS485 bus.

EPever, Renogy and Morningstar expose internal and battery temperatures and can be used for temperature alarms.

One Modbus charger is supported per port entry (the conf key is the port path). Running several Modbus chargers on one physical RS485 bus (multi-drop with distinct `unit` addresses) is electrically possible but not yet supported by the config structure — give each Modbus charger its own port.

**PORT error and PPV decay.** Any charger device whose port fails (serial error, timeout, or AGG stale timeout) has its `mppt_data` entry replaced with `{'CS': 'PORT'}`. `combine_charger_data` detects this and, instead of contributing zero to `combined['PPV']`, retains the last known PPV value and reduces it by 10% each cycle. This means feed-in is not cut immediately on a charger fault but tapers off gradually — after ~22 cycles (≈22 s at 1 s/cycle) the decayed value is below 14% of the original; after ~45 cycles below 1%. When the device recovers and delivers live data again, the actual measured PPV is used immediately and the decay resets. The decaying value is shown in the device's own PV column (alongside `ErrorP` in the mode column), so the fade-out is visible per device, not only in the combined total.

**Combined data** — PPV, Vbat, Ibat, Pload aggregated across all devices. Vbat is averaged (values below 2.0 V/cell excluded); if no plausible reading exists in a cycle, the last valid Vbat is held. PPV, Ibat and Pload are summed. Pload is the real sum of every device's measured load.

---

## MPPT power control

`set_victron_power(device_key, watts)` — unified interface for both AGG and conventional Victron ports.

**AGG path** — `VEDirect.set_watts(ser, watts)` → firmware sends `SET <SER#> <watts>` → converts W→A (`reg = round(watts / Vbat × 10)`, register `0x2015`, 0.1A), writes and verifies. Until the first Vbat value of a device has been received, the firmware rejects SET for that device with `ERR <SER#> vbat unknown`; the next setpoint one second later then takes effect.

**Conventional path** — zeroinput replicates firmware sequence after each complete VE.Direct block: SET HEX frame → ACK (400ms timeout) → GET readback → compare. Commands queued in `_victron_cmd_queues[port]` (maxsize=1). The W→A conversion uses the battery voltage of that charger, else the averaged battery voltage of all chargers, else 3.2 V per cell × `cell_count`.

---

## MultiPlus / VE.Bus control (`vebus.py`)

The MultiPlus is controlled directly over the VE.Bus MK2 protocol through an MK2/MK3-USB adapter at 2400 baud — no GX device. The protocol is spoken by `vebus.py` from [martiby/multiplus2](https://github.com/martiby/multiplus2) (Martin Steppuhn); the file is not part of the repository and is fetched during installation. If it is missing, the driver stays inactive. With `-v`, `vebus.py` reports through the Python `logging` module.

Startup sequence per device: open port → version probe (soft) → `init_address` (the real connectivity gate) → `scan_ess_assistant`, which walks the assistant RAM records from ID 128 to locate the ESS assistant and resolve the setpoint RAM-ID automatically (robust across models, not hard-coded to 131). If any step fails the driver stays inactive and the rest of zeroinput keeps running.

Each cycle the driver writes the ESS power setpoint via `CommandWriteViaID` (0x37) with flag `0x02` (**RAM only**, no EEPROM wear). The setpoint must be refreshed < 60 s; zeroinput's ~1 s loop guarantees this. All VE.Bus control points are RAM, so per-second writes are safe by design — this is the key difference from EEPROM-based hybrids.

**Sign convention.** The driver works in zeroinput/Soyosource terms: `set_power(positive) = feed-in`. Internally it negates to the Victron convention (positive = charge). `mk3_ess_sign: -1` flips the direction if the wiring/CT placement reports it reversed. `sleep()` writes 0 W (passthrough). `read_status()` returns `Pac` (feed-in positive), `Vbat`, `Ibat`, `soc`, `out_p` for monitoring.

---

## display_mppt_data

Header and data rows follow the column list `_MPPT_COLUMNS` (label and alignment per column). Before printing, all rows (chargers, `combined`, temperature sensors) are collected; `_mppt_render()` then hides every data column that is empty across all rows — `device` and `name` always stay visible. The `mode` column is additionally sized to the values actually present: narrow when only short values like `BULK` occur, wider as soon as `ABSORB`, `RECOND`, `EXTCON`, or `ErrorP` appears. The `Iload` column also counts as empty when it is only `OFF` or blank, since that carries no load information. Which columns are visible is recomputed on every call and thus follows the current device state.

REC output deferred via `_drain_rec_msgs()` helper.

- `%PVp` — `PPV / pvp × 100`; for `combined` uses sum of all device `pvp` values; empty if `pvp` not configured. The combined value is also stored as `mppt_data['combined']['PVperc']` and exportable as a vzlogger channel.
- `Iload` — Victron `LOAD=ON`: current A; `LOAD=OFF`: `OFF`; eSmart3: current if `Iload > 0`, else empty
- `mode` — Victron: OFF/FAULT/BULK/ABSORB/FLOAT/EQUAL/START/RECOND/EXTCON; eSmart3: WAIT/MPPT/BULK/FLOAT/PRE; both: `ErrorP` when `CS='PORT'`
- Unconfigured AGG devices: `<SER#>  <port_name>  UNCONFIGURED` (own format, printed after the table)
- REC messages (verbose) deferred to after `power request` line via `_rec_msgs` queue

---

## Predictor

The predictor's configuration keys (`load_prediction`, `min_spread_w`,
`predictor_log`) and its internal behaviour are fully documented in
**[predictor_spec.md](predictor_spec.md)**.

---

## Temperature alarms

Configured in the `alarms` conf block (separate from `chargers`), keyed by device name. Applies to eSmart3, EPever, Renogy and Morningstar devices (internal + external sensor) and AGG `temp_sensor` devices (external only).

Each sensor supports two independent alarms:
- **`int_hi` / `ext_hi`** — fires when `temp > threshold`
- **`int_lo` / `ext_lo`** — fires when `temp < threshold`

Each alarm has a `_cmd` (shell command) and `_interval` (repeat interval in seconds, default 300). An alarm is active only when both threshold and command are set. Thresholds may be negative or zero.

---

## Data logging

zeroinput writes its own values (feed-in power, zero-shift, battery voltage, PV power, temperatures) back to vzlogger via a file-based channel (`/tmp/vz/output_to_vz.log`). vzlogger picks these up and logs them to the volkszaehler.org database alongside all other channels, providing a unified view of the installation.

Channel mapping is defined in `vz_channels` (editable in the web interface).

---

## Configuration and hot-reload

`zeroinput.conf` is watched for changes each cycle. Most keys take effect immediately on save. Keys requiring restart: `chargers`, `inverters`, `vzlogger_log_file`, `persistent_vz_file`. The `chargers` and `inverters` blocks are structural — charger reader threads and inverter drivers are built once at startup, so changes need a restart (use the restart tab / `/api/restart`).

`predictor.py` is watched separately; changes (including configuration variables) are applied by reloading the module without restarting zeroinput.

---

## Web interface (`webconfig.py`)

HTTP server started with `-httpd`. Provides:
- **zeroinput.conf tab** — live editing of all hot-reloadable keys; path keys show restart-required notice. The `/api/conf` endpoint is structure-agnostic and edits the JSON in place, so the `chargers` and `inverters` blocks are editable here as raw values.
- **chargers tab** — structured editor for MPPT chargers and temperature sensors (eSmart3, Victron, Aggregator with SER# table, EPever, Renogy, Morningstar). Modbus types show a `unit` (slave address) field. Restart required on save. PVp field hidden for `type: temp` devices. Per-sensor heat-protection selection (only one valid).
- **inverters tab** — structured editor for feed-in inverters (type, port, stage checkboxes, count, max/min power, ESS sign). Validates for power coverage gaps before saving. Restart required on save.
- **alarms tab** — per-device temperature alarms (eSmart3: int\_hi/int\_lo/ext\_hi/ext\_lo; temp\_sensor: ext\_hi/ext\_lo). Device labels use `temp_display` name when set. Cards collapsed on load.
- **vz channels tab** — table editor for volkszaehler.org channel mapping
- **timer.txt tab** — text editor for discharge rules; shows notice when timer is disabled in conf
- **dirt shift tab** — editor for `dirt_shift.conf` (only visible when `dirt_shift/dirt_shift.conf` exists)
- **dirt shift log tab** — the last 24 hours of the dirt_shift log, read-only (only visible when `logfile` is set in `dirt_shift.conf`); **previous run** / **next run** jump to the start of a run, **latest** to the end
- **restart tab** — restarts the zeroinput and vzlogger services via button (one each; requires the matching sudoers entries). Shows a warning about the inverter output interruption.
- **status tab** — live HTML status page (only with `-web`)

The buttons **A−** / **A+** in the header set the text size of the status, timer.txt and dirt shift log views (50–400 % in steps of 25 %); the setting is stored per browser.

---

## Output modes

| Flag | Effect |
|---|---|
| `-v` | Verbose console output each cycle |
| `-web` | Write HTML status page (`zeroinput.html`) each cycle |
| `-httpd` | Start web configuration server |
| `-no-input` | Disable all feed-in |
| `-test-alarm` | Execute alarm command and exit |
