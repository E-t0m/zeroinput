the -v erbose output looks like this

```
query volkszähler for consumption data:
0 	begin 2024-11-20 16:00:00 1732114800000 	end 2024-11-21 15:59:00 1732201140000 	request duration: 0:00:04.035486 	rows: 25
1 	begin 2024-11-19 16:00:00 1732028400000 	end 2024-11-20 15:59:00 1732114740000 	request duration: 0:00:04.814463 	rows: 25
2 	begin 2024-11-18 16:00:00 1731942000000 	end 2024-11-19 15:59:00 1732028340000 	request duration: 0:00:03.845095 	rows: 25
3 	begin 2024-11-17 16:00:00 1731855600000 	end 2024-11-18 15:59:00 1731941940000 	request duration: 0:00:03.875253 	rows: 25
4 	begin 2024-11-16 16:00:00 1731769200000 	end 2024-11-17 15:59:00 1731855540000 	request duration: 0:00:03.851959 	rows: 25
5 	begin 2024-11-15 16:00:00 1731682800000 	end 2024-11-16 15:59:00 1731769140000 	request duration: 0:00:04.018686 	rows: 25
6 	begin 2024-11-14 16:00:00 1731596400000 	end 2024-11-15 15:59:00 1731682740000 	request duration: 0:00:03.858579 	rows: 25
query volkszähler for energy content:
0 	begin 2024-11-21 00:00:00 1732143600000 	end 2024-11-21 16:03:36 1732201416000 	request duration: 0:00:00.159054 	rows: 6295
1 	begin 2024-11-20 00:00:00 1732057200000 	end 2024-11-21 16:03:36 1732201416000 	request duration: 0:00:00.329424 	rows: 13311
1 	begin 2024-11-20 08:52:47 1732089167000 	end 2024-11-21 16:03:37 1732201417000 	request duration: 0:00:02.939414 	rows: 33
minimum voltage 48.3 V, latest voltage 52.3 V, remaining battery content 2283 Wh
date time     price	set	average	sum
2024-11-21T17 41.00	900	452	452
2024-11-21T16 40.14	490	408	860
2024-11-21T18 38.51	428	389	1249
2024-11-21T19 37.65	402	402	1651
2024-11-22T08 36.46	359	359	2010
2024-11-22T09 36.16	463	463	2473
tibber price avg: 33.55 min: 28.48 max: 41.00 spread: 12.52 (31 %) lt: 33.55 ht: 32.30, 92%lpt: 33.27
set lt to max: 31.50
                     2024-11-21T00 30.50                                        o
                     2024-11-21T01 30.07                                        o
                     2024-11-21T02 29.64                                       o
                     2024-11-21T03 29.33                                       o
                     2024-11-21T04 29.34                                       o
                     2024-11-21T05 30.32                                        o
                     2024-11-21T06 32.07                                          o
                     2024-11-21T07 33.77                                           o
                     2024-11-21T08 35.14                                             o
                     2024-11-21T09 35.38                                             o
                     2024-11-21T10 34.47                                            o
                     2024-11-21T11 33.06                                           o
                     2024-11-21T12 32.83                                          o
                     2024-11-21T13 33.69                                           o
                     2024-11-21T14 36.17                                              o
                     2024-11-21T15 37.74                                               o
now                  2024-11-21T16 40.14  490 PVpt                                        <
T off: 3 4           2024-11-21T17 41.00  900 PVpt                                         <
                     2024-11-21T18 38.51  428 PVpt                                      <
                     2024-11-21T19 37.65  402 PVpt                                     <
                     2024-11-21T20 34.94    0 PVpt                                  <
                     2024-11-21T21 33.96    0 PVpt                                 <
                     2024-11-21T22 33.94    0 PVpt                                 <
                     2024-11-21T23 32.10    0                                     |
                     2024-11-22T00 33.13    0                                      <
                     2024-11-22T01 31.95    0                                    |
 T on: 1 2           2024-11-22T02 30.96    0                                   >
                     2024-11-22T03 30.19    0                                   >
                     2024-11-22T04 29.86    0                                  >
                     2024-11-22T05 30.28    0                                   >
                     2024-11-22T06 33.25    0                                      <
                     2024-11-22T07 35.42    0 PVpt                                   <
                     2024-11-22T08 36.46  359 PVpt                                    <
                     2024-11-22T09 36.16  463 PVpt                                    <
                     2024-11-22T10 35.47    0 PVpt                                   <
                     2024-11-22T11 33.22    0                                      <
                     2024-11-22T12 31.83    0                                    |
                     2024-11-22T13 30.65    0                                   >
                     2024-11-22T14 30.76    0                                   >
                     2024-11-22T15 31.96    0                                    |
                     2024-11-22T16 32.55    0                                     <
                     2024-11-22T17 34.60    0 PVpt                                  <
                     2024-11-22T18 35.29    0 PVpt                                   <
                     2024-11-22T19 34.64    0 PVpt                                  <
                     2024-11-22T20 31.95    0                                    |
                     2024-11-22T21 31.97    0                                    |
                     2024-11-22T22 30.41    0                                   >
                     2024-11-22T23 28.48    0                                 >
done.

```
At first the consumption data is taken from volkszähler's database. This is cached hourly.
PV power, inverter input, net demand and auto (car / charger) is used to calculate a 7 day average profile. (similar to [profiler.py](https://github.com/E-t0m/zeroinput/blob/main/profiler.py))

Then the energy content gets fetched: It searches for the latest empty battery voltage and calculates the remaining battery content.

The tasmota timers (and switches) are set by thresholds.

The zeroinput timer file is calculated with descending hour prices as far as tibber allows the foresight.
The coming, price sorted hours get summed with their 7-day-average energy usage. This is done as long there is energy in the battery.
This information is written to zeroinput's timer file, which regulates the inverter.

PVpt is PV pass through, where the PV energy is directly transformed by the inverter, because of the small price gap to the next sheduled input hour.

tib_zero_tas.py should run every 10 min up to 60 min by cron.
