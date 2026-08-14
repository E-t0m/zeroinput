# verbose and debug console output or logfile
( -v -debug )
```
=== 2026-08-14 11:00:03 ===
query volkszähler for 7 day consumption data:
0 	begin 2026-08-13 11:00:00 	end 2026-08-14 11:00:00 	rows: 25 : 0
1 	begin 2026-08-12 11:00:00 	end 2026-08-13 11:00:00 	rows: 26 : 1
2 	begin 2026-08-11 11:00:00 	end 2026-08-12 11:00:00 	rows: 26 : 2
3 	begin 2026-08-10 11:00:00 	end 2026-08-11 11:00:00 	rows: 26 : 3
4 	begin 2026-08-09 11:00:00 	end 2026-08-10 11:00:00 	rows: 26 : 4
5 	begin 2026-08-08 11:00:00 	end 2026-08-09 11:00:00 	rows: 26 : 5
6 	begin 2026-08-07 11:00:00 	end 2026-08-08 11:00:00 	rows: 26 : 6

hour	basic_load
0	230
1	218
2	242
3	210
4	194
5	262
6	340
7	690
8	941
9	1039
10	990
11	1142
12	1761
13	1325
14	1963
15	1686
16	1425
17	1216
18	1382
19	1059
20	869
21	663
22	355
23	215
using cached PV curve from 2026-08-14 04:00
SMARD ratio by hour (2026-08-14), median 0.49: {0: 0.49, 1: 0.5, 2: 0.49, 3: 0.46, 4: 0.42, 5: 0.36, 6: 0.34, 7: 0.41, 8: 0.54, 9: 0.7, 10: 0.85, 11: 0.99, 12: 1.08, 13: 1.12, 14: 1.1, 15: 1.01, 16: 0.85, 17: 0.63, 18: 0.38, 19: 0.19, 20: 0.14, 21: 0.17, 22: 0.21, 23: 0.25}
SMARD zone fetch failed for 2026-08-15: incomplete day (0/24 hours)
2026-08-14 11:00:22 query volkszähler for energy content:
min voltage 48.7 V, latest 53.4 V, battery content 4329 Wh

hr  PV_curve  rad_Wm2  clr%   exp_PV basic_ld  balance  chg  dirt% zone    
 0         0        0     -        0      230        -    D    .51 green   
 1         0        0     -        0      218        -    D    .50 green   
 2         0        0     -        0      242        -    D    .51 red     
 3         0        0     -        0      210        -    D    .54 red     
 4         0        0     -        0      194        -    D    .58 red     
 5         0        0     -        0      262        -    D    .64 red     
 6        47        0     0        0      340        -    D    .66 red     
 7       183       15    10       18      690     -672    D    .59 red     
 8       377      110    34      128      941     -812    D    .46 green   
 9      1549      245    51      782     1039     -256    D    .30 green   
10      2203      425    68     1501      990     +511    L    .15 green   
11*     2405      595    81     1950     1142     +807    L     *1 green   
12      2488      713    89     2218     1761     +457    L     -8 green   
13      2474      789    96     2376     1325    +1050   !L    -12 green   
14      2686      816   100     2686     1963     +723    L    -10 green   
15      2414      794   100     2414     1686     +727    L     -1 green   
16      2141      721   100     2141     1425     +715    L     15 green   
17      1838      607   100     1838     1216     +621    L     37 green   
18      1455      464   100     1455     1382      +73    L     62 red     
19      1020      305   100     1020     1059      -38    D     81 red     
20       366      145   100      366      869     -503    D     86 red     
21         7       24     -        7      663     -655    D     83 red     
22         0        0     -        0      355        -    D     79 red     
23         0        0     -        0      215        -    D     75 red     
                            -------- -------- --------
                               20900    20416     +483        Ø 43

content 4328 Wh   reserve(120%) 0 Wh   =>  mode: FREE
timer: 2026-08-14 11:00:00 100 100 -1
wallbox: dirt_ok    True    dirt% 1 (<51)   %median 2 (<51%)   mode free
wallbox: energy_ok  True    content 4328 - reserve 2120 - margin 538 = 1670 Wh
wallbox: voltage_ok False   min 53.2 V over 15 min (>=54.00 V, idle)
wallbox: should_on  True    marker(before) True   action: none
dirt_shift done. 2026-08-14 11:00:28
```
