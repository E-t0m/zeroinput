# Saldierte Nulleinspeisung mit dem Haus-Stromzähler

Es wird **zuerst** der Eigenverbrauch im Haus gedeckt.
Wenn **dann** noch Leistung übrig ist, dann wird auch der **Akku** geladen.

Oder: Vorgaben in der [timer-Datei](https://github.com/E-t0m/zeroinput/blob/main/timer.txt) setzen andere Werte.

## Aufbau
![Schema](https://user-images.githubusercontent.com/110770475/204104728-d1dabefa-5ac4-446d-bf72-9fb58aaae4e6.jpg)

## Die Komponenten
- Soyosource GTN 1200W (max. 900 W im Batteriemodus), ACHTUNG! meines Wissens liegt kein Zertifikat für VDE-AR-N 4105 vor, in Deutschland ist das Gerät damit nicht zulässig. [Anleitung und Spezifikation](https://www.mediafire.com/file/kvn0jvyuubd3364/soyosource1.200W%252BGrid%252BTie%252BInverter.pdf/file)
- Victron Solar und / oder eSmart3 MPPT Laderegler [Anleitung und Spezifikation](https://www.solarcontroller-inverter.com/download/18122110445698.html), [Herstellerseite](https://www.ipandee.com/products/mppt-solar-charge-controller-esmart-12v-24v-36v-48v-20a-60a/), [Konfigurationssoftware Windows](http://www.mediafire.com/file/mt77gai7xxzig1g/install_SolarMate_CS_Windows.exe)
- Raspberry Pi (oder anderer (Kleinst)rechner)
- Lesekopf für den Stromzähler (moderne Messeinrichtung), oder ein Volkszähler-kompatibles Energymeter
* 16s LiFePO4 Akku

## Funktionsweise
Der Aufbau entspricht dem einer Inselanlage:
PV-Module, Akku und der Soyosource Netzwechselrichter (am Lastausgang) sind am eSmart3 Laderegler angeschlossen.

(Zur direkten Erfassung des Verbrauchs. Manche Victron Regler sind ohne Lastausgang.
Weitere Netzwechselrichter müssen wegen des 40A-Limit am Lastausgang des eSmart3 direkt an den Akku angeschlossen werden.
Die Regelung funktioniert auch ohne eSmart3 Regler und ohne Inverter als Last.)

Die Soyosource Inverter lassen sich per RS485 (Modbus) in der Einspeiseleistung regeln.
Dafür werden auch Messklemmen angeboten, aber damit könnte man keine Phasen saldieren.
Für die Regelung ist der Raspberry Pi zuständig, auf dem die [Volkszähler](http://volkszaehler.org) Software läuft.
Der Volkszähler liest mittels Lesekopf am Haus-Stromzähler die sekundengenauen, geeichten! echten Verbrauchsdaten ab.
Der OBIS Datensatz mit der Bezeichnung "1-0:16.7.0" gibt den aktuellen saldierten Verbrauch an.
Mit negativen Werten bei Einspeisung, auch bei Zählern mit Rücklaufsperre! Zumindest macht das meiner (DD3 BZ06) so.
Ohne "16.7.0" vom Zähler ist die Regelung nicht möglich! Nachprüfen!

Hier kommt dann das Script [zeroinput](https://github.com/E-t0m/zeroinput) ins Spiel,
es liest die Verbrauchsdaten vom Haus-Stromzähler über den [Volkszähler](http://volkszaehler.org) aus und berechnet die nötige Einspeiseleistung, um den Zähler auf Null zu setzen.
Grundsätzlich würde das Script auch ohne Volkszähler funktionieren und könnte den Lesekopf selbst auslesen.
Dann würde es auf wesentlich "kleinerer" Hardware laufen, aber so ganz ohne **Monitoring** wäre mir das zu riskant.
Der "unübliche Weg", das logfile umzuleiten, anstatt die dem Volkszähler eigenen Methoden (Datenbankzugriff über Netzwerk) zu verwenden, erhöht die Ausfallsicherheit erheblich.
Selbst bei einem Datenbankabsturz der Volkszähler-Software arbeitet die Regelung ungestört weiter! Ich hatte das bereits.
Wer kein Kabel zum Zähler legen will, könnte einen WIFI-Lesekopf benutzen und die Verbrauchsdaten per WLAN-Netzwerk übertragen.

In der Praxis **schwankt der Wert am Zähler minimal um die 0**, übrigens zeigt mein "smart Meter" an seinem Display auch Einspeisung ohne Minuszeichen als positiven Wert an.
(es gibt A- und A+ mit Pfeilen, diese zeigen Bezug / Export an)

## Funktionen
Das [Script](https://github.com/E-t0m/zeroinput) hat diese Funktionen:
- zeitgesteuerte Entladung des Akkus und der Inverterleistung
- automatische Umschaltung zwischen einem und mehreren Invertern
- Unterspannungschutz Akku unter 48 V
- Leistungsanpassung Akku von 48 V bis 51 V, mittels Regelkurve, mögliche Gesamtleistung immer zuzüglich PV
- mögliche Export-Einspeisung ab 54,5 V, "verschenkt" Energie ins Netz
- Minimalleistung
- Maximalleistung
- Automatische Anpassung oder permanentes Verschieben der Nulllinie in Richtung Bezug oder Export
- Korrektur der Kabelverluste zum Akku
- Alarmierung bei erhöhter externer (z.B Akku) oder interner Temperatur des eSmart3
- "Rampen Modus" für starke Sprünge im Verbrauch
- Unterdrückung der Schwingung des Regelkreises


Natürlich könnte man auch andere Laderegler, wie z.B. Epever einbinden - wenn sie auslesbar sind, denn die Akku-Spannung und PV-Leistung sind sehr wichtige Werte für die Regelung!
Auch jeder andere Netzwechselrichter kann verwendet werden, wenn er regelbar ist.
Denkbar ist auch ein regelbarer DC-DC-Wandler an einem Microwechselrichter.
Die "harten" Grenzwerte für Unter- und Überspannung des Akkus müssen sowohl in allen Laderegler(n) als auch in allen Netzwechselrichter(n) eingestellt werden.

## Rechtsrahmen
Soweit ich das beurteilen kann, muss man die hier beschriebene Anlage in Deutschland sowohl beim Netzbetreiber als auch im Marktstammdatenregister anmelden,
wenn man alle gesetzlichen Regularien befolgen will. Kann man aber nicht! Denn im Klartext:
Der genannte Soyosource Wechselrichter darf wegen der **fehlenden Zertifizierung** in Deutschland nicht ans Stromnetz angeschlossen werden.

## Beispiele für die Regelung
Die Werte für PV (gelb, Leistung der PV-Module) und Soyo P (grün, eingespeister Strom) werden **negiert** dargestellt!
Die Daten für PV, Soyo P und Akku U (rot, Akkuspannung) liefert das Script, wobei Soyo P berechnet wird.
PV und Akku U werden vom esmart3 Regler gelesen und an den Volkszähler zur Darstellung weitergereicht.
Dadurch entsteht eine Sekunde Zeitversatz zu den Kurven des Haus-Stromzählers.

### So sieht dann ein recht **guter Tag mit hohem Verbrauch** aus
![viel Verbrauch](https://user-images.githubusercontent.com/110770475/204105529-4d6d03e1-ca13-4224-8272-4995115232d0.png)
(2 soyo gti)
Die Tageswerte waren: PV Erzeugung 7,7 kWh, Einspeisung 7,3 kWh, bewusst verschenkt 0 kWh.
An diesem Tag liefen Backofen, Microwelle, Brunnenpumpe, Split-Klima, etc. Der Akku war am Ende des Tages schon leer.
Man sieht das Ein und Ausregeln am Morgen und Abend entlang der PV-Kurve. Gegen 11 die maximale Einspeisung.
Danach und sehr schön um ca. 17:30 Uhr die Leistungsanpassung für den Batteriestrom.

### Ein sonniger Tag mit **wenig Verbrauch**
![wenig Verbrauch](https://user-images.githubusercontent.com/110770475/204105552-fbbc1f4d-ab04-483d-a6ea-ae0f934cab16.png)
(2 soyo gti) Die Tageswerte waren: PV Erzeugung 6,1 kWh, Einspeisung 5,7 kWh, bewusst verschenkt 0,9 kWh.
Der größte Verbraucher war die Split-Klima. Die Akkuladung reichte bis weit in die Nacht.
Hier sieht man das Einregeln am Morgen und die Nachtlimitierung am Abend.
Der dunkelgrüne Bereich ist überschüssiger Strom, da die Batterie - siehe Spannungskurve - voll ist.
(inzwischen ist die Übereinspeisung in der Standard Einstellung wesentlich geringer!)

### **Ein trüber Tag**
![sehr trüber Tag](https://user-images.githubusercontent.com/110770475/204105585-13a50eb1-87cf-4dbc-8e62-469527aed402.jpg)

Solange die Leistung der PV den Verbrauch nicht übersteigt, geht die gesamte Leistung direkt in den Netzwechselrichter.
Der Akku wird nicht geladen. Im Wesentlichen entspricht das der Arbeitsweise eines Modul/Micro-Wechselrichters.

### **Waschmaschine in Heizphase**
![60 Grad Wäsche](https://user-images.githubusercontent.com/110770475/204105605-2a70356a-90d3-4a8a-a7a1-fddb570a9e3c.png)
(2 soyo gti)
Die Daten in der Tabelle beziehen sich auf den ganzen sichtbaren Ausschnitt.
Hier wird noch die "Summe L1+L2+L3" (OBIS "1-0:16.7.0") in Schwarz angezeigt.
Sie entspricht oberhalb der Nulllinie ziemlich genau der roten Linie für den Netzbezug ("1-0:1.8.0").
Der Teil unterhalb der Nulllinie ist die tatsächliche Einspeisung ins Stromnetz, also Energie, die "durch den Zähler ins Netz" geht.
Diese wird vom Zähler nicht "gerechnet", es wäre die zweite Richtung in einem Zweirichtungszähler.
In Summe waren das 430 Wh bezahlter Bezug und 418 Wh "physikalischer Bezug". Es wurden also 12 Wh unvergütet eingespeist.
Die kontinuierliche Welle ab 10:10 kommt durch den ständig anlaufenden Trommelmotor, diesem ansteigenden Verbrauch folgt das Script.
Aber da der Motor auch plötzlich wieder abschaltet, kommt durch die Trägheit der Regelung eine tatsächliche Einspeisung zustande.
Die Wellen in der grünen Linie stammen außerhalb der Heizphase durch diese Trägheit.
Während der Heizphase kommt die Welligkeit von der Leistungsanpassung an die Akkuspannung. (Die Waschmaschine lief einfach zu früh, der Akku war kaum geladen.)
Der Leistungseinbruch um ca. 10:33 kommt durch den Neustart des MPP-Trackers im Laderegler. (Grün und Gelb)
Erkennbar ist auch noch das Ansteigen der PV Leistung in dem Moment, wo die Heizphase beginnt.
Umgekehrt sinkt die PV Leistung mit langsam ansteigender Akku Spannung nach der Heizphase.

### **Milchkaffee mit Microwelle und Induktionsplatte** - für Fortgeschrittene
![milchkaffee](https://user-images.githubusercontent.com/110770475/204105626-c05746c4-1a6c-4252-910e-d2083dae432b.jpg)
(2 soyo gti)
Die roten Flächen sind der eingekaufte Bezug. Die grünen Flächen die eigene Einspeisung.
Die grauen Flächen sind die trägheitsbedingte Übereinspeisung, kostenlos eingespeiste Energie.

### Noch eine Verlaufsgrafik mit nur **einem Soyosource**
![einphasig](https://user-images.githubusercontent.com/110770475/204106401-e274ba31-8ad7-48a7-9975-7f3d39a58db0.jpg)
(1 soyo gti)

### Leertakten des Akkus
![leertakten](https://github.com/E-t0m/zeroinput/assets/110770475/59e0d728-b6f5-4dae-9b3c-78c18e5cba8e)
(3 soyo gti)
Hier sieht man, wie der Backofen den Akku leer taktet.
Dabei spielen folgende Komponenten der Regelung zusammen:
- Begrenzung des Entladestroms der Batterie, hier 2kW vom Akku + PV-Leistung
- Rampenmodus, bei großen Sprüngen im Verbrauch
- Korrektur der Batteriespannung
- Leistungsanpassung an die Batteriespannung, die Entladekurve von LFP fällt rasant ab

### **Schwankungen** der Regelung in einer eher ruhigen Phase
![schwankungen_mit_zeroshift](https://github.com/user-attachments/assets/9b7ab215-dbf8-4942-87af-356f95d4f6e5)
Die schwarzen Werte zeigen die Schwankungen in Bezug / Einspeisung an.
Die Einspeisung - von einem Inverter - betrug etwa 150 W in diesem Abschnitt.
Lila zeigt die automatische "Anhebung der Nulllinie" bei steigender Einspeisung durch wechselnden Verbrauch.

**Die Ausgabe des Scripts** ca. jede Sekunde:
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
Die Ausgabe funktioniert sowohl im Terminal (screen, s.u.) und / oder auch per **Webbrowser**.
Dafür einen link aus dem htdocs-Ordner des Volkszählers anlegen und zeroinput.py mit dem -web Parameter starten.
Die Seite ist dann im Browser unter der http ://üblichen-adresse-des-volkszählers **/zeroinput.html** erreichbar.
```
ln -s /home/vzlogger/zeroinput.html /home/pi/volkszaehler.org/htdocs
```

## Messgenauigkeit
Zur Genauigkeit der Daten vom esmart3 hat der Autor der [Esmart3 Bibliothek](https://github.com/skagmo/esmart_mppt), die ich modifiziert verwende, [einen Bericht veröffentlicht](https://skagmo.com/page.php?p=documents%2F04_esmart3_review).
Meiner Beobachtung nach, stimmt die eingespeiste Leistung vom Soyosource Inverter recht genau mit dem angeforderten Wert überein.
Zu beachten gibt es noch die verzögerte Ansprechzeit (ramp speed) von 400 W/s. Der Soyo braucht also 2+ Sekunden von 0 auf 100% Leistung. (das ist Absicht, kein Fehler)
Darum habe ich die Soyos einfach nur parallel angesteuert, um eine möglichst kurze Ansprechzeit zu haben, mit mehr Soyos wird das entsprechend noch besser, aber auch einer funktioniert!

## Wirkungsgrad
Die Ausgabe des Scripts oben zeigt: eingespeiste Leistung 643 W, wogegen der eSmart3 692 W Last anzeigt. Das ergibt ~ 93 % Wirkungsgrad in diesem Moment.
Das Laden und Entladen des Akkus kostet natürlich auch Energie.
Mit den Beispieldaten der Tage (weiter oben) lässt sich ein Gesamtwirkungsgrad berechnen:
- PV Erzeugung 7,7 kWh, Einspeisung 7,3 kWh, ergibt ~ 95 %
- PV Erzeugung 6,1 kWh, Einspeisung 5,7 kWh, ergibt ~ 93 %
Je mehr Energie durch den Akku geht, desto schlechter ist der Wirkungsgrad der gesamten Anlage.

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
wget https://raw.githubusercontent.com/E-t0m/esmart_mppt/master/esmart.py
chmod 744 /home/vzlogger/*py
chown vzlogger: /home/vzlogger/*py
su vzlogger
mkdir /tmp/vz
touch /tmp/vz/soyo.log
mkfifo /tmp/vz/vzlogger.fifo
python3 /home/vzlogger/zeroinput.py -v (mit strg+c beenden)
oder wer screen kennt (man screen):
screen -dmS zeroinput nice -1 python3 /home/vzlogger/zeroinput.py -v -web (mit screen -r "öffnen", mit strg-a, dann strg-d "schließen")
```
(Natürlich kann man auch git benutzen.)

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

So sieht die [Konfigurationssoftware des Esmart3 für Windows](https://www.solarcontroller-inverter.com/download/20113011263165.html), [alternativer Link](http://www.mediafire.com/file/mt77gai7xxzig1g/install_SolarMate_CS_Windows.exe) aus.
![Esmart3 Software](https://user-images.githubusercontent.com/110770475/204106343-8ca03bb5-ca3d-4174-9075-25db632ec087.jpg)

Da gibt es etwas, was man am Gerät selbst nicht einstellen kann: **Li-Ion**.
Die anderen Werte sind natürlich abhängig vom verwendeten Akku. Ich habe recht hohe und tiefe Werte einstellt, da die Leitung zum Akku nicht ganz optimal ist.
Bisher kamen sie allerdings auch noch nicht zum Einsatz, da das Script weit weg davon operiert! (Update: Werte reduziert!)

Die Konfiguration des Soyosource Inverters ist sehr übersichtlich

![Soyosource GTN setup](https://user-images.githubusercontent.com/110770475/204106365-97dc809d-fba2-4633-aa77-69b2061f7289.jpg)

### Geplant
- andere Laderegler einbinden. Der eSmart4 müsste kompatibel sein, bitte Rückmelden!
- andere Inverter einbinden
- Ein fertiges Volkszähler-Image, bei dem man nur noch den Lesekopf einstellen muss.

## Was fehlt noch?

## Viel Spaß beim Nachbauen!

Weitere Informationen gibt es in den Foren:
- [Akku Doktor Forum](https://akkudoktor.net/t/skalierbare-high-end-cheap-tech-nulleinspeisung-mit-volkszaehler-monitor-und-tibber-integration/5031)
- [photovoltaikforum](https://www.photovoltaikforum.com/thread/179609-nulleinspeisung-mit-mme-volkszaehler-soyosource-gti-esmart3-laderegler-akku-sald/)
