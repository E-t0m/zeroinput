# dirt_shift – Funktionsspezifikation
*v1.0*

## Zweck

`dirt_shift` verschiebt die Batterieentladung gezielt in die Netzstunden mit der höchsten CO₂-Intensität. Der deutsche Strommix ist abends und nachts ungünstiger (PV weg, Abendlast hoch, fossile Spitzen) und früh morgens, bis genug PV im Netz ist; tagsüber ist seine CO₂-Intensität niedrig. Wenn man wählen kann, *wann* die Batterie statt des Netzes den Verbrauch deckt, vermeidet man in den Stunden hoher CO₂-Intensität am meisten Emissionen. `dirt_shift` lenkt daher den gesamten verfügbaren Akkuinhalt in diese Stunden — am stärksten in die dreckigste.

Die CO₂-Intensität ist eine reine **Netz**-Eigenschaft und hängt nicht von der eigenen Anlage ab. Die eigene Anlage (PV, Verbrauch, Akkuinhalt) bestimmt nur die *Menge* der verfügbaren und benötigten Energie.

`dirt_shift` steuert die Batterieentladung anhand der Netz-CO₂-Intensität. Es schreibt dazu dieselbe `timer.txt`, die zeroinput für die Entladesteuerung liest.

Die direkte PV-Durchleitung (`pvpt`, PV pass-through) wird garantiert, außer im optionalen Precharge-Pfad (`precharge_enabled`, siehe dort) — dort, und nur dort, kann sie in den sehr sauberen Stunden gedrosselt werden, um gezielt PV-Überschuss in den Akku statt ins Haus zu lenken. Ohne diesen Pfad (Standard) gilt: momentan erzeugte PV-Leistung wird immer direkt zur Deckung des Hausverbrauchs durchgereicht, ohne den Umweg über die Batterie. Das hat den besten Wirkungsgrad (kein Lade- und Entladeverlust) und schont den Akku (kein zusätzlicher Zyklus). Außerhalb des Precharge-Pfads greift `dirt_shift` ausschließlich an der **Batterieentladung** an, nie an `pvpt`.

---

## Datenquellen

Alle Daten stammen aus dem volkszähler:

- **basic_load** — der tatsächliche Hausverbrauch in Wh/h, in der Standardformel berechnet als `Import + |Inverter| − Auto`. 7-Tage-Stundenmittel, stündlich in `dirt_avg_cache.json` zwischengespeichert. Dient der Mengenabschätzung der Rot-Reserve und der Deckelung außerhalb der dreckigsten Stunde (siehe Rot-Reserve und dreckigste Stunde).
- **Energieinhalt** — der reale Akkuinhalt in Wh, rekonstruiert über `get_vz_bat_cap` durch Integration von PV und Inverter seit dem letzten bekannten „leer"-Zustand (Spannung ≤ 3,0625 V/Zelle als Anker, also 49 V bei 16 Zellen; skaliert mit `cell_count` aus der zeroinput-Konfiguration), mit Wirkungsgraden. Als Anker zählt nur ein **anhaltender** Leerzustand: Die Spannung muss mindestens `EMPTY_MIN_DURATION_S` (Code-Konstante, 120 s) unter der Schwelle bleiben. Ein kurzer Einbruch — Lastspitze, Wechselrichter-Start, Ausfall von Sekunden — würde den Anker sonst mitten in einen halbvollen Akku setzen und den Inhalt ab da dauerhaft zu niedrig rechnen. Zusätzlich werden Spannungswerte **unter 2,5 V/Zelle** (`IMPLAUSIBLE_V_PER_CELL`, 40 V bei 16S — der LiFePO₄-Entladeschluss, unter dem jedes BMS abschaltet) schon beim Einlesen verworfen: Sie sind physikalisch unmöglich und damit ein Sensor- oder Verbindungsfehler. Das fängt auch einen *anhaltenden* Ausfall ab, den die Dauerprüfung allein durchließe. Bei jedem Lauf frisch abgefragt.

**Warnung bei negativem Inhalt.** Ein rekonstruierter Inhalt unter −`NEG_CONTENT_WARN_FRACTION` (Code-Konstante, 5 %) der `battery_capacity_wh` ist physikalisch unmöglich — seit dem letzten Leerzustand wurde mehr Entladung als Ladung gezählt — und wird immer gemeldet, auch ohne `-v`. `_neg_content_diagnosis` unterscheidet dabei zwei Ursachen anhand derselben Kanalbilanz **ganz ohne** jeden Wirkungsgrad (`raw_cap`): Da beide Wirkungsgrade den Inhalt nur nach unten drücken können, nie nach oben, ist `raw_cap` eine belastbare obere Schranke. Bricht `raw_cap` die Schwelle ebenfalls, kann keine Wirkungsgrad-Korrektur das erklären — die Kanaldaten selbst sind falsch, typischerweise ein Wechselrichter, der Leistung *anfordert*, aber nicht *liefert* (ausgelöster FI/RCD, durchgebrannte Sicherung, defektes Gerät). Bleibt `raw_cap` innerhalb der Grenze, könnten `PV_to_bat_efficiency`/`bat_to_AC_efficiency` schlicht niedriger als die realen Anlagenverluste eingestellt sein — die Meldung nennt in diesem Fall trotzdem beide Möglichkeiten, da sich ein kleinerer Kanalfehler damit nicht ausschließen lässt.
- **PV-Erzeugung** — derselbe PV-Kanal wird zusätzlich für die empirische PV-Referenzkurve genutzt (siehe dort); die Rohwerte werden dort mit `abs()` behandelt, da dieser Kanal in vielen Installationen negativ geloggt wird.

> **basic_load ist frei anpassbar.** Die Formel `Import + |Inverter| − Auto` bildet eine bestimmte Anlagenkonfiguration ab (mit E-Auto-Wallbox als gesondertem Kanal, der vom Hausverbrauch abgezogen wird). Sie ist **kein Standard**, sondern an die eigene Anlage anzupassen: nicht vorhandene Kanäle werden weggelassen, zusätzliche ergänzt. Ohne separat erfasste Wallbox entfällt `Auto`; ein zusätzlicher gesondert erfasster Verbraucher (etwa ein PV-Akku-Lader) käme als weiterer Abzugsterm hinzu. Abgezogen werden nur **planbare** Lasten, die nicht aus der Reserve gedeckt werden sollen (das Auto wird gezielt geladen, siehe Entladung nach Zone). Bedarfsgetriebene Lasten wie eine Klimaanlage bleiben dagegen **im** basic_load — sie gehören zum zu deckenden Verbrauch und sind über das 7-Tage-Mittel in Grenzen vorhersagbar erfasst. Maßgeblich ist, dass basic_load am Ende den **tatsächlich zu deckenden Hausverbrauch** ergibt — also Bezug plus die vom Akku gelieferte Wechselrichterleistung, bereinigt um alles, was nicht aus dem Akku/Netz gedeckt werden soll. Die Berechnung steht in `get_average` und wird dort direkt editiert; entsprechend wird der Kanalsatz in `vz_chans` reduziert oder erweitert.

Die 7-Tage-Basis (`average_days`) enthält genau eine volle Wochenstruktur — jeder Wochentag ist einmal vertreten, das Mittel ist über die Woche balanciert. Über `day_weights_pct` lassen sich einzelne Tage höher gewichten (siehe Konfiguration), etwa gestern und der gleiche Wochentag der Vorwoche; ohne Gewichtung zählt jeder Tag gleich.

---

## CO₂-Intensitätsprofil

Die Zonen-Einteilung (rot/grün) kommt aus **SMARD** (Bundesnetzagentur) — realen Day-Ahead-Netzdaten, kostenlos und ohne Anmeldung. SMARD ist Voraussetzung; eine alternative Quelle für echte Netzdaten gibt es nicht. Bei Ausfall über den Ein-Tages-Cache hinaus greift ein modellierter Notmodus (siehe Ausfall der SMARD-Abfrage).

`dirt_shift` fragt die prognostizierte Wind+Solar-Einspeisung und den prognostizierten Stromverbrauch für heute **und morgen** ab und bildet daraus pro Stunde das Verhältnis Erneuerbare/Last.

**Fehlt die Verbrauchsprognose, wird das Profil des letzten verfügbaren Tages verwendet.** SMARD veröffentlicht die beiden Reihen unabhängig voneinander, und die Verbrauchsprognose hinkt der Erzeugungsprognose regelmäßig um einen Tag oder mehr hinterher. Da ein Tag nur nutzbar ist, wenn **beide** Reihen ihn abdecken, legte allein dieser Verzug das ganze Programm still — obwohl mit der Erzeugungskurve genau die Größe vorlag, die den Tagesgang der Dreckigkeit bestimmt. Der Ersatz ist vertretbar, weil der Median-Schnitt ausschließlich die **Form** des Verhältnisses über das Fenster liest, nie dessen absolute Höhe; Lastprofile wiederholen sich von Tag zu Tag eng genug, dass die Form erhalten bleibt. An einem ungewöhnlichen Tag (Feiertag, Hitzewelle) verschiebt sich der Schnitt leicht — ein weit kleinerer Fehler als gar keine Zonen. Gesucht wird tageweise rückwärts bis zu sieben Tage, es gilt also stets das frischeste verfügbare Profil; bei `-v` wird der Ersatz gemeldet. Fehlt dagegen die **Erzeugungsprognose**, ist der Tag nicht beurteilbar und entfällt. Aus beiden Tagen wird dann das **rollierende Fenster** der nächsten 24 Stunden zusammengesetzt: Stunden von jetzt bis Mitternacht aus heute, die danach aus morgen.

Erst über dieses zusammengesetzte Fenster wird **einmal** der **Median** gebildet: Stunden mit einem Verhältnis auf oder über dem Median werden **grün**, die darunter **rot** — ein Schnitt, der sich an der tatsächlichen Streuung orientiert statt an einem festen Prozentsatz. Das trennt auch in einem durchgehend dreckigen Fenster noch die relativ saubereren Stunden von den schlimmsten. Da es ein Median ist, ergibt der Schnitt immer etwa 12 rote und 12 grüne Stunden; ein Entarten zu „alles grün" oder „alles rot" ist konstruktiv ausgeschlossen.

**Warum über das Fenster und nicht je Kalendertag:** Ein Schnitt pro Tag lässt „rot" bedeuten „dreckig *für diesen Tag*" — und das ist über die Mitternachtsgrenze hinweg, die das Fenster jeden Abend überquert, nicht vergleichbar. War der Mittag heute außergewöhnlich sauber, liegt der Tages-Median hoch, und eine real dreckige Abendstunde fällt trotzdem darunter und gilt als grün; die ruhigeren Stunden des nächsten Tages, absolut sauberer, gelten dagegen als rot. Der Akku wurde dann in der dreckigeren Stunde gestoppt, um Inhalt für die saubereren aufzuheben — genau das Gegenteil des Ziels. Ein einziger Schnitt über die 24 Stunden, um die es tatsächlich geht, behebt das und stellt die Zonen zugleich auf denselben Maßstab wie den Median-Test der Wallbox (siehe dort), der ebenfalls über das rollierende Fenster rechnet.

Nach außen liefert `dirt_shift` daraus ein **rollierendes 24-Stunden-Array**, verankert an der aktuellen Uhrzeit: Stunden von jetzt bis Mitternacht stammen aus der Einteilung von heute, Stunden nach Mitternacht aus der von morgen — eine Stunde, die im Array „schon vorbei" wirkt, ist damit tatsächlich das nächste Vorkommen dieser Stunde morgen, mit morgens eigener, echter Einteilung. Ist die Day-Ahead-Prognose für morgen zu einer bestimmten Stunde noch nicht veröffentlicht (typischerweise vor dem späten Nachmittag) oder komplett nicht verfügbar, gilt für diese Stunde die heutige Einteilung.

**Ausfall der SMARD-Abfrage:** Schlägt die Abfrage fehl, dürfen die zwischengespeicherten Daten genau **einen Tag** überbrücken — der Cache trägt ein `fetch_date`, und gestern geholte Daten sind noch nutzbar, weil deren „morgen"-Hälfte die Day-Ahead-Prognose für den nun laufenden Tag ist. Ist der Cache älter oder gar nicht vorhanden, versucht `dirt_shift` einen **Notmodus**: Aus der Wetterprognose (Solar und Wind über `wind_speed_100m` in **einem** Open-Meteo-Aufruf geholt und gemeinsam gecacht, wie die Strahlung stündlich) und einem fest hinterlegten, normierten Profil der **bundesweiten Netzlast** wird ein *relativer* Dreckigkeitsverlauf modelliert und wie sonst per Median geschnitten. Das ist bewusst grob und nie kalibriert — nur die Reihenfolge der Stunden zählt für den Schnitt —, aber es bezieht den Wind ein, sodass eine windige Nacht sauberer eingestuft wird als ein trüber Mittag (was eine reine Solar-Schätzung falsch herum träfe). Der Notmodus wird in jeder Ausgabe **deutlich als solcher gekennzeichnet** und nie mit echten Daten verwechselt; er wird nie zwischengespeichert. Lässt sich auch die Wetterprognose nicht abrufen, bricht `dirt_shift` hart ab und hinterlässt einen „Alles-erlaubt"-Timer.

Zwei Zonen:

- **rot** (dreckigere Hälfte) — Stunden mit einem Erneuerbaren-Verhältnis unter dem Median des rollierenden Fensters
- **grün** (sauberere Hälfte) — Stunden mit einem Verhältnis auf oder über diesem Median

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
- **grün, Inhalt ≤ Reserve, reservierte rote Stunden mindestens 10 `dirt%`-Punkte dreckiger als jetzt** → **kein Akku-Entladen**: nur `pvpt` (`000 100 000`). Der genaue Gleichstand (Inhalt exakt gleich der Reserve) zählt als „noch nicht erreicht" — Stopp, nicht frei.
- **grün, Inhalt ≤ Reserve, Abstand unter 10 Punkten** → **gedeckelt** wie in einer nicht-priorisierten roten Stunde: der Akku deckt den Hausanteil, mehr nicht.

**Warum der Mindestabstand:** 1 Wh von jetzt in eine spätere Stunde zu verschieben lohnt sich proportional zur `dirt%`-Differenz zwischen beiden. Liegt die reservierte Nacht nur wenige Punkte über der laufenden Stunde, bewegt sich der Gewinn innerhalb der Prognoseunschärfe — der Netzbezug jetzt ist dagegen sicher. Der Median-Schnitt allein kennt diese Größe nicht: Er macht die dreckigere Hälfte rot, auch wenn sie nur um einen Punkt dreckiger ist. Verglichen wird mit dem **Mittelwert** der roten Stunden, für die die Reserve gilt (die `red_window_demand` zählt, oder der nächste rote Block, falls `now` im Überschuss liegt) — die Reserve wird ja über den ganzen Block verbraucht, nicht in einer einzelnen Stunde. Der Abstand ist als **Differenz** in Punkten definiert, nicht als Anteil: `dirt%` ist vorzeichenbehaftet und wird an windigen Tagen negativ, ein Bruchteil des Medians würde dort seine eigene Bedeutung umkehren. `STOP_MIN_DIRT_GAP` (10) ist eine benannte Konstante im Code, keine Konfigurationsoption. Fehlt ein `dirt%`-Wert auf einer der beiden Seiten, gilt die vorsichtige Seite: Stopp. Die `-v`-Modus-Zeile zeigt den Abstand als `[red block +N dirt]` beziehungsweise `[red block only +N dirt < 10: limit, not stop]`.
- **rot, Inhalt ≥ Reserve** → **kein Limit**: die Reserve reicht komfortabel, keine Drosselung nötig.
- **rot, Inhalt < Reserve, laufende Stunde ist die dreckigste im Fenster** → **kein Limit**: hier wird die (unzureichende) Reserve bewusst verbraucht.
- **rot, Inhalt < Reserve, laufende Stunde ist *nicht* die dreckigste im Fenster** → **gedeckelt** auf `limit_discharge_rate` (Watt, Konfiguration) **und gleichzeitig** auf ein Viertelstunden-Energiebudget von `¼ × (min(100 %, reserve_pct) × basic_load − gemessene PV)` dieser Stunde (siehe Rot-Reserve und dreckigste Stunde).

`pvpt` (direkte PV-Durchleitung) läuft in jedem Fall in allen Zonen weiter; `dirt_shift` steuert ausschließlich die Batterieentladung.

**Warum Grün nicht einfach immer frei ist, solange die Reserve noch nicht erreicht ist:** Wird gezielt in den sauberen Stunden eine große, nicht in `basic_load` erfasste Last bedient (typischerweise eine E-Auto-Wallbox — bewusst ausgeklammert, damit sie nicht unnötig die Reserve-Berechnung aufbläht), sähe ein reiner PV-vs-`basic_load`-Vergleich davon nichts: `basic_load` enthält die Wallbox ja gar nicht, die Stunde bliebe rechnerisch eine Überschussstunde, obwohl der Akku real durch die Wallbox entladen wird. Der kategorische Stopp in Grün, solange die Reserve nicht erreicht ist, verhindert das: normaler Haushaltsverbrauch deckt sich weiterhin aus `pvpt`, die Wallbox-Spitze darüber hinaus zieht zwangsläufig aus dem Netz, nicht aus dem Akku.

---

## Rot-Reserve und dreckigste Stunde

Die **Rot-Reserve** ist `reserve_pct` (Standard 90 %) des `basic_load`-Bedarfs über alle **roten** Stunden zwischen jetzt und der nächsten **PV-Ertragsphase**, **netto** nach dem in diesen Stunden noch erwarteten PV-Ertrag. Die Grenze des Fensters ist die erste Stunde, deren erwartete PV den `basic_load` übersteigt (Überschussstunde): ab dort füllt sich der Akku tatsächlich wieder, und spätere rote Phasen werden vom kommenden Ertrag gedeckt, nicht von der gestrigen Ladung — sie dafür zurückzuhalten würde nur Speicherplatz für den kommenden Ertrag blockieren. Mehrere getrennte rote Phasen vor diesem Punkt (z. B. Abendrot und Nachtrot mit einer grünen Lücke dazwischen) werden alle zusammengezählt, da dazwischen nichts nachfüllt. An einem so trüben Tag, dass die erwartete PV den Verbrauch nie übersteigt, gibt es keine Überschussstunde — dann werden alle roten Stunden der rollierenden 24 Stunden reserviert, was korrekt ist, weil kein Nachfüllen kommt.

**Untergrenze über eine zweite Schätzung.** Die obige Fensterrechnung hat einen blinden Fleck: Liegt `now` selbst innerhalb einer laufenden Überschussphase, bricht der Scan sofort ab und liefert `0` — unter der Annahme, der kommende Ertrag fülle den Akku ohnehin. Diese Annahme fällt in sich zusammen, sobald eine Last (allen voran eine laufende Wallbox) den Akku schneller leert, als der Überschuss ihn füllt: Mit `reserve = 0` ist `content > reserve` trivial erfüllt, der Modus geht auf `free`, und der Akku wird ausgerechnet dann unbegrenzt freigegeben, wenn ihn nichts schützt. Deshalb wird zusätzlich `upcoming_red_demand` berechnet und der **größere** der beiden Werte gilt (dieselbe Formel nutzt auch die Wallbox, siehe dort):

```
Reserve = reserve_pct × max(red_window_demand,
                            max(0, upcoming_red_demand − surplus_before_next_red))
```

Keine der beiden Schätzungen ist dabei entbehrlich, denn sie haben komplementäre blinde Flecken: `red_window_demand` schaut **breit, aber nur bis zum ersten Überschuss** (summiert also mehrere getrennte rote Phasen und an einem trüben Tag die ganzen 24 Stunden), `upcoming_red_demand` schaut **schmal, dafür über den Überschuss hinweg** (nur der eine nächste zusammenhängende rote Block, aber auch mitten in einer Überschussphase sichtbar). Je nach Tageslage ist mal die eine, mal die andere die bindende.

**Der erwartete Überschuss wird davon abgezogen.** Die ursprüngliche Annahme von `red_window_demand` — nachts und vormittags entladen, ab Mittag lädt die PV den Akku wieder auf, abends erneut entladen — ist an sonnigen Tagen schlicht richtig; sie war nur nie nachgerechnet. `surplus_before_next_red` summiert deshalb den erwarteten Überschuss der Stunden zwischen jetzt und dem Beginn des nächsten roten Blocks und zieht ihn vom Bedarf ab. Zurückgehalten wird nur, was der Überschuss **nicht** deckt:

- Deckt er die Lücke mehrfach (typischer Sonnentag), fällt die Reserve auf 0 — der Akku darf den Vormittag über entladen, genau wie vorgesehen, und füllt sich nachmittags von selbst.
- Deckt er sie teilweise, bleibt der ungedeckte Rest stehen.
- Liegt kein Überschuss mehr vor dem roten Block (Abend, Nacht, trüber Tag), ändert sich nichts — der Schutz greift voll.

Der Übergang ist damit stetig und misst, wie weit die Annahme trägt, statt sie pauschal zu glauben oder pauschal zu verwerfen.

Diese Untergrenze wirkt nicht nur während einer Überschussphase, sondern in jedem Zeitfenster, das vor einem Überschuss liegt — also auch nachts in grünen Stunden, wenn der rote Block erst nach dem morgigen Ertrag kommt. Der Akku entlädt sich dort nicht mehr, solange sein Inhalt den nächsten roten Block nicht deckt: In einer sauberen Stunde soll das Haus aus dem Netz versorgt werden und der Inhalt für die dreckigen Stunden bereitstehen — genau der Zweck des Programms.

Zur Netto-Rechnung: `pvpt` deckt einen Teil dieses Bedarfs bereits direkt ab (an den Rändern eines roten Abschnitts, solange die Sonne noch nicht ganz weg bzw. schon wieder da ist), dieser Anteil muss also nicht zusätzlich aus dem Akku reserviert werden. Die 90 % legen die Reserve bewusst nicht über den berechneten Bedarf hinaus — der Akku soll sich im Regelfall über das rote Fenster praktisch vollständig entladen, statt Kapazität ungenutzt zu lassen.

### Wenn die Reserve knapp wird: die dreckigste Stunde zuerst

Reicht der aktuelle Inhalt nicht für die Reserve (`content < reserve`), wird nicht mehr pauschal jede rote Stunde unbegrenzt bedient. Stattdessen wird im selben Fenster (jetzt bis zur nächsten Überschussstunde) die **eine** rote Stunde mit dem höchsten `dirt%` bestimmt — bei gleichauf zählt die chronologisch früheste im Fenster. Nur diese Stunde bleibt unbegrenzt; jede andere rote Stunde im Fenster wird gleichzeitig auf `limit_discharge_rate` (Watt) **und** auf `¼ × (min(100 %, reserve_pct) × basic_load − gemessene PV)` dieser Stunde (Wh, Viertelstunden-Energiebudget) gedeckelt (siehe Entladung nach Zone).

Das Fenster wird bei **jedem** Lauf frisch ab der aktuellen Uhrzeit neu aufgebaut — eine bereits vergangene dreckigste Stunde fällt beim nächsten Lauf einfach aus dem (jetzt kürzeren) Fenster heraus, und die dann verbleibend dreckigste Stunde wird automatisch frei, ohne dass dafür eine gesonderte Regel nötig wäre. Grüne Stunden innerhalb des Fensters zählen dabei nicht mit — nur rote Stunden werden untereinander verglichen.

Die `-v`-Ausgabe zeigt die ermittelte dreckigste Stunde in der Zeile `content ... Wh (SoC%)   reserve(...%) ... Wh [window|upcoming: .../...-...pv] -> dirtiest  HH:MM   =>  mode: ...` (der Wert hinter `content` ist der Füllstand in Prozent der `battery_capacity_wh`); die Klammer nennt dabei, welche der beiden Schätzungen gerade bindend war, gefolgt von beiden Rohwerten (`red_window_demand`/`upcoming_red_demand`) und dem davon abgezogenen Überschuss (`...pv`), alle vor der `reserve_pct`-Skalierung; in der `-debug`-Tabelle trägt dieselbe Stunde ein führendes `!` an ihrer `chg`-Markierung (`!D`, gegenüber schlichtem `D` für jede andere Entlade-Stunde). Die `chg`-Spalte zeigt außerdem `L` für jede Ladestunde (`exp_PV > basic_load`), mit derselben `!`-Markierung (`!L`) für die sauberste Ladestunde des ganzen Tages — rein informativ, ohne Einfluss auf die Entscheidung. Eine eigene `balance`-Spalte zeigt `exp_PV − basic_load` vorzeichenbehaftet (leer bei `exp_PV = 0`, da dann redundant zu `basic_load`). Stunden, die im rollierenden Array eigentlich „morgen" abbilden sollen, aber mangels echter Morgen-Daten noch auf heutige Werte zurückgreifen (siehe CO₂-Intensitätsprofil und Wetterprognose-Skalierung), tragen ein führendes `.` — unabhängig voneinander auf `rad_Wm2` (Strahlungsprognose) und `dirt%` (SMARD-Einstufung), je nachdem welche der beiden Quellen für diese Stunde noch keine echten Morgen-Daten hatte. Eine abschließende Zeile summiert `exp_PV` und `basic_load` je über alle 24 Stunden, bildet deren Differenz (die Tagesbilanz) sowie den unbewichteten `dirt%`-Durchschnitt (`Ø`) über alle SMARD-abgedeckten Stunden.

Die Priorisierung wirkt an genau einer Stelle: welche Stunde `free` statt `limit` bekommt. Sie reserviert keine Wh explizit für die dreckigste Stunde gegenüber den anderen roten Stunden im selben Fenster — eine nicht-priorisierte rote Stunde ist stattdessen über das Viertelstunden-Energiebudget selbst begrenzt: auf `min(100 %, reserve_pct) × basic_load − gemessene PV` je Stunde (vier Viertelstunden zu je `¼` davon). `reserve_pct` skaliert dabei denselben Anteil des Bedarfs wie bei der Reserve-Berechnung selbst; die gemessene PV wird unabhängig davon in voller Höhe abgezogen, da sie ohnehin unbegrenzt per `pvpt` fließt und daher nicht anteilig „reserviert" werden muss. Der zusätzliche `limit_discharge_rate`-Deckel (Watt) begrenzt dabei nur die Momentanleistung innerhalb der Viertelstunde, nicht die insgesamt entnommene Energiemenge.

---

## Precharge (optional, Standard aus)

Mit `precharge_enabled: true` kann `dirt_shift` PV-Überschuss gezielt in den Akku umlenken, der sonst per `pvpt` ins Haus geflossen wäre (siehe `precharge_ac_pct`) — die einzige Ausnahme von der `pvpt`-Garantie (siehe Zweck). Precharge ist das **Spiegelbild der Entladelogik**: Wie die Entladung in dreckige Stunden verschoben wird, wird das Laden in saubere gezogen. Betroffen sind die **sehr sauberen** Stunden des Fensters, nicht mehr nur die eine sauberste.

Gedrosselt wird eine grüne Stunde nur, wenn alle drei folgenden Maßstäbe gelten — dieselben, die auch anderswo in der Steuerung verwendet werden:

**1. Rang.** Die Stunde muss zu den saubersten `precharge_cleanest_pct` % des rollierenden Fensters gehören (siehe `_dirt_rank_pct`, wie bei der Wallbox). Genau das weitet Precharge von der einzelnen saubersten Stunde auf alle sehr sauberen aus: Mehrere Stunden drosseln gemeinsam, jede trägt einen Teil des Ziels bei.

**2. Abstand.** Der `dirt%` der Stunde muss mindestens `STOP_MIN_DIRT_GAP` (Code-Konstante, 10) Punkte **unter** dem mittleren `dirt%` der roten Stunden liegen, für die geladen wird (siehe `reserved_red_hours`). 1 Wh von hier dorthin zu verschieben lohnt nur im Verhältnis zu dieser Differenz; ein Abstand innerhalb der Prognoseunschärfe wiegt den Rundlaufverlust nicht auf. Dieselbe Differenz-statt-Anteil-Rechnung wie bei `stop`, vorzeichensicher.

**3. Überlauf.** Würde ohne Umlenken überhaupt etwas verloren gehen? Der **gesamte** erwartete Lade-Überschuss der sauberen Stunden bis zum roten Block — **die laufende Stunde eingeschlossen** — muss den **freien Akkuraum** übersteigen (`battery_capacity_wh − content`). Nur der Teil, der nicht mehr hineinpasst, geht sonst verloren, und nur dieser Überlauf ist das Umlenken wert. `pvpt` selbst ist verlustfrei, der Akkuumweg nicht — Überschuss umzulenken, der ohnehin gepasst hätte, verschenkt bloß den Rundlaufverlust. Dass die laufende Stunde mitzählt, ist entscheidend: Zählte man nur die *übrigen* Stunden, nähme sich jede große Überschussstunde aus ihrer eigenen Überlaufprüfung heraus und hielte sich für unverzichtbar — genau der Fehler, der an einem sonnigen Tag die ertragreichsten Stunden gedrosselt hätte, obwohl der Akku sich mehrfach selbst füllt.

**Drosselungsstärke.** Umgelenkt wird der Überlauf, anteilig auf die qualifizierenden Stunden nach ihrem jeweiligen Überschuss verteilt; `pvpt` sinkt nur um die tatsächlich umgelenkten Watt:

```
Überlauf   = Σ(Überschuss der sauberen Stunden, jetzt inkl.) − freier Akkuraum
Umlenkung  = Überlauf × (Überschuss[jetzt] / Σ Überschuss)
ac_% = max(PRECHARGE_AC_FLOOR, round(100 × (exp_PV[jetzt] − Umlenkung) / exp_PV[jetzt]))
```

`PRECHARGE_AC_FLOOR` (Code-Konstante, 20) ist eine harte Untergrenze: Eine Precharge-Entscheidung darf die Wechselrichter-Durchleitung nie so weit absenken, dass zeroinput die Zeile als „kein gültiger Einspeisewert" verwirft (`timer.txt enabled but not active`). Selbstbegrenzend und ohne Gedächtnis wie der Rest: Mit steigendem `content` schrumpft der freie Raum, der Überlauf mit ihm, und `ac_%` klettert zurück auf 100. Läuft nichts über (freier Raum größer als der ganze kommende Überschuss), greift Precharge gar nicht — an einem knapp bemessenen, ohnehin leeren Akku ist das der Normalfall.

Die Ausfallsicherung (siehe dort) greift auch hier: Eine reine `ac_%`-Drosselung ohne gleichzeitiges Entlade-Limit löst ebenfalls die 30-Minuten-Alles-erlaubt-Zeile aus.

---

## Wallbox (optional, Standard aus)

Mit `wallbox_enabled: true` schaltet `dirt_shift` das Relais einer E-Auto-Wallbox über ein Tasmota-Gerät — ein von der bisherigen `basic_load`-Ausklammerung (siehe Entladung nach Zone) unabhängiger, zusätzlicher Mechanismus.

**Keine Anschluss- oder Ladezustandserkennung.** Ohne Rückmeldung zum Ladezustand des Fahrzeugs (kein SoC) lässt sich kein sinnvolles Tagesziel bilden — `dirt_shift` versucht das erst gar nicht. Das Relais wird einfach geschlossen gehalten, wann immer die Bedingungen erfüllt sind; ist kein Fahrzeug angeschlossen, passiert schlicht nichts (kein Ladestrom, kein Schaden).

**Einschalten und Ausschalten sind keine Gegenteile voneinander** — je nachdem, ob `dirt_shift` das Relais gerade selbst besitzt (siehe Besitzer-Merker unten), gelten unterschiedliche Kriterien:

### Ein einziges Kriterium, durchgehend geprüft

Anders als man vermuten könnte, gibt es **keine** unterschiedlichen Kriterien fürs Ein- und fürs Ausschalten — dieselbe Formel gilt fortlaufend, bei jedem Lauf neu, unabhängig davon, ob `dirt_shift` das Relais gerade schon besitzt:

```
should_on = Sauberkeits-Bedingung   ODER   Energie-Bedingung   ODER   Spannungs-Bedingung
```

**Sauberkeits-Bedingung** — zwei Grenzwerte, beide müssen zutreffen:
```
Rang von dirt%[jetzt] im Fenster ≤ wallbox_cleanest_pct %   UND
dirt%[jetzt] ≤ wallbox_absolute_max
```
`wallbox_cleanest_pct` (Standard `30`, in Prozent) verlangt, dass die laufende Stunde zu den saubersten so-und-so-viel Prozent des rollierenden Fensters gehört — ein eigener, engerer Maßstab als der allgemeine Rot/Grün-Schnitt, über dasselbe Fenster gebildet.

**Ein Rang, kein Anteil am Median.** `dirt%` ist eine vorzeichenbehaftete Größe: Übersteigt die erneuerbare Erzeugung die Last, wird sie negativ — an einem windigen, sonnigen Tag über viele Stunden hinweg. Ein Prozentsatz *davon* hat keine stabile Bedeutung: Dieselbe Einstellung ließe an einem gleichmäßig dreckigen Tag gar keine Stunde durch und an einem windigen den größten Teil des Fensters. Ein Rang ist allein über die Reihenfolge definiert und damit unempfindlich gegen Vorzeichen, Nullpunkt und Skalierung; die Einstellung lässt exakt den angegebenen Anteil der Stunden zu, unabhängig von der Datenlage. Gleichstände zählen als sauberer, damit identische Stunden gleich behandelt werden. `wallbox_absolute_max` (Standard `50`) verhindert, dass an einem durchgehend dreckigen Tag (hoher Median) die relative Schwelle allein noch eine spürbar dreckige Stunde durchwinkt. Der Median wird über alle 24 Stunden des rollierenden Tages gebildet (siehe CO₂-Intensitätsprofil), nicht nur über die verbleibenden.

**Der Batteriemodus spielt in dieser Bedingung keine Rolle.** Sie beantwortet genau eine Frage — ist jetzt ein guter Zeitpunkt, das Auto aus dem *Netz* zu laden? Ob der Akku dabei etwas verlieren könnte, ist eine andere Frage und wird an anderer Stelle beantwortet (siehe „Akkuschutz auf der Batterieseite" unten). Beides in einer Bedingung zu vermengen würde den Schutz verkehrt herum verdrahten: `free` ist der einzige Modus, in dem der Akku unbegrenzt entladen werden darf und eine Wallbox ihn also leeren könnte — `stop` dagegen sperrt jede Entladung. Eine Kopplung an den Modus gäbe die Wallbox also gerade dort frei, wo der Akku schutzlos ist, und verböte sie dort, wo er ohnehin nicht angetastet werden kann.

**Energie-Bedingung** — unabhängig von der Sauberkeit:
```
content − headroom  >  reserve_pct × max(red_window_demand, upcoming_red_demand)
margin   = wallbox_typical_power × 0,25 h   (Bezug einer Viertelstunde)
headroom = 2 × margin beim Einschalten  |  1 × margin solange dieser Pfad selbst hält
```
Die Bedarfsschätzung ist die der Rot-Reserve (siehe dort), der größere Wert aus `red_window_demand` und `upcoming_red_demand` — **ohne** den dortigen Abzug des erwarteten Überschusses. Das ist Absicht und macht die Wallbox strenger als den Akku: Eine laufende Wallbox ist genau der Verbraucher, der diesen Überschuss aufzehren würde, bevor er je im Akku ankommt; für sie darf er also nicht als verfügbar unterstellt werden. Die Asymmetrie läuft nur in diese Richtung — die Wallbox strenger, nie laxer. Beide werden gebraucht, und keine darf allein stehen. `red_window_demand` liefert `0`, solange `now` in einer angenommenen Überschussphase liegt — allein damit wäre die Prüfung ausgerechnet dann ein Freibrief, wenn sie am wichtigsten ist, denn eine laufende Wallbox kann mehr ziehen, als der reale verbleibende Überschuss hergibt. `upcoming_red_demand` zeigt dort nie `0`, bricht aber an der ersten grünen Stunde ab: Eine kurze grüne Lücke vor Sonnenaufgang stutzt sie auf einen Bruchteil dessen zusammen, was bis zur PV-Rückkehr wirklich gebraucht wird — und allein darauf gestützt leert eine Wallbox den Akku über Nacht gegen eine Reserve, die diese Stunden nie gesehen hat. **Die Wallbox darf an keinem lascheren Maßstab hängen als der Akku, aus dem sie zieht.**

Die Reserve wird bewusst **direkt gegen den aktuellen `content`** verglichen, ohne einen erwarteten künftigen Überschuss dazuzurechnen — genau der Überschuss, auf den man sich sonst verlassen würde, ist ja derjenige, den die Wallbox selbst aufzehren könnte.

**Warum zwei Schwellen (Hysterese der Energie-Bedingung):** `margin` ist genau der Bezug **eines** Laufs. Wird auf weniger als das eingeschaltet, hebt die Einschaltung sich beim nächsten Lauf durch ihre eigene Folge wieder auf — die Wallbox flattert im Viertelstundentakt ein und aus, ohne je nennenswert zu laden. Der Faktor `2` beim Einschalten verlangt deshalb mindestens einen Lauf Puffer, bevor die Prüfung kippen kann. Er ist als Faktor auf `margin` formuliert statt als fester Wh-Wert, damit er mit der tatsächlichen Wallbox-Leistung mitskaliert: Die Flatterperiode ist das Laufintervall, das relevante Maß also Laufzeit-in-Ladung, nicht eine absolute Energiemenge. Beide Schwellen sind benannte Konstanten im Code, keine Konfigurationsoption.

Wie bei der Spannungs-Bedingung wird dieser Hysterese-Zustand **getrennt vom Besitzer-Merker** geführt (Schlüssel `energy_on`, siehe unten) — die niedrigere Halteschwelle darf kein Relais stützen, das ein anderer Pfad eingeschaltet hat.

**Spannungs-Bedingung** — der volle Akku:
```
min(Vbat über die letzten 15 min)  ≥  Schwelle × cell_count
Schwelle = 3,375 V/Zelle beim Einschalten  |  3,25 V/Zelle solange dieser Pfad selbst hält
```
Ist die Batteriespannung über die vergangene Viertelstunde **nicht** unter die Schwelle gefallen, gilt der Akku als voll und weiterhin von PV gespeist — Überschuss, der sonst ungenutzt bliebe, kann ins Auto. Die Prüfung auf das **Minimum** eines Fensters statt auf den Momentanwert testet dabei zwei Dinge zugleich: hoher Ladestand *und* keine nennenswerte Entladelast in dieser Zeit. Ein einzelner Messwert könnte zufällig mitten in einer kurzen Lastspitze abgelesen werden.

Beide Schwellen sind **pro Zelle** definiert und werden mit `cell_count` skaliert, genau wie der Leer-Anker der Energieinhalts-Rechnung (siehe Datenquellen) — bei 16S also 54,0 V bzw. 52,0 V. Sie sind benannte Konstanten im Code, keine Konfigurationsoption.

**Warum zwei Schwellen (Hysterese der Spannungs-Bedingung):** Die Spannung bleibt ja auch deshalb hoch, *weil* die Wallbox aus ist. Schaltet sie ein, zieht sie Last, die Spannung sackt — und ohne die tiefere Halteschwelle würde genau die Bedingung wegfallen, die eben noch eingeschaltet hat: ein Flattern im Viertelstundentakt. Einmal eingeschaltet hält der Pfad deshalb bis 3,25 V/Zelle; erst danach braucht es wieder die volle Erholung auf 3,375 V/Zelle.

Dieser Hysterese-Zustand wird **getrennt vom Besitzer-Merker** geführt (Schlüssel `voltage_on` in derselben Datei, siehe unten). Andernfalls würde die tiefere Halteschwelle auch für ein Relais gelten, das die Sauberkeits-Bedingung bei sauberem Netz eingeschaltet hat — es bliebe dann über die Spannung weiter an, lange nachdem das Netz dreckig geworden ist.

**Die Spannungs-Bedingung wiederholt nicht einfach die Energie-Bedingung.** Gerade wenn eine lange dreckige Strecke bevorsteht, ist die Bedarfsschätzung und damit die wallbox-eigene Reserve groß — die Energie-Bedingung bleibt dann unerfüllt, obwohl der Akku physisch voll ist und keine Wh mehr aufnehmen kann. Genau diese Lücke schließt die Spannungsmessung. Sie ist zudem ein **direkter Messwert** und erbt damit keine Drift der `content`-Schätzung, die seit dem letzten Leer-Anker integriert wird. Ist die Spannung nicht abrufbar, gilt dieser Pfad schlicht als nicht erfüllt — der Lauf selbst läuft normal weiter.

### Akkuschutz auf der Batterieseite

Die Wallbox-Entscheidung fällt **vor** dem Schreiben der Timer-Zeile, und ihr Ergebnis fließt in den Entlademodus ein:

```
Wallbox läuft  UND  Modus wäre free  UND  Inhalt ≤ Reserve  UND  weder Energie- noch Spannungs-Bedingung  →  Modus wird limit
```

`free` gibt den Akku unbegrenzt frei — der einzige Modus, in dem eine laufende Wallbox ihn leeren kann. Herabgestuft wird aber nur, wenn das Auto den Akku tatsächlich **unter die Reserve** zöge, also `Inhalt ≤ Reserve` gilt: Deckt der Inhalt die Reserve bereits, darf der Überschuss darüber ans Auto, der Akku bleibt am Ende des Slots über seiner Reserve. In grüner Zone bedeutet `free` ohnehin `Inhalt > Reserve`, sodass die Herabstufung praktisch nur in der einen roten Stunde greift, in der `free` unabhängig vom Inhalt erzwungen ist (die dreckigste Stunde) — dort soll die erzwungene Entladung nicht ans Auto gehen. Trifft sie zu, wird die Entladung auf den Hausanteil dieses Slots gedeckelt (Rate und Slot-Budget wie bei `limit`, siehe Ausgabe: timer.txt), und die Wallbox-Last landet im Netz. Ohne die Reserve-Bedingung stoppte die Herabstufung an einem sonnigen Tag mit Reserve 0 die Entladung ganz, obwohl der Akku sich ohnehin wieder füllt und nichts zu schützen war. `stop` und `limit` schützen den Akku bereits und bleiben unverändert.

Die drei Bedingungen benennen damit jeweils die Energiequelle, aus der das Auto lädt: Sauberkeit → Netz, Energie → Akkuüberschuss, Spannung → PV-Überschuss. Und der Akkuschutz sitzt dort, wo die Batterieentladung tatsächlich gesteuert wird, statt als Nebenbedingung in der Wallbox-Logik.

Die `-v`-Ausgabe kennzeichnet die Herabstufung in der Modus-Zeile mit `[free->limit: wallbox on grid terms]`.

**Ladebedarf ist nicht definiert.** Wann und wie viel geladen wird, ist unbekannt; die Logik verfolgt kein Tagesziel und keine Frist. An einem Tag, an dem die saubersten Stunden allesamt dann liegen, wenn kein Auto angeschlossen ist, wird nicht geladen — das ist die Konsequenz einer rein gelegenheitsgetriebenen Steuerung und beabsichtigt.

**Warum alle Bedingungen durchgehend gelten, nicht nur beim Einschalten:** Weil die Pfade gleichberechtigt und gleichzeitig laufen sollen — eine Stunde, die sauber genug ist, darf laden, auch wenn die Energie-Bedingung (noch) nicht erfüllt ist; ein komfortabel gefüllter Akku darf laden, auch wenn es gerade dreckig ist; und ein voller Akku darf seinen sonst verschenkten Überschuss abgeben, auch wenn beide anderen Bedingungen dagegen sprechen. Ein Relais, das `dirt_shift` selbst eingeschaltet hat, bleibt deshalb an, solange **mindestens eine** der drei Bedingungen weiter zutrifft, und schaltet erst ab, wenn **alle gleichzeitig** nicht mehr erfüllt sind.

**Zur Spannungs-Bedingung und `limit`/`stop`:** Da die Energie-Bedingung dieselbe Reserve wie der Hauptmodus verwendet, kann sie ein Relais nicht mehr am Laufen halten, während der Akku selbst schon geschützt wird. Für die Spannungs-Bedingung gilt das nicht, dort aber bewusst: Ein voller Akku soll seinen Überschuss auch dann abgeben dürfen, wenn `limit`/`stop` gilt — das ist gerade der Fall, den sie abdecken soll (siehe unten). Ihre eigene Halteschwelle begrenzt das: Sackt die Spannung unter 3,25 V/Zelle, fällt der Pfad weg.

Eine Randbedingung, die aus der Sauberkeits-Bedingung folgt: In der roten Zone gilt `free` auch an der **dreckigsten Stunde selbst** (dort soll die Reserve bewusst verbraucht werden), unabhängig vom Akkuinhalt. In einer durchgehend „tiefroten" Nacht, in der keine Stunde die beiden Sauberkeits-Schwellen von sich aus erreicht, kann die Wallbox deshalb ausnahmsweise doch in der roten dreckigsten Stunde zu laden beginnen, sofern diese selbst noch unter `wallbox_absolute_max` bleibt — eine bewusst akzeptierte Randbedingung, keine Lücke.

**Precharge (siehe dort) hat keinen Einfluss auf die Wallbox** — beide Mechanismen laufen vollständig unabhängig nebeneinander.

### Besitzer-Merker

`dirt_shift` darf das Relais nur abschalten, wenn es selbst der Grund für den aktuellen Ein-Zustand ist — eine manuelle Aktivierung wird nie rückgängig gemacht, egal wie dreckig es wird. Dafür hält eine kleine persistente Datei (`dirt_wallbox_marker.json`) fest, ob `dirt_shift` das Relais aktuell eingeschaltet hat:

- **Einschalten** ist immer unproblematisch (No-op, falls schon an) — der Marker wird auf `true` gesetzt.
- **Ausschalten** geschieht nur, wenn der Marker `true` ist.
- Der **erste Lauf ohne Historie**: Ist das Relais dabei schon an, gilt das vorsorglich als fremd (Marker `false`) — `dirt_shift` fasst es nicht an.

**Konsequenz für manuelles Eingreifen:** Schaltet jemand das Relais manuell ein, während es gerade dreckig ist, bleibt es unangetastet — auch über mehrere Läufe hinweg. Sobald aber die **nächste** Stunde eintritt, die die Bedingungen erfüllt, „adoptiert" `dirt_shift` das Relais stillschweigend (setzt den Marker auf `true`, ohne real etwas zu ändern — es lief ja schon). Ab diesem Moment gilt es wieder als eigenes und kann bei der nächsten dreckigen Stunde regulär abgeschaltet werden. Der manuelle Schutz gilt also nur für die gerade laufende dreckige Phase, nicht dauerhaft.

Diese Erkennung hat eine bekannte Grenze: Schaltet jemand manuell **während** der Marker schon `true` ist, kann `dirt_shift` das nicht von einer eigenen Aktion unterscheiden — der Tasmota-Status zeigt nur den Zustand, nicht den Verursacher. Das bleibt eine akzeptierte Einschränkung, keine Lücke, die noch geschlossen werden müsste.

Dieselbe Datei führt **zwei weitere, unabhängige Schlüssel**: `energy_on` und `voltage_on`, die Hysterese-Zustände der Energie- bzw. der Spannungs-Bedingung (siehe oben). Sie sagen jeweils nur aus, ob dieser eine Pfad gerade selbst hält, und werden bei **jedem** Lauf neu geschrieben — auch dann, wenn sich am Relais nichts ändert. Alle drei Schlüssel werden unabhängig voneinander geschrieben, ohne sich gegenseitig zu überschreiben. Fehlt einer der beiden Hysterese-Schlüssel (etwa in einer Merker-Datei aus einem früheren Lauf), gilt er als `false`, der betroffene Pfad startet dann also auf der strengeren Einschaltschwelle.

### Schalten mit Verifikation

Geschaltet wird per direktem Tasmota-`Power{output}`-Befehl (HTTP GET), **kein** Tasmota-Timer — anders als bei `tib_zero_tas`, dessen preisgesteuerte Planung im Voraus feststeht. Hier muss jeder Lauf reagieren können (Zonenwechsel, Modus-Wechsel), das passt nur zu einem direkten Befehl im laufenden Lauf.

Der Befehl allein reicht nicht: Tasmota nimmt einen HTTP-Befehl auch entgegen, wenn das Gerät danach nicht erreichbar bleibt oder die Schaltung real nicht greift. Deshalb wird nach jedem Setzen der tatsächliche Relais-Status erneut abgefragt (`Power{output}` ohne Wert) — bis zu **3 Versuche**, **30 Sekunden** Abstand. Erst nach einem **verifizierten** Zustandswechsel wird der Besitzer-Marker aktualisiert; schlagen alle 3 Versuche fehl, bleibt der Marker unverändert.

**Läuft ganz am Ende von `main()`**, nach `write_timer` und der Dreckigkeits-Meldung — bewusst so platziert, damit ein hängendes oder unerreichbares Tasmota-Gerät (im ungünstigsten Fall bis zu ~90 Sekunden durch die Retry-Wartezeiten) nie das Schreiben von `timer.txt` verzögert. Weder Erfolg noch Fehlschlag beeinflussen den Rückgabewert von `dirt_shift` — beides wird ausschließlich unter `-v` gemeldet, exakt wie beim Dreckigkeitswert-Export.

---

## Ausfallsicherung

`dirt_shift` ist optional und darf den Normalbetrieb von zeroinput nie blockieren. Begrenzt oder stoppt ein Lauf die Entladung, oder deckelt er `ac_%` unter 100 (siehe Precharge), schreibt er zusätzlich eine „Alles-erlaubt"-Zeile (`100 100 -1`) 30 Minuten später. Läuft das Skript weiter, wird die Begrenzung alle 15 Minuten erneuert; fällt es aus (cron-Ausfall, volkszähler nicht erreichbar, Absturz), hebt sich die Begrenzung nach 30 Minuten von selbst auf, und zeroinput entlädt wieder frei, als gäbe es `dirt_shift` nicht. Im freien Modus ohne Precharge-Drosselung ist ohnehin alles erlaubt, dort genügt die eine Zeile.

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
- **entlade-W** — Entlade-Deckel; `100` (Prozent) = kein Limit, `limit_discharge_rate` (Watt, Konfiguration) = gedeckelt, `000` = kein Akku-Entladen (Stopp).
- **ac-%** — Wechselrichter-Durchleitung, `100` (pvpt garantiert), außer während einer aktiven Precharge-Drosselung (siehe dort): dann für jede qualifizierende sehr saubere grüne Stunde ein stetiger Wert zwischen `0` und `100`.
- **energie-Wh** — Energiebudget; im gedeckelten Modus `¼ × (min(100 %, reserve_pct) × basic_load − gemessene PV)` dieser Stunde, mindestens 0 (die tatsächliche Wh-Zuteilung für genau die laufende Viertelstunde), sonst `-1` = echt unbegrenzt (ein Sentinel, den `zeroinput.py`s `discharge_times.update()` erkennt und den Budget-Check dafür komplett überspringt), `000` = kein Budget (Stopp).

Die drei Modi sind also: `100 100 -1` (kein Limit), `<limit_discharge_rate> 100 <¼×(min(100 %,reserve_pct)×basic_load−PV_ist)>` (gedeckelt), `000 100 000` (Stopp). Im gedeckelten Modus wirken zwei Deckel gleichzeitig und schließen sich gegenseitig die Lücke: `limit_discharge_rate` begrenzt nur die Momentanleistung — eine dauerhaft erhöhte, aber unterhalb dieser Schwelle bleibende Last liefe daran vorbei unbegrenzt weiter. Das Wh-Feld begrenzt dagegen die insgesamt entnommene Energiemenge dieser Viertelstunde auf `¼ × (min(100 %, reserve_pct) × basic_load − gemessene PV)` — `reserve_pct` (Standard 90 %) skaliert dabei denselben Anteil des Bedarfs, den auch die Reserve-Berechnung ansetzt, statt nicht-priorisierten Stunden den vollen Bedarf zu erlauben; die PV wird davon unabhängig in voller Höhe abgezogen, weil `pvpt` sie ohnehin schon direkt durchleitet und sie nicht zusätzlich aus dem Akku kommen muss. Eine Stunde lang genug (Watt) und hoch genug (Wh) zu ziehen, umgeht daher keinen der beiden Deckel. `limit_discharge_rate` ist eine anlagenspezifische Konfiguration; das Wh-Budget ist fest und braucht keinen eigenen Konfigurationseintrag, da es sich direkt aus `basic_load`, `reserve_pct` und der gemessenen PV ergibt.

**Hier zählt die gemessene PV, nicht die Prognose.** Das Budget regelt die Viertelstunde, die *gerade jetzt* läuft, und dafür ist ein Stundenmittel zu grob: An den Rampenstunden am Tagesrand — die zugleich regelmäßig die roten Grenzstunden sind — kann das Mittel noch nahe am `basic_load` liegen, während die reale Erzeugung längst eingebrochen ist. Das Budget fiele dann auf wenige Wh, ausgerechnet in der Viertelstunde, in der der Akku das Haus tragen sollte; der Fehlbetrag käme aus dem Netz, in einer roten Stunde. `dirt_shift` fragt deshalb den `PV`-Kanal für die letzten 15 Minuten ab und rechnet mit diesem Ist-Wert; die Prognose bleibt Rückfallebene, wenn die Messung nicht verfügbar ist. Für alles Zukünftige (Reserve, dreckigste Stunde, Precharge) bleibt weiterhin ausschließlich die Prognose zuständig.

**`reserve_pct` wird hier — und nur hier — bei 100 % gekappt.** Bei der Reserve selbst ist ein Wert über 100 sinnvoll und macht das System vorsichtiger: Es hält mehr zurück als den errechneten Bedarf. Für einen Deckel würde derselbe Wert die Absicht umkehren — über 100 % läge das Budget über dem tatsächlichen Netto-Bedarf der Stunde, der Deckel würde also ausgerechnet dann nichts mehr begrenzen, wenn er gebraucht wird. Durch die Kappung wirkt ein höheres `reserve_pct` auf beiden Seiten durchgehend konservativer.

Ein Handlauf setzt `limit_discharge_rate` sehr hoch, um den Leistungsdeckel de facto zu deaktivieren und ausschließlich über das Energiebudget zu begrenzen.

Da `dirt_shift` jede Viertelstunde neu schreibt und sich damit die letzte Zeile der Timer-Datei bei jedem Lauf ändert, reicht ein einzelnes Viertelstunden-Budget je Lauf aus: `zeroinput.py`s Energiezähler wird genau dann zurückgesetzt (siehe `discharge_times.update()`), sodass die nächste Viertelstunde ihr eigenes frisches Budget bekommt. Nur wenn `dirt_shift` selbst ausfällt, bleibt die letzte geschriebene Zeile stehen und der Zähler läuft nicht mehr leer — dafür sorgt dann die Ausfallsicherung (siehe dort), die nach 30 Minuten ohnehin auf „alles erlaubt" umschaltet.

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
- `reserve_pct` — Prozent des `basic_load`-Bedarfs über das rote Fenster, der reserviert wird (Standard 90); skaliert außerdem das Viertelstunden-Energiebudget nicht-priorisierter roter Stunden (siehe Ausgabe: timer.txt, dort bei 100 % gekappt) sowie die wallbox-eigene Reserveschätzung (siehe Wallbox). Werte über 100 sind zulässig und machen die Reserve selbst vorsichtiger
- `limit_discharge_rate` — Watt-Deckel auf die Entladeleistung in einer nicht-priorisierten roten Stunde (Standard 3000); anlagenspezifisch, an die eigene gewöhnliche Spitzenlast anzupassen. Gilt zusammen mit dem festen Viertelstunden-Energiebudget (`¼ × (min(100 %, reserve_pct) × basic_load − gemessene PV)`, mindestens 0, keine eigene Konfiguration). Sehr hoch gesetzt wirkt der Leistungsdeckel de facto nicht mehr, nur das Energiebudget bleibt wirksam.
- `latitude`, `longitude` — Standort der Anlage (Dezimalgrad) für das Klarhimmel-Modell und die Strahlungsprognose; Standard ~Mitte Deutschland (51,0 / 10,0)
- `PV_to_bat_efficiency`, `bat_to_AC_efficiency` — Wirkungsgrade für die Rekonstruktion des Energieinhalts
- `max_days_empty_battery` — wie viele Tage rückwärts nach einem „leer"-Zustand gesucht wird
- `disable_zeroinput_timer` — auf `true` rechnet und gibt aus, ohne die Timer-Datei zu schreiben (Trockenlauf)
- `precharge_enabled` — optional, Standard `false`. Aktiviert den Precharge-Pfad (siehe dort), der `ac_%` in den sehr sauberen grünen Stunden unter 100 deckeln kann, um PV-Überschuss gezielt in den Akku statt ins Haus zu lenken.
- `precharge_cleanest_pct` — Rang-Schwelle für Precharge in Prozent (Standard 25): nur grüne Stunden unter den saubersten so viel Prozent des Fensters werden gedrosselt.
- `battery_capacity_wh` — nutzbare Akkukapazität in Wh (Standard 5000). Für Precharge: Umgelenkt wird nur, wenn der erwartete Überschuss den freien Raum (`battery_capacity_wh − content`) übersteigt, also sonst verloren ginge. Dient außerdem der Füllstand-Anzeige (`content ... Wh (SoC%)`) in der `-v`-Ausgabe.
- `wallbox_enabled` — optional, Standard `false`. Aktiviert die Wallbox-Steuerung (siehe dort).
- `wallbox_ip` — IP-Adresse des Tasmota-Geräts, das das Wallbox-Relais schaltet.
- `wallbox_output` — Tasmota-Relais-/Output-Nummer (Standard `"1"`).
- `wallbox_cleanest_pct` — die Wallbox darf nur laufen, wenn die laufende Stunde zu den saubersten so viel Prozent des rollierenden Fensters gehört (Standard `30`).
- `wallbox_absolute_max` — absolute Sauberkeits-Schwelle für die Wallbox in `dirt%` (Standard `50`).
- `wallbox_typical_power` — typische Wallbox-Ladeleistung in Watt (Standard `2000`), nur für die Viertelstunden-Vorausschau der Energie-Bedingung genutzt (siehe Wallbox) — keine gemessene Größe, eine Sicherheitsmarge.

Das Viertelstunden-Energiebudget (`¼ × (min(100 %, reserve_pct) × basic_load − gemessene PV)`, siehe Ausgabe: timer.txt) ist **keine eigene** Konfigurationsoption — es ergibt sich direkt aus `basic_load`, `reserve_pct` und der gemessenen PV-Leistung und braucht deshalb keinen eigenen Wert in `dirt_shift.conf`.

### Fehlerverhalten

Fehlende Daten führen so weit wie möglich **nicht** zum Abbruch, sondern lassen nur den betroffenen Teil entfallen. Fehlt der Akkuenergieinhalt, wird die Batterie freigegeben (die Reserve-Logik hängt an ihm), aber die Wallbox über ihre redundanten Pfade weiter gesteuert; sind dort alle Pfade mangels Daten nicht entscheidbar, bleibt das Relais unverändert. Fehlt SMARD, treiben die modellierten Notzonen den ganzen Lauf. Nur bei einem wirklich harten Fehler (keine vollständigen Tage für die Mittelung, oder weder SMARD noch Wetter-Modell verfügbar) bricht `dirt_shift` ab, schreibt aber zuvor — sofern der Timer-Pfad bekannt ist — eine „Alles-erlaubt"-Zeile, damit zeroinput nicht durch eine veraltete oder fehlende Begrenzung blockiert wird; das Wallbox-Relais bleibt dabei unangetastet:

```
2026-07-09 00:00:00 100 100 -1
```

(volle Entladung, volle Durchleitung, praktisch unbegrenztes Energiebudget, mit dem aktuellen Datum). Ist nicht einmal die `zeroinput.conf` lesbar (Timer-Pfad unbekannt), bleibt nur der Abbruch mit Fehlermeldung.

---

## Aufrufoptionen

- `-v` — ausführliche Konsolenausgabe
- `-html` — HTML-Kopf/-Fuß um die Ausgabe
- `-debug` — mehr Ausgabe (impliziert `-v`)
- `-avgnew` — erzwingt eine frische Abfrage statt der Caches: `basic_load`-Mittel, PV-Referenzkurve, Strahlungsprognose und SMARD-Zonen
