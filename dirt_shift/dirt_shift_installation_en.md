# dirt_shift – Installation guide

## Overview

`dirt_shift` shifts the battery discharge into the ecologically unfavourable night hours by writing the `timer.txt` that zeroinput reads for discharge control. It runs periodically (every quarter hour via cron), not as a permanent service.

The prerequisites are a working zeroinput installation with the discharge timer enabled and a reachable volkszähler with the `data.json` HTTP API. `dirt_shift` is an alternative to the price-driven tool `tib_zero_tas.py`: both write the same `timer.txt`, so they should not run at the same time. If you use a dynamic electricity tariff (e.g. Tibber), the price-driven variant is usually the better fit; `dirt_shift` targets installations without a dynamic tariff where the CO₂ balance is the control objective.

The detailed description of how it works is in `dirt_shift_spec_en.md`.

---

## Prerequisites

### Software

- Python 3 with the `requests` module (`pip3 install requests` or `apt install python3-requests`)
- a running zeroinput installation
- volkszähler with the `data.json` API enabled, reachable at `http://host:port/`

### On the zeroinput side

In `zeroinput.conf` the discharge timer must be active:

```json
"discharge_timer": true,
"discharge_t_file": "timer.txt",
```

`dirt_shift` reads the value `single_inverter_threshold` and the path `discharge_t_file` (resolved relative to `zeroinput.conf`) from `zeroinput.conf`. This guarantees `dirt_shift` writes exactly the file zeroinput reads. `zeroinput.conf` is only read, never changed.

---

## Installation

### 1. Copy the files

`dirt_shift` usually lives in a subfolder of the zeroinput installation:

```bash
cd /opt/zeroinput
mkdir -p dirt_shift
cp dirt_shift.py dirt_shift.conf dirt_shift/
chmod +x dirt_shift/dirt_shift.py
```

The default path to the parent `zeroinput.conf` is `../zeroinput.conf` and thus matches this folder structure. If `dirt_shift` lives elsewhere, adjust the `zeroinput_conf` key in `dirt_shift.conf` accordingly.

### 2. Adapt dirt_shift.conf

The supplied `dirt_shift.conf` contains placeholders that must be replaced:

```json
"zeroinput_conf": "../zeroinput.conf",
"vz_host_port": "192.168.1.10:8080",
"vz_chans": {
    "Inverter": "<UUID>",
    "Import":   "<UUID>",
    "Auto":     "<UUID>",
    "PV":       "<UUID>",
    "Vbat":     "<UUID>"
}
```

- `vz_host_port` — host and port of the volkszähler (data.json API)
- `vz_chans` — the channel UUIDs of your own installation

The remaining keys (`reserve_pct`, `build_reserve_after`, the `latitude`/`longitude` and CO₂-intensity parameters, `average_days`, `day_weights_pct`, the efficiencies, `max_days_empty_battery`) have usable defaults and can be left unchanged at first. `build_reserve_after` (default 13:30) sets from when the night reserve is protected; before that the battery discharges freely into whatever currently has high CO₂ intensity. The location (`latitude`/`longitude`, default ~centre of Germany) drives the solar-position calculation for the CO₂-intensity profile — the default is fine for rough control, but it can be set for your own region. `day_weights_pct` weights individual days of the average more heavily (chronological, index −1 = yesterday, index 0 = same weekday of the previous week); the length must match `average_days`, otherwise equal weighting is used.

### 3. Adapt the basic_load formula to your installation

`basic_load` is the actual house consumption. The default formula in `get_average` is:

```python
hours['basic_load'][i] = (hours['Import'][i] + abs(hours['Inverter'][i])
                     - hours['Auto'][i])
```

This formula reflects one particular installation and is **not a standard** — it must be adapted to your own installation. Only schedulable loads that should not be covered from the night budget are subtracted; demand-driven loads (e.g. an air conditioner) stay in the consumption. Absent channels are dropped, additional ones are added:

- without a separately metered wallbox the `Auto` term disappears
- a further separately metered load (e.g. a PV-battery charger) would come in as an additional subtraction term

What matters is that `basic_load` ends up as the actual house consumption to be covered. If a channel is removed from the formula, it can also be dropped from `vz_chans`.

### 4. Dry run to verify

Before activation, a run without writing the timer file is recommended:

```bash
cd /opt/zeroinput/dirt_shift
# temporarily set disable_zeroinput_timer to true in dirt_shift.conf
python3 dirt_shift.py -v
```

The verbose output (`-v`) shows sunrise/sunset, the current intensity zone (red/yellow/green), the night reserve, the energy content and the chosen discharge mode. `-debug` additionally prints the timer lines. `-avgnew` discards the cached 7-day average and fetches it anew.

If the values look plausible (intensity zone matching the time of day, reserve in the expected range), `disable_zeroinput_timer` can be set back to `false`.

### 5. Cron entry

`dirt_shift` should run every quarter hour:

```bash
crontab -e
```

```cron
*/15 * * * * cd /opt/zeroinput/dirt_shift && /usr/bin/python3 dirt_shift.py >/dev/null 2>&1
```

On each run the fresh energy content is fetched and `timer.txt` is rewritten with the upcoming slots. The 7-day average is served internally on an hourly basis from `dirt_avg_cache.json` and only refetched when needed — the quarter-hourly runs thus put little load on the volkszähler.

---

## Operation

### Command-line options

- `-v` — verbose console output
- `-html` — HTML header/footer around the output (for embedding in a web UI)
- `-debug` — more output, also shows the timer lines (implies `-v`)
- `-avgnew` — forces a fresh 7-day average instead of the cache
- `-h` — short help

### Interplay with zeroinput

`dirt_shift` only writes the `timer.txt`. The actual execution — discharge limit, PV pass-through, stage allocation — is done by zeroinput. Direct PV pass-through (`pvpt`) is guaranteed in every timer line with `ac 100%`; `dirt_shift` limits only the battery discharge.

### Error behaviour

On a hard error (volkszähler returns no complete days, energy content not computable), `dirt_shift` aborts, but first — if the timer path is known — writes an "all-allowed" line so zeroinput is not blocked by a stale limit:

```
0000-00-00 00:00:00 100 100 99999
```

If not even `zeroinput.conf` is readable (timer path unknown), only the abort with an error message remains. In both cases cron writes nothing to the log as long as `>/dev/null 2>&1` is set — for troubleshooting, remove that redirection temporarily or run `dirt_shift.py -v` by hand.

---

## Troubleshooting

**timer.txt is not written.** Check that `disable_zeroinput_timer` is `false` and that the `discharge_t_file` path read from `zeroinput.conf` is writable. A manual run with `-v` shows the resolved path.

**"cannot read zeroinput.conf".** The path in `zeroinput_conf` is wrong. It is resolved relative to the directory of `dirt_shift.py`.

**"no complete days returned by volkszähler".** The volkszähler has no complete days for the requested period. Only after a few days of operation does the average return sensible values. Until then the free-timer fallback applies.

**Intensity zone looks wrong.** The zone follows from the solar position (date + `latitude`/`longitude`) and the fixed bounds `green_earliest`/`green_latest`. With `-v` you can check sunrise/sunset and the computed zone. A wrongly set location or unsuitable bounds are the most common cause.
