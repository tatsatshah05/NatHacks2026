# OpenPath User Stories

**53 user stories · 187 story points · 6 people · 4 phases**

Version 2.0 · November 7, 2026 · natHACKS 2026. Every story is a GitHub issue with acceptance criteria and live status; click an ID to open it. Full details are in [PLAN.md](../PLAN.md).

## 1. Who Owns What

Story points per person per phase. One point is about one focused hour; each person plans for about 8 points per phase.

| Person | GitHub | Phase 1 | Phase 2 | Phase 3 | Phase 4 | Stories | Points |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Tatsat | @tatsatshah05 | 8 | 7 | 8 | 8 | 9 | 31 |
| Ipsa | @ipsamanhas | 8 | 8 | 8 | 8 | 10 | 32 |
| Estella | @estella1221 | 8 | 8 | 8 | 8 | 9 | 32 |
| Kaleb | @kalebpham | 8 | 8 | 8 | 8 | 9 | 32 |
| Kashish | @kashishgupta2911 | 7 | 8 | 7 | 7 | 8 | 29 |
| Matthew | Not yet a collaborator | 8 | 8 | 8 | 7 | 8 | 31 |
| **Team** | | **47** | **47** | **47** | **46** | **53** | **187** |

Phases: **1. Data Foundation** (hours 0–10) · **2. Vision and Routing** (hours 10–22) · **3. Web App and Alerts** (hours 22–34) · **4. Production and Launch** (hours 34–48).

## 2. All User Stories

### Phase 1: Data Foundation (hours 0–10)

**Checkpoint:** Live items from every Must source appear in `GET /api/obstructions`; `GET /api/status` shows each source's freshness; the city walking network loads within its memory budget; the API and web app deploy from `main` with CI passing; the 511 Alberta camera list is counted.

| ID | User story | Owner | Points | Priority |
| --- | --- | --- | --- | --- |
| [OP-101](https://github.com/tatsatshah05/NatHacks2026/issues/29) | **Obstruction contract and store**<br>As a developer, I want one shared format for every obstruction, so that each data source plugs in the same way and the app never depends on where an item came from. | Tatsat | 3 | Must |
| [OP-102](https://github.com/tatsatshah05/NatHacks2026/issues/30) | **Deployable skeleton with CI**<br>As the team, we want the API and web app deployed from day one with automatic checks, so that every merge is tested and the live site never breaks. | Matthew | 5 | Must |
| [OP-103](https://github.com/tatsatshah05/NatHacks2026/issues/31) | **Source scheduler and status page**<br>As a user, I want to see how fresh each data source is, so that I can trust what the app tells me. | Matthew | 3 | Must |
| [OP-104](https://github.com/tatsatshah05/NatHacks2026/issues/32) | **City walking network**<br>As a pedestrian anywhere in Edmonton, I want the app to know every walkable path, including stairs, crossings and elevators, so that routes match how I move. | Tatsat | 5 | Must |
| [OP-105](https://github.com/tatsatshah05/NatHacks2026/issues/33) | **LRT elevator and escalator adapter**<br>As a wheelchair user, I want to know when an elevator at an LRT station is out of service, so that I am never sent to a station I cannot use. | Kaleb | 3 | Must |
| [OP-106](https://github.com/tatsatshah05/NatHacks2026/issues/34) | **Map LRT elevators to the walking network**<br>As a wheelchair user, I want a broken elevator to change my route, not just show a warning, so that the route I get is truly step-free. | Kaleb | 3 | Must |
| [OP-107](https://github.com/tatsatshah05/NatHacks2026/issues/35) | **Construction sidewalk closures adapter**<br>As a pedestrian, I want sidewalk closures from construction permits on my route, so that I do not walk into a closed sidewalk. | Kaleb | 2 | Must |
| [OP-108](https://github.com/tatsatshah05/NatHacks2026/issues/36) | **Traffic disruptions adapter**<br>As a pedestrian, I want to know about road closures and incidents along my route, so that I can expect detours at crossings. | Ipsa | 2 | Must |
| [OP-109](https://github.com/tatsatshah05/NatHacks2026/issues/37) | **Weather and black-ice watch**<br>As someone walking in winter, I want a warning when the weather makes black ice likely, so that I can choose a safer route or time. | Estella | 3 | Must |
| [OP-110](https://github.com/tatsatshah05/NatHacks2026/issues/38) | **311 snow complaints and trail closures**<br>As a pedestrian, I want to see where neighbours report snowy sidewalks and where trails are closed, so that I know which areas to avoid. | Estella | 3 | Must |
| [OP-111](https://github.com/tatsatshah05/NatHacks2026/issues/39) | **Snow-clearing status adapter**<br>As a wheelchair user, I want to know when a plow has just passed a road on my route, so that I am warned about snow ridges across curb ramps. | Estella | 2 | Should |
| [OP-112](https://github.com/tatsatshah05/NatHacks2026/issues/40) | **Public camera access**<br>As the vision team, we want legal, reliable access to public road cameras, so that the model can read winter conditions without our own hardware. | Ipsa | 3 | Must |
| [OP-113](https://github.com/tatsatshah05/NatHacks2026/issues/41) | **Web app shell and live map**<br>As a user, I want a fast, accessible map that shows every current obstruction with icons and labels, so that I can see the city's conditions at a glance. | Kashish | 5 | Must |
| [OP-114](https://github.com/tatsatshah05/NatHacks2026/issues/42) | **Wireframes for the main screens**<br>As a user, I want the most important information first on every screen, so that I can decide quickly and safely. | Kashish | 2 | Must |
| [OP-115](https://github.com/tatsatshah05/NatHacks2026/issues/43) | **Label guide and labelling tool**<br>As a labeller, I want a clear label guide and a fast keyboard tool, so that I can label hundreds of camera images consistently. | Ipsa | 3 | Must |

### Phase 2: Vision and Routing (hours 10–22)

**Checkpoint:** The model meets its target on cameras it never saw in training; camera readings and the black-ice watch reach the obstruction feed; `GET /api/route` returns routes with alternates and a full report; a test elevator outage reroutes a wheelchair route.

| ID | User story | Owner | Points | Priority |
| --- | --- | --- | --- | --- |
| [OP-201](https://github.com/tatsatshah05/NatHacks2026/issues/44) | **Collect and label training data**<br>As the vision team, we want a labelled set of real camera images across day, night, dry, wet, snow and ice, so that the model learns the conditions it will see. | Estella | 5 | Must |
| [OP-202](https://github.com/tatsatshah05/NatHacks2026/issues/45) | **Train the road-condition model**<br>As a user, I want the app to recognise snow and ice from camera images, so that warnings come from what cameras see, not only from the forecast. | Ipsa | 5 | Must |
| [OP-203](https://github.com/tatsatshah05/NatHacks2026/issues/46) | **Evaluate the model and write the model card**<br>As a user and a judge, I want honest numbers on how well the model works, so that I know when to trust it. | Ipsa | 3 | Must |
| [OP-204](https://github.com/tatsatshah05/NatHacks2026/issues/47) | **Camera vision worker**<br>As a user, I want camera readings refreshed automatically, so that the map shows what the road looks like now. | Kaleb | 5 | Must |
| [OP-205](https://github.com/tatsatshah05/NatHacks2026/issues/48) | **Black-ice risk from cameras and weather**<br>As someone walking in winter, I want black-ice warnings that combine what cameras see with the temperature, so that warnings are specific to a place and time. | Kaleb | 3 | Must |
| [OP-206](https://github.com/tatsatshah05/NatHacks2026/issues/49) | **Match obstructions to the network**<br>As a user, I want every obstruction tied to the exact paths it affects, so that routes avoid the right streets. | Matthew | 5 | Must |
| [OP-207](https://github.com/tatsatshah05/NatHacks2026/issues/50) | **Profile routing with alternates**<br>As a wheelchair, walker, low-vision or standard user, I want the best route for how I move plus up to two alternates with distance and time, so that I can choose. | Tatsat | 5 | Must |
| [OP-208](https://github.com/tatsatshah05/NatHacks2026/issues/51) | **Route report**<br>As a user, I want a list of every obstruction on or near my route, grouped by indoor, sidewalks, roads and weather, so that I know exactly what I will face. | Matthew | 3 | Must |
| [OP-209](https://github.com/tatsatshah05/NatHacks2026/issues/52) | **Google Maps and Apple Maps hand-off**<br>As a user, I want to open my route in Google Maps or Apple Maps, so that I get turn-by-turn directions in an app I already know. | Tatsat | 2 | Must |
| [OP-210](https://github.com/tatsatshah05/NatHacks2026/issues/53) | **Planner screens with mock data**<br>As a user, I want to enter where I am and where I am going, choose how I move, and compare routes, so that planning takes seconds. | Kashish | 5 | Must |
| [OP-211](https://github.com/tatsatshah05/NatHacks2026/issues/54) | **Route report screen**<br>As a screen-reader user, I want the route report as a clear text list, so that the map is never the only way to get information. | Kashish | 3 | Must |
| [OP-212](https://github.com/tatsatshah05/NatHacks2026/issues/55) | **Accessibility review of designs**<br>As a disabled user, I want accessibility designed in from the start, so that the app works for me on day one. | Estella | 3 | Must |

### Phase 3: Web App and Alerts (hours 22–34)

**Checkpoint:** On a phone, the deployed site plans a route anywhere in Edmonton, shows the full report, updates live when an obstruction changes, sends a push alert for a saved route, and opens the route in Google Maps.

| ID | User story | Owner | Points | Priority |
| --- | --- | --- | --- | --- |
| [OP-301](https://github.com/tatsatshah05/NatHacks2026/issues/56) | **Connect the planner to the live API**<br>As a user, I want real routes and reports on my phone, so that I can rely on the app for a trip. | Kashish | 5 | Must |
| [OP-302](https://github.com/tatsatshah05/NatHacks2026/issues/57) | **Address and place search**<br>As a user, I want to type an address or a place name, so that I do not have to find it on the map. | Matthew | 3 | Must |
| [OP-303](https://github.com/tatsatshah05/NatHacks2026/issues/58) | **Live updates**<br>As a user with a route open, I want it to update by itself when something changes, so that I learn about a new obstruction before I reach it. | Matthew | 5 | Must |
| [OP-304](https://github.com/tatsatshah05/NatHacks2026/issues/59) | **Saved routes and push alerts (server)**<br>As a commuter, I want an alert when my usual route becomes blocked or much slower, so that I can leave early or go another way. | Tatsat | 5 | Must |
| [OP-305](https://github.com/tatsatshah05/NatHacks2026/issues/60) | **Replay mode**<br>As the demo team, we want to replay recorded real data from every source, so that the demo works when the weather does not cooperate, and judges always know it is a replay. | Tatsat | 3 | Must |
| [OP-306](https://github.com/tatsatshah05/NatHacks2026/issues/61) | **Alerts page and push client**<br>As a commuter, I want to save a route and turn on alerts in two taps, so that I am told when it changes. | Estella | 5 | Must |
| [OP-307](https://github.com/tatsatshah05/NatHacks2026/issues/62) | **Accessibility settings**<br>As a low-vision user, I want to control speech, text size and motion, so that the app works the way I need. | Estella | 3 | Must |
| [OP-308](https://github.com/tatsatshah05/NatHacks2026/issues/63) | **Community reports with photo check**<br>As a pedestrian, I want to report snow, ice or a blocked path in a few taps, with an optional photo, so that others are warned about places no camera can see. | Ipsa | 5 | Should |
| [OP-309](https://github.com/tatsatshah05/NatHacks2026/issues/64) | **Camera readings on the map**<br>As a user, I want to see each public camera's reading and when it was taken, so that I understand where an ice warning comes from. | Ipsa | 3 | Must |
| [OP-310](https://github.com/tatsatshah05/NatHacks2026/issues/65) | **City dashboard**<br>As a City operations coordinator, I want one list of every barrier ranked by how many trips it affects, so that crews clear the most important places first. | Kaleb | 3 | Should |
| [OP-311](https://github.com/tatsatshah05/NatHacks2026/issues/66) | **Walk and LRT trips**<br>As a wheelchair user who rides the LRT, I want trips that combine walking and the LRT and only use stations whose elevators work, so that I can cross the city step-free. | Kaleb | 5 | Should |
| [OP-312](https://github.com/tatsatshah05/NatHacks2026/issues/67) | **About, sources and privacy pages**<br>As a user, I want to know where the data comes from and what the app stores about me, so that I can trust it. | Kashish | 2 | Must |

### Phase 4: Production and Launch (hours 34–48)

**Checkpoint:** The release checklist in PLAN.md is complete, `v1.0` is tagged, the demo is rehearsed five times, and the project is submitted by hour 46.

| ID | User story | Owner | Points | Priority |
| --- | --- | --- | --- | --- |
| [OP-401](https://github.com/tatsatshah05/NatHacks2026/issues/68) | **Security and privacy review**<br>As a user, I want my location and reports handled safely, so that I can trust the app. | Tatsat | 3 | Must |
| [OP-402](https://github.com/tatsatshah05/NatHacks2026/issues/69) | **Monitoring and runbook**<br>As the team on demo day, we want to know within a minute if anything breaks and how to fix it, so that the live demo never fails. | Tatsat | 3 | Must |
| [OP-403](https://github.com/tatsatshah05/NatHacks2026/issues/70) | **Release v1.0**<br>As the team, we want a clean, tagged release at the feature freeze, so that we demo and submit exactly what we tested. | Tatsat | 2 | Must |
| [OP-404](https://github.com/tatsatshah05/NatHacks2026/issues/71) | **Performance and load**<br>As a user on a phone network, I want routes in about a second, so that the app feels instant. | Matthew | 5 | Must |
| [OP-405](https://github.com/tatsatshah05/NatHacks2026/issues/72) | **Data backup and restore**<br>As the team, we want backups we have actually restored, so that a database problem cannot end the demo. | Matthew | 2 | Must |
| [OP-406](https://github.com/tatsatshah05/NatHacks2026/issues/73) | **End-to-end tests**<br>As the team, we want the critical path tested automatically on every merge, so that late fixes never break the demo. | Kaleb | 5 | Must |
| [OP-407](https://github.com/tatsatshah05/NatHacks2026/issues/74) | **Data quality checks**<br>As a user, I want the app to notice when a city feed changes or breaks, so that I am never shown silently wrong data. | Kaleb | 3 | Must |
| [OP-408](https://github.com/tatsatshah05/NatHacks2026/issues/75) | **Accessibility audit**<br>As a disabled user, I want the whole app to meet WCAG 2.2 AA, so that an accessibility app is itself accessible. | Estella | 5 | Must |
| [OP-409](https://github.com/tatsatshah05/NatHacks2026/issues/76) | **Pitch deck and Devpost**<br>As a judge, I want a clear, honest story backed by real numbers, so that I can trust what the team built. | Estella | 3 | Must |
| [OP-410](https://github.com/tatsatshah05/NatHacks2026/issues/77) | **Web polish and performance**<br>As a judge opening the site from a QR code, I want it to load fast and look finished, so that the first impression is strong. | Kashish | 5 | Must |
| [OP-411](https://github.com/tatsatshah05/NatHacks2026/issues/78) | **Judge page and launch assets**<br>As a judge, I want to open the app on my own phone in seconds, so that I can try it during judging. | Kashish | 2 | Must |
| [OP-412](https://github.com/tatsatshah05/NatHacks2026/issues/79) | **Model monitoring and fallback**<br>As a user, I want ice warnings to keep working when cameras or the model fail, so that I am never left without a warning. | Ipsa | 3 | Must |
| [OP-413](https://github.com/tatsatshah05/NatHacks2026/issues/80) | **Technical write-up**<br>As a judge or future contributor, I want a clear technical overview, so that I can understand and extend the system. | Ipsa | 3 | Must |
| [OP-414](https://github.com/tatsatshah05/NatHacks2026/issues/81) | **Backup demo video**<br>As the team, we want a recorded demo of the full product, so that judging goes ahead even if the network fails. | Ipsa | 2 | Must |

## 3. Stories by Person

### Tatsat (@tatsatshah05): 31 points

| Phase | ID | Story | Points |
| --- | --- | --- | --- |
| 1. Data Foundation | [OP-101](https://github.com/tatsatshah05/NatHacks2026/issues/29) | Obstruction contract and store | 3 |
| 1. Data Foundation | [OP-104](https://github.com/tatsatshah05/NatHacks2026/issues/32) | City walking network | 5 |
| 2. Vision and Routing | [OP-207](https://github.com/tatsatshah05/NatHacks2026/issues/50) | Profile routing with alternates | 5 |
| 2. Vision and Routing | [OP-209](https://github.com/tatsatshah05/NatHacks2026/issues/52) | Google Maps and Apple Maps hand-off | 2 |
| 3. Web App and Alerts | [OP-304](https://github.com/tatsatshah05/NatHacks2026/issues/59) | Saved routes and push alerts (server) | 5 |
| 3. Web App and Alerts | [OP-305](https://github.com/tatsatshah05/NatHacks2026/issues/60) | Replay mode | 3 |
| 4. Production and Launch | [OP-401](https://github.com/tatsatshah05/NatHacks2026/issues/68) | Security and privacy review | 3 |
| 4. Production and Launch | [OP-402](https://github.com/tatsatshah05/NatHacks2026/issues/69) | Monitoring and runbook | 3 |
| 4. Production and Launch | [OP-403](https://github.com/tatsatshah05/NatHacks2026/issues/70) | Release v1.0 | 2 |

### Ipsa (@ipsamanhas): 32 points

| Phase | ID | Story | Points |
| --- | --- | --- | --- |
| 1. Data Foundation | [OP-108](https://github.com/tatsatshah05/NatHacks2026/issues/36) | Traffic disruptions adapter | 2 |
| 1. Data Foundation | [OP-112](https://github.com/tatsatshah05/NatHacks2026/issues/40) | Public camera access | 3 |
| 1. Data Foundation | [OP-115](https://github.com/tatsatshah05/NatHacks2026/issues/43) | Label guide and labelling tool | 3 |
| 2. Vision and Routing | [OP-202](https://github.com/tatsatshah05/NatHacks2026/issues/45) | Train the road-condition model | 5 |
| 2. Vision and Routing | [OP-203](https://github.com/tatsatshah05/NatHacks2026/issues/46) | Evaluate the model and write the model card | 3 |
| 3. Web App and Alerts | [OP-308](https://github.com/tatsatshah05/NatHacks2026/issues/63) | Community reports with photo check | 5 |
| 3. Web App and Alerts | [OP-309](https://github.com/tatsatshah05/NatHacks2026/issues/64) | Camera readings on the map | 3 |
| 4. Production and Launch | [OP-412](https://github.com/tatsatshah05/NatHacks2026/issues/79) | Model monitoring and fallback | 3 |
| 4. Production and Launch | [OP-413](https://github.com/tatsatshah05/NatHacks2026/issues/80) | Technical write-up | 3 |
| 4. Production and Launch | [OP-414](https://github.com/tatsatshah05/NatHacks2026/issues/81) | Backup demo video | 2 |

### Estella (@estella1221): 32 points

| Phase | ID | Story | Points |
| --- | --- | --- | --- |
| 1. Data Foundation | [OP-109](https://github.com/tatsatshah05/NatHacks2026/issues/37) | Weather and black-ice watch | 3 |
| 1. Data Foundation | [OP-110](https://github.com/tatsatshah05/NatHacks2026/issues/38) | 311 snow complaints and trail closures | 3 |
| 1. Data Foundation | [OP-111](https://github.com/tatsatshah05/NatHacks2026/issues/39) | Snow-clearing status adapter | 2 |
| 2. Vision and Routing | [OP-201](https://github.com/tatsatshah05/NatHacks2026/issues/44) | Collect and label training data | 5 |
| 2. Vision and Routing | [OP-212](https://github.com/tatsatshah05/NatHacks2026/issues/55) | Accessibility review of designs | 3 |
| 3. Web App and Alerts | [OP-306](https://github.com/tatsatshah05/NatHacks2026/issues/61) | Alerts page and push client | 5 |
| 3. Web App and Alerts | [OP-307](https://github.com/tatsatshah05/NatHacks2026/issues/62) | Accessibility settings | 3 |
| 4. Production and Launch | [OP-408](https://github.com/tatsatshah05/NatHacks2026/issues/75) | Accessibility audit | 5 |
| 4. Production and Launch | [OP-409](https://github.com/tatsatshah05/NatHacks2026/issues/76) | Pitch deck and Devpost | 3 |

### Kaleb (@kalebpham): 32 points

| Phase | ID | Story | Points |
| --- | --- | --- | --- |
| 1. Data Foundation | [OP-105](https://github.com/tatsatshah05/NatHacks2026/issues/33) | LRT elevator and escalator adapter | 3 |
| 1. Data Foundation | [OP-106](https://github.com/tatsatshah05/NatHacks2026/issues/34) | Map LRT elevators to the walking network | 3 |
| 1. Data Foundation | [OP-107](https://github.com/tatsatshah05/NatHacks2026/issues/35) | Construction sidewalk closures adapter | 2 |
| 2. Vision and Routing | [OP-204](https://github.com/tatsatshah05/NatHacks2026/issues/47) | Camera vision worker | 5 |
| 2. Vision and Routing | [OP-205](https://github.com/tatsatshah05/NatHacks2026/issues/48) | Black-ice risk from cameras and weather | 3 |
| 3. Web App and Alerts | [OP-310](https://github.com/tatsatshah05/NatHacks2026/issues/65) | City dashboard | 3 |
| 3. Web App and Alerts | [OP-311](https://github.com/tatsatshah05/NatHacks2026/issues/66) | Walk and LRT trips | 5 |
| 4. Production and Launch | [OP-406](https://github.com/tatsatshah05/NatHacks2026/issues/73) | End-to-end tests | 5 |
| 4. Production and Launch | [OP-407](https://github.com/tatsatshah05/NatHacks2026/issues/74) | Data quality checks | 3 |

### Kashish (@kashishgupta2911): 29 points

| Phase | ID | Story | Points |
| --- | --- | --- | --- |
| 1. Data Foundation | [OP-113](https://github.com/tatsatshah05/NatHacks2026/issues/41) | Web app shell and live map | 5 |
| 1. Data Foundation | [OP-114](https://github.com/tatsatshah05/NatHacks2026/issues/42) | Wireframes for the main screens | 2 |
| 2. Vision and Routing | [OP-210](https://github.com/tatsatshah05/NatHacks2026/issues/53) | Planner screens with mock data | 5 |
| 2. Vision and Routing | [OP-211](https://github.com/tatsatshah05/NatHacks2026/issues/54) | Route report screen | 3 |
| 3. Web App and Alerts | [OP-301](https://github.com/tatsatshah05/NatHacks2026/issues/56) | Connect the planner to the live API | 5 |
| 3. Web App and Alerts | [OP-312](https://github.com/tatsatshah05/NatHacks2026/issues/67) | About, sources and privacy pages | 2 |
| 4. Production and Launch | [OP-410](https://github.com/tatsatshah05/NatHacks2026/issues/77) | Web polish and performance | 5 |
| 4. Production and Launch | [OP-411](https://github.com/tatsatshah05/NatHacks2026/issues/78) | Judge page and launch assets | 2 |

### Matthew (GitHub invite pending): 31 points

| Phase | ID | Story | Points |
| --- | --- | --- | --- |
| 1. Data Foundation | [OP-102](https://github.com/tatsatshah05/NatHacks2026/issues/30) | Deployable skeleton with CI | 5 |
| 1. Data Foundation | [OP-103](https://github.com/tatsatshah05/NatHacks2026/issues/31) | Source scheduler and status page | 3 |
| 2. Vision and Routing | [OP-206](https://github.com/tatsatshah05/NatHacks2026/issues/49) | Match obstructions to the network | 5 |
| 2. Vision and Routing | [OP-208](https://github.com/tatsatshah05/NatHacks2026/issues/51) | Route report | 3 |
| 3. Web App and Alerts | [OP-302](https://github.com/tatsatshah05/NatHacks2026/issues/57) | Address and place search | 3 |
| 3. Web App and Alerts | [OP-303](https://github.com/tatsatshah05/NatHacks2026/issues/58) | Live updates | 5 |
| 4. Production and Launch | [OP-404](https://github.com/tatsatshah05/NatHacks2026/issues/71) | Performance and load | 5 |
| 4. Production and Launch | [OP-405](https://github.com/tatsatshah05/NatHacks2026/issues/72) | Data backup and restore | 2 |

## 4. Story Point Scale

| Points | Size | Example |
| --- | --- | --- |
| 1 | Trivial | Add a field to an existing endpoint |
| 2 | Small | One simple data adapter with tests |
| 3 | Medium | An adapter with special rules, or a small screen |
| 5 | Large | The routing engine, model training, or the planner screens |
| 8 | Too large | Split it into smaller stories |
