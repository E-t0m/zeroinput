# zeroinput – Installationsanleitung
*v2.1*

## Überblick

zeroinput ist ein Python-Skript für PV-Nulleinspeisung (Eigenverbrauchsoptimierung) auf einem Raspberry Pi.
Es liest den Stromzähler über vzlogger aus und steuert einen oder mehrere Batterie-Netzwechselrichter über RS485 oder USB — je nach Last zwischen Leistungsstufen umschaltend. Unterstützte Wechselrichtertypen: Soyosource-GTN-Limiter-Serie und Victron MultiPlus (ESS über MK3-USB-Adapter).

zeroinput nutzt **vzlogger** aus dem [Volkszähler](https://www.volkszaehler.org/)-Projekt auf zwei Arten:
- als **Zählerdatenquelle** — vzlogger liest den Stromzähler und streamt den Wirkleistungswert (`1-0:16.7.0`) über ein FIFO an zeroinput
- als **Datenlogger** — zeroinput schreibt eigene Werte (Einspeiseleistung, Batteriespannung, PV-Leistung, …) zurück an vzlogger, der sie zusammen mit den anderen Volkszähler-Kanälen protokolliert

zeroinput fügt sich damit natürlich in eine bestehende Volkszähler-Installation ein.

**Dateien:**

| Datei | Beschreibung |
|---|---|
| `zeroinput.py` | Hauptskript |
| `input_power_staging.py` | Zweistufige Leistungsverteilungslogik |
| `inverter_drivers.py` | Wechselrichter-Treiber-Abstraktion (Soyosource, Victron MK3) |
| `vebus.py` | VE.Bus MK2/MK3-Protokolltreiber (nur für Victron MultiPlus) |
| `predictor.py` | Lastprediktor (k-Means, optional) |
| `webconfig.py` | HTTP-Konfigurationsserver (optional, benötigt `-httpd`) |
| [`ve_aggregator.py`](https://github.com/E-t0m/ve.direct-aggregator) | VE.Direct-Aggregator-Client (optional, nur für `victron_agg`) |
| `zeroinput.conf` | Konfiguration (JSON) — aus `zeroinput.conf.starter` erstellen |
| `zeroinput.conf.starter` | Referenzkonfiguration mit allen verfügbaren Geräteklassen |
| `zeroinput_webconfig.html` | Weboberfläche |
| `zeroinput.service` | systemd-Dienst |
| `zerooutput.sh` | Live-Status im Terminal (`/usr/local/bin/zerooutput.sh`) |
| `timer.txt` | Entladetimer (optional) |

---

## Voraussetzungen

### Hardware

- Raspberry Pi (getestet mit Pi 3/4)
- Ein oder mehrere Wechselrichter:
  - **Soyosource GTN Limiter** (GTN-1000/1200/2000 mit RS485-Limiter-Port) — per USB-RS485-Adapter angebunden
  - **Victron MultiPlus / MultiPlus-II** (VE.Bus, Mikroprozessor 2. Generation) mit MK2-USB- oder MK3-USB-Adapter — ESS-Assistant muss in VEConfigure konfiguriert sein
- MPPT-Laderegler: eSmart3 (RS485) oder Victron MPPT (VE.Direct oder Aggregator)
- RS485-Adapter (USB) — Verdrahtung: **A+ an A+, B- an B-** an allen Geräten
- Stromzähler mit erweiterter Datenausgabe (PIN beim Netzbetreiber anfordern)
  Der OBIS-Datensatz `1-0:16.7.0` (Netz-Wirkleistung) **muss** vorhanden sein
- Zähler-Schnittstelle kompatibel mit vzlogger (IR-Lesekopf, Shelly 3EM/3PM, Modbus-Zähler, …)

### Software

```bash
sudo apt install python3 python3-serial vzlogger
```

Für den Victron MultiPlus ist pyserial bereits enthalten. Keine weiteren Bibliotheken nötig — `vebus.py` nutzt ausschließlich den Standard-Serport.

---

## Installation

### 1. Dateien kopieren

```bash
sudo useradd -m -s /bin/bash vzlogger   # falls noch nicht vorhanden
sudo usermod -aG dialout vzlogger       # RS485- und USB-Serial-Zugriff
sudo cp zeroinput.py input_power_staging.py inverter_drivers.py /home/vzlogger/
sudo cp vebus.py /home/vzlogger/        # nur für Victron MultiPlus
sudo cp predictor.py /home/vzlogger/    # optional
sudo cp webconfig.py /home/vzlogger/    # optional, nur für -httpd
sudo cp ve_aggregator.py /home/vzlogger/ # optional, nur für victron_agg
sudo cp zeroinput.conf zeroinput_webconfig.html zeroinput.service /home/vzlogger/
sudo cp timer.txt /home/vzlogger/       # optional
sudo chown vzlogger:vzlogger /home/vzlogger/*.py /home/vzlogger/*.conf /home/vzlogger/*.html
```

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

Damit der **Neustart**-Button in der Weboberfläche funktioniert, sudoers-Eintrag anlegen:

```bash
sudo sh -c 'echo "vzlogger ALL=(root) NOPASSWD: /bin/systemctl restart zeroinput" > /etc/sudoers.d/zeroinput'
sudo chmod 440 /etc/sudoers.d/zeroinput
```

### 4. vzlogger konfigurieren

zeroinput erzeugt beim Start automatisch eine Vorlage `vzlogger.conf.example` im Arbeitsverzeichnis, basierend auf der aktuellen `zeroinput.conf`. Sie enthält den obligatorischen `1-0:16.7.0`-Kanal und alle `vz_channels` mit UUID-Platzhaltern.

**Schritte:**
1. zeroinput einmal starten — `vzlogger.conf.example` wird erstellt
2. Für jeden Eintrag in der Volkszähler-Oberfläche einen Kanal anlegen und die UUID eintragen
3. `device` (Lesekopf-Pfad) und `middleware`-URL anpassen
4. `sudo cp vzlogger.conf.example /etc/vzlogger.conf`
5. `sudo systemctl restart vzlogger`

Der Kanal `1-0:16.7.0` (Wirkleistung) **muss** vorhanden sein.

> **Hinweis:** Je nach Auflösung der konfigurierten Kanäle kann vzlogger große Datenmengen schreiben. Siehe [Datenmengenverwaltung](https://wiki.volkszaehler.org/howto/datenmengen). zeroinput verwendet `verbosity: 15` nur um alle Zählerwerte vom FIFO zu erhalten — überschüssige Logeinträge werden verworfen.

### 5. zeroinput.conf erstellen

`zeroinput.conf.starter` nach `zeroinput.conf` kopieren und an die eigene Hardware anpassen. Die Starter-Datei enthält alle verfügbaren Geräteklassen mit Kommentaren. **Nicht benötigte Einträge löschen.**

zeroinput startet auch ohne vollständig konfigurierte Ports, sodass die Weboberfläche sofort verfügbar ist. **Alles im Browser unter `http://<hostname>:8081/` konfigurieren** — Lader, Wechselrichter, VZ-Kanäle, Alarme und alle anderen Einstellungen.

> **Wichtig:** `chargers` und `inverters` über die dedizierten Tabs in der Weboberfläche oder direkt in der Conf-Datei bearbeiten. Diese Blöcke erfordern einen Neustart nach dem Speichern — Neustart-Tab verwenden.

Wichtige Einstellungen:

| Schlüssel | Beschreibung |
|---|---|
| `chargers` | MPPT-Lader und Temperatursensoren (Port → Gerätekonfig). Strukturell — Neustart erforderlich. |
| `inverters` | Einspeise-Wechselrichter (id → Gerätekonfig). Strukturell — Neustart erforderlich. |
| `max_input_power` | Maximale Gesamteinspeiseleistung aller Wechselrichter (W). Auf 0 setzen um Einspeisung zu deaktivieren. |
| `max_bat_discharge` | Maximale Batterieentladeleistung (W) |
| `single_inverter_threshold` | Laststufe ab der Stufe 2 aktiviert (W) |
| `multi_inverter_wait` | Sekunden vor dem Zurückschalten auf Stufe 1 |
| `webconfig_port` | Webserver-Port (Standard 8081, 0 = deaktiviert) |
| `vz_channels` | Volkszähler-Kanalzuordnung (in Weboberfläche editierbar) |
| `load_prediction` | Lastprediktor aktivieren (true/false) |
| `discharge_timer` | Zeitgesteuerte Steuerung aktivieren (true/false) |

> **Wichtig:** Gerätenamen (`name`) müssen eindeutig sein — zeroinput beendet sich beim Start bei Duplikaten.

**Beispiel chargers-Block:**

```json
"chargers": {
    "/dev/ttyACM0": {"name": "esmart 60", "mppt_type": "eSmart3", "pvp": 2350, "temp_display": "out"},
    "/dev/ttyACM1": {"name": "esmart 40", "mppt_type": "eSmart3", "pvp": 1690, "temp_display": "bat"},
    "/dev/ttyACM2": {"name": "VE 150/35", "mppt_type": "victron", "pvp": 1720}
}
```

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

Inhalt von `zeroinput.service`:

```ini
[Unit]
Description=zeroinput PV-Nulleinspeisung
After=syslog.target network.target ntp.service vzlogger.service

[Service]
User=vzlogger
WorkingDirectory=/home/vzlogger
ExecStartPre=/bin/mkdir -p /tmp/vz
ExecStartPre=/bin/touch /tmp/vz/output_to_vz.log
ExecStart=/usr/bin/python3 -u /home/vzlogger/zeroinput.py -v -web -httpd
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
sudo cp zeroinput.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable zeroinput
sudo systemctl start zeroinput
```

Status prüfen:

```bash
sudo systemctl status zeroinput
journalctl -u zeroinput -f
```

---

## Betrieb

### Kommandozeilenoptionen

| Option | Beschreibung |
|---|---|
| `-v` | Ausführliche Ausgabe auf der Konsole |
| `-web` | HTML-Statusseite schreiben (`zeroinput.html`) |
| `-httpd` | Web-Konfigurationsserver starten (Port aus conf) |
| `-no-input` | Einspeisung deaktivieren |
| `-test-alarm` | Alarmbefehl testen und beenden |

### Weboberfläche

Mit `-httpd` erreichbar unter `http://<hostname>:8081/`

Tabs:
- **zeroinput.conf** — Alle hot-reloadbaren Schlüssel live bearbeiten. Strukturelle Schlüssel (`chargers`, `inverters`) zeigen Neustart-Hinweis.
- **chargers** — Strukturierter Editor für MPPT-Lader und Temperatursensoren (eSmart3, Victron, Aggregator mit SER#-Tabelle). Neustart nach dem Speichern erforderlich.
- **inverters** — Strukturierter Editor für Einspeise-Wechselrichter (Typ, Port, Stufen-Checkboxen, count, max/min-Leistung, ESS-Vorzeichen). Prüft beim Speichern auf Leistungslücken. Neustart erforderlich.
- **alarms** — Temperaturalarme pro Gerät (eSmart3: int\_hi/int\_lo/ext\_hi/ext\_lo; Temp-Sensor: ext\_hi/ext\_lo). Alarm-Befehle sollten mit `&` enden, damit der Regelzyklus nicht blockiert wird.
- **vz channels** — Volkszähler-Kanalzuordnung als editierbare Tabelle
- **timer.txt** — Entladeregeln bearbeiten
- **restart** — Sendet `sudo systemctl restart zeroinput`
- **status** — Live-Statusseite (nur mit `-web`)

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

**VE.Direct-Aggregator.** Mehrere Victron-MPPTs können über einen Arduino Mega 2560 oder Teensy 4.1 mit [VE.Direct-Aggregator](https://github.com/E-t0m/ve.direct-aggregator)-Firmware einen RS485-Port teilen. `ve_aggregator.py` muss im gleichen Verzeichnis wie `zeroinput.py` liegen.

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

conf-Schlüssel sind hot-reloadbar; Konstanten in `predictor.py` werden bei Dateiänderung automatisch übernommen. Vollständiges Verhalten in **[predictor_spec_de.md](predictor_spec_de.md)** dokumentiert.

---

## Volkszähler-Kanäle (vz_channels)

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
tail -f /home/vzlogger/zeroinput.html
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
crontab -u vzlogger -e   # @reboot-Zeile entfernen
sudo reboot
```

**zeroinput startet nicht:**
```bash
journalctl -u zeroinput -n 50
python3 /home/vzlogger/zeroinput.py -v
```

**Kein FIFO:** `mkfifo /tmp/vz/vzlogger.fifo`

**Keine RS485-Kommunikation:**
```bash
ls -la /dev/ttyACM* /dev/ttyUSB*
sudo usermod -aG dialout vzlogger
```

**MultiPlus antwortet nicht:** Prüfen ob der MK3-USB-Adapter angeschlossen ist und der ESS-Assistant in VEConfigure konfiguriert wurde. zeroinput protokolliert „MK3 inactive" beim Start — der Rest läuft normal weiter.

**Webserver nicht erreichbar:**
- `webconfig_port` in `zeroinput.conf` prüfen
- zeroinput mit `-httpd` starten
- Port-Konflikt prüfen: `ss -tlnp | grep 8081`
