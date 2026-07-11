# dirt_shift – Funktionsspezifikation
*v1.0*

## Zweck

`dirt_shift` verschiebt die Batterieentladung gezielt in die Netzstunden mit der höchsten CO₂-Intensität. Der deutsche Strommix ist abends und nachts ungünstiger (PV weg, Abendlast hoch, fossile Spitzen) und früh morgens, bis genug PV im Netz ist; tagsüber ist seine CO₂-Intensität niedrig. Wenn man wählen kann, *wann* die Batterie statt des Netzes den Verbrauch deckt, vermeidet man in den Stunden hoher CO₂-Intensität am meisten Emissionen. `dirt_shift` lenkt daher den gesamten verfügbaren Akkuinhalt in diese Stunden — am stärksten in die dreckigste.

Die CO₂-Intensität ist eine reine **Netz**-Eigenschaft und hängt nicht von der eigenen Anlage ab. Die eigene Anlage (PV, Verbrauch, Akkuinhalt) bestimmt nur die *Menge* der verfügbaren und benötigten Energie.

`dirt_shift` steuert die Batterieentladung anhand der Netz-CO₂-Intensität. Es schreibt dazu dieselbe `timer.txt`, die zeroinput für die Entladesteuerung liest.

Die direkte PV-Durchleitung (`pvpt`, PV pass-through) wird garantiert, außer im optionalen Precharge-Pfad (`precharge_enabled`, siehe dort) — dort, und nur dort, kann sie an einer einzelnen Stunde gedrosselt werden, um gezielt PV-Überschuss in den Akku statt ins Haus zu lenken. Ohne diesen Pfad (Standard) gilt: momentan erzeugte PV-Leistung wird immer direkt zur Deckung des Hausverbrauchs durchgereicht, ohne den Umweg über die Batterie. Das hat den besten Wirkungsgrad (kein Lade- und Entladeverlust) und schont den Akku (kein zusätzlicher Zyklus). Außerhalb des Precharge-Pfads greift `dirt_shift` ausschließlich an der **Batterieentladung** an, nie an `pvpt`.

---

## Datenquellen

Alle Daten stammen aus dem volkszähler:

- **basic_load** — der tatsächliche Hausverbrauch in Wh/h, in der Standardformel berechnet als `Import + |Inverter| − Auto`. 7-Tage-Stundenmittel, stündlich in `dirt_avg_cache.json` zwischengespeichert. Dient der Mengenabschätzung der Rot-Reserve und der Deckelung außerhalb der dreckigsten Stunde (siehe Rot-Reserve und dreckigste Stunde).
- **Energieinhalt** — der reale Akkuinhalt in Wh, rekonstruiert über `get_vz_bat_cap` durch Integration von PV und Inverter seit dem letzten bekannten „leer"-Zustand (Spannung ≤ 3,0625 V/Zelle als Anker, also 49 V bei 16 Zellen; skaliert mit `cell_count` aus der zeroinput-Konfiguration), mit Wirkungsgraden. Bei jedem Lauf frisch abgefragt.
- **PV-Erzeugung** — derselbe PV-Kanal wird zusätzlich für die empirische PV-Referenzkurve genutzt (siehe dort); die Rohwerte werden dort mit `abs()` behandelt, da dieser Kanal in vielen Installationen negativ geloggt wird.

> **basic_load ist frei anpassbar.** Die Formel `Import + |Inverter| − Auto` bildet eine bestimmte Anlagenkonfiguration ab (mit E-Auto-Wallbox als gesondertem Kanal, der vom Hausverbrauch abgezogen wird). Sie ist **kein Standard**, sondern an die eigene Anlage anzupassen: nicht vorhandene Kanäle werden weggelassen, zusätzliche ergänzt. Ohne separat erfasste Wallbox entfällt `Auto`; ein zusätzlicher gesondert erfasster Verbraucher (etwa ein PV-Akku-Lader) käme als weiterer Abzugsterm hinzu. Abgezogen werden nur **planbare** Lasten, die nicht aus der Reserve gedeckt werden sollen (das Auto wird gezielt geladen, siehe Entladung nach Zone). Bedarfsgetriebene Lasten wie eine Klimaanlage bleiben dagegen **im** basic_load — sie gehören zum zu deckenden Verbrauch und sind über das 7-Tage-Mittel in Grenzen vorhersagbar erfasst. Maßgeblich ist, dass basic_load am Ende den **tatsächlich zu deckenden Hausverbrauch** ergibt — also Bezug plus die vom Akku gelieferte Wechselrichterleistung, bereinigt um alles, was nicht aus dem Akku/Netz gedeckt werden soll. Die Berechnung steht in `get_average` und wird dort direkt editiert; entsprechend wird der Kanalsatz in `vz_chans` reduziert oder erweitert.

Die 7-Tage-Basis (`average_days`) enthält genau eine volle Wochenstruktur — jeder Wochentag ist einmal vertreten, das Mittel ist über die Woche balanciert. Über `day_weights_pct` lassen sich einzelne Tage höher gewichten (siehe Konfiguration), etwa gestern und der gleiche Wochentag der Vorwoche; ohne Gewichtung zählt jeder Tag gleich.

---

## CO₂-Intensitätsprofil

Die Zonen-Einteilung (rot/grün) kommt aus **SMARD** (Bundesnetzagentur) — realen Day-Ahead-Netzdaten, kostenlos und ohne Anmeldung. SMARD ist Voraussetzung; eine alternative Quelle gibt es nicht.

`dirt_shift` fragt die prognostizierte Wind+Solar-Einspeisung und den prognostizierten Stromverbrauch für heute **und morgen** ab und bildet daraus pro Stunde das Verhältnis Erneuerbare/Last, getrennt für jeden Kalendertag. Für jeden Tag wird der **Median** dieses Verhältnisses über die 24 Stunden gebildet: Die Stunden mit einem Verhältnis auf oder über dem Median werden **grün**, die darunter **rot** — ein Schnitt, der sich an der tatsächlichen Streuung des jeweiligen Tages orientiert statt an einem festen Prozentsatz. Das trennt auch an einem durchgehend dreckigen Tag noch die relativ saubereren Stunden von den schlimmsten, statt beide pauschal gleich zu behandeln.

Nach außen liefert `dirt_shift` daraus ein **rollierendes 24-Stunden-Array**, verankert an der aktuellen Uhrzeit: Stunden von jetzt bis Mitternacht stammen aus der Einteilung von heute, Stunden nach Mitternacht aus der von morgen — eine Stunde, die im Array „schon vorbei" wirkt, ist damit tatsächlich das nächste Vorkommen dieser Stunde morgen, mit morgens eigener, echter Einteilung. Ist die Day-Ahead-Prognose für morgen zu einer bestimmten Stunde noch nicht veröffentlicht (typischerweise vor dem späten Nachmittag) oder komplett nicht verfügbar, gilt für diese Stunde die heutige Einteilung.

**Ausfall der SMARD-Abfrage:** Schlägt die Abfrage fehl, dürfen die zwischengespeicherten Daten genau **einen Tag** überbrücken — der Cache trägt ein `fetch_date`, und gestern geholte Daten sind noch nutzbar, weil deren „morgen"-Hälfte die Day-Ahead-Prognose für den nun laufenden Tag ist. Ist der Cache älter oder gar nicht vorhanden, bricht `dirt_shift` hart ab und hinterlässt einen „Alles-erlaubt"-Timer.

Zwei Zonen:

- **rot** (dreckigere Hälfte) — Stunden mit einem Erneuerbaren-Verhältnis unter dem Tages-Median
- **grün** (sauberere Hälfte) — Stunden mit einem Verhältnis auf oder über dem Median

---

## PV-Referenzkurve

`dirt_shift` braucht eine Schätzung, wie viel PV-Ertrag den Rest des Tages noch zu erwarten ist. Statt eines physikalischen Modells der Dachfläche(n) — das bei mehreren Teilflächen mit unterschiedlicher Ausrichtung und jahreszeitlich wechselnder Verschattung aufwendig zu pflegen wäre — nutzt `dirt_shift` die **eigene, real gemessene** Erzeugung der Anlage: Für jede Stunde des Tages wird über die letzten 14 Tage (`PV_CURVE_DAYS`) das 95. Perzentil (`PV_CURVE_PERCENTILE`) der stündlichen PV-Werte gebildet — nahe am Maximum, aber ohne dass ein einzelner Rekordtag die Kurve verzerrt. Da die Kurve aus der eigenen Anlage kommt, spiegelt sie deren tatsächliche Geometrie (mehrere Teilflächen, Verschattung) automatisch wider, ohne dass irgendetwas über Neigung, Ausrichtung oder Verschattung konfiguriert werden müsste.

Die Kurve wird **einmal täglich** ab `PV_CURVE_REFRESH_HOUR` (4 Uhr, eine ruhige Zeit vor Sonnenaufgang ohne konkurrierende Tagesdaten) neu berechnet und in `dirt_pv_curve_cache.json` zwischengespeichert — unabhängig vom stündlichen Rhythmus der `basic_load`-Mittelung. Die zugrundeliegende Abfrage nutzt dieselbe stündliche Auflösung (`group=hour`) wie die `basic_load`-Abfrage, nicht Minutenwerte.

---

## Wetterprognose-Skalierung

Die PV-Referenzkurve zeigt, was an einem **typischen** Tag zu erwarten ist — sie weiß aber nichts über das Wetter von heute. Diese Lücke füllt eine kostenlose, anmeldefreie Strahlungsprognose von **Open-Meteo** (`shortwave_radiation`, W/m², stündlich, für den heutigen Tag). `shortwave_radiation` ist die globale Horizontalstrahlung (direkte plus diffuse Komponente) — das physikalische Modellergebnis von Open-Meteo für die tatsächlich am Boden ankommende Strahlungsleistung.

Aus der Strahlungsprognose wird ein **Klarhimmel-Index** gebildet: `expected_pv = Referenzwert × min(1, Strahlungsprognose / Klarhimmel-GHI)`. `Klarhimmel-GHI` ist die modellierte Globalstrahlung bei wolkenlosem Himmel für dieselbe Stunde und denselben Ort, nach dem Haurwitz-Klarhimmelmodell (1945): `GHI = 1098 × cos(z) × exp(−0,059 / cos(z))` für den Zenitwinkel `z` (aus Sonnenhöhe, siehe `solar_elevation_deg`/`clear_sky_ghi`), sonst 0 (Sonne unter dem Horizont). Das Modell braucht nur die Sonnenposition — keine Trübungs-/Aerosoldaten — und ist damit offline berechenbar. Der Index wird bei 1,0 gekappt (kurzzeitige Strahlungsüberhöhung an Wolkenrändern über den Klarhimmelwert hinaus wird nicht modelliert, um die Prognose konservativ zu halten).

Damit ergibt sich automatisch eine jahreszeit- und tageszeitabhängige Referenz: im Winter ist das Klarhimmel-GHI zur Mittagszeit deutlich niedriger als im Sommer (flacherer Sonnenstand), sodass derselbe gemessene Strahlungswert im Winter einen höheren Klarhimmel-Index (weniger Dämpfung) ergibt als im Sommer bei identischer absoluter Einstrahlung — was der physikalischen Realität entspricht.

Abgefragt wird jeweils die 48-Stunden-Reihe (heute **und** morgen) in einem einzigen Aufruf; Open-Meteo liefert eine fortlaufend aktualisierte Zeitreihe, in der jeder neue Modelllauf nahtlos an den vorherigen anschließt — auch bereits vergangene Stunden des heutigen Tages werden dabei mit dem jeweils aktuellsten Modellstand überschrieben. Aus den beiden 24-Werte-Reihen (heute/morgen) bildet `dirt_shift` ein **rollierendes 24-Stunden-Array**, verankert an der aktuellen Uhrzeit: Stunden von jetzt bis Mitternacht kommen aus der heutigen Reihe, Stunden nach Mitternacht aus der morgigen — eine Stunde, die im Array „schon vorbei" wirkt, ist damit tatsächlich die echte Prognose für das nächste Vorkommen dieser Stunde morgen. `clear_sky_ghi` wird für diese Stunden entsprechend mit dem morgigen statt dem heutigen Datum berechnet. Fehlt ein Stundenwert in der morgigen Reihe, fällt diese Stunde auf den heutigen Wert zurück.

Die ganze 48-Stunden-Reihe wird stündlich neu abgefragt und in `dirt_weather_cache.json` zwischengespeichert, unabhängig von den anderen Caches. Fehlt sie (Abfrage fehlgeschlagen), wird die Referenzkurve unskaliert verwendet. Fehlt auch die Referenzkurve selbst (keine PV-Prognose überhaupt, etwa bei einer frischen Installation ohne 14 Tage Historie), rechnet `dirt_shift` mit einem Null-PV-Tag weiter — konservativ, aber weiterhin funktionsfähig (siehe Rot-Reserve und dreckigste Stunde).

---

## Entladung nach Zone

Pro Lauf (¼-stündlich) bestimmen die aktuelle Zone, der Akkuinhalt im Vergleich zur Rot-Reserve, und — in Rot — ob die laufende Stunde die dreckigste im aktuellen Fenster ist, das Entladeverhalten:

- **grün, Inhalt > Reserve** → **kein Limit**: der Akku entlädt frei.
- **grün, Inhalt ≤ Reserve** → **kein Akku-Entladen**: nur `pvpt` (`000 100 000`). Der genaue Gleichstand (Inhalt exakt gleich der Reserve) zählt als „noch nicht erreicht" — Stopp, nicht frei.
- **rot, Inhalt ≥ Reserve** → **kein Limit**: die Reserve reicht komfortabel, keine Drosselung nötig.
- **rot, Inhalt < Reserve, laufende Stunde ist die dreckigste im Fenster** → **kein Limit**: hier wird die (unzureichende) Reserve bewusst verbraucht.
- **rot, Inhalt < Reserve, laufende Stunde ist *nicht* die dreckigste im Fenster** → **gedeckelt** auf `CAP_FACTOR × basic_load` dieser Stunde (siehe Rot-Reserve und dreckigste Stunde).

`pvpt` (direkte PV-Durchleitung) läuft in jedem Fall in allen Zonen weiter; `dirt_shift` steuert ausschließlich die Batterieentladung.

**Warum Grün nicht einfach immer frei ist, solange die Reserve noch nicht erreicht ist:** Wird gezielt in den sauberen Stunden eine große, nicht in `basic_load` erfasste Last bedient (typischerweise eine E-Auto-Wallbox — bewusst ausgeklammert, damit sie nicht unnötig die Reserve-Berechnung aufbläht), sähe ein reiner PV-vs-`basic_load`-Vergleich davon nichts: `basic_load` enthält die Wallbox ja gar nicht, die Stunde bliebe rechnerisch eine Überschussstunde, obwohl der Akku real durch die Wallbox entladen wird. Der kategorische Stopp in Grün, solange die Reserve nicht erreicht ist, verhindert das: normaler Haushaltsverbrauch deckt sich weiterhin aus `pvpt`, die Wallbox-Spitze darüber hinaus zieht zwangsläufig aus dem Netz, nicht aus dem Akku.

---

## Rot-Reserve und dreckigste Stunde

Die **Rot-Reserve** ist `reserve_pct` (Standard 90 %) des `basic_load`-Bedarfs über alle **roten** Stunden zwischen jetzt und der nächsten **PV-Ertragsphase**, **netto** nach dem in diesen Stunden noch erwarteten PV-Ertrag. Die Grenze des Fensters ist die erste Stunde, deren erwartete PV den `basic_load` übersteigt (Überschussstunde): ab dort füllt sich der Akku tatsächlich wieder, und spätere rote Phasen werden vom kommenden Ertrag gedeckt, nicht von der gestrigen Ladung — sie dafür zurückzuhalten würde nur Speicherplatz für den kommenden Ertrag blockieren. Mehrere getrennte rote Phasen vor diesem Punkt (z. B. Abendrot und Nachtrot mit einer grünen Lücke dazwischen) werden alle zusammengezählt, da dazwischen nichts nachfüllt. An einem so trüben Tag, dass die erwartete PV den Verbrauch nie übersteigt, gibt es keine Überschussstunde — dann werden alle roten Stunden der rollierenden 24 Stunden reserviert, was korrekt ist, weil kein Nachfüllen kommt.

Zur Netto-Rechnung: `pvpt` deckt einen Teil dieses Bedarfs bereits direkt ab (an den Rändern eines roten Abschnitts, solange die Sonne noch nicht ganz weg bzw. schon wieder da ist), dieser Anteil muss also nicht zusätzlich aus dem Akku reserviert werden. Die 90 % legen die Reserve bewusst nicht über den berechneten Bedarf hinaus — der Akku soll sich im Regelfall über das rote Fenster praktisch vollständig entladen, statt Kapazität ungenutzt zu lassen.

### Wenn die Reserve knapp wird: die dreckigste Stunde zuerst

Reicht der aktuelle Inhalt nicht für die Reserve (`content < reserve`), wird nicht mehr pauschal jede rote Stunde unbegrenzt bedient. Stattdessen wird im selben Fenster (jetzt bis zur nächsten Überschussstunde) die **eine** rote Stunde mit dem höchsten `dirt%` bestimmt — bei gleichauf zählt die chronologisch früheste im Fenster. Nur diese Stunde bleibt unbegrenzt; jede andere rote Stunde im Fenster wird auf `CAP_FACTOR × basic_load` dieser Stunde gedeckelt (siehe Entladung nach Zone).

Das Fenster wird bei **jedem** Lauf frisch ab der aktuellen Uhrzeit neu aufgebaut — eine bereits vergangene dreckigste Stunde fällt beim nächsten Lauf einfach aus dem (jetzt kürzeren) Fenster heraus, und die dann verbleibend dreckigste Stunde wird automatisch frei, ohne dass dafür eine gesonderte Regel nötig wäre. Grüne Stunden innerhalb des Fensters zählen dabei nicht mit — nur rote Stunden werden untereinander verglichen.

Die `-v`-Ausgabe zeigt die ermittelte dreckigste Stunde in der Zeile `red: content ... < reserve ... -> dirtiest hour HH:00`; in der `-debug`-Tabelle trägt dieselbe Stunde ein führendes `!` an ihrer `chg`-Markierung (`!D`, gegenüber schlichtem `D` für jede andere Entlade-Stunde). Die `chg`-Spalte zeigt außerdem `L` für jede Ladestunde (`exp_PV > basic_load`), mit derselben `!`-Markierung (`!L`) für die sauberste Ladestunde des ganzen Tages — rein informativ, ohne Einfluss auf die Entscheidung. Eine eigene `balance`-Spalte zeigt `exp_PV − basic_load` vorzeichenbehaftet (leer bei `exp_PV = 0`, da dann redundant zu `basic_load`). Stunden, die im rollierenden Array eigentlich „morgen" abbilden sollen, aber mangels echter Morgen-Daten noch auf heutige Werte zurückgreifen (siehe CO₂-Intensitätsprofil und Wetterprognose-Skalierung), tragen ein führendes `.` — unabhängig voneinander auf `rad_Wm2` (Strahlungsprognose) und `dirt%` (SMARD-Einstufung), je nachdem welche der beiden Quellen für diese Stunde noch keine echten Morgen-Daten hatte.

Die Priorisierung wirkt an genau einer Stelle: welche Stunde `free` statt `limit` bekommt. Sie reserviert keine Wh explizit für die dreckigste Stunde gegenüber den anderen roten Stunden im selben Fenster — eine nicht-priorisierte rote Stunde bleibt bis `CAP_FACTOR × basic_load` (Standard 2×) offen, nicht auf `1× basic_load` begrenzt, wie es die Reserve-Rechnung selbst voraussetzt (`red_window_demand`/`marginal_red_hour` gehen von `1× basic_load` je Stunde aus). Zieht eine nicht-priorisierte Stunde real mehr, bleibt für die dreckigste Stunde entsprechend weniger übrig, als die Reserve-Rechnung unterstellt.

---

## Precharge (optional, Standard aus)

Mit `precharge_enabled: true` kann `dirt_shift` PV-Überschuss gezielt in den Akku umlenken, der sonst per `pvpt` ins Haus geflossen wäre (siehe `precharge_ac_pct`) — die einzige Ausnahme von der `pvpt`-Garantie (siehe Zweck). Betroffen ist dabei nie mehr als **eine** Stunde pro Lauf, aber **welche** das ist, steht nicht von vornherein fest: Die Zielstunde (`cleanest_green_hour`, siehe unten) wird bei jedem Lauf neu ermittelt, ohne Gedächtnis an vorherige Läufe. Verstreicht die aktuell ermittelte Stunde ungenutzt (z. B. weil Bedingung 1/2 zu dem Zeitpunkt nicht erfüllt war), wandert das Ergebnis beim nächsten Lauf zur nächstbesten im dann kürzeren Fenster. Aktualisiert sich zusätzlich die Strahlungsprognose oder die SMARD-Einstufung, kann sich die Zielstunde auch deutlich stärker verschieben — bis hin zu einem kompletten Sprung, wenn sich dadurch die Fenstergrenze selbst ändert (siehe `_bridge_hours`). Das lohnt sich nur, wenn die natürliche Aufladung durch den Rest des Fensters nicht reicht **und** der Umweg über den Akku (mit Rundlaufverlust) trotzdem weniger CO₂ verursacht, als die betroffene rote Stunde sonst aus dem Netz zu decken.

Drei Bedingungen, alle drei nötig, jeden Lauf neu geprüft (kein Gedächtnis über Läufe hinweg):

**1. Lohnt sich der Rundlaufverlust?** `marginal_red_hour` bestimmt die dreckigste rote Stunde im Fenster, die der aktuelle `content` **noch nicht** deckt — dafür werden die roten Stunden dreckigste-zuerst sortiert und `content` gedanklich als deren Fehlbeträge verbraucht; die Stunde, an der er ausgeht, ist die marginale. `cleanest_green_hour` (Spiegel zu `dirtiest_hour`) bestimmt die sauberste grüne Stunde im Fenster. Der `dirt%`-Spread zwischen beiden muss den Rundlaufverlust übersteigen:

```
Schwelle = 100 − (PV_to_bat_efficiency × bat_to_AC_efficiency / 100)
```

Mit wachsendem `content` verschiebt sich die marginale Stunde schrittweise zu saubereren roten Stunden — der Spread schrumpft, und das Vorladen läuft von selbst aus, sobald nur noch vergleichsweise saubere rote Stunden ungedeckt sind. Deckt `content` bereits jede rote Stunde im Fenster, gibt es keine marginale Stunde mehr, kein Vorladen.

**2. Reicht die natürliche Aufladung nicht?**
```
Lücke = reserve − content − Σ(natürlicher Überschuss der übrigen grünen Fensterstunden, max(0, exp_PV−basic_load))
```
Ist `Lücke ≤ 0`, würde der Rest des Fensters die Reserve ohnehin von selbst füllen — kein Vorladen nötig.

**3. Ist gerade jetzt die ermittelte Kandidatenstunde dran?** Innerhalb eines einzelnen Laufs wird nie mehr als eine Stunde gedeckelt: nur wenn `now` mit `cleanest_green_hour` übereinstimmt, greift der Deckel; jede andere grüne Stunde bleibt in diesem Lauf bei `ac_% = 100`, unabhängig davon, ob Bedingung 1/2 erfüllt wären. Das ist aber keine für den ganzen Tag feststehende Einzelstunde — `cleanest_green_hour` wird bei jedem Lauf neu aus dem aktuellen Fenster ermittelt (siehe oben). Verstreicht eine Kandidatenstunde ungenutzt oder ohne die Lücke zu schließen, wandert das Ergebnis beim nächsten Lauf zur dann sauberste verbleibenden Stunde weiter — über mehrere Läufe hinweg kann Precharge dadurch durchaus **mehrere verschiedene Stunden** nacheinander drosseln, immer eine pro Lauf, bis Bedingung 1 oder 2 nicht mehr erfüllt ist.

**Drosselungsstärke, stetig statt gestuft:**
```
Potenzial = min(exp_PV[jetzt], basic_load[jetzt])
ac_% = round(100 × (1 − clamp(Lücke / Potenzial, 0, 1)))
```
Fehlt viel (`Lücke ≥ Potenzial`) → `ac_% = 0`. Fehlt wenig → `ac_%` nah bei 100. Die Formel gilt unabhängig davon, ob die gewählte Stunde selbst `L` oder `D` ist — in beiden Fällen lässt sich exakt `min(exp_PV, basic_load)` zusätzlich in den Akku umlenken.

Die Ausfallsicherung (siehe dort) greift auch hier: Eine reine `ac_%`-Drosselung ohne gleichzeitiges Entlade-Limit löst ebenfalls die 30-Minuten-Alles-erlaubt-Zeile aus.

---

## Ausfallsicherung

`dirt_shift` ist optional und darf den Normalbetrieb von zeroinput nie blockieren. Begrenzt oder stoppt ein Lauf die Entladung, oder deckelt er `ac_%` unter 100 (siehe Precharge), schreibt er zusätzlich eine „Alles-erlaubt"-Zeile (`100 100 99999`) 30 Minuten später. Läuft das Skript weiter, wird die Begrenzung alle 15 Minuten erneuert; fällt es aus (cron-Ausfall, volkszähler nicht erreichbar, Absturz), hebt sich die Begrenzung nach 30 Minuten von selbst auf, und zeroinput entlädt wieder frei, als gäbe es `dirt_shift` nicht. Im freien Modus ohne Precharge-Drosselung ist ohnehin alles erlaubt, dort genügt die eine Zeile.

---

## Netz-Dreckigkeit exportieren

`dirt_shift` kann den aktuellen Dreckigkeitswert zusätzlich in volkszähler protokollieren — bei jedem Lauf, wenn `vz_dirtiness_uuid` gesetzt ist. Der Wert ist `(1 − Verhältnis) × 100` (Erneuerbare/Last der aktuellen Stunde): Vorzeichen-Konvention wie bei den bestehenden Leistungskanälen der Anlage (Bezug positiv, Einspeisung negativ) — je positiver, desto dreckiger (unterdurchschnittlicher Erneuerbaren-Anteil); bei einem Erneuerbaren-Überschuss (Verhältnis > 1) wird der Wert sogar negativ, wie eine Einspeisung.

Geschrieben wird per direktem **HTTP-POST** an volkszählers Middleware-API, einmal pro Lauf: `http://{vz_host_port}/data/{vz_dirtiness_uuid}.json`, mit Wert und aktuellem Zeitstempel. Genutzt wird derselbe `vz_host_port`, den `dirt_shift` ohnehin schon für seine anderen volkszähler-Abfragen verwendet — kein vzlogger-Meter, keine lokale Datei. `vz_dirtiness_uuid` muss eine echte, vorher in volkszähler angelegte Kanal-UUID sein. Ein fehlgeschlagener Schreibversuch (Netzwerkfehler, falsche UUID) bricht den Lauf nicht ab, er wird nur unter `-v` gemeldet.

---

## Ausgabe: timer.txt

`dirt_shift` schreibt `timer.txt` im zeroinput-Format:

```
YYYY-MM-DD HH:MM:00  <entlade-W>  <ac-%>  <energie-Wh>
```

- Jede Zeile trägt das reale Kalenderdatum, an dem sie geschrieben wurde.
- **entlade-W** — Entlade-Deckel; `100` (Prozent) = kein Limit, `CAP_FACTOR × basic_load` (Watt) = gedeckelt, `000` = kein Akku-Entladen (Stopp).
- **ac-%** — Wechselrichter-Durchleitung, `100` (pvpt garantiert), außer während einer aktiven Precharge-Drosselung (siehe dort): dann für genau die eine sauberste grüne Kandidatenstunde ein stetiger Wert zwischen `0` und `100`.
- **energie-Wh** — Energiebudget; `99999` = praktisch unbegrenzt (Entladung erlaubt), `000` = kein Budget (Stopp).

Die drei Modi sind also: `100 100 99999` (kein Limit), `<CAP_FACTOR×basic_load> 100 99999` (gedeckelt), `000 100 000` (Stopp). Das Wh-Energiefeld bleibt auch im gedeckelten Modus unbegrenzt (`99999`) — die tatsächlich abgegebene Energiemenge wird nicht hierüber begrenzt, sondern ergibt sich aus der Reserve-Logik selbst. Der Deckel begrenzt nur die Momentanleistung: `basic_load` deckt den gewöhnlichen Verbrauch dieser Stunde, `CAP_FACTOR` (Standard 2×) lässt Spielraum für kurze Lastspitzen, ohne eine ineffiziente Wechselrichter-Stufenschaltung zu provozieren — bleibt aber weit unter jeder bewusst hohen Last (z. B. E-Auto-Ladung), die dadurch zuverlässig aus dem Netz statt aus dem Akku gedeckt wird.

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
- `vz_host_port`, `vz_chans` — volkszähler-Host und Kanal-UUIDs für die data.json-API. Getrennt von zeroinputs `vz_channels`/`vzlogger_log_file`: dirt_shift nutzt die HTTP-API für Mittelwerte, PV-Kurve und Energieinhalt, zeroinput die vzlogger-FIFO für die Live-Regelung. Beide greifen auf denselben volkszähler zu, die UUID-Listen müssen nicht identisch sein.
- `vz_dirtiness_uuid` — echte volkszähler-Kanal-UUID für den Dreckigkeitswert-Export per HTTP-POST (siehe Netz-Dreckigkeit exportieren). Leer deaktiviert den Export.
- `average_days` — Tage für das Stundenmittel (Standard 7)
- `day_weights_pct` — Tagesgewichtung in Prozent für das Mittel, chronologisch: Index 0 = ältester Tag (heute minus `average_days`, also der gleiche Wochentag der Vorwoche), Index −1 = gestern. Gestern und der Vorwochentag stärker zu gewichten fängt den jüngsten Trend und die Wochentagsstruktur ein. Die Länge muss `average_days` entsprechen; bei Abweichung werden alle Tage gleich gewichtet. Alle 100 = neutral.
- `reserve_pct` — Prozent des `basic_load`-Bedarfs über das rote Fenster, der reserviert wird (Standard 90)
- `latitude`, `longitude` — Standort der Anlage (Dezimalgrad) für das Klarhimmel-Modell und die Strahlungsprognose; Standard ~Mitte Deutschland (51,0 / 10,0)
- `PV_to_bat_efficiency`, `bat_to_AC_efficiency` — Wirkungsgrade für die Rekonstruktion des Energieinhalts
- `max_days_empty_battery` — wie viele Tage rückwärts nach einem „leer"-Zustand gesucht wird
- `disable_zeroinput_timer` — auf `true` rechnet und gibt aus, ohne die Timer-Datei zu schreiben (Trockenlauf)
- `precharge_enabled` — optional, Standard `false`. Aktiviert den Precharge-Pfad (siehe dort), der `ac_%` an einer einzelnen grünen Stunde unter 100 deckeln kann, um PV-Überschuss gezielt in den Akku statt ins Haus zu lenken.

`CAP_FACTOR` (Standard 2) ist **keine** Konfigurationsoption, sondern eine benannte Konstante im Code (siehe Ausgabe: timer.txt) — bewusst nicht extern konfigurierbar, da sie eine feste, dokumentierte Sicherheitsmarge ist, kein anlagenspezifischer Wert.

### Fehlerverhalten

Bei einem harten Fehler (volkszähler liefert keine vollständigen Tage, Energieinhalt nicht berechenbar, SMARD-Daten weder frisch noch als Ein-Tag-Ersatz verfügbar) bricht `dirt_shift` ab, schreibt aber zuvor — sofern der Timer-Pfad bekannt ist — eine „Alles-erlaubt"-Zeile, damit zeroinput nicht durch eine veraltete oder fehlende Begrenzung blockiert wird:

```
2026-07-09 00:00:00 100 100 99999
```

(volle Entladung, volle Durchleitung, praktisch unbegrenztes Energiebudget, mit dem aktuellen Datum). Ist nicht einmal die `zeroinput.conf` lesbar (Timer-Pfad unbekannt), bleibt nur der Abbruch mit Fehlermeldung.

---

## Aufrufoptionen

- `-v` — ausführliche Konsolenausgabe
- `-html` — HTML-Kopf/-Fuß um die Ausgabe
- `-debug` — mehr Ausgabe (impliziert `-v`)
- `-avgnew` — erzwingt eine frische Abfrage statt der Caches: `basic_load`-Mittel, PV-Referenzkurve, Strahlungsprognose und SMARD-Zonen
