# dirt_shift – Installationsanleitung

## Überblick

`dirt_shift` verschiebt die Batterieentladung in die ökologisch ungünstigen Nachtstunden und schreibt dazu die `timer.txt`, die zeroinput für die Entladesteuerung liest. Es läuft periodisch (viertelstündlich per cron), nicht als Dauerdienst.

Voraussetzung ist eine funktionierende zeroinput-Installation mit aktiviertem Entladetimer und ein erreichbarer volkszähler mit der `data.json`-HTTP-API. `dirt_shift` ist eine Alternative zum preisgesteuerten Tool `tib_zero_tas.py`: Beide schreiben dieselbe `timer.txt`, sollten also nicht gleichzeitig laufen. Wer einen dynamischen Stromtarif (z.B. Tibber) nutzt, ist mit der preisgesteuerten Variante meist besser bedient; `dirt_shift` richtet sich an Anlagen ohne dynamischen Tarif, bei denen die CO₂-Bilanz das Steuerungsziel ist.

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

`dirt_shift` liest aus `zeroinput.conf` den Wert `single_inverter_threshold` und den Pfad `discharge_t_file` (relativ zur `zeroinput.conf` aufgelöst). Dadurch schreibt `dirt_shift` garantiert dieselbe Datei, die zeroinput liest. `zeroinput.conf` wird dabei nur gelesen, nie verändert.

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

- `vz_host_port` — Host und Port des volkszählers (data.json-API)
- `vz_chans` — die Kanal-UUIDs der eigenen Anlage

Die übrigen Schlüssel (`reserve_pct`, `build_reserve_after`, die `latitude`/`longitude` und CO₂-Intensitäts-Parameter, `average_days`, `day_weights_pct`, die Wirkungsgrade, `max_days_empty_battery`) haben brauchbare Vorgabewerte und können zunächst unverändert bleiben. `build_reserve_after` (Standard 13:30) legt fest, ab wann die Nachtreserve geschützt wird; davor entlädt der Akku frei in alles, was gerade hohe CO₂-Intensität hat. Der Standort (`latitude`/`longitude`, Standard ~Mitte Deutschland) steuert die Sonnenstandsberechnung für das CO₂-Intensitätsprofil — für eine grobe Steuerung genügt der Default, für die eigene Region kann er gesetzt werden. `day_weights_pct` gewichtet einzelne Tage des Mittels stärker (chronologisch, Index −1 = gestern, Index 0 = gleicher Wochentag der Vorwoche); die Länge muss `average_days` entsprechen, sonst wird gleich gewichtet.

### 3. basic_load-Formel an die eigene Anlage anpassen

`basic_load` ist der tatsächliche Hausverbrauch. Die Standardformel in `get_average` lautet:

```python
hours['basic_load'][i] = (hours['Import'][i] + abs(hours['Inverter'][i])
                     - hours['Auto'][i])
```

Diese Formel bildet eine bestimmte Anlagenkonfiguration ab und ist **kein Standard** — sie muss an die eigene Anlage angepasst werden. Abgezogen werden nur planbare Lasten, die nicht aus dem Nachtbudget gedeckt werden sollen; bedarfsgetriebene Lasten (z. B. eine Klimaanlage) bleiben im Verbrauch. Nicht vorhandene Kanäle werden weggelassen, zusätzliche ergänzt:

- ohne separat erfasste Wallbox entfällt der `Auto`-Term
- ein weiterer gesondert erfasster Verbraucher (etwa ein PV-Akku-Lader) käme als zusätzlicher Abzugsterm hinzu

Maßgeblich ist, dass `basic_load` am Ende den tatsächlich zu deckenden Hausverbrauch ergibt. Wird ein Kanal aus der Formel entfernt, kann er auch aus `vz_chans` gestrichen werden.

### 4. Trockenlauf zur Prüfung

Vor der Aktivierung empfiehlt sich ein Lauf ohne Schreiben der Timer-Datei:

```bash
cd /opt/zeroinput/dirt_shift
# disable_zeroinput_timer in dirt_shift.conf vorübergehend auf true setzen
python3 dirt_shift.py -v
```

Die ausführliche Ausgabe (`-v`) zeigt Sonnenauf-/-untergang, die aktuelle Intensitätszone (rot/gelb/grün), die Nachtreserve, den Energieinhalt und den gewählten Entlademodus. Mit `-debug` werden zusätzlich die Timer-Zeilen ausgegeben. Mit `-avgnew` wird die zwischengespeicherte 7-Tage-Mittelung verworfen und neu abgefragt.

Stimmen die Werte plausibel (Intensitätszone zur Tageszeit passend, Reserve im erwarteten Bereich), kann `disable_zeroinput_timer` wieder auf `false` gesetzt werden.

### 5. Cron-Eintrag

`dirt_shift` soll viertelstündlich laufen:

```bash
crontab -e
```

```cron
*/15 * * * * cd /opt/zeroinput/dirt_shift && /usr/bin/python3 dirt_shift.py >/dev/null 2>&1
```

Bei jedem Lauf wird der frische Energieinhalt abgefragt und die `timer.txt` mit den zukünftigen Slots neu geschrieben. Die 7-Tage-Mittelung wird intern stündlich aus `dirt_avg_cache.json` bedient und nur bei Bedarf neu geholt — die viertelstündlichen Läufe belasten den volkszähler also gering.

---

## Betrieb

### Kommandozeilenoptionen

- `-v` — ausführliche Konsolenausgabe
- `-html` — HTML-Kopf/-Fuß um die Ausgabe (für die Einbindung in eine Weboberfläche)
- `-debug` — mehr Ausgabe, zeigt auch die Timer-Zeilen (impliziert `-v`)
- `-avgnew` — erzwingt eine frische 7-Tage-Mittelung statt des Caches
- `-h` — Kurzhilfe

### Zusammenspiel mit zeroinput

`dirt_shift` schreibt nur die `timer.txt`. Die eigentliche Umsetzung — Entladegrenze, PV-Durchleitung, Stufenverteilung — macht zeroinput. Die direkte PV-Durchleitung (`pvpt`) wird in jeder Timer-Zeile mit `ac 100%` garantiert; `dirt_shift` begrenzt ausschließlich die Batterieentladung.

### Fehlerverhalten

Bei einem harten Fehler (volkszähler liefert keine vollständigen Tage, Energieinhalt nicht berechenbar) bricht `dirt_shift` ab, schreibt aber zuvor — sofern der Timer-Pfad bekannt ist — eine „Alles-erlaubt"-Zeile, damit zeroinput nicht durch eine veraltete Begrenzung blockiert wird:

```
0000-00-00 00:00:00 100 100 99999
```

Ist nicht einmal die `zeroinput.conf` lesbar (Timer-Pfad unbekannt), bleibt nur der Abbruch mit Fehlermeldung. In beiden Fällen schreibt cron nichts ins Log, solange `>/dev/null 2>&1` gesetzt ist — zur Fehlersuche diese Umleitung vorübergehend entfernen oder `dirt_shift.py -v` von Hand starten.

---

## Fehlerbehebung

**Die timer.txt wird nicht geschrieben.** Prüfen, ob `disable_zeroinput_timer` auf `false` steht und der aus `zeroinput.conf` gelesene `discharge_t_file`-Pfad beschreibbar ist. Ein Handlauf mit `-v` zeigt den aufgelösten Pfad.

**„cannot read zeroinput.conf".** Der Pfad in `zeroinput_conf` stimmt nicht. Er wird relativ zum Verzeichnis von `dirt_shift.py` aufgelöst.

**„no complete days returned by volkszähler".** Der volkszähler hat für den abgefragten Zeitraum keine vollständigen Tage. Erst nach einigen Betriebstagen liefert die Mittelung sinnvolle Werte. Bis dahin greift der Frei-Timer-Fallback.

**Intensitätszone wirkt falsch.** Die Zone ergibt sich aus dem Sonnenstand (Datum + `latitude`/`longitude`) und den festen Schranken `green_earliest`/`green_latest`. Mit `-v` lassen sich Sonnenauf-/-untergang und die berechnete Zone prüfen. Ein falsch gesetzter Standort oder unpassende Schranken sind die häufigste Ursache.
