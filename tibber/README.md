# tibber

**Status: testing.** These scripts are not verified in continuous operation. Check the timer file they write before relying on it.

Price-driven discharge control for zeroinput with a Tibber tariff: `tibber_prices.py` fetches the prices, `tib_zero_tas.py` writes zeroinput's timer file and sets tasmota timers, `tibber_2_vz.py` logs the price to [volkszaehler.org](https://volkszaehler.org/).

Description and setup: [docs/en/tibber.md](../docs/en/tibber.md)
