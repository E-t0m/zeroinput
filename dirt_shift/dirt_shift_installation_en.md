# dirt_shift – Installation guide

## Overview

`dirt_shift` shifts battery discharge into the hours with the highest grid CO₂ intensity by writing the `timer.txt` that zeroinput reads for discharge control. It runs periodically (every quarter hour via cron), not as a permanent service.

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

`dirt_shift` reads only the path `discharge_t_file` (resolved relative to `zeroinput.conf`) and `cell_count` from `zeroinput.conf`. This guarantees `dirt_shift` writes exactly the file zeroinput reads. `zeroinput.conf` is only read, never changed. Every `dirt_shift`-specific parameter lives exclusively in `dirt_shift.conf`.

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

The remaining keys (`reserve_pct`, `latitude`/`longitude`, `average_days`, `day_weights_pct`, the efficiencies, `max_days_empty_battery`) have usable defaults and can be left unchanged at first.

- `reserve_pct` (default 90) determines what percentage of the computed red demand is held back as the red reserve.
- The location (`latitude`/`longitude`, default ~centre of Germany) drives the clear-sky model and the radiation forecast.
- `day_weights_pct` weights individual days of the average more heavily (chronological, index −1 = yesterday, index 0 = same weekday of the previous week); the length must match `average_days`, otherwise equal weighting is used.

**The cap `CAP_FACTOR` (default 2×) is not a configuration option.** It is a named constant in the code (`dirt_shift.py`) that caps discharge power in non-priority red hours to `CAP_FACTOR × basic_load` for that hour — deliberately not externally configurable, since it is a fixed safety margin, not an installation-specific value. To change it, edit the code directly.

**`precharge_enabled` (default `false`, optional/experimental):** enables an additional path that deliberately diverts PV surplus into the battery instead of the house during a single green hour (`pvpt` is throttled there) — the one exception to the otherwise unrestricted `pvpt` guarantee. Only engages when natural charging would not otherwise be enough and the round-trip loss is worth the avoided CO₂ cost. Details and the exact formula are in `dirt_shift_spec_en.md`, section "Precharge".

**SMARD is a prerequisite.** `dirt_shift` pulls real day-ahead grid data (Bundesnetzagentur, free, no registration) for today **and** tomorrow and derives the zones from it (a median cut: the cleaner half of the day is green, the dirtier half red); there is no alternative zone source. If the fetch fails, the cache substitutes for exactly **one** more day; beyond that `dirt_shift` aborts, leaving an all-allowed timer behind. Optionally, `vz_dirtiness_uuid` (a channel UUID created in volkszähler beforehand) can be set so `dirt_shift` logs the current dirtiness value to volkszähler via HTTP POST on every run (empty disables it).

The radiation forecast (Open-Meteo, `shortwave_radiation`, free, no registration) scales the empirical PV reference curve to the actual day's weather (today and tomorrow, no API key needed).

### 3. Adapt the basic_load formula to your installation

`basic_load` is the actual house consumption. The default formula in `get_average` is:

```python
hours['basic_load'][i] = (hours['Import'][i] + abs(hours['Inverter'][i])
                     - hours['Auto'][i])
```

This formula reflects one particular installation and must be adapted to your own installation. Only schedulable loads that should not be covered from the red reserve are subtracted (the car is charged deliberately, independent of the reserve calculation); demand-driven loads (e.g. an air conditioner) stay in the consumption. Absent channels are dropped, additional ones are added:

- without a separately metered wallbox the `Auto` term disappears
- a further separately metered load (e.g. a PV-battery charger) would come in as an additional subtraction term

What matters is that `basic_load` ends up as the actual house consumption to be covered. If a channel is removed from the formula, it can also be dropped from `vz_chans`.

**Important regarding the wallbox:** because `Auto` is deliberately excluded from `basic_load`, `dirt_shift` does not see wallbox charging directly. Protection against a wallbox spike draining the battery instead of the grid instead comes from the zone logic itself — see "Discharge by zone" in the spec (green stops categorically as long as the reserve has not been reached; red caps to `CAP_FACTOR × basic_load` outside the dirtiest hour).

### 4. Dry run to verify

Before activation, a run without writing the timer file is recommended:

```bash
cd /opt/zeroinput/dirt_shift
# temporarily set disable_zeroinput_timer to true in dirt_shift.conf
python3 dirt_shift.py -v -debug
```

The verbose output (`-v`) shows the current zone (red/green), the battery content, the red reserve, and — when the reserve is running short — the identified dirtiest hour in the window, along with the chosen discharge mode. `-debug` additionally shows the hourly overview table (PV reference curve, radiation forecast, clear-sky index, expected PV, `basic_load`, the charge/discharge tag, dirtiness, zone) and the written timer lines. `-avgnew` discards every cache (7-day average, PV curve, radiation forecast, SMARD) and refetches all of them.

If the values look plausible (zone matching the grid situation, reserve in the expected range), `disable_zeroinput_timer` can be set back to `false`.

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

On a hard error (volkszähler returns no complete days, energy content not computable, SMARD data neither fresh nor available as a one-day substitute), `dirt_shift` aborts, but first — if the timer path is known — writes an "all-allowed" line dated with the current date, so zeroinput is not blocked by a stale limit:

```
2026-07-09 00:00:00 100 100 99999
```

Because the line carries a real calendar date, the free state persists on its own once the day is over — zeroinput's timer parser, walking through every already-past line, ends up keeping exactly this one, without the file needing to be rewritten again.

If not even `zeroinput.conf` is readable (timer path unknown), only the abort with an error message remains. In both cases cron writes nothing to the log as long as `>/dev/null 2>&1` is set — for troubleshooting, remove that redirection temporarily or run `dirt_shift.py -v` by hand.

---

## Troubleshooting

**timer.txt is not written.** Check that `disable_zeroinput_timer` is `false` and that the `discharge_t_file` path read from `zeroinput.conf` is writable. A manual run with `-v` shows the resolved path.

**"cannot read zeroinput.conf".** The path in `zeroinput_conf` is wrong. It is resolved relative to the directory of `dirt_shift.py`.

**"no complete days returned by volkszähler".** The volkszähler has no complete days for the requested period. Only after a few days of operation does the average return sensible values. Until then the free-timer fallback applies.

**Aborts with "SMARD zone data unavailable".** The SMARD fetch failed and the cache is older than one day. `dirt_shift` leaves an all-allowed timer behind, so zeroinput keeps running unrestricted. Check the network connection and SMARD's reachability; a manual run with `-v` shows the reason.

**Zone looks wrong, or three zones (including "yellow") still show up.** Since the switch to the median cut, `dirt_shift` only knows two zones (red/green). If yellow hours still appear, that is a **stale cache file** (`dirt_smard_cache.json`) from a run before the switch — SMARD is only refetched once per hour. Fix: `-avgnew` forces an immediate refetch, or delete the cache file.

**Reserve is not protected as expected.** With `-debug`, the hourly overview table shows the actual `dirt%` and `zone` classification per hour, plus the `chg` column (`L`/`D`/`!D`/`!L`). The `red: content ... < reserve ... -> dirtiest hour HH:00` line in the `-v` output shows which hour currently counts as the dirtiest in the window. If that deviates noticeably from expectations, the usual cause is an ill-fitting `basic_load` formula (step 3), or a radiation forecast that doesn't match the actual weather (`-debug` table, `clr%` column).
