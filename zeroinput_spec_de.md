# zeroinput – Funktionsspezifikation
*v2.2*

## Zweck

zeroinput steuert einen oder mehrere Batterie-Netzwechselrichter für Nulleinspeisung (Eigenverbrauchsoptimierung). Der Stromzähler wird kontinuierlich ausgelesen; die Wechselrichterleistung wird jeden Zyklus so angepasst, dass der Zähler möglichst nahe an null bleibt — weder Bezug noch Einspeisung.

Die Wechselrichterseite ist eine generische Mehrtyp-Treiberarchitektur: Soyosource-Limiter-Wechselrichter und Victron MultiPlus (ESS) werden unterstützt, in beliebiger Mischung und Anzahl, verteilt auf zwei Leistungsstufen.

## Regelkreis

Jeder Zyklus (~1 s):

1. Zählerwert `Ls_read` (W) aus vzlogger-FIFO lesen (`1-0:16.7.0`)
2. Leistungsanforderung berechnen:
   `power_demand = Ls_read + last_demand + zero_shift + predictive_offset`
3. Grenzen anwenden (Batterie, PV, Timer, Min/Max)
4. Anforderung an Wechselrichter senden (zweimal pro Zyklus, 50 ms Abstand)
5. MPPT-Laderegler abfragen
6. Werte in Volkszähler schreiben

---

## Wechselrichtersteuerung

Wechselrichter werden im `inverters`-Block konfiguriert. Jeder Eintrag ist eine **Gruppe** baugleicher Einheiten an einem Port:

- `id` (der Dict-Schlüssel) — frei wählbare Kennung
- `type` — `soyosource` | `victron_mk3`
- `port` — serieller Pfad; ein Sender pro Port, mehrere baugleiche Einheiten teilen ihn über `count` (alle empfangen ein gemeinsames Broadcast-Paket)
- `stage` — **Liste** der Stufen, in denen die Gruppe läuft: `[1,2]` beide, `[1]` nur Grundlast, `[2]` nur Stufe 2. Eine bloße Zahl `n` wird als `[n]` gelesen. Stufe 2 schließt Stufe 1 **nicht** automatisch ein, ein harter Wechsel ist also möglich (z.B. ein Soyosource auf `[1]` und ein MultiPlus auf `[2]`). Eine **leere Liste `[]` deaktiviert den Wechselrichter** — er bleibt in der Config, bekommt aber nie Leistung und zählt nicht als aktiv.
- `count` — Anzahl baugleicher Einheiten am Port (Standard 1)
- `max_power` / `min_power` — **pro Einzelgerät**, in W. Unterhalb `min_power` schläft die Gruppe.

**Zwei Stufen**, anhand der Anforderungshistorie mit derselben Hysterese wie bisher gewählt (`single_inverter_threshold`, geglättet, `multi_inverter_wait` vor dem Zurückschalten):

- **Stufe 1** — nur Gruppen, deren `stage`-Liste die 1 enthält, tragen die Grundlast.
- **Stufe 2** — jede berechtigte Gruppe teilt sich `power_demand` zu **gleichen Teilen pro Einzelgerät**, unabhängig von der `max_power`. Mit wachsender Last steigt der gemeinsame Pro-Gerät-Wert, bis die kleinsten Einheiten ihre `max_power` erreichen; diese sättigen und die Restlast wird gleichmäßig auf die verbleibenden offenen Einheiten neu aufgeteilt. Das größte Gerät (z.B. der MultiPlus) sättigt damit zuletzt und wird von selbst zur obersten Stufe — durch Kapazität, nicht durch eine Sonderregel.

Jede Gruppe sendet genau einen Befehl pro Zyklus: eine Soyosource-Gruppe sendet ein Paket (Pro-Gerät-Wert) an alle ihre Einheiten; eine MultiPlus-Gruppe schreibt einen ESS-Sollwert. Beim Start prüft zeroinput, ob die Stufe-1-Gruppen allein `single_inverter_threshold` abdecken, und warnt andernfalls.

**Stufe-2→1-Überblendung.** Wechselt die Stufe von 2 auf 1 (nach der vollständigen `multi_inverter_wait`-Hysterese), müsste die einzelne verbleibende Stufe-1-Einheit sonst in einem Schritt von ihrem kleinen Gleichanteil (z.B. ~200 W bei vier Einheiten, die sich 800 W teilen) auf fast die gesamte Anforderung springen. Stattdessen wird die Pro-Gerät-Verteilung über `STAGE_FADE_CYCLES` Zyklen (5, ≈5 s) linear von der Stufe-2- zur Stufe-1-Verteilung überblendet: die Stufe-1-Einheit fadet hoch, während die Stufe-2-Einheiten herunterfaden. Da die einzelne hochfadende Einheit einen viel größeren Sprung zu bewältigen hat als jede herunterfadende, läuft das Ausfaden der Stufe-2-Einheiten um `STAGE2_FADE_OUT_DELAY` Zyklen (2) hinter dem Einfaden her — die hochfadende Einheit bekommt einen Vorsprung, sodass die summierte Einspeisung nie unter die Anforderung fällt. Der Kompromiss ist eine kurze Übereinspeisung während der Überlappung (wird hingenommen und pro Gerät auf `max_power` begrenzt). Die Übereinspeisung lebt nur in der Verteilung; `active_stage` liest die `power_demand`-Historie, nicht die gesendete Verteilung, kann also keinen Rückfall auf Stufe 2 auslösen. Eine laufende Rampe (`ramp_cnt > 0`) oder ein Lastanstieg, der groß genug ist, um `active_stage` wieder auf 2 zu heben, bricht die Überblendung sofort ab und die Anforderung wird normal verteilt. Das Hochschalten erfolgt sofort (ohne `multi_inverter_wait`-Verzögerung), sodass sowohl ein plötzlicher rampengroßer Sprung als auch ein allmählicher Anstieg, der nie eine Rampe ausgelöst hat, erfasst werden; fällt die Anforderung auf null, wird die Überblendung ebenfalls gelöscht. Der umgekehrte Übergang (1→2) wird nicht überblendet. Während Stufe 2 hängt die verbose-/Web-Ausgabe `Nc` (N Zyklen) an die Einheiten-Zeile an — eine bedingte Schätzung, wie viele Zyklen noch bis zur Rückkehr auf Stufe 1 verbleiben, *sofern jeder zukünftige Wert auf oder unter dem Schwellwert bleibt*. Sie wächst wieder, sobald ein neuer hoher Wert in die Historie eintritt, da das Herunterschalten das gesamte Fenster zum Einschwingen braucht.

**Lückenprüfung.** Beim Start (und beim Speichern der Inverter-Konfiguration in der Weboberfläche) durchläuft zeroinput die angeforderte Leistung von der kleinsten `min_power` bis `max_input_power` entlang des realen Regelpfads und meldet jedes Leistungsband, das keine Wechselrichterkombination liefern kann. In Stufe 2 bewegt die Gleichteilung die gelieferte Leistung in Schritten von etwa der Anzahl aktiver Einheiten — das ist die immanente Regelauflösung und keine Lücke; nur größere Sprünge werden gemeldet. Lückenlose Abdeckung ist Pflicht: eine Lücke (z.B. ein Stufe-1-Gerät endet bei 900 W, während das einzige Stufe-2-Gerät `min_power` 1500 W hat) erzeugt eine unübersehbare Warnung. Die Einspeisung wird über `max_input_power = 0` oder den Timer deaktiviert, nicht durch das Stehenlassen von Lücken.

Die Anzahl aktiver Einheiten (Summe der `count` über Gruppen, die Leistung erhalten haben) fließt in das Rampenverhalten ein.

---

## Wechselrichter-Treiber und unterstützte Hardware

Wechselrichter laufen über eine generische Treiberschicht (`inverter_drivers.py`). Jeder Typ ist eine Unterklasse von `InverterDriver` und implementiert `set_power(watts_per_unit)`, `sleep()`, optional `read_status()` sowie `start()`/`stop()`. `build_inverters()` erzeugt einen Treiber pro `inverters`-Eintrag und erzwingt einen Sender pro Port. Ein neuer Typ bedeutet: neue Unterklasse, Eintrag in `DRIVER_TYPES`, Einträge mit diesem `type` — die Stufenlogik bleibt unberührt.

**Soyosource** (`type: soyosource`) — das Limiter-Protokoll über RS485 mit 4800 Baud. Zustandslos: Port öffnen, Anforderungspaket zweimal senden (50 ms Abstand) zur Sicherheit, schließen. Alle baugleichen Einheiten am Port empfangen dasselbe Broadcast-Paket (keine Adressierung). Unterstützt wird die GTN-Limiter-Serie mit RS485:

- GTN-1000LIM (24/36/48/72/96 V) sowie die GTN-1200- / GTN-2000-Limiter-Varianten
- Display-, WiFi- und OEM-Dongle-Hardwarevarianten bieten alle denselben RS485-Limiter-Befehl
- Das wasserdichte **GTW**-Außengehäuse hat **keinen** Limiter/RS485 und ist nicht steuerbar
- Ein größeres Gerät wie der GTN-2000 ist nur ein Eintrag mit `max_power: 2000`; kein Code, nur Config (Paketkompatibilität vorher an echter Hardware prüfen)

**Victron MultiPlus** (`type: victron_mk3`) — aktiver ESS-Leistungssollwert über VE.Bus per MK2/MK3-USB-Adapter (siehe *MultiPlus / VE.Bus-Steuerung*). Voraussetzungen:

- Ein VE.Bus Multi, MultiPlus, MultiPlus-II, Multi Grid oder Quattro mit Mikroprozessor der 2. Generation (26/27); alle aktuell ausgelieferten VE.Bus-Wechselrichter/Lader erfüllen das. Der Multi RS ist ausgeschlossen (kein ESS).
- Der **ESS-Assistant** muss in VEConfigure konfiguriert sein, der Schalter vollständig auf EIN (nicht „nur Lader"). Andernfalls schlägt der Assistant-Scan fehl und der Treiber bleibt inaktiv.
- Ein MK2-USB- / MK3-USB-Adapter (oder direkte VE.Bus-RS485-Verbindung). Kein GX-Gerät nötig.
- Andere VE.Bus-ESS-Geräte (Quattro, weitere Multi-Varianten) nutzen denselben `vebus.py`-Pfad mit minimaler Anpassung.

**Warum nur diese zwei Typen.** Beide liefern einen *aktiven Watt-Sollwert im 1-s-Regeltakt aus einer steuerbaren (Batterie-)Quelle*. Andere Wechselrichterfamilien wurden geprüft und bewusst ausgeschlossen:

- **Growatt** (MIC/MOD/MID PV-Strings): nur prozentuale Leistungsdeckelung / Export-Limit, kein aktiver Sollwert. SPH/SPA-Hybride haben zwar einen Watt-Sollwert, schreiben aber in EEPROM/Flash und reagieren träge — bei 1 s unsicher. Off-Grid SPF ist Inselbetrieb.
- **Hoymiles HM/HMS** (Mikro): nur relatives/absolutes Leistungs-*Limit*, braucht eine DTU, reagiert in 18–90 s — mit dem 1-s-Takt unvereinbar.
- **APsystems EZ1** (Mikro): nur `setMaxPower` (Deckel) über fragiles HTTP/WLAN; der Hersteller hat die lokale Steuerung in Firmware 1.1.2_b entfernt.
- **Deye / Sunsynk / Sol-Ark** (Hybrid, baugleich): echter Watt-Sollwert über Modbus RTU, aber die Sollwert-Register sind als RAM oder Flash undokumentiert; die einzige feldbewährte sichere Rate ist ~1500 Schreibvorgänge/Tag (~1/Minute), ~38× unter zeroinputs Sekundentakt. Nur als bewusst schreibratenbegrenzte träge Stufe integrierbar, nicht als schnelle Regelstufe.

Die gemeinsame Regel: PV-String-, Mikro- und Off-Grid-Wechselrichter haben eine unkontrollierbare Quelle und können nur drosseln; echte Watt-Sollwert-Steuerung braucht eine steuerbare Quelle hinter dem Wechselrichter (DC-Batterie bei Soyosource, ESS-Batterie beim MultiPlus).

---

## Nullverschiebung (Zero-shift)

Ein konfigurierbarer Offset auf den Leistungssollwert:

- **Manuell** (`zero_shifting ≠ 0`): fester Bezugs- (`< 0`) oder Einspeise-Bias (`> 0`)
- **Automatisch** (`zero_shifting = 0`): aus der jüngsten Zählerhistorie abgeleitet; folgt langsam dem tatsächlichen Nulldurchgang, um Zähler- oder Timing-Offsets zu kompensieren. Pausiert während der Lastprediktor aktiv ist.

---

## Batterieverwaltung

- **Einstellbare Zellzahl** (`cell_count`): Alle Batteriespannungs-Schwellen sind als Spannung pro Zelle hinterlegt und skalieren mit der Zahl der LiFePO4-Zellen in Serie. Standard ist 16S (51,2 V nominal); auch 15S (48 V), 8S (24/28 V) und andere Werte sind möglich. Die folgenden Spannungsangaben gelten für 16S und skalieren entsprechend.
- **Spannungskurve** (48–51 V bei 16S, also 3,00–3,19 V/Zelle): Batterieentladeleistung wird durch eine Leistungskurve begrenzt; volle Entladung ab der oberen Schwelle erlaubt.
- **Unterspannungsschutz**: unter der unteren Schwelle (48 V bei 16S) wird der Wechselrichter für 1 Minute deaktiviert.
- **Spannungskorrektur** (`bat_voltage_const`): kompensiert Spannungsabfall unter Last mit einem konfigurierbaren Faktor (V/kW).
- **Freie Einspeisung** (`free_power_export`): oberhalb der Export-Schwelle (54,5 V bei 16S) wird überschüssige Energie gezielt ins Netz eingespeist; skaliert linear bis `max_input_power` bei der MPPT-Float-Spannung (57 V bei 16S).
- **Plausibilitätsfilter und Halten der Batteriespannung**: Bei der Mittelung der Batteriespannung über mehrere Laderegler werden Messwerte unter 2,0 V/Zelle verworfen, da eine LiFePO4-Zelle im Betrieb nie so tief fällt. Ein gestörter Regler, der 0 V oder einen unsinnig niedrigen Wert liefert, beeinflusst die gemittelte Spannung damit nicht. Liefert in einem Zyklus kein Regler einen plausiblen Wert — etwa nachts, wenn die MPPTs mangels PV-Eingangsspannung in den Ruhezustand gehen und keine Telemetrie mehr senden —, wird der zuletzt gemessene plausible Spannungswert gehalten und weitergegeben. So entsteht aus fehlender Messung keine 0 V, die wie eine leere Batterie aussähe und den Unterspannungsschutz auslösen würde. Diese Logik ist vollständig in der Laderegler-Aggregation gekapselt; die Regelung erhält immer einen plausiblen Spannungswert. Solange überhaupt noch nie ein gültiger Wert vorlag (Kaltstart), greift die Start-Wartephase.

**Start-Wartephase auf Batteriedaten.** Vor Eintritt in die Hauptschleife ruft zeroinput wiederholt `combine_charger_data()` auf (alle 0,2 s, bis zu 10 s), bis `mppt_data['combined']['Vbat'] > 0` ist. Ohne diese Wartephase würde der allererste Zyklus `Vbat == 0` sehen (noch keine Laderdaten abgefragt/empfangen) — nicht von einer echten 0-V-Messung unterscheidbar — und bei jedem Neustart fälschlich den 1-Minuten-Unterspannungstimeout auslösen. Das funktioniert für jeden Ladertyp: synchrone Leser (eSmart3, Modbus) haben bereits Daten aus dem Warmup-Lesevorgang von `build_chargers()`, während AGG/Victron-Lesethreads ihren ersten Block innerhalb dieses Zeitfensters liefern. Meldet kein Lader innerhalb von 10 s einen `Vbat`-Wert, protokolliert zeroinput eine Warnung und startet trotzdem.

---

## Hitzeschutz

Ein optionaler Hitzeschutz begrenzt `power_demand` linear anhand eines auswählbaren Temperatursensors. Unterhalb von `heat_temp_low` ist die volle `max_input_power` erlaubt, ab `heat_temp_high` wird der Wechselrichter abgeschaltet (Deckel 0), dazwischen linear interpoliert. Damit läuft der Wechselrichter bei Überhitzung nicht mit Restleistung weiter, sondern geht aus.

Als Auslöser dient genau ein Laderegler, dessen Konfiguration `heat_protect: true` trägt. Geeignet ist jeder temperaturführende Geber (eigener Temperatursensor, eSmart3, Modbus-Regler, Aggregator-Subsensor); gelesen wird `ext_temp`, ersatzweise `int_temp`. Ist kein Sensor gewählt, ist der Schutz deaktiviert. Liefert der gewählte Sensor kurzzeitig keinen Messwert, wird der zuletzt gültige Temperaturwert weiter verwendet — eine reale Geräte-/Kühlkörpertemperatur ändert sich langsam genug, dass eine kurze Lücke unkritisch ist. Erst bei anhaltendem Sensorausfall greift als Sicherung ein fester Anteil der Maximalleistung (`HEAT_FAIL_FRACTION`, standardmäßig 50 %).

Konfiguration: `heat_temp_low`, `heat_temp_high` (globale Schwellen) und die Sensor-Auswahl (`heat_protect`-Markierung an einem Laderegler, in der Weboberfläche per Auswahl).

---

## PV-Durchleitung

Die verfügbare PV-Leistung wird aus einem gleitenden Mittelwert der jüngsten MPPT-Ausgabe abzüglich eines Wirkungsgradspalts (`PV_to_AC_efficiency`) geschätzt. Die Leistungsanforderung wird auf `PV_power + allowed_battery_discharge` begrenzt, um ungewollten Batteriebezug zu vermeiden.

---

## Sägezahnverhinderung

Schwingungen in der Sendehistorie (wechselnde hohe/niedrige Anforderungen) werden durch Vergleich aufeinanderfolgender Paare erkannt. Bei bestätigtem Sägezahnverhalten ersetzt der Durchschnitt der letzten vier Werte die aktuelle Anforderung.

---

## Rampenverhalten

Große plötzliche Zähleränderungen lösen einen Rampenmodus aus: maßgeblich ist die **Änderung gegenüber dem vorigen Zyklus** (`Ls_read − last_Ls_read`), nicht der Absolutwert. Überschreitet diese Änderung 400 W, wird die Anforderung für `2 + round(min(|Sprung|, max_input_power) / (400 × Anzahl aktiver Einheiten))` Zyklen auf dem Sprungwert gehalten, bevor die normale Regelung wieder einsetzt. Die Prüfung auf die Änderung statt den Absolutwert ist wichtig, weil der Zähler vor dem Sprung nicht zwingend bei null lag — ein Lastabwurf, der den Zähler etwa von +300 W (Bezug) auf −1100 W (Einspeisung) wirft, ist ein Sprung von 1400 W, auch wenn keiner der beiden Absolutwerte für sich die Schwelle in der erwarteten Richtung sauber trifft. Die Formel nimmt an, dass jede aktive Wechselrichter-Einheit ihren Sollwert mit etwa 400 W/s rampt — ein größerer Sprung oder weniger aktive Einheiten brauchen mehr Zyklen zum Einschwingen, mehr Einheiten teilen sich die Rampe und sind schneller fertig. Der Sprung wird auf `max_input_power` begrenzt, da `power_demand` diesen Wert ohnehin nie überschreiten kann — ohne diese Begrenzung würde ein Sprung, der größer ist als das System je liefern kann, eine unrealistisch lange Haltezeit ergeben. Der Mindestwert ist 2 Zyklen (kleiner Sprung, mehrere Einheiten); gilt für Auf- und Abwärtsrampen gleich. Die erste Aufwärtsrampe nach einer stabilen Phase wird verworfen, um kurze, wenig bedeutsame Lastspitzen — wie das Anlaufen eines Kühlschrankkompressors — herauszufiltern, die sonst eine vollständige Rampenantwort auslösen würden.

Eine laufende Rampe wird abgebrochen, wenn der Zählersprung jetzt um mehr als 400 W in die entgegengesetzte Richtung zeigt (z.B. ein starker Abwärtssprung während eine Aufwärtsrampe noch herunterzählt). Ohne diesen Abbruch würde die Anforderung auf dem veralteten Rampenwert verharren — und unnötig einspeisen oder beziehen — bis der Countdown der ursprünglichen Rampe abgelaufen ist. Beim Abbruch startet im selben Zyklus eine neue Rampe in der neuen Richtung.

---

## Lastprediktor (`predictor.py`)

Der Lastprediktor erkennt zyklische Lasten (Waschmaschine, Spülmaschine, Herd) und
wiederkehrende kurze Hochlast-Spitzen und beugt der Einspeisung vor, die sie sonst verursachen
würden. Er betreibt zwei Mechanismen: k-Means-Pegelstabilisierung für zyklische Lasten und
einen Peak-/Override-Mechanismus für wiederkehrende kurze Spitzen. Eine Spitze gilt als kurz,
solange sie die Längenschwelle nicht überschreitet; überschreitet sie diese, wird sie als lange
Last eingestuft und vom Spitzen-Mechanismus ausgenommen. Der Override wird erst durch zwei
echte kurze Spitzen innerhalb des Beobachtungsfensters scharf geschaltet, sodass eine einzelne
lange Last keine Übersteuerung auslöst.

Er ist ein optionales, austauschbares Modul. zeroinput benötigt nur eine `LoadPredictor`-Klasse
mit `update(Ls_read, last2_send)`, `reload_conf(conf)`, `status()` und den Attributen `enabled`,
`offset` und `ramp_override_by_predictor`. Eigene Strategien können durch Ersetzen von
`predictor.py` implementiert werden, ohne zeroinput selbst zu ändern.

Ist `predictor.py` vorhanden, instanziiert zeroinput beim Start immer eine echte
`LoadPredictor` — unabhängig vom initialen Wert von `load_prediction` — und schaltet sie nur
über `reload_conf` ein/aus. Das Stub-Objekt (No-Op-`update`/`reload_conf` mit `enabled=False`)
kommt nur zum Einsatz, wenn `predictor.py` komplett fehlt (`ImportError`). Damit ist
`load_prediction` in beide Richtungen vollständig hot-umschaltbar: Einschalten initialisiert den
Lernzustand des Predictors neu (`reload_conf` ruft beim Aus→Ein-Übergang `_init_state()` auf),
Ausschalten greift sofort über die `enabled`-Prüfung unten.

zeroinput berücksichtigt `ramp_override_by_predictor` nur, solange `predictor.enabled` wahr ist.
Das schützt vor einem veralteten Override-Flag, das von vor dem Abschalten von `load_prediction`
zur Laufzeit übrig geblieben ist — ohne diese `enabled`-Prüfung würde ein solches Flag `ramp_cnt`
jeden Zyklus auf 0 zwingen und keine Rampe könnte laufen.

Die vollständige Beschreibung — beide Mechanismen, das Spreizungsfenster, das zyklenbasierte
Zeitmodell und alle Konstanten — steht in
**[predictor_spec_de.md](predictor_spec_de.md)**.


---

## Entladetimer

Optionale zeitbasierte Steuerung über `timer.txt`. Jede Regel legt fest:
- **battery** — maximale Entladeleistung (W oder % von `max_bat_discharge`)
- **inverter** — maximale Einspeiseleistung (W oder % von `max_input_power`)
- **energy_Wh** — Energiebudget pro Timerperiode; nach Überschreitung stoppt Batterieentladung, nur PV-Durchleitung läuft weiter

Regeln werden der Reihe nach aktiviert; `0000-00-00` als Datum gilt täglich.

`timer.txt` kann von Hand geschrieben oder von einem externen Tool erzeugt werden. Die [Tibber-Tools](https://github.com/E-t0m/zeroinput/tree/main/tibber) (optional, nicht gepflegt) erzeugen eine `timer.txt` aus Tibber-Dynamikpreisdaten und verteilen Wechselrichterleistung, Batterieentladung und Energiemengen in die teuersten Preisslots.

---

## MPPT-Laderegler-Unterstützung

Die laderseitige Ein-/Ausgabe liegt in `charger_drivers.py` (getrennt von der Wechselrichter-Treiberschicht). Dieses Modul besitzt `mppt_data`, alle Lesethreads, die `VEDirectBridge`-Instanzen und die Modbus-Ladertreiber. `zeroinput.py` ruft dessen öffentliche API auf (`build_chargers`, `poll_chargers`, `combine_charger_data`, `display_mppt_data`, `check_temp_alarms`, `set_victron_power`, `check_stale`) und liest `mppt_data` direkt als geteilte Referenz.

**eSmart3** — per RS485 jeden Zyklus abgefragt (Statusanforderung, Antwort parsen). Prüfsumme validiert (`(0xaa + sum(data)) & 0xFF == 0`) — fehlerhafte Pakete werden verworfen. Unterstützt Temperaturüberwachung und Alarme pro Gerät, Lastport-Daten (`Iload`, `Vload`, `Pload`) und `pvp` (PV-Spitzenleistung W) für `%PVp`-Anzeige. Mehrere Geräte unterstützt.

**Victron MPPT (konventionell)** — per VE.Direct-Protokoll in einem dedizierten Hintergrundthread pro Gerät ausgelesen. `IL` (Laststrom) und `LOAD` (EIN/AUS) für Lastport-Anzeige geparst. `Pload = IL × Vbat` bei Verfügbarkeit berechnet. `pvp` für `%PVp`-Anzeige gespeichert. Portausfall setzt `CS='PORT'` für `PORT ERROR`-Anzeige. Ein Thread pro Port.

**Victron MPPT (Aggregator)** — mehrere MPPTs an einem seriellen Port via `readtext_sendhex`-Firmware ([VE.Direct-Aggregator](https://github.com/E-t0m/ve.direct-aggregator), Arduino Mega 2560 / Teensy 4.1). VE.Direct ist elektrisch ein 3,3-V-UART; die Verbindung zum Pi läuft über einen USB-UART-Adapter. RS485-Pegelwandler können auf einer oder beiden Seiten zur Leitungsverlängerung eingesetzt werden, sind aber nicht erforderlich. Verwaltet durch `VEDirectBridge`, das `ve_aggregator.VEDirect` über den `on_block`-Callback einbindet — geparste Blöcke werden direkt mit Block-Rate in `mppt_data` geschrieben, kein Patching, kein doppeltes Parsen, kein Polling-Thread. [`ve_aggregator.py`](https://github.com/E-t0m/ve.direct-aggregator) muss im gleichen Verzeichnis wie `zeroinput.py` liegen. Geräte werden per SER# identifiziert (`mppt_type: victron_agg`). Geräte-`pvp` in `devices[ser]['pvp']`. Geräte mit `type: temp` in der conf werden zu `mppt_type: temp_sensor` — DS18B20-Temperaturblöcke (Feld `TEMP`) werden als `ext_temp` in `mppt_data` geschrieben und in einer separaten Zeile unterhalb der Haupttabelle angezeigt. `check_stale()` wird jeden Zyklus aufgerufen — ersetzt `mppt_data[key]` atomar durch `{'CS': 'PORT'}` für Geräte, die innerhalb von `device_timeout` nicht gesehen wurden, und nullt dabei alle Messwerte, um veraltete Daten in `combine_charger_data` und `set_victron_power` zu verhindern. Unkonfigurierte SER# werden als `UNCONFIGURED` angezeigt. Die AGG-Firmware sendet etwa alle 10 s ein `ALIVE`-Keepalive; `ve_aggregator.VEDirect` akzeptiert einen `on_alive`-Callback (aufgerufen von beiden ALIVE-Erkennungsstellen: dem Zeilenscanner des Reader-Threads und `_handle_block`), den `VEDirectBridge` nutzt, um jedes ALIVE als `REC <port> ALIVE <agg-name>` zu melden — bestätigt, dass die MCU selbst erreichbar ist, unabhängig von einzelnen MPPT-Geräten. Zwei Hintergrundthreads pro physischem Port (VE.Direct-Reader, -Sender). Mehrere Aggregator-Ports unterstützt.

**Modbus-Lader (EPever / Renogy / Morningstar)** — jeden Zyklus synchron abgefragt (öffnen → lesen → schließen), wie eSmart3, nicht threaded. Ein eigenständiger Modbus-RTU-Leser (CRC16, Request/Response-Framing) ist auf pyserial aufgesetzt, sodass keine externe Modbus-Bibliothek nötig ist. Jeder Typ nimmt ein optionales `unit` (Modbus-Slave-Adresse, Standard 1).
- `epever` — Tracer-AN / Tracer-BN (und LS-B). Input-Register (Funktion 4) ab 0x3100, Werte ×100, 115200 8N1. Liest PV V/P, Batterie V/I, Batterie- und Innentemperatur, SOC und Ladestatus.
- `renogy` — Rover / Rover Elite / Adventurer / Wanderer. Holding-Register (Funktion 3) 0x0100–0x0109, 9600 8N1. Die Batterietemperatur nutzt ein Vorzeichen-Flag-Byte (Bit 7 = negativ). Liest SOC, Batterie V/I, Lastleistung, PV V/P, Controller- und Batterietemperatur.
- `morningstar` — TriStar MPPT 45/60. RAM-Register mit Festkomma-Skalierung: V_PU (0x0000/1) und I_PU (0x0002/3) werden zuerst gelesen, dann angewandt als `n·V_PU·2⁻¹⁵` (Spannung), `n·I_PU·2⁻¹⁵` (Strom), `n·V_PU·I_PU·2⁻¹⁷` (Leistung). 9600 8N1. HINWEIS: EIA-485 gibt es nur beim TS-MPPT-60/M; der TS-MPPT-45 hat nur RS-232 und kann keinen RS485-Bus teilen.

EPever, Renogy und Morningstar liefern Innen- und Batterietemperatur und können für Temperaturalarme genutzt werden.

Pro Port-Eintrag wird ein Modbus-Lader unterstützt (der conf-Schlüssel ist der Port-Pfad). Mehrere Modbus-Lader an einem physischen RS485-Bus (Multi-Drop mit unterschiedlichen `unit`-Adressen) sind elektrisch möglich, werden aber von der Konfigurationsstruktur noch nicht unterstützt — jedem Modbus-Lader einen eigenen Port geben.

**PORT-Fehler und PPV-Decay.** Jedes Ladegerät, dessen Port ausfällt (Serialfehler, Timeout oder AGG-Stale-Timeout), bekommt seinen `mppt_data`-Eintrag durch `{'CS': 'PORT'}` ersetzt. `combine_charger_data` erkennt das und trägt statt null den zuletzt bekannten PPV-Wert bei — reduziert um 10% pro Zyklus. Die Einspeisung wird damit bei einem Laderausfall nicht sofort abgeschnitten, sondern klingt sanft ab: nach ca. 22 Zyklen (≈22 s bei 1 s/Zyklus) sind noch unter 14% des ursprünglichen Werts übrig, nach ca. 45 Zyklen unter 1%. Erholt sich das Gerät und liefert wieder Live-Daten, wird sofort der echte gemessene PPV verwendet und der Decay zurückgesetzt. Der abklingende Wert wird in der PV-Spalte des betroffenen Geräts angezeigt (neben `PORT ERROR` in der mode-Spalte), sodass das Ausklingen pro Gerät sichtbar ist, nicht nur in der Gesamtsumme.

**Kombinierte Daten** — PPV, Vbat, Ibat, Pload über alle Geräte aggregiert. Vbat wird gemittelt (Werte unter 2,0 V/Zelle ausgenommen); fehlt in einem Zyklus jede plausible Messung, wird der zuletzt gültige Vbat gehalten. PPV, Ibat und Pload werden summiert. Pload ist die echte Summe der tatsächlich gemessenen Lastwerte aller Geräte.

---

## MPPT-Leistungssteuerung

`set_victron_power(device_key, watts)` — einheitliche Schnittstelle für AGG- und konventionelle Victron-Ports.

**AGG-Pfad** — `VEDirect.set_watts(ser, watts)` → Firmware sendet `SET <SER#> <watts>` → wandelt W→A um (`reg = round(watts / Vbat × 10)`, Register `0x2015`, 0,1A), schreibt und verifiziert per Readback. `VBAT_FALLBACK = 24V` bis erste Vbat-Messung vorliegt.

**Konventioneller Pfad** — zeroinput repliziert die Firmware-Sequenz nach jedem vollständigen VE.Direct-Block: SET-HEX-Frame → ACK (400 ms Timeout) → GET-Readback → Vergleich. Befehle in `_victron_cmd_queues[port]` (maxsize=1) gepuffert.

---

## MultiPlus / VE.Bus-Steuerung (`vebus.py`)

Der MultiPlus wird direkt über das VE.Bus MK2-Protokoll per MK2/MK3-USB-Adapter mit 2400 Baud gesteuert — kein GX-Gerät. `vebus.py` ist von martiby/multiplus2 (MIT) abgeleitet und an den zeroinput-Stil angepasst.

Startsequenz pro Gerät: Port öffnen → Versionsabfrage (weich) → `init_address` (das eigentliche Verbindungs-Gate) → `scan_ess_assistant`, das die Assistant-RAM-Records ab ID 128 durchläuft, um den ESS-Assistant zu finden und die Sollwert-RAM-ID automatisch zu ermitteln (robust über Modelle, nicht fest auf 131). Schlägt ein Schritt fehl, bleibt der Treiber inaktiv und der Rest von zeroinput läuft weiter.

Jeden Zyklus schreibt der Treiber den ESS-Leistungssollwert per `CommandWriteViaID` (0x37) mit Flag `0x02` (**nur RAM**, kein EEPROM-Verschleiß). Der Sollwert muss < 60 s erneuert werden; zeroinputs ~1-s-Schleife garantiert das. Alle VE.Bus-Steuerpunkte liegen im RAM, sekündliches Schreiben ist also bauartbedingt sicher — das ist der entscheidende Unterschied zu EEPROM-basierten Hybriden.

**Vorzeichenkonvention.** Der Treiber rechnet in zeroinput-/Soyosource-Begriffen: `set_power(positiv) = Einspeisung`. Intern wird zur Victron-Konvention (positiv = laden) negiert. `mk3_ess_sign: -1` dreht die Richtung, falls Verdrahtung/CT-Platzierung sie umgekehrt meldet. `sleep()` schreibt 0 W (Passthrough). `read_status()` liefert `Pac` (Einspeisung positiv), `Vbat`, `Ibat`, `soc`, `out_p` für Monitoring.

---

## display_mppt_data

Kopfzeile: `port  name  W PV  %PVp  V bat  I bat  mode  Pload  Iload  age  Tint  Text`

Layout in der Modulkonstante `_MPPT_FMT` definiert (verwendet für Kopfzeile, Datenzeilen und Temperatursensor-Zeilen). REC-Ausgabe über Hilfsfunktion `_drain_rec_msgs()` verzögert.

- `%PVp` — `PPV / pvp × 100`; bei `combined` Summe aller Geräte-`pvp`; leer wenn `pvp` nicht konfiguriert. Der combined-Wert wird zusätzlich als `mppt_data['combined']['PVperc']` gespeichert und ist als vzlogger-Kanal exportierbar.
- `Iload` — Victron `LOAD=EIN`: Strom in A; `LOAD=AUS`: `OFF`; eSmart3: Strom wenn `Iload > 0`, sonst leer
- `mode` — Victron: OFF/FAULT/BULK/ABSORB/FLOAT/EQUAL/START/RECOND/EXTCON; eSmart3: WAIT/MPPT/BULK/FLOAT/PRE; beide: `PORT ERROR` bei `CS='PORT'`
- Unkonfigurierte AGG-Geräte: `<SER#>  <Port-Name>  UNCONFIGURED`
- REC-Meldungen (verbose) nach der `power request`-Zeile via `_rec_msgs`-Queue ausgegeben

---

## Prediktor

Die Konfigurationsschlüssel des Prediktors (`load_prediction`, `min_spread_w`,
`predictor_log`) und sein internes Verhalten sind vollständig in
**[predictor_spec_de.md](predictor_spec_de.md)** dokumentiert.

---

## Temperaturalarme

Konfiguriert im conf-Block `alarms` (getrennt von `chargers`), als Schlüssel der Gerätename. Gilt für eSmart3-, EPever-, Renogy- und Morningstar-Geräte (interner + externer Sensor) und AGG-`temp_sensor`-Geräte (nur externer Sensor).

Jeder Sensor unterstützt zwei unabhängige Alarme:
- **`int_hi` / `ext_hi`** — löst aus wenn `temp > Schwellwert`
- **`int_lo` / `ext_lo`** — löst aus wenn `temp < Schwellwert`

Jeder Alarm hat einen `_cmd` (Shell-Befehl) und ein `_interval` (Wiederholungsintervall in Sekunden, Standard 300). Ein Alarm ist nur aktiv wenn Schwellwert und Befehl gesetzt sind. Schwellwerte dürfen negativ oder null sein.

---

## Datenprotokollierung

zeroinput schreibt eigene Werte (Einspeiseleistung, Nullverschiebung, Batteriespannung, PV-Leistung, Temperaturen) über einen dateibasierten Kanal (`/tmp/vz/output_to_vz.log`) zurück an vzlogger. vzlogger liest diese aus und protokolliert sie zusammen mit allen anderen Kanälen in die Volkszähler-Datenbank, was eine einheitliche Sicht auf die Anlage ermöglicht.

Die Kanalzuordnung ist in `vz_channels` definiert (in der Weboberfläche editierbar).

---

## Konfiguration und Hot-Reload

`zeroinput.conf` wird jeden Zyklus auf Änderungen überwacht. Die meisten Schlüssel werden sofort beim Speichern wirksam. Schlüssel, die einen Neustart erfordern: `chargers`, `inverters`, `vzlogger_log_file`, `persistent_vz_file`. Die Blöcke `chargers` und `inverters` sind strukturell — Lader-Lesethreads und Wechselrichter-Treiber werden einmal beim Start aufgebaut, Änderungen erfordern daher einen Neustart (Restart-Tab / `/api/restart`).

`predictor.py` wird separat überwacht; Änderungen (einschließlich Konfigurationsvariablen) werden durch Neuladen des Moduls angewendet, ohne zeroinput neu zu starten.

---

## Weboberfläche (`webconfig.py`)

HTTP-Server, gestartet mit `-httpd`. Bietet:
- **zeroinput.conf-Tab** — Live-Bearbeitung aller hot-reloadbaren Schlüssel; Pfad-Schlüssel zeigen Neustart-Hinweis. Der `/api/conf`-Endpoint ist strukturunabhängig und bearbeitet das JSON direkt, die Blöcke `chargers` und `inverters` sind hier also als Rohwerte editierbar.
- **chargers-Tab** — strukturierter Editor für MPPT-Lader und Temperatursensoren (eSmart3, Victron, Aggregator mit SER#-Tabelle, EPever, Renogy, Morningstar). Modbus-Typen zeigen ein `unit`-Feld (Slave-Adresse). Neustart nach dem Speichern erforderlich. PVp-Feld bei `type: temp` ausgeblendet. Pro Sensor wählbare Hitzeschutz-Markierung (nur einer gültig).
- **inverters-Tab** — strukturierter Editor für Einspeise-Wechselrichter (Typ, Port, Stufen-Checkboxen, count, max/min-Leistung, ESS-Vorzeichen). Prüft beim Speichern auf Leistungslücken. Neustart erforderlich.
- **VZ-Kanäle-Tab** — Tabelleneditor für Volkszähler-Kanalzuordnung
- **timer.txt-Tab** — Texteditor für Entladeregeln; Hinweis wenn Timer in conf deaktiviert
- **Restart-Tab** — Neustart der Dienste zeroinput und vzlogger per Knopf (je ein Button; setzt passende sudoers-Einträge voraus)
- **Status-Tab** — Live-HTML-Statusseite (nur mit `-web`)

---

## Ausgabemodi

| Flag | Wirkung |
|---|---|
| `-v` | Ausführliche Konsolenausgabe jeden Zyklus |
| `-web` | HTML-Statusseite (`zeroinput.html`) jeden Zyklus schreiben |
| `-httpd` | Web-Konfigurationsserver starten |
| `-no-input` | Gesamte Einspeisung deaktivieren |
| `-test-alarm` | Alarmbefehl ausführen und beenden |
