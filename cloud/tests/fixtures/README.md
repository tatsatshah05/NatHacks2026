# Recorded fixtures

One folder per source, holding recorded API responses. Tests and `make dev` read these; they never call the network.

`weather/freeze_thaw/` is a real past night (Edmonton, November 5 to 6, 2025) from Open-Meteo's archive, recorded unchanged by `scripts/record_snapshot.py --weather-archive 2025-11-05`; `WEATHER_REPLAY=freeze_thaw` serves it (docs/OP-305.md).
