# verbose and debug console output or logfile
( -v -debug )
```
=== 2026-09-17 10:15:02 ===
using cached averages from 2026-09-17 10:00
using cached PV curve from 2026-09-17 04:00
using cached radiation forecast from 2026-09-17 10:00, data until 2026-09-18 23:00
using cached SMARD zones from 2026-09-17 10:00, data until 2026-09-17 23:00
rolling median ratio 0.46 over 24 covered hours
2026-09-17 10:15:02 query volkszähler for energy content:
min voltage 47.8 V, latest 52.1 V, battery content 595 Wh

hr  PV_curve  rad_Wm2  clr%   exp_PV basic_ld  balance  chg  dirt% zone    
10      2228      211    46     1027      821     +206    L    *20 green   
11      2248      345    61     1368      565     +803    L      7 green   
12      2363      331    52     1232     1031     +201    L     -2 green   
13      2615      315    48     1254      824     +430   !L     -6 green   
14      2705      376    60     1610      639     +971    L     -5 green   
15      2524      404    72     1818      559    +1259    L      3 green   
16      2194      380    85     1855      794    +1061    L     16 green   
17      1638      343   100     1638      925     +713    L     35 green   
18      1226      234   100     1226      591     +635    L     53 green   
19       235      105   100      235      628     -393    D     60 red     
20         4       13     -        4      724     -720    D     59 red     
21         0        0     -        0      249        -    D     57 red     
22         0        0     -        0      167        -    D     55 red     
23         0        0     -        0      130        -    D     52 green   
 0         0        0     -        0      126        -    D    .69 red     
 1         0        0     -        0      121        -    D    .66 red     
 2         0        0     -        0      118        -    D    .65 red     
 3         0        0     -        0      120        -    D    .65 red     
 4         0        0     -        0      188        -    D    .65 red     
 5         0        0     -        0      327        -    D    .65 red     
 6         2        0     -        2      366     -364    D    .67 red     
 7       105        0     0        0      288        -    D    .64 red     
 8       360       28    19       69      360     -291    D    .51 green   
 9      1401      155    50      701      804     -103    D    .35 green   
                            -------- -------- --------
                               14039    11465    +2574        Ø 42

wallbox: dirt_ok    True    dirt% 20 (<46)   rank 25% (<26)
wallbox: energy_ok  False   content 595 - reserve 3057 - headroom 1075 = -3537 Wh (idle)
wallbox: voltage_ok False   min 51.4 V over 15 min (>=54.0 V, idle)
wallbox: should_on  True    marker(before) True   action: none
content 595 Wh (6%)   reserve(200%) 0 Wh [window: 0/1529-6279pv]   =>  mode: FREE
timer: 2026-09-17 10:15:00 100 100 -1
dirt_shift done. 2026-09-17 10:15:03
```
