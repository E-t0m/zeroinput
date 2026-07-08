# dirt_shift – Installation guide

## Overview

`dirt_shift` shifts the battery discharge into the hours with the highest grid CO₂ intensity by writing the `timer.txt` that zeroinput reads for discharge control. It runs periodically (every quarter hour via cron), not as a permanent service.

The prerequisites are a working zeroinput installation with the discharge timer enabled and a reachable volkszähler with the `data.json` HTTP API.

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

`dirt_shift` reads only the path `discharge_t_file` (resolved relative to `zeroinput.conf`) and `cell_count` from `zeroinput.conf`. This guarantees `dirt_shift` writes exactly the file zeroinput reads. `zeroinput.conf` is only read, never changed. Every `dirt_shift`-specific parameter, including the discharge cap `yellow_cap`, lives exclusively in `dirt_shift.conf`.

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

- `vz_host_port` — host and port of the volkszähler (`data.json` API)
- `vz_chans` — the channel UUIDs of your own installation

The remaining keys (`reserve_pct`, `build_reserve_after`, the `latitude`/`longitude` and CO₂-intensity parameters, `yellow_cap`, `average_days`, `day_weights_pct`, the efficiencies, `max_days_empty_battery`) have usable defaults and can be left unchanged at first.

- `build_reserve_after` (default 13:30) sets a fixed clock time at which the red reserve is protected, used when no PV forecast can be obtained at all. When a PV forecast is available, an ongoing energy-balance projection (current battery content plus expected PV yield minus expected consumption, up to the next PV-surplus hour — the same window the red reserve itself is computed over) decides when protection kicks in — independent of the clock.
- `yellow_cap` (default 600 W) caps discharge power in the yellow transition zone, so short-lived load spikes cannot eat into the red reserve. A fixed, deliberately configured value — not one derived from forecast data.
- The location (`latitude`/`longitude`, default ~centre of Germany) drives the solar-position calculation, which serves as the fallback profile and is also used whenever SMARD is enabled but momentarily unavailable.
- `day_weights_pct` weights individual days of the average more heavily (chronological, index −1 = yesterday, index 0 = same weekday of the previous week); the length must match `average_days`, otherwise equal weighting is used.

**Optional: SMARD.** With `"smard_enabled": true`, `dirt_shift` pulls real day-ahead grid data (Bundesnetzagentur, free, no registration) for today **and** tomorrow and derives the zones from it. If SMARD's data for a given hour is not (yet) available, that hour uses the solar-position calculation; the run continues normally. Additionally, `vz_dirtiness_uuid` (a channel UUID created in volkszähler beforehand) can be set so `dirt_shift` logs the current dirtiness value to volkszähler via HTTP POST on every run (empty disables it).

The radiation forecast (Open-Meteo, `shortwave_radiation`, free, no registration) always runs independently of `smard_enabled` and scales the empirical PV reference curve to the actual day's weather (today and tomorrow, likewise free, no API key needed).

### 3. Adapt the basic_load formula to your installation

`basic_load` is the actual house consumption. The default formula in `get_average` is:

```python
hours['basic_load'][i] = (hours['Import'][i] + abs(hours['Inverter'][i])
                     - hours['Auto'][i])
```

This formula reflects one particular installation and must be adapted to your own installation. Only schedulable loads that should not be covered from the red reserve are subtracted; demand-driven loads (e.g. an air conditioner) stay in the consumption. Absent channels are dropped, additional ones are added:

- without a separately metered wallbox the `Auto` term disappears
- a further separately metered load (e.g. a PV-battery charger) would come in as an additional subtraction term

What matters is that `basic_load` ends up as the actual house consumption to be covered. If a channel is removed from the formula, it can also be dropped from `vz_chans`.

### 4. Dry run to verify

Before activation, a run without writing the timer file is recommended:

```bash
cd /opt/zeroinput/dirt_shift
# temporarily set disable_zeroinput_timer to true in dirt_shift.conf
python3 dirt_shift.py -v -debug
```

The verbose output (`-v`) shows sunrise/sunset, the current intensity zone (red/yellow/green), the red reserve along with its predicted build-up time (`>HH:MM`), the energy content, and the chosen discharge mode. `-debug` additionally shows the hourly overview table (PV reference curve, radiation forecast, clear-sky index, expected PV, dirtiness, zone) and the written timer lines. `-avgnew` discards every cache (7-day average, PV curve, radiation forecast, SMARD) and refetches all of them.

If the values look plausible (intensity zone matching the time of day, reserve in the expected range), `disable_zeroinput_timer` can be set back to `false`.

### 5. Cron entry

`dirt_shift` should run every quarter hour, **on** the quarter-hour marks (`0,15,30,45`), not shortly before them:

```bash
crontab -e
```

```cron
0,15,30,45 * * * * cd /opt/zeroinput/dirt_shift && /usr/bin/python3 dirt_shift.py >/dev/null 2>&1
```

**Why these exact minutes and not e.g. `59,14,29,44`:** `dirt_shift` always rounds the current slot **down** to the running quarter hour when writing the timer line (`now.minute // 15 * 15`). A run one minute before the mark still falls inside the **old** slot and writes its (stale) policy — the new slot would then only get its correct entry 14 minutes after it actually began. With `0,15,30,45`, each run starts exactly at the slot boundary, so the delay shrinks to plain cron dispatch jitter (seconds).

On each run the fresh energy content is fetched and `timer.txt` is rewritten with the current slot plus a 30-minute failsafe, each dated with the real calendar date. The other caches (7-day average, PV curve, radiation forecast, SMARD) are each served hourly internally and only refetched when needed — the quarter-hourly runs therefore put little load on volkszähler, Open-Meteo, and SMARD.

---

## Operation

### Command-line options

- `-v` — verbose console output
- `-html` — HTML header/footer around the output (for embedding in a web UI)
- `-debug` — more output, also shows the hourly overview table and the timer lines (implies `-v`)
- `-avgnew` — forces a fresh fetch of every cache (7-day average, PV curve, radiation forecast, SMARD zones)
- `-h` — short help

### Interplay with zeroinput

`dirt_shift` only writes the `timer.txt`. The actual execution — discharge limit, PV pass-through, stage allocation — is done by zeroinput. Direct PV pass-through (`pvpt`) is guaranteed in every timer line with `ac 100%`; `dirt_shift` limits only the battery discharge.

### Error behaviour

On a hard error (volkszähler returns no complete days, energy content not computable), `dirt_shift` aborts, but first — if the timer path is known — writes an "all-allowed" line dated with the current date, so zeroinput is not blocked by a stale limit:

```
2026-07-08 00:00:00 100 100 99999
```

Because the line carries a real calendar date, the free state persists on its own once the day is over — zeroinput's timer parser, walking through every already-past line, ends up keeping exactly this one, without the file needing to be rewritten again.

If not even `zeroinput.conf` is readable (timer path unknown), only the abort with an error message remains. In both cases cron writes nothing to the log as long as `>/dev/null 2>&1` is set — for troubleshooting, remove that redirection temporarily or run `dirt_shift.py -v` by hand.

---

## Troubleshooting

**timer.txt is not written.** Check that `disable_zeroinput_timer` is `false` and that the `discharge_t_file` path read from `zeroinput.conf` is writable. A manual run with `-v` shows the resolved path.

**"cannot read zeroinput.conf".** The path in `zeroinput_conf` is wrong. It is resolved relative to the directory of `dirt_shift.py`.

**"no complete days returned by volkszähler".** The volkszähler has no complete days for the requested period. Only after a few days of operation does the average return sensible values. Until then the free-timer fallback applies.

**Intensity zone looks wrong (sun-position fallback).** Without SMARD (or when SMARD momentarily fails), the zone follows from the solar position (date + `latitude`/`longitude`) and the fixed bounds `green_earliest`/`green_latest`. With `-v` you can check sunrise/sunset and the computed zone. A wrongly set location or unsuitable bounds are the most common cause.

**Intensity zone looks wrong (SMARD active).** With `-debug`, the hourly overview table shows the actual `dirt%` classification per hour. SMARD's percentile split follows the real shape of the day, so the red phase can fall at any time of day, not only at night.

**Reserve is protected too early or too late.** The `forecast: content ... reserve target (>HH:MM)` line in the `-v` output shows the predicted hour at which protection is expected to kick in. If that deviates noticeably from expectations, the usual cause is an ill-fitting `basic_load` formula (step 3), or a radiation forecast that doesn't match the actual weather (`-debug` table, `clr%` column).
