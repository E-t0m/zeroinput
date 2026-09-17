# dirt_shift — Kurzübersicht

## Zweck

`dirt_shift` verschiebt die Entladung eines PV-Heimspeichers gezielt in die Netzstunden mit
der höchsten CO₂-Intensität. Der deutsche Strommix ist abends, nachts und früh morgens
ungünstig (wenig PV, hohe Last, fossile Spitzen) und tagsüber sauber. Wer wählen kann, *wann*
die Batterie statt des Netzes den Verbrauch deckt, spart in den dreckigsten Stunden am meisten
Emissionen. `dirt_shift` lenkt den verfügbaren Akkuinhalt daher in genau diese Stunden.

Das Programm greift nicht selbst in die Hardware ein: Es schreibt viertelstündlich eine
`timer.txt`, die **zeroinput** ausliest und umsetzt. Die PV-Durchleitung ins Haus bleibt dabei
grundsätzlich unangetastet — begrenzt wird nur die Batterieentladung.

## Datengrundlage

Bei jedem Lauf werden zusammengeführt: der **Hausverbrauch** (7-Tage-Mittel aus dem
volkszähler), der aktuelle **Akkuinhalt** (aus Lade-/Entladeenergie seit dem letzten
anhaltenden Leerzustand rekonstruiert, gegen kurze Spannungsausreißer und Sensorfehler
abgesichert), die **PV-Prognose** (empirische Anlagenkurve, skaliert mit der
Strahlungsvorhersage von Open-Meteo) und die **Netz-CO₂-Intensität** (Day-Ahead-Prognose von
SMARD für erneuerbare Erzeugung und Last). Fehlende Daten werden mehrstufig abgefedert: Fällt
die Verbrauchsprognose aus, dient das Profil des letzten verfügbaren Tages als Ersatz; fällt
SMARD aus, überbrückt ein Cache einen Tag. Reicht auch das nicht, modelliert ein **Notmodus**
aus Solar- und Windprognose (Open-Meteo) sowie einem festen bundesweiten Lastprofil einen
relativen Dreckigkeitsverlauf — deutlich gekennzeichnet, nie mit echten Daten verwechselt. Erst
wenn selbst das Wetter nicht abrufbar ist, hinterlässt `dirt_shift` einen „Alles-erlaubt"-Timer,
sodass zeroinput nie durch veraltete Grenzen blockiert wird. Ein rekonstruierter Akkuinhalt, der
physikalisch unmöglich unter „leer" fällt, löst unabhängig davon eine Warnung aus, die grob
zwischen einer defekten Komponente (z. B. einem Wechselrichter, der Leistung anfordert, aber
nicht liefert) und einer zu niedrig eingestellten Umwandlungseffizienz unterscheidet.

## Rote und grüne Zonen

Aus dem Verhältnis erneuerbare Erzeugung zu Last entsteht je Stunde ein Dreckigkeitswert. Über
das rollierende 24-Stunden-Fenster ab der aktuellen Stunde wird dessen Median gebildet: die
sauberere Hälfte wird **grün**, die dreckigere **rot**. Der Schnitt passt sich damit der
tatsächlichen Streuung an, statt an einer festen Schwelle zu hängen.

## Entladesteuerung

Zurückgehalten wird eine **Reserve** — der Bedarf der kommenden roten Stunden bis zur nächsten
PV-Ladephase, abzüglich des bis dahin erwarteten Überschusses (an einem sonnigen Tag füllt sich
der Akku ohnehin, dann ist die Reserve null). Daraus folgt der Modus je Stunde:

- **grün, Inhalt über der Reserve** → freie Entladung.
- **grün, Inhalt darunter** → kein Entladen (das Haus läuft am Netz), damit der Inhalt für die
  dreckigen Stunden bereitsteht — aber nur, wenn diese spürbar dreckiger sind als jetzt; sonst
  deckt der Akku den laufenden Verbrauch weiter.
- **rot** → freie Entladung, wenn der Inhalt reicht; sonst gedeckelt auf den Hausanteil, wobei
  die eine dreckigste Stunde des Fensters unbegrenzt bedient wird.

Die Deckelung wirkt zweifach: als Leistungsgrenze (Watt) und als Energiebudget je Viertelstunde
(gegen die tatsächlich gemessene PV gerechnet). So wird sowohl eine kurze Lastspitze als auch
eine dauerhaft erhöhte Last ins Netz gedrängt statt in den Akku.

## Optionale Zusatzfunktionen

**Wallbox.** `dirt_shift` kann das Relais einer Wallbox über ein Tasmota-Gerät schalten, wenn
eine von drei — voneinander unabhängigen — Bedingungen zutrifft: die Stunde gehört zu den
saubersten des Fensters, der Akku hat Überschuss über seine Reserve, oder der Akku ist voll.
Fehlt für einen Pfad die nötige Messung (z. B. der Akkuinhalt), fällt nur dieser Pfad weg, die
übrigen entscheiden weiter; nur wenn keiner der drei entscheidbar ist, bleibt das Relais
unverändert. Ein Auto muss ohnehin aus dem Netz geladen werden — der Zweck ist, die sauberste
Stunde dafür zu wählen. Lädt die Wallbox über den Sauberkeits-Pfad, während der Akku seine
Reserve nicht deckt und auch sonst keinen Überschuss hat, wird dessen Entladung auf den
Hausanteil begrenzt, sodass der Ladestrom aus dem Netz kommt und nicht die Nachtreserve leert.
Nur selbst eingeschaltete Relais werden auch wieder abgeschaltet; eine manuelle Ladung bleibt
unangetastet.

**Precharge.** In den saubersten Stunden kann PV-Überschuss gezielt in den Akku gelenkt werden,
statt ins Haus — aber nur, wenn sonst tatsächlich Überschuss verloren ginge, weil er nicht mehr
in den freien Akkuraum passt. An einem knapp bemessenen Speicher greift das selten. Die
Wechselrichter-Durchleitung wird dabei nie auf 0 gedrosselt, damit zeroinput die Timer-Zeile in
jedem Fall als gültig erkennt.

## Betrieb

Aufruf viertelstündlich per cron. `-v` zeigt Zone, Akkustand, Reserve und Modus; `-debug`
zusätzlich die stündliche Übersichtstabelle. Alle anlagenspezifischen Werte stehen in
`dirt_shift.conf`; geteilte Größen liest `dirt_shift` aus `zeroinput.conf`, ohne sie zu ändern.
