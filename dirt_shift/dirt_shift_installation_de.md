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

`dirt_shift` liest aus `zeroinput.conf` nur den Pfad `discharge_t_file` (relativ zur `zeroinput.conf` aufgelöst) sowie `cell_count`. Dadurch schreibt `dirt_shift` garantiert dieselbe Datei, die zeroinput liest. `zeroinput.conf` wird dabei nur gelesen, nie verändert. Alle `dirt_shift`-eigenen Parameter, darunter der Entlade-Deckel `yellow_cap`, leben ausschließlich in `dirt_shift.conf`.

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

Die übrigen Schlüssel (`reserve_pct`, `build_reserve_after`, die `latitude`/`longitude` und CO₂-Intensitäts-Parameter, `yellow_cap`, `average_days`, `day_weights_pct`, die Wirkungsgrade, `max_days_empty_battery`) haben brauchbare Vorgabewerte und können zunächst unverändert bleiben.

- `build_reserve_after` (Standard 13:30) legt eine feste Uhrzeit fest, ab der die Rot-Reserve geschützt wird, falls überhaupt keine PV-Prognose zustande kommt. Liegt eine PV-Prognose vor, entscheidet eine laufende Energiebilanz-Projektion (aktueller Akkuinhalt plus erwarteter PV-Ertrag minus erwarteter Verbrauch, bis zur nächsten PV-Überschussstunde — demselben Fenster, über das auch die Rot-Reserve selbst berechnet wird), wann der Schutz einsetzt — unabhängig von der Uhrzeit.
- `yellow_cap` (Standard 600 W) begrenzt die Entladeleistung in der gelben Übergangszone, damit kurzzeitige Lastspitzen die Rot-Reserve nicht anzapfen. Ein fester, bewusst gesetzter Wert — kein aus Prognosedaten abgeleiteter.
- Der Standort (`latitude`/`longitude`, Standard ~Mitte Deutschland) steuert die Sonnenstandsberechnung, die als Fallback-Profil dient und auch dann verwendet wird, wenn SMARD aktiv, aber gerade nicht verfügbar ist.
- `day_weights_pct` gewichtet einzelne Tage des Mittels stärker (chronologisch, Index −1 = gestern, Index 0 = gleicher Wochentag der Vorwoche); die Länge muss `average_days` entsprechen, sonst wird gleich gewichtet.

**Optional: SMARD.** Mit `"smard_enabled": true` bezieht `dirt_shift` reale Day-Ahead-Netzdaten (Bundesnetzagentur, kostenlos, ohne Anmeldung) für heute **und morgen** und leitet daraus die Zonen ab. Ist SMARDs Abfrage für eine Stunde (noch) nicht verfügbar, wird für genau diese Stunde die Sonnenstandsberechnung herangezogen; der Lauf läuft normal weiter. Zusätzlich kann `vz_dirtiness_uuid` (eine vorher in volkszähler angelegte Kanal-UUID) gesetzt werden, damit `dirt_shift` den aktuellen Dreckigkeitswert bei jedem Lauf per HTTP-POST in volkszähler protokolliert (leer = deaktiviert).

Die Strahlungsprognose (Open-Meteo, `shortwave_radiation`, kostenlos, ohne Anmeldung) läuft unabhängig von `smard_enabled` immer mit und skaliert die empirische PV-Referenzkurve auf das tatsächliche Tageswetter (heute und morgen, ebenfalls kostenlos, kein API-Key nötig).

### 3. basic_load-Formel an die eigene Anlage anpassen

`basic_load` ist der tatsächliche Hausverbrauch. Die Standardformel in `get_average` lautet:

```python
hours['basic_load'][i] = (hours['Import'][i] + abs(hours['Inverter'][i])
                     - hours['Auto'][i])
```

Diese Formel bildet eine bestimmte Anlagenkonfiguration ab und muss an die eigene Anlage angepasst werden. Abgezogen werden nur planbare Lasten, die nicht aus der Rot-Reserve gedeckt werden sollen; bedarfsgetriebene Lasten (z. B. eine Klimaanlage) bleiben im Verbrauch. Nicht vorhandene Kanäle werden weggelassen, zusätzliche ergänzt:

- ohne separat erfasste Wallbox entfällt der `Auto`-Term
- ein weiterer gesondert erfasster Verbraucher (etwa ein PV-Akku-Lader) käme als zusätzlicher Abzugsterm hinzu

Maßgeblich ist, dass `basic_load` am Ende den tatsächlich zu deckenden Hausverbrauch ergibt. Wird ein Kanal aus der Formel entfernt, kann er auch aus `vz_chans` gestrichen werden.

### 4. Trockenlauf zur Prüfung

Vor der Aktivierung empfiehlt sich ein Lauf ohne Schreiben der Timer-Datei:

```bash
cd /opt/zeroinput/dirt_shift
# disable_zeroinput_timer in dirt_shift.conf vorübergehend auf true setzen
python3 dirt_shift.py -v -debug
```

Die ausführliche Ausgabe (`-v`) zeigt Sonnenauf-/-untergang, die aktuelle Intensitätszone (rot/gelb/grün), die Rot-Reserve samt prognostiziertem Aufbau-Zeitpunkt (`>HH:MM`), den Energieinhalt und den gewählten Entlademodus. `-debug` zeigt zusätzlich die stündliche Übersichtstabelle (PV-Referenzkurve, Strahlungsprognose, Klarhimmel-Index, erwartete PV, Dreckigkeit, Zone) sowie die geschriebenen Timer-Zeilen. Mit `-avgnew` werden alle Caches (7-Tage-Mittel, PV-Kurve, Strahlungsprognose, SMARD) verworfen und neu abgefragt.

Stimmen die Werte plausibel (Intensitätszone zur Tageszeit passend, Reserve im erwarteten Bereich), kann `disable_zeroinput_timer` wieder auf `false` gesetzt werden.

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

Bei einem harten Fehler (volkszähler liefert keine vollständigen Tage, Energieinhalt nicht berechenbar) bricht `dirt_shift` ab, schreibt aber zuvor — sofern der Timer-Pfad bekannt ist — eine „Alles-erlaubt"-Zeile mit dem aktuellen Datum, damit zeroinput nicht durch eine veraltete Begrenzung blockiert wird:

```
2026-07-08 00:00:00 100 100 99999
```

Da die Zeile ein echtes Kalenderdatum trägt, bleibt der freie Zustand automatisch dauerhaft bestehen, sobald der Tag vorbei ist — zeroinputs Timer-Parser übernimmt beim Durchlaufen aller bereits vergangenen Zeilen zuletzt genau diese, ohne dass die Datei erneut geschrieben werden müsste.

Ist nicht einmal die `zeroinput.conf` lesbar (Timer-Pfad unbekannt), bleibt nur der Abbruch mit Fehlermeldung. In beiden Fällen schreibt cron nichts ins Log, solange `>/dev/null 2>&1` gesetzt ist — zur Fehlersuche diese Umleitung vorübergehend entfernen oder `dirt_shift.py -v` von Hand starten.

---

## Fehlerbehebung

**Die timer.txt wird nicht geschrieben.** Prüfen, ob `disable_zeroinput_timer` auf `false` steht und der aus `zeroinput.conf` gelesene `discharge_t_file`-Pfad beschreibbar ist. Ein Handlauf mit `-v` zeigt den aufgelösten Pfad.

**„cannot read zeroinput.conf".** Der Pfad in `zeroinput_conf` stimmt nicht. Er wird relativ zum Verzeichnis von `dirt_shift.py` aufgelöst.

**„no complete days returned by volkszähler".** Der volkszähler hat für den abgefragten Zeitraum keine vollständigen Tage. Erst nach einigen Betriebstagen liefert die Mittelung sinnvolle Werte. Bis dahin greift der Frei-Timer-Fallback.

**Intensitätszone wirkt falsch (Sonnenstand-Fallback).** Ohne SMARD (oder wenn SMARD gerade fehlschlägt) ergibt sich die Zone aus dem Sonnenstand (Datum + `latitude`/`longitude`) und den festen Schranken `green_earliest`/`green_latest`. Mit `-v` lassen sich Sonnenauf-/-untergang und die berechnete Zone prüfen. Ein falsch gesetzter Standort oder unpassende Schranken sind die häufigste Ursache.

**Intensitätszone wirkt falsch (SMARD aktiv).** Mit `-debug` zeigt die stündliche Übersichtstabelle die tatsächliche `dirt%`-Einstufung pro Stunde. SMARDs Perzentil-Einteilung orientiert sich am realen Tagesverlauf, daher kann die rote Phase zu jeder Tageszeit liegen, nicht nur nachts.

**Reserve wird zu früh oder zu spät geschützt.** Die Zeile `forecast: content ... reserve target (>HH:MM)` in der `-v`-Ausgabe zeigt den prognostizierten Zeitpunkt, ab dem der Schutz voraussichtlich einsetzt. Weicht das deutlich von der Erwartung ab, meist verursacht durch eine unpassende `basic_load`-Formel (Schritt 3) oder eine Strahlungsprognose, die nicht zum tatsächlichen Wetter passt (`-debug`-Tabelle, Spalte `clr%`).
