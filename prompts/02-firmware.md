# 02 Firmware and site setup (reference for Phase 1 tasks)

Paste into Claude Code on branch `hw/firmware` after the kickoff skeleton is merged.

---

Read CLAUDE.md, especially Contracts and Hardware gotchas, and `firmware/`. You own the Arduino Uno firmware, `firmware/WIRING.md`, the camera window mount and the outdoor sensor setup. There is no tabletop model: we watch a real snowy sidewalk near the venue. The Uno talks to the Raspberry Pi hub over USB serial at 115200. Use a non-blocking `loop()` built on `millis()`, print a boot banner, and ignore malformed serial lines without crashing.

Our inventory is an Arduino Uno, 2 HC-SR04, 2 SG90 servos, a Feather ESP32 (spare), breadboards and a Raspberry Pi. The OV7670 camera module in the kit is NOT used. For v2 we have a DS18B20 probe, a 4.7 kOhm resistor and a water sensor, and maybe a DHT22.

Plan first. Write the code, then a README with exact wiring, flashing steps (Arduino IDE and PlatformIO) and a manual test I can follow with the serial monitor.

1. **`firmware/arduino_street/` (v1).**
   - Two HC-SR04 on D2/D3 and D4/D5, fired one after another with a `pulseIn` timeout of 25 ms. Emit `U<i>,<mm>` at 5 Hz, using the median of 3 pings.
   - Optional camera pan: one servo on D9. `S0,<deg>` attaches it, moves it, then detaches 500 ms later. The second servo is a spare.
   - `P` replies `OK`.
   - Optional, only if we get a WS2812B strip: `L<chars>` LED frame through a `const` segment-to-pixel table; call `show()` only when the frame changes.
2. **v2 sensors on the same Uno** (v2: only after Gate 3, once the camera works end to end).
   - DS18B20 on a OneWire pin with the 4.7 kOhm pull-up to 5 V; 10-bit, non-blocking conversion. Emit `T<surface_c>` at 1 Hz.
   - Water sensor: AO on A0, VCC from D12 switched on only while reading. Emit `W<0-1023>` at 1 Hz.
   - DHT22 only if fitted: emit `H<air_c>,<rh>` at 1 Hz.
   - The README includes a black-ice check: an ice pack plus a few drops of water on the plate reads 1 C or below and wet within 60 s.
3. **`firmware/WIRING.md`.**
   - A pin table and a power plan: servos on a 4xAA pack or a 5 V 2 A supply, ground tied to the Uno, never a 9 V battery.
   - The outdoor setup: the ultrasonic sensor in a sealed plastic box aimed along the curb cut, connected by a 5 m USB extension; the Arduino stays indoors where possible.
4. **Camera mount.** Mount the camera indoors against a window that overlooks the sidewalk and curb cut. Shade it from reflections, mark its position, and check the view at night.
5. **Hub-side helper.** `edge/tools/serial_console.py` on the Pi: send `S`, `P` (and `L`), and print `U` (and v2 `T`, `W`, `H`) lines live.
6. **Optional, the spare Feather ESP32.** Leave it unused unless we need a sensor far from the Uno. If so, `firmware/esp32_ultrasonic/` publishes `{"i", "mm"}` over MQTT, with a 1 kOhm / 2 kOhm divider on the echo line.

**Done (by the end of its phase):**
- `serial_console.py` on the Pi shows live distances from both ultrasonic sensors.
- The curb-cut sensor reports distances reliably from outdoors.
- The camera mount is fixed and the view is clear by day and night.

Survey the site with Estella: choose 4 to 6 sidewalk segments visible from the window, including the curb cut, and record them in `config/segments.json`. Get permission from the venue before mounting anything.
