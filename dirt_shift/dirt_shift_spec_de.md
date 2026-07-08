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

- **basic_load** — der tatsächliche Hausverbrauch in Wh/h, in der Standardformel berechnet als `Import + |Inverter| − Auto`. 7-Tage-Stundenmittel, stündlich in `dirt_avg_cache.json` zwischengespeichert. Dient der Mengenabschätzung der Rot-Reserve und der Energiebilanz (siehe Rot-Reserve und Energiebilanz).
- **Energieinhalt** — der reale Akkuinhalt in Wh, rekonstruiert über `get_vz_bat_cap` durch Integration von PV und Inverter seit dem letzten bekannten „leer"-Zustand (Spannung ≤ 3,0625 V/Zelle als Anker, also 49 V bei 16 Zellen; skaliert mit `cell_count` aus der zeroinput-Konfiguration), mit Wirkungsgraden. Bei jedem Lauf frisch abgefragt.
- **PV-Erzeugung** — derselbe PV-Kanal wird zusätzlich für die empirische PV-Referenzkurve genutzt (siehe dort); die Rohwerte werden dort mit `abs()` behandelt, da dieser Kanal in vielen Installationen negativ geloggt wird.

> **basic_load ist frei anpassbar.** Die Formel `Import + |Inverter| − Auto` bildet eine bestimmte Anlagenkonfiguration ab (mit E-Auto-Wallbox als gesondertem Kanal, der vom Hausverbrauch abgezogen wird). Sie ist **kein Standard**, sondern an die eigene Anlage anzupassen: nicht vorhandene Kanäle werden weggelassen, zusätzliche ergänzt. Ohne separat erfasste Wallbox entfällt `Auto`; ein zusätzlicher gesondert erfasster Verbraucher (etwa ein PV-Akku-Lader) käme als weiterer Abzugsterm hinzu. Abgezogen werden nur **planbare** Lasten, die nicht aus dem Nachtbudget gedeckt werden sollen (das Auto wird gezielt geladen). Bedarfsgetriebene Lasten wie eine Klimaanlage bleiben dagegen **im** basic_load — sie gehören zum nachts zu deckenden Verbrauch und sind über das 7-Tage-Mittel in Grenzen vorhersagbar erfasst. Maßgeblich ist, dass basic_load am Ende den **tatsächlich zu deckenden Hausverbrauch** ergibt — also Bezug plus die vom Akku gelieferte Wechselrichterleistung, bereinigt um alles, was nicht aus dem Akku/Netz gedeckt werden soll. Die Berechnung steht in `get_average` und wird dort direkt editiert; entsprechend wird der Kanalsatz in `vz_chans` reduziert oder erweitert.

Die 7-Tage-Basis (`average_days`) enthält genau eine volle Wochenstruktur — jeder Wochentag ist einmal vertreten, das Mittel ist über die Woche balanciert. Über `day_weights_pct` lassen sich einzelne Tage höher gewichten (siehe Konfiguration), etwa gestern und der gleiche Wochentag der Vorwoche; ohne Gewichtung zählt jeder Tag gleich.

---

## CO₂-Intensitätsprofil

Die Zonen-Einteilung (rot/gelb/grün) kommt wahlweise aus zwei Quellen:

**SMARD** (Bundesnetzagentur, `smard_enabled: true`) — reale Day-Ahead-Netzdaten, kostenlos und ohne Anmeldung. `dirt_shift` fragt die prognostizierte Wind+Solar-Einspeisung und den prognostizierten Stromverbrauch für heute **und morgen** ab und bildet daraus pro Stunde das Verhältnis Erneuerbare/Last, getrennt für jeden Kalendertag. Für jeden Tag werden dessen 24 Stunden nach diesem Verhältnis sortiert und per Perzentil eingeteilt: die `SMARD_GREEN_FRACTION` (30 %) höchsten Stunden werden grün, die `SMARD_RED_FRACTION` (30 %) niedrigsten rot, der Rest gelb — die Einteilung passt sich damit der Form des jeweiligen Tages an, statt an einem festen absoluten Schwellwert zu hängen.

Nach außen liefert `dirt_shift` daraus ein **rollierendes 24-Stunden-Array**, verankert an der aktuellen Uhrzeit: Stunden von jetzt bis Mitternacht stammen aus der Einteilung von heute, Stunden nach Mitternacht aus der von morgen — eine Stunde, die im Array „schon vorbei" wirkt, ist damit tatsächlich das nächste Vorkommen dieser Stunde morgen, mit morgens eigener, echter Einteilung statt einer Wiederverwendung des heutigen Musters. Ist die Day-Ahead-Prognose für morgen zu einer bestimmten Stunde noch nicht veröffentlicht (typischerweise vor dem späten Nachmittag) oder komplett nicht verfügbar, fällt diese Stunde auf die heutige Einteilung zurück. Schlägt schon die heutige Abfrage fehl (kein Netz, unvollständige Daten, `smard_enabled: false`), fällt `dirt_shift` insgesamt auf die Sonnenstand-Heuristik zurück.

**Sonnenstand** (Fallback, immer verfügbar, keine externen Daten) — Sonnenauf- und -untergang werden astronomisch aus Datum und Standort (`latitude`/`longitude`) berechnet (einfache Näherung, auf ein, zwei Minuten genau; Sommer-/Winterzeit wird über die Systemzeitzone berücksichtigt). Für gewickelte Stunden (vor der aktuellen Uhrzeit im rollierenden Array) wird dabei weiterhin das heutige Datum verwendet, nicht das morgige — Sonnenauf-/untergang verschieben sich von Tag zu Tag nur um ein bis zwei Minuten, der Unterschied ist hier vernachlässigbar. Der Erneuerbaren-Anteil im Netz läuft dem Sonnenstand hinterher: morgens sinkt die CO₂-Intensität erst, wenn genug PV relativ zur **Last** im Netz ist (die Last fährt uhrzeitgesteuert hoch), abends steigt sie, bevor die Sonne weg ist (PV fällt, Abendlast steigt). Deshalb ist das günstige (niedrig-intensive, „grüne") Fenster ein **Hybrid** aus Sonnenstand und festen Uhrzeit-Schranken:

Drei Zonen (beide Quellen liefern dieselben drei Werte):

- **rot** (höchste Intensität) — außerhalb des günstigen Fensters bzw. niedrigstes Erneuerbaren-Verhältnis: Abend, Nacht, früher Morgen
- **gelb** (Übergang) — je `yellow_width_h` an beiden Rändern des grünen Fensters bzw. mittleres Verhältnis
- **grün** (niedrige Intensität) — die Tagesmitte bzw. höchstes Verhältnis

---

## PV-Referenzkurve

Für die Energiebilanz (siehe nächster Abschnitt) braucht `dirt_shift` eine Schätzung, wie viel PV-Ertrag den Rest des Tages noch zu erwarten ist. Statt eines physikalischen Modells der Dachfläche(n) — das bei mehreren Teilflächen mit unterschiedlicher Ausrichtung und jahreszeitlich wechselnder Verschattung aufwendig zu pflegen wäre — nutzt `dirt_shift` die **eigene, real gemessene** Erzeugung der Anlage: Für jede Stunde des Tages wird über die letzten 14 Tage (`PV_CURVE_DAYS`) das 95. Perzentil (`PV_CURVE_PERCENTILE`) der stündlichen PV-Werte gebildet — nahe am Maximum, aber ohne dass ein einzelner Rekordtag die Kurve verzerrt. Da die Kurve aus der eigenen Anlage kommt, spiegelt sie deren tatsächliche Geometrie (mehrere Teilflächen, Verschattung) automatisch wider, ohne dass irgendetwas über Neigung, Ausrichtung oder Verschattung konfiguriert werden müsste.

Die Kurve wird **einmal täglich** ab `PV_CURVE_REFRESH_HOUR` (4 Uhr, eine ruhige Zeit vor Sonnenaufgang ohne konkurrierende Tagesdaten) neu berechnet und in `dirt_pv_curve_cache.json` zwischengespeichert — unabhängig vom stündlichen Rhythmus der `basic_load`-Mittelung. Die zugrundeliegende Abfrage nutzt dieselbe stündliche Auflösung (`group=hour`) wie die `basic_load`-Abfrage, nicht Minutenwerte.

---

## Wetterprognose-Skalierung

Die PV-Referenzkurve zeigt, was an einem **typischen** Tag zu erwarten ist — sie weiß aber nichts über das Wetter von heute. Diese Lücke füllt eine kostenlose, anmeldefreie Strahlungsprognose von **Open-Meteo** (`shortwave_radiation`, W/m², stündlich, für den heutigen Tag). `shortwave_radiation` ist die globale Horizontalstrahlung (direkte plus diffuse Komponente) — das physikalische Modellergebnis von Open-Meteo für die tatsächlich am Boden ankommende Strahlungsleistung.

Aus der Strahlungsprognose wird ein **Klarhimmel-Index** gebildet: `expected_pv = Referenzwert × min(1, Strahlungsprognose / Klarhimmel-GHI)`. `Klarhimmel-GHI` ist die modellierte Globalstrahlung bei wolkenlosem Himmel für dieselbe Stunde und denselben Ort, nach dem Haurwitz-Klarhimmelmodell (1945): `GHI = 1098 × cos(z) × exp(−0,059 / cos(z))` für den Zenitwinkel `z` (aus Sonnenhöhe, siehe `solar_elevation_deg`/`clear_sky_ghi`), sonst 0 (Sonne unter dem Horizont). Das Modell braucht nur die Sonnenposition — keine Trübungs-/Aerosoldaten — und ist damit offline berechenbar und konsistent mit der übrigen Sonnenstands-Mathematik von `dirt_shift`. Der Index wird bei 1,0 gekappt (kurzzeitige Strahlungsüberhöhung an Wolkenrändern über den Klarhimmelwert hinaus wird nicht modelliert, um die Prognose konservativ zu halten).

Damit ergibt sich automatisch eine jahreszeit- und tageszeitabhängige Referenz: im Winter ist das Klarhimmel-GHI zur Mittagszeit deutlich niedriger als im Sommer (flacherer Sonnenstand), sodass derselbe gemessene Strahlungswert im Winter einen höheren Klarhimmel-Index (weniger Dämpfung) ergibt als im Sommer bei identischer absoluter Einstrahlung — was der physikalischen Realität entspricht.

Abgefragt wird jeweils die 48-Stunden-Reihe (heute **und** morgen) in einem einzigen Aufruf; Open-Meteo liefert eine fortlaufend aktualisierte Zeitreihe, in der jeder neue Modelllauf nahtlos an den vorherigen anschließt — auch bereits vergangene Stunden des heutigen Tages werden dabei mit dem jeweils aktuellsten Modellstand überschrieben. Aus den beiden 24-Werte-Reihen (heute/morgen) bildet `dirt_shift` ein **rollierendes 24-Stunden-Array**, verankert an der aktuellen Uhrzeit: Stunden von jetzt bis Mitternacht kommen aus der heutigen Reihe, Stunden nach Mitternacht aus der morgigen — eine Stunde, die im Array „schon vorbei" wirkt, ist damit tatsächlich die echte Prognose für das nächste Vorkommen dieser Stunde morgen. `clear_sky_ghi` wird für diese Stunden entsprechend mit dem morgigen statt dem heutigen Datum berechnet. Fehlt ein Stundenwert in der morgigen Reihe, fällt diese Stunde auf den heutigen Wert zurück.

Die ganze 48-Stunden-Reihe wird stündlich neu abgefragt und in `dirt_weather_cache.json` zwischengespeichert, unabhängig von den anderen Caches. Fehlt sie (Abfrage fehlgeschlagen), wird die Referenzkurve unskaliert verwendet — fehlt auch die Referenzkurve selbst, gibt es keine Prognose, und `dirt_shift` fällt auf `build_reserve_after` als reine Uhrzeit-Grenze zurück (siehe nächster Abschnitt).

---

## Entladung nach Zone

Sobald die Rot-Reserve geschützt ist (siehe nächster Abschnitt), bestimmt die aktuelle Zone pro Lauf (¼-stündlich) das Entladeverhalten. Der gesamte Akkuinhalt wird in die Stunden hoher CO₂-Intensität gelenkt, am stärksten in die intensivsten:

- **rot** → **kein Limit**: voller Entlade-Deckel (`100 100 99999`), der Akku darf ungebremst raus
- **gelb** → **`yellow_cap`** (Watt) als Deckel: gebremste Entladung in der Übergangszone, sofern der aktuelle Inhalt noch über der Reserve liegt — ist er schon auf die Reserve gefallen, wird gestoppt
- **grün** → **kein Akku-Entladen**: nur `pvpt` (`000 100 000`)

Solange die Reserve nicht geschützt ist, entlädt der Akku unabhängig von der Zone frei (siehe nächster Abschnitt). `pvpt` (direkte PV-Durchleitung) läuft in jedem Fall in allen Zonen weiter; `dirt_shift` steuert ausschließlich die Batterieentladung.

---

## Rot-Reserve und Energiebilanz

Die **Rot-Reserve** ist `reserve_pct` (Standard 90 %) des `basic_load`-Bedarfs über alle **roten** Stunden zwischen jetzt und der nächsten **PV-Ertragsphase**, **netto** nach dem in diesen Stunden noch erwarteten PV-Ertrag. Die Grenze des Fensters ist die erste Stunde, deren erwartete PV den `basic_load` übersteigt (Überschussstunde): ab dort füllt sich der Akku tatsächlich wieder, und spätere rote Phasen werden vom kommenden Ertrag gedeckt, nicht von der gestrigen Ladung — sie dafür zurückzuhalten würde nur Speicherplatz für den kommenden Ertrag blockieren. Mehrere getrennte rote Phasen vor diesem Punkt (Abendrot, Nachtrot, Morgenrot mit gelben Lücken dazwischen) werden alle zusammengezählt, da dazwischen nichts nachfüllt. An einem so trüben Tag, dass die erwartete PV den Verbrauch nie übersteigt, gibt es keine Überschussstunde — dann werden alle roten Stunden der rollierenden 24 Stunden reserviert, was korrekt ist, weil kein Nachfüllen kommt. Zur Netto-Rechnung: `pvpt` deckt einen Teil dieses Bedarfs bereits direkt ab (an den Rändern eines roten Abschnitts, solange die Sonne noch nicht ganz weg bzw. schon wieder da ist), dieser Anteil muss also nicht zusätzlich aus dem Akku reserviert werden. Fehlt die PV-Prognose, kann keine Überschussstunde erkannt und nicht netto gerechnet werden — dann dient die reine `basic_load`-Summe über alle roten Stunden der rollierenden 24 Stunden als konservativer Fallback. Die 90 % legen die Reserve bewusst nicht über den berechneten Bedarf hinaus — der Akku soll sich im Regelfall über das rote Fenster praktisch vollständig entladen, statt Kapazität ungenutzt zu lassen.

Welche Stunden als „rot" zählen, kommt aus **derselben** Quelle, die auch die aktuelle Zonen-Entscheidung trifft (SMARD, falls aktiv, sonst der Sonnenstand) — nicht immer starr aus dem Sonnenstand, unabhängig davon. Mit dem Sonnenstand-Fallback ergibt das denselben einen zusammenhängenden Abend-bis-Morgen-Block wie zuvor; SMARDs reale Daten können dagegen mehrere getrennte rote Abschnitte über den Tag verteilt liefern, die dann alle zusammengezählt werden. Diese Konsistenz ist wichtig: Bestimmt SMARD die laufende Modus-Entscheidung, muss auch die Fenstergrenze für die Energiebilanz (siehe unten) SMARDs eigener Einteilung folgen — sonst könnte die Bilanz versuchen, bis zu einem Sonnenstand-Zeitpunkt zu überbrücken, der (aus SMARDs Sicht) bereits in der Vergangenheit liegt, und dabei fast einen ganzen Tag umwickeln, statt nur die tatsächlich verbleibenden Stunden zu zählen.

Ob die Reserve **jetzt schon** geschützt werden muss, entscheidet — sofern eine PV-Prognose vorliegt (siehe PV-Referenzkurve) — eine vollständige Energiebilanz, keine feste Uhrzeit:

```
Projektion = aktueller Inhalt + erwarteter PV-Restertrag − erwarteter Restverbrauch
             (jeweils von jetzt bis zum Beginn des roten Nachtfensters)
Reserve geschützt, wenn Projektion < Rot-Reserve
```

Restertrag und Restverbrauch werden über dieselbe Fensterlogik aufsummiert: von der aktuellen Stunde bis zur nächsten Stunde, die als rot eingestuft ist — wieder aus derselben Zonen-Quelle wie oben (SMARD, falls aktiv, sonst Sonnenstand), nicht immer starr aus dem Sonnenstand. Die laufende Stunde zählt dabei nur **anteilig** — nach den Minuten, die bis zum Stundenwechsel noch übrig sind — nicht komplett; da `dirt_shift` im ¼-Stunden-Raster läuft, ergibt das im Normalbetrieb effektiv Viertelstunden-Genauigkeit am Rand des Zeitfensters, ohne dass die zugrundeliegenden Stundenkurven selbst feiner aufgelöst werden müssten. Ist die aktuelle Stunde selbst schon rot, ist nichts mehr zu überbrücken, dort wird ohnehin immer frei entladen.

Diese Bilanz ist bewusst **unabhängig vom aktuellen Inhalt allein**: Ein voller Akku schützt nicht automatisch vor dem Schutz-Modus, wenn der erwartete Restverbrauch die erwartete Restsonne übersteigt; ein leerer Akku muss nicht automatisch schützen, wenn eine gute Prognose die Lücke deckt. Ob der Inhalt *aktuell* schon auf die Reserve gefallen ist, ist eine davon getrennte, jederzeit aktive Prüfung — sie entscheidet in der gelben Zone zwischen `limit` (Inhalt noch über der Reserve, Überschuss abfließen lassen) und `stop` (Inhalt schon an der Reserve).

**Ohne PV-Prognose** (Kurve nie erfolgreich berechnet) fällt die Entscheidung auf `build_reserve_after` als reine Uhrzeit-Grenze zurück, genau wie früher: vor dieser Uhrzeit (Standard 13:30) wird immer frei entladen, ab dann greift die Zonen-Logik unabhängig vom Energiebilanz-Ergebnis. Eine Kurve ohne Strahlungsprognose zählt noch als Prognose (dann eben unskaliert) — nur wenn wirklich keine PV-Referenzkurve vorliegt, greift die Uhrzeit-Regel.

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
- **`yellow_cap`** (Watt, Standard 600) — Leistungsobergrenze für Entladung in der gelben Zone. Kurzzeitiger Haushaltsverbrauch bleibt grundsätzlich unvorhersehbar, darum ist dies bewusst kein aus Prognosedaten abgeleiteter Wert, sondern eine feste, dokumentierte Grenze. Sie schützt die Rot-Reserve vor spitzenbedingtem Überzug, ohne die Wh-Budgetierung des Slots zu verändern — unabhängig von jeder Wechselrichter-Staging-Schwelle in `zeroinput.conf`.
- `vz_host_port`, `vz_chans` — volkszähler-Host und Kanal-UUIDs für die data.json-API. Getrennt von zeroinputs `vz_channels`/`vzlogger_log_file`: dirt_shift nutzt die HTTP-API für Mittelwerte, PV-Kurve und Energieinhalt, zeroinput die vzlogger-FIFO für die Live-Regelung. Beide greifen auf denselben volkszähler zu, die UUID-Listen müssen nicht identisch sein.
- `vz_dirtiness_uuid` — echte volkszähler-Kanal-UUID für den Dreckigkeitswert-Export per HTTP-POST (siehe Netz-Dreckigkeit exportieren). Leer deaktiviert den Export.
- `average_days` — Tage für das Stundenmittel (Standard 7)
- `day_weights_pct` — Tagesgewichtung in Prozent für das Mittel, chronologisch: Index 0 = ältester Tag (heute minus `average_days`, also der gleiche Wochentag der Vorwoche), Index −1 = gestern. Gestern und der Vorwochentag stärker zu gewichten fängt den jüngsten Trend und die Wochentagsstruktur ein. Die Länge muss `average_days` entsprechen; bei Abweichung werden alle Tage gleich gewichtet. Alle 100 = neutral.
- `reserve_pct` — Prozent des `basic_load`-Bedarfs über das rote Nachtfenster, der reserviert wird (Standard 90)
- `build_reserve_after` — Uhrzeit (HH:MM), reiner Fallback-Wert, wenn keine PV-Prognose vorliegt (Standard 13:30). Liegt eine Prognose vor, bestimmt stattdessen die Energiebilanz (siehe Rot-Reserve und Energiebilanz), unabhängig von der Uhrzeit.
- `smard_enabled` — reale CO₂-Intensitätszonen aus SMARD-Day-Ahead-Daten (Bundesnetzagentur; kein API-Key nötig) statt der Sonnenstand-Zonen. `false` (Standard) nutzt nur das Sonnenstand-Profil. Eine fehlgeschlagene SMARD-Abfrage fällt für diesen Lauf automatisch auf das Sonnenstand-Profil zurück.
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
2026-07-07 00:00:00 100 100 99999
```

(volle Entladung, volle Durchleitung, praktisch unbegrenztes Energiebudget, mit dem aktuellen Datum). Ist nicht einmal die `zeroinput.conf` lesbar (Timer-Pfad unbekannt), bleibt nur der Abbruch mit Fehlermeldung.

---

## Aufrufoptionen

- `-v` — ausführliche Konsolenausgabe
- `-html` — HTML-Kopf/-Fuß um die Ausgabe
- `-debug` — mehr Ausgabe (impliziert `-v`)
- `-avgnew` — erzwingt eine frische Abfrage statt der Caches: `basic_load`-Mittel, PV-Referenzkurve, Strahlungsprognose und SMARD-Zonen
