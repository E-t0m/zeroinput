# Saldierte Nulleinspeisung mit dem Haus-Stromzähler

Es wird **zuerst** der Eigenverbrauch im Haus gedeckt.
Wenn **dann** noch Leistung übrig ist, dann wird auch der **Akku** geladen.

Oder: Vorgaben in der [timer-Datei](https://github.com/E-t0m/zeroinput/blob/main/timer.txt) setzen andere Werte.

## Aufbau
![scheme](https://github.com/user-attachments/assets/8d767329-15f9-4098-b4b4-a9e70d0d98c9)

## Die Komponenten
- Soyosource GTN 1200W (max. 900 W im Batteriemodus), ACHTUNG! meines Wissens liegt kein Zertifikat für VDE-AR-N 4105 vor, in Deutschland ist das Gerät damit nicht zulässig. [Anleitung und Spezifikation](https://www.mediafire.com/file/kvn0jvyuubd3364/soyosource1.200W%252BGrid%252BTie%252BInverter.pdf/file)
- Victron Solar und / oder eSmart3 (4?) MPPT Laderegler
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
(2 soyosource gti)
Die Tageswerte waren: PV Erzeugung 7,7 kWh, Einspeisung 7,3 kWh, bewusst verschenkt 0 kWh.
An diesem Tag liefen Backofen, Microwelle, Brunnenpumpe, Split-Klima, etc. Der Akku war am Ende des Tages schon leer.
Man sieht das Ein und Ausregeln am Morgen und Abend entlang der PV-Kurve. Gegen 11 die maximale Einspeisung.
Danach und sehr schön um ca. 17:30 Uhr die Leistungsanpassung für den Batteriestrom.

### Ein sonniger Tag mit **wenig Verbrauch**
![wenig Verbrauch](https://user-images.githubusercontent.com/110770475/204105552-fbbc1f4d-ab04-483d-a6ea-ae0f934cab16.png)
(2 soyosource gti) Die Tageswerte waren: PV Erzeugung 6,1 kWh, Einspeisung 5,7 kWh, bewusst verschenkt 0,9 kWh.
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
(2 soyosource gti)
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
(2 soyosource gti, ohne Rampen-Modus!)
Die roten Flächen sind der eingekaufte Bezug. Die grünen Flächen die eigene Einspeisung.
Die grauen Flächen sind die trägheitsbedingte Übereinspeisung, kostenlos eingespeiste Energie.

### Noch eine Verlaufsgrafik mit nur **einem Soyosource**
![einphasig](https://user-images.githubusercontent.com/110770475/204106401-e274ba31-8ad7-48a7-9975-7f3d39a58db0.jpg)
(1 soyosource gti, Ohne Rampen-Modus!)

### Leertakten des Akkus
![leertakten](https://github.com/E-t0m/zeroinput/assets/110770475/59e0d728-b6f5-4dae-9b3c-78c18e5cba8e)
(3 soyosource gti)
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

So sieht die [Konfigurationssoftware des Esmart3 für Windows](https://www.solarcontroller-inverter.com/download/20113011263165.html), [alternativer Link](http://www.mediafire.com/file/mt77gai7xxzig1g/install_SolarMate_CS_Windows.exe) aus.
![Esmart3 Software](https://user-images.githubusercontent.com/110770475/204106343-8ca03bb5-ca3d-4174-9075-25db632ec087.jpg)

Da gibt es etwas, was man am Gerät selbst nicht einstellen kann: **Li-Ion**.
Die anderen Werte sind natürlich abhängig vom verwendeten Akku. Ich habe recht hohe und tiefe Werte einstellt, da die Leitung zum Akku nicht ganz optimal ist.
Bisher kamen sie allerdings auch noch nicht zum Einsatz, da das Script weit weg davon operiert! (Update: Werte reduziert!)

Die Einstellungen für den Akku sind auch an Victron Ladereglern durchzuführen!
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
