# zeroinput
set power demand to zero
for soyosource gti inverter(s) and esmart3 MPPT charge controller on a single RS485 line.

This script uses a https://volkszaehler.org/ installation for energy meter input and data display.

All values are set for a 16s LiFePO4 battery with nominal 51.2 Volt.
Limits are set to 1 C current of 25Ah cells, which is 25 A discharge current.

There is an introduction in german language at https://forum.drbacke.de/viewtopic.php?t=5409

##  An example how a day can look like in volkszaehler browser
- red shows the imported power from the net
- green is the generated power from the inverters
- yellow shows the PV power

![basic](https://user-images.githubusercontent.com/110770475/183761064-bd2632d8-4438-4288-b05c-e8126de78463.png)
