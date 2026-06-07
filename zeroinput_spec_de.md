# zeroinput – Funktionsspezifikation
*v2.1*

## Zweck

zeroinput steuert einen oder mehrere Batterie-Netzwechselrichter für Nulleinspeisung (Eigenverbrauchsoptimierung). Der Stromzähler wird kontinuierlich ausgelesen; die Wechselrichterleistung wird jeden Zyklus so angepasst, dass der Zähler möglichst nahe an null bleibt — weder Bezug noch Einspeisung.

Ab v2.1 ist die Wechselrichterseite eine generische Mehrtyp-Treiberarchitektur: Soyosource-Limiter-Wechselrichter und Victron MultiPlus (ESS) werden unterstützt, in beliebiger Mischung und Anzahl, verteilt auf zwei Leistungsstufen. Das frühere Einzeltyp-Modell („nur Soyosource") entfällt.

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

**Lückenprüfung.** Beim Start (und beim Speichern der Inverter-Konfiguration in der Weboberfläche) durchläuft zeroinput die angeforderte Leistung von der kleinsten `min_power` bis `max_input_power` entlang des realen Regelpfads und meldet jedes Leistungsband, das keine Wechselrichterkombination liefern kann. In Stufe 2 bewegt die Gleichteilung die gelieferte Leistung in Schritten von etwa der Anzahl aktiver Einheiten — das ist die immanente Regelauflösung und keine Lücke; nur größere Sprünge werden gemeldet. Lückenlose Abdeckung ist Pflicht: eine Lücke (z.B. ein Stufe-1-Gerät endet bei 900 W, während das einzige Stufe-2-Gerät `min_power` 1500 W hat) erzeugt eine unübersehbare Warnung. Die Einspeisung wird über `max_input_power = 0` oder den Timer deaktiviert, nicht durch das Stehenlassen von Lücken.

Die Anzahl aktiver Einheiten (Summe der `count` über Gruppen, die Leistung erhalten haben) ersetzt das alte `n_active_inverters` / `total_number_of_inverters` und fließt in Rampenverhalten und Pload-Projektion.

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

- **Spannungskurve** (48–51 V): Batterieentladeleistung wird durch eine Leistungskurve begrenzt; volle Entladung ab 51 V erlaubt.
- **Unterspannungsschutz**: unter 48 V wird der Wechselrichter für 1 Minute deaktiviert.
- **Spannungskorrektur** (`bat_voltage_const`): kompensiert Spannungsabfall unter Last mit einem konfigurierbaren Faktor.
- **Freie Einspeisung** (`free_power_export`): bei Batteriespannungen über 54,5 V wird überschüssige Energie gezielt ins Netz eingespeist; skaliert linear bis `max_input_power` bei der MPPT-Float-Spannung (~57 V).

---

## PV-Durchleitung

Die verfügbare PV-Leistung wird aus einem gleitenden Mittelwert der jüngsten MPPT-Ausgabe abzüglich eines Wirkungsgradspalts (`PV_to_AC_efficiency`) geschätzt. Die Leistungsanforderung wird auf `PV_power + allowed_battery_discharge` begrenzt, um ungewollten Batteriebezug zu vermeiden.

---

## Sägezahnverhinderung

Schwingungen in der Sendehistorie (wechselnde hohe/niedrige Anforderungen) werden durch Vergleich aufeinanderfolgender Paare erkannt. Bei bestätigtem Sägezahnverhalten ersetzt der Durchschnitt der letzten vier Werte die aktuelle Anforderung.

---

## Rampenverhalten

Große plötzliche Zähleränderungen (> 400 W) lösen einen Rampenmodus aus: die Anforderung wird für `2 + Anzahl aktiver Einheiten` Zyklen auf dem Sprungwert gehalten, bevor die normale Regelung wieder einsetzt. Die erste Aufwärtsrampe nach einer stabilen Phase wird verworfen, um kurze, wenig bedeutsame Lastspitzen — wie das Anlaufen eines Kühlschrankkompressors — herauszufiltern, die sonst eine vollständige Rampenantwort auslösen würden.

---

## Lastprediktor (`predictor.py`)

Der Lastprediktor erkennt zyklische Lasten (Waschmaschine, Spülmaschine, Herd) und
wiederkehrende kurze Hochlast-Spitzen und beugt der Einspeisung vor, die sie sonst verursachen
würden. Er betreibt zwei Mechanismen: k-Means-Pegelstabilisierung für zyklische Lasten und
einen Peak-/Override-Mechanismus für wiederkehrende kurze Spitzen.

Er ist ein optionales, austauschbares Modul. zeroinput benötigt nur eine `LoadPredictor`-Klasse
mit `update(Ls_read, last2_send)`, `reload_conf(conf)`, `status()` und den Attributen `enabled`,
`offset` und `ramp_override_by_predictor`. Eigene Strategien können durch Ersetzen von
`predictor.py` implementiert werden, ohne zeroinput selbst zu ändern.

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

**eSmart3** — per RS485 jeden Zyklus abgefragt (Statusanforderung, Antwort parsen). Prüfsumme validiert (`(0xaa + sum(data)) & 0xFF == 0`) — fehlerhafte Pakete werden verworfen. Unterstützt Temperaturüberwachung und Alarme pro Gerät, Lastport-Daten (`Iload`, `Vload`, `Pload`) und `pvp` (PV-Spitzenleistung W) für `%PVp`-Anzeige. Mehrere Geräte unterstützt.

**Victron MPPT (konventionell)** — per VE.Direct-Protokoll in einem dedizierten Hintergrundthread pro Gerät ausgelesen. `IL` (Laststrom) und `LOAD` (EIN/AUS) für Lastport-Anzeige geparst. `Pload = IL × Vbat` bei Verfügbarkeit berechnet. `pvp` für `%PVp`-Anzeige gespeichert. Portausfall setzt `CS='PORT'` für `PORT ERROR`-Anzeige. Ein Thread pro Port.

**Victron MPPT (Aggregator)** — mehrere MPPTs an einem RS485-Port via `readtext_sendhex`-Firmware ([VE.Direct-Aggregator](https://github.com/E-t0m/ve.direct-aggregator), Arduino Mega 2560 / Teensy 4.1). Verwaltet durch `VEDirectBridge`, das `ve_aggregator.VEDirect` über den `on_block`-Callback einbindet — geparste Blöcke werden direkt mit Block-Rate in `mppt_data` geschrieben, kein Patching, kein doppeltes Parsen, kein Polling-Thread. [`ve_aggregator.py`](https://github.com/E-t0m/ve.direct-aggregator) muss im gleichen Verzeichnis wie `zeroinput.py` liegen. Geräte werden per SER# identifiziert (`mppt_type: victron_agg`). Geräte-`pvp` in `devices[ser]['pvp']`. Geräte mit `type: temp` in der conf werden zu `mppt_type: temp_sensor` — DS18B20-Temperaturblöcke (Feld `TEMP`) werden als `ext_temp` in `mppt_data` geschrieben und in einer separaten Zeile unterhalb der Haupttabelle angezeigt. `check_stale()` wird jeden Zyklus aufgerufen — ersetzt `mppt_data[key]` atomar durch `{'CS': 'PORT'}` für Geräte, die innerhalb von `device_timeout` nicht gesehen wurden, und nullt dabei alle Messwerte, um veraltete Daten in `combine_charger_data` und `set_victron_power` zu verhindern. Unkonfigurierte SER# werden als `UNCONFIGURED` angezeigt. Zwei Hintergrundthreads pro physischem Port. Mehrere Aggregator-Ports unterstützt.

**Kombinierte Daten** — PPV, Vbat, Ibat, Pload über alle Geräte aggregiert. Vbat gemittelt. Pload nur von Lader-Ports aufsummiert, die sich eine Leitung mit einem Soyosource-Wechselrichter teilen (Victron-DC-Lastports ausgenommen), multipliziert mit der Anzahl aktiver Einheiten. Hinweis: bei gemischten Leistungsklassen (z.B. 900 W Soyosource + 2400 W MultiPlus) ist diese Einzelwert-Projektion nur näherungsweise.

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

Konfiguriert im conf-Block `alarms` (getrennt von `chargers`), als Schlüssel der Gerätename. Gilt für eSmart3-Geräte (interner + externer Sensor) und AGG-`temp_sensor`-Geräte (nur externer Sensor).

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
- **chargers-Tab** — strukturierter Editor für MPPT-Lader und Temperatursensoren (eSmart3, Victron, Aggregator mit SER#-Tabelle). Neustart nach dem Speichern erforderlich. PVp-Feld bei `type: temp` ausgeblendet.
- **inverters-Tab** — strukturierter Editor für Einspeise-Wechselrichter (Typ, Port, Stufen-Checkboxen, count, max/min-Leistung, ESS-Vorzeichen). Prüft beim Speichern auf Leistungslücken. Neustart erforderlich.
- **VZ-Kanäle-Tab** — Tabelleneditor für Volkszähler-Kanalzuordnung
- **timer.txt-Tab** — Texteditor für Entladeregeln; Hinweis wenn Timer in conf deaktiviert
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
