# dirt_shift – Funktionsspezifikation
*v1.0*

## Zweck

`dirt_shift` verschiebt die Batterieentladung gezielt in die Netzstunden mit der höchsten CO₂-Intensität. Der deutsche Strommix ist abends und nachts ungünstiger (PV weg, Abendlast hoch, fossile Spitzen) und früh morgens, bis genug PV im Netz ist; tagsüber ist seine CO₂-Intensität niedrig. Wenn man wählen kann, *wann* die Batterie statt des Netzes den Verbrauch deckt, vermeidet man in den Stunden hoher CO₂-Intensität am meisten Emissionen. `dirt_shift` lenkt daher den gesamten verfügbaren Akkuinhalt in diese Stunden — am stärksten in die mit der höchsten Intensität.

Die CO₂-Intensität ist eine reine **Netz**-Eigenschaft und hängt nicht von der eigenen Anlage ab. Die eigene Anlage (PV, Verbrauch, Akkuinhalt) bestimmt nur die *Menge* der verfügbaren und nachts benötigten Energie.

`dirt_shift` ist ein eigenständiges Tool aus derselben Familie wie `tib_zero_tas.py` und eine **Alternative** zu dessen preisgesteuertem Ansatz. Beide schreiben dieselbe `timer.txt` und sollten nicht gleichzeitig laufen. Wer einen dynamischen Stromtarif (z.B. Tibber) nutzt, ist mit der preisgesteuerten Variante meist besser bedient; `dirt_shift` richtet sich an Anlagen ohne dynamischen Tarif, bei denen die CO₂-Bilanz das Steuerungsziel ist. Es werden **keine** Netzdaten abgerufen — das CO₂-Intensitätsprofil wird allein aus dem berechneten Sonnenstand plus festen Last-Schranken abgeleitet (siehe CO₂-Intensitätsprofil).

Die direkte PV-Durchleitung (`pvpt`, PV pass-through) wird **immer** garantiert, unabhängig von allem. Damit ist gemeint, dass momentan erzeugte PV-Leistung direkt zur Deckung des Hausverbrauchs durchgereicht wird, ohne den Umweg über die Batterie. Das hat den besten Wirkungsgrad (kein Lade- und Entladeverlust) und schont den Akku (kein zusätzlicher Zyklus). `dirt_shift` greift ausschließlich an der **Batterieentladung** an, nie an `pvpt`.

---

## Datenquellen

Alle Daten stammen aus dem volkszähler (gleiche Kanäle wie `tib_zero_tas`):

- **basic_load** — der tatsächliche Hausverbrauch in Wh/h, in der Standardformel berechnet als `Import + |Inverter| − Auto`. 7-Tage-Stundenmittel, stündlich in `dirt_avg_cache.json` zwischengespeichert. Dient der Mengenabschätzung der Nachtreserve.
- **Energieinhalt** — der reale Akkuinhalt in Wh, rekonstruiert über `get_vz_bat_cap` durch Integration von PV und Inverter seit dem letzten bekannten „leer"-Zustand (Spannung ≤ 3,0625 V/Zelle als Anker, also 49 V bei 16 Zellen; skaliert mit `cell_count` aus der zeroinput-Konfiguration), mit Wirkungsgraden. Bei jedem Lauf frisch abgefragt.

> **basic_load ist frei anpassbar.** Die Formel `Import + |Inverter| − Auto` bildet eine bestimmte Anlagenkonfiguration ab (mit E-Auto-Wallbox als gesondertem Kanal, der vom Hausverbrauch abgezogen wird). Sie ist **kein Standard**, sondern an die eigene Anlage anzupassen: nicht vorhandene Kanäle werden weggelassen, zusätzliche ergänzt. Ohne separat erfasste Wallbox entfällt `Auto`; ein zusätzlicher gesondert erfasster Verbraucher (etwa ein PV-Akku-Lader) käme als weiterer Abzugsterm hinzu. Abgezogen werden nur **planbare** Lasten, die nicht aus dem Nachtbudget gedeckt werden sollen (das Auto wird gezielt geladen). Bedarfsgetriebene Lasten wie eine Klimaanlage bleiben dagegen **im** basic_load — sie gehören zum nachts zu deckenden Verbrauch und sind über das 7-Tage-Mittel in Grenzen vorhersagbar erfasst. Maßgeblich ist, dass basic_load am Ende den **tatsächlich zu deckenden Hausverbrauch** ergibt — also Bezug plus die vom Akku gelieferte Wechselrichterleistung, bereinigt um alles, was nicht aus dem Akku/Netz gedeckt werden soll. Die Berechnung steht in `get_average` und wird dort direkt editiert; entsprechend wird der Kanalsatz in `vz_chans` reduziert oder erweitert.

Die 7-Tage-Basis (`average_days`) enthält genau eine volle Wochenstruktur — jeder Wochentag ist einmal vertreten, das Mittel ist über die Woche balanciert. Über `day_weights_pct` lassen sich einzelne Tage höher gewichten (siehe Konfiguration), etwa gestern und der gleiche Wochentag der Vorwoche; ohne Gewichtung zählt jeder Tag gleich.

---

## CO₂-Intensitätsprofil

Die CO₂-Intensität des Netzes wird ohne externe Daten aus dem **Sonnenstand** plus festen **Last-Schranken** abgeleitet. Sonnenauf- und -untergang werden astronomisch aus Datum und Standort (`latitude`/`longitude`) berechnet (einfache Näherung, auf ein, zwei Minuten genau — gegenüber den Stunden-Schranken irrelevant; Sommer-/Winterzeit wird über die Systemzeitzone berücksichtigt).

Der Erneuerbaren-Anteil im Netz läuft dem Sonnenstand hinterher: morgens sinkt die CO₂-Intensität erst, wenn genug PV relativ zur **Last** im Netz ist (die Last fährt uhrzeitgesteuert hoch), abends steigt sie, bevor die Sonne weg ist (PV fällt, Abendlast steigt). Deshalb ist das günstige (niedrig-intensive, „grüne") Fenster ein **Hybrid** aus Sonnenstand und festen Uhrzeit-Schranken:

```
grün-Start = max(Sonnenaufgang + green_morning_offset_h, green_earliest)
grün-Ende  = min(Sonnenuntergang − red_evening_offset_h,  green_latest)
```

Im Sommer dominieren die festen Schranken (`green_earliest` ~09:00, `green_latest` ~17:30) — der sehr frühe Sonnenaufgang senkt die Netz-Intensität nicht schon um 7 Uhr, weil die Last erst später hochfährt. Im Winter dominiert der spätere Sonnenaufgang / frühere Sonnenuntergang, das günstige Fenster schrumpft von selbst.

Drei Zonen:

- **rot** (höchste Intensität) — außerhalb des günstigen Fensters: Abend, Nacht, früher Morgen
- **gelb** (Übergang) — je `yellow_width_h` an beiden Rändern des grünen Fensters
- **grün** (niedrige Intensität) — die Tagesmitte

---

## Entladung nach Zone

Pro Lauf (¼-stündlich) wird aus der aktuellen Zone das Entladeverhalten bestimmt. Der gesamte Akkuinhalt wird in die Stunden hoher CO₂-Intensität gelenkt, am stärksten in die intensivsten:

- **rot** → **kein Limit**: voller Entlade-Deckel (`100 100 99999`), der Akku darf ungebremst raus
- **gelb** → **`single_inverter_threshold`** als Deckel: gebremste Entladung in der Übergangszone
- **grün** → **kein Akku-Entladen**: nur `pvpt` (`000 100 000`)

`pvpt` (direkte PV-Durchleitung) läuft in allen Zonen weiter; `dirt_shift` steuert ausschließlich die Batterieentladung.

---

## Nachtreserve und build_reserve_after

Die **Nachtreserve** ist `reserve_pct` (Standard 90 %) des `basic_load`-Bedarfs über das kommende zusammenhängende **rote Fenster** (vom abendlichen Grün-Ende über die Nacht bis zum morgendlichen Grün-Start). Die 90 % sind so gewählt, dass die Reserve bis zum Morgen nahezu aufgebraucht ist.

`build_reserve_after` (Standard 13:30) steuert, ab wann diese Reserve geschützt wird:

- **vor 13:30**: keine Reserve geschützt — der Akku entlädt frei in alles, was *jetzt* hohe Intensität hat (auch das morgendliche rote Fenster). Das fährt den Akku vormittags herunter und schafft Platz für den Nachmittagsertrag.
- **ab 13:30**: Überschuss über die Reserve darf weiterhin in intensive (gelbe) Stunden raus; sobald der Inhalt aber auf die Reserve gefallen ist, wird die Entladung in nicht-roten Stunden gestoppt (nur `pvpt`), bis das rote Nachtfenster selbst beginnt. Dort wird die Reserve dann nach Zone ausgegeben (rot ohne Limit, gelb mit single-inverter-Deckel).

Der Zeitpunkt 13:30 liegt bewusst in der zweiten Tageshälfte: Der größte vormittägliche Verbrauch (Kochen) ist dann vorüber, der weitere PV-Ertrag absehbarer. Kommt nachmittags wider Erwarten kein Ertrag mehr, ist die Nacht eben teilweise aus dem Netz zu decken — `dirt_shift` ist eine Optimierung, kein kritischer Regler.

## Ausfallsicherung

`dirt_shift` ist optional und darf den Normalbetrieb von zeroinput nie blockieren. Begrenzt oder stoppt ein Lauf die Entladung (gelb/grün/Reserveschutz), schreibt er zusätzlich eine „Alles-erlaubt"-Zeile (`100 100 99999`) 30 Minuten später. Läuft das Skript weiter, wird die Begrenzung alle 15 Minuten erneuert; fällt es aus (cron-Ausfall, volkszähler nicht erreichbar, Absturz), hebt sich die Begrenzung nach 30 Minuten von selbst auf, und zeroinput entlädt wieder frei, als gäbe es `dirt_shift` nicht. Im roten Fenster (kein Limit) ist ohnehin alles erlaubt, dort genügt die eine Zeile.

---

## Ausgabe: timer.txt

`dirt_shift` schreibt `timer.txt` im zeroinput-Format:

```
0000-00-00 HH:MM:00  <entlade-W>  <ac-%>  <energie-Wh>
```

- Datum `0000-00-00` = täglich wiederkehrend (kein Kalenderdatum, der Mitternachtswechsel braucht keine Sonderbehandlung).
- **entlade-W** — Entlade-Deckel; `100` (Prozent) = kein Limit (rot), `single_inverter_threshold` (Watt) = gebremst (gelb), `000` = kein Akku-Entladen (grün/Stopp).
- **ac-%** — Wechselrichter-Durchleitung, immer `100` (pvpt garantiert).
- **energie-Wh** — Energiebudget; `99999` = praktisch unbegrenzt (Entladung erlaubt), `000` = kein Budget (Stopp).

Die drei Modi sind also: `100 100 99999` (rot, kein Limit), `<single_inv> 100 99999` (gelb, single-inverter), `000 100 000` (grün/Stopp).

Werte > 100 werden als Watt interpretiert, Werte ≤ 100 als Prozent — wie im bestehenden zeroinput-Timer-Format. Der `discharge_times`-Parser von zeroinput liest die ¼-stündliche Auflösung ohne Anpassung; er ersetzt `0000-00-00` durch das aktuelle Datum und übernimmt jeweils den letzten bereits vergangenen Slot.

Der geschriebene Plan ist kurz: der aktuelle ¼-Stunden-Slot im gewählten Modus (frei / single-inverter / Stopp), und — falls der Modus begrenzt oder stoppt — eine „Alles-erlaubt"-Zeile 30 Minuten später als Ausfallsicherung.

`dirt_shift` läuft ¼-stündlich (z. B. per cron) und schreibt die Datei jedes Mal neu mit aktuellem Energieinhalt und aktueller Zone.

---

## Konfiguration

`dirt_shift.conf` enthält **nur** den Pfad zur `zeroinput.conf` und die dirt_shift-eigenen Parameter. Werte, die bereits in `zeroinput.conf` stehen, werden von dort gelesen statt dupliziert — `dirt_shift` ändert `zeroinput.conf` nie.

Aus `zeroinput.conf` gelesen (read-only):

- **`single_inverter_threshold`** — begrenzt den Entlade-Deckel pro Slot, damit die Nachtentladung auf Stufe 1 bleibt.
- **`discharge_t_file`** — der Pfad der Timer-Datei, die zeroinput liest. `dirt_shift` schreibt genau diese Datei (relativ zur zeroinput.conf aufgelöst). Damit zeigen Schreiber und Leser garantiert auf dieselbe Datei. In `zeroinput.conf` muss zusätzlich `discharge_timer` aktiviert sein, sonst ignoriert zeroinput die Datei.

dirt_shift-eigene Schlüssel in `dirt_shift.conf`:

- `zeroinput_conf` — Pfad zur `zeroinput.conf` (Standard `../zeroinput.conf`, da dirt_shift üblicherweise in einem Unterordner von zeroinput liegt)
- `vz_host_port`, `vz_chans` — volkszähler-Host und Kanal-UUIDs für die data.json-API. Getrennt von zeroinputs `vz_channels`/`vzlogger_log_file`: dirt_shift nutzt die HTTP-API für Mittelwerte und Energieinhalt, zeroinput die vzlogger-FIFO für die Live-Regelung. Beide greifen auf denselben volkszähler zu, die UUID-Listen müssen nicht identisch sein.
- `average_days` — Tage für das Stundenmittel (Standard 7)
- `day_weights_pct` — Tagesgewichtung in Prozent für das Mittel, chronologisch: Index 0 = ältester Tag (heute minus `average_days`, also der gleiche Wochentag der Vorwoche), Index −1 = gestern. Gestern und der Vorwochentag stärker zu gewichten fängt den jüngsten Trend und die Wochentagsstruktur ein. Die Länge muss `average_days` entsprechen; bei Abweichung werden alle Tage gleich gewichtet. Alle 100 = neutral.
- `reserve_pct` — Prozent des `basic_load`-Bedarfs über das rote Nachtfenster, der reserviert wird (Standard 90)
- `build_reserve_after` — Uhrzeit (HH:MM), ab der die Nachtreserve geschützt wird (Standard 13:30). Davor entlädt der Akku frei in alles, was jetzt hohe Intensität hat; ab dann darf nur der Überschuss über die Reserve in intensive Stunden, und sobald der Inhalt auf die Reserve fällt, stoppt die Entladung bis zum roten Nachtfenster.
- `latitude`, `longitude` — Standort der Anlage (Dezimalgrad) für die Sonnenstandsberechnung; Standard ~Mitte Deutschland (51,0 / 10,0)
- `green_morning_offset_h`, `red_evening_offset_h` — Stunden-Versatz zum Sonnenstand: grün erst so lange nach Sonnenaufgang, rot so lange vor Sonnenuntergang (Standard 3,5 / 3,0)
- `green_earliest`, `green_latest` — feste Uhrzeit-Schranken des grünen Fensters, im Sommer dominierend (Standard 9,0 / 17,5)
- `yellow_width_h` — Breite der gelben Übergangszone an beiden grünen Rändern (Standard 1,0)
- `PV_to_bat_efficiency`, `bat_to_AC_efficiency` — Wirkungsgrade für die Rekonstruktion des Energieinhalts
- `max_days_empty_battery` — wie viele Tage rückwärts nach einem „leer"-Zustand gesucht wird
- `disable_zeroinput_timer` — auf `true` rechnet und gibt aus, ohne die Timer-Datei zu schreiben (Trockenlauf)

### Fehlerverhalten

Bei einem harten Fehler (volkszähler liefert keine vollständigen Tage, Energieinhalt nicht berechenbar) bricht `dirt_shift` ab, schreibt aber zuvor — sofern der Timer-Pfad bekannt ist — eine „Alles-erlaubt"-Zeile, damit zeroinput nicht durch eine veraltete oder fehlende Begrenzung blockiert wird:

```
0000-00-00 00:00:00 100 100 99999
```

(volle Entladung, volle Durchleitung, praktisch unbegrenztes Energiebudget, täglich ab Mitternacht). Ist nicht einmal die `zeroinput.conf` lesbar (Timer-Pfad unbekannt), bleibt nur der Abbruch mit Fehlermeldung.

---

## Aufrufoptionen

- `-v` — ausführliche Konsolenausgabe
- `-html` — HTML-Kopf/-Fuß um die Ausgabe
- `-debug` — mehr Ausgabe (impliziert `-v`)
- `-avgnew` — erzwingt eine frische 7-Tage-Mittelung statt des Caches
