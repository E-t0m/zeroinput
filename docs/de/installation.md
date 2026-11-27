# zeroinput – Installationsanleitung
*v2.3*

## Überblick

zeroinput ist ein Python-Skript für PV-Nulleinspeisung (Eigenverbrauchsoptimierung) auf einem Linux-Gerät.
Es liest den Stromzähler über vzlogger aus und steuert einen oder mehrere Batterie-Netzwechselrichter über RS485 oder USB — je nach Last zwischen Leistungsstufen umschaltend. Unterstützte Wechselrichtertypen: Soyosource-GTN-Limiter-Serie und Victron MultiPlus-II (ESS über MK3-USB-Adapter).

zeroinput nutzt **vzlogger** aus dem [volkszaehler.org](https://www.volkszaehler.org/)-Projekt auf zwei Arten:
- als **Zählerdatenquelle** — vzlogger liest den Stromzähler und streamt den Wirkleistungswert (`1-0:16.7.0`) über ein FIFO an zeroinput
- als **Datenlogger** — zeroinput schreibt eigene Werte (Einspeiseleistung, Batteriespannung, PV-Leistung, …) zurück an vzlogger, der sie zusammen mit den anderen volkszaehler.org-Kanälen protokolliert

zeroinput fügt sich damit natürlich in eine bestehende volkszaehler.org-Installation ein.

**Dateien:**

| Datei | Beschreibung |
|---|---|
| `zeroinput.py` | Hauptskript |
| `input_power_staging.py` | Zweistufige Leistungsverteilungslogik |
| `inverter_drivers.py` | Wechselrichter-Treiber-Abstraktion (Soyosource, Victron MK3) |
| `charger_drivers.py` | MPPT-Ladertreiber (eSmart3, Victron, EPever, Renogy, Morningstar) |
| [`vebus.py`](https://github.com/martiby/multiplus2) | VE.Bus-Protokolltreiber von martiby/multiplus2 (optional, nur für Victron MultiPlus-II); nicht Teil des Repositorys, wird bei der Installation geladen |
| `predictor.py` | Lastprediktor (k-Means, optional) |
| `webconfig.py` | HTTP-Konfigurationsserver (optional, benötigt `-httpd`) |
| [`ve_aggregator.py`](https://github.com/E-t0m/ve.direct-aggregator) | VE.Direct-Aggregator-Client (optional, nur für `victron_agg`) |
| `zeroinput.conf.starter` | Referenzkonfiguration mit allen verfügbaren Geräteklassen, Vorlage für `zeroinput.conf` |
| `zeroinput.conf` | Eigene Konfiguration (JSON), aus `zeroinput.conf.starter` erstellt; nicht Teil des Repositorys |
| `zeroinput_webconfig.html` | Weboberfläche |
| `zeroinput.service` | systemd-Dienst |
| `zerooutput.sh` | Live-Status im Terminal, zeigt die von `-web` geschriebene `zeroinput.html` |
| `timer.txt.example` | Vorlage für den Entladetimer `timer.txt` (optional); `timer.txt` ist nicht Teil des Repositorys |
| `dirt_shift/` | CO₂-geführte Entladesteuerung (optional), siehe [dirt_shift-Installation](dirt_shift_installation.md) |
| `tibber/` | Preisgeführte Entladesteuerung mit Tibber (optional, im Teststadium), siehe [tibber](../en/tibber.md) |
| `tools/` | Hilfsskripte |
| `tests/` | Tests ohne Hardware |
| `docs/` | Dokumentation |

---

## Voraussetzungen

### Hardware

- Ein Linux-Gerät mit USB
- Ein oder mehrere Wechselrichter:
  - **Soyosource GTN Limiter** (GTN-1000/1200/2000 mit RS485-Limiter-Port) — per USB-RS485-Adapter angebunden
  - **Victron MultiPlus / MultiPlus-II** (VE.Bus, Mikroprozessor 2. Generation) mit MK2-USB- oder MK3-USB-Adapter — ESS-Assistant muss in VEConfigure konfiguriert sein
- MPPT-Laderegler: eSmart3 (RS485), Victron MPPT (VE.Direct oder Aggregator), EPever Tracer-AN/-BN, Renogy Rover/Elite/Adventurer oder Morningstar TriStar MPPT (Modbus RTU über RS485, einige Modelle über RS232)
- RS485-Adapter (USB) — Verdrahtung: **A+ an A+, B- an B-** an allen Geräten; für Laderegler mit RS232-Port ein USB-RS232-Adapter
- Stromzähler mit erweiterter Datenausgabe (PIN beim Messstellenbetreiber anfordern)
  Der OBIS-Datensatz `1-0:16.7.0` (Netz-Wirkleistung) **muss** vorhanden sein
- Zähler-Schnittstelle kompatibel mit vzlogger (IR-Lesekopf, Shelly 3EM / Pro 3EM, Modbus-Zähler, …)

#### eSmart3 und eSmart4 unter anderen Marken

Der eSmart3 (Hersteller Ipandee/I-Panda) wird unter vielen Marken verkauft. Typenbezeichnung, Handbuch oder Gehäuse weisen bei diesen Marken auf einen eSmart3 hin:

| Marke | Modell |
|---|---|
| Ipandee / I-Panda | eSmart3-20A bis eSmart3-60A |
| ohne Marke | eSmart3-40A (mit zeroinput getestet) |
| ACEHE | eSmart3-40A |
| anancooler | Esmart 3-40A |
| BRCU | Esmart3 (40A) |
| GT | ESmart3 40A |
| KR | Esmart3 (40A bis 60A) |
| KRXNY | Esmart3 (40A, 60A) |
| Lovsun | ESMART3 Series |
| MAIXI | eSmart3-40A |
| PowMr | Esmart3-40A-II, Esmart3-60A-II (andere PowMr-Regler wie der HHJ-60A sind keine eSmart3) |
| Ruoxiang | ESmart3 40A, eSmart3-60A |
| SameeHome | Esmart3-40A |
| SUPOELE | eSmart3-40A, eSmart3-60A, SP-Esmart3 |
| Temank | eSmart3-60A |
| VEVOR | 60A MPPT „eSmart 3 Series“ (mit zeroinput getestet) |
| Y-SOLAR | Esmart3-60A |

Der eSmart4 (Modellnummern ESF48L50, ESF48L60, ESF48H50, ESF48H60) spricht laut [Joba_ESmart3](https://github.com/joba-1/Joba_ESmart3) dasselbe Protokoll. Mit zeroinput ist er ungetestet; einzutragen ist er als `mppt_type: eSmart3`. Rückmeldungen sind willkommen. Bekannte Marken: Ipandee, AOJIA (Esmart4), ECO-WORTHY (ECOMPPT60A), iSunergy (ESF48L60), Solarepic (ESF48H60), SUPOELE (ESF48L60).

Möglicherweise baugleich, aber nur schwach belegt (mit zeroinput ungetestet, Rückmeldungen sind willkommen): Daweglop (Esmart3-40A), iSunergy (ESF48H80, ESF48H100), ECO-WORTHY (60A MPPT mit Bluetooth- und WLAN-Modul BW02).

RJ45-Buchse (eSmart3 und eSmart4): 1 RS485-A, 2 RS485-B, 3 und 4 frei, 5 und 6 GND, 7 und 8 +5 V.

#### Modbus-Laderegler unter anderen Marken

Die Renogy Rover sind Regler von SRNE, und SRNE-Regler werden auch unter weiteren Marken verkauft. Der Renogy Commander stammt von EPever (EPsolar). Typenbezeichnung oder Handbuch weisen bei diesen Marken auf dasselbe Gerät hin. Mit zeroinput sind sie ungetestet; Rückmeldungen sind willkommen.

| Marke | Modell | `mppt_type` |
|---|---|---|
| SRNE | ML2420, ML2430, ML2440, ML4860 | `renogy` |
| Rich Solar | ML2420, ML2430, ML2440, ML4830N15, ML4860 | `renogy` |
| PowMr | ML4860 (der PowMr HHJ-60A ist ein anderes Gerät) | `renogy` |
| Temank | ML4860 | `renogy` |
| Snaterm | ML4860 | `renogy` |
| EPsolar | Tracer-AN, Tracer-BN (EPsolar und EPever sind derselbe Hersteller) | `epever` |
| Renogy | Commander 60A (EPsolar iTracer IT6415ND) | `epever` |
| Ultisolar | eTracer ET4415BND, ET6415BND | `epever` |

Die Modbus-Beschreibung von EPever gilt auch für die Serien Tracer-A, XTRA, Triron, iTracer und eTracer; mit zeroinput sind sie ungetestet.

Der Renogy Tracer MPPT (z. B. CTRL-MPPT20) ist ein EPsolar Tracer der ersten Generation. Er spricht ein anderes Protokoll und wird nicht unterstützt.

Für Morningstar ist keine andere Marke bekannt.

Möglicherweise baugleich, aber nur schwach belegt (mit zeroinput ungetestet, Rückmeldungen sind willkommen): WindyNation TrakMax und Zenith Grape Solar (`renogy`, laut [solarthing](https://github.com/wildmountainfarms/solarthing) kompatibel), Weize (`renogy`, in einem Forenbeitrag als SRNE genannt), FANGPUSUN (`victron`, äußerlich wie Victron-Regler, VE.Direct-Port laut Forenbericht).

Einige SRNE-Regler (z. B. ML2420, Rich Solar RS-MPPT40) haben einen RS232-Port statt RS485, der Renogy Rover 60A hat beide. Ein RS232-Port braucht einen USB-RS232-Adapter statt eines RS485-Adapters. Welche Schnittstelle der Regler hat, steht im Handbuch.

### Software

```bash
sudo apt install git python3 python3-serial vzlogger
```

`vzlogger` liegt ab Debian 13 (trixie) im Paketarchiv. Auf älteren Systemen das Paket aus dem Repository des volkszaehler.org-Projekts installieren, siehe [vzlogger-Installation](https://wiki.volkszaehler.org/software/controller/vzlogger/installation_cpp-version).

Für den Victron MultiPlus-II wird zusätzlich `vebus.py` aus dem Projekt [martiby/multiplus2](https://github.com/martiby/multiplus2) gebraucht. Die Datei ist nicht Teil des Repositorys und wird bei der Installation in den Ordner von zeroinput geladen, festgelegt auf einen geprüften Stand. Sie braucht nur pyserial.

---

## Installation

### 1. Repository klonen

```bash
sudo useradd -m -s /bin/bash vzlogger   # falls noch nicht vorhanden
sudo usermod -aG dialout vzlogger       # RS485- und USB-Serial-Zugriff
sudo mkdir /opt/zeroinput
sudo chown vzlogger:vzlogger /opt/zeroinput
sudo -u vzlogger git clone https://github.com/E-t0m/zeroinput.git /opt/zeroinput
cd /opt/zeroinput
sudo -u vzlogger cp zeroinput.conf.starter zeroinput.conf
sudo -u vzlogger cp timer.txt.example timer.txt   # optional, für den Entladetimer
sudo -u vzlogger wget -O vebus.py https://raw.githubusercontent.com/martiby/multiplus2/f28e8dc3c002e21a9d71cd0b58308490e76da529/vebus.py   # nur für Victron MultiPlus-II
sudo ln -s /opt/zeroinput/zerooutput.sh /usr/local/bin/zerooutput.sh   # optional, Live-Status als Befehl
```

zeroinput läuft direkt aus dem geklonten Ordner `/opt/zeroinput`. Die eigene Konfiguration (`zeroinput.conf`, `timer.txt`) und alle zur Laufzeit geschriebenen Dateien stehen in der `.gitignore`; eine Aktualisierung per `git pull` lässt sie unberührt.

### 2. Persistente Gerätenamen per udev (empfohlen)

Um zu verhindern, dass sich Ports nach einem Neustart ändern, feste Gerätenamen per udev-Regeln vergeben.
`/etc/udev/rules.d/99-zeroinput.rules` erstellen:

```
# IR-Lesekopf (cp210x-Chip)
SUBSYSTEMS=="usb-serial", DRIVERS=="cp210x", SYMLINK+="lesekopf"
# RS485-Adapter (ch341-Chip)
SUBSYSTEMS=="usb-serial", DRIVERS=="ch341-uart", SYMLINK+="rs485"
# MK3-USB-Adapter (FTDI-Chip)
SUBSYSTEMS=="usb-serial", DRIVERS=="ftdi_sio", SYMLINK+="mk3usb"
```

Mit mehreren identischen Adaptern per USB-Port unterscheiden:

```
SUBSYSTEMS=="usb", ATTRS{devpath}=="1.1", SYMLINK+="rs485a"
SUBSYSTEMS=="usb", ATTRS{devpath}=="1.3", SYMLINK+="rs485b"
```

Dann: `sudo udevadm control --reload-rules && sudo udevadm trigger`

### 3. FIFO und Verzeichnis anlegen

Das FIFO muss vor dem Start von vzlogger **und** zeroinput vorhanden sein. Folgende Zeilen in `/etc/systemd/system/vzlogger.service` ergänzen:

```ini
[Service]
ExecStartPre=/bin/mkdir -p /tmp/vz
ExecStartPre=/bin/bash -c 'test -p /tmp/vz/vzlogger.fifo || mkfifo /tmp/vz/vzlogger.fifo'
```

Dann:

```bash
sudo systemctl daemon-reload
sudo systemctl restart vzlogger
```

Damit die **Neustart**-Buttons in der Weboberfläche funktionieren, sudoers-Eintrag anlegen. Die Weboberfläche kann sowohl zeroinput als auch vzlogger neu starten, daher beide Dienste freigeben:

```bash
sudo sh -c 'printf "vzlogger ALL=(root) NOPASSWD: /bin/systemctl restart zeroinput\nvzlogger ALL=(root) NOPASSWD: /bin/systemctl restart vzlogger\n" > /etc/sudoers.d/zeroinput'
sudo chmod 440 /etc/sudoers.d/zeroinput
```

### 4. vzlogger konfigurieren

zeroinput erzeugt beim Start automatisch eine Vorlage `vzlogger.conf.example` in `/opt/zeroinput`, basierend auf der aktuellen `zeroinput.conf`. Sie enthält den obligatorischen `1-0:16.7.0`-Kanal und alle `vz_channels` mit UUID-Platzhaltern.

**Schritte:**
1. zeroinput einmal starten — `vzlogger.conf.example` wird erstellt
2. Für jeden Eintrag in der volkszaehler.org-Oberfläche einen Kanal anlegen und die UUID eintragen
3. `device` (Lesekopf-Pfad) und `middleware`-URL anpassen
4. `sudo cp /opt/zeroinput/vzlogger.conf.example /etc/vzlogger.conf`
5. `sudo systemctl restart vzlogger`

Der Kanal `1-0:16.7.0` (Wirkleistung) **muss** vorhanden sein.

> **Hinweis:** Je nach Auflösung der konfigurierten Kanäle kann vzlogger große Datenmengen schreiben. Siehe [Datenmengenverwaltung](https://wiki.volkszaehler.org/howto/datenmengen). zeroinput verwendet `verbosity: 15` nur um alle Zählerwerte vom FIFO zu erhalten — überschüssige Logeinträge werden verworfen.

### 5. zeroinput.conf anpassen

Die in Schritt 1 aus `zeroinput.conf.starter` erstellte `zeroinput.conf` an die eigene Hardware anpassen. Sie enthält alle verfügbaren Geräteklassen mit Kommentaren. **Nicht benötigte Einträge löschen.**

zeroinput startet auch ohne vollständig konfigurierte Ports, sodass die Weboberfläche sofort verfügbar ist. **Alles im Browser unter `http://<hostname>:8081/` konfigurieren** — Lader, Wechselrichter, VZ-Kanäle, Alarme und alle anderen Einstellungen.

> **Wichtig:** `chargers` und `inverters` über die dedizierten Tabs in der Weboberfläche oder direkt in der Conf-Datei bearbeiten. Diese Blöcke erfordern einen Neustart nach dem Speichern — Neustart-Tab verwenden.

Wichtige Einstellungen:

| Schlüssel | Beschreibung |
|---|---|
| `chargers` | MPPT-Lader und Temperatursensoren (Port → Gerätekonfig). Strukturell — Neustart erforderlich. |
| `inverters` | Einspeise-Wechselrichter (id → Gerätekonfig). Strukturell — Neustart erforderlich. |
| `max_input_power` | Maximale Gesamteinspeiseleistung aller Wechselrichter (W). Auf 0 setzen um Einspeisung zu deaktivieren. Liegt der Wert über der Kapazität der Wechselrichter, erscheint eine Warnung. |
| `max_bat_discharge` | Maximale Batterieentladeleistung (W) |
| `single_inverter_threshold` | Laststufe ab der Stufe 2 aktiviert (W) |
| `multi_inverter_wait` | Sekunden vor dem Zurückschalten auf Stufe 1 |
| `cell_count` | Anzahl der LiFePO4-Zellen in Serie (16 = 51,2 V, 15 = 48 V, 8 = 24/28 V). Die fest eingebauten Batteriespannungs-Schwellen skalieren mit diesem Wert. |
| `bat_voltage_const` | Faktor der Spannungskorrektur unter Last (V/kW, 0 = deaktiviert) |
| `free_power_export` | Freie Einspeisung bei hoher Batteriespannung (true/false) |
| `free_export_voltage_start` | Freie Einspeisung: Packspannung (V), ab der eingespeist wird (Standard 54,5 V; pro Zelle, also geteilt durch `cell_count`, sinnvoll 3,2–3,65 V) |
| `free_export_voltage_full` | Freie Einspeisung: Packspannung (V), bei der `max_input_power` erreicht wird (Standard 57,0 V). Muss über der Startspannung liegen. |
| `heat_temp_low` | Hitzeschutz: unterhalb dieser Sensortemperatur (°C) volle Leistung |
| `heat_temp_high` | Hitzeschutz: ab dieser Sensortemperatur (°C) wird der Wechselrichter abgeschaltet, dazwischen linear. Auslöse-Sensor per `heat_protect`-Markierung an einem Lader in der Weboberfläche. |
| `webconfig_port` | Webserver-Port (Standard 8081, 0 = deaktiviert) |
| `vz_channels` | volkszaehler.org-Kanalzuordnung (in Weboberfläche editierbar) |
| `load_prediction` | Lastprediktor aktivieren (true/false) |
| `discharge_timer` | Zeitgesteuerte Steuerung aktivieren (true/false) |

> **Wichtig:** Gerätenamen (`name`) müssen eindeutig sein — zeroinput beendet sich beim Start bei Duplikaten.

**Beispiel chargers-Block:**

```json
"chargers": {
    "/dev/ttyACM0": {"name": "esmart 60",   "mppt_type": "eSmart3", "pvp": 2350, "temp_display": "out"},
    "/dev/ttyACM2": {"name": "VE 150/35",   "mppt_type": "victron", "pvp": 1720},
    "/dev/ttyACM6": {"name": "Tracer AN",   "mppt_type": "epever", "pvp": 1300, "unit": 1},
    "/dev/ttyACM7": {"name": "Rover Elite", "mppt_type": "renogy", "pvp": 800,  "unit": 1},
    "/dev/ttyACM8": {"name": "TriStar 60",  "mppt_type": "morningstar", "pvp": 3000, "unit": 1}
}
```

Felder im Lader-Eintrag:

| Feld | Beschreibung |
|---|---|
| `mppt_type` | `eSmart3`, `victron`, `victron_agg`, `temp_sensor`, `epever`, `renogy` oder `morningstar` |
| `pvp` | PV-Spitzenleistung (W) für die `%PVp`-Anzeige |
| `unit` | Modbus-Slave-Adresse (nur epever/renogy/morningstar, Standard 1) |
| `temp_display` | Bezeichnung für den externen Temperaturwert (eSmart3) |

Hinweise zu Modbus-Ladern: **epever** (Tracer-AN/-BN) läuft mit 115200 Baud; **renogy** (Rover/Elite/Adventurer und SRNE-Regler) und **morningstar** (TriStar MPPT 45/60) mit 9600. Der Morningstar **EIA-485-Port existiert nur beim TS-MPPT-60/M** — der TS-MPPT-45 hat nur RS-232 und kann keinen RS485-Bus teilen. Ein Modbus-Lader pro Port: mehrere an einem physischen Bus (Multi-Drop mit unterschiedlichen `unit`-Adressen) werden von der Konfiguration noch nicht unterstützt — jedem einen eigenen Port/Adapter geben.

**Beispiel inverters-Block:**

```json
"inverters": {
    "base": {"name": "soyo base", "type": "soyosource",  "port": "/dev/ttyACM3",
             "stage": [1,2], "count": 1, "max_power": 900, "min_power": 10},
    "mp2":  {"name": "MultiPlus II", "type": "victron_mk3", "port": "/dev/ttyUSB0",
             "stage": [2], "count": 1, "max_power": 2400, "min_power": 200}
}
```

Felder im Wechselrichter-Eintrag:

| Feld | Beschreibung |
|---|---|
| `type` | `soyosource` oder `victron_mk3` |
| `port` | Serieller Gerätepfad. Ein Sender pro Port; `count` für mehrere baugleiche Einheiten am selben Port. |
| `stage` | Liste der Stufen in denen das Gerät läuft: `[1,2]` beide, `[1]` nur Grundlast, `[2]` nur Stufe 2. Leere Liste `[]` deaktiviert das Gerät ohne es zu entfernen. |
| `count` | Anzahl baugleicher Einheiten am Port (alle empfangen ein gemeinsames Broadcast-Paket). |
| `max_power` | Maximalleistung pro Einzelgerät (W). Beim GTN-2000: 1600 W (Batteriemodus). |
| `min_power` | Mindestleistung pro Einzelgerät (W). Darunter schläft das Gerät. |
| `mk3_ess_sign` | `1` (Standard) oder `-1` um die Einspeiserichtung umzukehren (nur Victron MK3). |

Eine physisch geteilte Leitung (eSmart3 liest + Soyosource sendet auf derselben Leitung) wird als zwei Einträge ausgedrückt: einer in `chargers` und einer in `inverters` mit demselben `port`.

Für den Victron-Aggregator (mehrere MPPTs an einem Port):

```json
"chargers": {
    "/tmp/ttyVirtual": {
        "name": "AGG", "mppt_type": "victron_agg",
        "devices": {
            "HQ12345ABC": {"name": "VE 150/35", "pvp": 1500, "type": "mppt"},
            "TEMP-P2-S0": {"name": "Rack Temp",               "type": "temp"}
        }
    }
}
```

### 6. systemd-Dienst installieren

Die Datei `zeroinput.service` liegt im Repository:

```ini
[Unit]
Description=zeroinput PV zero feed-in control
After=syslog.target network.target ntp.service vzlogger.service

[Service]
User=vzlogger
WorkingDirectory=/opt/zeroinput
ExecStartPre=/bin/mkdir -p /tmp/vz
ExecStartPre=/bin/touch /tmp/vz/output_to_vz.log
ExecStart=/usr/bin/python3 -u /opt/zeroinput/zeroinput.py -web -httpd
ExecStartPost=+/bin/bash -c 'test -f /tmp/vz/vzlogger_restarted || (sleep 3 && /bin/systemctl restart vzlogger.service && touch /tmp/vz/vzlogger_restarted)'
Restart=on-failure
RestartSec=15
Nice=-1
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

```bash
sudo cp /opt/zeroinput/zeroinput.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable zeroinput
sudo systemctl start zeroinput
```

Status prüfen:

```bash
sudo systemctl status zeroinput
journalctl -u zeroinput -f
```

> **Hinweis zur Ausgabe (`-v`):** Die obige `ExecStart`-Zeile startet zeroinput **ohne** `-v`. Die Option `-v` schaltet eine ausführliche Statusausgabe ein, die in jedem Regelzyklus (etwa jede Sekunde) mehrere Zeilen schreibt. Da `StandardOutput=journal` gesetzt ist, landet diese Ausgabe im systemd-Journal und damit im Syslog — im Dauerbetrieb summiert sich das auf hunderttausende Zeilen pro Tag und bläht das Log auf. Für den Dauerbetrieb wird `-v` deshalb weggelassen; es werden dann nur noch Start-, Fehler- und Konfigurations-Reload-Meldungen protokolliert. Den Live-Status sieht man weiterhin über die `-web`-Statusseite im Browser. `-v` ist für den manuellen Start im Vordergrund gedacht (siehe Fehlersuche), wenn man die Ausgabe direkt mitlesen will. Wer die ausführliche Ausgabe dennoch dauerhaft im Journal möchte, kann `-v` ergänzen und das Journal über `journald.conf` (z. B. `SystemMaxUse=`) größenbegrenzen oder die Ausgabe per `StandardOutput=append:/var/log/zeroinput.log` mit einer logrotate-Regel in eine eigene, rotierte Datei umleiten.

### 7. Aktualisieren

```bash
cd /opt/zeroinput
sudo -u vzlogger git pull
sudo systemctl restart zeroinput
```

Mit einem Victron MultiPlus-II muss `vebus.py` im Ordner liegen; fehlt die Datei, wird sie mit dem `wget`-Befehl aus der Installation geladen.

Neue Konfigurationsschlüssel stehen in `zeroinput.conf.starter`. Die Weboberfläche ergänzt Schlüssel, die in der eigenen `zeroinput.conf` fehlen, beim Öffnen mit ihren Standardwerten und schreibt sie beim nächsten Speichern in die Datei.

---

## Betrieb

### Kommandozeilenoptionen

| Option | Beschreibung |
|---|---|
| `-v` | Ausführliche Statusausgabe auf der Konsole (jede Sekunde mehrere Zeilen). Für den manuellen Start im Vordergrund gedacht; im systemd-Dauerbetrieb weglassen, da die Ausgabe sonst das Journal/Syslog aufbläht. |
| `-web` | HTML-Statusseite schreiben (`zeroinput.html`) |
| `-httpd` | Web-Konfigurationsserver starten (Port aus conf) |
| `-no-input` | Einspeisung deaktivieren |
| `-test-alarm` | Alarmbefehl testen und beenden |

### Weboberfläche

Mit `-httpd` erreichbar unter `http://<hostname>:8081/`

**Die Weboberfläche hat keine Anmeldung.** Jeder, der den Port erreicht, kann die Konfiguration ändern und damit Befehle als Benutzer `vzlogger` ausführen (Alarmbefehle, `predictor.py`). Den Port deshalb nur im eigenen Heimnetz erreichbar machen, nie per Portweiterleitung ins Internet freigeben. Für den Fernzugriff ein VPN nutzen (z. B. WireGuard). Anfragen fremder Webseiten weist der Server ab.

Tabs:
- **zeroinput.conf** — Alle hot-reloadbaren Schlüssel live bearbeiten. Strukturelle Schlüssel (`chargers`, `inverters`) zeigen Neustart-Hinweis.
- **chargers** — Strukturierter Editor für MPPT-Lader und Temperatursensoren (eSmart3, Victron, Aggregator mit SER#-Tabelle, EPever, Renogy, Morningstar). Modbus-Typen zeigen ein `unit`-Feld (Slave-Adresse). Neustart nach dem Speichern erforderlich.
- **inverters** — Strukturierter Editor für Einspeise-Wechselrichter (Typ, Port, Stufen-Checkboxen, parallele Einheiten, max/min-Leistung, ESS-Vorzeichen). Prüft beim Speichern auf Leistungslücken. Neustart erforderlich.
- **alarms** — Temperaturalarme pro Gerät (eSmart3: int\_hi/int\_lo/ext\_hi/ext\_lo; Temp-Sensor: ext\_hi/ext\_lo). Alarm-Befehle sollten mit `&` enden, damit der Regelzyklus nicht blockiert wird.
- **vz channels** — volkszaehler.org-Kanalzuordnung als editierbare Tabelle
- **timer.txt** — Entladeregeln bearbeiten
- **restart** — Startet zeroinput oder vzlogger neu (`sudo systemctl restart <dienst>`); je ein Button. Erfordert die passenden sudoers-Einträge (siehe Installation).
- **dirt shift** — `dirt_shift.conf` bearbeiten (nur sichtbar, wenn `dirt_shift/dirt_shift.conf` vorhanden ist)
- **dirt shift log** — die letzten 24 Stunden des dirt_shift-Logs, nur lesend (nur sichtbar, wenn in `dirt_shift.conf` ein `logfile` gesetzt ist). Die Schaltflächen **previous run** und **next run** blättern laufweise und springen jeweils an den Anfang eines Laufs, **latest** springt ans Ende.
- **status** — Live-Statusseite (nur mit `-web`)

Die Schaltflächen **A−** und **A+** in der Kopfzeile stellen die Schriftgröße der Ansichten status, timer.txt und dirt shift log in Schritten von 25 % zwischen 50 % und 400 % ein. Die Einstellung wird im Browser gespeichert und gilt damit pro Gerät.

### Hot-Reload

Änderungen an `zeroinput.conf` werden beim Speichern automatisch übernommen. Schlüssel die einen Neustart erfordern:

- `chargers`, `inverters` (strukturell — Treiber und Lesethreads werden einmalig beim Start aufgebaut)
- `vzlogger_log_file`, `persistent_vz_file`, `webconfig_port`

---

## Hardware-Hinweise

**Leistungsstufen.** zeroinput verteilt die Einspeiseanforderung auf zwei Stufen. Stufe 1 bedient die Grundlast bis `single_inverter_threshold` mit ausschließlich Stufe-1-Wechselrichtern. Stufe 2 fügt alle Stufe-2-Wechselrichter hinzu, die sich die Last gleichmäßig teilen — kleinere Einheiten sättigen zuerst, das größte Gerät (z.B. MultiPlus) übernimmt automatisch den Rest. Einspeisung deaktivieren über `max_input_power: 0` oder den Timer, nicht durch Stehenlassen von Lücken.

**Lückenprüfung.** Beim Start und beim Speichern der Inverter-Konfiguration prüft zeroinput, ob die konfigurierten Wechselrichter den gesamten Leistungsbereich von 0 bis `max_input_power` lückenlos abdecken. Eine Lücke (z.B. Stufe-1-Soyo endet bei 900 W, Stufe-2-MultiPlus hat `min_power: 1500`) erzeugt eine unübersehbare Warnung.

**Soyosource-Rampenrate.** Der Wechselrichter rampt mit 400 W/s. Große Laststufen verursachen kurzzeitig Bezug oder Einspeisung — das ist normal.

**Soyosource GTN-2000.** Verwendet dasselbe RS485-Limiter-Protokoll wie GTN-1000/1200. `max_power: 1600` verwenden (Batteriemodus-Nennleistung, nicht den höheren Solarmodus-Wert).

**Victron MultiPlus.** Benötigt einen MK2-USB- oder MK3-USB-Adapter und den ESS-Assistant in VEConfigure konfiguriert (Schalter vollständig EIN, nicht „nur Lader"). Kein GX-Gerät nötig. Der ESS-Leistungssollwert wird nur in den RAM geschrieben — sekündliche Schreibvorgänge sind bauartbedingt sicher. Falls die Einspeiserichtung vertauscht ist, `mk3_ess_sign: -1` setzen.

**RS485-Bus.** Alle Geräte verbinden: A+ an A+, B- an B-. Abschlusswiderstände am letzten Gerät aktivieren, sofern vorhanden.

**VE.Direct-Aggregator.** Mehrere Victron-MPPTs können über einen Arduino Mega 2560 oder Teensy 4.1 mit [VE.Direct-Aggregator](https://github.com/E-t0m/ve.direct-aggregator)-Firmware einen seriellen Port teilen. VE.Direct ist ein 3,3-V-UART; die Verbindung zum Pi erfolgt über einen USB-UART-Adapter. RS485-Pegelwandler können optional auf einer oder beiden Seiten für längere Leitungen eingesetzt werden. `ve_aggregator.py` muss im gleichen Verzeichnis wie `zeroinput.py` liegen.

---

## Lastprediktor

Der Lastprediktor (`load_prediction: true`, Standard: false) erkennt zyklische Lasten (Waschmaschine, Herd) und kurze hohe Lastspitzen und stabilisiert die Einspeisung gegen Übereinspeisung.

| Einstellung | Ort | Beschreibung |
|---|---|---|
| `load_prediction` | conf | Hauptschalter (Standard: false) |
| `min_spread_w` | conf | Mindestspreizung LOW/HIGH damit k-Means greift (Standard: 150 W) |
| `predictor_log` | conf | `/tmp/predictor.log` schreiben (Standard: true) |
| `MAX_SPREAD_W` | `predictor.py` | Maximale Spreizung; darüber gilt die Last nicht als zyklisch |
| `PEAK_SHORT_MAX_N` | `predictor.py` | Grenze kurzer/langer Peak in Zyklen |
| `LOG_FILE` | `predictor.py` | Log-Pfad (`''` = deaktiviert) |

conf-Schlüssel sind hot-reloadbar; Konstanten in `predictor.py` werden bei Dateiänderung automatisch übernommen. Vollständiges Verhalten in **[predictor_spec.md](predictor_spec.md)** dokumentiert.

---

## volkszaehler.org-Kanäle (vz_channels)

Format pro Eintrag: `[Gerät, Schlüssel, vz_kanal, Faktor]`

- **Gerät** — Gerätename aus `chargers`, `"combined"` für PV-Gesamt, oder `null` für direkte Variablen
- **Schlüssel** — Datenschlüssel (siehe unten)
- **vz_kanal** — UUID-Alias in der vzlogger-Konfiguration
- **Faktor** — Multiplikator (z.B. `-1` zum Invertieren)

**Direkte Variablen** (`Gerät: null`): `power_demand`, `zero_shift`, `bat_voltage`

**combined**: `PPV`, `PVperc`, `Vbat`, `Ibat`, `Pload`

**eSmart3**: `PPV`, `VPV`, `Vbat`, `Ibat`, `Pload`, `int_temp`, `ext_temp`

**Victron MPPT**: `PPV`, `VPV`, `Vbat`, `Ibat`, `IL`

**Temperatursensor** (AGG, type: temp): `ext_temp`

---

## Entladetimer

Mit `discharge_timer: true` und einer `timer.txt`. Format pro Zeile:

```
JJJJ-MM-TT HH:MM:SS <Batterie> <Wechselrichter> <Energie_Wh>
```

`0000-00-00` als Datum gilt täglich. Werte > 100 = Watt, ≤ 100 = Prozent der Maximalleistung.

Beispiel:
```
0000-00-00 22:00:00   50   80   5000
0000-00-00 06:00:00   100  100  0
```

---

## Temperaturalarme

Konfiguriert in `zeroinput.conf` unter `alarms`, als Schlüssel der Gerätename. Ein Alarm aktiviert sich automatisch wenn Schwellwert und Befehl gesetzt sind.

```json
"alarms": {
    "esmart 60": {
        "int_hi": 60, "int_hi_cmd": "befehl &", "int_hi_interval": 300,
        "ext_hi": 55, "ext_hi_cmd": "befehl &"
    }
}
```

eSmart3 unterstützt `int_hi`, `int_lo`, `ext_hi`, `ext_lo`. Temperatursensoren unterstützen `ext_hi`, `ext_lo`. Befehle sollten mit `&` enden, damit der Regelzyklus nicht blockiert wird.

---

## Fehlerbehebung

**Live-Ausgabe verfolgen:**
```bash
zerooutput.sh
# oder:
tail -f /opt/zeroinput/zeroinput.html
```

**systemd-Logs:**
```bash
journalctl -u zeroinput -f
journalctl -u zeroinput -n 100
```

**RS485-Ports belegt (`[Errno 16] Device or resource busy`):**
Eine alte zeroinput-Instanz läuft noch.
```bash
ps aux | grep zeroinput
crontab -u vzlogger -e   # zeroinput startet über systemd: eine @reboot-Zeile für zeroinput entfernen
sudo reboot
```

**zeroinput startet nicht:**
```bash
journalctl -u zeroinput -n 50
python3 /opt/zeroinput/zeroinput.py -v
```

**Kein FIFO:** `mkfifo /tmp/vz/vzlogger.fifo`

**Keine RS485-Kommunikation:**
```bash
ls -la /dev/ttyACM* /dev/ttyUSB*
sudo usermod -aG dialout vzlogger
```

**MultiPlus antwortet nicht:** Prüfen ob der MK3-USB-Adapter angeschlossen ist und der ESS-Assistant in VEConfigure konfiguriert wurde. zeroinput protokolliert „MK3 inactive" beim Start — der Rest läuft normal weiter.

**Modbus-Lader (EPever / Renogy / Morningstar) zeigt keine Daten:** Prüfen ob `unit` (Modbus-Slave-Adresse) mit der Controller-Einstellung übereinstimmt, die Baudrate stimmt (EPever 115200, Renogy/Morningstar 9600) und RS485 A/B nicht vertauscht sind. Die Anzeige zeigt `PORT ERROR` bei fehlgeschlagenem Port-Lesen. Beim Morningstar sicherstellen, dass das Modell einen EIA-485-Port hat (nur TS-MPPT-60/M).

**Webserver nicht erreichbar:**
- `webconfig_port` in `zeroinput.conf` prüfen
- zeroinput mit `-httpd` starten
- Port-Konflikt prüfen: `ss -tlnp | grep 8081`
