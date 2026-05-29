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
