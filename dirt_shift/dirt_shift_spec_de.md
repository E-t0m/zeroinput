# dirt_shift – Funktionsspezifikation
*v1.0*

## Zweck

`dirt_shift` verschiebt die Batterieentladung gezielt in die Netzstunden mit der höchsten CO₂-Intensität. Der deutsche Strommix ist abends und nachts ungünstiger (PV weg, Abendlast hoch, fossile Spitzen) und früh morgens, bis genug PV im Netz ist; tagsüber ist seine CO₂-Intensität niedrig. Wenn man wählen kann, *wann* die Batterie statt des Netzes den Verbrauch deckt, vermeidet man in den Stunden hoher CO₂-Intensität am meisten Emissionen. `dirt_shift` lenkt daher den gesamten verfügbaren Akkuinhalt in diese Stunden — am stärksten in die mit der höchsten Intensität.

Die CO₂-Intensität ist eine reine **Netz**-Eigenschaft und hängt nicht von der eigenen Anlage ab. Die eigene Anlage (PV, Verbrauch, Akkuinhalt) bestimmt nur die *Menge* der verfügbaren und nachts benötigten Energie.

`dirt_shift` steuert die Batterieentladung anhand der Netz-CO₂-Intensität. Es schreibt dazu dieselbe `timer.txt`, die zeroinput für die Entladesteuerung liest.

Die direkte PV-Durchleitung (`pvpt`, PV pass-through) wird **immer** garantiert, unabhängig von allem. Damit ist gemeint, dass momentan erzeugte PV-Leistung direkt zur Deckung des Hausverbrauchs durchgereicht wird, ohne den Umweg über die Batterie. Das hat den besten Wirkungsgrad (kein Lade- und Entladeverlust) und schont den Akku (kein zusätzlicher Zyklus). `dirt_shift` greift ausschließlich an der **Batterieentladung** an, nie an `pvpt`.

---

## Datenquellen

Alle Daten stammen aus dem volkszähler:

- **basic_load** — der tatsächliche Hausverbrauch in Wh/h, in der Standardformel berechnet als `Import + |Inverter| − Auto`. 7-Tage-Stundenmittel, stündlich in `dirt_avg_cache.json` zwischengespeichert. Dient der Mengenabschätzung der Rot-Reserve und der Schutzstunden-Auswahl (siehe Rot-Reserve und Schutzstunden).
- **Energieinhalt** — der reale Akkuinhalt in Wh, rekonstruiert über `get_vz_bat_cap` durch Integration von PV und Inverter seit dem letzten bekannten „leer"-Zustand (Spannung ≤ 3,0625 V/Zelle als Anker, also 49 V bei 16 Zellen; skaliert mit `cell_count` aus der zeroinput-Konfiguration), mit Wirkungsgraden. Bei jedem Lauf frisch abgefragt.
- **PV-Erzeugung** — derselbe PV-Kanal wird zusätzlich für die empirische PV-Referenzkurve genutzt (siehe dort); die Rohwerte werden dort mit `abs()` behandelt, da dieser Kanal in vielen Installationen negativ geloggt wird.

> **basic_load ist frei anpassbar.** Die Formel `Import + |Inverter| − Auto` bildet eine bestimmte Anlagenkonfiguration ab (mit E-Auto-Wallbox als gesondertem Kanal, der vom Hausverbrauch abgezogen wird). Sie ist **kein Standard**, sondern an die eigene Anlage anzupassen: nicht vorhandene Kanäle werden weggelassen, zusätzliche ergänzt. Ohne separat erfasste Wallbox entfällt `Auto`; ein zusätzlicher gesondert erfasster Verbraucher (etwa ein PV-Akku-Lader) käme als weiterer Abzugsterm hinzu. Abgezogen werden nur **planbare** Lasten, die nicht aus dem Nachtbudget gedeckt werden sollen (das Auto wird gezielt geladen). Bedarfsgetriebene Lasten wie eine Klimaanlage bleiben dagegen **im** basic_load — sie gehören zum nachts zu deckenden Verbrauch und sind über das 7-Tage-Mittel in Grenzen vorhersagbar erfasst. Maßgeblich ist, dass basic_load am Ende den **tatsächlich zu deckenden Hausverbrauch** ergibt — also Bezug plus die vom Akku gelieferte Wechselrichterleistung, bereinigt um alles, was nicht aus dem Akku/Netz gedeckt werden soll. Die Berechnung steht in `get_average` und wird dort direkt editiert; entsprechend wird der Kanalsatz in `vz_chans` reduziert oder erweitert.

Die 7-Tage-Basis (`average_days`) enthält genau eine volle Wochenstruktur — jeder Wochentag ist einmal vertreten, das Mittel ist über die Woche balanciert. Über `day_weights_pct` lassen sich einzelne Tage höher gewichten (siehe Konfiguration), etwa gestern und der gleiche Wochentag der Vorwoche; ohne Gewichtung zählt jeder Tag gleich.

---

## CO₂-Intensitätsprofil

Die Zonen-Einteilung (rot/gelb/grün) kommt aus **SMARD** (Bundesnetzagentur) — realen Day-Ahead-Netzdaten, kostenlos und ohne Anmeldung. SMARD ist Voraussetzung; eine alternative Quelle gibt es nicht.

`dirt_shift` fragt die prognostizierte Wind+Solar-Einspeisung und den prognostizierten Stromverbrauch für heute **und morgen** ab und bildet daraus pro Stunde das Verhältnis Erneuerbare/Last, getrennt für jeden Kalendertag. Für jeden Tag werden dessen 24 Stunden nach diesem Verhältnis sortiert und per Perzentil eingeteilt: die `SMARD_GREEN_FRACTION` (30 %) höchsten Stunden werden grün, die `SMARD_RED_FRACTION` (30 %) niedrigsten rot, der Rest gelb — die Einteilung passt sich damit der Form des jeweiligen Tages an, statt an einem festen absoluten Schwellwert zu hängen.

Nach außen liefert `dirt_shift` daraus ein **rollierendes 24-Stunden-Array**, verankert an der aktuellen Uhrzeit: Stunden von jetzt bis Mitternacht stammen aus der Einteilung von heute, Stunden nach Mitternacht aus der von morgen — eine Stunde, die im Array „schon vorbei" wirkt, ist damit tatsächlich das nächste Vorkommen dieser Stunde morgen, mit morgens eigener, echter Einteilung. Ist die Day-Ahead-Prognose für morgen zu einer bestimmten Stunde noch nicht veröffentlicht (typischerweise vor dem späten Nachmittag) oder komplett nicht verfügbar, gilt für diese Stunde die heutige Einteilung.

**Ausfall der SMARD-Abfrage:** Schlägt die Abfrage fehl, dürfen die zwischengespeicherten Daten genau **einen Tag** überbrücken — der Cache trägt ein `fetch_date`, und gestern geholte Daten sind noch nutzbar, weil deren „morgen"-Hälfte die Day-Ahead-Prognose für den nun laufenden Tag ist. Ist der Cache älter oder gar nicht vorhanden, bricht `dirt_shift` hart ab und hinterlässt einen „Alles-erlaubt"-Timer.

Drei Zonen:

- **rot** (höchste Intensität) — die Stunden mit dem niedrigsten Erneuerbaren-Verhältnis
- **gelb** (Übergang) — das mittlere Band
- **grün** (niedrige Intensität) — die Stunden mit dem höchsten Verhältnis

---

## PV-Referenzkurve

Für die Energiebilanz (siehe nächster Abschnitt) braucht `dirt_shift` eine Schätzung, wie viel PV-Ertrag den Rest des Tages noch zu erwarten ist. Statt eines physikalischen Modells der Dachfläche(n) — das bei mehreren Teilflächen mit unterschiedlicher Ausrichtung und jahreszeitlich wechselnder Verschattung aufwendig zu pflegen wäre — nutzt `dirt_shift` die **eigene, real gemessene** Erzeugung der Anlage: Für jede Stunde des Tages wird über die letzten 14 Tage (`PV_CURVE_DAYS`) das 95. Perzentil (`PV_CURVE_PERCENTILE`) der stündlichen PV-Werte gebildet — nahe am Maximum, aber ohne dass ein einzelner Rekordtag die Kurve verzerrt. Da die Kurve aus der eigenen Anlage kommt, spiegelt sie deren tatsächliche Geometrie (mehrere Teilflächen, Verschattung) automatisch wider, ohne dass irgendetwas über Neigung, Ausrichtung oder Verschattung konfiguriert werden müsste.

Die Kurve wird **einmal täglich** ab `PV_CURVE_REFRESH_HOUR` (4 Uhr, eine ruhige Zeit vor Sonnenaufgang ohne konkurrierende Tagesdaten) neu berechnet und in `dirt_pv_curve_cache.json` zwischengespeichert — unabhängig vom stündlichen Rhythmus der `basic_load`-Mittelung. Die zugrundeliegende Abfrage nutzt dieselbe stündliche Auflösung (`group=hour`) wie die `basic_load`-Abfrage, nicht Minutenwerte.

---

## Wetterprognose-Skalierung

Die PV-Referenzkurve zeigt, was an einem **typischen** Tag zu erwarten ist — sie weiß aber nichts über das Wetter von heute. Diese Lücke füllt eine kostenlose, anmeldefreie Strahlungsprognose von **Open-Meteo** (`shortwave_radiation`, W/m², stündlich, für den heutigen Tag). `shortwave_radiation` ist die globale Horizontalstrahlung (direkte plus diffuse Komponente) — das physikalische Modellergebnis von Open-Meteo für die tatsächlich am Boden ankommende Strahlungsleistung.

Aus der Strahlungsprognose wird ein **Klarhimmel-Index** gebildet: `expected_pv = Referenzwert × min(1, Strahlungsprognose / Klarhimmel-GHI)`. `Klarhimmel-GHI` ist die modellierte Globalstrahlung bei wolkenlosem Himmel für dieselbe Stunde und denselben Ort, nach dem Haurwitz-Klarhimmelmodell (1945): `GHI = 1098 × cos(z) × exp(−0,059 / cos(z))` für den Zenitwinkel `z` (aus Sonnenhöhe, siehe `solar_elevation_deg`/`clear_sky_ghi`), sonst 0 (Sonne unter dem Horizont). Das Modell braucht nur die Sonnenposition — keine Trübungs-/Aerosoldaten — und ist damit offline berechenbar. Der Index wird bei 1,0 gekappt (kurzzeitige Strahlungsüberhöhung an Wolkenrändern über den Klarhimmelwert hinaus wird nicht modelliert, um die Prognose konservativ zu halten).

Damit ergibt sich automatisch eine jahreszeit- und tageszeitabhängige Referenz: im Winter ist das Klarhimmel-GHI zur Mittagszeit deutlich niedriger als im Sommer (flacherer Sonnenstand), sodass derselbe gemessene Strahlungswert im Winter einen höheren Klarhimmel-Index (weniger Dämpfung) ergibt als im Sommer bei identischer absoluter Einstrahlung — was der physikalischen Realität entspricht.

Abgefragt wird jeweils die 48-Stunden-Reihe (heute **und** morgen) in einem einzigen Aufruf; Open-Meteo liefert eine fortlaufend aktualisierte Zeitreihe, in der jeder neue Modelllauf nahtlos an den vorherigen anschließt — auch bereits vergangene Stunden des heutigen Tages werden dabei mit dem jeweils aktuellsten Modellstand überschrieben. Aus den beiden 24-Werte-Reihen (heute/morgen) bildet `dirt_shift` ein **rollierendes 24-Stunden-Array**, verankert an der aktuellen Uhrzeit: Stunden von jetzt bis Mitternacht kommen aus der heutigen Reihe, Stunden nach Mitternacht aus der morgigen — eine Stunde, die im Array „schon vorbei" wirkt, ist damit tatsächlich die echte Prognose für das nächste Vorkommen dieser Stunde morgen. `clear_sky_ghi` wird für diese Stunden entsprechend mit dem morgigen statt dem heutigen Datum berechnet. Fehlt ein Stundenwert in der morgigen Reihe, fällt diese Stunde auf den heutigen Wert zurück.

Die ganze 48-Stunden-Reihe wird stündlich neu abgefragt und in `dirt_weather_cache.json` zwischengespeichert, unabhängig von den anderen Caches. Fehlt sie (Abfrage fehlgeschlagen), wird die Referenzkurve unskaliert verwendet — fehlt auch die Referenzkurve selbst, gibt es keine Prognose, und `dirt_shift` fällt auf `build_reserve_after` als reine Uhrzeit-Grenze zurück (siehe nächster Abschnitt).

---

## Entladung nach Zone

Pro Lauf (¼-stündlich) bestimmen die aktuelle Zone und der Schutz-Status das Entladeverhalten:

- **rot** → **kein Limit**: voller Entlade-Deckel (`100 100 99999`), der Akku darf ungebremst raus. Rote Stunden sind **immer** frei, unabhängig vom Schutz — dort soll die Reserve ja gerade verbraucht werden.
- **gelb, geschützt** → **`yellow_cap`** (Watt) als Deckel: gebremste Entladung, sofern der aktuelle Inhalt noch über der Reserve liegt — ist er schon auf die Reserve gefallen, wird gestoppt
- **grün, geschützt** → **kein Akku-Entladen**: nur `pvpt` (`000 100 000`)
- **nicht geschützt** (gelb oder grün) → **kein Limit**: der Akku entlädt frei

`pvpt` (direkte PV-Durchleitung) läuft in jedem Fall in allen Zonen weiter; `dirt_shift` steuert ausschließlich die Batterieentladung.

---

## Rot-Reserve und Schutzstunden

Die **Rot-Reserve** ist `reserve_pct` (Standard 90 %) des `basic_load`-Bedarfs über alle **roten** Stunden zwischen jetzt und der nächsten **PV-Ertragsphase**, **netto** nach dem in diesen Stunden noch erwarteten PV-Ertrag. Die Grenze des Fensters ist die erste Stunde, deren erwartete PV den `basic_load` übersteigt (Überschussstunde): ab dort füllt sich der Akku tatsächlich wieder, und spätere rote Phasen werden vom kommenden Ertrag gedeckt, nicht von der gestrigen Ladung — sie dafür zurückzuhalten würde nur Speicherplatz für den kommenden Ertrag blockieren. Mehrere getrennte rote Phasen vor diesem Punkt (Abendrot, Nachtrot, Morgenrot mit gelben Lücken dazwischen) werden alle zusammengezählt, da dazwischen nichts nachfüllt. An einem so trüben Tag, dass die erwartete PV den Verbrauch nie übersteigt, gibt es keine Überschussstunde — dann werden alle roten Stunden der rollierenden 24 Stunden reserviert, was korrekt ist, weil kein Nachfüllen kommt.

Zur Netto-Rechnung: `pvpt` deckt einen Teil dieses Bedarfs bereits direkt ab (an den Rändern eines roten Abschnitts, solange die Sonne noch nicht ganz weg bzw. schon wieder da ist), dieser Anteil muss also nicht zusätzlich aus dem Akku reserviert werden. Die 90 % legen die Reserve bewusst nicht über den berechneten Bedarf hinaus — der Akku soll sich im Regelfall über das rote Fenster praktisch vollständig entladen, statt Kapazität ungenutzt zu lassen.

### Welche Stunden geschützt werden

Der Reserve-Aufbau geschieht **strikt nach Dreckigkeit**, nicht nach Uhrzeit: Kandidaten sind alle **nicht-roten** Stunden im selben Fenster (von jetzt bis zur ersten Überschussstunde). Sie werden nach `dirt%` **aufsteigend** sortiert — die saubersten zuerst — und ihre Fehlbeträge (`basic_load − erwartete PV`, mindestens 0) in dieser Reihenfolge aufsummiert. Jede Stunde bis einschließlich derjenigen, die die Summe erstmals auf das Reserve-Ziel bringt, wird geschützt.

Die Auswahl muss dabei **nicht zusammenhängend** sein: Eine saubere Stunde spät im Fenster kann geschützt werden, während eine dreckigere Stunde davor frei bleibt, wenn deren Fehlbetrag zum Erreichen des Ziels nicht nötig war. Reicht die Summe über **alle** Kandidaten nicht aus, werden alle geschützt — die Auswahl schiebt sich dann automatisch so weit in die dreckigeren Stunden des Tages, wie das Fenster hergibt, weil nichts anderes mehr zum Zurückhalten übrig ist.

Der aktuelle Lauf ist genau dann geschützt, wenn die laufende Stunde in dieser Menge liegt. Die Entscheidung beruht damit auf einer Rangfolge über das ganze Fenster, nicht auf einem einzelnen Prognosewert. Die `-v`-Ausgabe zeigt die gewählten Stunden in der Zeile `dirt-ranked: ... -> protected hours [...]`.

Ob der Inhalt *aktuell* schon auf die Reserve gefallen ist, ist eine davon getrennte, jederzeit aktive Prüfung — sie entscheidet in der gelben Zone zwischen `limit` (Inhalt noch über der Reserve, Überschuss abfließen lassen) und `stop` (Inhalt schon an der Reserve).

**Ohne PV-Prognose** (Kurve nie erfolgreich berechnet, etwa bei einer frischen Installation) fällt die Entscheidung auf `build_reserve_after` als reine Uhrzeit-Grenze zurück: vor dieser Uhrzeit (Standard 13:30) wird immer frei entladen, ab dann greift die Zonen-Logik. Eine Kurve ohne Strahlungsprognose zählt noch als Prognose (dann eben unskaliert) — nur wenn wirklich keine PV-Referenzkurve vorliegt, greift die Uhrzeit-Regel.

---

## Ausfallsicherung

`dirt_shift` ist optional und darf den Normalbetrieb von zeroinput nie blockieren. Begrenzt oder stoppt ein Lauf die Entladung (gelb/grün/Reserveschutz), schreibt er zusätzlich eine „Alles-erlaubt"-Zeile (`100 100 99999`) 30 Minuten später. Läuft das Skript weiter, wird die Begrenzung alle 15 Minuten erneuert; fällt es aus (cron-Ausfall, volkszähler nicht erreichbar, Absturz), hebt sich die Begrenzung nach 30 Minuten von selbst auf, und zeroinput entlädt wieder frei, als gäbe es `dirt_shift` nicht. Im roten Fenster (kein Limit) ist ohnehin alles erlaubt, dort genügt die eine Zeile.

---

## Netz-Dreckigkeit exportieren

Ist SMARD aktiv, kann `dirt_shift` den aktuellen Dreckigkeitswert zusätzlich in volkszähler protokollieren — bei jedem Lauf, wenn `vz_dirtiness_uuid` gesetzt ist. Der Wert ist `(1 − Verhältnis) × 100` (Erneuerbare/Last der aktuellen Stunde): Vorzeichen-Konvention wie bei den bestehenden Leistungskanälen der Anlage (Bezug positiv, Einspeisung negativ) — je positiver, desto dreckiger (unterdurchschnittlicher Erneuerbaren-Anteil); bei einem Erneuerbaren-Überschuss (Verhältnis > 1) wird der Wert sogar negativ, wie eine Einspeisung.

Geschrieben wird per direktem **HTTP-POST** an volkszählers Middleware-API, einmal pro Lauf: `http://{vz_host_port}/data/{vz_dirtiness_uuid}.json`, mit Wert und aktuellem Zeitstempel. Genutzt wird derselbe `vz_host_port`, den `dirt_shift` ohnehin schon für seine anderen volkszähler-Abfragen verwendet — kein vzlogger-Meter, keine lokale Datei. `vz_dirtiness_uuid` muss eine echte, vorher in volkszähler angelegte Kanal-UUID sein. Ein fehlgeschlagener Schreibversuch (Netzwerkfehler, falsche UUID) bricht den Lauf nicht ab, er wird nur unter `-v` gemeldet.

---

## Ausgabe: timer.txt

`dirt_shift` schreibt `timer.txt` im zeroinput-Format:

```
YYYY-MM-DD HH:MM:00  <entlade-W>  <ac-%>  <energie-Wh>
```

- Jede Zeile trägt das reale Kalenderdatum, an dem sie geschrieben wurde.
- **entlade-W** — Entlade-Deckel; `100` (Prozent) = kein Limit (rot), `yellow_cap` (Watt) = gebremst (gelb), `000` = kein Akku-Entladen (grün/Stopp).
- **ac-%** — Wechselrichter-Durchleitung, immer `100` (pvpt garantiert).
- **energie-Wh** — Energiebudget; `99999` = praktisch unbegrenzt (Entladung erlaubt), `000` = kein Budget (Stopp).

Die drei Modi sind also: `100 100 99999` (rot bzw. Reserve nicht geschützt, kein Limit), `<yellow_cap> 100 99999` (gelb, gedeckelt), `000 100 000` (grün/Stopp). Das Wh-Energiefeld bleibt auch im gelben Modus unbegrenzt (`99999`) — die tatsächlich abgegebene Energiemenge wird nicht hierüber, sondern über das eigene Wh-Kontingent des Slots (Reserve-/Rotfenster-Logik) begrenzt. `yellow_cap` deckelt nur die Momentanleistung, sodass kurze Lastspitzen noch aus dem Akku bedient werden können, ohne das Kontingent dauerhaft zu überziehen.

Werte > 100 werden als Watt interpretiert, Werte ≤ 100 als Prozent — wie im bestehenden zeroinput-Timer-Format. zeroinputs `discharge_times`-Parser liest die Zeilen der Reihe nach und übernimmt für jede Zeile mit Zeitstempel in der Vergangenheit deren Werte, bis er auf die erste Zeile mit Zeitstempel in der Zukunft trifft (dort bricht er ab) — der aktive Zustand ist damit immer der der letzten bereits vergangenen Zeile. Läuft `dirt_shift` nicht mehr und liegen irgendwann beide Zeilen in der Vergangenheit, bricht die Schleife nicht mehr ab, sondern läuft bis zum Ende durch — der Zustand landet dann bei der **letzten** Zeile der Datei. Da diese letzte Zeile bei `dirt_shift` immer die Ausfallsicherungs-Zeile (`FREE`) bzw. bei Modus `free` die einzige, ohnehin freie Zeile ist, stellt sich der Zustand von selbst dauerhaft auf „alles erlaubt" — ohne dass die Datei erneut geschrieben werden muss.

Der geschriebene Plan ist kurz: der aktuelle ¼-Stunden-Slot im gewählten Modus (frei / gedeckelt / Stopp), und — falls der Modus begrenzt oder stoppt — eine „Alles-erlaubt"-Zeile 30 Minuten später als Ausfallsicherung.

`dirt_shift` läuft ¼-stündlich (z. B. per cron) und schreibt die Datei jedes Mal neu mit aktuellem Energieinhalt und aktueller Zone.

---

## Konfiguration

`dirt_shift.conf` enthält **nur** den Pfad zur `zeroinput.conf` und die dirt_shift-eigenen Parameter. Werte, die bereits in `zeroinput.conf` stehen, werden von dort gelesen statt dupliziert — `dirt_shift` ändert `zeroinput.conf` nie.

Aus `zeroinput.conf` gelesen (read-only):

- **`discharge_t_file`** — der Pfad der Timer-Datei, die zeroinput liest. `dirt_shift` schreibt genau diese Datei (relativ zur zeroinput.conf aufgelöst). Damit zeigen Schreiber und Leser garantiert auf dieselbe Datei. In `zeroinput.conf` muss zusätzlich `discharge_timer` aktiviert sein, sonst ignoriert zeroinput die Datei.

dirt_shift-eigene Schlüssel in `dirt_shift.conf`:

- `zeroinput_conf` — Pfad zur `zeroinput.conf` (Standard `../zeroinput.conf`, da dirt_shift üblicherweise in einem Unterordner von zeroinput liegt)
- **`yellow_cap`** (Watt, Standard 1000) — Leistungsobergrenze für Entladung in der gelben Zone. Kurzzeitiger Haushaltsverbrauch bleibt grundsätzlich unvorhersehbar, darum ist dies bewusst kein aus Prognosedaten abgeleiteter Wert, sondern eine feste, dokumentierte Grenze. Sie schützt die Rot-Reserve vor spitzenbedingtem Überzug, ohne die Wh-Budgetierung des Slots zu verändern — unabhängig von jeder Wechselrichter-Staging-Schwelle in `zeroinput.conf`.
- `vz_host_port`, `vz_chans` — volkszähler-Host und Kanal-UUIDs für die data.json-API. Getrennt von zeroinputs `vz_channels`/`vzlogger_log_file`: dirt_shift nutzt die HTTP-API für Mittelwerte, PV-Kurve und Energieinhalt, zeroinput die vzlogger-FIFO für die Live-Regelung. Beide greifen auf denselben volkszähler zu, die UUID-Listen müssen nicht identisch sein.
- `vz_dirtiness_uuid` — echte volkszähler-Kanal-UUID für den Dreckigkeitswert-Export per HTTP-POST (siehe Netz-Dreckigkeit exportieren). Leer deaktiviert den Export.
- `average_days` — Tage für das Stundenmittel (Standard 7)
- `day_weights_pct` — Tagesgewichtung in Prozent für das Mittel, chronologisch: Index 0 = ältester Tag (heute minus `average_days`, also der gleiche Wochentag der Vorwoche), Index −1 = gestern. Gestern und der Vorwochentag stärker zu gewichten fängt den jüngsten Trend und die Wochentagsstruktur ein. Die Länge muss `average_days` entsprechen; bei Abweichung werden alle Tage gleich gewichtet. Alle 100 = neutral.
- `reserve_pct` — Prozent des `basic_load`-Bedarfs über das rote Nachtfenster, der reserviert wird (Standard 90)
- `build_reserve_after` — Uhrzeit (HH:MM), nur genutzt, wenn keine PV-Prognose vorliegt (Standard 13:30). Liegt eine Prognose vor, bestimmt die dirt%-sortierte Schutzstunden-Auswahl (siehe Rot-Reserve und Schutzstunden), unabhängig von der Uhrzeit.
- `latitude`, `longitude` — Standort der Anlage (Dezimalgrad) für das Klarhimmel-Modell und die Strahlungsprognose; Standard ~Mitte Deutschland (51,0 / 10,0)
- `PV_to_bat_efficiency`, `bat_to_AC_efficiency` — Wirkungsgrade für die Rekonstruktion des Energieinhalts
- `max_days_empty_battery` — wie viele Tage rückwärts nach einem „leer"-Zustand gesucht wird
- `disable_zeroinput_timer` — auf `true` rechnet und gibt aus, ohne die Timer-Datei zu schreiben (Trockenlauf)

### Fehlerverhalten

Bei einem harten Fehler (volkszähler liefert keine vollständigen Tage, Energieinhalt nicht berechenbar) bricht `dirt_shift` ab, schreibt aber zuvor — sofern der Timer-Pfad bekannt ist — eine „Alles-erlaubt"-Zeile, damit zeroinput nicht durch eine veraltete oder fehlende Begrenzung blockiert wird:

```
2026-07-07 00:00:00 100 100 99999
```

(volle Entladung, volle Durchleitung, praktisch unbegrenztes Energiebudget, mit dem aktuellen Datum). Ist nicht einmal die `zeroinput.conf` lesbar (Timer-Pfad unbekannt), bleibt nur der Abbruch mit Fehlermeldung.

---

## Aufrufoptionen

- `-v` — ausführliche Konsolenausgabe
- `-html` — HTML-Kopf/-Fuß um die Ausgabe
- `-debug` — mehr Ausgabe (impliziert `-v`)
- `-avgnew` — erzwingt eine frische Abfrage statt der Caches: `basic_load`-Mittel, PV-Referenzkurve, Strahlungsprognose und SMARD-Zonen
