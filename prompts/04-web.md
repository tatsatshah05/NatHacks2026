# 04 Web app (web and pitch owner)

Paste into Claude Code on branch `web/app` after the kickoff skeleton is merged.

---

Read CLAUDE.md, especially the Accessibility requirements, and all of `web/`. You own the Next.js App Router app deployed on Vercel. Many judges will open it on their own phones from a QR code, so it must be fast and clear, and it must be accessible itself. It is an accessibility product. Mobile first.

Plan first, then build in this order. Run `make types && npm run lint && npm run typecheck` after each step.

1. **API layer.** Write a typed client in `src/lib/api.ts` that uses only the generated `api-types.ts`. Add a `useLive()` hook for `NEXT_PUBLIC_WS_URL` with auto-reconnect and exponential backoff, plus a visible connection badge ("Live" / "Reconnecting").
2. **Planner (`/`).**
   - Full-screen MapLibre map with the OpenFreeMap style. The hazard layer uses colour plus dash pattern plus an icon per state, with a legend.
   - From and To pickers: the places list plus "tap the map".
   - A profile picker with plain names and icons: Wheelchair, Walker or cane, Low vision, Standard.
   - Up to 3 route cards showing minutes, distance, hazards avoided and warnings. Selecting a card highlights its route; each card has a "Steps" list in text.
   - Buttons on every card: **Open in Google Maps** and **Open in Apple Maps** (the Apple button notes that it may not keep our detour).
   - When the active route changes live: update the map, announce the reason in an `aria-live="assertive"` region, speak it with `speechSynthesis` (toggle in the header, on by default), and call `navigator.vibrate([200, 100, 200])`.
   - Add a weather strip from `GET /api/weather` (for example, "Black-ice watch: freeze-thaw after rain"). When `mode` is `replay`, show a clear **Replay** badge on every page.
3. **Report a hazard.** A floating button: pick Snow, Ice or Blocked, confirm the location, `POST /api/reports`, then show a toast.
4. **Alerts (`/alerts`) and PWA.**
   - Add `manifest.webmanifest` and icons.
   - `public/sw.js` handles `push` (show the notification) and `notificationclick` (open the deep link).
   - "Save this route": subscribe with the VAPID public key, then `POST /api/push/subscribe` and `/api/saved-routes`, using an anonymous `client_id` kept in localStorage.
   - Detect iOS without home-screen install and show "Add to Home Screen to get alerts" instead of failing silently.
   - Add a "Send me a test alert" button.
5. **City dashboard (`/city`).** A live hazard list (state, source, age, confidence), the clear-first ranking from `/api/priority` with a **Dispatch crew** button (asks once for the admin key and keeps it in sessionStorage), and an event timeline. Show `http://localhost:8001/video` in an iframe only when the page runs on the hub.
6. **Judge page (`/demo`).** A huge QR code for the production URL, the one-line pitch and "Try it: pick Wheelchair, Home to Clinic".
7. **Accessibility pass.**
   - Keyboard-only walkthrough of every page.
   - axe DevTools (or `@axe-core/playwright`) with zero serious issues.
   - 200% zoom, `prefers-reduced-motion`, and contrast checks.
   - Screen-reader labels on all map controls.
   - Write the results in `web/ACCESSIBILITY.md` for the Devpost.
8. **Vercel.** Root dir `web`; set the env vars for Production and Preview; confirm the WebSocket connects from the deployed site to Render (`wss://`).

**Done (Gate 2, hour 18):** on a phone, the deployed site shows the live map and route, and a sim hazard reroutes it without a refresh.

**Done (Gate 3, hour 30):**
- A push alert arrives on Android and opens the right route.
- The Google Maps hand-off follows our detour.
- `/city` dispatch works.
- Lint and typecheck pass.

You also own the pitch assets:
- Slides with every statistic linked to its source in the notes.
- The backup demo video, recorded at hour 40.
- The Devpost write-up.
