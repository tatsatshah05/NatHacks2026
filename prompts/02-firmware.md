# 02 Firmware and model (hardware owner)

Paste into Claude Code on branch `hw/firmware` after the kickoff skeleton is merged.

---

Read CLAUDE.md, especially Contracts and Hardware gotchas, and `firmware/`. You own three Arduino-framework sketches (PlatformIO preferred; also include Arduino IDE instructions) and `firmware/WIRING.md`. Every sketch must use a non-blocking `loop()`, reconnect on its own, print a boot banner over serial, and keep secrets in a git-ignored `secrets.h` (commit `secrets.example.h`).

Plan first. For each sketch, write the code, then a README with the exact wiring, the flashing steps and a manual test procedure I can follow with a serial monitor.

1. **`firmware/esp32cam/`** (AI-Thinker ESP32-CAM, OV2640, PSRAM). Base it on the official CameraWebServer example, trimmed down:
   - Wi-Fi from `secrets.h`; mDNS name `pathsense-cam`.
   - `/capture` (JPEG), `/stream` on port 81, `/control`; SVGA resolution, JPEG quality 12.
   - Flash LED (GPIO 4) forced off.
   - The README covers the 5 V / 2 A supply, holding IO0 to GND to flash, and what brownout resets look like.
2. **`firmware/esp32_ice_station/`** (ESP32 dev board). **v2 only.** v1 is camera plus weather (decided Nov 6), so build this sketch only after Gate 3, once the camera works end to end. For v1, the spare ESP32 can optionally become `firmware/esp32_ultrasonic/`: it moves 1 or 2 HC-SR04s onto the spare ESP32 over Wi-Fi, with a 1 kOhm / 2 kOhm divider on each echo line, publishing `{"i", "mm"}` to `pathsense/sensor/ultra-<i>`. Then set up the Path B props: a clear plastic film cut to one segment, and the lamp angled so the film shows a highlight on camera.

   Path A version:
   - DS18B20 waterproof probe on a OneWire pin with a 4.7 kOhm pull-up; 10-bit, non-blocking conversion.
   - Water sensor on an ADC1 pin (GPIO 32 to 39), with a calibrated dry/wet threshold stored in `secrets.h`.
   - DHT22 (or DHT11) for air temperature and humidity; compute the dew point with the Magnus formula.
   - Publish `{"surface_c", "air_c", "rh", "wet", "dew_c", "ts"}` to `pathsense/sensor/ice-1` every 500 ms with PubSubClient and ArduinoJson v7. Use a last will of `offline` on `pathsense/sensor/ice-1/status` and publish `online` on connect.
   - The README includes a black-ice demo check: ice pack plus a few drops of water on the plate should read surface temperature at or below 1 C and wet within 60 s.
3. **`firmware/arduino_street/`** (Uno or Nano, USB serial at 115200):
   - Two or three HC-SR04 sensors, fired one after another, `pulseIn` timeout 25 ms. Each emits `U<i>,<mm>` at 5 Hz (median of 3 pings).
   - WS2812B via Adafruit_NeoPixel. On `L<chars>`, map each segment character through a `const` table (segment index to a pixel range) and set colours: `G` green, `W` white, `C` cyan, `R` red, `B` blue, `.` off. Call `show()` only when the frame changed.
   - Servo: `S<deg>` means attach, move, then detach 500 ms later (prevents jitter while the LEDs update).
   - `P` replies `OK`. Ignore malformed lines without crashing.
4. **`firmware/WIRING.md`.** A pin table for each board; a power plan (separate 5 V supply for the LEDs, common ground, 330 Ohm on LED data, 1000 uF across LED power, a 5 V / 2 A supply for the CAM); where each sensor sits on the model (ultrasonics aimed along the curb-cut segments, the ice station plate on one segment, the servo plow arm beside the snow segment).
5. **Hub-side helpers** (Python, in `edge/tools/`): `serial_console.py` to send `L`, `S` and `P` and print `U` lines; `mqtt_watch.py` to print ice-station messages.

**Done (Gate 2, hour 18):**
- The CAM stream works on our router.
- Ice-station JSON arrives on the hub's Mosquitto at 2 Hz.
- `serial_console.py` shows live ultrasonic distances and lights a test LED pattern.
- The servo sweeps on command without the LEDs flickering.

Also build the tabletop model with the team, to match `config/model_map.json`: matte dark-gray sidewalks, ArUco markers glued flat at the four corners, segment numbers written small beside each segment, and the camera mount fixed at about 70 cm.
