# dirt_shift – Funktionsspezifikation
*v2.1*

Diese Spezifikation beschreibt, **was** `dirt_shift` tut; sie gilt für die Version, die das Skript als `VERSION` führt. Die ausführliche Begründung einzelner Regeln steht im Docstring der jeweils genannten Funktion in `dirt_shift.py`.

## Zweck

`dirt_shift` verschiebt die Batterieentladung in die Netzstunden mit der höchsten CO₂-Intensität — abends, nachts und früh morgens, wenn kaum PV im Netz ist. Die CO₂-Intensität ist eine Eigenschaft des **Netzes**; die eigene Anlage (PV, Verbrauch, Akkuinhalt) bestimmt nur die verfügbare und benötigte Energiemenge.

Die Batterie steuert `dirt_shift` nicht selbst, sondern schreibt die `timer.txt`, die zeroinput für die Entladesteuerung liest. Nur die optionale Wallbox schaltet es direkt. Die direkte PV-Durchleitung ins Haus (`pvpt`) bleibt immer unangetastet — einzige Ausnahme ist der optionale Precharge-Pfad. Begrenzt wird ausschließlich die **Batterieentladung**.

---

## Datenquellen

- **[volkszaehler.org](https://volkszaehler.org/)** (HTTP-API, `vz_host_port`, Zeitlimit `VZ_TIMEOUT_S` = 30 s je Abfrage):
  - **basic_load** — Hausverbrauch je Stunde, `Import + |Inverter| − Auto`, als gewichtetes Mittel über `average_days` Tage (`get_average`, stündlich in `dirt_avg_cache.json`). Jeder Tag ist ein 24-Stunden-Abschnitt, der eine Stunde vor der aktuellen endet (`VZ_WINDOW_LAG_H`), weil volkszaehler.org die gerade abgeschlossene Stunde noch nicht liefert; gezählt werden nur vollständige Abschnitte. Gleiches gilt für die PV-Referenzkurve. Die Formel ist an die eigene Anlage anzupassen: abgezogen werden nur planbare Lasten, die nicht aus der Reserve gedeckt werden sollen (etwa eine gesondert erfasste Wallbox).
  - **Energieinhalt** — rekonstruiert aus PV- und Inverter-Energie seit dem letzten Leerzustand, mit `PV_to_bat_efficiency` und `bat_to_AC_efficiency` (`get_vz_bat_cap`, bei jedem Lauf frisch).
  - **PV-Erzeugung** — Grundlage der PV-Referenzkurve; der Mittelwert der letzten 15 Minuten (`recent_pv_power`) dient dem Energiebudget und der Spannungs-Bedingung der Wallbox.
  - **Batteriespannung** (`Vbat`) — Leer-Anker des Energieinhalts und Spannungs-Bedingung der Wallbox.
- **Energy-Charts-Stromampel** (Fraunhofer ISE, Lizenz CC BY 4.0) — prognostizierter Anteil aller Erneuerbaren an der Last in Viertelstunden, erste Quelle der Zonen (`get_ampel_zones`).
- **SMARD** (Bundesnetzagentur) — Day-Ahead-Prognosen für Wind+Solar-Erzeugung und Stromverbrauch, zweite Quelle der Zonen (`get_smard_zones`).
- **Open-Meteo** — Strahlungs- und Windprognose (`shortwave_radiation`, `wind_speed_100m`) in einem Aufruf.

**Leer-Anker.** Als Nullpunkt der Integration zählt nur ein **anhaltender** Leerzustand: Spannung ≤ 3,0625 V/Zelle (49 V bei 16 Zellen, skaliert mit `cell_count`) für mindestens `EMPTY_MIN_DURATION_S` (120 s); ein kurzer Einbruch unter Last setzt keinen Anker (`_empty_anchor`). Spannungswerte unter `IMPLAUSIBLE_V_PER_CELL` (2,5 V/Zelle, 40 V bei 16S) sind physikalisch unmöglich und werden beim Einlesen verworfen. Beide Kanäle werden vorzeichenunabhängig verarbeitet (Betrag, Richtung fest).

**Warnung bei negativem Inhalt.** Fällt der Inhalt unter −`NEG_CONTENT_WARN_FRACTION` (5 %) der `battery_capacity_wh`, meldet `dirt_shift` das immer, auch ohne `-v`. `_neg_content_diagnosis` unterscheidet anhand derselben Bilanz ohne jeden Wirkungsgrad: Ist auch sie negativ, sind die Kanaldaten falsch (typisch: ein Wechselrichter, der Leistung anfordert, aber nicht liefert — ausgelöster FI, Sicherung, Defekt); sonst könnten die Wirkungsgrade zu niedrig eingestellt sein.

---

## CO₂-Intensitätsprofil

Aus der Netzprognose bildet `dirt_shift` je Stunde das Verhältnis Erneuerbare/Last und daraus den **Dreckigkeitswert** `dirt% = (1 − Verhältnis) × 100` — negativ, wenn die Erneuerbaren die Last übersteigen.

**Quelle** (`get_grid_zones`): zuerst die Stromampel, deren Viertelstundenwerte je Stunde gemittelt werden (`_ampel_days`); meldet sie `substitute: true` (aus historischen Daten geschätzt), gilt sie als nicht verfügbar. Sonst SMARD. Ein Lauf verwendet für das ganze Fenster genau **eine** Quelle: Die Stromampel zählt alle Erneuerbaren, SMARD nur Wind und Solar, die Werte liegen also auf verschiedenen Skalen und dürfen im Median-Schnitt nicht gemischt werden. Ein Tag gilt ab `GRID_MIN_HOURS` (20) Stunden als abgedeckt. Die verwendete Quelle steht in den Cache-Zeilen und über der Debug-Tabelle.

Heute und morgen werden zu einem **rollierenden 24-Stunden-Fenster** ab der aktuellen Stunde zusammengesetzt. Über dieses Fenster wird **einmal** der Median gebildet (`_cut_zones`):

- **rot** — Verhältnis unter dem Median (dreckigere Hälfte)
- **grün** — Verhältnis auf oder über dem Median

Der Median teilt immer etwa hälftig, auch an durchgehend sauberen oder dreckigen Tagen. Geschnitten wird über das Fenster, nicht je Kalendertag, damit Stunden über Mitternacht hinweg vergleichbar bleiben.

**Lückenfüllung:**
- Fehlt die Verbrauchsprognose eines Tages, wird das Profil des letzten verfügbaren Tages verwendet, bis zu sieben Tage zurück (`_smard_load_series`); fehlt die Erzeugungsprognose, entfällt der Tag.
- Sind Stunden von morgen noch nicht veröffentlicht, gelten dort die heutigen Werte derselben Quelle; morgen wird nicht aus der anderen Quelle ergänzt. Beide Quellen veröffentlichen den Folgetag in der Regel ab etwa 19 Uhr.
- Abgerufen wird stündlich (`dirt_grid_cache.json`, mit der Quelle); ein Fehlversuch wird erst zur nächsten Stunde wiederholt.

**Ausfall.** Liefern beide Quellen nichts, darf der Cache genau **einen Tag** überbrücken, aber nur, wenn die gestern geholten Daten tatsächlich eine Prognose für heute enthalten (`_substitute_day`); die Werte von gestern selbst werden nie als heute ausgegeben. Danach greift ein **Notmodus** (`emergency_zones`): Aus Solar- und Windprognose und einem festen, normierten Profil der **bundesweiten** Netzlast wird ein relativer Dreckigkeitsverlauf modelliert und wie gewohnt geschnitten. Er ist grob, bezieht aber den Wind ein. Der Notmodus wird in jeder Ausgabe deutlich gekennzeichnet, nicht zwischengespeichert und **nicht** nach volkszaehler.org exportiert. Ist auch das Wetter nicht abrufbar, bricht `dirt_shift` ab (siehe Fehlerverhalten).

---

## PV-Prognose

**Referenzkurve** (`get_pv_curve`): je Stunde das 95. Perzentil (`PV_CURVE_PERCENTILE`) der gemessenen Erzeugung der letzten 14 Tage (`PV_CURVE_DAYS`). Sie bildet Ausrichtung und Verschattung der eigenen Anlage ohne Konfiguration ab. Neuberechnung einmal täglich ab 4 Uhr (`PV_CURVE_REFRESH_HOUR`), Cache `dirt_pv_curve_cache.json`.

**Wetterskalierung** (`scaled_pv_curve`):

```
expected_pv = Referenzwert × min(1, Strahlungsprognose / Klarhimmel-GHI)
```

Das Klarhimmel-GHI folgt dem Haurwitz-Modell aus dem Sonnenstand (`clear_sky_ghi`). Strahlung und Wind werden als 48-Stunden-Reihe stündlich abgerufen (`dirt_weather_cache.json`) und wie die Zonen zu einem rollierenden Fenster zusammengesetzt; fehlende Morgen-Werte fallen auf heute zurück. Ohne Strahlungsprognose gilt die unskalierte Referenzkurve, ohne Referenzkurve ein Null-PV-Tag.

---

## Rot-Reserve und dreckigste Stunde

Die **Reserve** ist die Energie, die für die kommenden roten Stunden zurückgehalten wird:

```
Reserve = reserve_pct × max(red_window_demand,
                            max(0, upcoming_red_demand − surplus_before_next_red))
```

- `red_window_demand` — Netto-Bedarf (`basic_load − expected_pv`) aller roten Stunden von jetzt bis zur ersten Überschussstunde; an einem trüben Tag ohne Überschuss alle roten Stunden des Fensters.
- `upcoming_red_demand` — Netto-Bedarf des nächsten zusammenhängenden roten Blocks, auch über eine laufende Überschussphase hinweg.
- `surplus_before_next_red` — erwarteter Überschuss bis zum Beginn dieses Blocks; er füllt den Akku ohnehin und wird deshalb abgezogen. An einem Sonnentag fällt die Reserve so auf 0.

Die beiden Bedarfsschätzungen decken sich gegenseitig ihre blinden Flecken ab; begründet in den jeweiligen Docstrings. `reserve_pct` (Standard 90) darf über 100 liegen und macht die Reserve dann vorsichtiger.

**Dreckigste Stunde** (`dirtiest_hour`): Reicht der Inhalt nicht für die Reserve, wird im selben Fenster die rote Stunde mit dem höchsten `dirt%` bestimmt (bei Gleichstand die früheste). Nur sie wird unbegrenzt bedient; alle anderen roten Stunden werden gedeckelt. Das Fenster wird bei jedem Lauf neu aufgebaut, eine vergangene dreckigste Stunde fällt damit von selbst heraus.

---

## Entladung nach Zone

| Zone | Bedingung | Modus |
|---|---|---|
| grün | Inhalt > Reserve | **frei** |
| grün | Inhalt ≤ Reserve, Inhalt + Überschuss bis zum roten Block > `battery_capacity_wh` | **gedeckelt** (Überlaufschutz) |
| grün | Inhalt ≤ Reserve, roter Block mindestens `STOP_MIN_DIRT_GAP` (10) Punkte dreckiger | **Stopp** |
| grün | Inhalt ≤ Reserve, Abstand kleiner | **gedeckelt** |
| rot | Inhalt ≥ Reserve | **frei** |
| rot | Inhalt < Reserve, laufende Stunde ist die dreckigste | **frei** |
| rot | Inhalt < Reserve, sonst | **gedeckelt** |

- **Überlaufschutz:** Ein Stopp würde den Akku voll stehen lassen, während der Mittagsüberschuss wegen Nulleinspeisung abgeregelt wird. Stattdessen deckt der Akku in dieser Stunde das Haus und schafft Platz. Ohne `battery_capacity_wh` entfällt diese Prüfung.
- **Mindestabstand:** Verglichen wird mit dem Mittelwert der roten Stunden, für die die Reserve gilt (`reserved_red_dirt`). Die Schwelle fängt Prognoseunschärfe ab; ein Rundlaufverlust entsteht beim Verschieben bereits gespeicherter Energie nicht. Fehlt ein `dirt%`-Wert, gilt Stopp.
- **Deckel:** `limit_discharge_rate` (Watt) **und** gleichzeitig ein Energiebudget je Viertelstunde:

```
Budget = ¼ × (min(100 %, reserve_pct) × basic_load − gemessene PV)   (mindestens 0)
```

Das Budget rechnet mit der **gemessenen** PV der letzten 15 Minuten (`recent_pv_power`), weil ein Stundenmittel an den Rampenstunden zu grob ist; `reserve_pct` wird hier bei 100 % gekappt. Beides ist in `write_timer` begründet.

---

## Precharge (optional, Standard aus)

Mit `precharge_enabled: true` kann `dirt_shift` in sauberen Stunden PV, die sonst ins Haus ginge, in den Akku lenken; das Haus bezieht diesen Anteil aus dem Netz. Der Tausch: sauberer Netzbezug in der laufenden Stunde gegen mehr Ladung für den dreckigen roten Block — bezahlt mit dem Rundlaufverlust.

Das hilft nur, solange der Akku bis zum roten Block **nicht** ohnehin voll wird; sonst füllt Umlenken ihn nur früher, und mehr PV wird später abgeregelt:

```
Kapazität 10, Inhalt 5, Überschuss bis Rot 9, roter Bedarf 12
ohne Precharge:        Akku voll, 4 abgeregelt, rot bezieht 2
Precharge lenkt 1 um:  Haus bezieht 1, Akku trotzdem voll, 5 abgeregelt, rot bezieht 2
```

Eine grüne Stunde wird nur gedrosselt, wenn alle drei Bedingungen gelten (`precharge_ac_pct`):

1. **Rang** — sie gehört zu den saubersten `precharge_cleanest_pct` % des Fensters (`_dirt_rank_pct`).
2. **Rundlauf-Abstand** — der rote Block ist um mindestens `precharge_round_trip_gap()` Punkte dreckiger:
   ```
   Mindestabstand = (1 − PV_to_bat_efficiency × bat_to_AC_efficiency) × 100 × round_trip_margin
   ```
   `round_trip_margin` ist die Stellschraube zwischen Kosten und Sauberkeit: 1,0 lenkt um, sobald es netto CO₂ spart; größere Werte verlangen einen deutlicheren Gewinn, ein sehr großer schaltet Precharge praktisch ab; Werte unter 1,0 gelten als 1,0.
3. **Restplatz** — nach dem natürlichen Überschuss bleibt Platz, den der rote Block auch nutzt:
   ```
   Endinhalt = content + surplus_before_next_red
   Nutzbar   = min(battery_capacity_wh − Endinhalt, Bedarf des roten Blocks − Endinhalt)
   ```

`Nutzbar` wird auf die Stunden bis zum roten Block verteilt, die Rang **und** Abstand erfüllen, im Verhältnis zur PV, die sie ins Haus liefern:

```
umlenkbar[h] = min(exp_PV[h], basic_load[h])
Umlenkung    = min(umlenkbar[jetzt], Nutzbar × umlenkbar[jetzt] / Σ umlenkbar)
ac_%         = max(PRECHARGE_AC_FLOOR, round(100 × (exp_PV[jetzt] − Umlenkung) / exp_PV[jetzt]))
```

**Nur echte Daten:** Das Precharge-Fenster reicht von der aktuellen Stunde bis zur letzten Stunde mit echten Netzdaten. Es endet an der ersten Stunde, die nur ein Ersatzwert ist (heutiger Wert für ein noch nicht veröffentlichtes Morgen) oder keinen Wert hat. Rang, Mittelwert und Bedarf des roten Blocks sowie die Verteilung verwenden nur Stunden in diesem Fenster; liegt der rote Block ganz dahinter, bleibt `ac_%` bei 100. Im Notmodus ist Precharge aus. Die Entladesteuerung nutzt weiterhin das volle Fenster.

`PRECHARGE_AC_FLOOR` (20) verhindert, dass zeroinput die Zeile als ungültig verwirft. Precharge greift typischerweise an Tagen mit sauberem Netz, aber wenig eigenem PV-Überschuss, an Sonnentagen nicht. Es beeinflusst die Wallbox nicht.

---

## Wallbox (optional, Standard aus)

Mit `wallbox_enabled: true` schaltet `dirt_shift` das Relais einer Wallbox über ein Tasmota-Gerät. Einen Ladebedarf (Menge, Frist) kennt es nicht; ist kein Auto angeschlossen, passiert beim Einschalten nichts.

Bei jedem Lauf gilt dieselbe Formel, fürs Ein- wie fürs Weiterlaufen (`wallbox_decide`):

```
should_on = Sauberkeits-Bedingung  ODER  Energie-Bedingung  ODER  Spannungs-Bedingung
```

| Bedingung | Prüfung | Lädt aus |
|---|---|---|
| Sauberkeit | Rang der Stunde ≤ `wallbox_cleanest_pct` % **und** `dirt%` ≤ `wallbox_absolute_max` | Netz |
| Energie | `content − headroom > reserve_pct × max(red_window_demand, upcoming_red_demand)` | Akkuüberschuss |
| Spannung | Minimum von Vbat über 15 min ≥ 3,375 V/Zelle **und** gemessene PV > `basic_load` | PV-Überschuss |

- **Sauberkeit** ist ein Rang, kein Anteil am Median, weil `dirt%` vorzeichenbehaftet ist (`_dirt_rank_pct`). Der Batteriemodus spielt dabei keine Rolle.
- **Energie** rechnet bewusst **ohne** Überschussabzug und ist damit strenger als die Hauptreserve: Eine laufende Wallbox würde genau diesen Überschuss aufzehren. `headroom` ist beim Einschalten 2 × `margin`, beim Halten 1 × (`margin` = `wallbox_typical_power` × ¼ h), gegen Flattern (`wallbox_energy_ok`).
- **Spannung** gilt nur bei tatsächlichem PV-Überschuss (gemessene PV der letzten 15 min über dem Hausverbrauch der Stunde): Ohne Überschuss wird nichts abgeregelt, ein voller Akku im Plateau hält die Wallbox also weder nachts noch an trüben Nachmittagen. Die Hysterese ist schmal — Einschalten ab 3,375, Halten bis 3,35 V/Zelle —, damit das Halteband über dem LiFePO₄-Plateau bleibt und das Minimum über 15 min eine echte Entladelast weiterhin erkennt (`wallbox_voltage_ok`).

**Fehlende Daten:** Fehlt der Eingang eines Pfads (Akkuinhalt, Spannung), fällt nur dieser Pfad weg; die übrigen entscheiden weiter. Ist kein Pfad entscheidbar, bleibt das Relais unverändert.

### Akkuschutz auf der Batterieseite

Die Wallbox-Entscheidung fällt **vor** dem Schreiben der Timer-Zeile und fließt in den Modus ein:

```
Wallbox läuft  UND  Modus wäre frei  UND  Inhalt ≤ Reserve  UND  weder Energie- noch Spannungs-Bedingung
  →  Modus wird gedeckelt
```

Praktisch betrifft das die dreckigste rote Stunde, in der `frei` unabhängig vom Inhalt gilt: Die erzwungene Entladung soll nicht ins Auto gehen, dessen Last landet im Netz. Die `-v`-Ausgabe zeigt `[free->limit: wallbox on grid terms]`.

### Wallbox ohne wirksamen Timer

Die Wallbox wird unabhängig vom Timer gesteuert: auch mit `disable_zeroinput_timer: true` in `dirt_shift.conf` oder `discharge_timer: false` in `zeroinput.conf`. Dann fehlt allerdings der Akkuschutz auf der Batterieseite; allein über die Sauberkeits-Bedingung kann das Auto den Akku unter die Reserve entladen. Die Energie-Bedingung prüft die Reserve weiterhin.

### Besitzer-Merker

`dirt_shift` schaltet nur ab, was es selbst eingeschaltet hat. `dirt_wallbox_marker.json` hält dazu `dirt_shift_on` (eigenes Relais) sowie `energy_on` und `voltage_on` (Hysterese-Zustände, getrennt geführt, damit keine Halteschwelle ein fremd eingeschaltetes Relais stützt). Beim ersten Lauf gilt ein bereits eingeschaltetes Relais als fremd. Ein manuell eingeschaltetes Relais wird übernommen, sobald eine Stunde die Bedingungen erfüllt, und kann danach regulär abgeschaltet werden. Eine manuelle Schaltung, während der Merker schon gesetzt ist, lässt sich nicht erkennen.

### Schalten mit Verifikation

Direkter `Power{output}`-Befehl, danach Abfrage des tatsächlichen Relais-Zustands: bis zu 3 Versuche im Abstand von 30 s (`wallbox_switch`). Erst ein verifizierter Wechsel aktualisiert den Merker. Ein nicht erreichbares Tasmota-Gerät kann den Lauf dadurch um bis zu ~90 s verzögern, auch das Schreiben der Timer-Zeile.

---

## Ausgabe: timer.txt

```
YYYY-MM-DD HH:MM:00 <entlade-W> <ac-%> <energie-Wh>
```

Felder durch genau ein Leerzeichen getrennt; Werte > 100 gelten als Watt, ≤ 100 als Prozent.

| Modus | Zeile |
|---|---|
| frei | `100 100 -1` |
| gedeckelt | `<limit_discharge_rate> 100 <Budget>` |
| Stopp | `000 100 000` |

`-1` ist der Sentinel für „unbegrenzt", den zeroinput erkennt. zeroinput setzt seinen Energiezähler bei jeder neuen Zeile zurück; da `dirt_shift` viertelstündlich schreibt, erhält jede Viertelstunde ihr eigenes Budget. Während Precharge liegt `ac-%` zwischen 20 und 100. Geschrieben wird nur der aktuelle Viertelstunden-Slot (`write_timer`); zeroinput übernimmt jeweils die letzte bereits begonnene Zeile.

**DISABLED timer:** Steht in `zeroinput.conf` `discharge_timer` auf `false`, wird die Zeile trotzdem geschrieben, aber bei jedem Lauf gemeldet, dass zeroinput sie nicht anwendet. Die Wallbox wird weiter gesteuert.

---

## Ausfallsicherung

Begrenzt, stoppt oder drosselt ein Lauf, folgt 30 Minuten später eine „Alles-erlaubt"-Zeile (`100 100 -1`). Läuft `dirt_shift` weiter, wird die Begrenzung alle 15 Minuten erneuert; fällt es aus, hebt sie sich von selbst auf.

---

## Netz-Dreckigkeit exportieren

Ist `vz_dirtiness_uuid` gesetzt, schreibt jeder Lauf den aktuellen `dirt%`-Wert per HTTP-POST in diesen volkszaehler.org-Kanal; beim ersten Lauf einer Stunde zusätzlich den Wert der Vorstunde eine Sekunde vor der Stundengrenze, damit die Kurve eine saubere Stufe bildet (`write_dirtiness_to_vz`). Der Wert folgt der Skala der jeweiligen Quelle: Mit der Stromampel liegt er niedriger als mit SMARD; wechselt die Quelle, entfällt die Stufe zur Vorstunde. Ein Sprung im Verlauf ohne entsprechende Wetteränderung zeigt deshalb einen Quellenwechsel an; welche Quelle gilt, nennen die `-v`-Ausgabe und das Logfile. Im Notmodus wird nichts exportiert. Ein Fehlschlag wird nur mit `-v` gemeldet.

---

## Ausgabe: pv_forecast.json

Einmal pro Kalendertag schreibt `write_daily_pv_forecast` neben `dirt_shift.py` (fester Name `PV_FORECAST_FILE`, nicht konfigurierbar):

```json
{"datum": "2026-09-29", "pv_forecast_kwh": 23.4}
```

`pv_forecast_kwh` ist die über den **ganzen Kalendertag** (Stunden 0–23) summierte, wetterskalierte PV-Prognose. Das eigene Datum dient als Marke, ob heute schon geschrieben wurde; fehlen Daten, wird beim nächsten Lauf erneut versucht. `dirt_shift` liest die Datei selbst nie. Feldname und Bedeutung entsprechen dem Feld `pv_forecast_kwh` der API von [Ortsnetz-Auslastung](https://github.com/thomaslehmann1234/datenerfassung-ortsnetz-auslastung), für die sie gedacht ist; ein vzlogger-Cron-Skript dieses Projekts liest den Wert und überträgt ihn mit den Netzspannungswerten.

---

## Ausgabe: -v und -debug

Die erste Zeile nennt die Version (`dirt_shift 2.1`).

**Cache-Zeilen** nennen Abrufzeitpunkt und Reichweite der Daten, etwa `using cached Energy-Charts zones from 2026-11-18 06:00, data until 2026-11-18 23:00`; dabei steht die verwendete Quelle (`Energy-Charts` oder `SMARD`).

**Modus-Zeile:**

```
content ... Wh (SoC%)   reserve(...%) ... Wh [window|upcoming: .../...-...pv] -> dirtiest  HH:MM   =>  mode: ...
```

Die Klammer nennt die bindende Schätzung, beide Rohwerte und den abgezogenen Überschuss. Zusätze wie `(comfortable)`, `[red block +N dirt]` oder `[stop would overflow ...]` begründen den Modus.

**Debug-Tabelle** (`_hourly_debug_table`): eine Kopfzeile `dirt% source: ...` mit der Quelle (`Energy-Charts`, `SMARD` oder `model` im Notmodus), dann 24 Zeilen chronologisch ab der aktuellen Stunde, über Mitternacht hinweg.

- `chg`: `L` Ladestunde (`exp_PV > basic_load`), `D` Entladestunde; `!D` = dreckigste Stunde, `!L` = sauberste Ladestunde (nur informativ).
- `balance` = `exp_PV − basic_load`.
- Führender `.` bei `rad_Wm2` bzw. `dirt%`: für diese Stunde von morgen liegen noch keine eigenen Daten vor.
- Vor `dirt%` der aktuellen Stunde: `*` exportiert, `!` Export fehlgeschlagen.
- Summenzeile: Tagessummen von `exp_PV` und `basic_load`, deren Differenz, `dirt%`-Durchschnitt.

---

## Fehlerverhalten

Fehlende Daten lassen so weit wie möglich nur den betroffenen Teil entfallen:

| Fehlt | Folge |
|---|---|
| Akkuinhalt | Batterie frei, Wallbox über die übrigen Pfade weiter gesteuert |
| Stromampel | SMARD |
| Stromampel und SMARD | Cache vom Vortag, sofern er eine Prognose für heute enthält, sonst Notmodus |
| Strahlungsprognose | unskalierte Referenzkurve |
| SMARD-Verbrauchsprognose | Profil des letzten verfügbaren Tages |
| volkszaehler.org nicht erreichbar | gecachte Mittelwerte und PV-Kurve; da der Akkuinhalt fehlt, Batterie frei |

Nur wenn für die Mittelung keine vollständigen Tage vorliegen, volkszaehler.org nicht erreichbar ist und keine gecachten Mittelwerte vorhanden sind, oder weder Netzdaten noch Wetter verfügbar sind, bricht `dirt_shift` ab. Zuvor schreibt es eine „Alles-erlaubt"-Zeile mit aktuellem Datum (`write_free_timer`), außer bei `disable_zeroinput_timer: true`; das Wallbox-Relais bleibt unangetastet. Ist nicht einmal `zeroinput.conf` lesbar, bleibt nur die Fehlermeldung.

---

## Konfiguration

`dirt_shift.conf` enthält den Pfad zur `zeroinput.conf` und die eigenen Parameter; `zeroinput.conf` wird nie verändert.

Aus `zeroinput.conf` gelesen:

- `discharge_t_file` — Pfad der Timer-Datei (relativ zur `zeroinput.conf`)
- `cell_count` — Zellenzahl für die Spannungsschwellen (Standard 16)
- `discharge_timer` — ob zeroinput die Timer-Datei anwendet; fehlt der Schlüssel, gilt er als aktiv

In `dirt_shift.conf`:

| Schlüssel | Bedeutung | Standard |
|---|---|---|
| `zeroinput_conf` | Pfad zur `zeroinput.conf` | `../zeroinput.conf` |
| `vz_host_port`, `vz_chans` | volkszaehler.org-Host und Kanal-UUIDs | — |
| `vz_dirtiness_uuid` | Kanal für den Dreckigkeits-Export, leer = aus | leer |
| `average_days` | Tage für das `basic_load`-Mittel | 7 |
| `day_weights_pct` | Gewichtung je Tag, Index 0 = ältester; bei falscher Länge gleich gewichtet | `[200, 100, 100, 100, 100, 150, 200]` |
| `reserve_pct` | Anteil des Bedarfs als Reserve; skaliert auch Budget (dort ≤ 100 %) und Wallbox-Reserve | 90 |
| `limit_discharge_rate` | Watt-Deckel in gedeckelten Stunden | 3000 |
| `latitude`, `longitude` | Standort für Klarhimmel-Modell und Wetter | 51,0 / 10,0 |
| `PV_to_bat_efficiency`, `bat_to_AC_efficiency` | Wirkungsgrade in % für Energieinhalt und Rundlaufverlust | 98 / 90 |
| `max_days_empty_battery` | Suchtiefe für den Leer-Anker in Tagen | 7 |
| `battery_capacity_wh` | nutzbare Kapazität; Überlaufschutz, Precharge, Füllstand, Negativwarnung; 0 = Prüfungen aus | 5000 |
| `disable_zeroinput_timer` | keine Timer-Datei schreiben (auch nicht nach Abbruch); Akku ungesteuert, Wallbox weiter gesteuert | `false` |
| `logfile` | spiegelt die Ausgabe in eine Datei und aktiviert dabei `-v`; leer = aus | leer |
| `precharge_enabled` | Precharge-Pfad | `false` |
| `precharge_cleanest_pct` | Rang-Schwelle für Precharge in % | 25 |
| `round_trip_margin` | Kosten/Sauberkeit für Precharge, ≥ 1,0 | 1,5 |
| `wallbox_enabled` | Wallbox-Steuerung | `false` |
| `wallbox_ip`, `wallbox_output` | Tasmota-Gerät und Relais-Nummer | —, `"1"` |
| `wallbox_cleanest_pct` | Rang-Schwelle für die Sauberkeits-Bedingung in % | 30 |
| `wallbox_absolute_max` | absolute `dirt%`-Obergrenze für die Wallbox | 50 |
| `wallbox_typical_power` | typische Ladeleistung in W für die Energie-Bedingung | 2000 |

Die Hilfetexte zu jedem Schlüssel stehen als `*_help`-Einträge in `dirt_shift.conf`.

---

## Aufrufoptionen

- `-v` — ausführliche Ausgabe
- `-debug` — zusätzlich die Stundentabelle (impliziert `-v`)
- `-avgnew` — alle Caches frisch abrufen
- `-html` — HTML-Kopf/-Fuß um die Ausgabe
- `-h` — Kurzhilfe mit Versionsnummer
