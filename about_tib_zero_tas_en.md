the -v erbose output looks like this

```
using cached volkszähler averages from 2025-03-23 17:05
2025-03-23 17:37 query volkszähler for energy content:
0 	begin 2025-03-23 00:00:00 1742684400000 	end 2025-03-23 17:37:53 1742747873000 	request duration: 0:00:00.964860 	rows: 39454
1 	begin 2025-03-22 00:00:00 1742598000000 	end 2025-03-23 17:37:54 1742747874000 	request duration: 0:00:02.320856 	rows: 96594
1 	begin 2025-03-22 08:18:56 1742627936000 	end 2025-03-23 17:37:57 1742747877000 	request duration: 0:00:03.760152 	rows: 34
minimum voltage 48.2 V, latest voltage 53.4 V, remaining battery content 8570 Wh
date time     price	set	average	sum
2025-03-24T07 40.74	2700	959	959
2025-03-24T08 40.45	2700	928	1887
2025-03-23T19 38.15	1421	748	2635
2025-03-24T06 38.03	833	463	3098
2025-03-23T20 37.92	1411	830	3928
2025-03-23T18 37.85	1597	998	4926
2025-03-24T09 37.30	2700	1177	6103
2025-03-23T21 36.66	895	639	6742
2025-03-23T22 36.24	641	493	7235
2025-03-23T17 35.97	746	622	7857
2025-03-24T05 35.45	384	349	8206
2025-03-23T23 34.79	272	272	8478
2025-03-24T10 34.42	2700	1023	9501
tibber price avg: 36.10 min: 32.97 max: 40.74 spread: 7.77 (19 %) 
pvpt	 > 32.35 ¢, 94%lpt 
charge	 < 26.47 ¢, 82%lpt - 2 ¢ profit
                     2025-03-23T00 22.82 ¢           |                      o
                     2025-03-23T01 23.45 ¢           |                       o
                     2025-03-23T02 24.01 ¢           |                        o
                     2025-03-23T03 23.99 ¢           |                       o
                     2025-03-23T04 24.15 ¢           |                        o
                     2025-03-23T05 24.17 ¢           |                        o
                     2025-03-23T06 24.15 ¢           |                        o
                     2025-03-23T07 24.08 ¢           |                        o
                     2025-03-23T08 23.45 ¢           |                       o
                     2025-03-23T09 23.94 ¢           |                       o
                     2025-03-23T10 22.83 ¢           |                      o
                     2025-03-23T11 22.33 ¢           |                      o
                     2025-03-23T12 22.80 ¢           |                      o
                     2025-03-23T13 21.79 ¢           |                     o
                     2025-03-23T14 22.56 ¢           |                      o
                     2025-03-23T15 26.25 ¢           |                          o
                     2025-03-23T16 31.24             |                               o
now                  2025-03-23T17 35.97    746 PVpt |                                   o
auto_off             2025-03-23T18 37.85   1597 PVpt |                                     o
                     2025-03-23T19 38.15   1421 PVpt |                                      o
                     2025-03-23T20 37.92   1411 PVpt |                                     o
                     2025-03-23T21 36.66    895 PVpt |                                    o
                     2025-03-23T22 36.24    641 PVpt |                                    o
                     2025-03-23T23 34.79    272 PVpt |                                  o
                     2025-03-24T00 34.34      0 PVpt |                                  o
                     2025-03-24T01 33.96      0 PVpt |                                 o
                     2025-03-24T02 33.32      0 PVpt |                                 o
                     2025-03-24T03 32.97      0 PVpt |                                o
                     2025-03-24T04 33.33      0 PVpt |                                 o
                     2025-03-24T05 35.45    384 PVpt |                                   o
                     2025-03-24T06 38.03    833 PVpt |                                      o
                     2025-03-24T07 40.74   2700 PVpt |                                        o
                     2025-03-24T08 40.45   2700 PVpt |                                        o
                     2025-03-24T09 37.30   2700 PVpt |                                     o
                     2025-03-24T10 34.42   2700 PVpt |                                  o
                     2025-03-24T11 33.97      0 PVpt |                                 o
                     2025-03-24T12 33.18             |                                 o
                     2025-03-24T13 33.20             |                                 o
                     2025-03-24T14 33.87             |                                 o
                     2025-03-24T15 34.98             |                                  o
                     2025-03-24T16 36.77             |                                    o
                     2025-03-24T17 39.54             |                                       o
                     2025-03-24T18 46.42             |                                              o
                     2025-03-24T19 44.16             |                                            o
                     2025-03-24T20 40.45             |                                        o
                     2025-03-24T21 37.68             |                                     o
                     2025-03-24T22 36.14             |                                    o
                     2025-03-24T23 34.90             |                                  o
done. 2025-03-23 17:38:00
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
