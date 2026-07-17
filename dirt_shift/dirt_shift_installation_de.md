# dirt_shift – Installationsanleitung

## Überblick

`dirt_shift` verschiebt die Batterieentladung in die Stunden mit der höchsten Netz-CO₂-Intensität und schreibt dazu die `timer.txt`, die zeroinput für die Entladesteuerung liest. Es läuft periodisch (viertelstündlich per cron), nicht als Dauerdienst.

Voraussetzung ist eine funktionierende zeroinput-Installation mit aktiviertem Entladetimer und ein erreichbarer volkszähler mit der `data.json`-HTTP-API.

Die ausführliche Beschreibung der Funktionsweise steht in `dirt_shift_spec_de.md`.

---

## Voraussetzungen

### Software

- Python 3 mit dem Modul `requests` (`pip3 install requests` oder `apt install python3-requests`)
- eine laufende zeroinput-Installation
- volkszähler mit aktivierter `data.json`-API, erreichbar über `http://host:port/`

### zeroinput-seitig

In `zeroinput.conf` muss der Entladetimer aktiv sein:

```json
"discharge_timer": true,
"discharge_t_file": "timer.txt",
```

`dirt_shift` liest aus `zeroinput.conf` nur den Pfad `discharge_t_file` (relativ zur `zeroinput.conf` aufgelöst) sowie `cell_count`. Dadurch schreibt `dirt_shift` garantiert dieselbe Datei, die zeroinput liest. `zeroinput.conf` wird dabei nur gelesen, nie verändert. Alle `dirt_shift`-eigenen Parameter leben ausschließlich in `dirt_shift.conf`.

---

## Installation

### 1. Dateien kopieren

`dirt_shift` liegt üblicherweise in einem Unterordner der zeroinput-Installation:

```bash
cd /opt/zeroinput
mkdir -p dirt_shift
cp dirt_shift.py dirt_shift.conf dirt_shift/
chmod +x dirt_shift/dirt_shift.py
```

Der Standardpfad zur übergeordneten `zeroinput.conf` ist `../zeroinput.conf` und passt damit zu dieser Ordnerstruktur. Liegt `dirt_shift` woanders, ist der Schlüssel `zeroinput_conf` in `dirt_shift.conf` entsprechend anzupassen.

### 2. dirt_shift.conf anpassen

Die mitgelieferte `dirt_shift.conf` enthält Platzhalter, die ersetzt werden müssen:

```json
"zeroinput_conf": "../zeroinput.conf",
"vz_host_port": "192.168.1.10:8080",
"vz_chans": {
    "Inverter": "<UUID>",
    "Import":   "<UUID>",
    "Auto":     "<UUID>",
    "PV":       "<UUID>",
    "Vbat":     "<UUID>"
}
```

- `vz_host_port` — Host und Port des volkszählers (`data.json`-API)
- `vz_chans` — die Kanal-UUIDs der eigenen Anlage

Die übrigen Schlüssel (`reserve_pct`, `limit_discharge_rate`, `latitude`/`longitude`, `average_days`, `day_weights_pct`, die Wirkungsgrade, `max_days_empty_battery`) haben brauchbare Vorgabewerte und können zunächst unverändert bleiben.

- `reserve_pct` (Standard 90) bestimmt, wie viel Prozent des berechneten roten Bedarfs als Rot-Reserve zurückgehalten wird — und skaliert im gleichen Verhältnis auch das Viertelstunden-Energiebudget nicht-priorisierter roter Stunden (siehe unten) sowie die wallbox-eigene Reserveschätzung.
- Der Standort (`latitude`/`longitude`, Standard ~Mitte Deutschland) steuert das Klarhimmel-Modell und die Strahlungsprognose.
- `day_weights_pct` gewichtet einzelne Tage des Mittels stärker (chronologisch, Index −1 = gestern, Index 0 = gleicher Wochentag der Vorwoche); die Länge muss `average_days` entsprechen, sonst wird gleich gewichtet.

**Der Deckel in nicht-priorisierten roten Stunden besteht aus zwei gleichzeitig geltenden Limits.** Ein Leistungs-Deckel `limit_discharge_rate` (Watt) in `dirt_shift.conf` — anlagenspezifisch, an die eigene gewöhnliche Spitzenlast anzupassen; sehr hoch gesetzt wirkt er de facto nicht mehr. Und ein festes Viertelstunden-Energiebudget von `¼ × (reserve_pct × basic_load − erwartete PV)` dieser Stunde (mindestens 0), das keinen eigenen Konfigurationseintrag braucht, da es sich direkt aus `basic_load`, `reserve_pct` und der Strahlungsprognose ergibt: `reserve_pct` skaliert dabei denselben Anteil des Bedarfs wie bei der Rot-Reserve selbst, statt nicht-priorisierten Stunden den vollen Bedarf zu erlauben; die erwartete PV wird davon unabhängig in voller Höhe abgezogen, da `pvpt` sie ohnehin schon direkt durchleitet. Beide Deckel zusammen schließen sich gegenseitig die Lücke: Der Watt-Deckel allein ließe eine dauerhaft anliegende, aber unterhalb der Schwelle bleibende Last unbegrenzt durch; das Wh-Budget allein ließe eine kurze Lastspitze noch durch, bevor es aufgebraucht ist. Details stehen in `dirt_shift_spec_de.md`, Abschnitt „Ausgabe: timer.txt".

**`precharge_enabled` (Standard `false`, optional/experimentell):** Aktiviert einen zusätzlichen Pfad, der in einer einzelnen grünen Stunde gezielt PV-Überschuss in den Akku statt ins Haus lenkt (`pvpt` wird dort gedrosselt) — die einzige Ausnahme von der sonst uneingeschränkten `pvpt`-Garantie. Greift nur, wenn die natürliche Aufladung sonst nicht reicht und sich der Rundlaufverlust gegenüber der eingesparten CO₂-Last lohnt. Details und die genaue Formel stehen in `dirt_shift_spec_de.md`, Abschnitt „Precharge".

**SMARD ist Voraussetzung.** `dirt_shift` bezieht reale Day-Ahead-Netzdaten (Bundesnetzagentur, kostenlos, ohne Anmeldung) für heute **und morgen** und leitet daraus die Zonen ab (Median-Schnitt: sauberere Tageshälfte grün, dreckigere rot); eine alternative Zonenquelle gibt es nicht. Schlägt die Abfrage fehl, überbrückt der Cache genau **einen Tag**; danach bricht `dirt_shift` ab und hinterlässt einen „Alles-erlaubt"-Timer. Optional kann `vz_dirtiness_uuid` (eine vorher in volkszähler angelegte Kanal-UUID) gesetzt werden, damit `dirt_shift` den aktuellen Dreckigkeitswert bei jedem Lauf per HTTP-POST in volkszähler protokolliert (leer = deaktiviert).

Die Strahlungsprognose (Open-Meteo, `shortwave_radiation`, kostenlos, ohne Anmeldung) skaliert die empirische PV-Referenzkurve auf das tatsächliche Tageswetter (heute und morgen, kein API-Key nötig).

### 3. basic_load-Formel an die eigene Anlage anpassen

`basic_load` ist der tatsächliche Hausverbrauch. Die Standardformel in `get_average` lautet:

```python
hours['basic_load'][i] = (hours['Import'][i] + abs(hours['Inverter'][i])
                     - hours['Auto'][i])
```

Diese Formel bildet eine bestimmte Anlagenkonfiguration ab und muss an die eigene Anlage angepasst werden. Abgezogen werden nur planbare Lasten, die nicht aus der Rot-Reserve gedeckt werden sollen (das Auto wird gezielt geladen, unabhängig von der Reserve-Rechnung); bedarfsgetriebene Lasten (z. B. eine Klimaanlage) bleiben im Verbrauch. Nicht vorhandene Kanäle werden weggelassen, zusätzliche ergänzt:

- ohne separat erfasste Wallbox entfällt der `Auto`-Term
- ein weiterer gesondert erfasster Verbraucher (etwa ein PV-Akku-Lader) käme als zusätzlicher Abzugsterm hinzu

Maßgeblich ist, dass `basic_load` am Ende den tatsächlich zu deckenden Hausverbrauch ergibt. Wird ein Kanal aus der Formel entfernt, kann er auch aus `vz_chans` gestrichen werden.

**Wichtig zur Wallbox:** Weil `Auto` bewusst aus `basic_load` ausgeklammert ist, sieht `dirt_shift` eine Wallbox-Ladung nicht direkt. Der Schutz davor, dass eine Wallbox-Spitze den Akku statt des Netzes belastet, kommt stattdessen aus der Zonenlogik selbst — siehe „Entladung nach Zone" in der Spec (grüne Zone stoppt kategorisch, solange die Reserve nicht erreicht ist; rote Zone deckelt außerhalb der dreckigsten Stunde auf `limit_discharge_rate` und gleichzeitig auf `¼ × (reserve_pct × basic_load − erwartete PV)` je Viertelstunde).

**Optional: aktive Wallbox-Steuerung (`wallbox_enabled`, Standard `false`).** Unabhängig von der passiven Ausklammerung oben kann `dirt_shift` das Relais einer Wallbox über ein Tasmota-Gerät aktiv schalten — eingeschaltet, sobald **entweder** zwei konfigurierbare Sauberkeits-Schwellen (`wallbox_median_fraction`, `wallbox_absolute_max`, dazu `mode == free`) **oder** genug Akkuinhalt für eine wallbox-eigene Reserveschätzung übrig ist; abgeschaltet erst, wenn **beide** Bedingungen gleichzeitig wegfallen. Ausgeschaltet wird dabei nur, was `dirt_shift` selbst eingeschaltet hat (manuelle Aktivierung bleibt unangetastet, egal wie dreckig oder energiearm es wird, siehe Spec-Abschnitt „Wallbox"). Details, inklusive der Retry-/Verifikationslogik beim Schalten, stehen in `dirt_shift_spec_de.md`.

### 4. Trockenlauf zur Prüfung

Vor der Aktivierung empfiehlt sich ein Lauf ohne Schreiben der Timer-Datei:

```bash
cd /opt/zeroinput/dirt_shift
# disable_zeroinput_timer in dirt_shift.conf vorübergehend auf true setzen
python3 dirt_shift.py -v -debug
```

Die ausführliche Ausgabe (`-v`) zeigt die aktuelle Zone (rot/grün), den Akkuinhalt, die Rot-Reserve, und bei knapper Reserve die ermittelte dreckigste Stunde im Fenster, sowie den gewählten Entlademodus. `-debug` zeigt zusätzlich die stündliche Übersichtstabelle (PV-Referenzkurve, Strahlungsprognose, Klarhimmel-Index, erwartete PV, `basic_load`, Lade-/Entlade-Markierung, Dreckigkeit, Zone) sowie die geschriebenen Timer-Zeilen. Mit `-avgnew` werden alle Caches (7-Tage-Mittel, PV-Kurve, Strahlungsprognose, SMARD) verworfen und neu abgefragt.

Stimmen die Werte plausibel (Zone zur Netzlage passend, Reserve im erwarteten Bereich), kann `disable_zeroinput_timer` wieder auf `false` gesetzt werden.

### 5. Cron-Eintrag

`dirt_shift` soll viertelstündlich laufen, und zwar **zu** den Viertelstunden-Marken (`0,15,30,45`), nicht kurz davor:

```bash
crontab -e
```

```cron
0,15,30,45 * * * * cd /opt/zeroinput/dirt_shift && /usr/bin/python3 dirt_shift.py >/dev/null 2>&1
```

**Warum genau diese Minuten und nicht z. B. `59,14,29,44`:** `dirt_shift` rundet den aktuellen Slot beim Schreiben der Timer-Zeile immer nach **unten** auf die laufende Viertelstunde (`now.minute // 15 * 15`). Ein Lauf eine Minute vor der Marke liegt noch im **alten** Slot und schreibt dessen (veraltete) Policy — der neue Slot bekäme seinen korrekten Eintrag dann erst 14 Minuten nach seinem eigentlichen Beginn. Mit `0,15,30,45` beginnt jeder Lauf exakt am Slot-Anfang, die Verzögerung schrumpft auf reinen Cron-Dispatch-Jitter (Sekunden).

Bei jedem Lauf wird der frische Energieinhalt abgefragt und die `timer.txt` mit dem aktuellen Slot plus einer 30-Minuten-Ausfallsicherung neu geschrieben, jeweils mit dem echten Kalenderdatum. Die übrigen Caches (7-Tage-Mittel, PV-Kurve, Strahlungsprognose, SMARD) werden intern jeweils stündlich bedient und nur bei Bedarf neu geholt — die viertelstündlichen Läufe belasten volkszähler, Open-Meteo und SMARD also gering.

---

## Betrieb

### Kommandozeilenoptionen

- `-v` — ausführliche Konsolenausgabe
- `-html` — HTML-Kopf/-Fuß um die Ausgabe (für die Einbindung in eine Weboberfläche)
- `-debug` — mehr Ausgabe, zeigt auch die stündliche Übersichtstabelle und die Timer-Zeilen (impliziert `-v`)
- `-avgnew` — erzwingt eine frische Abfrage aller Caches (7-Tage-Mittel, PV-Kurve, Strahlungsprognose, SMARD-Zonen)
- `-h` — Kurzhilfe

### Zusammenspiel mit zeroinput

`dirt_shift` schreibt nur die `timer.txt`. Die eigentliche Umsetzung — Entladegrenze, PV-Durchleitung, Stufenverteilung — macht zeroinput. Die direkte PV-Durchleitung (`pvpt`) wird in jeder Timer-Zeile mit `ac 100%` garantiert; `dirt_shift` begrenzt ausschließlich die Batterieentladung.

### Fehlerverhalten

Bei einem harten Fehler (volkszähler liefert keine vollständigen Tage, Energieinhalt nicht berechenbar, SMARD-Daten weder frisch noch als Ein-Tag-Ersatz verfügbar) bricht `dirt_shift` ab, schreibt aber zuvor — sofern der Timer-Pfad bekannt ist — eine „Alles-erlaubt"-Zeile mit dem aktuellen Datum, damit zeroinput nicht durch eine veraltete Begrenzung blockiert wird:

```
2026-07-09 00:00:00 100 100 -1
```

Da die Zeile ein echtes Kalenderdatum trägt, bleibt der freie Zustand automatisch dauerhaft bestehen, sobald der Tag vorbei ist — zeroinputs Timer-Parser übernimmt beim Durchlaufen aller bereits vergangenen Zeilen zuletzt genau diese, ohne dass die Datei erneut geschrieben werden müsste.

Ist nicht einmal die `zeroinput.conf` lesbar (Timer-Pfad unbekannt), bleibt nur der Abbruch mit Fehlermeldung. In beiden Fällen schreibt cron nichts ins Log, solange `>/dev/null 2>&1` gesetzt ist — zur Fehlersuche diese Umleitung vorübergehend entfernen oder `dirt_shift.py -v` von Hand starten.

---

## Fehlerbehebung

**Die timer.txt wird nicht geschrieben.** Prüfen, ob `disable_zeroinput_timer` auf `false` steht und der aus `zeroinput.conf` gelesene `discharge_t_file`-Pfad beschreibbar ist. Ein Handlauf mit `-v` zeigt den aufgelösten Pfad.

**„cannot read zeroinput.conf".** Der Pfad in `zeroinput_conf` stimmt nicht. Er wird relativ zum Verzeichnis von `dirt_shift.py` aufgelöst.

**„no complete days returned by volkszähler".** Der volkszähler hat für den abgefragten Zeitraum keine vollständigen Tage. Erst nach einigen Betriebstagen liefert die Mittelung sinnvolle Werte. Bis dahin greift der Frei-Timer-Fallback.

**Abbruch mit „SMARD zone data unavailable".** Die SMARD-Abfrage schlägt fehl und der Cache ist älter als einen Tag. `dirt_shift` hinterlässt einen „Alles-erlaubt"-Timer, zeroinput läuft also uneingeschränkt weiter. Netzwerkverbindung und Erreichbarkeit von SMARD prüfen; ein Handlauf mit `-v` zeigt den Grund.

**Zone wirkt falsch, oder es werden noch drei Zonen (inkl. „gelb") angezeigt.** `dirt_shift` kennt seit der Umstellung auf den Median-Schnitt nur noch zwei Zonen (rot/grün). Tauchen weiterhin gelbe Stunden auf, liegt das an einer **veralteten Cache-Datei** (`dirt_smard_cache.json`) aus einem Lauf vor der Umstellung — SMARD wird nur einmal pro Stunde neu abgefragt. Abhilfe: `-avgnew` erzwingt eine sofortige Neuabfrage, oder die Cache-Datei löschen.

**Reserve wird nicht wie erwartet geschützt.** Mit `-debug` zeigt die stündliche Übersichtstabelle die tatsächliche `dirt%`- und `zone`-Einstufung pro Stunde sowie die `chg`-Spalte (`L`/`D`/`!D`/`!L`). Die Zeile `content ... Wh   reserve(...%) ... Wh -> dirtiest  HH:MM   =>  mode: ...` in der `-v`-Ausgabe zeigt, welche Stunde aktuell als dreckigste im Fenster gilt. Weicht das deutlich von der Erwartung ab, meist verursacht durch eine unpassende `basic_load`-Formel (Schritt 3) oder eine Strahlungsprognose, die nicht zum tatsächlichen Wetter passt (`-debug`-Tabelle, Spalte `clr%`).
