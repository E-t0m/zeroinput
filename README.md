# zeroinput v2.3
Zero export (Nulleinspeisung) for PV and battery systems: local control of grid-tie inverters and MPPT chargers, no cloud.

| English | Deutsch |
|----------|---------|
| [Introduction](docs/en/about.md) | [Vorstellung](docs/de/about.md) |
| [Installation manual](docs/en/installation.md) | [Installationsanleitung](docs/de/installation.md) |
| [Specifications](docs/en/spec.md) | [Spezifikationen](docs/de/spec.md) |
| [Load predictor](docs/en/predictor_spec.md) | [Lastprediktor](docs/de/predictor_spec.md) |

<picture>
	<source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/dirt_shift_day_dark.png">
	<img src="docs/screenshots/dirt_shift_day.png" alt="dirt_shift on a November day: PV covers the house in the morning, the battery takes over when the grid is dirtiest">
</picture>

dirt_shift moves the battery discharge into the hours with the dirtiest grid power (purple, right axis).

### [Screenshots of the webconfig](docs/screenshots.md)
![scheme](https://github.com/user-attachments/assets/ee5862bb-375d-429d-96a0-52863d9d64b7)

This script uses a https://volkszaehler.org/ installation for energy meter input and data display.

## Quick start

```bash
sudo mkdir /opt/zeroinput
sudo chown vzlogger:vzlogger /opt/zeroinput
sudo -u vzlogger git clone https://github.com/E-t0m/zeroinput.git /opt/zeroinput
cd /opt/zeroinput
sudo -u vzlogger cp zeroinput.conf.starter zeroinput.conf
```

zeroinput runs directly from the cloned folder `/opt/zeroinput` as user `vzlogger` and is updated with `git pull`. Your own configuration files are not part of the repository. All steps are in the [installation manual](docs/en/installation.md).

## Add-ons

| | English | Deutsch |
|---|----------|---------|
| **dirt_shift** - shifts battery discharge into the hours with the dirtiest grid power | [Introduction](docs/en/dirt_shift_about.md), [Installation](docs/en/dirt_shift_installation.md), [Specification](docs/en/dirt_shift_spec.md), [Console output](docs/en/dirt_shift_console_output.md) | [Vorstellung](docs/de/dirt_shift_about.md), [Installation](docs/de/dirt_shift_installation.md), [Spezifikation](docs/de/dirt_shift_spec.md) |
| **tibber** (testing) - shifts battery discharge into the most expensive hours of a Tibber tariff | [Description](docs/en/tibber.md) | |

## Repository layout

| Path | Content |
|---|---|
| `zeroinput.py` and the other `*.py` files | the control loop, drivers, predictor and webconfig server |
| `zeroinput.conf.starter`, `timer.txt.example` | templates for your own `zeroinput.conf` and `timer.txt` |
| `zeroinput.service` | systemd service |
| `zerooutput.sh` | live status in the terminal |
| `dirt_shift/` | dirt_shift add-on |
| `tibber/` | tibber add-on (testing) |
| `tools/` | helper scripts |
| `tests/` | tests that run without hardware |
| `docs/` | documentation in English and German, screenshots |

## An example day in the volkszaehler browser
- red shows the imported power from the net
- green is the generated power from the inverters
- yellow shows the PV power

![basic](https://user-images.githubusercontent.com/110770475/183761064-bd2632d8-4438-4288-b05c-e8126de78463.png)

Version 1 is kept under the tag [v1](https://github.com/E-t0m/zeroinput/tree/v1/v1).

## License

zeroinput is released under the [MIT License](LICENSE).

The eSmart3 driver in `charger_drivers.py` is an own implementation; its frame layout follows the protocol description in [skagmo/esmart_mppt](https://github.com/skagmo/esmart_mppt).

`vebus.py` for the Victron MultiPlus-II is not part of the repository and not covered by the MIT License. It is fetched during installation from [martiby/multiplus2](https://github.com/martiby/multiplus2) (Martin Steppuhn).
