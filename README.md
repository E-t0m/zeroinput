# zeroinput
set power demand to zero
for soyosource gti inverter(s) and esmart3 MPPT charge controller on a single RS485 line.

This script uses a https://volkszaehler.org/ installation for energy meter input and data display.

All values are set for a 16s LiFePO4 battery with nominal 51.2 Volt.
Limits are set to 1 C current of 25Ah cells, which is 25 A discharge current.

![scheme](https://user-images.githubusercontent.com/110770475/185705907-b9e98c0f-1543-417c-aed6-432e5230b275.jpg)

##  An example how a day can look like in volkszaehler browser
- red shows the imported power from the net
- green is the generated power from the inverters
- yellow shows the PV power

![basic](https://user-images.githubusercontent.com/110770475/183761064-bd2632d8-4438-4288-b05c-e8126de78463.png)

There is an introduction in german language at https://forum.drbacke.de/viewtopic.php?t=5409

You must create chanels and adapt the vzlogger.conf for your own hardware and needs! See https://volkszaehler.org/

as root:
```
cd /home/vzlogger
wget https://raw.githubusercontent.com/E-t0m/zeroinput/main/zeroinput.py
wget https://raw.githubusercontent.com/E-t0m/esmart_mppt/master/esmart.py
chmod 744 /home/vzlogger/*py
chown vzlogger: /home/vzlogger/*py
su vzlogger
mkdir /tmp/vz
touch /tmp/vz/soyo.log
mkfifo /tmp/vz/vzlogger.fifo
python3 /home/vzlogger/zeroinput.py -v (cancel with ctrl+c)
or if you know screen (man screen):
screen -dmS zeroinput nice -1 python3 /home/vzlogger/zeroinput.py -v (screen -r to attach, ctrl-a+ctrl-d deattach)
```
