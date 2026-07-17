# verbose and debug console output
( -v -debug )
```
using cached averages from 2026-07-17 09:00
using cached PV curve from 2026-07-17 04:00
using cached radiation forecast from 2026-07-17 09:00
using cached SMARD zones from 2026-07-17 09:00
2026-07-17 09:30:02 query volkszähler for energy content:
min voltage 48.3 V, latest 48.6 V, battery content 22 Wh

hr  PV_curve  rad_Wm2  clr%   exp_PV basic_ld  balance  chg  dirt% zone    
 0         0        0     -        0      204        -    D    .86 red     
 1         0        0     -        0      199        -    D    .85 red     
 2         0        0     -        0      165        -    D    .85 red     
 3         0        0     -        0      183        -    D    .86 red     
 4         0        0     -        0      163        -    D    .88 red     
 5        10        0     -       10      235     -224    D    .90 red     
 6        94        2     2        2      288     -286    D    .87 red     
 7       218       61    24       53      280     -227    D    .79 red     
 8       458      139    33      152      259     -106    D    .67 green   
 9*     1306      122    21      277      808     -531    D    *55 green   
10      2156      190    27      578      703     -124    D     44 green   
11      2332      254    31      731      840     -109    D     35 green   
12      2298      237    27      623     1555     -932    D     29 green   
13      2300      264    30      679     1167     -488    D     26 green   
14      2315      316    36      840     1115     -275    D     26 green   
15      1923      302    38      722      998     -276    D     27 green   
16      1889      358    51      965      963       +2   !L     32 green   
17      1666      563   100     1659      932     +727    L     41 green   
18      1521      434   100     1521     1055     +465    L     54 green   
19      1129      258   100     1129     1111      +18    L     66 green   
20       638      180   100      638      743     -105    D     74 red     
21        49       74     -       49      412     -363    D     77 red     
22         0        6     -        0      354        -    D     75 red     
23         0        0     -        0      173        -    D     71 red     
                            -------- -------- --------
                               10628    14908    -4280        Ø 62

content 21 Wh   reserve(120%) 0 Wh   =>  mode: FREE
timer: 2026-07-17 09:30:00 100 100 -1
wallbox: dirt% 55 (<50)   %median 79 (<50%)
wallbox: content 21 - reserve 3177 - margin 538 = -3694 Wh
wallbox: should_on False   marker(before) False   action: none
dirt_shift done. 2026-07-17 09:30:03
```
