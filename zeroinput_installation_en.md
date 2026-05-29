# zeroinput – Installation Guide
*v2.0*

## Overview

zeroinput is a Python script for PV zero feed-in (self-consumption optimisation) on a Raspberry Pi.
It reads the electricity meter via vzlogger and controls one or more Soyosource Grid-Tie Inverters over RS485 – switching between single-inverter and full operation depending on load.

zeroinput relies on **vzlogger** from the [Volkszähler](https://www.volkszaehler.org/) project in two ways:
- as the **meter data source** — vzlogger reads the electricity meter and streams the active power value (`1-0:16.7.0`) to zeroinput via a FIFO
- as the **data logger** — zeroinput writes its own values (feed-in power, battery voltage, PV power, …) back to vzlogger, which logs them alongside the other Volkszähler channels already present in your installation

zeroinput therefore integrates naturally into an existing Volkszähler setup without replacing or duplicating it.

**Files:**

| File | Description |
|---|---|
| `zeroinput.py` | Main script |
| `predictor.py` | Load predictor (k-means, optional) |
| `webconfig.py` | HTTP configuration server (optional, requires `-httpd`) |
| `ve_aggregator.py` | VE.Direct Aggregator client (optional, required for `victron_agg`) |
| `zeroinput.conf` | Configuration (JSON) |
| `zeroinput_webconfig.html` | Web interface |
| `zeroinput.service` | systemd service |
| `zerooutput.sh` | Live status in terminal (`/usr/local/bin/zerooutput.sh`) |
| `timer.txt` | Discharge timer (optional) |

---

## Requirements

### Hardware

- Raspberry Pi (tested with Pi 3/4)
- Soyosource GTI inverter(s) with RS485 connection
- eSmart3 or Victron MPPT charge controller with RS485/VE.Direct
- RS485 adapter (USB) – wiring: **A+ to A+, B- to B-** on all devices
- Electricity meter with extended data output (request PIN from your grid operator)
  The OBIS dataset `1-0:16.7.0` (net active power) **must** be available – without this value control is not possible!
- Meter interface – compatible with anything vzlogger can read, e.g.:
  - IR read head (e.g. Hichi USB)
  - Shelly 3EM / 3PM
  - Modbus meter
  - Any device providing OBIS `1-0:16.7.0` (active power)

### Software

```bash
sudo apt install python3 python3-serial vzlogger
```

---

## Installation

### 1. Copy files

```bash
sudo useradd -m -s /bin/bash vzlogger   # if not already existing
sudo usermod -aG dialout vzlogger          # RS485 access
sudo cp zeroinput.py /home/vzlogger/
sudo cp predictor.py /home/vzlogger/      # optional
sudo cp webconfig.py /home/vzlogger/      # optional, only for -httpd
sudo cp ve_aggregator.py /home/vzlogger/  # optional, only for victron_agg
sudo cp zeroinput.conf zeroinput_webconfig.html zeroinput.service /home/vzlogger/
sudo cp timer.txt /home/vzlogger/        # optional
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
```

With multiple identical adapters, distinguish by USB port:

```
SUBSYSTEMS=="usb", ATTRS{devpath}=="1.1", SYMLINK+="rs485a"
SUBSYSTEMS=="usb", ATTRS{devpath}=="1.3", SYMLINK+="rs485b"
```

Then: `sudo udevadm control --reload-rules && sudo udevadm trigger`

### 3. Create FIFO and directory

The FIFO must exist before both vzlogger **and** zeroinput start. The cleanest solution is to create it in the vzlogger.service unit.

Add the following lines to `/etc/systemd/system/vzlogger.service`:

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

This ensures vzlogger creates the FIFO on startup and zeroinput finds it ready.

### 4. Configure vzlogger

> **Tip:** Once zeroinput is running with `-httpd`, most settings — including RS485 ports, vz channels, and the discharge timer — can be edited comfortably in the browser at `http://<hostname>:8081/`. Direct file editing is only required for the initial setup or for keys that need a restart (see section 5).

zeroinput automatically generates a template `vzlogger.conf.example` in the same directory as `zeroinput.py` on startup, based on the current `zeroinput.conf`. It already contains:

- The mandatory `1-0:16.7.0` channel with UUID placeholder
- All configured `vz_channels` with their own UUID placeholders
- The correct FIFO path

**Steps:**
1. Start zeroinput once – `vzlogger.conf.example` is created in the directory of `zeroinput.py`
2. Open the file, create a channel in the Volkszähler interface for each entry and insert the UUID
3. Adjust `device` (read head path) and `middleware` URL
4. Copy as `/etc/vzlogger.conf`: `sudo cp vzlogger.conf.example /etc/vzlogger.conf`
5. `sudo systemctl restart vzlogger`

> The file is rewritten on every zeroinput start and automatically updated when `vz_channels` are changed in the web interface.

The `1-0:16.7.0` channel (active power) **must** be present – without this value control is not possible.

> **Note:** Depending on the resolution of configured channels, vzlogger may write large amounts of data to the database. See [data volume management](https://wiki.volkszaehler.org/howto/datenmengen) to avoid database overflow. zeroinput uses `verbosity: 15` only to receive all meter values from the FIFO – surplus log entries are discarded.

### 5. Adjust zeroinput.conf

zeroinput starts even with unconfigured RS485 ports, so the web interface is available immediately after the service starts. **Configure everything in the browser at `http://<hostname>:8081/`** — including RS485 ports, vz channels, and all other settings.

The only key that must be set before the first start is `webconfig_port` (default: `8081`). It is already set in the provided `zeroinput.conf`.

Direct file editing is a fallback if the web interface is not reachable:

```bash
nano /home/vzlogger/zeroinput.conf
```

Key settings:

| Key | Description |
|---|---|
| `rs485` | RS485 ports and device names |
| `basic_load_inverter_port` | Port of the base load inverter |
| `total_number_of_inverters` | Total number of inverters across all ports. In multi-inverter mode, zeroinput divides the demand by this value and sends that fraction to each RS485 port. Multiple inverters wired in parallel on one port all respond to the same packet and each feed that fraction – so they effectively multiply it. **If this value does not match the actual number of physical inverters, the total feed-in power will be wrong.** Hot-reloadable. |
| `max_input_power` | Maximum total power of all inverters (W) |
| `max_bat_discharge` | Maximum battery discharge power (W) |
| `webconfig_port` | Web server port (0 = disabled) |
| `vz_channels` | Volkszähler channel mapping (editable in web interface) |

> **Important:** Device names (`name`) must be unique – zeroinput will exit on startup if duplicates are found.

**Example rs485 configuration:**

```json
"rs485": {
    "/dev/ttyACM0": {
        "name": "esmart 60",
        "mppt_type": "eSmart3",
        "pvp": 900,
        "inverter": "soyosource",
        "temp_display": "out",
        "alarm": {
            "temp_int": 45, "int_cmd": "mpg321 /home/vzlogger/voice/alarm.mp3 &", "int_interval": 300,
            "temp_ext": 35, "ext_cmd": "",                                          "ext_interval": 300
        }
    },
    "/dev/ttyACM1": {
        "name": "esmart 40",
        "mppt_type": "eSmart3",
        "inverter": "soyosource",
        "temp_display": "bat",
        "alarm": {
            "temp_int": 50, "int_cmd": "mpg321 /home/vzlogger/voice/alarm.mp3 &", "int_interval": 300,
            "temp_ext": 40, "ext_cmd": "./alarm_akku.sh &",                        "ext_interval": 300
        }
    },
    "/dev/ttyACM2": {"name": "VE 150/35", "mppt_type": "victron"},
    "/dev/ttyACM3": {"name": "soyo",       "inverter": "soyosource"}
}
```

For a single Victron MPPT on its own port:

```json
"/dev/ttyACM2": {"name": "VE 150/35", "mppt_type": "victron", "pvp": 1500}
```

For multiple Victron MPPTs on a single port via the VE.Direct Aggregator (`readtext_sendhex` firmware), use `victron_agg` with a `devices` map of SER# → `{name, pvp}`:

```json
"/dev/ttyACM2": {
    "mppt_type": "victron_agg",
    "devices": {
        "HQ12345ABC": {"name": "VE 150/35", "pvp": 1500, "type": "mppt"},
                "TEMP-P2-S0":  {"name": "Rack Temp", "type": "temp"},
        "HQ67890DEF": {"name": "VE 75/15",  "pvp":  800}
    }
}
```

`pvp` (PV peak power in W) is informational. `ve_aggregator.py` must be present in the same directory as `zeroinput.py`. Requires `readtext_sendhex` firmware on the Arduino/Teensy aggregator.

An alarm fires only when both threshold and command are set. To disable a single alarm, leave the command empty.

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
ExecStart=/usr/bin/python3 -u /home/vzlogger/zeroinput.py -v -web -httpd
ExecStartPost=+/bin/bash -c 'test -f /tmp/vz/vzlogger_restarted || (sleep 3 && /bin/systemctl restart vzlogger.service && touch /tmp/vz/vzlogger_restarted)'
Restart=on-failure
RestartSec=15
Nice=-1
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

> `ExecStartPost` uses `+` to restart vzlogger with root privileges – so vzlogger finds `/tmp/vz/output_to_vz.log` after zeroinput has started.

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

---

## Operation

### Command line options

| Option | Description |
|---|---|
| `-v` | Verbose output to console |
| `-web` | Write HTML status page (`/home/vzlogger/zeroinput.html`) |
| `-httpd` | Start web configuration server (port from conf) |
| `-no-input` | Disable power feed-in |
| `-test-alarm` | Test alarm command and exit |

### Web interface

With `-httpd`, the web interface is available at:

```
http://<hostname>:8081/
```

Tabs:
- **zeroinput.conf** – Edit configuration live (locked fields require restart)
- **rs485** – RS485 port and device configuration including Victron AGG (SER# mapping, pvp) and eSmart3 alarms (requires restart after saving)
- **vz channels** – Volkszähler channel mapping as editable table
- **timer.txt** – Edit discharge timer
- **status** – Live status page (only with `-web`)

### Hot-reload

Changes to `zeroinput.conf` are applied automatically when the file is saved – no restart required.

Exceptions (require restart):

- `rs485`
- `basic_load_inverter_port`
- `webconfig_port`
- `vzlogger_log_file`
- `persistent_vz_file`

---

## Hardware notes

**Soyosource ramp rate:** The inverter ramps up and down at 400W/s. With 3 inverters that is 1200W/s. Large load steps (oven, washing machine) will therefore cause brief import or export – this is normal.

**Parallel inverters on one port:** Multiple Soyosource inverters can be wired in parallel on a single RS485 port – all receive the same demand packet and each feeds that amount independently. zeroinput sends `power_demand / total_number_of_inverters` to every port. If two inverters share one port, both respond to the same packet and together feed twice the sent value. `total_number_of_inverters` must therefore equal the total number of physical inverter units, regardless of how they are distributed across ports. If the value is wrong, the total feed-in is proportionally wrong. Adjustable live in the web interface without restart.

**RS485 bus:** Connect all devices on the same bus: A+ to A+, B- to B-. Enable termination resistors on the last device if available.

**VE.Direct Aggregator:** Multiple Victron MPPTs can share a single RS485 port via an Arduino Mega 2560 or Teensy 4.1 running the `readtext_sendhex` firmware. zeroinput uses `ve_aggregator.py` to read data and send charge power limits (`SET <SER#> <watts>`). Each device is identified by its SER# — configure using `mppt_type: victron_agg` in the **rs485** tab of the web interface. Multiple aggregator ports are supported.

---

## Load predictor

The load predictor (`load_prediction: true` in conf, default: false) detects cyclic loads (washing machine, oven etc.) using k-means and stabilises feed-in at the LOW level. The motor draws its additional power directly from the grid – without over-feeding.

Predictor settings are configured directly in `predictor.py` as module-level variables at the top of the file – no restart required, changes are picked up automatically on file save:

| Variable | Description |
|---|---|
| `MIN_SPREAD_W` | Minimum spread LOW/HIGH in W (default: 150) |
| `STARTUP_S` | Observation time before first action (default: 10 s) |
| `SHORT_PEAK_MAX` | Maximum duration of a short cyclic peak in s (default: 8) |
| `LOG_FILE` | Path to predictor log file (`''` = disabled) |

---

## Volkszähler channels (vz_channels)

The channel mapping is configured in `zeroinput.conf` under `vz_channels` and is editable in the web interface under **vz channels**.

Format per entry: `[device, key, vz_channel, factor]`

- **device** – Device name from `rs485` (e.g. `"esmart 60"`), `"combined"` for total PV, or `null` for direct variables
- **key** – Data key of the device (see below)
- **vz_channel** – UUID alias in the Volkszähler configuration
- **factor** – Multiplier (e.g. `-1` to invert sign)

### Available keys

**Direct variables** (`device: null`):

| Key | Description |
|---|---|
| `power_demand` | Total feed-in power (W) |
| `zero_shift` | Zero point offset (W) |
| `bat_voltage` | Corrected battery voltage (V) |

**combined** (sum of all MPPTs):

| Key | Description |
|---|---|
| `PPV` | Total PV power (W) |
| `Vbat` | Average battery voltage (V) |
| `Ibat` | Total battery current (A) |
| `Pload` | Total load power (W) |

**eSmart3:**

| Key | Description |
|---|---|
| `PPV` | PV power (W) |
| `VPV` | PV voltage (V) |
| `IPV` | PV current (A) |
| `Vbat` | Battery voltage (V) |
| `Ibat` | Battery current (A) |
| `Pload` | Load power (W) |
| `int_temp` | Internal temperature (°C) |
| `ext_temp` | External temperature (°C) |

**Victron MPPT:**

| Key | Description |
|---|---|
| `PPV` | PV power (W) |
| `VPV` | PV voltage (V) |
| `Vbat` | Battery voltage (V) |
| `Ibat` | Battery current (A) |

---

## Discharge timer

With `discharge_timer: true` and a `timer.txt`, feed-in can be time-controlled. Format per line:

```
YYYY-MM-DD HH:MM:SS <battery> <inverter> <energy_Wh>
```

`0000-00-00` as date is automatically replaced by the current date – the rule is therefore executed daily.

Battery and inverter values are interpreted as:
- **> 100** → Watts (absolute power)
- **≤ 100** → Percentage of configured maximum power

Example:
```
# date       time     battery  inverter  energy_Wh
0000-00-00 22:00:00   50       80        5000
0000-00-00 06:00:00   100      100       0
```
From 22:00: battery max 50%, inverter max 80%, until 5000 Wh discharged.
From 06:00: full operation.

---

## Temperature alarm

Each eSmart3 charge controller can trigger temperature alarms. An alarm is active automatically when both a threshold and a command are configured for it – no global enable flag required. Configure per device in `rs485`:

```json
"alarm": {
    "temp_int": 45,
    "int_cmd": "mpg321 /home/vzlogger/voice/regler.mp3 &",
    "int_interval": 300,
    "temp_ext": 35,
    "ext_cmd": "echo heat outside &",
    "ext_interval": 300
}
```

An alarm is triggered when the measured temperature exceeds the threshold **and** the corresponding command is non-empty. To disable an individual alarm, clear the command or set the threshold to 0.

---

## Troubleshooting

**Follow live output:**
```bash
tail -f /home/vzlogger/zeroinput.html
```

Or filtered without HTML tags, with automatic clear on each write cycle – available as the convenience script `zerooutput.sh`:
```bash
zerooutput.sh
```

Which runs:
```bash
watch -n1 -t 'grep -v "^<!DOCTYPE\|^<html\|^<head\|^<meta\|^<style\|^<body\|^<pre\|^</" /home/vzlogger/zeroinput.html'
```

Or in the browser via the Volkszähler web server – create a symlink once:
```bash
ln -s /home/vzlogger/zeroinput.html /home/pi/volkszaehler.org/htdocs/zeroinput.html
```
Then available at `http://<hostname>/zeroinput.html`

**systemd logs:**
```bash
journalctl -u zeroinput -f
journalctl -u zeroinput -n 100
```

**RS485 ports busy (`[Errno 16] Device or resource busy`):**
An old zeroinput instance is still running (e.g. from a `@reboot` crontab entry).
```bash
ps aux | grep zeroinput        # check running instances
crontab -u vzlogger -e         # remove @reboot line
sudo reboot
```

**zeroinput does not start:**
```bash
journalctl -u zeroinput -n 50
python3 /home/vzlogger/zeroinput.py -v   # start manually
```

**No FIFO:**
```bash
mkfifo /tmp/vz/vzlogger.fifo
```

**No RS485 communication:**
```bash
ls -la /dev/ttyACM*
sudo usermod -aG dialout vzlogger
```

**Web server not reachable:**
- Check `webconfig_port` in `zeroinput.conf`
- Start zeroinput with `-httpd`
- Check port conflict: `ss -tlnp | grep 8081`
