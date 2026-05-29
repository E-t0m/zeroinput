# zeroinput – Funktionsspezifikation
*v2.0*

## Zweck

zeroinput steuert einen oder mehrere Soyosource-Netzwechselrichter für Nulleinspeisung (Eigenverbrauchsoptimierung). Der Stromzähler wird kontinuierlich ausgelesen; die Wechselrichterleistung wird jeden Zyklus so angepasst, dass der Zähler möglichst nahe an null bleibt — weder Bezug noch Einspeisung.

---

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

**Einzelwechselrichter-Modus** — wenn `power_demand ≤ single_inverter_threshold` oder nur ein Wechselrichter konfiguriert ist: Anforderung vollständig an `basic_load_inverter_port`.

**Mehrwechselrichter-Modus** — oberhalb des Schwellwerts: `power_demand / total_number_of_inverters` wird an jeden konfigurierten RS485-Port gesendet. Parallel verdrahtete Wechselrichter antworten auf dasselbe Paket und speisen ihren Anteil unabhängig ein — daher muss `total_number_of_inverters` der tatsächlichen Anzahl physischer Wechselrichter entsprechen.

Der Übergang zwischen den Modi ist hysteresekontrolliert über `multi_inverter_wait` (History der letzten Anforderungswerte).

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

Große plötzliche Zähleränderungen (> 400 W) lösen einen Rampenmodus aus: die Anforderung wird für `2 + n_active_inverters` Zyklen auf dem Sprungwert gehalten, bevor die normale Regelung wieder einsetzt. Die erste Aufwärtsrampe nach einer stabilen Phase wird verworfen, um kurze, wenig bedeutsame Lastspitzen — wie das Anlaufen eines Kühlschrankkompressors — herauszufiltern, die sonst eine vollständige Rampenantwort auslösen würden.

---

## Lastprediktor (`predictor.py`)

Erkennt zyklische Lasten (Waschmaschine, Spülmaschine, Herd) mittels k-Means-Clustering auf der geschätzten Lasthistorie:

- Identifiziert zwei stabile Lastniveaus: **LOW** und **HIGH**
- Nach Bestätigung (≥ 4 Phasenübergänge) wird ein prädiktiver Offset angewendet, um den Wechselrichter unabhängig von der aktuellen Phase auf LOW-Niveau zu halten — die HIGH-Last bezieht ihre Zusatzleistung direkt aus dem Netz
- **Spitzenerkennung**: kurze, aber hohe wiederholte `Ls_read`-Lastspitzen lösen `ramp_override` aus, der den Wechselrichter auf LOW hält und die Spitze vollständig ignoriert. Begründung: diese Spitzen steigen und fallen schneller als der Wechselrichter rampen kann — wenn der Zielwert erreicht wäre, ist die Last bereits verschwunden und erzeugt erhebliche Einspeisung. Kein Reagieren ist besser.
- Setzt automatisch zurück bei anhaltend hoher Last (> `LONG_PEAK_MIN` s über Schwellwert)
- `STARTUP_S`, `SHORT_PEAK_MAX`, `LOG_FILE` sind Modulkonstanten in `predictor.py`, bei Dateiänderung via `reload_predictor_if_changed` neu geladen
- `min_spread_w`, `load_prediction` und `predictor_log` sind conf-Keys, ohne Modulneustart hot-reloadable aus `zeroinput.conf`

Das Prediktordesign ist bewusst offen und modular: zeroinput benötigt nur eine `LoadPredictor`-Klasse mit `update(Ls_read, last2_send)`, `reload_conf(conf)`, `status()` und den Attributen `enabled`, `offset` und `ramp_override_by_predictor`. Eigene Vorhersagestrategien können durch Ersetzen von `predictor.py` implementiert werden, ohne zeroinput selbst zu ändern.

---

## Entladetimer

Optionale zeitbasierte Steuerung über `timer.txt`. Jede Regel legt fest:
- **battery** — maximale Entladeleistung (W oder % von `max_bat_discharge`)
- **inverter** — maximale Einspeiseleistung (W oder % von `max_input_power`)
- **energy_Wh** — Energiebudget pro Timerperiode; nach Überschreitung stoppt Batterieentladung, nur PV-Durchleitung läuft weiter

Regeln werden der Reihe nach aktiviert; `0000-00-00` als Datum gilt täglich.

---

## MPPT-Laderegler-Unterstützung

**eSmart3** — per RS485 jeden Zyklus abgefragt (Statusanforderung, Antwort parsen). Prüfsumme validiert (`(0xaa + sum(data)) & 0xFF == 0`) — fehlerhafte Pakete werden verworfen. Unterstützt Temperaturüberwachung und Alarme pro Gerät, Lastport-Daten (`Iload`, `Vload`, `Pload`) und `pvp` (PV-Spitzenleistung W) für `%PVp`-Anzeige. Mehrere Geräte unterstützt.

**Victron MPPT (konventionell)** — per VE.Direct-Protokoll in einem dedizierten Hintergrundthread pro Gerät ausgelesen. `IL` (Laststrom) und `LOAD` (EIN/AUS) für Lastport-Anzeige geparst. `Pload = IL × Vbat` bei Verfügbarkeit berechnet. `pvp` für `%PVp`-Anzeige gespeichert. Portausfall setzt `CS='PORT'` für `PORT ERROR`-Anzeige. Ein Thread pro Port.

**Victron MPPT (Aggregator)** — mehrere MPPTs an einem RS485-Port via `readtext_sendhex`-Firmware ([VE.Direct-Aggregator](https://github.com/E-t0m/ve.direct-aggregator), Arduino Mega 2560 / Teensy 4.1). Verwaltet durch `VEDirectBridge`, das `ve_aggregator.VEDirect` über den `on_block`-Callback einbindet — geparste Blöcke werden direkt mit Block-Rate in `mppt_data` geschrieben, kein Patching, kein doppeltes Parsen, kein Polling-Thread. [`ve_aggregator.py`](https://github.com/E-t0m/ve.direct-aggregator) muss im gleichen Verzeichnis wie `zeroinput.py` liegen. Geräte werden per SER# identifiziert (`mppt_type: victron_agg`). Geräte-`pvp` in `devices[ser]['pvp']`. Geräte mit `type: temp` in der conf werden zu `mppt_type: temp_sensor` — DS18B20-Temperaturblöcke (Feld `TEMP`) werden als `ext_temp` in `mppt_data` geschrieben und in einer separaten Zeile unterhalb der Haupttabelle angezeigt. `check_stale()` wird jeden Zyklus aufgerufen — ersetzt `mppt_data[key]` atomar durch `{'CS': 'PORT'}` für Geräte, die innerhalb von `device_timeout` nicht gesehen wurden, und nullt dabei alle Messwerte, um veraltete Daten in `combine_charger_data` und `set_victron_power` zu verhindern. Unkonfigurierte SER# werden als `UNCONFIGURED` angezeigt. Zwei Hintergrundthreads pro physischem Port. Mehrere Aggregator-Ports unterstützt.

**Kombinierte Daten** — PPV, Vbat, Ibat, Pload über alle Geräte aggregiert. Vbat gemittelt. Pload nur von Ports mit `inverter: soyosource` aufsummiert (Victron-DC-Lastports ausgenommen, sofern kein Wechselrichter konfiguriert), multipliziert mit `n_active_inverters`.

---

## MPPT-Leistungssteuerung

`set_victron_power(device_key, watts)` — einheitliche Schnittstelle für AGG- und konventionelle Victron-Ports.

**AGG-Pfad** — `VEDirect.set_watts(ser, watts)` → Firmware sendet `SET <SER#> <watts>` → wandelt W→A um (`reg = round(watts / Vbat × 10)`, Register `0x2015`, 0,1A), schreibt und verifiziert per Readback. `VBAT_FALLBACK = 24V` bis erste Vbat-Messung vorliegt.

**Konventioneller Pfad** — zeroinput repliziert die Firmware-Sequenz nach jedem vollständigen VE.Direct-Block: SET-HEX-Frame → ACK (400 ms Timeout) → GET-Readback → Vergleich. Befehle in `_victron_cmd_queues[port]` (maxsize=1) gepuffert.

---

## display_mppt_data

Kopfzeile: `port  name  W PV  %PVp  V bat  I bat  mode  Pload  Iload  age  Tint  Text`

Layout in der Modulkonstante `_MPPT_FMT` definiert (verwendet für Kopfzeile, Datenzeilen und Temperatursensor-Zeilen). REC-Ausgabe über Hilfsfunktion `_drain_rec_msgs()` verzögert.

- `%PVp` — `PPV / pvp × 100`; bei `combined` Summe aller Geräte-`pvp`; leer wenn `pvp` nicht konfiguriert
- `Iload` — Victron `LOAD=EIN`: Strom in A; `LOAD=AUS`: `OFF`; eSmart3: Strom wenn `Iload > 0`, sonst leer
- `mode` — Victron: OFF/FAULT/BULK/ABSORB/FLOAT/EQUAL/START/RECOND/EXTCON; eSmart3: WAIT/MPPT/BULK/FLOAT/PRE; beide: `PORT ERROR` bei `CS='PORT'`
- Unkonfigurierte AGG-Geräte: `<SER#>  <Port-Name>  UNCONFIGURED`
- REC-Meldungen (verbose) nach der `power request`-Zeile via `_rec_msgs`-Queue ausgegeben

---

## Wechselrichter-Fehleralarm

`inverter_fault_alarm: {cmd, interval}` — löst im reinen PV-Modus aus (`bat_discharge == 0`, `pv_power > 100 W`, alle Wechselrichter aktiv), wenn `avg(long_send_history) > pv_power × n/(n-1) × 0,85`. Ein ausgefallener Wechselrichter veranlasst zeroinput, die Anforderung in Richtung `pv_power × n/(n-1)` zu rampen — dieses Verhältnis ist das Erkennungssignal. Gibt verbose-Warnung aus; führt `cmd` maximal alle `interval` Sekunden aus.

---

## Prediktor

`predictor_log: true/false` (conf, hot-reloadable) — steuert `/tmp/predictor.log` und die Ausgabe der Spaltenköpfe beim Start. Standard: `true`.

`min_spread_w` (conf, hot-reloadable) — Mindestspreizung zwischen LOW- und HIGH-k-Means-Zentroid für die Prediktoraktivierung. Standard: `150` W. Bei zu geringer Lastspreizung deaktiviert sich der Prediktor und zeroinput fällt auf Reaktivregelung zurück.

`_kmeans2` lehnt unimodale Verteilungen ab: beide Gruppen müssen ≥ 15 % der History-Werte enthalten, sonst wird `None, None` zurückgegeben und der Prediktor deaktiviert sich. Verhindert fehlerhafte Offsets während Lastübergängen.

`MAX_HIST = 60` und `TRANSITIONS_MIN = 4` sind Modulkonstanten in `predictor.py`.

---

## Temperaturalarme

Pro eSmart3-Gerät, unabhängig für internen und externen Sensor:
- Schwellwert (°C) und Shell-Befehl pro Alarm konfigurierbar
- Alarm wird ausgelöst, wenn die Temperatur den Schwellwert überschreitet **und** ein Befehl gesetzt ist
- Individuelles Wiederholungsintervall pro Alarm (`int_interval`, `ext_interval`)
- Kein globaler Aktivierungsschalter — ein Alarm ist aktiv, wenn er konfiguriert ist

---

## Datenprotokollierung

zeroinput schreibt eigene Werte (Einspeiseleistung, Nullverschiebung, Batteriespannung, PV-Leistung, Temperaturen) über einen dateibasierten Kanal (`/tmp/vz/output_to_vz.log`) zurück an vzlogger. vzlogger liest diese aus und protokolliert sie zusammen mit allen anderen Kanälen in die Volkszähler-Datenbank, was eine einheitliche Sicht auf die Anlage ermöglicht.

Die Kanalzuordnung ist in `vz_channels` definiert (in der Weboberfläche editierbar).

---

## Konfiguration und Hot-Reload

`zeroinput.conf` wird jeden Zyklus auf Änderungen überwacht. Die meisten Schlüssel werden sofort beim Speichern wirksam. Schlüssel, die einen Neustart erfordern: `rs485`, `basic_load_inverter_port`, `vzlogger_log_file`, `persistent_vz_file`.

`predictor.py` wird separat überwacht; Änderungen (einschließlich Konfigurationsvariablen) werden durch Neuladen des Moduls angewendet, ohne zeroinput neu zu starten.

---

## Weboberfläche (`webconfig.py`)

HTTP-Server, gestartet mit `-httpd`. Bietet:
- **zeroinput.conf-Tab** — Live-Bearbeitung aller hot-reloadbaren Schlüssel; Pfad-Schlüssel zeigen Neustart-Hinweis
- **RS485-Tab** — strukturierter Editor für RS485-Port- und Gerätekonfiguration einschließlich Alarmschwellwerte, Befehle und Intervalle; Neustart-Hinweis beim Speichern
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
