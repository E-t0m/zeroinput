# dirt_shift – Functional specification
*v1.0*

## Purpose

`dirt_shift` deliberately shifts battery discharge into the grid hours with the highest CO₂ intensity. The German electricity mix is dirtier in the evening and at night (PV gone, evening load high, fossil peaks) and in the early morning until enough PV is on the grid; during the day its CO₂ intensity is low. If you can choose *when* the battery rather than the grid covers consumption, you avoid the most emissions in the hours of high CO₂ intensity. `dirt_shift` therefore directs all available battery energy into those hours — most strongly into the ones with the highest intensity.

CO₂ intensity is a pure **grid** property and does not depend on your own installation. Your installation (PV, consumption, battery content) only determines the *amount* of energy available and needed overnight.

`dirt_shift` steers battery discharge based on grid CO₂ intensity. It writes the same `timer.txt` that zeroinput reads for discharge control.

Direct PV pass-through (`pvpt`) is **always** guaranteed, regardless of everything else. This means that PV power produced right now is passed straight through to cover house consumption, without the detour via the battery. That has the best efficiency (no charge/discharge loss) and spares the battery (no extra cycle). `dirt_shift` acts exclusively on the **battery discharge**, never on `pvpt`.

---

## Data sources

All data comes from volkszähler:

- **basic_load** — the actual house consumption in Wh/h, computed in the default formula as `Import + |Inverter| − Auto`. A 7-day hourly average, cached hourly in `dirt_avg_cache.json`. Used to size the red reserve and the energy balance (see Red reserve and energy balance).
- **Energy content** — the real battery content in Wh, reconstructed via `get_vz_bat_cap` by integrating PV and inverter since the last known "empty" state (voltage ≤ 3.0625 V/cell as the anchor, i.e. 49 V at 16 cells; scaled with `cell_count` from the zeroinput configuration), with efficiencies applied. Fetched fresh on every run.
- **PV generation** — the same PV channel is also used for the empirical PV reference curve (see below); the raw values there are handled with `abs()`, since this channel is logged negative on many installations.

> **basic_load is freely adaptable.** The formula `Import + |Inverter| − Auto` reflects one particular installation (with an EV wallbox as a separate channel that is subtracted from house consumption). It is **not a standard** but must be adapted to your own installation: absent channels are dropped, additional ones are added. Without a separately metered wallbox the `Auto` term disappears; an additional separately metered load (e.g. a PV-battery charger) would come in as a further subtraction term. Only **schedulable** loads that should not be covered from the night budget are subtracted (the car is charged deliberately). Demand-driven loads such as an air conditioner stay **in** basic_load — they belong to the consumption to be covered overnight and are captured within limits through the 7-day average. What matters is that basic_load ends up as the **actual house consumption to be covered** — i.e. import plus the inverter power delivered by the battery, cleaned of everything that should not be covered from battery/grid. The calculation lives in `get_average` and is edited there directly; the channel set in `vz_chans` is reduced or extended accordingly.

The 7-day basis (`average_days`) contains exactly one full week structure — every weekday appears once, so the average is balanced across the week. `day_weights_pct` lets you weight individual days more heavily (see Configuration), e.g. yesterday and the same weekday of the previous week; without weighting every day counts equally.

---

## CO₂-intensity profile

The zone classification (red/yellow/green) comes from one of two sources:

**SMARD** (Bundesnetzagentur, `smard_enabled: true`) — real day-ahead grid data, free and without registration. `dirt_shift` fetches the forecasted wind+solar generation and forecasted total consumption for today **and tomorrow**, and builds the hourly ratio of renewables to load from them, separately for each calendar date. Each date's 24 hours are ranked by this ratio and split by percentile — the `SMARD_GREEN_FRACTION` (30 %) highest hours become green, the `SMARD_RED_FRACTION` (30 %) lowest become red, the rest yellow — so the split adapts to each day's own shape rather than hanging on a fixed absolute threshold.

Externally, `dirt_shift` turns this into a **rolling 24-hour array**, anchored at the current time: hours from now until midnight come from today's classification, hours after midnight come from tomorrow's — an hour that looks 'already past' in the array is really the next upcoming occurrence of that hour tomorrow, with tomorrow's own real classification rather than a reuse of today's pattern. If tomorrow's day-ahead forecast for a given hour is not published yet (typically before late afternoon) or unavailable entirely, that hour falls back to today's classification. If even today's query fails (no network, incomplete data, `smard_enabled: false`), `dirt_shift` falls back to the sun-position heuristic entirely.

**Sun position** (fallback, always available, no external data) — sunrise and sunset are computed astronomically from date and location (`latitude`/`longitude`; a simple approximation, accurate to within a minute or two; DST is accounted for via the system timezone). For wrapped hours (before the current time in the rolling array), this still uses today's date, not tomorrow's — sunrise/sunset shift by only a minute or two day to day, so the difference is negligible here. The renewable share on the grid lags the sun position: in the morning, CO₂ intensity only drops once enough PV is on the grid relative to **load** (load ramps up on a clock schedule), in the evening it rises before the sun is fully gone (PV falls, evening load rises). The favourable (low-intensity, "green") window is therefore a **hybrid** of sun position and fixed clock bounds:

Three zones (both sources produce the same three values):

- **red** (highest intensity) — outside the favourable window, or the lowest renewable ratio: evening, night, early morning
- **yellow** (transition) — `yellow_width_h` on both edges of the green window, or a middling ratio
- **green** (low intensity) — the middle of the day, or the highest ratio

---

## PV reference curve

For the energy balance (see next section), `dirt_shift` needs an estimate of how much PV yield is still expected for the rest of the day. Instead of a physical model of the roof (which would be a burden to maintain with several sections of different orientation and seasonal shading) — `dirt_shift` uses the installation's **own, actually measured** output: for each hour of the day, the `PV_CURVE_PERCENTILE`th percentile (95th) of that hour's PV readings over the last `PV_CURVE_DAYS` (14) days is taken — close to the peak, but without one record day skewing the curve. Because the curve comes from the installation itself, it automatically reflects its real geometry (several sections, shading) without anything about tilt, orientation, or shading needing to be configured.

The curve is recomputed **once a day**, from `PV_CURVE_REFRESH_HOUR` (4, a quiet pre-sunrise time with no same-day data to compete with) on, and cached in `dirt_pv_curve_cache.json` — independent of the hourly cadence of the `basic_load` average. The underlying query uses the same hourly resolution (`group=hour`) as the `basic_load` query, not minute-level values.

---

## Radiation-forecast scaling

The PV reference curve shows what to expect on a **typical** day — it knows nothing about today's actual weather. That gap is filled by a free, registration-free radiation forecast from **Open-Meteo** (`shortwave_radiation`, W/m², hourly, for today). `shortwave_radiation` is the global horizontal irradiance (direct plus diffuse component together) — Open-Meteo's physical model output for the actual radiative power reaching the ground.

A **clear-sky index** is formed from the radiation forecast: `expected_pv = reference value × min(1, radiation forecast / clear-sky GHI)`. `clear-sky GHI` is the modelled global irradiance under a cloudless sky for the same hour and location, from the Haurwitz (1945) clear-sky model: `GHI = 1098 × cos(z) × exp(−0.059 / cos(z))` for zenith angle `z` (derived from solar elevation, see `solar_elevation_deg`/`clear_sky_ghi`), else 0 (sun below the horizon). The model needs only the solar position — no turbidity/aerosol data — so it is computable offline and consistent with the rest of dirt_shift's sun-position math. The index is capped at 1.0 (brief cloud-edge radiation enhancement above the clear-sky value is not modelled, to keep the forecast conservative).

This automatically yields a season- and time-of-day-dependent reference: in winter, midday clear-sky GHI is substantially lower than in summer (shallower sun angle), so the same measured radiation value produces a higher clear-sky index (less damping) in winter than in summer at the same absolute irradiance — matching physical reality.

Each request fetches the full 48-hour series (today **and** tomorrow) in one call; Open-Meteo maintains a continuously updated timeseries in which each new model run is stitched seamlessly onto the previous one — even already-elapsed hours of today get overwritten with the latest available model state. From the two 24-value series (today/tomorrow), `dirt_shift` builds a **rolling 24-hour array**, anchored at the current time: hours from now until midnight come from today's series, hours after midnight come from tomorrow's — an hour that looks 'already past' in the array is really the genuine forecast for the next upcoming occurrence of that hour tomorrow. `clear_sky_ghi` is evaluated against tomorrow's date rather than today's for those hours accordingly. An hour missing from tomorrow's series falls back to today's value for that hour.

The full 48-hour series is refetched hourly and cached in `dirt_weather_cache.json`, independent of the other caches. If it is unavailable (fetch failed), the reference curve is used unscaled — if the reference curve itself is also unavailable, there is no forecast at all, and `dirt_shift` falls back to `build_reserve_after` as a plain clock cutoff (see next section).

---

## Discharge by zone

Once the red reserve is protected (see next section), the current zone determines the discharge behaviour on each run (every quarter hour). All battery content is steered into the high-CO₂-intensity hours, most strongly into the most intense ones:

- **red** → **no limit**: full discharge cap (`100 100 99999`), the battery may discharge unrestricted
- **yellow** → **`yellow_cap`** (watts) as the cap: throttled discharge in the transition zone, as long as current content is still above the reserve — once it has dropped to the reserve, discharge stops
- **green** → **no battery discharge**: `pvpt` only (`000 100 000`)

As long as the reserve is not protected, the battery discharges freely regardless of zone (see next section). `pvpt` (direct PV pass-through) keeps running in every zone regardless; `dirt_shift` controls the battery discharge only.

---

## Red reserve and energy balance

The **red reserve** is `reserve_pct` (default 90 %) of the `basic_load` demand across every **red** hour between now and the next **PV production phase**, **net** of any PV still expected during those hours. The window's boundary is the first hour whose expected PV exceeds its `basic_load` (a surplus hour): from there on the battery genuinely refills, and any later red span is covered by the coming yield, not by yesterday's charge — holding content back for it would only block storage room for the coming yield. Several separate red spans before that point (evening red, night red, morning red with yellow gaps between them) are all summed, since nothing refills the battery in between. On a day so dull that expected PV never exceeds load, no surplus hour exists — then all red hours of the rolling 24 h are reserved for, which is correct, as no refill is coming. On the netting: `pvpt` already covers part of that demand directly (at the edges of a red span, while the sun is not fully gone yet or once it is back), so that part need not also be reserved from the battery. If no PV forecast is available, no surplus hour can be detected and no netting done — the plain `basic_load` sum across all red hours of the rolling 24 h then serves as the conservative fallback. The 90 % deliberately adds no safety margin above the calculated demand — under normal circumstances the battery is meant to discharge essentially fully over the red window, rather than holding capacity back unused.

Which hours count as "red" comes from the **same** source that drives the current zone decision (SMARD if active, otherwise the sun position) — not always rigidly from the sun position regardless. With the sun-position fallback this gives the same single contiguous evening-to-morning block as before; SMARD's real data may instead produce several separate red spans spread across the day, all of which are summed together. This consistency matters: if SMARD drives the current mode decision, the window boundary for the energy balance (see below) must follow SMARD's own classification too — otherwise the balance could try to bridge to a sun-position point that (from SMARD's perspective) already lies in the past, wrapping around almost a full day instead of counting only the hours actually remaining.

Whether the reserve needs to be protected **right now** is decided — provided a PV forecast is available (see PV reference curve) — by a full energy-balance projection, not a fixed clock time:

```
Projection = current content + expected remaining PV yield − expected remaining consumption
             (both over the same window as the red reserve: from now to the
             first PV-surplus hour, whether the hours in between are red or not)
Reserve protected when Projection < red reserve
```

Both sides of the comparison must run over the same window to be comparable: if the red reserve ends at the first surplus hour (see above), the projection must end exactly there too — not at the next red hour, which with several separate red spans can lie much later and would then fold in swings from a far larger span than the reserve itself covers.

The remaining yield and remaining consumption are summed over the same window: from the current hour up to the next hour classified red — again from the same zone source as above (SMARD if active, otherwise sun position), not always rigidly from the sun position. The running hour counts only for its **remaining fraction** — the minutes still left until the top of the hour — not in full; since `dirt_shift` runs on a 15-minute schedule, this effectively gives quarter-hour precision at the edge of the window in normal operation, without the underlying hourly curves themselves needing finer resolution. If the current hour is already red, there is nothing left to bridge, so discharge is always free there anyway.

This balance is deliberately **independent of current content alone**: a full battery does not automatically guard against protection mode if expected remaining consumption exceeds expected remaining sun; an empty battery does not automatically have to protect if a good forecast closes the gap. Whether content has *currently* already dropped to the reserve is a separate, always-active check — it is what distinguishes yellow's `limit` (content still above the reserve, let the surplus flow out) from `stop` (content already at the reserve).

**Without a PV forecast** (curve never successfully computed), the decision falls back to `build_reserve_after` as a plain clock cutoff, exactly as before: before this time (default 13:30) discharge is always free, from then on the zone logic applies regardless of the energy-balance result. A curve without a radiation forecast still counts as a forecast (then simply unscaled) — only when no PV reference curve is available at all does the clock rule apply.

## Failsafe

`dirt_shift` is optional and must never block zeroinput's normal operation. If a run limits or stops the discharge (yellow/green/reserve protection), it additionally writes an "all-allowed" line (`100 100 99999`) 30 minutes later. While the script keeps running, the limit is renewed every 15 minutes; if it fails (cron failure, volkszähler unreachable, crash), the limit lifts itself after 30 minutes and zeroinput discharges freely again, as if `dirt_shift` did not exist. In the red window (no limit) everything is allowed anyway, so the single line suffices there.

---

## Exporting grid dirtiness

With SMARD active, `dirt_shift` can additionally log the current dirtiness value to volkszähler — on every run, if `vz_dirtiness_uuid` is set. The value is `(1 − ratio) × 100` (renewables/load for the current hour): the sign convention matches the installation's existing power channels (Import positive, Inverter negative) — the more positive, the dirtier (below-average renewable share); on a renewable surplus (ratio > 1) the value even goes negative, like a feed-in.

The write is a direct **HTTP POST** to volkszähler's middleware API, once per run: `http://{vz_host_port}/data/{vz_dirtiness_uuid}.json`, with the value and current timestamp. It reuses the same `vz_host_port` dirt_shift already uses for its other volkszähler queries — no vzlogger meter, no local file. `vz_dirtiness_uuid` must be a real channel UUID, created in volkszähler beforehand. A failed write (network error, wrong UUID) does not abort the run, it is only reported under `-v`.

---

## Output: timer.txt

`dirt_shift` writes `timer.txt` in the zeroinput format:

```
YYYY-MM-DD HH:MM:00  <discharge-W>  <ac-%>  <energy-Wh>
```

- Each line carries the real calendar date it was written for.
- **discharge-W** — discharge cap; `100` (percent) = no limit (red), `yellow_cap` (watts) = throttled (yellow), `000` = no battery discharge (green/stop).
- **ac-%** — inverter pass-through, always `100` (pvpt guaranteed).
- **energy-Wh** — energy budget; `99999` = effectively unlimited (discharge allowed), `000` = no budget (stop).

The three modes are thus: `100 100 99999` (red, or reserve not protected, no limit), `<yellow_cap> 100 99999` (yellow, capped), `000 100 000` (green/stop). The energy_Wh field stays unlimited (`99999`) even in yellow mode: the actual energy handed out is bounded not by this field but by the slot's own Wh budget (reserve/red-window logic). `yellow_cap` only limits instantaneous power, so short spikes can still be served from the battery without permanently exceeding the contingent.

Values > 100 are interpreted as watts, values ≤ 100 as percent — as in the existing zeroinput timer format. zeroinput's `discharge_times` parser reads the lines in order and applies each line's values as long as its timestamp is in the past, stopping at the first line whose timestamp is still in the future (that is where it breaks) — the active state is always that of the last already-past line. Once dirt_shift stops running and both lines eventually fall into the past, the loop no longer breaks and runs through to the end instead, so the state settles on the **last** line in the file. Since that last line is always the failsafe line (`FREE`), or the single, already-free line in 'free' mode, the state settles into 'all allowed' on its own — without the file needing to be rewritten again.

The written plan is short: the current quarter-hour slot in the chosen mode (free / capped / stop), and — if the mode limits or stops — an "all-allowed" line 30 minutes later as a failsafe.

`dirt_shift` runs every quarter hour (e.g. via cron) and rewrites the file each time with the current energy content and current zone.

---

## Configuration

`dirt_shift.conf` contains **only** the path to `zeroinput.conf` and dirt_shift's own parameters. Values that already live in `zeroinput.conf` are read from there rather than duplicated — `dirt_shift` never modifies `zeroinput.conf`.

Read from `zeroinput.conf` (read-only):

- **`discharge_t_file`** — the path of the timer file that zeroinput reads. `dirt_shift` writes exactly this file (resolved relative to zeroinput.conf). This guarantees writer and reader point at the same file. `discharge_timer` must additionally be enabled in `zeroinput.conf`, otherwise zeroinput ignores the file.

dirt_shift's own keys in `dirt_shift.conf`:

- `zeroinput_conf` — path to `zeroinput.conf` (default `../zeroinput.conf`, since dirt_shift usually lives in a subfolder of zeroinput)
- **`yellow_cap`** (watts, default 600) — discharge power cap for the yellow zone. Short-term household consumption is inherently unpredictable, so this is deliberately not a value derived from forecast data but a fixed, documented limit. It protects the red reserve from being drawn down by spikes, without changing the slot's Wh budgeting — unrelated to any inverter staging threshold in `zeroinput.conf`.
- `vz_host_port`, `vz_chans` — volkszähler host and channel UUIDs for the data.json API. Separate from zeroinput's `vz_channels`/`vzlogger_log_file`: dirt_shift uses the HTTP API for averages, the PV curve, and energy content, zeroinput the vzlogger FIFO for live control. Both access the same volkszähler; the UUID lists need not be identical.
- `vz_dirtiness_uuid` — real volkszähler channel UUID for the dirtiness-value export via HTTP POST (see Exporting grid dirtiness). Empty disables the export.
- `average_days` — days for the hourly average (default 7)
- `day_weights_pct` — per-day weighting in percent for the average, chronological: index 0 = oldest day (today minus `average_days`, i.e. the same weekday of the previous week), index −1 = yesterday. Weighting yesterday and the previous week's weekday more heavily captures the most recent trend and the weekday structure. The length must match `average_days`; on mismatch all days are weighted equally. All 100 = neutral.
- `reserve_pct` — percent of the `basic_load` demand across the red window that is reserved (default 90)
- `build_reserve_after` — time (HH:MM), a plain fallback value used only when no PV forecast is available (default 13:30). When a forecast is available, the energy balance decides instead (see Red reserve and energy balance), independent of the clock.
- `smard_enabled` — real CO₂-intensity zones from SMARD day-ahead data (Bundesnetzagentur; no API key needed) instead of the sun-position zones. `false` (default) uses only the sun-position profile. A failed SMARD query falls back to the sun-position profile for that run automatically.
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
2026-07-07 00:00:00 100 100 99999
```

(full discharge, full pass-through, effectively unlimited energy budget, dated with the current date). If not even `zeroinput.conf` is readable (timer path unknown), only the abort with an error message remains.

---

## Command-line options

- `-v` — verbose console output
- `-html` — HTML header/footer around the output
- `-debug` — more output (implies `-v`)
- `-avgnew` — forces a fresh query instead of the caches: the `basic_load` average, the PV reference curve, the radiation forecast, and the SMARD zones
