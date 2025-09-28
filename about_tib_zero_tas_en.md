the -v erbose output looks like this

```
using cached volkszähler averages from 2025-09-28 17:14
2025-09-28 17:22 query volkszähler for energy content:
0 	begin 2025-09-28 00:00:00 1759010400000 	end 2025-09-28 17:22:30 1759072950000 	request duration: 0:00:00.777906 	rows: 39691
1 	begin 2025-09-27 00:00:00 1758924000000 	end 2025-09-28 17:22:30 1759072950000 	request duration: 0:00:01.739430 	rows: 88420
	begin 2025-09-27 12:14:35 1758968075000 	end 2025-09-28 17:22:32 1759072952000 	request duration: 0:00:02.370159 	rows: 30
minimum voltage 48.2 V, latest voltage 52.9 V, remaining battery content 6210 Wh
date time     price	 set	average	 sum
2025-09-29T07 47.62	2700	 829	 829
2025-09-29T08 41.05	2700	 684	1513
2025-09-28T19 37.61	1146	 603	2116
2025-09-29T06 37.25	 671	 373	2489
2025-09-28T18 36.78	1097	 645	3134
2025-09-28T20 36.18	 790	 494	3628
2025-09-29T09 35.72	2700	 612	4240
2025-09-28T21 34.70	 610	 436	4676
2025-09-28T17 33.79	 519	 399	5075
2025-09-28T22 33.36	 294	 245	5320
2025-09-29T05 32.90	 226	 205	5525
2025-09-28T23 32.58	 186	 186	5711
2025-09-29T00 32.01	 152	 152	5863
2025-09-29T10 31.93	2700	 517	6380
tibber price avg: 34.82 min: 31.30 max: 47.62 spread: 16.32 (34 %) 
pvpt	 > 30.01 ¢, 94%lpt  
charge	 < 24.41 ¢, 82%lpt - 2 ¢ profit
                     2025-09-28T00 32.80             |                                o
                     2025-09-28T01 32.53             |                                o
                     2025-09-28T02 32.42             |                                o
                     2025-09-28T03 32.08             |                                o
                     2025-09-28T04 32.83             |                                o
                     2025-09-28T05 32.80             |                                o
                     2025-09-28T06 33.20             |                                 o
                     2025-09-28T07 33.78             |                                 o
                     2025-09-28T08 32.95             |                                o
                     2025-09-28T09 32.50             |                                o
                     2025-09-28T10 30.96             |                              o
                     2025-09-28T11 25.65             |                         o
                     2025-09-28T12 23.23 ¢           |                       o
                     2025-09-28T13 22.21 ¢           |                      o
                     2025-09-28T14 22.74 ¢           |                      o
                     2025-09-28T15 28.49             |                            o
                     2025-09-28T16 31.43             |                               o
now                  2025-09-28T17 33.79    519 PVpt |                                 o
                     2025-09-28T18 36.78   1097 PVpt |                                    o
                     2025-09-28T19 37.61   1146 PVpt |                                     o
                     2025-09-28T20 36.18    790 PVpt |                                    o
                     2025-09-28T21 34.70    610 PVpt |                                  o
                     2025-09-28T22 33.36    294 PVpt |                                 o
                     2025-09-28T23 32.58    186 PVpt |                                o
                     2025-09-29T00 32.01    152 PVpt |                                o
                     2025-09-29T01 31.75      0 PVpt |                               o
                     2025-09-29T02 31.65      0 PVpt |                               o
                     2025-09-29T03 31.66      0 PVpt |                               o
                     2025-09-29T04 31.70      0 PVpt |                               o
                     2025-09-29T05 32.90    226 PVpt |                                o
                     2025-09-29T06 37.25    671 PVpt |                                     o
                     2025-09-29T07 47.62   2700 PVpt |                                               o
                     2025-09-29T08 41.05   2700 PVpt |                                         o
                     2025-09-29T09 35.72   2700 PVpt |                                   o
                     2025-09-29T10 31.93   2700 PVpt |                               o
                     2025-09-29T11 31.30      0 PVpt |                               o
                     2025-09-29T12 30.56             |                              o
                     2025-09-29T13 30.36             |                              o
                     2025-09-29T14 31.11             |                               o
                     2025-09-29T15 31.59             |                               o
                     2025-09-29T16 33.09             |                                 o
                     2025-09-29T17 37.95             |                                     o
                     2025-09-29T18 57.21             |                                                         o
                     2025-09-29T19 69.60             |                                                                     o
                     2025-09-29T20 41.14             |                                         o
                     2025-09-29T21 35.93             |                                   o
                     2025-09-29T22 34.08             |                                  o
                     2025-09-29T23 32.34             |                                o
disabled tasmota timer
done. 2025-09-28 17:22:35
```
At first the consumption data is taken from volkszähler's database. This gets cached hourly.
PV power, inverter input, net demand, car, charger, climate is used to calculate a 7 day average profile. (similar to [profiler.py](https://github.com/E-t0m/zeroinput/blob/main/profiler.py))

Then the energy content gets fetched: It searches for the latest empty battery voltage and calculates the remaining battery content.

The tasmota timers (and switches) are set by price thresholds.

The zeroinput timer file is calculated with descending hour prices as far as tibber allows the foresight.
The coming, price sorted hours get summed with their 7-day-average energy usage. This is done as long there is energy in the battery.
This information is written to zeroinput's timer file, which rules the inverter.

PVpt is PV pass through, where the PV energy is directly transformed by the inverter, because of the small price gap to the next sheduled hour with input from battery.

tib_zero_tas.py should run every 15 minutes:
```
14,29,44,59 * * * *	python3 /home/vzlogger/tibber/tib_zero_tas.py -v -html > /home/vzlogger/tibber/tib_zero_tas.html
```
i write the output to a html file which is linked (as root) from the vzlogger htdocs folder:
```
ln -s /home/vzlogger/tib_zero_tas.html /home/pi/volkszaehler.org/htdocs
```
It's found at http ://the-usual-volkszaehler-site /tib_zero_tas.html
