# OP-114: Main screen wireframes

Low-fidelity, content-first wireframes for the four screens that make up the
core PathSense experience. These are intentionally structural: they define
information priority, interaction order, and responsive behaviour before visual
polish.

## Shared navigation and priorities

```text
┌──────────────────────────────────────────────────────────────┐
│ PathSense                         Plan  Alerts  City  Menu   │
├──────────────────────────────────────────────────────────────┤
│ Main content                                                  │
└──────────────────────────────────────────────────────────────┘
```

- **Primary navigation:** Plan, Alerts, City. The current destination is
  identified by text and a visible focus/active treatment, not colour alone.
- **Mobile:** navigation becomes a labelled Menu button. The menu is opened
  with Enter/Space, closed with Escape, and returns focus to the trigger.
- **Persistent status:** stale data and live-update status appear as text near
  the relevant content. A live dot is supplementary, never the only signal.
- **Order:** safety-critical conditions first, then the action, then detail and
  attribution.

## 1. Planner

### Desktop

```text
┌──────────────────────────────────────────────────────────────┐
│ PathSense                 Plan  Alerts  City                  │
├───────────────────────────────┬──────────────────────────────┤
│ Plan a route                  │ Map                           │
│                               │                              │
│ From                          │       [origin]                │
│ [ Use my location       ]     │              ─── route ───     │
│                               │                    [dest]     │
│ To                            │                              │
│ [ Search address/place   ]     │  +/−  Legend  12 conditions  │
│                               │                              │
│ How are you travelling?       ├──────────────────────────────┤
│ ( ) Wheelchair  ( ) Walker    │ Current conditions            │
│ ( ) Low vision  ( ) Standard  │ [!] Elevator out — 3 min ago  │
│                               │ [❄] Snow risk — 12 min ago    │
│ [ Find accessible route ]     │ [ View all conditions ]        │
│                               │                              │
│ Recent routes                 │                              │
│ Central Station → Clinic      │                              │
└───────────────────────────────┴──────────────────────────────┘
```

### Mobile

```text
┌────────────────────────┐
│ PathSense        Menu  │
├────────────────────────┤
│ Plan a route           │
│ From                   │
│ [ Use my location ]    │
│ To                     │
│ [ Search address... ]  │
│ Profile                │
│ [ Wheelchair       v ] │
│ [ Find accessible   ] │
│   route               │
├────────────────────────┤
│ Conditions near route  │
│ [!] Elevator out       │
│ [ View map ]           │
├────────────────────────┤
│ Recent routes          │
└────────────────────────┘
```

**Priority and behaviour**

1. Origin, destination, and mobility profile are the first decision.
2. The primary action remains visible without scrolling on a phone.
3. Address search exposes a labelled results list; results are keyboard
   selectable and never rely on map tapping.
4. The map is a supporting visual. Conditions are repeated as a text list.
5. A validation message is placed directly after the invalid field and is
   announced to assistive technology.

## 2. Route report

### Desktop

```text
┌──────────────────────────────────────────────────────────────┐
│ ← Back to planner       Accessible route                     │
├───────────────────────────────┬──────────────────────────────┤
│ Central Station → Clinic      │ Route map                     │
│ Wheelchair · 18 min · 1.4 km  │                              │
│                               │      ───── route ─────       │
│ [ Save route ] [ Share ]      │                              │
│                               │                              │
│ Safety summary                │                              │
│ ✓ No known blocked paths      │                              │
│ ! 1 caution: snow nearby      │                              │
│                               ├──────────────────────────────┤
│ Step-by-step                 │ Route alternatives              │
│ 1. Exit Central Station      │ [ Fastest 18 min ]             │
│ 2. Turn right on 100 St      │ [ Fewer barriers 21 min ]      │
│ 3. Cross at accessible ramp  │ [ View comparison ]            │
│                               │                              │
│ Conditions on this route     │                              │
│ [!] Snow complaint · 100 St  │                              │
└───────────────────────────────┴──────────────────────────────┘
```

### Mobile

```text
┌────────────────────────┐
│ ← Planner   Route      │
├────────────────────────┤
│ Central Station       │
│ → Clinic              │
│ 18 min · 1.4 km       │
│ [ Save route ]        │
├────────────────────────┤
│ Safety summary        │
│ ✓ No blocked paths    │
│ ! 1 caution nearby    │
├────────────────────────┤
│ [ Map ] [ List ]      │
│                      │
│ Step 1 of 3           │
│ Exit Central Station  │
│ [ Previous ] [ Next ] │
├────────────────────────┤
│ Other routes          │
│ Fewer barriers · 21m  │
└────────────────────────┘
```

**Priority and behaviour**

1. Endpoints, travel profile, duration, and safety summary come before the
   map and turn-by-turn detail.
2. The route report has a complete text mode with numbered steps and all
   obstruction details.
3. Route alternatives are buttons with a text comparison, not colour-coded
   lines alone.
4. Saving announces success in a status region. Sharing has a labelled
   fallback when the device share API is unavailable.
5. The mobile stepper exposes the current step and total, and supports
   keyboard operation.

## 3. Alerts

### Desktop

```text
┌──────────────────────────────────────────────────────────────┐
│ PathSense                 Plan  Alerts  City                  │
├──────────────────────────────────────────────────────────────┤
│ Alerts                                                       │
│ Stay informed when a saved route changes.                    │
│                                                              │
│ Saved routes                       Notification settings      │
│ ┌──────────────────────────────┐   ┌────────────────────────┐ │
│ │ Home → Work                  │   │ Route changes       [on]│ │
│ │ 2 changes this week          │   │ Blocked paths      [on]│ │
│ │ [ View route ] [ Remove ]    │   │ Quiet hours 22:00–07:00│ │
│ └──────────────────────────────┘   └────────────────────────┘ │
│                                                              │
│ Recent alerts                                                │
│ [!] Elevator outage · Central Station · 10:42                │
│     Your saved route may be affected. [Review route]         │
│ [i] Snow risk cleared · 100 St · Yesterday                   │
└──────────────────────────────────────────────────────────────┘
```

### Mobile

```text
┌────────────────────────┐
│ PathSense        Menu  │
├────────────────────────┤
│ Alerts                 │
│ [ Manage notifications]│
├────────────────────────┤
│ Saved routes           │
│ Home → Work            │
│ 2 changes this week   │
│ [ View ] [ Remove ]    │
├────────────────────────┤
│ Recent alerts          │
│ [!] Elevator outage    │
│ Central Station        │
│ [ Review route ]       │
├────────────────────────┤
│ Notification settings  │
│ Route changes     [on]│
└────────────────────────┘
```

**Priority and behaviour**

1. New or safety-relevant alerts appear before settings.
2. Each alert includes what changed, where, when, affected route, and a
   direct action.
3. Toggle controls have visible on/off text and an accessible name; state is
   not conveyed by colour alone.
4. New alerts are announced without moving focus unexpectedly. Users can
   pause live announcements.
5. Removing a saved route requires confirmation and provides an undo action.

## 4. City view

### Desktop

```text
┌──────────────────────────────────────────────────────────────┐
│ PathSense                 Plan  Alerts  City                  │
├──────────────────────────────────────────────────────────────┤
│ City conditions                         Updated 10:45         │
│ [ Search the map ] [ Filter: All v ]                         │
├───────────────────────────────┬──────────────────────────────┤
│ Priority conditions            │ Edmonton map                 │
│ 12 current obstructions        │                              │
│                               │ [patterned markers + labels] │
│ [!] 4 blocked                 │                              │
│ [❄] 5 snow / ice risks        │                              │
│ [!] 3 cautions                │                              │
│                               │                              │
│ [ View accessible list ]      │                              │
├───────────────────────────────┴──────────────────────────────┤
│ Legend: icon + pattern + text for every condition             │
└──────────────────────────────────────────────────────────────┘
```

### Mobile

```text
┌────────────────────────┐
│ PathSense        Menu  │
├────────────────────────┤
│ City conditions        │
│ Updated 10:45          │
│ [ Search ] [ Filter ]  │
├────────────────────────┤
│ [ Map ] [ List ]       │
│                        │
│     map / markers      │
│                        │
├────────────────────────┤
│ Priority conditions    │
│ [!] 4 blocked          │
│ [❄] 5 snow / ice risks │
│ [ View full list ]     │
└────────────────────────┘
```

**Priority and behaviour**

1. The city-level count, freshness, and highest-risk conditions come first.
2. Map and list are equivalent views. The list supports filtering, keyboard
   navigation, and screen-reader access.
3. Every condition has an icon, pattern, and text label. Colour reinforces
   severity but is never the only distinction.
4. Search and filters have labelled controls, a clear-all action, and a
   result count announced after changes.
5. At 200% zoom the map and list stack vertically; no essential content is
   hidden or clipped.

## Accessibility review handoff

Review with Estella before Phase 2 starts:

- [ ] Confirm reading order and heading hierarchy on all four screens.
- [ ] Confirm keyboard order, focus return, and Escape behaviour for menus,
      dialogs, filters, and route alternatives.
- [ ] Confirm all alerts and route changes have a concise live announcement.
- [ ] Confirm text alternatives are complete when maps are unavailable.
- [ ] Confirm controls remain at least 44px and layouts work at 200% zoom.
- [ ] Confirm colour contrast and non-colour indicators in the implemented UI.

**Review status:** wireframes prepared for accessibility review; Estella
approval should be recorded on the OP-114 pull request before Phase 2 begins.
