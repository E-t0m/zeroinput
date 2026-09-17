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

The remaining keys (`reserve_pct`, `limit_discharge_rate`, `latitude`/`longitude`, `average_days`, `day_weights_pct`, the efficiencies, `max_days_empty_battery`) have usable defaults and can be left unchanged at first.

- `reserve_pct` (default 90) determines what percentage of the computed red demand is held back as the red reserve — and scales the quarter-hour energy budget of non-priority red hours (below), and the reserve the wallbox control checks against (the same formula), in the same proportion.
- The location (`latitude`/`longitude`, default ~centre of Germany) drives the clear-sky model and the radiation forecast.
- `day_weights_pct` weights individual days of the average more heavily (chronological, index −1 = yesterday, index 0 = same weekday of the previous week); the length must match `average_days`, otherwise equal weighting is used.

**The cap in a non-priority red hour consists of two limits that apply at once.** A power cap, `limit_discharge_rate` (Watt) in `dirt_shift.conf` — installation-specific, adapt to your own ordinary peak load; set very high, it is effectively no longer in effect. And a fixed quarter-hour energy budget of `1/4 × (min(100 %, reserve_pct) × basic_load − measured PV)` for that hour (floored at 0), which needs no configuration entry of its own since it follows directly from `basic_load`, `reserve_pct`, and the measured PV power (the `PV` channel over the last 15 minutes, not the forecast's hourly mean — too coarse on the ramp hours at each end of the day; the forecast serves only as the fallback): `reserve_pct` scales the same share of demand here as in the red reserve itself, rather than letting a non-priority hour draw its full demand — capped at 100% here, though, since a cap above the actual net demand would no longer restrict anything (for the reserve itself, values above 100 are meaningful and act more cautiously); the measured PV is subtracted in full regardless, since `pvpt` already passes it through directly and it does not also need to come from the battery. Together the two caps close the gap the other leaves open: the Watt cap alone would let a load through unrestricted as long as it stays permanently below the threshold; the Wh budget alone would still let a brief spike through before it is exhausted. Details are in `dirt_shift_spec_en.md`, section "Output: timer.txt".

**`precharge_enabled` (default `false`, optional):** enables an additional path that deliberately diverts PV surplus into the battery instead of the house (`pvpt` is throttled there) — the one exception to the otherwise unrestricted `pvpt` guarantee. It is the mirror of the discharge logic: just as discharge is shifted into dirty hours, charging is pulled into the **very cleanest** hours. A green hour is throttled if it ranks among the cleanest `precharge_cleanest_pct` % of the window (default 25), is at least 10 `dirt%` points cleaner than the coming red block, **and** the expected surplus exceeds the battery's free room (`battery_capacity_wh − content`) — i.e. only when surplus would otherwise be lost. Several very clean hours throttle together; as content grows, the throttling tapers off on its own. Requires `battery_capacity_wh` (usable capacity in Wh). Details and the exact formula are in `dirt_shift_spec_en.md`, section "Precharge".

**SMARD is a prerequisite.** `dirt_shift` pulls real day-ahead grid data (Bundesnetzagentur, free, no registration) for today **and** tomorrow and derives the zones from it (a median cut over the rolling 24-hour window: the cleaner half green, the dirtier half red); there is no alternative source of *real* grid data (an outage triggers a modelled emergency mode instead, see below). If the fetch fails, the cache substitutes for exactly **one** more day; beyond that `dirt_shift` switches to an **emergency mode** that models the zones from the weather forecast (solar plus wind via Open-Meteo) and a fixed nationwide grid-load profile — clearly flagged, never mistaken for real data. Only if the weather cannot be fetched either does `dirt_shift` leave an all-allowed timer behind. Optionally, `vz_dirtiness_uuid` (a channel UUID created in volkszähler beforehand) can be set so `dirt_shift` logs the current dirtiness value to volkszähler via HTTP POST on every run (empty disables it).

The radiation forecast (Open-Meteo, `shortwave_radiation`, free, no registration) scales the empirical PV reference curve to the actual day's weather (today and tomorrow, no API key needed). The same call also fetches and caches `wind_speed_100m`, which only the emergency mode needs — so it adds no extra network request.

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

**Important regarding the wallbox:** because `Auto` is deliberately excluded from `basic_load`, `dirt_shift` does not see wallbox charging directly. Protection against a wallbox spike draining the battery instead of the grid instead comes from the zone logic itself — see "Discharge by zone" in the spec (green stops categorically as long as the reserve has not been reached; red caps to `limit_discharge_rate` and simultaneously to `1/4 × (min(100 %, reserve_pct) × basic_load − measured PV)` per quarter-hour outside the dirtiest hour).

**Optional: active wallbox control (`wallbox_enabled`, default `false`).** Independent of the passive exclusion above, `dirt_shift` can actively switch a wallbox's relay via a Tasmota device — switched on once **any one** of three conditions holds: two configurable dirtiness thresholds (`wallbox_cleanest_pct` — the hour must rank among the cleanest that-many percent of the rolling 24-hour window — and `wallbox_absolute_max` as an absolute ceiling) — the discharge mode plays no part; while the wallbox runs without the battery having surplus of its own, battery discharge is instead capped to the house's share, so the car charges from the grid, **or** enough battery content left against the very same red reserve the battery itself is measured by, **or** a full battery (voltage has not dropped below 3.375 V/cell over the last 15 minutes, so 54.0 V at 16S — otherwise wasted PV surplus then goes into the car). It only switches off once **all three** fail at the same time. The energy and voltage conditions each carry a hysteresis: they demand more to switch on than to hold, so the wallbox's own load does not immediately undo the condition that switched it on and leave the wallbox flapping at the quarter hour. The energy condition asks for double the buffer (two quarter hours of wallbox draw instead of one), the voltage condition holds down to 3.25 V/cell. All of these thresholds are named constants in the code, not configuration; the voltage values scale with `cell_count`. Only ever switches off what it switched on itself (a manual activation is left untouched, however dirty or energy-short it gets, see the spec's "Wallbox" section). Details, including the retry/verification logic for switching, are in `dirt_shift_spec_en.md`.

### 4. Dry run to verify

Before activation, a run without writing the timer file is recommended:

```bash
cd /opt/zeroinput/dirt_shift
# temporarily set disable_zeroinput_timer to true in dirt_shift.conf
python3 dirt_shift.py -v -debug
```

The verbose output (`-v`) shows the current zone (red/green), the battery content, the red reserve, and — when the reserve is running short — the identified dirtiest hour in the window, along with the chosen discharge mode. With wallbox control active, four more lines follow, each starting with its own verdict — the three paths individually, and below them their OR together with the actual action:

```
wallbox: dirt_ok    False   dirt% 15 (<51)   rank 62% (<30)   mode free
wallbox: energy_ok  True    content 3890 - reserve 2120 - headroom 1076 = 694 Wh (idle)
wallbox: voltage_ok False   min 53.3 V over 15 min (>=54.00 V, idle)
wallbox: should_on  True    marker(before) True   action: none
```

`idle` or `engaged` at the end of the energy and voltage lines shows which of that path's two hysteresis thresholds currently applies — both demand more to switch on than to hold; `marker(before)` is the owner marker as it stood before this run. `-debug` additionally shows the hourly overview table (PV reference curve, radiation forecast, clear-sky index, expected PV, `basic_load`, the charge/discharge tag, dirtiness, zone) and the written timer lines. The table runs **chronologically from the current hour** (first row) and across midnight into the next day. It is therefore not a calendar day but the rolling 24-hour window every calculation reads forward through — from the wrap of `23` to `0` onward, the rows are tomorrow's forecast. `-avgnew` discards every cache (7-day average, PV curve, radiation forecast, SMARD) and refetches all of them.

If the values look plausible (zone matching the grid situation, reserve in the expected range), `disable_zeroinput_timer` can be set back to `false`.

### 5. Cron entry

`dirt_shift` should run every quarter hour, **on** the quarter-hour marks (`0,15,30,45`), not shortly before them:

```bash
crontab -e
```

```cron
0,15,30,45 * * * * cd /opt/zeroinput/dirt_shift && /usr/bin/python3 dirt_shift.py >/dev/null 2>&1
```

**Why these exact minutes and not e.g. `59,14,29,44`:** `dirt_shift` always rounds the current slot **down** to the running slot when writing the timer line (`now.minute // SLOT_MINUTES * SLOT_MINUTES`). A run one minute before the mark still falls inside the **old** slot and writes its (stale) policy — the new slot would then only get its correct entry 14 minutes after it actually began. With `0,15,30,45`, each run starts exactly at the slot boundary, so the delay shrinks to plain cron dispatch jitter (seconds). The slot length is the named constant `SLOT_MINUTES` (default 15) in the code, and it also drives the per-slot energy budget, the wallbox margin, and the voltage window; changing it means changing the cron entry to match.

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

If the **battery energy content** is missing (a volkszähler gap on the voltage), the battery cannot be steered and is freed (all-allowed timer). The **wallbox control still runs**: its three paths are redundant, a missing input drops only its own path (no content → the energy path, no voltage reading → the voltage path), the others decide as usual. Only if **all** wallbox paths are undecidable for lack of data is the relay left unchanged (no switching either way). Likewise for missing SMARD data: there the emergency mode feeds the zones and the whole run, wallbox included, proceeds normally.

Only on a genuinely hard error (volkszähler returns no complete days for the average, or neither SMARD nor the weather model is available) does `dirt_shift` abort, but first — if the timer path is known — writes an "all-allowed" line dated with the current date, so zeroinput is not blocked by a stale limit; the wallbox relay is left untouched:

```
2026-07-09 00:00:00 100 100 -1
```

Because the line carries a real calendar date, the free state persists on its own once the day is over — zeroinput's timer parser, walking through every already-past line, ends up keeping exactly this one, without the file needing to be rewritten again.

If not even `zeroinput.conf` is readable (timer path unknown), only the abort with an error message remains. In both cases cron writes nothing to the log as long as `>/dev/null 2>&1` is set — for troubleshooting, remove that redirection temporarily or run `dirt_shift.py -v` by hand.

---

## Troubleshooting

**timer.txt is not written.** Check that `disable_zeroinput_timer` is `false` and that the `discharge_t_file` path read from `zeroinput.conf` is writable. A manual run with `-v` shows the resolved path.

**"cannot read zeroinput.conf".** The path in `zeroinput_conf` is wrong. It is resolved relative to the directory of `dirt_shift.py`.

**"no complete days returned by volkszähler".** The volkszähler has no complete days for the requested period. Only after a few days of operation does the average return sensible values. Until then the free-timer fallback applies.

**"SMARD load forecast for ... unavailable — using profile from ...".** Not an error: SMARD publishes the generation and load forecasts independently, and the load forecast often lags by a day. `dirt_shift` then uses the load profile of the most recent available day so the zones can be computed at all — the generation curve, which shapes the daily pattern, is current.

**"SMARD unavailable — MODELLED emergency zones" message.** The SMARD fetch failed and the cache is older than one day; `dirt_shift` falls back to zones modelled from the weather. That is a reasoned estimate, not real grid data — the night zones in particular should not be over-trusted. Once SMARD returns, normal operation resumes automatically. If `dirt_shift` aborts outright with "SMARD zone data unavailable", the weather is unreachable too; zeroinput then keeps running unrestricted via the all-allowed timer. Check the network connection and the reachability of SMARD and Open-Meteo.

**Zone looks wrong.** There are exactly two zones (red/green), cut at the median of the rolling 24-hour window. An hour can therefore change zone over the course of a day without the SMARD data changing at all — the window keeps moving. What counts is always the cut over the 24 hours from now, not over the calendar day: a dirty evening hour stays red even if its calendar day was very clean overall. `-debug` shows the median used (`rolling median ratio`), and `-avgnew` forces an immediate SMARD refetch.

**Reserve is not protected as expected.** With `-debug`, the hourly overview table shows the actual `dirt%` and `zone` classification per hour, plus the `chg` column (`L`/`D`/`!D`/`!L`). The `content ... Wh (SoC%)   reserve(...%) ... Wh [window|upcoming: .../...-...pv] -> dirtiest  HH:MM   =>  mode: ...` line in the `-v` output shows which hour currently counts as the dirtiest in the window. If that deviates noticeably from expectations, the usual cause is an ill-fitting `basic_load` formula (step 3), or a radiation forecast that doesn't match the actual weather (`-debug` table, `clr%` column).

**The reserve looks too high or too low.** The square bracket on that same line names which of the two demand estimates was binding (`window` = `red_window_demand`, `upcoming` = `upcoming_red_demand`), followed by both raw values before the `reserve_pct` scaling. If it reads `upcoming` with a `window` value of 0, the current hour lies inside a PV surplus stretch: the window calculation then sees no red demand left, while the second estimate sees the next red block beyond it. The figure after the minus (`...pv`) is the expected surplus before that block, subtracted from the demand — if it covers the demand entirely the reserve is 0 and the battery may discharge through the morning, since PV will refill it in the afternoon anyway. A reserve of 0 on a sunny morning is therefore not a fault (see "Red reserve and the dirtiest hour" in the spec).

**The wallbox does not switch as expected.** A manual run with `-v` shows the three paths individually (`dirt_ok`, `energy_ok`, `voltage_ok`); it switches on as soon as **one** of them is `True`, and only switches off once **all three** are `False`. Common causes: `dirt_ok` stays `False` even though `dirt%` looks low — what counts is not the absolute value alone but the **rank** within the window (`rank`): an hour that is clean in itself still fails if other hours of the window are cleaner still. Or the mode condition is missing, readable on the same line (`mode`). Or `action: none` despite a matching `should_on` — then the relay is already in the desired state. If it never switches off even with all three paths `False`, `marker(before)` is `False`: `dirt_shift` only ever switches off what it switched on itself.

**"battery content came out … below empty" warning.** The reconstructed energy content has gone clearly negative (below −5 % of `battery_capacity_wh`), which is physically impossible: more discharge than charge has been counted since the last empty state. The message also computes the same channel balance with **no** efficiency applied at all, and distinguishes two cases:

- **"Efficiency settings cannot explain this … check the inverters."** Even with zero assumed conversion loss the balance stays negative — no efficiency setting could explain that away, since efficiencies only ever push content down, never up. The channel data itself must be wrong: typically an inverter that *requests* power but does not *deliver* it — after a tripped RCD, a blown fuse, or a failed unit, the inverter channel logs a discharge that never actually flowed.
- **"… PV_to_bat_efficiency / bat_to_AC_efficiency may be set lower than this installation's real losses …"** Without any efficiency applied the balance would be plausible — the configured percentages may simply be lower (more pessimistic) than the real installation. Check them against a genuine empty-to-empty cycle and raise if needed. An inverter fault remains possible too, which is why the message names it as an alternative.

Either way, an anchor too far back (no real empty state for many days) makes the effect larger, since the error accumulates over a longer integration. The control keeps running meanwhile (a negative content is below any reserve anyway → the battery is spared); once a real empty state recurs, the content self-calibrates.
