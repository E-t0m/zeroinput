# Saldierte Nulleinspeisung mit dem Haus-Stromzähler

Hallo Allerseits,
ich möchte hier meine eigene private Anlage und Regelung zur Null-Einspeisung vorstellen.

Absolute Priorität hat die Einspeisung ins Netz.
Es wird **zuerst** soviel Strom **eingespeist** wie die PV liefern kann oder zur Nullung des Verbrauchs nötig ist.
Wenn **dann** noch Leistung übrig ist, dann wird auch der **Akku** geladen.

## Aufbau
![Schema](https://user-images.githubusercontent.com/110770475/204104728-d1dabefa-5ac4-446d-bf72-9fb58aaae4e6.jpg)

## Die Komponenten
![Schaltkasten](https://user-images.githubusercontent.com/110770475/204104803-e4959f68-4e98-4980-82a2-20e1e6b33e83.jpg)
0. Rohrlüfter in KG Rohr als aktive Kühlung, Thermosensor von 9
1. esmart3 MPPT Laderegler (das Billigteil wird unter verschiedensten Namen angeboten)
[Anleitung und Spezifikation](https://www.solarcontroller-inverter.com/download/18122110445698.html), [Herstellerseite](https://www.solarcontroller-inverter.com/products/MPPT-eSmart3-Series-Solar-Controller.htm), [Konfigurationssoftware Windows](https://www.solarcontroller-inverter.com/download/20113011263165.html)
2. Soyosource GTN 1200W (900 W im Batteriemodus), ACHTUNG! meines Wissens liegt kein Zertifikat für VDE-AR-N 4105 vor, in Deutschland ist das Gerät damit nicht zulässig. [Anleitung und Spezifikation](https://www.mediafire.com/file/kvn0jvyuubd3364/soyosource1.200W%252BGrid%252BTie%252BInverter.pdf/file)
3. AC Not-Aus Schalter
4. Sammelschraube für PV + mit hübschen Winkeln als Kühlkörper für die Sperrdioden, Vorsichtsmaßnahme wegen 8
5. Trennschalter für PV -
6. Sicherungsautomat und RCD für L1
7. kombinierte Sicherung und RCD für L2
8. Step-Up MPPT Regler für das 390 W Modul, elejoy EL-MU400SP, [Anleitung und Spezifikation](https://enerprof.de/media/pdf/c8/1b/b9/User-Manual_MPPT_LED_DISPLAY_STEP-UP_SOLAR_CHARGE_CONTROLLER_DE.pdf)
9. Thermoschalter für die Lüftung

Das passt gerade so in einen 60x60 cm Schaltschrank.
Nicht zu sehen auf dem Bild:
* PV Module mit 1690 Wp (5x 260 W Poly, 1x 390 W Mono), nicht optimal ausgerichtet, die Poly sind 20 Jahre alt (185€/kWp)!
* Raspberry Pi (oder anderer (Kleinst)rechner)
* Lesekopf für den Stromzähler (moderne Messeinrichtung)
* 16s LiFePO4 Akku mit 25 Ah, also 1,28 kWh

Der Preis für die gesamte Anlage war knapp 2 k€.

## Funktionsweise
Die Anlage funktioniert grundlegend wie eine Insel:
PV, Akku und die Netzwechselrichter (als Last) hängen am Laderegler.
Die Soyosource Inverter lassen sich per RS485 (Modbus) in der Einspeiseleistung regeln.
Dafür werden auch Messklemmen angeboten, aber damit könnte man keine Phasen saldieren.
Für die Regelung ist der Raspberry Pi zuständig, auf dem die [Volkszähler](http://volkszaehler.org) Software läuft.
Der Volkszähler liest mittels Lesekopf am Haus-Stromzähler die sekundengenauen, geeichten! echten Verbrauchsdaten ab.
Der OBIS Datensatz mit der Bezeichnung "1-0:16.7.0" gibt den aktuellen saldierten Verbrauch an.
Mit negativen Werten bei Einspeisung, auch bei Zählern mit Rücklaufsperre! Zumindest macht das meiner (DD3 BZ06) so.

Hier kommt dann mein Script [zeroinput](https://github.com/E-t0m/zeroinput) ins Spiel,
es liest die Verbrauchsdaten vom Haus-Stromzähler über den [Volkszähler](http://volkszaehler.org) aus und berechnet die nötige Einspeiseleistung, um den Zähler auf Null zu setzen.
Grundsätzlich würde das Script auch ohne Volkszähler funktionieren und könnte den Lesekopf selbst auslesen.
Dann würde es auf wesentlich "kleinerer" Hardware laufen, aber so ganz ohne Monitoring wäre mir das zu riskant.
Der "unübliche Weg", das logfile umzuleiten, anstatt die dem Volkszähler eigenen Methoden (Datenbankzugriff über Netzwerk) zu verwenden, erhöht die Ausfallsicherheit erheblich.
Selbst bei einem Datenbankabsturz der Volkszähler-Software arbeitet das Script weiter! Ich hatte das bereits...

In der Praxis **schwankt der Wert am Zähler minimal um die 0**, übrigens zeigt mein "smart Meter" an seinem Display auch Einspeisung ohne Minuszeichen als positiven Wert an.
(es gibt A- und A+ mit Pfeilen, diese zeigen Bezug / Lieferung an)

## Funktionen
Das [Script](https://github.com/E-t0m/zeroinput) hat diese Funktionen:
- Unterspannungschutz Akku unter 48 V
- Leistungsanpassung Akku von 48 V bis 50 V, mittels Regelkurve, mögliche Gesamtleistung immer zuzüglich PV
- "Über"einspeisung ab 53 V bis "Saturation charging voltage" ("Sättigungsladespannung“, am esmart3), 0,5 W / 0,1 V, "zieht die Nulllinie nach unten", bei Überschuss
- Begrenzung für den Batterie Entladestrom ~ 1250 W (25 A, 1 C), mögliche Gesamtleistung immer zuzüglich PV
- Unterdrückung der Schwingung des Regelkreises
- Nachtlimit 200 W (der kleine Akku hält nie eine ganze Nacht durch)
- Minimalleistung 6 W
- Maximalleistung 1800 W
- Alarmierung bei erhöhter Batterietemperatur oder interner Temperatur des esmart3

Diese Werte **können und sollten** an die jeweilige Anlage und Akkugröße **angepasst werden**!
Natürlich könnte man auch andere Laderegler, wie z.B. Epever oder Victron einbinden. Die Akku-Spannung und PV-Leistung sind sehr wichtige Werte für die Regelung!
Auch jeder andere Netzwechselrichter kann verwendet werden, wenn er regelbar ist.
Denkbar ist auch ein regelbarer DC-DC-Wandler an einem Microwechselrichter.
Meine Anlage kann z.B. nur die volle Inverterleistung abgeben, wenn die PV in dem Moment genug Leistung bringt - um den Akku nicht zu überlasten.
Die "harten" Grenzwerte für Unter- und Überspannung etc. sind sowohl im Laderegler als auch in den Netzwechselrichtern eingestellt.

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

Die Tageswerte waren: PV Erzeugung 7,7 kWh, Einspeisung 7,3 kWh, bewusst verschenkt 0 kWh.
An diesem Tag liefen Backofen, Microwelle, Brunnenpumpe, Split-Klima, etc. Der Akku war am Ende des Tages schon leer.
Man sieht das Ein und Ausregeln am Morgen und Abend entlang der PV-Kurve. Gegen 11 die maximale Einspeisung.
Danach und sehr schön um ca. 17:30 Uhr die Leistungsanpassung für den Batteriestrom.

### Ein sonniger Tag mit **wenig Verbrauch**
![wenig Verbrauch](https://user-images.githubusercontent.com/110770475/204105552-fbbc1f4d-ab04-483d-a6ea-ae0f934cab16.png)

Die Tageswerte waren: PV Erzeugung 6,1 kWh, Einspeisung 5,7 kWh, bewusst verschenkt 0,9 kWh.
Der größte Verbraucher war die Split-Klima. Die Akkuladung reichte bis weit in die Nacht.
Hier sieht man das Einregeln am Morgen und die Nachtlimitierung am Abend.
Der dunkelgrüne Bereich ist überschüssiger Strom, da die Batterie - siehe Spannungskurve - voll ist.
(inzwischen ist die Übereinspeisung in der Standard Einstellung wesentlich geringer!)

### **Ein trüber Tag**
![sehr trüber Tag](https://user-images.githubusercontent.com/110770475/204105585-13a50eb1-87cf-4dbc-8e62-469527aed402.jpg)

### **Waschmaschine in Heizphase**
![60 Grad Wäsche](https://user-images.githubusercontent.com/110770475/204105605-2a70356a-90d3-4a8a-a7a1-fddb570a9e3c.png)

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
Die roten Flächen sind der eingekaufte Bezug. Die grünen Flächen die eigene Einspeisung.
Die grauen Flächen sind die trägheitsbedingte Übereinspeisung, kostenlos eingespeiste Energie.

### Noch eine Verlaufsgrafik mit nur **einem Soyosource**
![einphasig](https://user-images.githubusercontent.com/110770475/204106401-e274ba31-8ad7-48a7-9975-7f3d39a58db0.jpg)

### **Schwankungen** der Regelung in einer eher ruhigen Phase
![Schwankungen](https://user-images.githubusercontent.com/110770475/204105644-0ce5aaba-ebd4-4854-8335-e142a41a482f.jpg)
Die schwarzen Werte zeigt der Haus-Zähler ohne Minuszeichen an.
Rot muss bezahlt werden. Die Einspeisung betrug 400 bis 450 W in diesem Abschnitt.

**Die Ausgabe des Scripts** im verbose mode, jede Sekunde neu:
```
16:36:17         SOC 27  Mode CC
PV       55.1 V  16.8 A  873 W
Battery  52.0 V  3.5 A   181 W
Load     52.1 V  13.3 A  692 W
Temp     int 41 °C      Bat 30 °C

input history [644, 645, 644, 643] 
        1/2  0.2 % 
        3/4  -0.2 %
        no saw detected
interval 1.03 s
meter -2 W
input 643 W 
```

## Messgenauigkeit
Zur Genauigkeit der Daten vom esmart3 hat der Autor der [Esmart3 Bibliothek](https://github.com/skagmo/esmart_mppt), [die ich modifiziert verwende](https://github.com/E-t0m/esmart_mppt), [einen Bericht veröffentlicht](https://skagmo.com/page.php?p=documents%2F04_esmart3_review).
Meiner Beobachtung nach, stimmt die eingespeiste Leistung vom Soyosource Inverter recht genau mit dem angeforderten Wert überein.
Zu beachten gibt es noch die verzögerte Ansprechzeit (ramp speed) von meines Wissens 400 W/s. Der Soyo braucht also 2+ Sekunden von 0 auf 100% Leistung. (das ist Absicht, kein Fehler)
Darum habe ich die beiden Soyo einfach nur parallel angesteuert, um eine möglichst kurze Ansprechzeit zu haben, mit mehr Soyos würde das entsprechend noch besser, aber auch einer würde funktionieren!
(Der 2-phasige Anschluss meiner Anlage wäre nicht nötig und stammt von Experimenten mit Phasen-basierter Nulleinspeisung, die ich inzwischen verworfen habe! Trotzdem schön zu haben.)
Ab wann macht **ein weiterer Wechselrichter** Sinn? 
Der Grundverbrauch liegt laut Hersteller bei < 2 W. Mit 3 W gerechnet, ergeben sich 72 Wh / Tag.
Also kommt man auf 72 Wh / 900 Wh * 60 Minuten = 4,8 Minuten Volllast Einspeisung.
Läuft der "weitere" Inverter also mehr als 5 Minuten mit Volllast pro Tag, lohnt er sich, ganz grob gerechnet.

## Wirkungsgrad
Die Ausgabe des Scripts oben zeigt: eingespeiste Leistung 643 W, wogegen der esmart 692 W Last anzeigt. Das ergibt ~ 93 % Wirkungsgrad in diesem Moment.
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
- Die ganzen Geräte wie oben schon beschrieben montieren.
- Den RS485-Anschluss des Raspi (i.d.R. ein USB-Stick mit Klemmen) mit den RS485 Anschlüssen von Soyo und esmart3 verbinden: A+ an A+, B- an B-.
- Den Volkszähler für die Nulleinspeisung ein wenig modifizieren.

Wenn der eigene Volkszähler erfolgreich läuft, dann können noch Kanäle entsprechend dieser [vzlogger.conf](https://github.com/E-t0m/zeroinput/blob/main/vzlogger.conf) angelegt werden.
Auf jeden Fall muss ***"identifier": "1-0:16.7.0*255" und "verbosity": 15*** enthalten sein, damit das Script damit rechnen kann.
Auch der Pfad für das "log" in der vzlogger.conf muss angepasst werden: "/tmp/vz/vzlogger.fifo"
Obwohl es nicht zum Betrieb nötig ist, sollte der [Umgang mit Datenmengen](https://wiki.volkszaehler.org/howto/datenmengen) beachtet werden, sonst "läuft die Datenbank irgendwann über"!

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
screen -dmS zeroinput nice -1 python3 /home/vzlogger/zeroinput.py -v (mit screen -r "öffnen", mit strg-a, dann strg-d "schließen")
```

Dann nochmal in einem anderen Terminal - als root - den vzlogger neu starten
```systemctl restart vzlogger```

Um das Script **automatisch beim Hochfahren des Raspi** zu starten, mittels
```
su vzlogger
crontab -e
```
diese Zeile:
```
@reboot mkdir /tmp/vz; touch /tmp/vz/soyo.log; mkfifo /tmp/vz/vzlogger.fifo; screen -dmS zeroinput nice -1 python3 /home/vzlogger/zeroinput.py -v
```
in die crontab eintragen.
Um später auf die Ausgabe zu kommen, als Benutzer "vzlogger" (```su vzlogger```), ```screen -r``` eingeben. Danach strg-a, dann strg-d zum "schließen" benutzen.

Wenn dieser Eintrag erfolgt ist, startet die Regelung nach einem Stromausfall von selbst wieder. 
Mit ein wenig Verzögerung durch die Wechselrichter selbst und den Startvorgang des Raspi.
Wird der Lesekopf abgezogen, hört die Einspeisung einfach auf und der Zähler steigt auf den Wert des Verbrauchs.
Sobald der Lesekopf wieder angebracht wird, beginnt die Einspeisung von selbst.

So sieht die [Konfigurationssoftware des Esmart3 für Windows](https://www.solarcontroller-inverter.com/download/20113011263165.html) aus.
![Esmart3 Software](https://user-images.githubusercontent.com/110770475/204106343-8ca03bb5-ca3d-4174-9075-25db632ec087.jpg)

Da gibt es etwas, was man am Gerät selbst nicht einstellen kann: **Li-Ion**.
Die anderen Werte sind natürlich abhängig vom verwendeten Akku. Ich habe recht hohe und tiefe Werte einstellt, da die Leitung zum Akku nicht ganz optimal ist.
Bisher kamen sie allerdings auch noch nicht zum Einsatz, da das Script weit weg davon operiert! (Update: Werte reduziert!)

Die Konfiguration des Soyosource Inverters ist sehr übersichtlich

![Soyosource GTN setup](https://user-images.githubusercontent.com/110770475/204106365-97dc809d-fba2-4633-aa77-69b2061f7289.jpg)

### Geplant
- Akku-Puffer für Notstromversorgung. (Erfordert einen zusätzlichen Inselwechselrichter!)
- vorauseilende Regelung bei wiederkehrenden Verbrauchsmustern, z.B. Waschmaschine, Mikrowelle, Kochfeld
- andere Laderegler einbinden, es muss nicht immer der Esmart3 sein
- Ein fertiges Volkszähler-Image, bei dem man nur noch den Lesekopf einstellen muss.

## Was fehlt noch?

## Viel Spaß beim Nachbauen!
