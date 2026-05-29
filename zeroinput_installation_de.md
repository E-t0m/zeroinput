# zeroinput – Installationsanleitung
*v2.0*

## Überblick

zeroinput ist ein Python-Skript für PV-Nulleinspeisung (Eigenverbrauchsoptimierung) auf einem Raspberry Pi.
Es liest den Stromzähler über vzlogger aus und steuert einen oder mehrere Soyosource-Netzwechselrichter über RS485 – je nach Last zwischen Einzelbetrieb und Vollbetrieb umschaltend.

zeroinput nutzt **vzlogger** aus dem [Volkszähler](https://www.volkszaehler.org/)-Projekt auf zwei Arten:
- als **Zählerdatenquelle** — vzlogger liest den Stromzähler und streamt den Wirkleistungswert (`1-0:16.7.0`) über ein FIFO an zeroinput
- als **Datenlogger** — zeroinput schreibt eigene Werte (Einspeiseleistung, Batteriespannung, PV-Leistung, …) zurück an vzlogger, der sie zusammen mit den anderen Volkszähler-Kanälen protokolliert

zeroinput fügt sich damit natürlich in eine bestehende Volkszähler-Installation ein, ohne sie zu ersetzen oder zu duplizieren.

**Dateien:**

| Datei | Beschreibung |
|---|---|
| `zeroinput.py` | Hauptskript |
| `predictor.py` | Lastprediktor (k-Means, optional) |
| `webconfig.py` | HTTP-Konfigurationsserver (optional, benötigt `-httpd`) |
| `ve_aggregator.py` | VE.Direct-Aggregator-Client (optional, benötigt für `victron_agg`) |
| `zeroinput.conf` | Konfiguration (JSON) |
| `zeroinput_webconfig.html` | Weboberfläche |
| `zeroinput.service` | systemd-Dienst |
| `zerooutput.sh` | Live-Status im Terminal (`/usr/local/bin/zerooutput.sh`) |
| `timer.txt` | Entladetimer (optional) |

---

## Voraussetzungen

### Hardware

- Raspberry Pi (getestet mit Pi 3/4)
- Soyosource-GTI-Wechselrichter mit RS485-Anschluss
- eSmart3- oder Victron-MPPT-Laderegler mit RS485/VE.Direct
- RS485-Adapter (USB) – Verdrahtung: **A+ an A+, B- an B-** an allen Geräten
- Stromzähler mit erweiterter Datenausgabe (PIN beim Netzbetreiber anfordern)
  Der OBIS-Datensatz `1-0:16.7.0` (Netz-Wirkleistung) **muss** vorhanden sein – ohne diesen Wert ist keine Regelung möglich!
- Zähler-Schnittstelle – kompatibel mit allem was vzlogger lesen kann, z. B.:
  - IR-Lesekopf (z. B. Hichi USB)
  - Shelly 3EM / 3PM
  - Modbus-Zähler
  - Jedes Gerät das OBIS `1-0:16.7.0` (Wirkleistung) liefert

### Software

```bash
sudo apt install python3 python3-serial vzlogger
```

---

## Installation

### 1. Dateien kopieren

```bash
sudo useradd -m -s /bin/bash vzlogger   # falls noch nicht vorhanden
sudo usermod -aG dialout vzlogger          # RS485-Zugriff
sudo cp zeroinput.py /home/vzlogger/
sudo cp predictor.py /home/vzlogger/      # optional
sudo cp webconfig.py /home/vzlogger/      # optional, nur für -httpd
sudo cp ve_aggregator.py /home/vzlogger/  # optional, nur für victron_agg
sudo cp zeroinput.conf zeroinput_webconfig.html zeroinput.service /home/vzlogger/
sudo cp timer.txt /home/vzlogger/        # optional
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
```

Mit mehreren identischen Adaptern per USB-Port unterscheiden:

```
SUBSYSTEMS=="usb", ATTRS{devpath}=="1.1", SYMLINK+="rs485a"
SUBSYSTEMS=="usb", ATTRS{devpath}=="1.3", SYMLINK+="rs485b"
```

Dann: `sudo udevadm control --reload-rules && sudo udevadm trigger`

### 3. FIFO und Verzeichnis anlegen

Das FIFO muss vor dem Start von vzlogger **und** zeroinput vorhanden sein. Die sauberste Lösung ist, es in der vzlogger.service-Unit anzulegen.

Folgende Zeilen in `/etc/systemd/system/vzlogger.service` ergänzen:

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

Damit legt vzlogger beim Start das FIFO an und zeroinput findet es bereit.

### 4. vzlogger konfigurieren

> **Tipp:** Sobald zeroinput mit `-httpd` läuft, können die meisten Einstellungen — einschließlich RS485-Ports, VZ-Kanäle und Entladetimer — bequem im Browser unter `http://<hostname>:8081/` bearbeitet werden. Direkte Dateibearbeitung ist nur für die Ersteinrichtung oder Schlüssel die einen Neustart erfordern nötig (siehe Abschnitt 5).

zeroinput erzeugt beim Start automatisch eine Vorlage `vzlogger.conf.example` im gleichen Verzeichnis wie `zeroinput.py`, basierend auf der aktuellen `zeroinput.conf`. Sie enthält bereits:

- Den obligatorischen `1-0:16.7.0`-Kanal mit UUID-Platzhalter
- Alle konfigurierten `vz_channels` mit eigenen UUID-Platzhaltern
- Den korrekten FIFO-Pfad

**Schritte:**
1. zeroinput einmal starten – `vzlogger.conf.example` wird im Verzeichnis von `zeroinput.py` erstellt
2. Datei öffnen, für jeden Eintrag in der Volkszähler-Oberfläche einen Kanal anlegen und die UUID eintragen
3. `device` (Lesekopf-Pfad) und `middleware`-URL anpassen
4. Als `/etc/vzlogger.conf` kopieren: `sudo cp vzlogger.conf.example /etc/vzlogger.conf`
5. `sudo systemctl restart vzlogger`

> Die Datei wird bei jedem zeroinput-Start neu geschrieben und bei Änderungen der `vz_channels` in der Weboberfläche automatisch aktualisiert.

Der Kanal `1-0:16.7.0` (Wirkleistung) **muss** vorhanden sein – ohne diesen Wert ist keine Regelung möglich.

> **Hinweis:** Je nach Auflösung der konfigurierten Kanäle kann vzlogger große Datenmengen in die Datenbank schreiben. Siehe [Datenmengenverwaltung](https://wiki.volkszaehler.org/howto/datenmengen), um Datenbanküberlauf zu vermeiden. zeroinput verwendet `verbosity: 15` nur um alle Zählerwerte vom FIFO zu erhalten – überschüssige Logeinträge werden verworfen.

### 5. zeroinput.conf anpassen

zeroinput startet auch ohne konfigurierte RS485-Ports, sodass die Weboberfläche sofort nach dem Dienststarten verfügbar ist. **Alles im Browser unter `http://<hostname>:8081/` konfigurieren** — einschließlich RS485-Ports, VZ-Kanäle und alle anderen Einstellungen.

Der einzige Schlüssel der vor dem ersten Start gesetzt sein muss, ist `webconfig_port` (Standard: `8081`). Er ist in der mitgelieferten `zeroinput.conf` bereits gesetzt.

Direkte Dateibearbeitung als Fallback wenn die Weboberfläche nicht erreichbar ist:

```bash
nano /home/vzlogger/zeroinput.conf
```

Wichtige Einstellungen:

| Schlüssel | Beschreibung |
|---|---|
| `rs485` | RS485-Ports und Gerätenamen |
| `basic_load_inverter_port` | Port des Grundlast-Wechselrichters |
| `total_number_of_inverters` | Gesamtanzahl der Wechselrichter über alle Ports. Im Mehrwechselrichter-Modus teilt zeroinput die Anforderung durch diesen Wert und sendet den Anteil an jeden RS485-Port. Mehrere parallel verdrahtete Wechselrichter an einem Port antworten alle auf dasselbe Paket und speisen jeweils diesen Anteil ein – multiplizieren ihn also. **Stimmt dieser Wert nicht mit der tatsächlichen Anzahl physischer Wechselrichter überein, ist die Gesamt-Einspeiseleistung falsch.** Hot-reloadbar. |
| `max_input_power` | Maximale Gesamtleistung aller Wechselrichter (W) |
| `max_bat_discharge` | Maximale Batterieentladeleistung (W) |
| `webconfig_port` | Webserver-Port (0 = deaktiviert) |
| `vz_channels` | Volkszähler-Kanalzuordnung (in Weboberfläche editierbar) |
| `min_spread_w` | Mindestspreizung LOW/HIGH für Prediktor-Aktivierung (W, Standard: 150). Hot-reloadbar. |
| `predictor_log` | Prediktor-Log nach `/tmp/predictor.log` schreiben (true/false). Hot-reloadbar. |

> **Wichtig:** Gerätenamen (`name`) müssen eindeutig sein – zeroinput beendet sich beim Start bei Duplikaten.

**Beispiel-RS485-Konfiguration:**

```json
"rs485": {
    "/dev/ttyACM0": {
        "name": "esmart 60",
        "mppt_type": "eSmart3",
        "pvp": 900,
        "inverter": "soyosource",
        "temp_display": "out",
        "alarm": {
            "temp_int": 45, "int_cmd": "mpg321 /home/vzlogger/voice/alarm.mp3 &", "int_interval": 300,
            "temp_ext": 35, "ext_cmd": "",                                          "ext_interval": 300
        }
    },
    "/dev/ttyACM1": {
        "name": "esmart 40",
        "mppt_type": "eSmart3",
        "inverter": "soyosource",
        "temp_display": "bat",
        "alarm": {
            "temp_int": 50, "int_cmd": "mpg321 /home/vzlogger/voice/alarm.mp3 &", "int_interval": 300,
            "temp_ext": 40, "ext_cmd": "./alarm_akku.sh &",                        "ext_interval": 300
        }
    },
    "/dev/ttyACM2": {"name": "VE 150/35", "mppt_type": "victron"},
    "/dev/ttyACM3": {"name": "soyo",       "inverter": "soyosource"}
}
```

Für einen einzelnen Victron-MPPT an einem eigenen Port:

```json
"/dev/ttyACM2": {"name": "VE 150/35", "mppt_type": "victron", "pvp": 1500}
```

Für mehrere Victron-MPPTs an einem Port über den VE.Direct-Aggregator (`readtext_sendhex`-Firmware), `victron_agg` mit einer `devices`-Zuordnung von SER# → `{name, pvp}` verwenden:

```json
"/dev/ttyACM2": {
    "mppt_type": "victron_agg",
    "name": "Dach AGG",
    "devices": {
        "HQ12345ABC": {"name": "VE 150/35", "pvp": 1500, "type": "mppt"},
        "TEMP-P2-S0":  {"name": "Rack Temp", "type": "temp"},
        "HQ67890DEF": {"name": "VE 75/15",  "pvp":  800, "type": "mppt"}
    }
}
```

`pvp` (PV-Spitzenleistung in W) dient der `%PVp`-Anzeige. `ve_aggregator.py` muss im gleichen Verzeichnis wie `zeroinput.py` liegen. Erfordert `readtext_sendhex`-Firmware auf dem Arduino/Teensy-Aggregator. Geräte vom Typ `temp` werden als DS18B20-Temperatursensoren behandelt und in einer separaten Zeile der MPPT-Tabelle angezeigt.

Ein Alarm wird nur ausgelöst wenn Schwellwert und Befehl gesetzt sind. Einzelnen Alarm deaktivieren: Befehl leer lassen.

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

> `ExecStartPost` verwendet `+` um vzlogger mit Root-Rechten neu zu starten – damit vzlogger `/tmp/vz/output_to_vz.log` findet, nachdem zeroinput es angelegt hat.

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
| `-web` | HTML-Statusseite schreiben (`/home/vzlogger/zeroinput.html`) |
| `-httpd` | Web-Konfigurationsserver starten (Port aus conf) |
| `-no-input` | Einspeisung deaktivieren |
| `-test-alarm` | Alarmbefehl testen und beenden |

### Weboberfläche

Mit `-httpd` ist die Weboberfläche erreichbar unter:

```
http://<hostname>:8081/
```

Tabs:
- **zeroinput.conf** – Konfiguration live bearbeiten (gesperrte Felder erfordern Neustart)
- **RS485** – RS485-Port- und Gerätekonfiguration einschließlich Victron-AGG (SER#-Zuordnung, pvp, Temperatursensoren) und eSmart3-Alarme (erfordert Neustart nach dem Speichern)
- **VZ-Kanäle** – Volkszähler-Kanalzuordnung als editierbare Tabelle
- **timer.txt** – Entladetimer bearbeiten
- **Status** – Live-Statusseite (nur mit `-web`)

### Hot-Reload

Änderungen an `zeroinput.conf` werden automatisch beim Speichern übernommen – kein Neustart nötig.

Ausnahmen (erfordern Neustart):

- `rs485`
- `basic_load_inverter_port`
- `webconfig_port`
- `vzlogger_log_file`
- `persistent_vz_file`

---

## Hardware-Hinweise

**Soyosource-Rampenrate:** Der Wechselrichter rampt mit 400 W/s hoch und runter. Bei 3 Wechselrichtern sind das 1200 W/s. Große Laststufen (Herd, Waschmaschine) verursachen daher kurzzeitig Bezug oder Einspeisung – das ist normal.

**Parallele Wechselrichter an einem Port:** Mehrere Soyosource-Wechselrichter können parallel an einem RS485-Port verdrahtet werden – alle empfangen dasselbe Anforderungspaket und speisen jeweils diesen Betrag ein. zeroinput sendet `power_demand / total_number_of_inverters` an jeden Port. Teilen sich zwei Wechselrichter einen Port, antworten beide auf dasselbe Paket und speisen zusammen das Doppelte des gesendeten Werts ein. `total_number_of_inverters` muss daher der Gesamtzahl physischer Wechselrichter entsprechen, unabhängig von der Portaufteilung. Stimmt der Wert nicht, ist die Gesamt-Einspeisung proportional falsch. Live in der Weboberfläche ohne Neustart anpassbar.

**RS485-Bus:** Alle Geräte am gleichen Bus verbinden: A+ an A+, B- an B-. Abschlusswiderstände am letzten Gerät aktivieren, sofern vorhanden.

**VE.Direct-Aggregator:** Mehrere Victron-MPPTs können über einen Arduino Mega 2560 oder Teensy 4.1 mit `readtext_sendhex`-Firmware einen RS485-Port teilen. zeroinput verwendet `ve_aggregator.py` zum Auslesen und zum Senden von Ladeleistungsgrenzen (`SET <SER#> <watts>`). Jedes Gerät wird per SER# identifiziert — im **RS485**-Tab der Weboberfläche mit `mppt_type: victron_agg` konfigurieren. Mehrere Aggregator-Ports werden unterstützt.

---

## Lastprediktor

Der Lastprediktor (`load_prediction: true` in conf, Standard: false) erkennt zyklische Lasten (Waschmaschine, Herd usw.) per k-Means und stabilisiert die Einspeisung auf LOW-Niveau. Der Motor bezieht seine Zusatzleistung direkt aus dem Netz – ohne Übereinspeisung.

Prediktor-Einstellungen werden teils in `predictor.py` als Modulkonstanten am Dateianfang konfiguriert – kein Neustart nötig, Änderungen werden bei Dateiänderung automatisch übernommen:

| Variable | Beschreibung |
|---|---|
| `STARTUP_S` | Beobachtungszeit vor erster Aktion (Standard: 10 s) |
| `SHORT_PEAK_MAX` | Maximaldauer einer kurzen zyklischen Spitze in s (Standard: 8) |
| `LOG_FILE` | Pfad zur Prediktor-Logdatei (`''` = deaktiviert) |

Diese Einstellungen sind aus `zeroinput.conf` hot-reloadbar (kein Modulneustart nötig):

| Schlüssel | Beschreibung |
|---|---|
| `load_prediction` | Lastprediktor aktivieren (true/false) |
| `min_spread_w` | Mindestspreizung LOW/HIGH in W (Standard: 150) |
| `predictor_log` | Log-Ausgabe und Spaltenköpfe beim Start (true/false, Standard: true) |

---

## Volkszähler-Kanäle (vz_channels)

Die Kanalzuordnung wird in `zeroinput.conf` unter `vz_channels` konfiguriert und in der Weboberfläche unter **VZ-Kanäle** editiert.

Format pro Eintrag: `[Gerät, Schlüssel, vz_kanal, Faktor]`

- **Gerät** – Gerätename aus `rs485` (z. B. `"esmart 60"`), `"combined"` für PV-Gesamt, oder `null` für direkte Variablen
- **Schlüssel** – Datenschlüssel des Geräts (siehe unten)
- **vz_kanal** – UUID-Alias in der Volkszähler-Konfiguration
- **Faktor** – Multiplikator (z. B. `-1` zum Invertieren des Vorzeichens)

### Verfügbare Schlüssel

**Direkte Variablen** (`Gerät: null`):

| Schlüssel | Beschreibung |
|---|---|
| `power_demand` | Gesamt-Einspeiseleistung (W) |
| `zero_shift` | Nullpunkt-Offset (W) |
| `bat_voltage` | Korrigierte Batteriespannung (V) |

**combined** (Summe aller MPPTs):

| Schlüssel | Beschreibung |
|---|---|
| `PPV` | Gesamt-PV-Leistung (W) |
| `Vbat` | Mittlere Batteriespannung (V) |
| `Ibat` | Gesamt-Batteriestrom (A) |
| `Pload` | Gesamt-Lastleistung (W) |

**eSmart3:**

| Schlüssel | Beschreibung |
|---|---|
| `PPV` | PV-Leistung (W) |
| `VPV` | PV-Spannung (V) |
| `Vbat` | Batteriespannung (V) |
| `Ibat` | Batteriestrom (A) |
| `Pload` | Lastleistung (W) |
| `int_temp` | Innentemperatur (°C) |
| `ext_temp` | Außentemperatur (°C) |

**Victron MPPT (konventionell und AGG):**

| Schlüssel | Beschreibung |
|---|---|
| `PPV` | PV-Leistung (W) |
| `VPV` | PV-Spannung (V) |
| `Vbat` | Batteriespannung (V) |
| `Ibat` | Batteriestrom (A) |
| `IL` | Laststrom (A) |

**Temperatursensor (AGG, type: temp):**

| Schlüssel | Beschreibung |
|---|---|
| `ext_temp` | Temperatur des DS18B20-Sensors (°C) |

---

## Entladetimer

Mit `discharge_timer: true` und einer `timer.txt` kann die Einspeisung zeitgesteuert werden. Format pro Zeile:

```
JJJJ-MM-TT HH:MM:SS <Batterie> <Wechselrichter> <Energie_Wh>
```

`0000-00-00` als Datum wird automatisch durch das aktuelle Datum ersetzt – die Regel wird damit täglich ausgeführt.

Batterie- und Wechselrichterwerte werden interpretiert als:
- **> 100** → Watt (absolute Leistung)
- **≤ 100** → Prozent der konfigurierten Maximalleistung

Beispiel:
```
# Datum      Uhrzeit  Batterie  Wechselrichter  Energie_Wh
0000-00-00 22:00:00   50        80              5000
0000-00-00 06:00:00   100       100             0
```
Ab 22:00: Batterie max 50 %, Wechselrichter max 80 %, bis 5000 Wh entladen.
Ab 06:00: Vollbetrieb.

---

## Temperaturalarm

Jeder eSmart3-Laderegler kann Temperaturalarme auslösen. Ein Alarm ist automatisch aktiv wenn Schwellwert und Befehl konfiguriert sind – kein globaler Aktivierungsschalter nötig. Pro Gerät in `rs485` konfigurieren:

```json
"alarm": {
    "temp_int": 45,
    "int_cmd": "mpg321 /home/vzlogger/voice/regler.mp3 &",
    "int_interval": 300,
    "temp_ext": 35,
    "ext_cmd": "echo Hitze außen &",
    "ext_interval": 300
}
```

Ein Alarm wird ausgelöst wenn die gemessene Temperatur den Schwellwert überschreitet **und** der entsprechende Befehl nicht leer ist. Einzelnen Alarm deaktivieren: Befehl leeren oder Schwellwert auf 0 setzen.

---

## Fehlerbehebung

**Live-Ausgabe verfolgen:**
```bash
tail -f /home/vzlogger/zeroinput.html
```

Oder gefiltert ohne HTML-Tags, mit automatischem Löschen bei jedem Schreibzyklus – als Komfortskript `zerooutput.sh` verfügbar:
```bash
zerooutput.sh
```

Führt aus:
```bash
watch -n1 -t 'grep -v "^<!DOCTYPE\|^<html\|^<head\|^<meta\|^<style\|^<body\|^<pre\|^</" /home/vzlogger/zeroinput.html'
```

Oder im Browser über den Volkszähler-Webserver – einmalig Symlink anlegen:
```bash
ln -s /home/vzlogger/zeroinput.html /home/pi/volkszaehler.org/htdocs/zeroinput.html
```
Dann erreichbar unter `http://<hostname>/zeroinput.html`

**systemd-Logs:**
```bash
journalctl -u zeroinput -f
journalctl -u zeroinput -n 100
```

**RS485-Ports belegt (`[Errno 16] Device or resource busy`):**
Eine alte zeroinput-Instanz läuft noch (z. B. aus einem `@reboot`-Crontab-Eintrag).
```bash
ps aux | grep zeroinput        # laufende Instanzen prüfen
crontab -u vzlogger -e         # @reboot-Zeile entfernen
sudo reboot
```

**zeroinput startet nicht:**
```bash
journalctl -u zeroinput -n 50
python3 /home/vzlogger/zeroinput.py -v   # manuell starten
```

**Kein FIFO:**
```bash
mkfifo /tmp/vz/vzlogger.fifo
```

**Keine RS485-Kommunikation:**
```bash
ls -la /dev/ttyACM*
sudo usermod -aG dialout vzlogger
```

**Webserver nicht erreichbar:**
- `webconfig_port` in `zeroinput.conf` prüfen
- zeroinput mit `-httpd` starten
- Port-Konflikt prüfen: `ss -tlnp | grep 8081`
