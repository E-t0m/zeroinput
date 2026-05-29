## Bauanleitung
- Den Stromzähler wenn nötig mit PIN zur (erweiterten) Datenausgabe bringen. Die PIN gibt es beim Messstellenbetreiber bzw. Netzbetreiber (nicht Stromanbieter).
Es gibt eine [praktische App](https://play.google.com/store/apps/details?id=de.bloggingwelt.blinkeingabestromzaehler) zur PIN-Eingabe für Ungeduldige.
- Den Volkszähler zum Laufen bringen. [Zur Anleitung](https://wiki.volkszaehler.org/howto/getstarted), [das Forum dazu](https://www.photovoltaikforum.com/board/131-volkszaehler-org/) ***Ohne Volkszähler läuft das Script nicht!*** Also zuerst damit anfangen.
- Es ist sehr sinnvoll dem IR-Lesekopf und RS485-Adapter per udev-Regel einen [eigenen, festen Gerätenamen](https://wiki.volkszaehler.org/hardware/controllers/ir-schreib-lesekopf-usb-ausgang) zu geben.
Für meine Geräte liegen .rules Dateien in /dev/udev/rules.d/ mit diesen Regeln:
```
SUBSYSTEMS=="usb-serial", DRIVERS=="cp210x", SYMLINK+="lesekopf"
SUBSYSTEMS=="usb-serial", DRIVERS=="ch341-uart", SYMLINK+="rs485"
```
Oder bei mehreren gleichen Geräten unterschieden durch den Anschluss am Raspi:
```
SUBSYSTEMS=="usb" ATTRS{devpath}=="1.1" SYMLINK+="rs485a"
SUBSYSTEMS=="usb" ATTRS{devpath}=="1.3" SYMLINK+="rs485b"
```
- Die ganzen Geräte wie oben schon beschrieben montieren.
- Den RS485-Anschluss des Raspi (i.d.R. ein USB-Stick mit Klemmen) mit den RS485 Anschlüssen von Soyo und eSmart3 verbinden: A+ an A+, B- an B-.
- Den Volkszähler für die Nulleinspeisung ein wenig modifizieren.

Wenn der eigene Volkszähler erfolgreich läuft, dann können noch Kanäle entsprechend dieser [vzlogger.conf](https://github.com/E-t0m/zeroinput/blob/main/vzlogger.conf) angelegt werden.
Auf jeden Fall muss ***"identifier": "1-0:16.7.0\*255" und "verbosity": 15*** enthalten sein, damit das Script damit rechnen kann.
Auch der Pfad für das "log" in der vzlogger.conf muss angepasst werden: "/tmp/vz/vzlogger.fifo"
Obwohl es nicht unbedingt zum Betrieb nötig ist, sollte der [Umgang mit Datenmengen](https://wiki.volkszaehler.org/howto/datenmengen) beachtet werden, sonst "läuft die Datenbank irgendwann über"!

```
als root:
apt install python3-serial
cd /home/vzlogger
wget https://raw.githubusercontent.com/E-t0m/zeroinput/main/zeroinput.py
chmod 744 /home/vzlogger/zeroinput.py
chown vzlogger: /home/vzlogger/zeroinput.py
su vzlogger
mkdir /tmp/vz
touch /tmp/vz/soyo.log
mkfifo /tmp/vz/vzlogger.fifo
python3 /home/vzlogger/zeroinput.py -v (mit strg+c beenden)
oder wer screen kennt (man screen):
screen -dmS zeroinput nice -1 python3 /home/vzlogger/zeroinput.py -v -web (mit screen -r "öffnen", mit strg-a, dann strg-d "schließen")
```
(Natürlich kann man auch **git** benutzen.)

Dann nochmal in einem anderen Terminal - als root - den vzlogger neu starten
```systemctl restart vzlogger```

Um das Script **automatisch beim Hochfahren des Raspi** zu starten, mittels
```
su vzlogger
crontab -e
```
diese Zeile:
```
@reboot mkdir /tmp/vz; touch /tmp/vz/soyo.log; mkfifo /tmp/vz/vzlogger.fifo; screen -dmS zeroinput nice -1 python3 /home/vzlogger/zeroinput.py -v -web
```
in die crontab eintragen.
Um später auf die Ausgabe zu kommen, als Benutzer "vzlogger" (```su vzlogger```), ```screen -r``` eingeben. Danach strg-a, dann strg-d zum "Schließen" benutzen.

Wenn dieser Eintrag erfolgt ist, startet die Regelung nach einem Neustart von selbst wieder. 
Mit ein wenig Verzögerung durch die Wechselrichter selbst und den Startvorgang des Raspi.
Wird der Lesekopf abgezogen, hört die Einspeisung einfach auf und der Zähler steigt auf den Wert des Verbrauchs.
Sobald der Lesekopf wieder angebracht wird, beginnt die Einspeisung von selbst.
Je nach Stromzähler muss nach einem Stromausfall wieder die "erweiterte Datenausgabe" aktiviert werden.
