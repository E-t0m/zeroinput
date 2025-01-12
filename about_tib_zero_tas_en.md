the -v erbose output looks like this

```
using cached volkszähler averages from 2025-01-12 15:00
2025-01-12 15:07 query volkszähler for energy content:
0 	begin 2025-01-12 00:00:00 1736636400000 	end 2025-01-12 15:07:55 1736690875000 	request duration: 0:00:00.157158 	rows: 5407
1 	begin 2025-01-11 00:00:00 1736550000000 	end 2025-01-12 15:07:55 1736690875000 	request duration: 0:00:00.313256 	rows: 12005
2 	begin 2025-01-10 00:00:00 1736463600000 	end 2025-01-12 15:07:55 1736690875000 	request duration: 0:00:00.475422 	rows: 19179
3 	begin 2025-01-09 00:00:00 1736377200000 	end 2025-01-12 15:07:56 1736690876000 	request duration: 0:00:00.654006 	rows: 26038
3 	begin 2025-01-09 11:00:12 1736416812000 	end 2025-01-12 15:07:57 1736690877000 	request duration: 0:00:07.335252 	rows: 77
minimum voltage 48.3 V, latest voltage 52.7 V, remaining battery content 2891 Wh
date time     price	set	average	sum
2025-01-13T08 47.74	2700	385	385
2025-01-13T07 42.84	632	316	701
2025-01-13T09 41.91	2700	490	1191
2025-01-13T17 38.92	2700	499	1690
2025-01-13T18 38.92	2700	553	2243
2025-01-12T18 38.64	885	553	2796
2025-01-12T19 38.29	675	450	3246
tibber price avg: 36.63 min: 33.09 max: 47.74 spread: 14.65 (31 %) lt: 36.63 ht: 35.17 
pvpt	 > 34.46 ¢, 90%lpt 
charge	 < 24.29 ¢, 76%lpt - 5 ¢ profit
set timer lt to max: 25.00
                     2025-01-12T00 31.14                                           o
                     2025-01-12T01 31.16                                           o
                     2025-01-12T02 31.51                                           o
                     2025-01-12T03 31.21                                           o
                     2025-01-12T04 31.31                                           o
                     2025-01-12T05 31.16                                           o
                     2025-01-12T06 31.41                                           o
                     2025-01-12T07 33.30                                             o
                     2025-01-12T08 34.84                                              o
                     2025-01-12T09 35.48                                               o
                     2025-01-12T10 35.21                                               o
                     2025-01-12T11 33.76                                             o
                     2025-01-12T12 33.45                                             o
                     2025-01-12T13 33.05                                             o
                     2025-01-12T14 33.66                                             o
now                  2025-01-12T15 35.32      0 PVpt                                   <
                     2025-01-12T16 37.01      0 PVpt                                     <
                     2025-01-12T17 37.97      0 PVpt                                     <
                     2025-01-12T18 38.64    885 PVpt                                      <
                     2025-01-12T19 38.29    675 PVpt                                      <
                     2025-01-12T20 37.24      0 PVpt                                     <
                     2025-01-12T21 36.36      0 PVpt                                    <
                     2025-01-12T22 35.61      0 PVpt                                   <
                     2025-01-12T23 34.86      0 PVpt                                  |
                     2025-01-13T00 34.57      0 PVpt                                  |
                     2025-01-13T01 33.69      0                                      |
                     2025-01-13T02 33.62      0                                      |
                     2025-01-13T03 33.14      0                                      |
                     2025-01-13T04 33.09      0                                      |
                     2025-01-13T05 34.29      0                                       |
                     2025-01-13T06 37.88      0 PVpt                                     <
                     2025-01-13T07 42.84    632 PVpt                                          <
                     2025-01-13T08 47.74   2700 PVpt                                               <
                     2025-01-13T09 41.91   2700 PVpt                                         <
                     2025-01-13T10 38.05      0 PVpt                                      <
                     2025-01-13T11 34.96      0 PVpt                                  |
                     2025-01-13T12 33.58      0                                      |
                     2025-01-13T13 33.45      0                                      |
                     2025-01-13T14 33.65      0                                      |
                     2025-01-13T15 37.64      0 PVpt                                     <
                     2025-01-13T16 38.16      0 PVpt                                      <
                     2025-01-13T17 38.92   2700 PVpt                                      <
                     2025-01-13T18 38.92   2700 PVpt                                      <
                     2025-01-13T19 37.82      0 PVpt                                     <
                     2025-01-13T20 35.85      0 PVpt                                   <
                     2025-01-13T21 35.50      0 PVpt                                   <
                     2025-01-13T22 34.94      0 PVpt                                  |
                     2025-01-13T23 33.37      0                                      |
done.
```
At first the consumption data is taken from volkszähler's database. This gets cached hourly.
PV power, inverter input, net demand, car, charger, climate is used to calculate a 7 day average profile. (similar to [profiler.py](https://github.com/E-t0m/zeroinput/blob/main/profiler.py))

Then the energy content gets fetched: It searches for the latest empty battery voltage and calculates the remaining battery content.

The tasmota timers (and switches) are set by thresholds.

The zeroinput timer file is calculated with descending hour prices as far as tibber allows the foresight.
The coming, price sorted hours get summed with their 7-day-average energy usage. This is done as long there is energy in the battery.
This information is written to zeroinput's timer file, which regulates the inverter.

PVpt is PV pass through, where the PV energy is directly transformed by the inverter, because of the small price gap to the next sheduled input hour.

tib_zero_tas.py should run every 10 min up to 60 min by cron.
