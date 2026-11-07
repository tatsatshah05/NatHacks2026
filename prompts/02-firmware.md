# 02 Firmware and model (reference for Phase 1 tasks)

Paste into Claude Code on branch `hw/firmware` after the kickoff skeleton is merged.

---

Read CLAUDE.md, especially Contracts and Hardware gotchas, and `firmware/`. You own the Arduino Uno firmware, `firmware/WIRING.md`, the camera mount and the physical model. The Uno talks to the Raspberry Pi hub over USB serial at 115200. Use a non-blocking `loop()` built on `millis()`, print a boot banner, and ignore malformed serial lines without crashing.

Our inventory is an Arduino Uno, 2 HC-SR04, 2 SG90 servos, a Feather ESP32 (spare), breadboards and a Raspberry Pi. The OV7670 camera module in the kit is NOT used. For v2 we have a DS18B20 probe, a 4.7 kOhm resistor and a water sensor, and maybe a DHT22.

Plan first. Write the code, then a README with exact wiring, flashing steps (Arduino IDE and PlatformIO) and a manual test I can follow with the serial monitor.

1. **`firmware/arduino_street/` (v1).**
   - Two HC-SR04 on D2/D3 and D4/D5, fired one after another with a `pulseIn` timeout of 25 ms. Emit `U<i>,<mm>` at 5 Hz, using the median of 3 pings.
   - Two servos: plow on D9, barrier on D10 (move the DS18B20 to another free pin in v2 if needed and update WIRING.md). `S<i>,<deg>` attaches servo `i`, moves it, then detaches 500 ms later to stop jitter.
   - `P` replies `OK`.
   - Optional, only if we get a WS2812B strip: `L<chars>` LED frame through a `const` segment-to-pixel table; call `show()` only when the frame changes.
2. **v2 sensors on the same Uno** (v2: only after Gate 3, once the camera works end to end).
   - DS18B20 on a OneWire pin with the 4.7 kOhm pull-up to 5 V; 10-bit, non-blocking conversion. Emit `T<surface_c>` at 1 Hz.
   - Water sensor: AO on A0, VCC from D12 switched on only while reading. Emit `W<0-1023>` at 1 Hz.
   - DHT22 only if fitted: emit `H<air_c>,<rh>` at 1 Hz.
   - The README includes a black-ice check: an ice pack plus a few drops of water on the plate reads 1 C or below and wet within 60 s.
3. **`firmware/WIRING.md`.**
   - A pin table and a power plan: servos on a 4xAA pack or a 5 V 2 A supply, ground tied to the Uno, never a 9 V battery.
   - Where each part sits on the model: ultrasonics aimed along the two curb-cut segments, the plow arm beside the snow segment, the barrier at a sidewalk entrance, and the v2 plate on one segment.
4. **Camera mount.** A fixed overhead mount about 70 cm above the board, for whichever camera we pick (Pi Camera Module on a 30 to 50 cm ribbon, or a phone in a clamp). Mark its position so it can be put back exactly.
5. **Hub-side helper.** `edge/tools/serial_console.py` on the Pi: send `S`, `P` (and `L`), and print `U` (and v2 `T`, `W`, `H`) lines live.
6. **Optional, the spare Feather ESP32.** Leave it unused unless we need a sensor far from the Uno. If so, `firmware/esp32_ultrasonic/` publishes `{"i", "mm"}` over MQTT, with a 1 kOhm / 2 kOhm divider on the echo line.

**Done (by the end of its phase):**
- `serial_console.py` on the Pi shows live distances from both ultrasonic sensors.
- Both servos move on command without jitter, powered from their own supply.
- The model is built, with the camera mount fixed.

Build the tabletop model with Estella to match `config/model_map.json`: matte dark-gray sidewalks, ArUco markers glued flat at the four corners, segment numbers written small beside each segment, and a clear film cut for the black-ice segment with the lamp angled so it shows a highlight on camera.
