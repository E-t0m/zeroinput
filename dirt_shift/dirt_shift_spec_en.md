# dirt_shift – Functional specification
*v1.0*

## Purpose

`dirt_shift` steers the battery discharge deliberately into the grid hours with the highest CO₂ intensity. The German electricity mix is dirtier in the evening and at night (PV gone, evening load high, fossil peaks) and in the early morning until enough PV is on the grid; during the day its CO₂ intensity is low. If you can choose *when* the battery rather than the grid covers consumption, you avoid the most emissions in the hours of high CO₂ intensity. `dirt_shift` therefore directs all available battery energy into those hours — most strongly into the ones with the highest intensity.

CO₂ intensity is a pure **grid** property and does not depend on your own installation. Your installation (PV, consumption, battery content) only determines the *amount* of energy available and needed overnight.

`dirt_shift` is a standalone tool from the same family as `tib_zero_tas.py` and an **alternative** to its price-driven approach. Both write the same `timer.txt` and should not run at the same time. If you use a dynamic electricity tariff (e.g. Tibber), the price-driven variant is usually the better fit; `dirt_shift` targets installations without a dynamic tariff where the CO₂ balance is the control objective. **No** grid data is fetched — the CO₂-intensity profile is derived solely from the computed solar position plus fixed load bounds (see CO₂-intensity profile).

Direct PV pass-through (`pvpt`) is **always** guaranteed, regardless of everything else. This means that PV power produced right now is passed straight through to cover house consumption, without the detour via the battery. That has the best efficiency (no charge/discharge loss) and spares the battery (no extra cycle). `dirt_shift` acts exclusively on the **battery discharge**, never on `pvpt`.

---

## Data sources

All data comes from volkszähler (same channels as `tib_zero_tas`):

- **basic_load** — the actual house consumption in Wh/h, computed in the default formula as `Import + |Inverter| − Auto`. A 7-day hourly average, cached hourly in `dirt_avg_cache.json`. Used to size the night reserve.
- **Energy content** — the real battery content in Wh, reconstructed via `get_vz_bat_cap` by integrating PV and inverter since the last known "empty" state (voltage ≤ 3.0625 V/cell as the anchor, i.e. 49 V at 16 cells; scaled with `cell_count` from the zeroinput configuration), with efficiencies applied. Fetched fresh on every run.

> **basic_load is freely adaptable.** The formula `Import + |Inverter| − Auto` reflects one particular installation (with an EV wallbox as a separate channel that is subtracted from house consumption). It is **not a standard** but must be adapted to your own installation: absent channels are dropped, additional ones are added. Without a separately metered wallbox the `Auto` term disappears; an additional separately metered load (e.g. a PV-battery charger) would come in as a further subtraction term. Only **schedulable** loads that should not be covered from the night budget are subtracted (the car is charged deliberately). Demand-driven loads such as an air conditioner stay **in** basic_load — they belong to the consumption to be covered overnight and are captured within limits through the 7-day average. What matters is that basic_load ends up as the **actual house consumption to be covered** — i.e. import plus the inverter power delivered by the battery, cleaned of everything that should not be covered from battery/grid. The calculation lives in `get_average` and is edited there directly; the channel set in `vz_chans` is reduced or extended accordingly.

The 7-day basis (`average_days`) contains exactly one full week structure — every weekday appears once, so the average is balanced across the week. `day_weights_pct` lets you weight individual days more heavily (see Configuration), e.g. yesterday and the same weekday of the previous week; without weighting every day counts equally.

---

## CO₂-intensity profile

The grid's CO₂ intensity is derived without external data from the **solar position** plus fixed **load bounds**. Sunrise and sunset are computed astronomically from date and location (`latitude`/`longitude`) (a simple approximation accurate to a minute or two — irrelevant against the hourly bounds; daylight saving is handled via the system timezone).

The renewable share on the grid lags the solar position: in the morning CO₂ intensity only falls once there is enough PV relative to **load** on the grid (load ramps up on a clock schedule), in the evening it rises before the sun is gone (PV falls, evening load rises). The favourable (low-intensity, "green") window is therefore a **hybrid** of solar position and fixed clock bounds:

```
green start = max(sunrise + green_morning_offset_h, green_earliest)
green end   = min(sunset  − red_evening_offset_h,   green_latest)
```

In summer the fixed bounds dominate (`green_earliest` ~09:00, `green_latest` ~17:30) — the very early sunrise does not lower grid intensity at 7 a.m., because load only ramps up later. In winter the later sunrise / earlier sunset dominates, and the favourable window shrinks on its own.

Three zones:

- **red** (highest intensity) — outside the favourable window: evening, night, early morning
- **yellow** (transition) — `yellow_width_h` on both edges of the green window
- **green** (low intensity) — the middle of the day

---

## Discharge by zone

On each run (every quarter hour) the discharge behaviour is derived from the current zone. All battery content is steered into the high-CO₂-intensity hours, most strongly into the most intense ones:

- **red** → **no limit**: full discharge cap (`100 100 99999`), the battery may discharge unrestricted
- **yellow** → **`single_inverter_threshold`** as the cap: throttled discharge in the transition zone
- **green** → **no battery discharge**: `pvpt` only (`000 100 000`)

`pvpt` (direct PV pass-through) keeps running in all zones; `dirt_shift` controls the battery discharge only.

---

## Night reserve and build_reserve_after

The **night reserve** is `reserve_pct` (default 90 %) of the `basic_load` demand across the upcoming contiguous **red window** (from the evening green-end through the night to the morning green-start). The 90 % is chosen so the reserve is nearly used up by morning.

`build_reserve_after` (default 13:30) controls from when this reserve is protected:

- **before 13:30**: no reserve protected — the battery discharges freely into whatever has high intensity *now* (including the morning red window). This runs the battery down in the morning and makes room for the afternoon yield.
- **from 13:30**: surplus above the reserve may still go out into intense (yellow) hours; but once the content has fallen to the reserve, discharge into non-red hours is stopped (`pvpt` only) until the red night window itself begins. There the reserve is then released by zone (red without limit, yellow with the single-inverter cap).

The 13:30 point deliberately sits in the second half of the day: the largest morning consumption (cooking) is over by then, and the further PV yield is more predictable. If, against expectation, no more yield comes in the afternoon, the night simply has to be covered partly from the grid — `dirt_shift` is an optimization, not a critical controller.

## Failsafe

`dirt_shift` is optional and must never block zeroinput's normal operation. If a run limits or stops the discharge (yellow/green/reserve protection), it additionally writes an "all-allowed" line (`100 100 99999`) 30 minutes later. While the script keeps running, the limit is renewed every 15 minutes; if it fails (cron failure, volkszähler unreachable, crash), the limit lifts itself after 30 minutes and zeroinput discharges freely again, as if `dirt_shift` did not exist. In the red window (no limit) everything is allowed anyway, so the single line suffices there.

---

## Output: timer.txt

`dirt_shift` writes `timer.txt` in the zeroinput format:

```
0000-00-00 HH:MM:00  <discharge-W>  <ac-%>  <energy-Wh>
```

- Date `0000-00-00` = daily recurring (no calendar date, the midnight rollover needs no special handling).
- **discharge-W** — discharge cap; `100` (percent) = no limit (red), `single_inverter_threshold` (watts) = throttled (yellow), `000` = no battery discharge (green/stop).
- **ac-%** — inverter pass-through, always `100` (pvpt guaranteed).
- **energy-Wh** — energy budget; `99999` = effectively unlimited (discharge allowed), `000` = no budget (stop).

The three modes are thus: `100 100 99999` (red, no limit), `<single_inv> 100 99999` (yellow, single-inverter), `000 100 000` (green/stop).

Values > 100 are interpreted as watts, values ≤ 100 as percent — as in the existing zeroinput timer format. zeroinput's `discharge_times` parser reads the quarter-hourly resolution without adaptation; it replaces `0000-00-00` with the current date and takes the last slot that has already passed.

The written plan is short: the current quarter-hour slot in the chosen mode (free / single-inverter / stop), and — if the mode limits or stops — an "all-allowed" line 30 minutes later as a failsafe.

`dirt_shift` runs every quarter hour (e.g. via cron) and rewrites the file each time with the current energy content and current zone.

---

## Configuration

`dirt_shift.conf` contains **only** the path to `zeroinput.conf` and dirt_shift's own parameters. Values that already live in `zeroinput.conf` are read from there rather than duplicated — `dirt_shift` never modifies `zeroinput.conf`.

Read from `zeroinput.conf` (read-only):

- **`single_inverter_threshold`** — limits the discharge cap per slot so the night discharge stays on stage 1.
- **`discharge_t_file`** — the path of the timer file that zeroinput reads. `dirt_shift` writes exactly this file (resolved relative to zeroinput.conf). This guarantees writer and reader point at the same file. `discharge_timer` must additionally be enabled in `zeroinput.conf`, otherwise zeroinput ignores the file.

dirt_shift's own keys in `dirt_shift.conf`:

- `zeroinput_conf` — path to `zeroinput.conf` (default `../zeroinput.conf`, since dirt_shift usually lives in a subfolder of zeroinput)
- `vz_host_port`, `vz_chans` — volkszähler host and channel UUIDs for the data.json API. Separate from zeroinput's `vz_channels`/`vzlogger_log_file`: dirt_shift uses the HTTP API for averages and energy content, zeroinput the vzlogger FIFO for live control. Both access the same volkszähler; the UUID lists need not be identical.
- `average_days` — days for the hourly average (default 7)
- `day_weights_pct` — per-day weighting in percent for the average, chronological: index 0 = oldest day (today minus `average_days`, i.e. the same weekday of the previous week), index −1 = yesterday. Weighting yesterday and the previous week's weekday more heavily captures the most recent trend and the weekday structure. The length must match `average_days`; on mismatch all days are weighted equally. All 100 = neutral.
- `reserve_pct` — percent of the `basic_load` demand across the red night window that is reserved (default 90)
- `build_reserve_after` — time (HH:MM) from which the night reserve is protected (default 13:30). Before that the battery discharges freely into whatever has high intensity now; from then only the surplus above the reserve may go into intense hours, and once content falls to the reserve, discharge stops until the red night window.
- `latitude`, `longitude` — installation location (decimal degrees) for the solar-position calculation; default ~centre of Germany (51.0 / 10.0)
- `green_morning_offset_h`, `red_evening_offset_h` — hour offset from the solar position: green only this long after sunrise, red this long before sunset (default 3.5 / 3.0)
- `green_earliest`, `green_latest` — fixed clock bounds of the green window, dominant in summer (default 9.0 / 17.5)
- `yellow_width_h` — width of the yellow transition zone at both green edges (default 1.0)
- `PV_to_bat_efficiency`, `bat_to_AC_efficiency` — efficiencies for reconstructing the energy content
- `max_days_empty_battery` — how many days back an "empty" state is searched for
- `disable_zeroinput_timer` — set to `true` to compute and print without writing the timer file (dry run)

### Error behaviour

On a hard error (volkszähler returns no complete days, energy content not computable), `dirt_shift` aborts, but first — if the timer path is known — writes an "all-allowed" line so zeroinput is not blocked by a stale or missing limit:

```
0000-00-00 00:00:00 100 100 99999
```

(full discharge, full pass-through, effectively unlimited energy budget, daily from midnight). If not even `zeroinput.conf` is readable (timer path unknown), only the abort with an error message remains.

---

## Command-line options

- `-v` — verbose console output
- `-html` — HTML header/footer around the output
- `-debug` — more output (implies `-v`)
- `-avgnew` — forces a fresh 7-day average instead of the cache
