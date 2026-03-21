# Balanced zero feed-in with the house electricity meter

Hello everybody,
I would like to present my own private system and regulation for zero feed-in.

The own consumption in the house is covered **first**. If there is **then** still power left, the **battery** will also be charged.

Or: settings in the [timer file](https://github.com/E-t0m/zeroinput/blob/main/timer.txt) set other values.

## Structure
![scheme](https://github.com/user-attachments/assets/8d767329-15f9-4098-b4b4-a9e70d0d98c9)

## The components
- Soyosource GTN 1200W (max. 900 W in battery mode), Please make sure that this device can be connected to the grid in your country. [guidance and specification](https://www.mediafire.com/file/kvn0jvyuubd3364/soyosource1.200W%252BGrid%252BTie%252BInverter.pdf/file)
- Victron Solar and / or eSmart3 MPPT charge controller [guidance and specification](https://www.solarcontroller-inverter.com/download/18122110445698.html), [manufacturer page](https://www.ipandee.com/products/mppt-solar-charge-controller-esmart-12v-24v-36v-48v-20a-60a/), [Configuration software Windows](http://www.mediafire.com/file/mt77gai7xxzig1g/install_SolarMate_CS_Windows.exe)
- Raspberry Pi (or other (micro)computer)
- Reading head for the electricity meter (modern measuring device) or volkszaehler compatible energymeter
* 16s LiFePO4 battery

## Functionality
The system basically works like an island:
PV modules, battery and the Soyosource grid inverter (as a load) are connected to the eSmart3 charge controller.

(For direct consumption recording. Some Victron controllers have no load output.
Further grid inverters must be connected directly to the battery because of the 40A limit of the load output of the eSmart3.
The regulation also works without an eSmart3 controller and without an inverter as a load.)

The feed-in power of the Soyosource inverters can be regulated via RS485 (Modbus).
Measuring clamps are also offered for this, but you cannot balance phases with them.
The Raspberry Pi, which runs the [Volkszaehler](http://volkszaehler.org) software, is responsible for the regulation.
The Volkszähler reads the second-accurate, calibrated! real consumption data by means of a reading head on the house electricity meter.
The OBIS data record with the id "1-0:16.7.0" indicates the current balanced consumption.
With negative values when feeding, even with counters with a backstop! At least that's what mine (DD3 BZ06) does.
Without "16.7.0" from the meter the regulation is not possible! Check this first.

This is where my script [zeroinput](https://github.com/E-t0m/zeroinput) comes into play,
it reads the consumption data from the home electricity meter via the [Volkszaehler](http://volkszaehler.org) and calculates the necessary feed-in power to set the meter to zero.
In principle, the script would also work without a Volkszähler and could read the reading head itself.
Then it would run on much "smaller" hardware, but without **monitoring** it would be too risky for me.
The "unusual way" of redirecting the log file instead of using the Volkszaehler's own methods (database access via network) increases the reliability considerably.
Even if the database of the Volkszaehler crashes, the script continues to work uninterrupted! I already had that.
If you don't want to lay a cable to the meter, you could use a WIFI read head and transmit the consumption data via the WLAN network.

In practice, **the value on the meter fluctuates minimally around 0**, by the way, my "smart meter" also shows feed-in without a minus sign as a positive value on its display.
(there are A- and A+ with arrows, these show demand / export)

## Functions
The [script](https://github.com/E-t0m/zeroinput) has these functions:
- Time-controlled battery discharge and inverter power
- Automatic switching between one and multiple inverters
- Battery undervoltage protection below 48V
- Power adjustment battery from 48 V to 51 V, using control curve, possible total power always plus PV
- Possible export feed-in from 54.5 V, "gives away" energy to the grid
- Minimum power
- Maximum power
- Automatic adjustment or permanent shifting of the zero line towards demand or export
- Correction of battery cable loss
- Alarm for increased external (e.g. battery) or internal temperature of the esmart3
- Ramp mode for high changes in consumption
- Suppression of the oscillation of the control loop

These values **can and should** be **adjusted** to the respective system and battery size!
Of course you could also integrate other charge controllers, such as Epever. The battery voltage and PV power are very important values for the control!
Any other grid inverter can also be used if it can be regulated.
A controllable DC-DC converter on a micro-inverter is also conceivable.
The "hard" limit values for undervoltage and overvoltage of the battery must be set both in all charge controller(s) and in all grid inverter(s).

## Legal
As far as I can tell, you have to register the system described here in Germany with both the network operator and the market master data register,
if you want to comply with all legal regulations. But you can't! Because in plain language:
The mentioned Soyosource inverter may **not** be connected to the power grid in Germany because of the **missing certification**.

## Examples of the regulation
The values for PV (yellow, power of the PV modules) and Soyo P (green, current fed in) are displayed **negated**!
The script supplies the data for PV, Soyo P and battery U (red, battery voltage), whereby Soyo P is calculated.
PV and battery U are read by the esmart3 controller and passed on to the Volkszähler for display.
This creates a one-second time offset to the curves of the house electricity meter.

### This is what a fairly **good day with high consumption** looks like
![much consumption](https://user-images.githubusercontent.com/110770475/204105529-4d6d03e1-ca13-4224-8272-4995115232d0.png)
(2 soyosource gti)
The daily values were: PV generation 7.7 kWh, feed-in 7.3 kWh, deliberately given away 0 kWh.
On this day, the oven, microwave, well pump, split air conditioning, and so on were running. At the end of the day, the battery was already empty.
You can see the on and off regulation in the morning and evening along the PV curve. Around 11 the maximum feed.
After that and very nice at about 5:30 p.m. the power adjustment for the battery power.

### A sunny day with **low consumption**
![little consumption](https://user-images.githubusercontent.com/110770475/204105552-fbbc1f4d-ab04-483d-a6ea-ae0f934cab16.png)
(2 soyosource gti)
The daily values were: PV generation 6.1 kWh, feed-in 5.7 kWh, deliberately given away 0.9 kWh.
The largest consumer was the split air conditioning. The battery charge lasted far into the night.
Here you can see the adjustment in the morning and the night limit in the evening.
The dark green area is excess current because the battery - see voltage curve - is full.
(Meanwhile, the overfeed in the standard setting is much lower!)

### **A cloudy day**
![very cloudy day](https://user-images.githubusercontent.com/110770475/204105585-13a50eb1-87cf-4dbc-8e62-469527aed402.jpg)

As long as the power of the PV does not exceed the consumption, all power goes directly to the grid inverter.
The battery is not charging. Essentially, this corresponds to the mode of operation of a module/micro-inverter.

### **Washing machine in heating phase**
![60 degrees wash](https://user-images.githubusercontent.com/110770475/204105605-2a70356a-90d3-4a8a-a7a1-fddb570a9e3c.png)
(2 soyosource gti)
The data in the table refer to the entire visible section.
Here the "Sum L1+L2+L3" (OBIS "1-0:16.7.0") is displayed in black.
Above the zero line, it corresponds almost exactly to the red line for the network reference ("1-0:1.8.0").
The part below the zero line is the actual feed into the power grid, i.e. energy that goes "through the meter into the grid".
This is not "calculated" by the counter, it would be the second direction in a bidirectional counter.
In total, that was 430 Wh paid purchase and 418 Wh "physical purchase". So 12 Wh were fed in without remuneration.
The continuous wave from 10:10 comes from the constantly running drum motor, the script follows this increasing consumption.
But since the motor also suddenly switches off again, the inertia of the control results in an actual feed-in.
The ripples in the green line come from outside the heating phase due to this inertia.
During the heating phase, the ripple comes from the power adjustment to the battery voltage. (The washing machine just ran too early, the battery was barely charged.)
The drop in performance at around 10:33 a.m. is due to the restart of the MPP tracker in the charge controller. (Green and Yellow)
The increase in PV power can also be seen at the moment when the heating phase begins.
Conversely, the PV power decreases with slowly increasing battery voltage after the heating phase.

### **Milk coffee with microwave and induction plate** - for advanced users
![milk coffee](https://user-images.githubusercontent.com/110770475/204105626-c05746c4-1a6c-4252-910e-d2083dae432b.jpg)
(2 soyosource gti, without ramp mode!)
The red areas are the purchased reference. The green areas the own feed.
The gray areas are the inertial overfeed, energy fed in for free.

### Another history graph with just **one Soyosource**
![single phase](https://user-images.githubusercontent.com/110770475/204106401-e274ba31-8ad7-48a7-9975-7f3d39a58db0.jpg)
(1 soyosource gti, without ramp mode!)

### Clocking the battery empty
![leertakten](https://github.com/E-t0m/zeroinput/assets/110770475/59e0d728-b6f5-4dae-9b3c-78c18e5cba8e)
(3 soyosource gti)
Here you can see how the oven clocks the battery empty.
The following components of the regulation interact:
- Limitation of the discharge current of the battery, here 2kW from the battery + PV power
- Ramp mode, for large jumps in consumption
- Battery voltage correction
- Power adjustment to the battery voltage, the discharge curve of LFP falls rapidly

### **Fluctuations** in a rather quiet phase
![schwankungen_mit_zeroshift](https://github.com/user-attachments/assets/9b7ab215-dbf8-4942-87af-356f95d4f6e5)
The black values show the fluctuations in consumption/feed-in.
The feed-in - from one inverter - was about 150 W in this section.
Purple shows the automatic "raising of the zero line" when the feed-in increases due to changing consumption.

**The output of the script**, updated every second:
```
port          name        PV W   bat V   bat I   mode  P load  T int  T ext         
all           combined     842   52.97   15.80            759                       
/dev/ttyACM0  esmart 60    265   53.10    5.00   MPPT             25      8 out     
/dev/ttyACM1  esmart 40    311   52.80    5.90   MPPT     253     32     18 bat     
/dev/ttyACM2  VE 150/35    266   53.00    4.90   BULK                               

timer active: bat discharge 0 %, energy 0/0 Wh, inverter 100 % 

voltage correction 53.3 V, dif -0.3 V
no saw detected
input history [782, 775, 768, 759] 	1:2  1.2 %	 3:4  0.9 %

meter  339 W (auto shift 0 W import), interval 1.00 s, 14:22:24
inverter  759 W limited, PV -94 W, no battery discharge

1:  power request 3 x 253 W
2:  power request 3 x 253 W
1:  /dev/ttyACM0 : esmart 60 status request
1:  /dev/ttyACM1 : esmart 40 status request
REC /dev/ttyACM2 : VE 150/35 delay 1.00 s
REC /dev/ttyACM0 : esmart 60 delay 2.93 s
2:  /dev/ttyACM0 : esmart 60 status request
REC /dev/ttyACM1 : esmart 40 delay 2.93 s
2:  /dev/ttyACM1 : esmart 40 status request

```
The output works both in the terminal (screen, see below) and / or via **web browser**.
For this, create a link from the htdocs folder of the Volkszähler and start zeroinput.py with the -web parameter.
The page is then accessible in the browser at http://usual-address-of-the-volkszaehler **/zeroinput.html**
```
ln -s /home/vzlogger/zeroinput.html /home/pi/volkszaehler.org/htdocs
```

## Measurement accuracy
As for the accuracy of the data from the esmart3, the author of the [esmart3 library](https://github.com/skagmo/esmart_mppt), which I use modified, [published a review](https://skagmo.com/page.php?p=documents%2F04_esmart3_review).
According to my observation, the power fed in by the Soyosource Inverter corresponds quite exactly with the requested value.
There is also the delayed response time (ramp speed) of 400 W/s. So the Soyo takes 2+ seconds to go from 0 to 100% power. (this is intentional, not a bug)
That's why I just controlled the Soyos in parallel to have the shortest possible response time, with more Soyos it is even better, but only one works also!

## Efficiency
The output of the script above shows: injected power 643 W, while the esmart shows 692 W load. That gives ~93% efficiency at that moment.
Of course, charging and discharging the battery also costs energy.
With the example data of the days (above) an overall efficiency can be calculated:
- PV generation 7.7 kWh, feed-in 7.3 kWh, results in ~ 95%
- PV generation 6.1 kWh, feed-in 5.7 kWh, results in ~ 93%

The more energy goes through the battery, the worse the efficiency of the entire system.

## Building instructions
- Bring the electricity meter with PIN to (extended) data output if necessary. The PIN is available from the metering point operator or network operator (not electricity provider).
There is a [practical app](https://play.google.com/store/apps/details?id=de.bloggingwelt.blinkeingabestromzaehler) for PIN entry for the impatient.
- Get the Volkszähler working. [Instructions](https://wiki.volkszaehler.org/howto/getstarted), [the forum](https://www.photovoltaikforum.com/board/131-volkszaehler-org/) ***Without Volkszähler the script doesn't run!*** So start with that first.
- It makes a lot of sense to assign a [own, fixed device name](https://wiki.volkszaehler.org/hardware/controllers/ir-schreib-lesekopf-usb-ausgang) to the IR read head and RS485 adapter using a udev rule.
For my devices there are .rules files in /dev/udev/rules.d/ with these rules:
```
SUBSYSTEMS=="usb-serial", DRIVERS=="cp210x", SYMLINK+="lesekopf"
SUBSYSTEMS=="usb-serial", DRIVERS=="ch341-uart", SYMLINK+="rs485"
```
Or with identical devices separated by the connector on the Raspi:
```
SUBSYSTEMS=="usb" ATTRS{devpath}=="1.1" SYMLINK+="rs485a"
SUBSYSTEMS=="usb" ATTRS{devpath}=="1.3" SYMLINK+="rs485b"
```
- Assemble all the devices as described above.
- Connect the RS485 port of the Raspi (usually a USB stick with clamps) to the RS485 ports of Soyo and esmart3: A+ to A+, B- to B-.
- Modify the Volkszähler for zero feed-in a bit.

If your own Volkszähler runs successfully, then channels can be created according to this [vzlogger.conf](https://github.com/E-t0m/zeroinput/blob/main/vzlogger.conf).
In any case, ***"identifier": "1-0:16.7.0\*255" and "verbosity": 15*** must be included so that the script can calculate with them.
The path for the "log" in vzlogger.conf must also be adjusted: "/tmp/vz/vzlogger.fifo"
Although it is not necessary for operation, the [handling of data quantities](https://wiki.volkszaehler.org/howto/datenmengen) should be observed, otherwise "the database will overflow at some point"!

```
as root:
apt install python3-serial
cd /home/vzlogger
wget https://raw.githubusercontent.com/E-t0m/zeroinput/main/zeroinput.py
chmod 744 /home/vzlogger/zeroinput.py
chown vzlogger: /home/vzlogger/zeroinput.py
su vzlogger
mkdir /tmp/vz
touch /tmp/vz/soyo.log
mkfifo /tmp/vz/vzlogger.fifo
python3 /home/vzlogger/zeroinput.py -v (cancel with ctrl+c)
or if you know screen (man screen):
screen -dmS zeroinput nice -1 python3 /home/vzlogger/zeroinput.py -v -web (screen -r "to open", ctrl-a and ctrl-d "close")
```
(Of course you can also use **git**.)

Then again in another terminal - as root - restart the vzlogger
```systemctl restart vzlogger```

To start the script **automatically when booting up the Raspi**, use
```
su vzlogger
crontab -e
```
this line:
```
@reboot mkdir /tmp/vz; touch /tmp/vz/soyo.log; mkfifo /tmp/vz/vzlogger.fifo; screen -dmS zeroinput nice -1 python3 /home/vzlogger/zeroinput.py -v -web
```
enter in the crontab.
To get the output later, as user "vzlogger" (```su vzlogger```), enter ```screen -r```. Then use ctrl-a, then ctrl-d to "close".

Once this entry has been made, the regulation restarts automatically after a reboot of the Raspi.
With a little delay due to the inverters themselves and the Raspi's startup process.
If the reading head is removed, the feed-in simply stops and the counter increases to the consumption value.
As soon as the reading head is attached again, the feeding starts automatically.
Depending on the electricity meter, the "extended data output" may need to be reactivated after a power failure.

This is what the [Esmart3 configuration software](https://www.solarcontroller-inverter.com/download/20113011263165.html), [alternative link](http://www.mediafire.com/file/mt77gai7xxzig1g/install_SolarMate_CS_Windows.exe) looks like.
![Esmart3 Software](https://user-images.githubusercontent.com/110770475/204106343-8ca03bb5-ca3d-4174-9075-25db632ec087.jpg)

There is something that cannot be set on the device itself: **Li-Ion**.
The other values are of course dependent on the battery used. I have set quite high and low values because the line to the battery is not quite optimal.
So far, however, they have not been used because the script operates far away from them! (Update: values reduced!)

The battery settings must also be made on Victron charge controllers!
The configuration of the Soyosource Inverter is very clear

![Soyosource GTN setup](https://user-images.githubusercontent.com/110770475/204106365-97dc809d-fba2-4633-aa77-69b2061f7289.jpg)

### Planned
- Integrate other charge controllers. The eSmart4 should be compatible, please give feedback!
- Integrate other inverters
- A ready-made Volkszähler image, where you only have to adjust the reading head.

## What is still missing?

## Have fun rebuilding!

More information is available on the (german) forums:
- [Akku Doktor Forum](https://akkudoktor.net/t/skalierbare-high-end-cheap-tech-nulleinspeisung-mit-volkszaehler-monitor-und-tibber-integration/5031)
- [photovoltaikforum](https://www.photovoltaikforum.com/thread/179609-nulleinspeisung-mit-mme-volkszaehler-soyosource-gti-esmart3-laderegler-akku-sald/)
