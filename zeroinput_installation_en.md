# zeroinput – Installation Guide
*v2.2*

## Overview

zeroinput is a Python script for PV zero feed-in (self-consumption optimisation) on a Raspberry Pi.
It reads the electricity meter via vzlogger and controls one or more battery grid-tie inverters over RS485 or USB — switching between power stages depending on load. Supported inverter types: Soyosource GTN limiter series and Victron MultiPlus (ESS via MK3-USB adapter).

zeroinput relies on **vzlogger** from the [Volkszähler](https://www.volkszaehler.org/) project in two ways:
- as the **meter data source** — vzlogger reads the electricity meter and streams the active power value (`1-0:16.7.0`) to zeroinput via a FIFO
- as the **data logger** — zeroinput writes its own values (feed-in power, battery voltage, PV power, …) back to vzlogger, which logs them alongside the other Volkszähler channels

zeroinput integrates naturally into an existing Volkszähler setup without replacing or duplicating it.

**Files:**

| File | Description |
|---|---|
| `zeroinput.py` | Main script |
| `input_power_staging.py` | Two-stage power distribution logic |
| `inverter_drivers.py` | Inverter driver abstraction (Soyosource, Victron MK3) |
| `charger_drivers.py` | MPPT charger drivers (eSmart3, Victron, EPever, Renogy, Morningstar) |
| `vebus.py` | VE.Bus MK2/MK3 protocol driver (only needed for Victron MultiPlus) |
| `predictor.py` | Load predictor (k-means, optional) |
| `webconfig.py` | HTTP configuration server (optional, requires `-httpd`) |
| [`ve_aggregator.py`](https://github.com/E-t0m/ve.direct-aggregator) | VE.Direct Aggregator client (optional, only for `victron_agg`) |
| `zeroinput.conf` | Configuration (JSON) — create from `zeroinput.conf.starter` |
| `zeroinput.conf.starter` | Reference configuration showing all available device classes |
| `zeroinput_webconfig.html` | Web interface |
| `zeroinput.service` | systemd service |
| `zerooutput.sh` | Live status in terminal (`/usr/local/bin/zerooutput.sh`) |
| `timer.txt` | Discharge timer (optional) |

---

## Requirements

### Hardware

- Raspberry Pi (tested with Pi 3/4)
- One or more inverters:
  - **Soyosource GTN limiter** (GTN-1000/1200/2000 with RS485 limiter port) — wired via USB RS485 adapter
  - **Victron MultiPlus / MultiPlus-II** (VE.Bus, 2nd-gen microprocessor) with MK2-USB or MK3-USB adapter — ESS assistant must be configured in VEConfigure
- MPPT charge controller: eSmart3 (RS485), Victron MPPT (VE.Direct or Aggregator), EPever Tracer-AN/-BN, Renogy Rover/Elite/Adventurer, or Morningstar TriStar MPPT (all RS485/Modbus RTU)
- RS485 adapter (USB) — wiring: **A+ to A+, B- to B-** on all devices
- Electricity meter with extended data output (request PIN from your grid operator)
  The OBIS dataset `1-0:16.7.0` (net active power) **must** be available
- Meter interface compatible with vzlogger (IR read head, Shelly 3EM/3PM, Modbus meter, …)

### Software

```bash
sudo apt install python3 python3-serial vzlogger
```

For Victron MultiPlus support, pyserial is already included above. No additional libraries required — `vebus.py` uses only the standard serial port.

---

## Installation

### 1. Copy files

```bash
sudo useradd -m -s /bin/bash vzlogger   # if not already existing
sudo usermod -aG dialout vzlogger       # RS485 and USB serial access
sudo cp zeroinput.py input_power_staging.py inverter_drivers.py charger_drivers.py /home/vzlogger/
sudo cp vebus.py /home/vzlogger/        # only needed for Victron MultiPlus
sudo cp predictor.py /home/vzlogger/    # optional
sudo cp webconfig.py /home/vzlogger/    # optional, only for -httpd
sudo cp ve_aggregator.py /home/vzlogger/ # optional, only for victron_agg
sudo cp zeroinput.conf zeroinput_webconfig.html zeroinput.service /home/vzlogger/
sudo cp timer.txt /home/vzlogger/       # optional
sudo chown vzlogger:vzlogger /home/vzlogger/*.py /home/vzlogger/*.conf /home/vzlogger/*.html
```

### 2. Persistent device names via udev (recommended)

To prevent ports from changing after a reboot, assign fixed device names via udev rules.
Create `/etc/udev/rules.d/99-zeroinput.rules`:

```
# IR read head (cp210x chip)
SUBSYSTEMS=="usb-serial", DRIVERS=="cp210x", SYMLINK+="lesekopf"
# RS485 adapter (ch341 chip)
SUBSYSTEMS=="usb-serial", DRIVERS=="ch341-uart", SYMLINK+="rs485"
# MK3-USB adapter (FTDI chip)
SUBSYSTEMS=="usb-serial", DRIVERS=="ftdi_sio", SYMLINK+="mk3usb"
```

With multiple identical adapters, distinguish by USB port:

```
SUBSYSTEMS=="usb", ATTRS{devpath}=="1.1", SYMLINK+="rs485a"
SUBSYSTEMS=="usb", ATTRS{devpath}=="1.3", SYMLINK+="rs485b"
```

Then: `sudo udevadm control --reload-rules && sudo udevadm trigger`

### 3. Create FIFO and directory

The FIFO must exist before both vzlogger **and** zeroinput start. Add the following lines to `/etc/systemd/system/vzlogger.service`:

```ini
[Service]
ExecStartPre=/bin/mkdir -p /tmp/vz
ExecStartPre=/bin/bash -c 'test -p /tmp/vz/vzlogger.fifo || mkfifo /tmp/vz/vzlogger.fifo'
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl restart vzlogger
```

To enable the **Restart** buttons in the web interface, add a sudoers entry. The web interface can restart both zeroinput and vzlogger, so permit both services:

```bash
sudo sh -c 'printf "vzlogger ALL=(root) NOPASSWD: /bin/systemctl restart zeroinput\nvzlogger ALL=(root) NOPASSWD: /bin/systemctl restart vzlogger\n" > /etc/sudoers.d/zeroinput'
sudo chmod 440 /etc/sudoers.d/zeroinput
```

### 4. Configure vzlogger

zeroinput generates a template `vzlogger.conf.example` on startup based on the current `zeroinput.conf`. It already contains the mandatory `1-0:16.7.0` channel and all `vz_channels` with UUID placeholders.

**Steps:**
1. Start zeroinput once — `vzlogger.conf.example` is created in the working directory
2. Create a channel in the Volkszähler interface for each entry and insert its UUID
3. Adjust `device` (read head path) and `middleware` URL
4. `sudo cp vzlogger.conf.example /etc/vzlogger.conf`
5. `sudo systemctl restart vzlogger`

The `1-0:16.7.0` channel (active power) **must** be present.

> **Note:** Depending on channel resolution, vzlogger may write large amounts of data. See [data volume management](https://wiki.volkszaehler.org/howto/datenmengen). zeroinput uses `verbosity: 15` only to receive meter values from the FIFO — surplus log entries are discarded.

### 5. Create zeroinput.conf

Copy `zeroinput.conf.starter` to `zeroinput.conf` and adapt it to your hardware. The starter file contains all available device classes with comments. **Delete the entries you do not need.**

zeroinput starts even without fully configured ports, so the web interface is available immediately. **Configure everything in the browser at `http://<hostname>:8081/`** — chargers, inverters, vz channels, alarms, and all other settings.

> **Important:** Edit `chargers` and `inverters` via the dedicated tabs in the web interface or directly in the conf file. These blocks require a restart after saving — use the **restart** tab.

Key settings:

| Key | Description |
|---|---|
| `chargers` | MPPT chargers and temperature sensors (port → device config). Structural — restart required. |
| `inverters` | Feed-in inverters (id → device config). Structural — restart required. |
| `max_input_power` | Maximum total feed-in power of all inverters combined (W). Set to 0 to disable feed-in. |
| `max_bat_discharge` | Maximum battery discharge power (W) |
| `single_inverter_threshold` | Demand level above which stage 2 activates (W) |
| `multi_inverter_wait` | Seconds before falling back to stage 1 |
| `cell_count` | Number of LiFePO4 cells in series (16 = 51.2 V, 15 = 48 V, 8 = 24/28 V). All battery voltage thresholds scale with this value. |
| `bat_voltage_const` | Voltage-correction factor under load (V/kW, 0 = disabled) |
| `free_power_export` | Free export at high battery voltage (true/false) |
| `heat_temp_low` | Heat protection: below this sensor temperature (°C), full power |
| `heat_temp_high` | Heat protection: at/above this sensor temperature (°C) the inverter is switched off, linear in between. Trigger sensor via `heat_protect` flag on a charger in the web interface. |
| `webconfig_port` | Web server port (default 8081, 0 = disabled) |
| `vz_channels` | Volkszähler channel mapping (editable in web interface) |
| `load_prediction` | Enable load predictor (true/false) |
| `discharge_timer` | Enable timer-based control (true/false) |

> **Important:** Device names (`name`) must be unique — zeroinput exits on startup if duplicates are found.

**Example chargers block:**

```json
"chargers": {
    "/dev/ttyACM0": {"name": "esmart 60",   "mppt_type": "eSmart3", "pvp": 2350, "temp_display": "out"},
    "/dev/ttyACM2": {"name": "VE 150/35",   "mppt_type": "victron", "pvp": 1720},
    "/dev/ttyACM6": {"name": "Tracer AN",   "mppt_type": "epever", "pvp": 1300, "unit": 1},
    "/dev/ttyACM7": {"name": "Rover Elite", "mppt_type": "renogy", "pvp": 800,  "unit": 1},
    "/dev/ttyACM8": {"name": "TriStar 60",  "mppt_type": "morningstar", "pvp": 3000, "unit": 1}
}
```

Charger configuration fields:

| Field | Description |
|---|---|
| `mppt_type` | `eSmart3`, `victron`, `victron_agg`, `temp_sensor`, `epever`, `renogy`, or `morningstar` |
| `pvp` | PV peak power (W) for the `%PVp` display |
| `unit` | Modbus slave address (epever/renogy/morningstar only, default 1) |
| `temp_display` | Label for the external temperature reading (eSmart3) |

Modbus charger notes: **epever** (Tracer-AN/-BN) runs at 115200 baud; **renogy** (Rover/Elite/Adventurer) and **morningstar** (TriStar MPPT 45/60) at 9600. The Morningstar **EIA-485 port exists only on the TS-MPPT-60/M** — the TS-MPPT-45 is RS-232 only and cannot share an RS485 bus. One Modbus charger per port: running several on one physical bus (multi-drop with distinct `unit` addresses) is not yet supported by the config — give each its own port/adapter.

**Example inverters block:**

```json
"inverters": {
    "base":  {"name": "soyo base", "type": "soyosource",  "port": "/dev/ttyACM3",
              "stage": [1,2], "count": 1, "max_power": 900, "min_power": 10},
    "mp2":   {"name": "MultiPlus II", "type": "victron_mk3", "port": "/dev/ttyUSB0",
              "stage": [2], "count": 1, "max_power": 2400, "min_power": 200}
}
```

Inverter configuration fields:

| Field | Description |
|---|---|
| `type` | `soyosource` or `victron_mk3` |
| `port` | Serial device path. One sender per port; use `count` for multiple identical units sharing a port. |
| `stage` | List of stages the unit runs in: `[1,2]` both, `[1]` base only, `[2]` stage 2 only. Empty list `[]` disables the unit without removing it. |
| `count` | Number of identical units sharing the port (they all receive one broadcast packet). |
| `max_power` | Maximum power per single unit (W). For GTN-2000 use 1600 W (battery mode). |
| `min_power` | Minimum useful power per single unit (W). Below this the unit sleeps. |
| `mk3_ess_sign` | `1` (default) or `-1` to flip feed-in direction (Victron MK3 only). |

A physically shared line (eSmart3 reading + Soyosource sending on the same wire) is expressed as two separate entries: one in `chargers` and one in `inverters` with the same `port`.

For Victron Aggregator (multiple MPPTs on one port):

```json
"chargers": {
    "/tmp/ttyVirtual": {
        "name": "AGG", "mppt_type": "victron_agg",
        "devices": {
            "HQ12345ABC": {"name": "VE 150/35", "pvp": 1500, "type": "mppt"},
            "TEMP-P2-S0": {"name": "Rack Temp",               "type": "temp"}
        }
    }
}
```

### 6. Install systemd service

Contents of `zeroinput.service`:

```ini
[Unit]
Description=zeroinput PV zero feed-in control
After=syslog.target network.target ntp.service vzlogger.service

[Service]
User=vzlogger
WorkingDirectory=/home/vzlogger
ExecStartPre=/bin/mkdir -p /tmp/vz
ExecStartPre=/bin/touch /tmp/vz/output_to_vz.log
ExecStart=/usr/bin/python3 -u /home/vzlogger/zeroinput.py -web -httpd
ExecStartPost=+/bin/bash -c 'test -f /tmp/vz/vzlogger_restarted || (sleep 3 && /bin/systemctl restart vzlogger.service && touch /tmp/vz/vzlogger_restarted)'
Restart=on-failure
RestartSec=15
Nice=-1
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

```bash
sudo cp zeroinput.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable zeroinput
sudo systemctl start zeroinput
```

Check status:

```bash
sudo systemctl status zeroinput
journalctl -u zeroinput -f
```

> **Note on output (`-v`):** The `ExecStart` line above starts zeroinput **without** `-v`. The `-v` option enables a verbose status output that writes several lines on every control cycle (about once per second). Because `StandardOutput=journal` is set, this output goes to the systemd journal and thus to syslog — in continuous operation this adds up to hundreds of thousands of lines per day and bloats the log. For continuous operation `-v` is therefore omitted; only startup, error and config-reload messages are logged. The live status is still available via the `-web` status page in the browser. `-v` is intended for a manual foreground start (see troubleshooting), when you want to read the output directly. If you do want the verbose output in the journal permanently, add `-v` and either cap the journal via `journald.conf` (e.g. `SystemMaxUse=`) or redirect the output to a separate rotated file with `StandardOutput=append:/var/log/zeroinput.log` plus a logrotate rule.

---

## Operation

### Command line options

| Option | Description |
|---|---|
| `-v` | Verbose status output to console (several lines per second). Intended for a manual foreground start; omit it in continuous systemd operation, otherwise the output bloats the journal/syslog. |
| `-web` | Write HTML status page (`zeroinput.html`) |
| `-httpd` | Start web configuration server (port from conf) |
| `-no-input` | Disable power feed-in |
| `-test-alarm` | Test alarm command and exit |

### Web interface

With `-httpd`, the web interface is available at `http://<hostname>:8081/`

Tabs:
- **zeroinput.conf** — Edit all hot-reloadable keys live. Structural keys (`chargers`, `inverters`) show a restart-required notice.
- **chargers** — Structured editor for MPPT chargers and temperature sensors (eSmart3, Victron, Aggregator with SER# table, EPever, Renogy, Morningstar). Modbus types show a `unit` (slave address) field. Restart required after saving.
- **inverters** — Structured editor for feed-in inverters (type, port, stage checkboxes, parallel units, max/min power, ESS sign). Validates for power coverage gaps before saving. Restart required.
- **alarms** — Per-device temperature alarms (eSmart3: int\_hi/int\_lo/ext\_hi/ext\_lo; temp sensor: ext\_hi/ext\_lo). Alarm commands should end with `&` to avoid blocking the control loop.
- **vz channels** — Volkszähler channel mapping as editable table
- **timer.txt** — Edit discharge timer rules
- **restart** — Restart zeroinput or vzlogger (`sudo systemctl restart <service>`); one button each. Requires the matching sudoers entries (see Installation).
- **status** — Live status page (only with `-web`)

### Hot-reload

Changes to `zeroinput.conf` are applied automatically when saved. Keys requiring restart:

- `chargers`, `inverters` (structural — drivers and reader threads are built once at startup)
- `vzlogger_log_file`, `persistent_vz_file`, `webconfig_port`

---

## Hardware notes

**Power stages.** zeroinput distributes feed-in demand across two stages. Stage 1 handles base load up to `single_inverter_threshold` using only stage-1 inverters. Stage 2 adds all stage-2 inverters sharing the load equally per unit — as demand grows, smaller units saturate first and the largest unit (e.g. MultiPlus) takes the remainder automatically. Feed-in is disabled via `max_input_power: 0` or the timer, not by leaving stage gaps.

**Coverage gap check.** At startup and on saving the inverters config, zeroinput checks that the configured inverters cover the full power range from 0 to `max_input_power` without gaps. A gap (e.g. stage-1 Soyo ending at 900 W while stage-2 MultiPlus has `min_power: 1500`) triggers an unmissable warning.

**Soyosource ramp rate.** The inverter ramps at 400 W/s. Large load steps cause brief import or export — this is normal.

**Soyosource GTN-2000.** Uses the same RS485 limiter protocol as the GTN-1000/1200. Use `max_power: 1600` (battery mode rating, not the higher solar-mode figure).

**Victron MultiPlus.** Requires an MK2-USB or MK3-USB adapter and the ESS assistant configured in VEConfigure (switch fully ON, not charger-only). No GX device required. The ESS power setpoint is written to RAM only — per-second writes are safe by design. If feed-in direction is reversed, set `mk3_ess_sign: -1`.

**RS485 bus.** Connect all devices: A+ to A+, B- to B-. Enable termination resistors on the last device if available.

**VE.Direct Aggregator.** Multiple Victron MPPTs can share a single serial port via an Arduino Mega 2560 or Teensy 4.1 running the [VE.Direct Aggregator](https://github.com/E-t0m/ve.direct-aggregator) firmware. VE.Direct is a 3.3 V UART; connection to the Pi is via USB-UART adapter. RS485 level converters can optionally be used on either or both sides for longer cable runs. `ve_aggregator.py` must be in the same directory as `zeroinput.py`.

---

## Load predictor

The load predictor (`load_prediction: true`, default: false) detects cyclic loads (washing machine, oven) and short high surges, stabilising feed-in to avoid over-feeding.

| Setting | Where | Description |
|---|---|---|
| `load_prediction` | conf | Master enable (default: false) |
| `min_spread_w` | conf | Minimum LOW/HIGH spread for k-means to engage (default: 150 W) |
| `predictor_log` | conf | Write `/tmp/predictor.log` (default: true) |
| `MAX_SPREAD_W` | `predictor.py` | Maximum spread; above this the load is not treated as cyclic |
| `PEAK_SHORT_MAX_N` | `predictor.py` | Short/long peak boundary in cycles |
| `LOG_FILE` | `predictor.py` | Log path (`''` = disabled) |

conf keys are hot-reloadable; constants in `predictor.py` are applied automatically on file save. Full behaviour documented in **[predictor_spec_en.md](predictor_spec_en.md)**.

---

## Volkszähler channels (vz_channels)

Format per entry: `[device, key, vz_channel, factor]`

- **device** — device name from `chargers`, `"combined"` for total PV, or `null` for direct variables
- **key** — data key (see below)
- **vz_channel** — UUID alias in vzlogger configuration
- **factor** — multiplier (e.g. `-1` to invert sign)

**Direct variables** (`device: null`): `power_demand`, `zero_shift`, `bat_voltage`

**combined**: `PPV`, `PVperc`, `Vbat`, `Ibat`, `Pload`

**eSmart3**: `PPV`, `VPV`, `Vbat`, `Ibat`, `Pload`, `int_temp`, `ext_temp`

**Victron MPPT**: `PPV`, `VPV`, `Vbat`, `Ibat`, `IL`

**Temperature sensor** (AGG, type: temp): `ext_temp`

---

## Discharge timer

With `discharge_timer: true` and a `timer.txt`. Format per line:

```
YYYY-MM-DD HH:MM:SS <battery> <inverter> <energy_Wh>
```

`0000-00-00` as date applies daily. Values > 100 = watts, ≤ 100 = percent of maximum.

Example:
```
0000-00-00 22:00:00   50   80   5000
0000-00-00 06:00:00   100  100  0
```

---

## Temperature alarms

Configured in `zeroinput.conf` under `alarms`, keyed by device name. An alarm activates automatically when both threshold and command are set.

```json
"alarms": {
    "esmart 60": {
        "int_hi": 60, "int_hi_cmd": "your_command &", "int_hi_interval": 300,
        "ext_hi": 55, "ext_hi_cmd": "your_command &"
    }
}
```

eSmart3 supports `int_hi`, `int_lo`, `ext_hi`, `ext_lo`. Temperature sensors support `ext_hi`, `ext_lo`. Commands should end with `&` to avoid blocking the control loop.

---

## Troubleshooting

**Follow live output:**
```bash
zerooutput.sh
# or:
tail -f /home/vzlogger/zeroinput.html
```

**systemd logs:**
```bash
journalctl -u zeroinput -f
journalctl -u zeroinput -n 100
```

**RS485 ports busy (`[Errno 16] Device or resource busy`):**
An old zeroinput instance is still running.
```bash
ps aux | grep zeroinput
crontab -u vzlogger -e   # remove any @reboot line
sudo reboot
```

**zeroinput does not start:**
```bash
journalctl -u zeroinput -n 50
python3 /home/vzlogger/zeroinput.py -v
```

**No FIFO:** `mkfifo /tmp/vz/vzlogger.fifo`

**No RS485 communication:**
```bash
ls -la /dev/ttyACM* /dev/ttyUSB*
sudo usermod -aG dialout vzlogger
```

**MultiPlus not responding:** Check that the MK3-USB adapter is connected and the ESS assistant is configured in VEConfigure. zeroinput logs "MK3 inactive" on startup if no response is received — the rest of zeroinput continues running normally.

**Modbus charger (EPever / Renogy / Morningstar) shows no data:** Verify the `unit` (Modbus slave address) matches the controller setting, the baud rate is correct (EPever 115200, Renogy/Morningstar 9600), and RS485 A/B are not swapped. The display shows `PORT ERROR` when a port read fails. For Morningstar, confirm the model has an EIA-485 port (TS-MPPT-60/M only).

**Web server not reachable:**
- Check `webconfig_port` in `zeroinput.conf`
- Start zeroinput with `-httpd`
- Check port conflict: `ss -tlnp | grep 8081`
