# dirt_shift – Functional Specification
*v2.0*

This specification describes **what** `dirt_shift` does; it applies to the version the script carries as `VERSION`. The detailed rationale for individual rules lives in the docstring of the function named in each case, in `dirt_shift.py`.

## Purpose

`dirt_shift` shifts battery discharge into the grid hours with the highest CO₂ intensity — evening, night and early morning, when there is little PV on the grid. CO₂ intensity is a property of the **grid**; the installation itself (PV, consumption, battery content) only determines the energy available and needed.

`dirt_shift` does not control the battery directly; it writes the `timer.txt` that zeroinput reads for discharge control. Only the optional wallbox is switched directly. Direct PV pass-through to the house (`pvpt`) is always left alone — the one exception is the optional precharge path. Only **battery discharge** is limited.

---

## Data sources

- **volkszähler** (HTTP API, `vz_host_port`, timeout `VZ_TIMEOUT_S` = 30 s per request):
  - **basic_load** — house consumption per hour, `Import + |Inverter| − Auto`, as a weighted mean over `average_days` days (`get_average`, hourly in `dirt_avg_cache.json`). Each day is a 24-hour window ending one hour before the current one (`VZ_WINDOW_LAG_H`), because volkszähler does not yet return the hour that has just closed; only complete windows count. The same applies to the PV reference curve. The formula must be adapted to the installation: only plannable loads that should not be covered from the reserve are subtracted (such as a separately metered wallbox).
  - **Energy content** — reconstructed from PV and inverter energy since the last empty state, with `PV_to_bat_efficiency` and `bat_to_AC_efficiency` (`get_vz_bat_cap`, fresh every run).
  - **PV generation** — basis of the PV reference curve; the mean over the last 15 minutes (`recent_pv_power`) feeds the energy budget and the wallbox voltage condition.
  - **Battery voltage** (`Vbat`) — empty anchor of the energy content and the wallbox voltage condition.
- **SMARD** (Bundesnetzagentur) — day-ahead forecasts of wind+solar generation and consumption, basis of the zones.
- **Open-Meteo** — radiation and wind forecast (`shortwave_radiation`, `wind_speed_100m`) in one call.

**Empty anchor.** Only a **sustained** empty state counts as the integration's zero point: voltage ≤ 3.0625 V/cell (49 V at 16 cells, scaled by `cell_count`) for at least `EMPTY_MIN_DURATION_S` (120 s); a brief dip under load sets no anchor (`_empty_anchor`). Voltage samples below `IMPLAUSIBLE_V_PER_CELL` (2.5 V/cell, 40 V at 16S) are physically impossible and discarded on read. Both channels are processed sign-independently (magnitude, fixed direction).

**Warning on negative content.** If content falls below −`NEG_CONTENT_WARN_FRACTION` (5 %) of `battery_capacity_wh`, `dirt_shift` always reports it, even without `-v`. `_neg_content_diagnosis` tells two causes apart using the same balance with no efficiency applied: if that is negative too, the channel data is wrong (typically an inverter that requests power but does not deliver it — tripped RCD, fuse, failure); otherwise the efficiencies may be set too low.

---

## CO₂ intensity profile

From the SMARD forecasts `dirt_shift` computes the renewables/load ratio per hour and from it the **dirtiness** `dirt% = (1 − ratio) × 100` — negative when renewables exceed load.

Today and tomorrow are assembled into a **rolling 24-hour window** starting at the current hour. The median is taken **once** over that window (`_cut_zones`):

- **red** — ratio below the median (dirtier half)
- **green** — ratio at or above the median

A median always splits roughly in half, even on uniformly clean or dirty days. The cut is taken over the window, not per calendar day, so hours stay comparable across midnight.

**Gap filling:**
- If a day's load forecast is missing, the profile of the most recent available day is used, up to seven days back (`_smard_load_series`); if the generation forecast is missing, the day is dropped.
- Hours of tomorrow not yet published use today's values.
- Fetched hourly (`dirt_smard_cache.json`); a failed attempt is only retried the next hour.

**Outage.** The cache may bridge exactly **one day**, since data fetched yesterday holds today's day-ahead forecast. Beyond that an **emergency mode** takes over (`emergency_zones`): a relative dirtiness curve is modelled from solar and wind forecasts and a fixed, normalised **nationwide** grid-load profile, then cut as usual. It is coarse but accounts for wind. The emergency mode is clearly flagged in every output, never cached and **not** exported to volkszähler. If the weather cannot be fetched either, `dirt_shift` aborts (see Error behaviour).

---

## PV forecast

**Reference curve** (`get_pv_curve`): per hour the 95th percentile (`PV_CURVE_PERCENTILE`) of measured generation over the last 14 days (`PV_CURVE_DAYS`). It reflects the installation's orientation and shading without configuration. Recomputed once a day from 4 a.m. (`PV_CURVE_REFRESH_HOUR`), cache `dirt_pv_curve_cache.json`.

**Weather scaling** (`scaled_pv_curve`):

```
expected_pv = reference × min(1, radiation forecast / clear-sky GHI)
```

Clear-sky GHI follows the Haurwitz model from the sun's position (`clear_sky_ghi`). Radiation and wind are fetched hourly as a 48-hour series (`dirt_weather_cache.json`) and assembled into a rolling window like the zones; missing tomorrow values fall back to today. Without a radiation forecast the unscaled reference curve applies, without a reference curve a zero-PV day.

---

## Red reserve and the dirtiest hour

The **reserve** is the energy held back for the coming red hours:

```
reserve = reserve_pct × max(red_window_demand,
                            max(0, upcoming_red_demand − surplus_before_next_red))
```

- `red_window_demand` — net demand (`basic_load − expected_pv`) of all red hours from now up to the first surplus hour; on a dull day without surplus, all red hours of the window.
- `upcoming_red_demand` — net demand of the next contiguous red block, even across a running surplus stretch.
- `surplus_before_next_red` — surplus expected before that block begins; it fills the battery anyway and is therefore subtracted. On a sunny day the reserve thus drops to 0.

The two demand estimates cover each other's blind spots; the rationale is in their docstrings. `reserve_pct` (default 90) may exceed 100, which makes the reserve more cautious.

**Dirtiest hour** (`dirtiest_hour`): if content does not cover the reserve, the red hour with the highest `dirt%` in the same window is picked (earliest on a tie). Only it is served without limit; every other red hour is capped. The window is rebuilt every run, so a past dirtiest hour drops out on its own.

---

## Discharge by zone

| Zone | Condition | Mode |
|---|---|---|
| green | content > reserve | **free** |
| green | content ≤ reserve, content + surplus up to the red block > `battery_capacity_wh` | **capped** (overflow guard) |
| green | content ≤ reserve, red block at least `STOP_MIN_DIRT_GAP` (10) points dirtier | **stop** |
| green | content ≤ reserve, smaller gap | **capped** |
| red | content ≥ reserve | **free** |
| red | content < reserve, current hour is the dirtiest | **free** |
| red | content < reserve, otherwise | **capped** |

- **Overflow guard:** a stop would keep the battery full while the midday surplus is curtailed under zero feed-in. Instead the battery covers the house in this hour and makes room. Without `battery_capacity_wh` this check is skipped.
- **Minimum gap:** compared against the mean of the red hours the reserve is held for (`reserved_red_dirt`). The threshold absorbs forecast uncertainty; shifting energy that is already stored involves no round-trip loss. If a `dirt%` value is missing, stop applies.
- **Cap:** `limit_discharge_rate` (watts) **and** at the same time an energy budget per quarter hour:

```
budget = ¼ × (min(100 %, reserve_pct) × basic_load − measured PV)   (at least 0)
```

The budget uses the **measured** PV of the last 15 minutes (`recent_pv_power`), since an hourly mean is too coarse at the ramp hours; `reserve_pct` is capped at 100 % here. Both are explained in `write_timer`.

---

## Precharge (optional, off by default)

With `precharge_enabled: true`, `dirt_shift` can steer PV that would otherwise feed the house into the battery during clean hours; the house draws that share from the grid. The trade: a clean grid import in the current hour for more charge for the dirty red block — paid for with the round-trip loss.

This only helps while the battery would **not** be full by the red block anyway; otherwise diverting just fills it earlier and more PV is curtailed later:

```
capacity 10, content 5, surplus up to red 9, red demand 12
without precharge:     battery full, 4 curtailed, red imports 2
precharge diverts 1:   house imports 1, battery still full, 5 curtailed, red imports 2
```

A green hour is only throttled if all three conditions hold (`precharge_ac_pct`):

1. **Rank** — it ranks among the cleanest `precharge_cleanest_pct` % of the window (`_dirt_rank_pct`).
2. **Round-trip gap** — the red block is dirtier by at least `precharge_round_trip_gap()` points:
   ```
   minimum gap = (1 − PV_to_bat_efficiency × bat_to_AC_efficiency) × 100 × round_trip_margin
   ```
   `round_trip_margin` is the cost-versus-cleanliness setting: 1.0 diverts as soon as it saves CO₂ net; larger values demand a clearer gain, a very large one effectively turns precharge off; values below 1.0 count as 1.0.
3. **Remaining room** — after the natural surplus there is room left that the red block will actually use:
   ```
   end content = content + surplus_before_next_red
   usable      = min(battery_capacity_wh − end content, red block demand − end content)
   ```

`usable` is spread across the hours up to the red block that pass both rank **and** gap, in proportion to the PV they feed the house:

```
divertable[h] = min(exp_PV[h], basic_load[h])
divert        = min(divertable[now], usable × divertable[now] / Σ divertable)
ac_%          = max(PRECHARGE_AC_FLOOR, round(100 × (exp_PV[now] − divert) / exp_PV[now]))
```

**Real data only:** the precharge window runs from the current hour to the last hour with real SMARD data. It ends at the first hour that is only a substitute (today's value for a tomorrow not yet published) or has no value. Rank, the red block's mean and demand, and the distribution use only hours inside that window; if the red block lies wholly beyond it, `ac_%` stays at 100. In emergency mode precharge is off. Discharge control keeps using the full window.

`PRECHARGE_AC_FLOOR` (20) keeps zeroinput from rejecting the line as invalid. Precharge typically engages on days with a clean grid but little PV surplus of one's own, not on sunny days. It does not affect the wallbox.

---

## Wallbox (optional, off by default)

With `wallbox_enabled: true`, `dirt_shift` switches a wallbox's relay via a Tasmota device. It knows no charging need (amount, deadline); if no car is plugged in, switching on does nothing.

Every run applies the same formula, both for switching on and for staying on (`wallbox_decide`):

```
should_on = cleanliness condition  OR  energy condition  OR  voltage condition
```

| Condition | Check | Charges from |
|---|---|---|
| Cleanliness | rank of the hour ≤ `wallbox_cleanest_pct` % **and** `dirt%` ≤ `wallbox_absolute_max` | grid |
| Energy | `content − headroom > reserve_pct × max(red_window_demand, upcoming_red_demand)` | battery surplus |
| Voltage | minimum of Vbat over 15 min ≥ 3.375 V/cell **and** measured PV > `basic_load` | PV surplus |

- **Cleanliness** is a rank, not a share of the median, because `dirt%` is signed (`_dirt_rank_pct`). The battery mode plays no part in it.
- **Energy** deliberately calculates **without** the surplus deduction and is therefore stricter than the main reserve: a running wallbox would eat exactly that surplus. `headroom` is 2 × `margin` when switching on, 1 × when holding (`margin` = `wallbox_typical_power` × ¼ h), against flapping (`wallbox_energy_ok`).
- **Voltage** only applies while there is actual PV surplus (measured PV of the last 15 min above the hour's house load): without surplus nothing is curtailed, so a charged battery resting in the plateau holds the wallbox neither at night nor on dull afternoons. The hysteresis is narrow — on from 3.375, held down to 3.35 V/cell — so the holding band stays above the LiFePO₄ plateau and the 15-minute minimum still detects a real discharge load (`wallbox_voltage_ok`).

**Missing data:** if a path's input is missing (battery content, voltage), only that path drops out; the others keep deciding. If no path can decide, the relay is left unchanged.

### Battery-side protection

The wallbox decision is made **before** the timer line is written and feeds into the mode:

```
wallbox running  AND  mode would be free  AND  content ≤ reserve  AND  neither energy nor voltage condition
  →  mode becomes capped
```

In practice this concerns the dirtiest red hour, where free applies regardless of content: the forced discharge must not go into the car, whose load lands on the grid. The `-v` output shows `[free->limit: wallbox on grid terms]`.

### Wallbox without an effective timer

The wallbox is controlled independently of the timer: also with `disable_zeroinput_timer: true` in `dirt_shift.conf` or `discharge_timer: false` in `zeroinput.conf`. The battery-side protection is then missing, though; on the cleanliness condition alone the car can drain the battery below the reserve. The energy condition still checks the reserve.

### Owner marker

`dirt_shift` only switches off what it switched on itself. `dirt_wallbox_marker.json` holds `dirt_shift_on` (own relay) plus `energy_on` and `voltage_on` (hysteresis states, kept separately so no holding threshold props up a relay switched on by someone else). On the first run an already-on relay counts as foreign. A manually switched-on relay is adopted as soon as an hour meets the conditions and can then be switched off as usual. A manual switch while the marker is already set cannot be detected.

### Switching with verification

A direct `Power{output}` command, then the actual relay state is queried: up to 3 attempts, 30 s apart (`wallbox_switch`). Only a verified change updates the marker. An unreachable Tasmota device can therefore delay the run by up to ~90 s, including the writing of the timer line.

---

## Output: timer.txt

```
YYYY-MM-DD HH:MM:00 <discharge-W> <ac-%> <energy-Wh>
```

Fields separated by exactly one space; values > 100 count as watts, ≤ 100 as percent.

| Mode | Line |
|---|---|
| free | `100 100 -1` |
| capped | `<limit_discharge_rate> 100 <budget>` |
| stop | `000 100 000` |

`-1` is the "unlimited" sentinel zeroinput recognises. zeroinput resets its energy counter on every new line; since `dirt_shift` writes every quarter hour, each quarter hour gets its own budget. During precharge `ac-%` lies between 20 and 100. Only the current quarter-hour slot is written (`write_timer`); zeroinput applies the last line that has already begun.

**DISABLED timer:** if `discharge_timer` is `false` in `zeroinput.conf`, the line is written anyway, but every run reports that zeroinput will not act on it. The wallbox is still controlled.

---

## Failsafe

If a run caps, stops or throttles, an "all-allowed" line (`100 100 -1`) follows 30 minutes later. While `dirt_shift` keeps running, the limit is renewed every 15 minutes; if it stops, the limit lifts on its own.

---

## Exporting grid dirtiness

If `vz_dirtiness_uuid` is set, every run writes the current `dirt%` value to that volkszähler channel via HTTP POST; on the first run of an hour it also writes the previous hour's value one second before the hour boundary, so the curve forms a clean step (`write_dirtiness_to_vz`). Nothing is exported in emergency mode. A failure is only reported with `-v`.

---

## Output: pv_forecast.json

Once per calendar day, `write_daily_pv_forecast` writes next to `dirt_shift.py` (fixed name `PV_FORECAST_FILE`, not configurable):

```json
{"datum": "2026-09-29", "pv_forecast_kwh": 23.4}
```

`pv_forecast_kwh` is the weather-scaled PV forecast summed over the **whole calendar day** (hours 0–23). The file's own date marks whether today has been written; if data is missing, the next run tries again. `dirt_shift` itself never reads the file. Field name and meaning match the `pv_forecast_kwh` field of the [Ortsnetz-Auslastung](https://github.com/thomaslehmann1234/datenerfassung-ortsnetz-auslastung) API it is intended for; a vzlogger cron script of that project reads the value and submits it along with the grid voltage readings.

---

## Output: -v and -debug

The first line gives the version (`dirt_shift 2.0`).

**Cache lines** give the fetch time and the reach of the data, e.g. `using cached SMARD zones from 2026-09-10 20:00, data until 2026-09-11 19:00`.

**Mode line:**

```
content ... Wh (SoC%)   reserve(...%) ... Wh [window|upcoming: .../...-...pv] -> dirtiest  HH:MM   =>  mode: ...
```

The bracket names the binding estimate, both raw values and the subtracted surplus. Suffixes such as `(comfortable)`, `[red block +N dirt]` or `[stop would overflow ...]` explain the mode.

**Debug table** (`_hourly_debug_table`): 24 rows in chronological order from the current hour, across midnight.

- `chg`: `L` charging hour (`exp_PV > basic_load`), `D` discharging hour; `!D` = dirtiest hour, `!L` = cleanest charging hour (informational only).
- `balance` = `exp_PV − basic_load`.
- A leading `.` on `rad_Wm2` or `dirt%`: no own data yet for this hour of tomorrow.
- Before the current hour's `dirt%`: `*` exported, `!` export failed.
- Summary row: daily totals of `exp_PV` and `basic_load`, their difference, the `dirt%` average.

---

## Error behaviour

Missing data drops only the affected part wherever possible:

| Missing | Consequence |
|---|---|
| battery content | battery free, wallbox still steered via the other paths |
| SMARD beyond one day | emergency mode |
| radiation forecast | unscaled reference curve |
| load forecast | profile of the most recent available day |
| volkszähler unreachable | cached averages and PV curve; battery free, as the battery content is missing |

Only if there are no complete days for the average, volkszähler is unreachable and no cached averages exist, or neither SMARD nor weather is available, does `dirt_shift` abort. Beforehand it writes an "all-allowed" line dated today (`write_free_timer`), except with `disable_zeroinput_timer: true`; the wallbox relay is left untouched. If not even `zeroinput.conf` is readable, only the error message remains.

---

## Configuration

`dirt_shift.conf` holds the path to `zeroinput.conf` and its own parameters; `zeroinput.conf` is never modified.

Read from `zeroinput.conf`:

- `discharge_t_file` — path of the timer file (relative to `zeroinput.conf`)
- `cell_count` — number of cells for the voltage thresholds (default 16)
- `discharge_timer` — whether zeroinput applies the timer file; if the key is absent, it counts as enabled

In `dirt_shift.conf`:

| Key | Meaning | Default |
|---|---|---|
| `zeroinput_conf` | path to `zeroinput.conf` | `../zeroinput.conf` |
| `vz_host_port`, `vz_chans` | volkszähler host and channel UUIDs | — |
| `vz_dirtiness_uuid` | channel for the dirtiness export, empty = off | empty |
| `average_days` | days for the `basic_load` average | 7 |
| `day_weights_pct` | weight per day, index 0 = oldest; equal weights on a length mismatch | `[200, 100, 100, 100, 100, 150, 200]` |
| `reserve_pct` | share of demand held as reserve; also scales the budget (capped at 100 % there) and the wallbox reserve | 90 |
| `limit_discharge_rate` | watt cap in capped hours | 3000 |
| `latitude`, `longitude` | location for the clear-sky model and weather | 51.0 / 10.0 |
| `PV_to_bat_efficiency`, `bat_to_AC_efficiency` | efficiencies in % for energy content and round-trip loss | 98 / 90 |
| `max_days_empty_battery` | search depth for the empty anchor in days | 7 |
| `battery_capacity_wh` | usable capacity; overflow guard, precharge, state of charge, negative warning; 0 = checks off | 5000 |
| `disable_zeroinput_timer` | write no timer file (not even after an abort); battery unsteered, wallbox still controlled | `false` |
| `logfile` | mirrors the output to a file and enables `-v`; empty = off | empty |
| `precharge_enabled` | precharge path | `false` |
| `precharge_cleanest_pct` | rank threshold for precharge in % | 25 |
| `round_trip_margin` | cost/cleanliness for precharge, ≥ 1.0 | 1.5 |
| `wallbox_enabled` | wallbox control | `false` |
| `wallbox_ip`, `wallbox_output` | Tasmota device and relay number | —, `"1"` |
| `wallbox_cleanest_pct` | rank threshold for the cleanliness condition in % | 30 |
| `wallbox_absolute_max` | absolute `dirt%` ceiling for the wallbox | 50 |
| `wallbox_typical_power` | typical charging power in W for the energy condition | 2000 |

The help text for every key is in the `*_help` entries of `dirt_shift.conf`.

---

## Command-line options

- `-v` — verbose output
- `-debug` — also the hourly table (implies `-v`)
- `-avgnew` — refetch all caches
- `-html` — HTML header/footer around the output
- `-h` — short help with the version number
