# dirt_shift — Overview

## Purpose

`dirt_shift` shifts the discharge of a PV home battery into the grid hours with the highest
CO₂ intensity. The German electricity mix is dirty in the evening, at night, and in the early
morning (little PV, high load, fossil peaking plants) and clean during the day. Being able to
choose *when* the battery covers consumption instead of the grid saves the most emissions in
the dirtiest hours. `dirt_shift` therefore steers the available battery content into exactly
those hours.

The program never touches the hardware itself: every fifteen minutes it writes a `timer.txt`,
which **zeroinput** reads and acts on. PV pass-through to the house is left untouched as a rule
— only battery discharge is limited.

## Data basis

Every run brings together: the **house load** (7-day average from volkszähler), the current
**battery content** (reconstructed from charge/discharge energy since the last sustained empty
state, hardened against brief voltage outliers and sensor faults), the **PV forecast**
(empirical system curve, scaled by Open-Meteo's radiation forecast), and the **grid CO₂
intensity** (SMARD's day-ahead forecast for renewable generation and load). Missing data is
cushioned in stages: if the load forecast fails, the most recent available day's profile
substitutes for it; if SMARD fails, a cache bridges one day. If even that is not enough, an
**emergency mode** models a relative dirtiness curve from solar and wind forecasts (Open-Meteo)
together with a fixed nationwide load profile — clearly flagged, never mistaken for real data.
Only when even the weather cannot be fetched does `dirt_shift` leave an "all-allowed" timer
behind, so zeroinput is never blocked by a stale limit. A reconstructed battery content that
falls physically impossibly below "empty" independently triggers a warning that roughly
distinguishes a faulty component (e.g. an inverter that requests power but does not deliver it)
from a conversion efficiency set too low.

## Red and green zones

Each hour's ratio of renewable generation to load yields a dirtiness value. Its median is taken
over the rolling 24-hour window starting at the current hour: the cleaner half becomes **green**,
the dirtier half **red**. The cut thus adapts to the actual spread instead of hanging on a fixed
threshold.

## Discharge control

A **reserve** is held back — the demand of the coming red hours up to the next PV charging
phase, minus the surplus expected before then (on a sunny day the battery fills itself anyway,
so the reserve is zero). The mode for each hour follows from that:

- **green, content above the reserve** → free discharge.
- **green, content below it** → no discharge (the house runs off the grid), so the content stays
  available for the dirty hours — but only if those are noticeably dirtier than now; otherwise
  the battery keeps covering the running consumption.
- **red** → free discharge if the content suffices; otherwise capped to the house's share, with
  the single dirtiest hour of the window served without limit.

The cap works two ways: as a power limit (watts) and as an energy budget per quarter hour
(measured against the actually measured PV). That pushes both a brief load spike and a
sustained higher load onto the grid instead of the battery.

## Optional extra features

**Wallbox.** `dirt_shift` can switch a wallbox's relay via a Tasmota device whenever one of
three — mutually independent — conditions holds: the hour ranks among the cleanest of the
window, the battery has surplus above its reserve, or the battery is full. If the reading a path
needs is missing (e.g. the battery content), only that path drops out; the others keep deciding.
Only if none of the three can decide is the relay left unchanged. A car has to be charged from
the grid one way or another — the point is to pick the cleanest hour for it. If the wallbox is
charging via the cleanliness path while the battery does not cover its reserve and has no
surplus either, its discharge is capped to the house's share, so the charging current comes from
the grid rather than draining the overnight reserve. Only a relay `dirt_shift` switched on itself
is switched off again; a manual charge is left alone.

**Precharge.** In the cleanest hours, PV surplus can be steered deliberately into the battery
instead of the house — but only when surplus would otherwise genuinely be lost because it no
longer fits the battery's free room. On a modestly sized battery this rarely engages. Inverter
pass-through is never throttled all the way to 0, so zeroinput always recognises the timer line
as valid.

## Operation

Invoked every fifteen minutes via cron. `-v` shows zone, battery level, reserve and mode;
`-debug` additionally shows the hourly overview table. All installation-specific values live in
`dirt_shift.conf`; shared values are read by `dirt_shift` from `zeroinput.conf` without being
changed there.
