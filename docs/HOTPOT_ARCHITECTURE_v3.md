# HOTPOT TABLE — ARCHITECTURE & BUILD SPECIFICATION v3.0

**Status:** authoritative. Supersedes all Stage 1–2 design notes.
**Target:** Seeed "Make a Sign" Interactive Signage Contest 2026.
**Audience:** Claude Code instances implementing this system, and the sole developer (RS).

---

## 0. HOW TO USE THIS DOCUMENT

This document is the ground truth. `CLAUDE.md` in the repo root is a short pointer to it plus current status.

Rules for any Claude Code instance working from this document:

1. **Work one milestone at a time.** Never start milestone N+1 before N's acceptance test has been run by a human on the physical rig.
2. **Within a milestone, work one step at a time.** Commit after each step. Stop after the commit and report back.
3. **Never assume an external API.** Where this document names a library function, verify it exists in the installed version before building on it. Three separate design errors in this project's history came from trusting remembered APIs. Specific known traps are flagged inline as **VERIFY**.
4. **Every check must be capable of failing.** If a verification would pass regardless of whether the code is correct, it is not a verification. Flagged inline as **TRAP**.
5. **Physical observation beats screenshots.** A framebuffer capture proves the renderer drew something. It does not prove the projector put it on the right piece of table. State which kind of evidence you have.
6. RS is dyslexic. Reports are short. One thing at a time. Confirmation questions name the single specific thing being confirmed.

---

## 1. WHAT THIS PRODUCT ACTUALLY IS

### 1.1 What this format is called

**English name: Hot Pot. Chinese name: 称重火锅 (chēngzhòng huǒguō) — weigh-by-weight hot pot.**

An earlier draft of this document claimed the pick-and-weigh mechanic was specific to 麻辣烫 (málàtàng) and that calling it "hot pot" would read as inauthentic. **That claim was wrong and is retracted.** The mechanic is not diagnostic — it runs under at least three names, and businesses using it overwhelmingly brand themselves as hot pot.

Evidence:

- Tang Bar (San Mateo) calls itself hot pot and runs exactly our flow: <cite index="40-1">diners select their ingredients from a buffet-style ingredient bar and weigh their selections to determine the price; after choosing their soup base, the staff cooks the hot pot for them.</cite> The owner frames it explicitly as hot pot for people who want the meal without the cooking.
- Big Way Hot Pot: <cite index="44-1">after you have filled your pot it is weighed at the counter and you pay in exchange for a number; all your raw ingredients are cooked in their back kitchen with your chosen broth.</cite>
- The identical mechanic also runs as 冒菜 (màocài): <cite index="39-1">customers select ingredients from a buffet table into baskets, choose a broth style, then hand everything to a staffer to have the hot-pot bowl assembled and cooked in the kitchen; payment is by total weight of ingredients at $3.99 per 100 grams.</cite>
- And as 麻辣烫: <cite index="18-1">grab a bowl and tongs, select from meats, vegetables, mushrooms, noodles and tofu products, the selection is weighed to determine the price, and everything is cooked in your chosen broth.</cite>

**Naming decision and its reasoning:**

"Hot pot" is the *broad* claim; "malatang" is a *narrow* one. A narrow claim gives a judge a target — if the broth on the day is not málà, the name is wrong. The broad claim is never wrong, and it is demonstrably correct for this format.

For the Chinese UI, **称重火锅** is used rather than plain 火锅. It is accurate, and it signals knowledge of the specific format rather than the generic word. 自助火锅 (self-serve hot pot) is an acceptable alternative if 称重火锅 reads awkwardly to a native speaker — **ask one before the contest.**

### 1.2 What the research does establish

The naming claim was wrong. The operational details are correct under any of the three names, and they are what make the build read as authentic:

- Pricing is conventionally **per 100 g**. Shops differ on whether all items share one rate or each has its own. <cite index="12-1">A commonly quoted figure is roughly $3 per 100 grams.</cite> Our per-item `pricePer100g` is the more sophisticated variant and is correct.
- **Broth choice is a required step**, not polish. Every source describes it as one of the defining choices alongside the ingredients themselves.
- **Spice level is a required step.** <cite index="16-1">Picking your own ingredients and adjusting the spice level yourself is the defining feature from the diner's point of view.</cite> Level 0 must genuinely exist as a plain option.
- **The order ends with a number, not a meal.** <cite index="40-1">Diners wait for their order number to appear on a screen and then collect the dish.</cite> This is exactly why the checkout loop (§18) matters — it is the real operational flow, not an invented feature.
- Tongs and a bowl, self-service, one diner at a time filling their own bowl. This confirms the multi-diner attribution question stays out of scope (§24.4).

### 1.3 Consequences for the build

| Decision | Rationale |
|---|---|
| Product name: **Hot Pot** (en) / **称重火锅** (zh) | Demonstrably correct for this format, and the broad claim carries no risk. |
| Repo, code identifiers and file names keep `hotpot-*` | Already correct. Nothing to rename. |
| Broth selection is a required step | One of the defining choices. |
| Spice level 0–3 is a required step | <cite index="16-1">Adjusting the spice level yourself is a defining feature.</cite> Cheap to build, high authenticity return. |
| The order ends with a **numbered handoff** | <cite index="40-1">The real flow ends with an order number and a collection.</cite> |
| Ingredient names in Chinese must be the specific real names | 香菇 for shiitake, not a generic 蘑菇. This is where a Chinese judge actually looks. |

### 1.4 The machine it ships on — RESOLVED

This lives in section 1 rather than in a hardware appendix because it is now a fixed input to almost every downstream number in this document, and it should be read before §10.4, §11.2 and §14.6 rather than after.

The deploy target is the **Seeed ODYSSEY-X86J4125800 v2** (SKU 102110767, ~$218). It replaces "reComputer, model unidentified" everywhere that phrase appears.

| | |
|---|---|
| SoC | Intel Celeron **J4125**, Gemini Lake Refresh, Goldmont Plus |
| Cores | **4 cores / 4 threads — no hyper-threading** |
| Clocks | 2.0 GHz base, 2.4 GHz all-core turbo, 2.7 GHz single-core turbo |
| TDP | 10 W |
| Vector ISA | **SSE4.2 only — no AVX, no AVX2** |
| iGPU | Intel UHD Graphics 600, **12 EUs**, 250–750 MHz (~144 GFLOPS FP32) |
| RAM | **8 GB LPDDR4, soldered, not upgradeable**, shared with the iGPU (~38.4 GB/s total) |
| Storage | 64 GB eMMC on board, microSD, M.2 Key M 2242/2280 (PCIe 2.0 ×4, NVMe), M.2 Key B (SATA III / USB 2.0 / SIM), 1× SATA III |
| Video | HDMI 2.0a 4K@60 + DisplayPort 1.2 over USB-C 4K@60, two displays |
| USB | **1× USB 3.1 Type-A, 1× USB 3.1 Type-C, 2× USB 2.0 Type-A** |
| Network | Dual **2.5 GbE**, Wi-Fi 5 dual-band, Bluetooth 5.0 |
| Co-processor | **RP2040**, Arduino-programmable (v1 had a SAMD21 — v2 does not) |
| Headers | 40-pin Raspberry-Pi-compatible, 4-pin PWM fan, RTC battery |
| Power | 12–19 V via 5.5/2.1 mm barrel jack, or 5–12 V USB-C PD |
| Size | 110 × 110 mm |

**No feature in this document is cut to fit this board.** Everything specified still ships. What changes is where the adaptive controllers *start*, what gets measured rather than assumed, and a short list of parts to order alongside the board. The four facts below are the ones that actually propagate.

**(a) Exactly 4 cores, no SMT.** §10.4's affinity plan was written speculatively and happens to fit precisely — four cores, four assignments. That is now finalised there, with the caveat that "fits precisely" means zero headroom for the OS.

**(b) No AVX2.** This is the least obvious fact on the list and the most dangerous. Goldmont Plus stops at SSE4.2. Every x86 inference path we depend on — MediaPipe's XNNPACK backend, TFLite, and whatever Edge Impulse compiles into a `.eim` — has AVX2 fast paths and will either fall back to much slower SSE code or, in the bad case, be built AVX2-only and die with SIGILL on first inference. **A binary that runs on the development machine proves nothing about this board.** New VERIFY items in §11.2 and §19.1.

**(c) The iGPU is small and shares memory with the CPU.** 12 EUs at 750 MHz is roughly 144 GFLOPS, somewhere between 15× and 30× less than the Arc part the 60 FPS figure in §14.6 came from. The sharper limit is bandwidth, not shader math: a GPU fluid solver is a chain of full-frame ping-pong passes, and it is drawing on the same ~38.4 GB/s that four busy CPU cores are already using. A 20-iteration Jacobi pressure solve at 480×270 RGBA32F moves on the order of 120 MB per frame — about 7 GB/s at 60 fps, before advection, vorticity, particles or the light pass. This is why §14.6 now starts at the bottom of the ladder and climbs, and why RGBA16F is worth trying: it halves every one of those numbers.

**(d) One USB 3 Type-A port.** The peripheral set fills the board exactly, so the allocation is not free choice:

| Device | Port | Why |
|---|---|---|
| Camera | **USB 3.1 Type-A** | The only port with the bandwidth. Never behind a hub, never sharing with the others. §5.4 and §6.6 assume this. |
| XIAO load cells | USB 2.0 Type-A | Serial at 115200 needs nothing more (§4.9). |
| Microphone, *if* the webcam has no capture device | USB 2.0 Type-A | The open VERIFY at §3. If the webcam does expose one, this port stays free — that is the only spare port in the build. |
| Projector | HDMI | 2.0a, 4K@60 capable. |
| Power | **Barrel jack, not USB-C** | Powering over USB-C consumes the Type-C port. Use 12 V on the barrel jack and keep Type-C free. |

There is no port left for a keyboard and mouse at deploy time. **Bring a powered USB hub for bring-up**, and do not leave it in the build — staff operation is the browser view over LAN (§12), which is exactly why that design is right.

**Parts to order with the board:**

1. **An NVMe SSD in the M.2 Key M slot.** 64 GB of eMMC has to hold Ubuntu, an openFrameworks tree plus build artifacts, the models, `orders.sqlite3`, logs, and the dataset that §12.7 captures image-by-image. It will not fit comfortably, and eMMC random write is poor enough that SQLite commits and dataset capture will both feel it. Cheapest risk removal available on this project. Put the OS on it; failing that, at minimum `datasets/`, `logs/` and `state/`.
2. **The 4-pin PWM fan.** See below.
3. **12 V barrel-jack PSU**, 3 A or better.

**Thermals are a real risk here, not a formality.** This workload pins four cores and the iGPU simultaneously and indefinitely, which is the worst case for a 10 W passively-cooled part, and it does so *next to a hot pot* — elevated ambient, steam, and airborne grease. Passive cooling that is fine on a bench will throttle in that environment, and a throttled J4125 loses clock across all four cores at once, which will present as the fluid degrading and the tracker slowing at the same time and will be debugged as a software problem. Mount the board away from the burner in a ventilated enclosure, fit the fan, and add package temperature and the throttle flag to the developer panel (§12.8) so the failure is legible when it happens.

**Two things the board offers that this design does not currently use**, recorded so the decision is deliberate rather than forgotten:

- **The onboard RP2040.** The load cells are already working on a XIAO over USB serial with frozen firmware (§4.9, §9.5). Moving them to the onboard RP2040 would remove a device and a cable and would be a genuinely good story for a Seeed contest, since it uses the board's distinguishing feature. It also means rewriting working firmware near a deadline. **Decision: keep the XIAO.** Revisit only if a USB port becomes the binding constraint.
- **The second 2.5 GbE port.** A contest hall's Wi-Fi is hostile and the staff tablet depends on the network (§12, and `camera.host_for_browser` in §8.6). Put one NIC on the venue LAN and hang a small travel router off the other as a private network for the tablet, or use the board's own Wi-Fi as an AP. Either way the staff view stops depending on the venue. Decide during M9 setup.

---

## 2. INVARIANTS

These are load-bearing. Violating one is a design regression, not a style choice.

**I1 — Core owns all state.** Weights, cart, prices, bin map, geometry, FSM, locale, mode. No other process holds authoritative state.

**I2 — openFrameworks is a dumb renderer.** It receives resolved, semantic state (a finished string, a number, a rectangle, a highlight enum) and draws it. It computes nothing it could be told. It retains only **rig calibration constants** — the keystone homography, and the two I9 illuminant values `field_level` and `white_floor` (§8.6) — plus frame-to-frame tweening. These are properties of the physical installation, measured once and read at startup, not application state: core cannot resolve them because they describe the projector, not the transaction.

**I3 — Core never touches a frame.** No pixel data enters the core process. Ever.

**I4 — Price is cumulative and absolute.**
```
removedGrams[i] = max(0, startWeightGrams[i] - binWeightGrams[i])
price[i]        = (removedGrams[i] / 100.0) * pricePer100g[i]
```
Never sum per-event deltas. Two absolute weights subtracted cannot bake in a dropped or doubled event. There is **no put-back branch and none is to be added** — a put-back raises the weight, lowers the difference, lowers the total. The refund *is* the arithmetic.

**I5 — The 10g deadband is display-only.** It never enters price maths. It **snaps to current truth** when the gap reaches 10g, so small picks accumulate and are eventually shown. The wrong version — "ignore events under 10g" — throws picks away. Do not write it.

**I6 — Re-baseline, never re-tare.** `startWeightGrams[i] = binWeightGrams[i]`. Nothing becomes zero. The word "tare" is reserved for the load-cell zero-point calibration in staff view, which is a different operation on a different quantity.

**I7 — Food position is not fixed.** Trays get swapped mid-service. The bin→item map is live data written by the classifier, never a constant. Price follows the food, not the bin.

**I8 — Distinguish states by hue at full chroma, not by brightness.** Brightness is spent on illumination (I9) and is therefore not available as a signalling channel — everything on the table sits near the top of the range, so a "dimmer" state reads as a rendering fault rather than as a state. Hues must additionally be **luminance-matched to each other**, or a state change reads as the table brightening rather than as the state changing. Already measured on the rig: the dwell-progress green had to come down from 255 to 115 to sit at the same luminance as the 200-red it replaces, because a full-value green on a light field lands at about 1.4:1 — worse than the dim cyan that had already been rejected at the table.

**I9 — The projected field is the illuminant, not a background.** The demo runs in a dark room, so the projector is the only light the camera has. Three consequences, none of them aesthetic:

1. **Every tray cutout carries a flat, pure-white patch at full field level.** Not black. A black rectangle over a cutout is not "leaving the food alone" — with no ambient to fall back on it is the food in total darkness, which starves the classifier rather than protecting it. The choice is projector light or no light, and the patch also lights the back of the hand exactly when the hand is over a bin.
2. **Nothing coloured, patterned, textured or animated ever reaches a cutout.** The objection this invariant was originally written against was real, but it was about a *coloured, patterned* image washing pink and white over the food — not about light as such. Flat white is neither. Fluid, gradients, grid lines, diagonals, text and low-stock tints are all excluded from the bin patches. §13.2 makes this structural rather than something to remember.
3. **The rest of the table stays above a white floor**, bright enough that the back of the hand is always lit for the tracker. Colour is free above that floor; darkness is not. The floor and its mechanism are specified in §13.2.

**The one exception is dot calibration** (`overlay.kind = "calibrating"`), which inverts completely: black field, white dots. The dots must stay separable from a white table top, and the solve runs the camera at a dark exposure to keep them that way — a white field there puts the dots on a background as bright as they are and the solve finds nothing. Calibration and food classification are both setting-mode activities but never run simultaneously, so the two lighting regimes never have to coexist.

**I10 — Camera near-vertical.** Hand-position error = hand height ÷ tan(elevation angle). At 40° a 100mm-high hand lands 119mm off — the wrong bin. At 80° it is 18mm. **The camera elevation angle has never been measured. It must be measured before M4.**

---

## 3. PROCESS ARCHITECTURE

Six Python processes plus one C++ process. Processes, not threads: separate interpreters, separate cores, no shared GIL. Jitter is the enemy and oF needs a core to itself.

| Process | Owns | Never does |
|---|---|---|
| `camera` | `/dev/video0`, the shared-memory frame ring, the MJPEG HTTP server | Any analysis. It is deliberately the dumbest process in the system and must stay that way. |
| `tracker` | MediaPipe Hands, hand role assignment | Decide anything. It reports positions and roles. |
| `classifier` | Food classification **and projected-dot detection** — all frame analysis that is not hand tracking | Run in serving mode. It sleeps unless core wakes it. |
| `voice` | The microphone (ALSA), keyword spotting | Interpret meaning. It reports which keyword fired. |
| `core` | Everything in I1, the staff-view HTTP+WS server, the serial thread, the order database | Touch a frame (I3). Block. |
| `of` (C++) | The projector output, the keystone homography, tweening, the fluid simulation, **audio output** | Hold state or compute logic (I2). |
| `run.py` | Launching, supervising, restarting, and killing all of the above | Any application logic. |

### 3.1 Why the microphone is not in the camera process

The webcam's microphone is almost certainly the same physical USB device as the camera, but it is a **different kernel device node** — ALSA, not V4L2. Two processes opening two different nodes on one USB device is fine. There is no ownership conflict, so nothing forces camera and voice together, and the camera process must stay dumb.

**VERIFY at M0:** run `arecord -l` and confirm the webcam exposes a capture device. If it does not, voice needs a separate USB mic and that is a hardware order, not a code change.

### 3.2 Why the classifier owns dot detection

Projected-dot calibration needs someone to find dot centroids in a camera frame. Core cannot (I3). Camera must not (it stays dumb). The classifier already attaches to frames and already runs only in setting mode — and calibration *is* a setting-mode activity. It fits with zero new machinery. The classifier is therefore better understood as "the vision process": one process, all frame analysis except hands.

### 3.3 Restart-tolerant topology

**Core is the TCP server for every control link. Everyone else is a client and reconnects with backoff.** One rule, and it makes start order almost irrelevant: any process may start, die and restart in any order and the system reconverges. `camera` is additionally a server (shm creator + MJPEG). `run.py` still starts them in a sensible order to reduce noise in the logs, but correctness does not depend on it.

---

## 4. TRANSPORT SPECIFICATION

Three traffic classes with different failure needs. This was argued out and is settled.

| Class | Transport | Why |
|---|---|---|
| Frames | POSIX shared memory ring | Never send pixels through a socket. |
| Cursor (tracker → oF, tracker → core) | UDP, localhost, one datagram per frame | A lost cursor packet is worthless 16ms later. TCP would *queue* stale ones — a 200ms hiccup then delivers a burst in order and the hand visibly replays through history. That is exactly the jitter six processes exist to avoid. |
| Control (everything else) | TCP, newline-delimited JSON (JSONL), UTF-8 | These must not vanish. A dropped "enter setting mode" wedges the system. |

**Receiver rule for UDP: drain to latest.** Read the socket non-blocking until it is empty, keep the highest `seq`, discard the rest. Never process a backlog.

**Why not ZeroMQ for everything:** it is a new build dependency on both sides, and the side that is risky is the oF build on the ODYSSEY (§1.4) — a board with 64 GB of eMMC where every added dependency is also disk and build time. `ofxNetwork` ships with openFrameworks and has both UDP and TCP in the box — zero new dependencies on the Linux build. The one thing ZMQ would genuinely buy is automatic reconnect, and §19 provides that in ~40 lines.

### 4.1 Ports and names (defaults, all in `config/system.json`)

| Name | Default | Owner |
|---|---|---|
| `core.control_port` | 8765 | core (TCP server) |
| `core.web_port` | 8090 | core (HTTP + WebSocket) |
| `camera.mjpeg_port` | 8081 | camera (HTTP) |
| `cursor.of_port` | 8770 | of (UDP listener) |
| `cursor.core_port` | 8771 | core (UDP listener) |
| `frames.shm_name` | `hotpot_frames` | camera |

### 4.2 Control link — common envelope

Every JSONL line is an object with a `t` (type) field. Every client opens with `hello` and heartbeats every 1000ms.

```json
{"t":"hello","who":"tracker","pid":4412,"ver":3}
{"t":"hb","ts":1754838400.117}
```

Core replies to `hello` with the client's current configuration, so clients hold no config of their own beyond how to find core:

```json
{"t":"welcome","who":"tracker","cfg":{"homography_cam_to_stage":[[...]],"stage":[1920,1080],"hand_model_complexity":1,"emit_hz":60}}
```

**Core marks a client dead after 3 missed heartbeats (3s).** Death is surfaced on the staff view status pips and, if it is `of` or `camera`, on the table.

### 4.3 core → of

Sent at a fixed 60Hz, whether or not anything changed. A fixed-rate state stream means oF's tweener always has a fresh target and never has to guess whether silence means "no change" or "core is dead."

```json
{"t":"state","seq":90211,"ts":1754838400.117,
 "mode":"serving",
 "locale":"zh",
 "fluid":{"style":"mala","enabled":true,"intensity":0.6},
 "bins":[
   {"i":0,"label":"香菇","sub":"¥12.00/100g","grams":418,"picked":38,"price":4.56,
    "hl":"hover","stock":"ok","resolved":true}
 ],
 "total":{"amount":41.20,"text":"¥41.20"},
 "widgets":[
   {"id":"done","kind":"button","rect":[1480,880,380,140],"label":"结账",
    "dwell":0.42,"enabled":true,"style":"primary"}
 ],
 "overlay":{"kind":"none"}
}
```

Notes that are not optional:

- `label` and `text` are **already resolved strings in the current locale**. oF does no lookup (I2). It only needs `locale` to pick a font.
- `rect` is in **stage space** (§5), always `[x, y, w, h]`.
- `hl` ∈ `none | hover | picking | picked | lowstock | disabled`.
- `mode` ∈ `serving | setting` (§9.1). Derived from the FSM state, never stored separately — two places that can disagree about which mode the table is in is the failure this field exists to prevent. oF defaults it to `serving` when absent, deliberately: a line that somehow lost the field must not paint `SETTING — NOT BILLING` over a table that is billing.
- `overlay.kind` ∈ `none | recap | qr | calibrating | uncalibrated | error`.
- `bins` always has exactly 8 entries. An unresolved bin has `resolved:false`, an empty `label`, and bills nothing.

### 4.4 core → of, one-shot events

Separate from `state` because `state` repeats and a repeated sound would fire 60 times a second.

```json
{"t":"evt","kind":"sound","id":"pick_confirm","gain":1.0}
{"t":"evt","kind":"burst","at":[640,300],"style":"pick","strength":0.8}
{"t":"evt","kind":"stream","from":[640,300],"to":[1660,940],"style":"price"}
```

Events are fire-and-forget. If oF misses one because it just restarted, nothing breaks.

### 4.5 of → core

oF sends only heartbeats and telemetry. It never sends input.

```json
{"t":"stat","fps":59.8,"sim_res":[480,270],"gpu_ms":9.1,"dropped":0}
```

### 4.6 tracker → of and tracker → core (UDP)

One datagram per camera frame, sent to both ports. Compact JSON — at 60Hz and ~150 bytes this is free, and being human-readable during bring-up is worth more than the bytes.

```json
{"seq":98123,"ts":1754838400.117,"hands":[
  {"id":3,"role":"pointer","x":941.2,"y":510.8,"conf":0.93},
  {"id":4,"role":"ambient","x":300.1,"y":700.4,"conf":0.81}
]}
```

`x,y` are **stage space** floats. `role` ∈ `pointer | ambient`. See §11.3 for role assignment.

### 4.7 core ↔ classifier

```json
{"t":"cmd","id":17,"op":"classify","rects":[[x,y,w,h], ...8],"mode":"once"}
{"t":"cmd","id":18,"op":"classify","rects":[...],"mode":"live","hz":2}
{"t":"cmd","id":19,"op":"stop"}
{"t":"cmd","id":20,"op":"detect_dots","expect":12,"min_area":40}
{"t":"cmd","id":21,"op":"capture","rects":[...],"labels":["mushroom",...],"burst":5}
```
```json
{"t":"result","id":17,"bins":[{"i":0,"label":"mushroom","conf":0.94},...],"ms":42}
{"t":"dots","id":20,"points":[[cx,cy],...],"ms":18}
{"t":"captured","id":21,"files":["datasets/captures/mushroom/17548384001.jpg",...]}
```

`rects` are **camera space** (§5) — the classifier never sees stage space.

### 4.8 voice → core

```json
{"t":"kw","word":"done","conf":0.87,"lang":"zh","ts":1754838400.117}
```

The voice process reports **which keyword fired**, never what it means. Meaning is per-FSM-state and only core knows the state. "Done" in setting mode and "done" in serving mode are different actions.

### 4.9 XIAO → core (USB serial)

**VERIFY before writing the parser:** read `firmware/loadcells/src/main.cpp` and match its actual print format. Do not assume the format below — it is a description of intent, not a spec to code against.

Expected shape: one line per sample at ~78Hz, eight signed integers of raw HX711 counts. The parser must:
- be tolerant of partial lines at startup (the first line after open is usually truncated),
- discard any line that does not parse cleanly rather than crash,
- never block the core main loop (it runs in a thread, §9.5).

---

## 5. COORDINATE SPACES

This was an unresolved hole. It is now settled. Three spaces, one canonical.

### 5.1 The three spaces

| Space | Units | Who lives in it |
|---|---|---|
| **Camera space** | camera pixels, e.g. 0..1919 × 0..1079 | MediaPipe raw output, bin rects as dragged by staff, classifier crops |
| **Stage space** — **CANONICAL** | 1920 × 1080, origin top-left | Everything semantic: cursor, bin rects sent to oF, widget rects, core's zone tests |
| **Projector output** | the physical keystoned quad on the table | oF only, produced by the final warp |

### 5.2 Why stage space is canonical and not millimetres

Millimetres would need a physical measurement to anchor. Stage space needs none: it is oF's un-keystoned framebuffer, and the projected-dot calibration produces a camera→stage homography **directly**, because the dots are drawn at known stage coordinates and then keystoned onto the table by the same warp that will carry the UI.

`H_cam→stage` therefore **implicitly contains the keystone**. This has one consequence that must be written into the staff view as a warning:

> **Changing the keystone invalidates the camera→stage homography.** Re-run dot calibration after any keystone adjustment.

### 5.3 Who holds what

- **Core** computes and owns `H_cam→stage` (`state/homography.json`).
- Core pushes it to `tracker` in the `welcome` message. Tracker converts MediaPipe output to stage space before sending. oF and core therefore both receive stage-space cursors and never disagree.
- Core owns bin rects **in both spaces**: camera-space rects are the ground truth (staff dragged them there); stage-space rects are derived through `H_cam→stage`.
- Core pushes camera-space rects to the classifier and stage-space rects to oF.
- oF owns only the keystone, applied to the final composite.

**TRAP:** verifying the derived stage rects by reprojecting them back through the same `H` passes by construction, regardless of whether `H` points the right way. The only check that can fail is physical: project the derived rects and look at whether they land on the real trays. This trap has already been hit three times in this project in different disguises. Do not hit it a fourth.

### 5.4 The MJPEG scaling trap

Staff drag rects on an `<img>` element that is CSS-scaled and may be served at a different resolution than capture. Rects must be stored in **capture-space pixels**, not display pixels.

Implementation rule, in the browser:
```js
const sx = img.naturalWidth  / img.clientWidth;
const sy = img.naturalHeight / img.clientHeight;
// every mouse/touch coordinate is multiplied by sx, sy before being stored or sent
```
`naturalWidth` reflects what the server actually sent, so this is correct whether MJPEG is full-res or downscaled, and stays correct if the config changes.

---

## 6. THE SHARED-MEMORY FRAME RING

Single producer (camera), multiple readers (tracker, classifier). Lock-free, seqlock-verified. This is the standard pattern and there is no reason to invent anything.

### 6.1 Layout

One `multiprocessing.shared_memory.SharedMemory` segment named `hotpot_frames`.

```
offset  size    field
0       4       magic  = 0x48505446 ('HPTF')
4       4       version = 3
8       4       width
12      4       height
16      4       channels (3, BGR)
20      4       slot_count (default 8)
24      8       write_counter   (uint64, monotonic, published last)
32      32      reserved
64      N*24    slot headers: [frame_id:u64][ts_ns:u64][ready:u32][pad:u32] × N
64+N*24 N*W*H*C slot pixel data
```

### 6.2 Write path (camera)

```
slot = write_counter % slot_count
write pixels into slot
write slot header: frame_id = write_counter, ts_ns = now
memory barrier
write_counter += 1        # publication
```

### 6.3 Read path (tracker, classifier)

```
c1 = write_counter
slot = (c1 - 1) % slot_count
id1 = slot.frame_id
copy pixels out
id2 = slot.frame_id
if id1 != id2: retry     # torn read, writer lapped us
```

With 8 slots at 30fps a reader has ~260ms before it is lapped. Tearing should be effectively unobservable; the check exists so that when it does happen the frame is discarded rather than silently corrupted.

### 6.4 Staleness — how a reader knows camera died

`ts_ns` in the slot header. If `now - ts_ns > 500ms`, the camera is dead or stalled. The reader must:
- stop emitting (tracker sends nothing rather than sending a frozen cursor),
- report `{"t":"stat","frames_stale":true}` to core,
- keep polling; when frames resume, resume silently.

**This is the answer to "who notices when the camera dies": the consumers do, immediately, without asking anyone.**

### 6.5 One resolution, not several

The ring holds **capture resolution only** — the highest useful resolution the webcam offers. Consumers adapt:
- `classifier` crops at full res (it wants the detail).
- `tracker` downsamples with `cv2.resize` before MediaPipe (cheap, and MediaPipe wants small).
- `camera`'s MJPEG server encodes at `mjpeg_width` (config), independently.

A second ring at a second resolution would double memory bandwidth to save one `resize` per frame. Not worth it.

### 6.6 Camera capture settings

Request `MJPG` as the V4L2 pixel format if the webcam offers it, then decode on the host. Most USB webcams cannot deliver high resolution at high framerate in `YUYV` because of USB bandwidth; `MJPG` is how they get there. Enumerate at startup with `v4l2-ctl --list-formats-ext` and log the chosen mode.

Also at startup, disable anything that fights the classifier:
- auto-exposure → manual, fixed
- auto white balance → off, fixed
- autofocus → off, fixed

Changing exposure between the training set and inference is a classifier accuracy bug that looks like a model problem. Lock it and record the values in `state/camera_settings.json`.

**The projector field level is part of this set.** Since the field is the illuminant (I9), `field_level` and camera exposure are one coupled parameter, not two — and the coupled pair must be identical at capture time and at inference time, for exactly the reason above. Consequences:

- `field_level` lives in `config/system.json` (§8.6) as a percentage of full projector output. Default **100%**.
- It is **swept on the rig against camera exposure** during M3/M4 — projector at each level, camera exposure adjusted, look at the bin crops — and then frozen. It is a measured rig parameter, not a look chosen in code.
- The chosen value is mirrored into `state/camera_settings.json` alongside exposure and WB, so a single file answers "under what light was this dataset taken."
- **The bin patches are always at full level regardless of `field_level`.** `field_level` sweeps the field; the cutouts stay at the top of the range. "Fully lit" is not a level to be traded off.

---

## 7. REPOSITORY LAYOUT

```
hotpot-table/
├── CLAUDE.md                       # short: status + pointer to this doc
├── docs/
│   └── HOTPOT_ARCHITECTURE_v3.md   # this file
├── run.py                          # THE single entry point
├── config/
│   ├── system.default.json         # committed
│   └── system.json                 # gitignored, seeded from default on first run
├── state/                          # gitignored entirely — runtime state
│   ├── homography.json
│   ├── bin_rects.json
│   ├── loadcell_cal.json
│   ├── bin_map.json
│   ├── camera_settings.json
│   ├── session.jsonl               # write-ahead journal, §19.3
│   └── orders.sqlite3
├── data/                           # committed — content, not state
│   ├── catalogue.json
│   └── locales/
│       ├── en.json
│       └── zh.json
├── python/
│   ├── pyproject.toml
│   └── hotpot/
│       ├── common/
│       │   ├── framebus.py         # §6
│       │   ├── wire.py             # JSONL framing, reconnecting TCP client, TCP server
│       │   ├── cursorbus.py        # UDP send / drain-to-latest recv
│       │   ├── geometry.py         # homography fit, apply, invert
│       │   ├── atomicio.py         # write-temp + fsync + rename
│       │   ├── health.py           # heartbeat, status registry
│       │   └── log.py
│       ├── camera/main.py
│       ├── tracker/main.py
│       ├── classifier/
│       │   ├── main.py
│       │   ├── backend_ei.py       # Edge Impulse runner
│       │   ├── backend_stub.py     # deterministic fake, for M0–M6
│       │   └── dots.py             # projected-dot detection
│       ├── voice/
│       │   ├── main.py
│       │   ├── backend_ei.py
│       │   └── backend_stub.py
│       └── core/
│           ├── main.py
│           ├── fsm.py
│           ├── cart.py
│           ├── pricing.py
│           ├── scale.py            # serial thread + calibration maths
│           ├── binmap.py
│           ├── geometry_store.py   # rects in both spaces, homography
│           ├── orders.py           # sqlite, order queue, day summary
│           ├── journal.py          # §19.3
│           ├── i18n.py
│           └── web/
│               ├── server.py       # HTTP + WebSocket
│               └── static/         # staff view SPA, §12
├── of/hotpot-table/                # openFrameworks app
│   ├── src/
│   │   ├── main.cpp
│   │   ├── ofApp.h / ofApp.cpp
│   │   ├── StateLink.h/.cpp        # TCP client, JSONL, tweening
│   │   ├── CursorLink.h/.cpp       # UDP, drain-to-latest
│   │   ├── Stage.h/.cpp            # FBO stack + keystone
│   │   ├── FluidLayer.h/.cpp       # §13
│   │   ├── UiLayer.h/.cpp
│   │   └── AudioBus.h/.cpp         # §14
│   └── bin/data/
│       ├── keystone.json
│       ├── fonts/
│       └── audio/
├── firmware/loadcells/             # UNCHANGED — it works
├── models/
│   ├── ingredients-x86_64.eim      # gitignored if large; see models/README.md
│   ├── keywords-x86_64.eim
│   └── README.md                   # provenance: EI project id, version, val acc
├── assets/
│   └── tts_src/                    # phrase lists for offline TTS rendering
├── datasets/                       # gitignored
│   └── captures/<label>/*.jpg
└── tools/
    ├── export_edgeimpulse.py
    ├── render_tts.py
    └── measure_camera_angle.md
```

### 7.1 What survives from the current code

**Keep:**
- `firmware/loadcells/` — verified working, do not touch.
- The measured values inside `bin_offsets.json` — they encode real rig geometry. They become the **seed** for `state/bin_rects.json`, converted, not the live file.
- The keystone corner values currently in the oF app → `of/hotpot-table/bin/data/keystone.json`.
- `ingredients.json`'s item list and price placeholders → restructured into `data/catalogue.json` (§8.1).
- The two `ofxFlowTools` C++17 patches and the plan to carry them as a fork submodule.
- Knowledge: projector is GLFW index 2, origin −3840,136, manual 4-point keystone survives power cycles.

**Delete outright.** This is a rewrite; deleting is the point.
- All oF C++ pricing, cart, deadband, bin-map and catalogue-loading code. It moves to Python.
- The oF keyboard mock. It moves to the staff view as real buttons.
- The in-bin weight text draw (open debt #1) — dies with the rewrite.
- `labelPlacementLogged` (open debt #2) — dies with the rewrite. **The debt is closed by deletion, not by proving the flag worked.** Do not spend time forcing it to trip.
- The "coprime" comment (open debt #4) — the justification was wrong and irrelevant.

**Open debt #5** (bins ending a session under 10g never cross the deadband, so up to ~10g per bin goes unbilled) is fixed for free in M6: at order finalisation, displayed values snap to true weights. It required a Done button, which M6 builds.

**Open debt #3** (currency placeholder) is resolved by §16.4.

---

## 8. CONFIGURATION AND STATE FILES

Hard rule: **config is committed and human-edited; state is gitignored and machine-written.** Never mix them in one file. Every machine write goes through `atomicio.write_json` — write to `<name>.tmp`, `fsync`, `os.replace`. A power cut mid-write must never produce a half-written homography.

### 8.1 `data/catalogue.json` — committed

Every item that could ever be in a bin. Not which bin it is in.

```json
{
  "schema": 3,
  "base_currency": "INR",
  "items": [
    {
      "id": "mushroom",
      "pricePer100g": 12.0,
      "names": {"en": "Shiitake mushroom", "zh": "香菇"},
      "tags": ["vegetarian", "vegan"],
      "class_name": "mushroom"
    }
  ]
}
```

`class_name` is the label string the ML model emits. Keeping it separate from `id` means retraining with different class names does not force a catalogue rewrite.

#### Hidden labels versus display names

**`id` and `class_name` are hidden. `names` is the only thing a diner ever reads.** These are two different vocabularies, not two spellings of one:

| field | who it is for | example |
|---|---|---|
| `id` | the catalogue and `bin_map.json` | `soya_chunks` |
| `class_name` | the ML model's output label | `soya_chunks` |
| `names` | the diner, on the table | `{"en": "Fish Ball", "zh": "鱼丸"}` |

The label names **a thing that is cheap to buy, photograph and train on**. The display name is **the hot pot ingredient it stands in for**. A bin trained on soya chunks can sell as a fish ball or a meat ball; `curly_noodle` and `long_noodle` are training labels for whatever noodles the menu actually lists. `names` is therefore **not a translation of `id`**, and no code may derive one from the other — no prettifier, no title-casing, no underscore stripping.

Where the two coincide in one locale, that is a coincidence of that locale and nothing more: `egg` shows as "Egg" in English and 鸡蛋 in Chinese, and the Chinese string is a translation of the *display* name, not of the label.

Two consequences, both enforced in `core/pricing.py`:

- **`Item.display_name(locale)` is the only way to get a label onto the table**, and it cannot return `id` or `class_name`. Its chain is `names[locale]` → `names["en"]`, and it raises rather than reaching past that. The bug this replaced was `names.get(locale, item.id)` in `core/main.py`, which projected the training label onto a plate the moment a locale was missing one name.
- **`Catalogue.load()` refuses any item without a non-empty `en` name.** That is what makes the chain above total. A missing translation is tolerated and degrades to English; a missing English name stops core at startup, because nothing sits below it but the hidden label.

### 8.2 `state/bin_map.json` — machine-written

```json
{"schema":3,"written":1754838400.1,"locked":true,
 "bins":[{"i":0,"item_id":"mushroom","conf":0.94,"source":"classifier"},
         {"i":1,"item_id":null,"conf":0.31,"source":"classifier"}]}
```

`item_id: null` ⇒ unresolved ⇒ renders empty ⇒ bills nothing. `locked` is true in serving mode, false while setting mode is live-updating.

### 8.3 `state/loadcell_cal.json` — machine-written

```json
{"schema":3,
 "bins":[{"i":0,"zero_counts":83422,"counts_per_gram":-214.77,"calibrated_at":1754838400.1,
          "ref_mass_g":500.0,"noise_counts_rms":18.3}]}
```

`counts_per_gram` is **signed**. Several cells are mounted inverted; the sign is *computed during calibration*, never asked of the operator. All downstream logic sees grams only.

### 8.4 `state/bin_rects.json` — machine-written

```json
{"schema":3,"camera_size":[1920,1080],
 "bins":[{"i":0,"cam":[210,140,300,220]}]}
```
Camera space is the stored ground truth. Stage-space rects are derived at load time and never persisted — persisting a derived value invites the two copies to disagree.

### 8.5 `state/homography.json` — machine-written

```json
{"schema":3,"H_cam_to_stage":[[...],[...],[...]],
 "computed_at":1754838400.1,"n_points":12,"rms_px":1.8,
 "keystone_fingerprint":"a91f...",
 "camera_size":[1920,1080],"stage_size":[1920,1080]}
```

`keystone_fingerprint` is a hash of `keystone.json`. oF reports its fingerprint in `stat`; if it differs from the one recorded here, core raises "calibration stale — keystone changed" on the staff view. This makes §5.2's warning enforceable instead of merely written down.

### 8.6 `config/system.json` — human-edited

```json
{
  "core":   {"control_port":8765,"web_port":8090,"locale":"en","conf_floor":0.65,
             "deadband_g":10.0,"settle_ms":300},
  "camera": {"device":"/dev/video0","capture":[1920,1080],"fps":30,
             "mjpeg_port":8081,"mjpeg_width":1920,"mjpeg_fps":8,
             "host_for_browser":"localhost"},
  "tracker":{"model_complexity":1,"max_hands":2,"emit_hz":60,"mirror_handedness":false},
  "classifier":{"backend":"stub","model":"models/ingredients-x86_64.eim","live_hz":2},
  "voice":  {"backend":"stub","model":"models/keywords-x86_64.eim","threshold":0.75,
             "enabled":false},
  "of":     {"stage":[1920,1080],"monitor_index":2,"fluid_sim_scale":4,"target_fps":60,
             "field_level":1.0,"white_floor":0.45},
  "dev":    {"panel_enabled":false}
}
```

`camera.host_for_browser` is the config value that will bite on deploy day: in development the browser and the camera process are both on localhost; on the ODYSSEY, if the staff tablet is a different machine, this must be the board's LAN address. It exists as an explicit field precisely so it is visible rather than hardcoded. Note that the board has two 2.5 GbE ports and Wi-Fi, so "the LAN address" is genuinely ambiguous on this hardware — write the address of whichever interface the tablet is actually on, and see §1.4 on giving the tablet its own network rather than trusting a venue's.

`of.field_level` and `of.white_floor` are the two I9 knobs, and both are **measured on the rig, not chosen** (§6.6, §13.2). They are config rather than constants specifically so they can be swept without a rebuild. `field_level` is additionally mirrored into `state/camera_settings.json` at startup, because it belongs to the dataset's provenance as much as exposure does — config says what the rig is set to, that file says what the training images were taken under.

---

## 9. CORE — DOMAIN MODEL

### 9.1 The finite state machine

```
BOOT
 └─► UNCALIBRATED ──(calibration complete)──► IDLE
IDLE ──(hand present OR staff "start")──► SELECTING
SELECTING ──(dwell "done" OR keyword "done")──► BROTH
BROTH ──(broth chosen)──► SPICE
SPICE ──(spice chosen)──► RECAP
RECAP ──(dwell "confirm")──► CHECKOUT
CHECKOUT ──(receipt fetched OR timeout 90s)──► IDLE   [re-baseline, clear cart]
any ──(enter setting, cart not active)──► SETTING
SETTING ──(exit setting)──► IDLE   [refresh weights, re-baseline, lock bin map]
```

The two modes are **SERVING** (the table is billing) and **SETTING** (the table is being changed). `SETTING` is the FSM state; everything else is SERVING. The names say what the table is *doing*, not who is standing at it — staff are present in both, and a diner can walk up during either. Note that `staff "start"` above is a *person* pressing Start on the tablet and has nothing to do with the mode; that is a collision these names remove rather than create.

Rules that are not negotiable:

- **Setting mode is refused while a cart is active.** One wrong keypress must not destroy a diner's order. The staff view shows *why* it is refused and offers "cancel the order first" — so the refusal must carry a plain-language reason, not just a false.
- **"Cart is active" is the deadbanded shown grams, not raw removed grams.** Raw removed grams moves with load-cell noise, which would hold the refusal true permanently and make setting mode unreachable. It is also what the diner can see: refusing on something visible is explicable to an operator, refusing on invisible noise is not. The accepted cost is that a sub-deadband pick survives entry and is discarded by exit's re-baseline.
- **Cancel-order re-baselines** (`startWeightGrams[i] = binWeightGrams[i]`), clears the cart, and stays in serving mode. Nothing goes to zero (I6).
- One shared function `reset_session()` is called from three places: cancel, checkout completion, and setting-mode exit.
- **Setting-mode exit does three things, IN THIS ORDER: refresh every bin's weight from the scale, then `reset_session()`, then lock the bin map.** All three live inside the exit transition so no caller can do two of them and forget the third.
  - **The refresh is not optional and omitting it mis-bills silently.** Billing is disabled for the whole of setting mode, so at exit `live_g` still holds the weights from when the mode was *entered*, and `reset_session()` does `start_g[i] = live_g[i]`. Swap a full tray for an empty one during setting mode — which is the entire point of the mode — and without the refresh, exit baselines `start_g` to the tray that left. The difference becomes removed grams and **the next diner is billed for the swap.**
  - A bin the scale cannot weigh (uncalibrated, or a dead XIAO) keeps its placeholder weight; `reset_session()` still re-baselines it where it stands.
- **BOOT always goes to UNCALIBRATED if `homography.json` or `bin_rects.json` is missing.** In UNCALIBRATED, serving mode is unreachable, oF shows the `uncalibrated` overlay, and the staff view opens on the calibration wizard. This is the first-boot path and it must work on a fresh clone with an empty `state/`.

### 9.2 Pricing (restating I4/I5 as code shape)

```python
removed_g   = max(0.0, start_g[i] - live_g[i])
price[i]    = (removed_g / 100.0) * catalogue[bin_map[i]].price_per_100g
total       = sum(price[i] for i in range(8) if bin_map[i] is not None)
```

Display deadband, separate from the above and touching nothing above:

```python
if abs(removed_g - shown_g[i]) >= DEADBAND_G:
    shown_g[i] = removed_g          # SNAP to truth, not increment
```

At order finalisation: `shown_g[i] = removed_g` for all i, unconditionally. That single line is the fix for open debt #5.

### 9.3 Unresolved bins

A bin is unresolved if `bin_map[i].item_id is None` or `conf < conf_floor`. Unresolved bins:
- render with no label and a muted plate,
- contribute 0.00 to the total no matter how much mass leaves them,
- are listed loudly in the staff view,
- block setting-mode exit with a confirm dialog ("2 bins unresolved — items taken from them will not be charged. Exit anyway?").

### 9.4 Hover and dwell

Core receives stage-space cursors from the tracker (§4.6) and hit-tests them against stage-space bin rects and widget rects.

- Only `role == "pointer"` hands are hit-tested. Ambient hands are ignored entirely for selection.
- Dwell: a widget accumulates dwell time while the pointer is continuously inside it. Leaving resets to 0 after a 150ms grace (so a jittery frame does not reset a nearly-complete dwell).
- Default dwell to fire: **1200ms**. Configurable. This is long enough not to misfire and short enough not to feel broken.
- Core sends `dwell` as a 0..1 fraction in `state.widgets[].dwell` so oF can draw a filling ring. oF does not time anything.
- Hover on a *bin* is feedback only. It never bills. Billing is weight, always (I4).

### 9.5 The serial thread

One thread inside core. Blocking `readline()` on the serial port at ~78Hz. It writes the latest parsed 8-tuple into a single slot guarded by a `threading.Lock`, plus a timestamp. The main loop reads the slot. There is no queue — a queue would let the main loop fall behind and then process stale weights.

```python
class ScaleReader(threading.Thread):
    def run(self):
        while not self._stop.is_set():
            line = self.port.readline()
            counts = parse(line)          # returns None on junk
            if counts is None: continue
            with self._lock:
                self._latest = (counts, time.time())
```

Main-loop read: if `time.time() - ts > 0.5`, the XIAO is gone → all bins report stale → staff view shows a red serial pip → the table shows a fault overlay. Do not silently bill from a frozen reading.

Smoothing: a median-of-5 on counts before gram conversion. Median rather than mean because a single bad HX711 read is an outlier, not noise, and a mean would smear it across five samples.

Settle detection for the classifier trigger: a bin has settled when its gram value has stayed within ±2g for `settle_ms` (default 300ms).

### 9.6 Load-cell calibration maths

Two-point, done entirely from the staff view (§12.4).

```
zero_counts     = median(counts while bin empty, 2s window)
counts_per_gram = (median(counts with ref mass) - zero_counts) / ref_mass_g
grams[i]        = (counts[i] - zero_counts[i]) / counts_per_gram[i]
```

`counts_per_gram` comes out negative for inverted cells and everything downstream just works. **The operator is never asked about sign or orientation.** The staff view shows only: "Bin 3 empty? → Tare" then "Place a known weight → enter grams → Calibrate."

Sanity check that can actually fail: after calibration, if `abs(counts_per_gram) < 10` the cell is probably not connected or the reference mass was too light. Refuse and say so.

### 9.7 The order database — `state/orders.sqlite3`

```sql
CREATE TABLE orders(
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL,             -- short human code, e.g. "A17"
  created_at REAL NOT NULL,
  status TEXT NOT NULL,           -- new | cooking | served | void
  broth TEXT, spice INTEGER,
  locale TEXT, currency TEXT,
  total REAL NOT NULL
);
CREATE TABLE order_lines(
  order_id INTEGER REFERENCES orders(id),
  bin INTEGER, item_id TEXT, grams REAL, price_per_100g REAL, line_total REAL
);
```

SQLite because it is in the standard library, is a single file, survives a power cut, and gives the staff view real restaurant-software features (today's orders, revenue, top items) for almost no code.

---

## 10. `run.py` — SINGLE-COMMAND START AND STOP

Requirement: everything starts with one call and dies with one call.

### 10.1 Why a Python launcher and not systemd

systemd is the production-correct answer on Linux but it does not exist on the Windows development machine, and having two different launch paths for dev and deploy is exactly how "works on my machine" happens. A small Python supervisor runs identically on both. For the ODYSSEY, a systemd unit is provided that does nothing but `ExecStart=/usr/bin/python3 /opt/hotpot/run.py` — one launch path, optionally wrapped.

### 10.2 Behaviour

```
python run.py                 # start everything, stream merged logs, Ctrl-C stops all
python run.py --stop          # stop a detached instance
python run.py --only core,of  # start a subset (development)
python run.py --no-restart    # disable auto-restart (debugging a crash)
```

Implementation requirements:

- Each child is launched in its **own process group** (`start_new_session=True` on POSIX, `CREATE_NEW_PROCESS_GROUP` on Windows) so that killing the group kills grandchildren too. An orphaned process holding `/dev/video0` or a TCP port is the single most annoying failure mode in a system like this.
- A pidfile at `state/run.pid` holding the launcher pid and each child's pid.
- Each child prints `HOTPOT-READY <name>` to stdout once it is genuinely serving. The launcher waits for readiness before starting the next tier.
- The launcher tails every child's stdout/stderr, prefixes each line with the process name and a colour, and writes to `logs/hotpot-<date>.log` with rotation.
- On `SIGINT`/`SIGTERM`: send `SIGTERM` to every group, wait 5s, `SIGKILL` survivors, remove the pidfile. Report which processes needed the kill — needing it is a bug to fix later.

### 10.3 Start tiers

```
tier 1: camera            (creates shm, serves MJPEG)
tier 2: core              (binds control port, opens serial, serves staff view)
tier 3: tracker, classifier, voice, of   (all clients, all reconnecting)
```

Because every client reconnects with backoff (§19), tier order is an optimisation for clean logs, not a correctness requirement. Do not add ordering dependencies that make it one.

### 10.4 CPU affinity — using the hardware fully

**Now finalised against the J4125: 4 cores, 4 threads, no SMT (§1.4).** The plan below was written before the board was known and happens to fit it exactly — four cores, four assignments. "Exactly" is the important word: there is no spare core, so the details of what is *not* pinned matter as much as what is.

On Linux, after all children are up, the launcher pins processes with `os.sched_setaffinity`:

| Core | Process | Notes |
|---|---|---|
| 0 | `of` | **Dedicated, exclusive.** Everything else is excluded from it. This is the whole reason for the process split. |
| 1 | `tracker` | MediaPipe inference, single-threaded by configuration so it cannot spill. |
| 2 | `classifier`, `voice` | They never run hot at the same time — classifier is setting-mode, voice is serving-mode. |
| 3 | `camera`, `core` | Plus the launcher, plus everything the OS does. |

Because there is no fifth core, three rules follow that were optional on a bigger machine and are not here:

- **The launcher does not pin itself.** It stays on the default mask, or explicitly on core 3. A supervisor that has claimed a core is a supervisor competing with the thing it supervises.
- **Set `OMP_NUM_THREADS=1` and the equivalent TFLite/OpenCV thread counts to 1** for `tracker` and `classifier` before spawning them. These libraries default to spawning one worker per visible core. Four workers inside a one-core affinity mask is strictly worse than one worker — it is the same work plus scheduler thrash — and it is invisible until you look for it.
- **Interrupts still land on core 0.** USB completion for the camera in particular. Pinning `of` exclusively excludes *processes*, not IRQs. If M8 shows frame-time spikes on the table that correlate with camera activity, move USB IRQ affinity off core 0 (`/proc/irq/*/smp_affinity`) before touching anything in the renderer. Do not reach for this pre-emptively; know it exists so it is a ten-minute fix rather than a lost day.

If the machine has fewer than 4 cores, log a warning and skip pinning entirely rather than pinning badly. That branch is now unreachable on the deploy target but stays for the development machines.

---

## 11. TRACKER

### 11.1 Pipeline

```
attach shm → read latest frame → downsample to model input →
MediaPipe Hands → landmarks → pick a cursor point →
camera→stage homography → role assignment → UDP to of and core
```

### 11.2 Cursor point

Use the **middle-finger MCP joint (landmark 9)**, not the wrist and not the index tip. The wrist sits far from where a person feels their hand is; the index tip moves wildly as fingers flex and while holding tongs. Landmark 9 is the palm centre and is stable whether the hand is open, closed, or gripping.

`model_complexity`: **start at 0 (lite), probe upward.** This reverses the previous instruction, which said to start at 1 on the grounds that "the reComputer x86 is far stronger than the Pi 4B the old 8-FPS figure came from." Now that the board is known (§1.4) that reasoning does not survive: a J4125 is roughly 2× a Pi 4B in both single- and multi-core terms, not an order of magnitude, and **it has no AVX2**, which is precisely the instruction set MediaPipe's x86 inference leans on. The honest expectation is that complexity 1 does not hold 25 fps on this board.

So: start at 0, measure for 5 seconds, and if the measured rate is above 45 fps try 1 and keep it only if it stays above 25. Auto-probe rather than hardcode, and log which rung it settled on. Probing upward from a rung that works is safe; probing downward from one that does not means the first seconds of every run are janky.

**VERIFY at M0, before any tracker work (§1.4b):** run one MediaPipe Hands inference on the board and confirm it neither crashes with `SIGILL` nor silently falls back to a path that cannot hold 25 fps at complexity 0. An illegal-instruction crash on demo day from an AVX2-only wheel is a foreseeable failure with a cheap check. If the stock `mediapipe` wheel is AVX2-only, the fallback is building it for this target or dropping to a plain TFLite hand model — that is a hardware-driven porting task to discover in M0, not in M5.

### 11.3 Two hands, two roles — pointer and ambient

Requirement: the right hand selects; the left hand holds the bowl and may play with the fluid but must never select anything.

MediaPipe reports handedness, but handedness from an overhead camera with a possibly-mirrored image is not reliable enough to bet the interaction on. Therefore:

**Role is assigned to a tracked hand, not recomputed per frame.**

```
1. Track hands across frames by nearest-neighbour on the cursor point
   (gate: 150 px in stage space between frames). Assign each a stable id.
2. When a hand first appears:
     - if no pointer currently exists          → role = pointer
     - else if MediaPipe says "Right"          → role = pointer, demote the other
     - else                                    → role = ambient
3. Role is LOCKED for the lifetime of that tracked id. It never flips mid-gesture.
4. When the pointer hand disappears for >500ms, its role is released.
   The remaining ambient hand is promoted to pointer only after a further 500ms.
```

Config `tracker.mirror_handedness` flips MediaPipe's label; the staff view has a "swap hands" button that toggles it live, because the correct value is a property of the physical mounting and is fastest to determine by trying it.

Rationale for step 2's fallback: with one hand on the table, that hand is the pointer regardless of which hand it is. A left-handed diner alone at the table must not be locked out.

### 11.4 What ambient hands do

They are still sent (`role:"ambient"`) so oF can inject fluid forces at their position. oF draws **no cursor and no dwell ring** for them. Core **discards them entirely** before hit-testing. The isolation is at the consumer, in both consumers, so a bug in one cannot leak selections.

### 11.5 Bench tests still outstanding

Both were flagged in Stage 1 and are still undone. They belong in M5.

1. **Bowl-hand false triggering.** A bowl held over a bin may present as a hand-shaped blob. Test with the real bowl.
2. **Tongs and palm confidence.** MediaPipe's palm detector may lose confidence when the hand grips tongs. Test with the real tongs, at the real height, under the real projector light.

---

## 12. STAFF VIEW

The staff view is not a debug page. It is the calibration surface, the diagnostics surface, the dataset-capture tool, and the restaurant-management console. It must be usable by an untrained person and must also, on demand, expose everything a developer needs.

### 12.1 Design principles

- **Dark UI.** The environment is dim; a white page is a flashlight in the operator's face. **This is the operator's screen, not the projected field** — it is not a contradiction of I9, which governs light landing on the table. The two surfaces have opposite jobs: the tablet is read by a human eye in a dark room, the table is read by a camera that has no other light source.
- **Touch first.** Assume a tablet. Minimum touch target 44×44 CSS px. No hover-only affordances.
- **One primary action per screen.** An untrained operator should never have to choose between two similarly-weighted buttons.
- **State is always visible.** Mode, order total, and the six process pips are in a header that never scrolls away.
- **Plain language, no jargon in the operator-facing layer.** "Bin 3 is empty" not "bin 3 unresolved, conf 0.31 < floor 0.65". The second sentence belongs in the developer panel.
- Served by core, plain HTML/CSS/vanilla JS as a single-page app. **No build step.** A build step means the deploy machine needs Node, and it will not have it at 2am before judging.

### 12.2 Layout

```
┌──────────────────────────────────────────────────────────────┐
│ 称重火锅  [ SERVING MODE ]        Order: ¥41.20      ● ● ● ● ● ●  │  header, fixed
├──────────────────────────────────────────────────────────────┤
│ [Live] [Bins] [Orders] [Setup] [Capture]           EN | 中文 │  tabs
├──────────────────────────────────────────────────────────────┤
│                                                              │
│                      (tab content)                           │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│  [ ENTER SETTING MODE ]              [ Cancel order ]          │  action bar, fixed
└──────────────────────────────────────────────────────────────┘
```

The six pips are `camera · tracker · classifier · voice · core · table`, each green/amber/red, each tappable for detail. `table` means oF. Colour alone is never the only signal — each pip also carries a one-letter code, because kitchen lighting and colour-blindness both exist.

The **mode chip** shows the live mode: neutral in serving, **amber** in setting, matching the table's own amber chrome (I8) so the tablet and the table are visibly one statement. Its text changes with its hue, per the same never-colour-alone rule as the pips.

**The mode toggle sits immediately beside the chip, in the header — not in the action bar**, which is where the mockup above puts it. Correcting the mockup, after seeing it on a tablet: a status indicator at the top-left and the control that changes it at the bottom-left read as two unrelated things, and nothing tells you which control drives the state you are looking at. State and its control go together. Keeping a consequential toggle out of the bottom thumb-rest also makes it harder to mis-tap, which is the same concern that ruled out a two-position switch.

- **One primary button that names what happens next** — `ENTER SETTING MODE` / `EXIT SETTING MODE`. Not a two-position switch: §12.1 is "one primary action per screen", and a switch invites a mis-tap into the one transition that destroys a diner's order.
- **When refused, the button stays tappable and explains.** It is never disabled silently. Tapping with an active cart shows §9.1's reason verbatim, plus a "Cancel the order first" action wired to `Cancel order` — that pairing is what §9.1 requires.
- The mode message carries `cart_active` alongside the mode, so the button can pre-warn beside itself before a tap is made rather than after a round trip.

The **action bar** stays fixed to the bottom, above the developer panel, on every tab, and holds what is **order-scoped** rather than system-scoped: `Cancel order`, which confirms before it fires because it re-baselines. That split — system state in the header, order actions in the bar — is why the toggle moved and `Cancel order` did not.

### 12.3 Tab: Live

- The MJPEG feed at natural size, CSS-scaled to fit, with an HTML `<canvas>` overlay sized to `naturalWidth × naturalHeight` and CSS-scaled identically.
- Overlay draws: the 8 bin rects, each labelled with the item name, live grams, and — when the classifier is running — the label and confidence.
- A hand marker for each tracked hand, with the pointer drawn differently from ambient.
- Toggle chips: `rects · labels · weights · hands · dots`.

### 12.4 Tab: Bins

Eight cards. Each card, top to bottom:

```
┌────────────────────────────┐
│ Bin 3        香菇 Shiitake │
│                            │
│        418 g               │   ← large, ~44px
│        ¥12.00 / 100g       │
│                            │
│  ●●●●●●○○  stable          │   ← noise indicator
│                            │
│  [ Tare ]    [ Calibrate ] │
└────────────────────────────┘
```

**Tare and Calibrate both require setting mode** (§9.1). The flow below asks the operator to empty a bin and then place a reference mass in it; in serving mode both of those are ordinary picks and would be charged for. The mode is what makes them safe — it replaced an earlier per-bin freeze that existed only because no mode-wide state had been built yet.

**Both buttons are disabled outside setting mode, and say why** — on the card permanently, and in a `title` for hover. They are never left live to fail at the end. The first version refused only when the request reached core, which meant the operator had already emptied the bin, opened the wizard and tapped Confirm before being told: the answer arrived after all the physical work, at the last possible moment. Core still refuses independently — the rule about what is safe to do to a bin belongs on the side that owns the cart, and a stale page must not be able to tare a serving table — but no operator should ever see that refusal.

**Tare has a bulk button; Calibrate cannot have one.** Setting the table means eight empty bins at once, and taring them one at a time is eight trips through a wizard whose entire content is "the bin is empty". `Tare all 8 bins` does every bin from **one** capture window — `scale.capture()` already reduces all eight channels over the same window — so it takes 2 s rather than 8 × 2 s, and every bin's zero comes from the same instant, which matters more than the speed: a drift affecting the whole board lands on all of them identically instead of being smeared across sixteen seconds. Each bin still gets its own zero out of that window; a shared one would mis-weigh seven bins. Calibrate has no bulk form because each bin needs its own reference mass physically placed in it, one at a time.

Calibration flow, one screen at a time, no branching:

1. Tap **Tare** → "Make sure bin 3 is empty" → **Confirm** → 2s capture → "Done. Bin 3 reads 0 g."
2. Tap **Calibrate** → "Place a known weight in bin 3" → numeric text field, prefilled 500 g, overtype with the physical keyboard (Enter submits) → **Confirm** → 2s capture → "Done. Bin 3 reads 500 g."
3. If the result fails the sanity check (§9.6), show "That didn't work — check the wiring for bin 3, or use a heavier weight" and do not save.

The operator is never shown or asked about counts, sign, multipliers, or orientation.

### 12.5 Tab: Orders

Restaurant-management surface. This is what makes the entry look like a product rather than a demo.

- **Queue:** cards for today's orders, newest first, `code · time · total · status`. Status advances `new → cooking → served` with one tap. Void requires confirmation.
- **Order detail:** the itemised lines, weights, prices, broth, spice.
- **Today:** order count, gross revenue, average order value, top 5 items by weight sold. Plain numbers and one bar list — no charting library.
- **Low stock:** bins whose current weight is below a per-item threshold, surfaced as a list and mirrored to the table as a pulsing plate.

### 12.6 Tab: Setup

- **Calibrate projector ↔ camera** (dot calibration wizard, §17.2). Big single button, then a progress line, then a result with the RMS error in pixels and a plain-language verdict.
- **Adjust bin rectangles** — drag the 8 rects on the live feed. Snap-to-grid optional. Undo. Save is explicit.
- **Verify** — projects the derived stage rects onto the table and asks the operator: *"Are the outlines sitting on the trays?"* with **Yes** / **No**. **TRAP:** this human confirmation is the only verification of the homography's direction that can actually fail. Do not replace it with a reprojection check.
- Swap hands (§11.3), locale, broth and spice options, deadband, dwell time, confidence floor.
- **Developer panel toggle** — off by default (§12.8).

### 12.7 Tab: Capture — dataset collection

This exists so that training data can be gathered from the real rig under the real lighting, which is the only data that matters.

- Live view of the 8 bin crops, side by side, at the resolution the model will see.
- Each crop has a label selector defaulting to the current bin-map item.
- **Capture all** — saves 8 crops with their assigned labels in one tap.
- **Burst** — N frames over M seconds (default 10 over 5s), so the operator can nudge the tray between frames and get pose variation.
- Session counter per label, so the operator can see they have 40 mushrooms and 6 prawns and go collect more prawns.
- Files: `datasets/captures/<label>/<unixms>_bin<i>.jpg`, plus a sidecar `.json` with bin index, rect, timestamp, and the exposure/WB **and `field_level`** values from `state/camera_settings.json`.
- **Export for Edge Impulse** — runs `tools/export_edgeimpulse.py`, producing a folder-per-label tree ready for `edge-impulse-uploader`.

Deliberate design note, **revised under I9**: an earlier session concluded "do not vary the lighting and do not measure the illuminant," on the reasoning that the dataset spans varied conditions naturally across sessions so the model learns to ignore illumination, and that illuminant measurement only matters for a single-illuminant dataset — "which this is not."

That premise no longer holds. In a dark room with the projector as the only light, this **is** a single-illuminant dataset, and the illuminant is one this system controls exactly. The conclusion changes accordingly:

- **Do not vary the lighting** — still right, and now for a stronger reason. There is nothing to vary; the field is a constant.
- **Do not measure the illuminant** — still right, but not because measurement is unnecessary. It is unnecessary because the illuminant is *known*: it is a flat white patch at a recorded `field_level`. Record it (above); do not go photometering it.
- **New and load-bearing:** capture must run with the bin patches lit exactly as serving mode lights them. A capture session taken with a different `field_level`, with the fluid or the staff grid bleeding into a cutout, or during calibration's black-field inversion, produces a training set the live rig will never reproduce. The Capture tab must therefore drive the same bin-patch path as serving mode, not its own.

### 12.8 Developer panel

Hidden behind a toggle in Setup, off by default, persisted in `localStorage`. When on, a collapsible strip appears at the bottom of every tab:

- Per-process: PID, uptime, restart count, last heartbeat age, CPU%, **pinned core**.
- **Machine: package temperature, current core clock, and a throttle flag.** Not decoration. §1.4 explains why: this workload saturates four cores and the iGPU beside a heat source, and a throttled J4125 loses clock on all four cores at once. That presents as the fluid degrading and the tracker slowing *simultaneously*, which looks exactly like a software regression and will be debugged as one for hours. Read `/sys/class/thermal/thermal_zone*/temp` and the `package_throttle_count` under `/sys/devices/system/cpu/cpu0/thermal_throttle/`. Surface the flag in red — it is the one telemetry line that turns a mystery into a five-second diagnosis.
- Camera: capture resolution, actual FPS, frame_id, shm slot in use, dropped frames.
- Tracker: inference ms, hands tracked, ids and roles, emit rate.
- oF: FPS, GPU ms, fluid sim resolution, keystone fingerprint.
- Core: loop rate, serial rate, raw counts per bin next to grams, current FSM state.
- Homography matrix, RMS error, point count.
- Live log tail with a level filter.
- **Mock controls** — the keyboard mock from Stage 2, reborn: pick/put-back buttons per bin with the `{45,6,120,3,25,80}` g size cycle. This is how M1 is demoed before load cells are wired, and it stays forever as a test harness.

### 12.9 Typography — staff view

| Role | Size | Weight |
|---|---|---|
| Page/tab titles | 20px | 500 |
| Body, labels | 16px | 400 |
| Secondary, units, hints | 14px | 400 |
| Large numerics (grams, total) | 44px | 500, tabular-nums |
| Developer panel | 13px | 400, monospace |

Font stack: `Inter, "Noto Sans SC", system-ui, sans-serif`. **`tabular-nums` on every live-updating number** — without it, digits change width as they update and the whole row jitters, which reads as instability.

---

## 13. openFRAMEWORKS RENDERER

### 13.1 Responsibilities

Receive state, tween it, draw it, warp it, play sounds. Nothing else (I2).

### 13.2 FBO stack

Two FBOs and two full-stage passes, warped once at the end. The order is what enforces I9:

```
1. fluidFBO    — the fluid simulation, upscaled to stage size
2. uiFBO       — labels, prices, widgets, cursor, overlays, bin outlines
   ─────────────────────────────────────────────────────────────
3. FLOOR LIFT  — applied to the composite of 1+2, per pixel:
                     out = k + (1 - k) * in          (k = white_floor)
4. LIGHT PASS  — flat pure-white patches stamped over every tray cutout,
                 at full field level, after everything else
   ─────────────────────────────────────────────────────────────
   → keystone warp → screen
```

**Step 4 is last on purpose, and that is the entire safety property.** I9's rule is not "remember not to draw into a cutout" — it is "you cannot." Any layer that misbehaves, any overlay added later, any uncommented test pattern, any fluid splash: all of it is overwritten by the light pass before the frame reaches the projector. A rule enforced by drawing order cannot be forgotten by a future change. This has already been proven necessary — the Stage 1a test pattern's grid and diagonals crossed four of the eight cutouts, and were only survivable while black rects were drawn after them to absorb the damage.

**Step 3, the floor lift**, is what makes "bright and vibrant" and "trackable" compatible. Rather than restricting every palette to pale tints, the styles keep their full hue and chroma relationships and the composite is lifted toward white by a constant:

- `white_floor` (`k`) is a config value in `config/system.json`, **default 0.45**, meaning no channel of any pixel outside a cutout ever falls below 45% of full output.
- The floor is on the **minimum RGB channel, not on luminance** — and that distinction is the point. A fully saturated red has a relative luminance of at most 0.21, so a luminance floor of any useful height would ban saturated colour outright and force the whole table into pastels. A per-channel floor guarantees every channel carries light — so skin still reads as skin to MediaPipe — while leaving the chroma *between* channels free. Vivid colour above a guaranteed white base.
- It is unconditional and per-frame. There is no style exemption and no "only when a hand is near" optimisation; intermittent hand tracking is a worse failure than a compromised palette.
- **VERIFY on the rig at M5, with the tracker running:** sweep `k` downward from 0.45 and find where hand detection starts dropping frames over the darkest part of the field. Set it with margin above that. This is a measured number, not a chosen one — 0.45 is a starting point.

**VERIFY, and this has bitten before:** `ofxFlowTools` leaves the blend mode as `OF_BLENDMODE_ADD`. Call `ofEnableAlphaBlending()` explicitly before drawing the UI layer, every frame. Do not assume the state you left it in.

**The scrim layer is gone.** It had two jobs and both are obsolete: black rects over cutouts are now the light pass, inverted; and dark plates behind text are unnecessary on a light field, where contrast comes from dark ink and heavy stroke weight instead (§13.4).

### 13.3 Tweening

oF holds, per bin: displayed grams, displayed price, highlight colour, plate scale. Each is tweened toward the target from `state` with a critically-damped spring (no overshoot, no oscillation) at roughly 150ms to settle. Numbers use an odometer roll (§15.3), not a linear lerp — a rolling digit reads as a value changing, a lerping digit reads as a glitch.

If no `state` message has arrived for 500ms, oF freezes the last state and draws a small connection-lost indicator in a corner. It does not black out — a frozen table is far better on a contest floor than a dead one.

### 13.4 Fonts and sizes on the table

Scale factor: the stage is 1920 px across a 1524 mm table ⇒ **1.26 px per mm**. Cap height ≈ 0.7 × font size, so `font_px = mm_cap_height × 1.26 ÷ 0.7 = mm × 1.8`.

| Element | Cap height | Font size (en) | Font size (zh) |
|---|---|---|---|
| Running total, numeral | 45 mm | 80 px | 92 px |
| Total label | 16 mm | 28 px | 32 px |
| Bin item name | 20 mm | 36 px | 42 px |
| Bin weight / unit price | 14 mm | 26 px | 30 px |
| Button label | 18 mm | 32 px | 38 px |
| Voice hint strip | 12 mm | 22 px | 26 px |
| Recap line item | 14 mm | 26 px | 30 px |
| Developer overlay | — | 16 px | 16 px |

Chinese is set **15% larger than English at equal cap height** because CJK glyphs pack far more stroke detail into the same box and lose legibility first under projector blur.

Fonts: `Inter` for `en`, `Noto Sans SC` for `zh`. Two `ofTrueTypeFont` instances per size, selected by `state.locale`.

Rules that are not style preferences:
- **Dark ink on a light field, and set bold.** The field is bright by requirement (I9), so text can no longer win on brightness and there is no dark plate to sit on. Contrast has to come from stroke width, which is the one thing a bold face has more of — already the reason the bin labels use DejaVu Sans Bold rather than the regular cut. Halos and outline rings invert with it: dark rings on light ground, never light-on-light.
- **Load each font at its final display size.** Never load small and scale up — projected text at 3× scale is mud.
- **VERIFY the CJK range API before use.** oF 0.12's `ofTrueTypeFontSettings` supports adding Unicode ranges, but the exact enum name has changed across versions. Check the installed header. Loading the full CJK range at 42 px produces a very large atlas; if load time or VRAM becomes a problem, generate the exact glyph set from `data/locales/zh.json` and add only those ranges.
- **Halo and outline rings must be FILLED geometry. No stroke API of any kind produces a ring you can rely on.** An earlier version of this bullet said to use `ofPath` with `setStrokeWidth()` and never `ofSetLineWidth()`. **That instruction was wrong and is retracted — they are the same call.** VERIFIED in the installed oF 0.12.1, not remembered: `ofGLRenderer::draw(const ofPath &)` reaches `setLineWidth(shape.getStrokeWidth())`, which is `glLineWidth()`. So an unfilled `ofPath` *is* `ofSetLineWidth()` wearing a different name, and inherits the 1 px cap the bullet was written to avoid — Mesa on Intel, the ODYSSEY's driver family, is exactly where that bites. Worse, `ofGLProgrammableRenderer::setLineWidth()` has its `glLineWidth()` call **commented out**, so on the programmable renderer stroke width is ignored outright and every line is 1 px. This app is on the fixed-function renderer today only because `ofGLWindowSettings::glVersionMajor` defaults to 2; M8's fluid will force GL 3.2+ and take every stroked ring in the app to a hairline on the same day.
  - Rectangular rings: four filled bars (`UiLayer::drawRing` is the reference implementation). No tessellator, no winding rules, correct on both renderers.
  - Circular rings — the M5 dwell ring, M8's halos: a **filled** `ofPath` built from an outer arc and an inner `arcNegative`. Never two `ofDrawCircle` calls with the background colour punched through the middle: over a fluid there is no background colour to punch with.
  - This is the third API in this project's history that was trusted from memory and turned out not to do what the name says (`addTemporalForce` in §14.1, the CJK range enum below, this one). The rule in §0 exists because of exactly this.

### 13.5 Text orientation

The counter is approached from one side. All text has a single fixed orientation toward the diner. Do not build multi-orientation text — it doubles the layout work for a case that does not exist.

---

## 14. FLUID — THE CENTREPIECE

### 14.1 Working API facts, already established

- `addTemporalForce()` **does not exist** in current master. That was the 0.10-era `ftFluidSimulation`. Do not reach for it.
- The current class is **`ftFluidFlow`**, and its inputs are **textures**: `addDensity(tex)`, `addVelocity(tex)`, `addTemperature(tex)`.
- Working injection pattern:
  - Draw the hand position as a white filled circle into a density FBO → `addDensity(fbo.getTexture())`.
  - Compute the per-frame position delta, encode it as colour `ofFloatColor(d.x*2.0, d.y*2.0, 0)` with `OF_BLENDMODE_DISABLED` into a `GL_RG32F` velocity FBO → `addVelocity(fbo.getTexture())`.
  - **Density alone gives a static blob. Velocity is what makes it flow.** Both are required.
- Routing point input through `ftOpticalFlow` **does not work** — optical flow only resolves small frame-to-frame displacements in real video. The entire `opticalFlow + combinedBridgeFlow` chain can be deleted, which also frees GPU budget on the ODYSSEY — where, per §14.6, it is a memory-bandwidth refund rather than merely tidier code.
- Two C++17 patches are required in the addon: `ftPixelFlow.h` lines 50–51 `min()` → `std::min()`, and `ftAverageFlow.cpp` line 186 `std::bind2nd` → a lambda. Carry these as a **fork added as a git submodule** so the Linux build does not rediscover them at the worst possible moment.

**VERIFY at the start of M8:** enumerate `ftFluidFlow`'s actual `ofParameterGroup` at runtime and log every parameter name. The names below are the standard Stam-solver vocabulary and are almost certainly right, but "almost certainly right" is how the `addTemporalForce` hour was lost. Print the real list, then map the styles onto it.

### 14.2 Parameter vocabulary

The relevant knobs, in the standard GPU-fluid sense:

- **viscosity** — velocity diffusion. <cite index="20-1">Increasing it smooths out movement and gives the impression of a thicker, more viscous medium.</cite>
- **vorticity** (vorticity confinement) — restores the fine rotational curl that a coarse grid damps away. <cite index="21-1">Numerical dissipation on a coarse grid damps out the rotational features that make smoke and low-viscosity fluid interesting; vorticity confinement computes a force that restores an approximation of the dissipated vorticity.</cite> This is the single most important parameter for making the fluid look alive rather than like a smudge.
- **dissipation** — how fast density fades. <cite index="20-1">Even a small dissipation value makes the medium fade away noticeably with time.</cite>
- **temperature / ambientTemperature / gravity** — buoyancy. Hot density rises. This is what makes a broth look like it is simmering rather than being stirred.

### 14.3 The three styles

Three, with genuinely different colour and behaviour, so the table does not become one animation the judges have already seen by minute two. Cycled per session, or set by staff, or by voice.

**All three run on a light field, and this is a change from the first draft of this section.** Every palette below was originally specified against a near-black base — the fluid was a glowing thing in a dark room. That is incompatible with I9: a dark field blankets the table in exactly the light the tracker needs, and a saturated moving field over a cutout is exactly the coloured, patterned wash the classifier must never see. So:

- **The base of every palette is the paper**, near-white. Density does not add glow to darkness; it adds **colour and depth to paper**, like oil pooling on broth or ink spreading in water. This is a better read from directly above anyway — a top-down pot is a coloured surface, not a light source.
- **The stops below are pre-lift values.** They are composited and then lifted by `k` (§13.2), so a stop that looks near-black here lands as a mid tone on the table. Palettes are authored at full chroma and the floor handles trackability; do not pre-pale them by hand and then lift them twice.
- **No style touches a cutout.** The light pass covers all eight regardless of what the fluid is doing (§13.2), so styles need no per-bin masking logic of their own.

---

**Style 1 — 麻辣 `mala` (default, serving mode)**

The signature Chongqing red-oil surface. <cite index="2-1">The beef tallow base is spicy, aromatic, and visually dense</cite> — that is what this evokes.

| Property | Setting |
|---|---|
| Palette | paper `#FFF4EA` warm white → mid `#FFB020` oil gold → peak `#E8442A` chilli red |
| Vorticity | **high** (~0.7 of range) — curling, rolling, alive |
| Viscosity | low-mid — oil, not syrup |
| Dissipation | **low** — trails linger, the table stays warm-looking between diners |
| Buoyancy | positive; injections rise slowly |
| Particles | chilli flakes and Sichuan peppercorn specks advected by the velocity field, ~200 sprites, respawned at the edges |

Density ramps paper → gold → red, so denser means *redder and deeper*, not brighter. The peppercorn and chilli-flake sprites become genuinely legible here in a way they never were on oxblood — dark specks on a warm field, which is what they actually look like in a pot.

Reads as: a simmering red-oil pot seen from directly above. This is the one that must look right, because it is on screen 90% of the time.

---

**Style 2 — 清汤 `qingtang` (clear broth, calm)**

| Property | Setting |
|---|---|
| Palette | paper `#FFFBF2` cream → mid `#EBDCA8` pale broth gold → peak `#C9A227` amber |
| Vorticity | **low** |
| Viscosity | **high** — slow, silky, ink-in-water ribbons |
| Dissipation | mid |
| Buoyancy | near zero; motion comes almost entirely from the hand |
| Particles | goji berries and jujube slices, ~40 sprites, large and slow |

The narrowest palette of the three and the brightest overall, which makes it the safest style under the floor: it sits well above `k` almost everywhere, so the lift barely touches it. The red goji sprites are the only strong chroma on the table.

Reads as: a clear bone broth, barely moving. This is the style to use when the UI is dense — a full cart with eight labelled bins and a recap card needs a quiet background, and switching to this automatically when the cart exceeds ~6 items is a good adaptive touch.

---

**Style 3 — 水墨 `shuimo` (ink wash) — the judge-facing one**

| Property | Setting |
|---|---|
| Palette | paper `#F4EFE4` background, ink `#14100E`, single accent vermillion `#C1272D` |
| Vorticity | mid-high |
| Viscosity | **very low** — sharp, fast tendrils |
| Dissipation | **high** — blooms then fades, like ink dropped in water |
| Buoyancy | slight negative; ink sinks and spreads |
| Particles | none — the density field is the whole image |

Reads as 水墨画, Chinese ink-wash painting. Culturally legible to the judges at a glance. It is also the cheapest of the three (no particles), so it is the safe fallback if the UHD 600 disappoints — and on a 12-EU iGPU sharing memory with four busy cores (§1.4c), "cheapest" has stopped being a tiebreaker and become a real argument for shipping it first.

**This style used to be the exception and is now the model.** It was the only one already built on paper, and the earlier draft of this section flagged it as an awkward inversion to be worked around. Under I9 it is the other two that moved to meet it, and the note that used to live here — "the scrim rectangles must stay black regardless of style" — is deleted, not amended. It is exactly backwards.

**The cost `shuimo` pays, stated honestly:** its ink is `#14100E`, near-black, and the floor lift (§13.2) takes that to roughly a mid grey at `k = 0.45`. Ink wash with grey ink is a weaker image than ink wash with black ink. Two things follow, and the order matters:

- The fix is **not** a style exemption from the floor. A style that goes dark is a style during which hand tracking degrades, and an intermittently-responsive table in front of judges is worse than a muted one.
- The fix is to buy contrast elsewhere: sharper tendrils, more coverage, and the vermillion accent doing more work. If that is still not enough on the rig, lower `k` globally with the tracker running and confirm detection holds (§13.2) — a measurement, not an exception.

This makes `shuimo` the style most sensitive to where `k` lands, so sweep it against this style rather than against `qingtang`, which will pass at any value.

### 14.4 Event-driven fluid moments

The fluid must not just respond to hands. It must respond to the *transaction*. These are what make it feel like the table understands what is happening.

| Event | Injection |
|---|---|
| **Hover** a bin | a gentle continuous density source at the bin centre; ring ripple outward |
| **Pick confirmed** (weight settled, delta > threshold) | radial velocity impulse at the bin + a dense colour puff, strength ∝ grams removed. Big pick, big splash. |
| **Price increment** | a thin `stream` of density from the bin to the running-total plate. Visual causality: the diner sees *why* the number moved. |
| **Put back** (weight rises) | the same stream, reversed, in a cooler hue. No refund logic anywhere — this is decoration over arithmetic that already works (I4). |
| **Done pressed** | full-field radial impulse from the table centre; all three palettes converge toward gold over ~1.5s, then settle |
| **Order complete** | one bright bloom, then the field calms to near-still for 3s while the order code is displayed |
| **Low stock** | a cool desaturated patch parked **around** that bin, slowly pulsing — an annulus outside the cutout, never over it (I9) |
| **Idle attract** | slow automated velocity injections along a Lissajous path, one every ~4s, so the table breathes when nobody is there. Stops the instant a hand appears. |

### 14.5 Setting mode — the visible difference

**In setting mode the fluid is off entirely.** Not dimmed — off.

**Setting mode cannot go dark, and this is the correction that matters most in this section.** The earlier specification here was a flat dark slate `#0E1114` background. That is precisely backwards: per §3, **the classifier runs in setting mode and sleeps in serving mode.** Setting mode is the only time food is being classified at all, so it is the one mode where the illuminant requirement is not merely active but critical. A dark slate field would starve the classifier at the exact moment it is working — while serving mode, which needs no classification, sat brightly lit doing nothing with the light.

Setting mode look, corrected:

- **The field and the bin patches are identical to serving mode.** Same white, same `field_level`, same light pass. This is not negotiable and is not a look.
- The mode difference is carried entirely by **hue and chrome**, per I8: amber UI chrome instead of red, a persistent banner **panel** (see below), and every bin plate showing its numeric grams and raw confidence.
  - **The Chinese string is NOT decided and must not be invented.** `zh` locale data does not exist yet (M1 is English-only end to end) and §17.3 is explicit that Chinese judges will read this. Get it confirmed by a native speaker before any zh string ships. The banner is English-only until then.

#### The banner panel — position and words

Two corrections, both made after seeing the first version on the table.

**It is not a full-width strip along the top edge.** That is what this section said, and it covered the far row's item names. Those labels are drawn *upward* from their rings into the 177 mm far margin, and a two-line wrapped name puts ink as high as ~50 px — inside a 72 px strip. Staff have to read those names to confirm which tray is which, during setting mode above all, which is precisely when the banner is up. Covering them defeats the mode.

The panel therefore sits in the **centre column** — the span between bin 1's right edge and bin 2's left edge, the pot gap — which is the one horizontal span on the table with no bin and no label in it, by construction. Derive it from the bin rects, never hardcode it. Being narrower it is taller and two-line; a ~440 × 88 mm amber block is still unmistakable from three metres, which was the actual goal. The strip shape was only ever one way to get there.

**The words are for a diner, not an operator.** Both banners lead with the same headline:

| State | Headline | Subline | Fill |
|---|---|---|---|
| setting mode | `NOT SERVING` | `setting the table` | amber |
| `overlay.kind == "error"` | `NOT SERVING` | `scales offline` | red |

The headline is the only part a diner needs and is equally true of both; which one it is matters only to the operator, who gets it from the subline and the hue. The earlier text said `NOT BILLING`, which is an internal word on an external surface — and it was the system's **second** word for an idea the mode was already calling *serving*. **There is one word now, "serving", and it is used everywhere**: the banner, the mode name, and the staff view's refusal messages. Do not reintroduce "billing" in anything an operator or a diner reads; it survives only in code comments about the cart.
- The 100 mm calibration grid is drawn as **dark lines on the light field**, and is **masked out of the bin patches** — the light pass (§13.2) does this for free, but it is worth knowing why the grid appears to break at the cutouts. Dark lines crossing a cutout are exactly the patterned shadow I9 exists to prevent; that is not a rendering bug and must not be "fixed."
- Fluid off plus a visible grid plus amber chrome plus a banner is still unmissable from across the room, which was the actual goal. Darkness was only ever one way to achieve it, and it was the one way that broke the classifier.

**Dot calibration remains the exception** and inverts the field to black with white dots (I9). That is a distinct overlay state within setting mode, it never coincides with food classification, and it is the only time the table goes dark.

#### Banner precedence — a general rule, not a special case

There is **one** top-edge banner strip. More than one state can claim it at once, so the order is fixed here rather than re-argued each time a new one arrives:

> **The state that changes what the table is DOING outranks a fault report from a subsystem that state has already disabled.**

Concretely, **`SETTING` wins over `error`.** Both are true at once the moment someone knocks the XIAO cable out during setting-mode work. Nothing bills in setting mode, so `SCALES OFFLINE — NOT BILLING` would be warning about a risk that cannot occur, while displacing the message that is true. The person doing that work is holding the tablet, whose Bins tab already reads `Load cells: no connection`; the table banner is for everyone *not* holding the tablet.

`calibrating` (M4) and `recap`/`qr` (M6) each land on this same strip and are settled by the same rule.

### 14.6 Adaptive quality — using the GPU fully without gambling

The 60 FPS figure came from Arc integrated graphics on the development machine. **It will not hold at 480×270 on the deploy board**, and now that the board is known (§1.4) that can be said plainly rather than hedged. UHD 600 is 12 EUs at 750 MHz — roughly 144 GFLOPS against the Arc part's several TFLOPS — and, more to the point, its memory is the same LPDDR4 the four CPU cores are using. A ping-pong fluid solver is bandwidth-bound: the pressure solve alone at 480×270 RGBA32F is on the order of 7 GB/s at 60 fps, out of ~38.4 GB/s shared with everything else.

The controller does not change. **What changes is that it starts at the bottom and climbs, instead of starting in the middle and falling.**

```
sim_scale ∈ {8, 6, 4, 3, 2}      # stage_size / sim_scale = simulation grid
start at config value (default 8 → 240×135 on the deploy board;
                       4 on the dev machine, set in config/system.json)
every 2s:
  if avg_fps > 58 and sim_scale > 2:  sim_scale -= 1   # step up quality
  if avg_fps < 50:                    sim_scale += 1   # step down, immediately
  if sim_scale == 8 and avg_fps < 45: disable particles
  if still < 40:                      fall back to style `shuimo` and report it
```

Starting at 4 on this board means the table stutters visibly for the first several seconds of every run while the controller steps down one rung at a time — the diner sees the worst frame of the session first. Starting at 8 means it looks correct immediately and quietly improves. The ceiling found is identical either way. Make the start value a config field per machine rather than a constant, so the dev machine keeps starting at 4.

Two levers to try before accepting whatever rung it lands on, both cheap and both bandwidth rather than shader work:

- **RGBA16F instead of RGBA32F** for the ping-pong targets, wherever ofxFlowTools allows it. This halves every byte moved above, and half-float is more than enough precision for a velocity field that is being rendered, not integrated over minutes. Try this first; it is likely worth a full rung or more.
- **Deleting the `opticalFlow + combinedBridgeFlow` chain**, which §14.1 already establishes does not work for point input. On the dev machine that was free cleanup. Here it is a direct bandwidth refund.

Report the current `sim_scale` in `stat` so the developer panel shows it. This satisfies "use the hardware at its maximum" honestly: it finds the maximum rather than assuming it, and it degrades in a defined order instead of stuttering.

**Run the projector output at 1920×1080, not 4K.** The board will happily drive 4K@60 over HDMI 2.0a and the fill rate cost of doing so is not recoverable. Nothing in this design — fluid, plate rendering, type — resolves anything a projector shows at 1080p.

---

## 15. SOUND

### 15.1 Where audio lives

**oF owns the audio device.** Sound is presentation, core must never block on it, and oF already runs a real-time loop. Core sends `{"t":"evt","kind":"sound","id":"..."}` and oF plays it.

One process owning the output device also means there is never a contention problem when voice output is added later (§16.3).

### 15.2 The sound set

All short, all non-annoying at the 200th repetition, all pre-rendered WAV in `of/hotpot-table/bin/data/audio/`.

| id | When | Character |
|---|---|---|
| `hover` | pointer enters a bin | very soft tick, −18 dB |
| `dwell_tick` | every 300ms during a dwell | rising pitch ladder, 4 steps |
| `dwell_fire` | dwell completes | clean confirm chime |
| `pick_confirm` | weight settles, item added | a wooden *tok*, pitch shifted by grams — small pick high, big pick low |
| `putback` | weight rises | the *tok* reversed |
| `total_tick` | running total changes | tiny click per digit roll |
| `mode_setting` | entering setting mode | two-tone descending |
| `mode_serving` | leaving setting mode | two-tone ascending |
| `broth_select` | broth chosen | a soft ladle-in-liquid sound |
| `spice_select` | spice chosen | short sizzle |
| `order_done` | order confirmed | warm three-note resolve |
| `error` | refused action | soft double thud, never a harsh buzzer |
| `attract` | idle loop, every 30s | almost inaudible simmer bed, loopable |

Design constraint: **there is a diner standing 500 mm away in a noisy hall.** Everything must be audible at that distance without being loud, so favour mid-frequency percussive sounds over low rumbles or high pings, both of which lose to hall noise.

### 15.3 Odometer and audio together

The running total rolls digit by digit like a mechanical odometer, and `total_tick` fires per digit roll. The pairing is what sells it — a silent odometer looks like a lagging number, an odometer with clicks looks like a machine counting money.

---

## 16. VOICE

### 16.1 Should there be voice at all — the honest answer

**Voice input: yes, as a redundant accelerator. Never as the only path to any action.**

Every voice command must have a working hand-dwell equivalent, always. Reasons: a contest hall is loud, accents vary, and a voice-only path that fails in front of judges fails visibly. As a redundant accelerator, a voice failure is invisible — the diner just uses the button.

**Voice output (the table talking): design for it, do not build it in the first pass.** A table that speaks is charming once and grating on repeat, and a demo hall is exactly where repetition happens. The architecture stays open to it (§16.3) at essentially zero cost, and it can be enabled if the demo feels like it needs it.

### 16.2 How the diner learns the commands

This is a real problem — an invisible interface is not an interface. Four layers:

1. **A persistent voice hint strip** along the diner-facing table edge, showing 2–3 *currently valid* commands for the current FSM state, rotating every 6s. In SELECTING: "Say **done** · say **spicy**". In BROTH: "Say **mala** or **clear**". Commands that are not currently valid are never shown.
2. **A microphone glyph** that lights when the keyword spotter is listening and pulses when it hears something. Feedback that the system is even trying is most of the battle.
3. **Idle attract mode** cycles the full command list slowly as part of the ambient display, so a diner waiting in the queue learns them before reaching the table.
4. **Bold-styled voice words on the buttons themselves** — the Done button reads `结账 / Done` with "done" styled as a spoken word. It teaches the vocabulary at the exact moment the diner is looking for that action.

### 16.3 Voice output, when it is added

**Pre-render every phrase offline. No runtime TTS.**

`tools/render_tts.py` reads a phrase list from `assets/tts_src/{en,zh}.json`, renders each with Piper TTS, and writes WAVs into `bin/data/audio/tts/`. Core then sends `{"t":"evt","kind":"sound","id":"tts/zh/order_ready"}` — the *same event type as any other sound*. oF needs no new code path, there is no runtime TTS dependency, no latency, no ALSA contention, and the phrases are deterministic and auditable before the contest.

This is why voice output costs nothing to keep open: it is already just a sound id.

### 16.4 Keyword set

Both languages in one model. The keyword spotter reports the word; core decides the meaning by FSM state (§4.8).

| Keyword | en | zh |
|---|---|---|
| done / finish | "done" | "好了" |
| cancel | "cancel" | "取消" |
| mala broth | "spicy" | "麻辣" |
| clear broth | "clear" | "清汤" |
| more spice | "spicier" | "加辣" |
| less spice | "milder" | "微辣" |
| language | "English" | "中文" |
| surprise me | "surprise" | "随便" |
| help | "help" | "帮忙" |

`surprise me` and `help` are P3. The rest are P2 and land in M9.

---

## 17. LANGUAGE, CURRENCY, AND THE CHINESE-JUDGE LAYER

### 17.1 i18n mechanics

`data/locales/{en,zh}.json` are flat key→string maps. **Core resolves every string before it leaves core** (I2). oF receives finished text and a `locale` field used only to select a font.

The staff view has its own independent locale toggle — the operator may read English while the table shows Chinese.

Locale switches via: a projected button (dwell), the voice keywords in §16.4, or the staff view.

### 17.2 Currency

Open debt #3 resolved: **currency is a property of the locale, not of the catalogue.**

```json
// data/locales/zh.json
{"_currency": {"symbol": "¥", "rate": 0.085, "decimals": 2},
 "total": "总计", "done": "结账", ...}
```

`catalogue.json` holds `pricePer100g` in the base currency (INR, since the rig is built in Kerala). Each locale carries a symbol and a conversion rate. Switching to Chinese shows plausible ¥ prices; switching to English shows ₹. For the contest, set the `zh` rate so that a typical bowl lands in the ¥30–60 band that a Chinese judge will recognise as correct for a weigh-by-weight hot pot bowl.

### 17.3 Authenticity checklist for the demo

Product name: **Hot Pot** in English, **称重火锅** in Chinese (§1.1). Never 麻辣烫 — that is a narrower claim than this build can guarantee.

Small things, cheap to build, that make the difference between "a clever table" and "they actually run this format":

- Broth options named and written correctly: 麻辣 (mala), 清汤 (clear), 番茄 (tomato). <cite index="5-1">Tomato broth is a genuinely common third option, valued for being tangy and refreshing.</cite>
- Spice level 0–3, with 0 explicitly available as plain broth. <cite index="16-1">Many shops offer a level 0 with no numbing at all, and this is a normal, expected choice.</cite>
- The recap card shows **grams per item**, because in a weigh-by-weight shop that is the number the customer actually checks.
- The order ends with a **numbered code**, matching the real handoff-to-kitchen flow.
- Ingredient names in Chinese must be the real names for the real items — 香菇 for shiitake, not a generic 蘑菇.

---

## 18. CHECKOUT

### 18.1 The flow

```
SELECTING ──"done"──► BROTH ──► SPICE ──► RECAP ──"confirm"──► CHECKOUT ──► IDLE
```

- **BROTH:** three large projected plates with names and a colour swatch each. Dwell to choose. Voice equivalent.
- **SPICE:** four plates, 0–3, with chilli glyphs. Dwell or voice.
- **RECAP:** an animated card. Line items fly in one at a time, each showing item name, grams, and line total; then the total resolves with an odometer roll. This is the moment the diner reviews and it is also the natural showpiece for judges.
- **CHECKOUT:** order written to SQLite, a short code assigned (`A17`), a QR code projected, the code spoken by the sound bus, and the order pushed to the staff view queue.

### 18.2 The payment mock — make it real enough to scan

The QR encodes a URL served by core: `http://<host>:8090/r/<order_code>`.

Scanning it on a phone opens a mobile-friendly receipt page — itemised, in the diner's chosen locale, with a **Pay ₹41.20** button that shows a success state and marks the order `paid` in the database. The table sees the payment land (via the WebSocket) and plays `order_done`.

This closes the loop *and* is genuinely impressive, because a judge can scan it with their own phone and watch the table react.

**A real UPI deep link (`upi://pay?...`) is supported as a config option and is OFF by default.** A QR that opens a real payment app asking a judge for real money is not a demo, it is an incident.

### 18.3 Timeouts

CHECKOUT auto-returns to IDLE after 90s whether or not the receipt was fetched. The order stays in the queue as unpaid. A contest floor has no patience and no diner will remember to press anything.


---

## 19. ML PIPELINE — EDGE IMPULSE

Edge Impulse replaces Roboflow, for both models. Two EI projects, one workflow, one showcase.

### 19.1 Why this works cleanly here

<cite index="29-1">Edge Impulse's Linux Python SDK runs models on Linux devices; you build a `.eim` file from the project's deployment page — selecting x86 as the deployment option for a general-purpose CPU without AI acceleration — and load it from Python.</cite> <cite index="36-1">The model file contains all signal-processing code and the neural network, and is downloaded with `edge-impulse-linux-runner --download modelfile.eim`.</cite> <cite index="38-1">The same SDK provides an `AudioImpulseRunner` for real-time microphone classification</cite>, so the keyword spotter uses the identical toolchain as the food classifier. One vendor, one workflow, two models — a far better story for Edge Impulse than a single project would be.

**Select the plain x86 target, and VERIFY the `.eim` actually runs on the board (§1.4b).** The J4125 is a Goldmont Plus part with **no AVX2**, which is an unusual thing for an x86 deploy target to lack and therefore exactly the kind of assumption a build pipeline makes silently. EI's x86 option is described as being for a general-purpose CPU without AI acceleration, which is right in intent, but "no accelerator" and "no AVX2" are different claims and only one of them is being promised.

So the check is not optional and cannot be done anywhere but on the board: download the `.eim`, run **one real inference of each model** on it, and confirm it neither aborts with `SIGILL`/`Illegal instruction` nor comes back too slow to hold `live_hz`. Do this in M0, the day the board is available, with a throwaway model if the real ones are not trained yet — the point is to test the toolchain, not the accuracy. If it fails, the fallbacks are an EI deployment target built without AVX2, or exporting TFLite and running it directly; both are recoverable in M0 and neither is recoverable the week of the contest.

### 19.2 Project 1 — `hotpot-ingredients` (image classification)

- **Task:** classification, not detection. The bin rect already localises the food; asking a detector to find it again wastes compute and adds a failure mode.
- **Classes:** the 8 catalogue items plus `empty` (an empty tray). 9 classes.
- **Input:** 160×160 RGB. Start here; drop to 96×96 only if inference time forces it.
- **Learning block:** transfer learning, MobileNetV2 α=0.35.
- **Data:** collected via the staff view Capture tab (§12.7) on the real rig. Target ≥150 images per class across ≥4 sessions on different days.
- **Existing Roboflow data is reusable:** crop each annotated bounding box out of the old detection dataset into a class folder. That converts a detection dataset into a classification dataset for free. The old `tray` class becomes `empty`.
- **Deploy:** Linux (x86_64) → `models/ingredients-x86_64.eim`.
- **Runtime:** `ImageImpulseRunner` from `edge_impulse_linux`.

The `tongs` class from the Roboflow work stays deleted. Pickup and put-back are detected by load-cell weight change, not vision. Nothing in this architecture needs to see tongs.

### 19.3 Project 2 — `hotpot-keywords` (audio KWS)

- **Classes:** the §16.4 keyword set, plus `_noise` and `_unknown`. Both are mandatory — a KWS model without a noise class fires constantly.
- **Processing:** MFCC. **Learning block:** 1D convolutional.
- **Data:** record on the actual rig microphone, in the actual room, including the projector fan and hall noise in `_noise`. Keyword models trained on clean audio fail in halls.
- **Deploy:** Linux (x86_64) → `models/keywords-x86_64.eim`.
- **Runtime:** `AudioImpulseRunner`, sliding window with overlap.

### 19.4 Backend abstraction — mandatory

Both the classifier and voice processes define an interface and two implementations. This is not over-engineering; it is what makes M1–M6 possible before any model exists.

```python
class ClassifierBackend(Protocol):
    def classify(self, bgr_crop) -> tuple[str, float]: ...

# backend_stub.py — returns the label already in bin_map with conf 0.99,
#                   or cycles labels deterministically when asked to.
# backend_ei.py    — wraps ImageImpulseRunner.
```

Selected by `config.classifier.backend`. The stub is not throwaway code — it stays forever as the offline test path and as the fallback if a model file is missing on demo day.

### 19.5 Data provenance

`models/README.md` records, per model: EI project ID, EI model version, class list, validation accuracy, confusion matrix summary, date, and the dataset session ranges used. No Git LFS. If a `.eim` exceeds a comfortable repo size, gitignore it and record the download command in the README instead.

---

## 20. CRASH RECOVERY

The second of the two originally-open questions. Settled here.

### 20.1 Policy per process

| Process | What is lost | Who notices | How | Restart |
|---|---|---|---|---|
| `camera` | frames | tracker, classifier | shm timestamp goes stale >500ms (§6.4) | auto; recreates shm, consumers re-attach |
| `tracker` | cursor | core | heartbeat timeout 3s | auto |
| `classifier` | staff scanning | core | heartbeat timeout | auto; core re-issues the pending scan |
| `voice` | voice input only | core | heartbeat timeout | auto; nothing else degrades |
| `of` | the table display | core | TCP disconnect | auto; core resends full state on reconnect |
| `core` | **everything** | of, browser, all clients | TCP disconnect | auto; **state restored from the journal, §20.3** |

### 20.2 Reconnect discipline (every client)

```
connect → on failure: sleep(backoff); backoff = min(backoff*2, 10s)
on success: backoff = 1s; send hello; wait for welcome; resume
```

A client must never exit because core is not there yet. This is what makes start order irrelevant (§3.3).

The launcher restarts a crashed process with the same backoff. After 5 failures in 60s it marks the process `failed`, stops restarting, and surfaces it loudly — on the staff view and, if it is `of` or `camera`, on the table. An infinite crash loop that silently eats CPU is worse than a stopped process.

### 20.3 Core state durability — the write-ahead journal

Core is the only process holding state that cannot be recomputed. It gets a journal.

`state/session.jsonl`, append-only, `fsync` after each line:

```json
{"t":"session_start","ts":...,"mode":"serving"}
{"t":"baseline","ts":...,"start_g":[418.2, 903.1, ...]}
{"t":"binmap_locked","ts":...,"bins":[...]}
{"t":"snapshot","ts":...,"live_g":[380.4, 903.0, ...],"total":41.20}
{"t":"order_finalised","ts":...,"code":"A17","order_id":42}
{"t":"session_end","ts":...}
```

`snapshot` is written every 2s while a cart is non-empty. Two lines per second of a small array is nothing, and it means a core crash mid-order loses at most 2 seconds.

On startup, core reads the journal. If the last `session_start` has no matching `session_end`, core restores `start_g` and the last `snapshot`, then raises a banner on the staff view: **"Recovered an interrupted order — ¥41.20. Keep it or cancel?"** A human decides. Silently resuming billing after a crash is worse than asking.

The journal is truncated on clean `session_end`. Finalised orders live in SQLite; the journal only ever holds the in-flight one.

### 20.4 Atomic writes everywhere else

Every state file in §8 is written through `atomicio.write_json`: temp file → `fsync` → `os.replace`. A power cut mid-write must never produce a half-written homography or a corrupt calibration, because either would silently mis-bill rather than visibly fail.

---

## 21. MILESTONES

Ordering principle, unchanged from the start: **the system is demoable from M1 onward, and every later milestone swaps one mock for one real part.** No milestone may leave the system undemoable.

Each milestone below is written so a Claude Code instance can start it directly. Each ends with an acceptance test that a human runs on the physical rig.

---

### M0 — Scaffold, launcher, transport

**Goal:** six processes start and stop with one command, connect, heartbeat, and survive being killed.

**Depends on:** nothing.

**Build:**
1. Repo restructure per §7. Delete everything listed in §7.1 as "delete outright". Preserve `firmware/` untouched.
2. `common/wire.py` — JSONL framing, reconnecting TCP client, TCP server with per-client callbacks.
3. `common/health.py` — heartbeat send/track, status registry.
4. `common/atomicio.py`, `common/log.py`.
5. `run.py` per §10 — process groups, readiness lines, merged prefixed logging, tiered start, clean shutdown, `--stop`, `--only`, `--no-restart`.
6. Stub `main.py` for camera, tracker, classifier, voice — each connects, says hello, heartbeats, prints `HOTPOT-READY`, does nothing else.
7. `core/main.py` — control server, client registry, and a minimal staff view serving only the header with six status pips over a WebSocket.

**Do NOT:** open the camera, open the serial port, touch MediaPipe, or write any oF code.

**Acceptance (human, on the dev machine):**
- `python run.py` → six pips green in the browser within 5s.
- `kill -9` any child → its pip goes red within 3s, then green again within 5s.
- `Ctrl-C` → every process gone. `ps aux | grep hotpot` returns nothing.
- Verify the last point specifically: an orphan holding a port is the failure this milestone exists to prevent.

---

### M0.B — Board bring-up (ODYSSEY X86J4125800 v2)

**Run this the day the board arrives, whatever milestone is in progress.** It is off the critical path and depends on nothing, but every check in it can only be done on the board, and each one has a fallback that is cheap now and ruinous in M8. Half a day here buys back the three highest risks in §23.

**Depends on:** the board being physically present. Nothing else.

**Build — in this order, stopping at the first thing that fails:**
1. **Install Ubuntu on the NVMe SSD**, not the eMMC (§1.4). Confirm both 2.5 GbE ports enumerate — they need a recent kernel; 22.04 or newer is fine.
2. **Peripheral port allocation per §1.4d.** Camera on the USB 3.1 Type-A, XIAO on a USB 2.0, power on the barrel jack. Confirm the camera negotiates its capture mode on that port and check what happens if it is moved to USB 2.0, so the failure signature is known.
3. **`arecord -l`** — the long-open VERIFY from §3. Settle whether a separate USB mic is needed while there is still time to order one.
4. **One MediaPipe Hands inference** (§11.2). Confirm no `SIGILL`. Record the achieved fps at `model_complexity` 0 and 1, and write both numbers into `CLAUDE.md`. This sets the probe start point rather than guessing it.
5. **One Edge Impulse `.eim` inference** (§19.1), a throwaway model if the real ones are not trained. Confirm no `SIGILL`.
6. **A throwaway "hello world" openFrameworks build on the board itself** (§23), followed immediately by **one ofxFlowTools example**. The oF build proves the toolchain; the ofxFlowTools example proves Mesa on UHD 600 does what the Arc driver does — float render targets and GLSL version are the two places it could differ.
7. **Fit the fan.** Run a 30-minute all-core plus GPU soak and watch package temperature and the throttle counter (§12.8).

**Do NOT:** port the actual application, tune fluid parameters, or treat any performance number here as final. This milestone answers "does it run at all", not "how fast".

**Acceptance (human, on the board):**
- Every one of items 3–6 either passes or has a written fallback decision in `CLAUDE.md`. A recorded failure with a chosen fallback passes this milestone; an unrun check does not.
- The soak test's peak package temperature and throttle count are written down. If it throttled on a bench, it will throttle harder beside a hot pot, and that is a cooling problem to solve now.

---

### M1 — Core domain + oF renderer + mock picks

**Goal:** Stage 2's behaviour, restored on the new architecture, driven from the staff view.

**Depends on:** M0.

**Build:**
1. `data/catalogue.json` (§8.1) from the existing `ingredients.json`. `data/locales/en.json` with every string used.
2. `core/pricing.py` (§9.2), `core/cart.py`, `core/binmap.py`, `core/i18n.py`, `core/fsm.py` (§9.1) — states BOOT, IDLE, SELECTING only for now.
3. Core state broadcaster: 60Hz `state` messages per §4.3.
4. oF app rewritten: `StateLink` (TCP JSONL client, reconnecting), `Stage` (FBO stack + keystone loaded from `keystone.json`), `UiLayer` — 8 plates, labels, prices, running total. Tweening per §13.3. Fonts and sizes per §13.4, English only.
5. Staff view: Live tab shell, and the developer panel's **mock controls** (§12.8) — per-bin pick/put-back with the `{45,6,120,3,25,80}` g cycle.

**Do NOT:** implement the deadband snap yet if it complicates the first pass — but if you do implement it, implement I5's snap version, never the "ignore small events" version.

**Acceptance (human, on the projected surface):**
- Clicking "pick 45g, bin 3" in the browser: bin 3's plate updates and the total rises, on the table, within 200ms.
- Picking 45g then 6g then 120g from one bin gives a total equal to 171g × price ÷ 100 — verify by arithmetic, not by watching.
- Kill core → the table freezes and shows a connection indicator; it does not go black. Restart → the table resumes.
- **State the kind of evidence:** this is physical observation of the projected surface, not a framebuffer capture.

---

### M2 — Load cells

**Goal:** real grams replace mock picks. The keyboard mock survives as a test tool.

**Depends on:** M1.

**Build:**
1. **Read `firmware/loadcells/src/main.cpp` first** and match its actual output format. Do not assume §4.9.
2. `core/scale.py` — serial thread per §9.5, median-of-5, staleness detection, settle detection.
3. Calibration maths per §9.6, persisted to `state/loadcell_cal.json` (§8.3) atomically.
4. Staff view **Bins tab** (§12.4) — 8 cards, live grams, noise indicator, Tare and Calibrate flows with the one-screen-at-a-time wizard.
5. Wire real grams into pricing. Mock controls stay, now gated behind the developer panel.

**Do NOT:** ask the operator anything about sign, orientation, or counts. The sign is computed (§9.6).

**Acceptance (human, on the rig):**
- Tare all 8 bins empty → all read 0 ±2 g.
- Calibrate bin 5 with a known 500 g mass → reads 500 ±3 g.
- Repeat for an **inverted** cell → also reads correctly, with no operator input about orientation.
- Remove ~100 g from a calibrated bin → the table's total rises by the correct amount, verified by arithmetic.
- Unplug the XIAO → serial pip red within 1s, table shows a fault overlay, **no billing occurs from the frozen reading.**

---

### M2.6 — Mode (SERVING / SETTING)

**Goal:** the state that gates all billing exists, before anything else branches on it.

**Depends on:** M2 (a real weight to freeze).

**Why here and not later:** every milestone from M3 on branches on the mode — M4's calibration wizard is a setting-mode activity, M5's hover must be inert while staff hands are over the table, M6's checkout shares `reset_session()`, M7 is built entirely on enter/exit, M8 renders the visible difference. Building it now means M4–M8 are written against a mode that exists instead of each inventing its own gate, which M2 build item 4 already had to do once.

**Build:**
1. FSM state SETTING and its two transitions (§9.1). Entry is refusable **with a reason**, not a bare false — §9.1 requires the staff view to show why.
2. "Cart is active" as a predicate on the **deadbanded** shown grams (§9.1's second rule). Reading raw removed grams instead makes the mode unreachable on a noisy cell.
3. Exit's three ordered steps, all inside the transition: refresh weights, `reset_session()`, lock the bin map. **The refresh is the trap — see §9.1.**
4. Billing gated: the scale does not reach the cart at all in SETTING. `mode` on the `state` message derived from the FSM state (§4.3).
5. Delete the per-bin calibration billing freeze M2 build item 4 added — it was the local workaround for this missing global state, and the mode makes all of it dead.
6. Wire: `set_mode` in; `mode` out, broadcast **on change, not on a timer**, carrying `cart_active` and any refusal reason.
7. `on_join` sends a list, so a joining tablet gets both the pips and the mode.
8. Staff view: §12.2's action bar, the mode toggle, the header chip.
9. oF: the §14.5 banner, and the precedence rule that goes with it.
10. Doc edits — §9.1, §4.3, §12.2, §12.4, §14.5, §15.2 and this entry, in the same commit as the code.

**Do NOT:** invent the Chinese strings for either mode (§14.5). `zh` locale data does not exist and §17.3 says Chinese judges will read them.

**Acceptance (human, on the rig):**
- Tap `ENTER SETTING MODE` with an empty cart → header chip goes amber, the table shows the amber banner, unmistakable from three metres.
- Pick ~50 g from a bin, then tap `ENTER SETTING MODE` → **refused**, with a readable reason and a working "Cancel the order first".
- In setting mode, lift a whole tray out and put a different one back → the total does not move and no pick is registered.
- Exit setting mode → **the total is 0 and stays 0.** This is the trap in build item 3. A large phantom pick here means the weight refresh is missing.
- After exit, `state/bin_map.json` has `"locked": true`.
- Tare and Calibrate are unreachable in serving mode and work in setting mode.
- Unplug the XIAO while in setting mode → the banner still reads `SETTING — NOT BILLING`, not the scales-offline one.

---

### M3 — Camera process

**Goal:** frames flow, the staff view shows them, camera death is detected.

**Depends on:** M0 (not M2 — can be built in parallel with M2 if desired).

**Build:**
1. `common/framebus.py` per §6 — writer and reader, seqlock, staleness.
2. `camera/main.py` — V4L2 open, format enumeration and MJPG preference (§6.6), exposure/WB/focus lock written to `state/camera_settings.json`, shm writer, MJPEG HTTP server with `/stream.mjpg`, `/snapshot.jpg`, `/info.json`.
3. Staff view **Live tab** — the MJPEG `<img>` plus a canvas overlay, with the `naturalWidth` scaling rule (§5.4) implemented even though nothing is drawn on it yet.
4. Developer panel: capture resolution, actual FPS, frame_id, shm slot.

**Also in M3 — the outstanding physical measurement:**
Measure the **camera elevation angle** (I10). This has been open since Stage 1 and caps every downstream accuracy claim. `tools/measure_camera_angle.md` documents the method: measure the camera lens height above the table and the horizontal distance from lens to the far bin centre, then `angle = atan(height / horizontal)`. Record the number in `CLAUDE.md`. If it is below 70°, that is a hardware problem to solve before M4, not a software one.

**Acceptance (human):**
- Browser shows the live feed at the configured resolution and rate.
- `kill -9` camera → within 1s the pips go red and a "camera stalled" banner appears; the feed resumes automatically after restart.
- Camera elevation angle measured and written down.

---

### M4 — Calibration and dataset capture

**Goal:** the geometry is real, and training data collection starts, so that model training can run in the background during M5 and M6.

**Depends on:** M1 (oF can draw), M3 (frames exist).

**Build:**
1. `common/geometry.py` — homography fit (`cv2.findHomography` with RANSAC), apply, invert. `core/geometry_store.py` — owns `H_cam→stage` and rects in both spaces (§5.3).
2. `classifier/dots.py` — projected-dot detection: threshold, contour, centroid, area filter. Returns camera-space points.
3. Dot calibration wizard: core tells oF to draw a known dot pattern (`overlay.kind = "calibrating"`), tells the classifier to detect, fits `H`, writes `state/homography.json` with `rms_px` and the keystone fingerprint (§8.5).
4. Staff view **Setup tab** (§12.6) — the wizard, rect dragging on the live feed, save, and the **Verify** step.
5. `state/bin_rects.json`, seeded from the old `bin_offsets.json` values converted to camera space.
6. UNCALIBRATED state in the FSM (§9.1) — works from a fresh clone with an empty `state/`.
7. Staff view **Capture tab** (§12.7) and `tools/export_edgeimpulse.py`.

**TRAP, restated because this is where it lives:** do not verify the derived stage rects by reprojecting through the same `H`. That passes by construction regardless of direction. The Verify step projects the rects onto the physical table and asks a human whether they land on the trays. That is the only check that can fail.

**Acceptance (human, on the rig):**
- Fresh clone with empty `state/` boots to UNCALIBRATED; the table says so; the staff view opens on the wizard.
- Run dot calibration → RMS error reported, under ~3 px.
- Drag the 8 rects on the feed, save, press Verify → the projected outlines sit on the real trays. Answer honestly.
- Nudge the keystone → the staff view raises "calibration stale — keystone changed."
- Capture 20 images per class and export → a folder-per-label tree ready to upload.
- **Sweep `field_level` against camera exposure (§6.6), pick the pair, freeze it, and confirm it is written to `state/camera_settings.json`.** Then look at a bin crop and confirm the food is evenly lit with no colour cast and no visible edge from a UI element — physical observation of the projected surface, not a framebuffer capture.
- **Every capture is taken with the bin patches lit exactly as serving mode lights them** (§12.7). If the Capture tab has its own lighting path, that is a bug to fix before collecting a single image, not after.
- **Start EI training now.** M5 and M6 do not depend on the model.

---

### M5 — Tracker, hover, dwell, buttons

**Goal:** hands drive the interface. Right hand selects, left hand does not.

**Depends on:** M3 (frames), M4 (homography — the cursor is meaningless without it).

**Build:**
1. `tracker/main.py` — shm reader, MediaPipe Hands, landmark 9 as the cursor (§11.2), homography from `welcome`, role assignment per §11.3, UDP dual-send.
2. `common/cursorbus.py` — send, and drain-to-latest receive.
3. oF `CursorLink` — UDP listener, drain-to-latest, draws the pointer cursor and the dwell ring. Ambient hands get no cursor.
4. Core hover and dwell per §9.4. Widgets: Done, Cancel, Language. Ambient hands discarded before hit-testing.
5. Staff view: hand markers on the Live overlay, "swap hands" button, dwell time setting.

**Bench tests, in this milestone (§11.5):**
- Hold the real bowl over a bin. Does it register as a hand? Record the answer.
- Hold the real tongs. Does palm confidence survive? Record the answer.

**Acceptance (human, on the rig):**
- Right hand over bin 3 → the plate highlights within ~100ms of the hand arriving.
- Left hand over bin 3 → **nothing happens to the UI.** Try hard to make it select. It must not.
- Dwell on Done → the ring fills over 1.2s and fires.
- Move a hand quickly across the table → the cursor tracks without visible replay-through-history. This is the check that UDP drain-to-latest is working.
- **Sweep `white_floor` with the tracker running (§13.2)** and record two numbers: the value chosen, and the value at which detection began dropping frames. A single number here is a chosen number; two numbers are a measured one. Do this in the dark, with the room as it will be on demo day.

---

### M6 — Checkout, orders, the closed loop

**Goal:** a diner can complete a transaction end to end.

**Depends on:** M2 (real weights), M5 (buttons).

**Build:**
1. FSM states BROTH, SPICE, RECAP, CHECKOUT (§9.1, §18.1).
2. `core/orders.py` — SQLite schema (§9.7), order codes, status transitions.
3. Recap overlay in oF — line items flying in, odometer total (§13.3, §15.3).
4. QR generation and the receipt page at `/r/<code>` (§18.2). UPI option present and off.
5. Staff view **Orders tab** (§12.5) — queue, detail, today's summary, low stock.
6. `core/journal.py` (§20.3) — the write-ahead journal and the recovery banner.
7. **Open debt #5 closes here:** at finalisation, `shown_g[i] = removed_g` unconditionally.

**Acceptance (human, on the rig):**
- Pick from three bins, press Done, choose broth and spice, confirm.
- The recap totals match arithmetic done by hand from the three weights.
- Scan the QR with a phone → the itemised receipt loads → tap Pay → the table reacts.
- The order appears in the staff queue; advance it to `served`.
- Kill core mid-order, restart → the recovery banner offers the interrupted order with the correct total.
- Take 8 g from a bin and finalise → **those 8 g are billed**, proving the deadband fix.

---

### M7 — Classifier live

**Goal:** the bin map is written by vision. Price follows the food, not the bin (I7).

**Depends on:** M4 (rects), M6 (a stable system), and a trained model.

**Build:**
1. `classifier/backend_ei.py` wrapping `ImageImpulseRunner`; `backend_stub.py` kept and still selectable.
2. Startup scan: all 8 bins at once, slow is fine.
3. Setting-mode live classification at `live_hz`, updating a **provisional** bin map shown on the table and in the staff view.
4. On setting-mode exit: apply the confidence floor and write `state/bin_map.json` atomically. The exit transition and its `locked: true` write already exist (M2.6); what this adds is the classifier's provisional map being committed through them, and blocking exit if any bin is unresolved without an explicit confirm (§9.3).
5. **No re-scan after normal diner picks.** The hand is still in frame and the food has not changed. Re-scanning there is pure risk.

**Acceptance (human, on the rig):**
- Enter setting mode, physically swap two trays → both labels follow within ~2s, on the table and in the staff view.
- Exit setting mode → labels lock; swapping trays now changes nothing until setting mode is re-entered.
- Cover a bin with an unrecognisable object → it goes unresolved, renders empty, and removing mass from it bills **zero**.
- Exit is blocked with a clear confirm while that bin is unresolved.

---

### M8 — Fluid and sound

**Goal:** the table becomes captivating. Deliberately last, because a halo 30 mm off must not be ambiguous between a calibration error and a shader bug.

**Depends on:** M5 (hand positions), M6 (events to react to).

**Build:**
1. Fork `ofxFlowTools`, apply the two C++17 patches, add as a git submodule.
2. **VERIFY first:** log `ftFluidFlow`'s actual parameter group at runtime (§14.1) before mapping styles onto it.
3. `FluidLayer` — density and velocity FBO injection per §14.1. Delete the `opticalFlow + combinedBridgeFlow` chain entirely.
4. The three styles (§14.3) as named parameter presets plus palette LUTs.
5. Event-driven injections (§14.4). Ambient hands inject; they still select nothing.
6. Setting mode visual (§14.5) — fluid off, **field and bin patches unchanged from serving mode**, grid masked out of the cutouts, amber chrome. The banner itself is already built (M2.6), including its precedence rule; the rest of the §14.5 look is this build item.
7. Adaptive quality controller (§14.6), reported in `stat`.
8. `AudioBus` and the full sound set (§15.2), including `mode_setting`/`mode_serving` on the transitions M2.6 built. Odometer ticks paired with the total roll.

**Acceptance (human, on the rig):**
- Each of the three styles runs at ≥55 FPS with the full UI drawn. Record the achieved `sim_scale` for each.
- A big pick produces a visibly bigger splash than a small pick.
- The price stream visibly connects the bin to the total.
- Entering setting mode is unmistakable from three metres away — **and the field does not darken when it happens** (§14.5). If the table dims on entering setting mode, the classifier has just been starved.
- **Run all three styles at full intensity and watch the cutouts.** Not one frame of colour, texture or shadow reaches a bin, in any style, during any event burst, including the full-field `Done` impulse. Physical observation of the projected surface. If this can fail, the light pass is not last (§13.2).
- Left hand stirs the fluid and selects nothing — verify again here, because this is where the temptation to let ambient hands "just also count" appears.

---

### M9 — Voice

**Goal:** a redundant accelerator that cannot break anything by failing.

**Depends on:** M6 (commands to issue), and a trained KWS model.

**Build:**
1. `voice/backend_ei.py` wrapping `AudioImpulseRunner`; stub kept.
2. `voice/main.py` — continuous sliding-window inference, threshold, debounce (one fire per keyword per 1.5s), report-only (§4.8).
3. Core maps keyword → action **by FSM state**.
4. The voice hint strip and mic glyph in oF (§16.2).
5. Voice words styled on the buttons themselves.

**Acceptance (human, on the rig):**
- Every voice command has a working hand equivalent. Verify each one both ways.
- `kill -9 voice` → nothing else degrades; the hint strip disappears; the buttons still work.
- Say a keyword with the projector fan running and hall noise playing. Record the hit rate honestly.

---

## 22. PRIORITY TIERS

If time runs out, cut from the bottom.

**P0 — without these there is no entry.** M0, M1, M2, M4, M5, M6. Camera (M3) is P0 only because M4 needs it.

**P1 — without these the entry is weak.** M7 (classifier — this is the "Technology" score), M8 (fluid and sound — this is the "Interaction" score).

**P2 — differentiators.** M9 voice. Chinese locale. The scannable payment mock. The Orders tab.

**P3 — good to have, cut without regret.**
- Low-stock pulse and sold-out marking — both as treatments *around* a bin, never as light removed from it (I9)
- Idle attract mode
- Combo suggestion popups
- Dietary filter
- "Surprise me" wave gesture
- Bowl-fill progress icon
- Judge-facing debug overlay on the table
- Voice output (§16.3)
- Today's-revenue charts beyond plain numbers

Note that several P3 items are nearly free once P1 exists — low-stock pulse is one `hl` enum value and one threshold. Build them opportunistically, never on the critical path.

---

## 23. RISK REGISTER

| Risk | Severity | Mitigation |
|---|---|---|
| ~~reComputer model unidentified~~ | **CLOSED.** Resolved to the ODYSSEY-X86J4125800 v2 — see §1.4. Core count is settled (§10.4), the GPU is characterised (§14.6), and the fluid target is reachable at a lower sim resolution than the dev machine reaches. Superseded by the three rows below, which are the parts of this risk that survive. | — |
| **No AVX2 on Goldmont Plus** | **Highest.** MediaPipe (§11.2) and the Edge Impulse `.eim` runners (§16) are the exposed surfaces. Worst case is a SIGILL on first inference from an AVX2-only build; ordinary case is a slow SSE fallback. Nothing on the development machine can reveal either. | Run one real inference of *each* model on the board in M0 — hands, ingredients, keywords. This is a 20-minute check that de-risks two whole milestones. Fallbacks: rebuild for this target, or drop to plain TFLite models. The stub backends (§19.4) mean neither blocks anything else. |
| **Thermal throttling in the deployed position** | **High**, and near-invisible until it happens. 10 W passive part, four cores plus iGPU saturated indefinitely, mounted near a hot pot with steam and grease. Throttling degrades fluid and tracker at the same instant and reads as a software regression. | Fit the fan, mount away from the burner in a ventilated enclosure, and put package temperature plus the throttle flag on the developer panel (§12.8). Soak-test at M9 for the length of a real service, not for a demo's length. |
| **64 GB eMMC is not enough** | Medium, but certain to bite. Ubuntu + the oF tree and build artifacts + models + `orders.sqlite3` + logs + the §12.7 dataset capture do not fit comfortably, and eMMC random write is poor enough that SQLite commits and image capture both suffer. | Order an NVMe SSD for the M.2 Key M slot with the board (§1.4) and install the OS on it. If that slips, move `datasets/`, `logs/` and `state/` there and leave the OS on eMMC. |
| Camera elevation angle unmeasured | High — caps all hand-position accuracy (I10) | Measured in M3. If <70°, remount before M4. |
| oF build on Linux | High — the Makefile and `config.make` must be generated **on the board itself**, and the ofxFlowTools patches must be in the submodule or they will be rediscovered at the worst moment. Additionally, ofxFlowTools' GL feature use must be checked against Mesa on Gen9LP (UHD 600) rather than against the dev machine's Arc driver — float render targets and the exact GLSL version in particular. | Do a throwaway "hello world" oF build on the board the day it arrives, long before M8, and follow it immediately with one ofxFlowTools example. Two hours in M0 against two days in M8. |
| MediaPipe confidence with tongs | Medium — could break the whole hand interaction | Bench test in M5. Fallback: increase the bin hover zones and lean on weight for confirmation, which already is the source of truth. |
| Classifier accuracy on visually similar items | Medium — soya chunks, prawns and mushrooms are the known-hard set | Confidence floor already protects billing (§9.3). Collect more data on exactly those three. |
| **The projector is the only illuminant (I9)** | **High** — `field_level` and camera exposure are one coupled parameter, so anything that changes the light on a cutout silently invalidates the training set. A projector bulb dimming with age does this slowly and invisibly. | Sweep and freeze the pair at M3/M4, record both in `state/camera_settings.json`, and re-capture rather than re-tune if the projector is ever changed. The light pass (§13.2) structurally prevents the *rendering* half of this class of bug; the hardware half needs the record. |
| Venue will not go dark | Medium — I9 assumes a dark room, and a contest hall may be brightly lit or have windows | The design does not invert: a white field is still correct under ambient, it just stops being the *only* light. What breaks is the dataset, since ambient is uncontrolled. Mitigation is to capture on-site during setup if the hall light differs materially from the build room, which the Capture tab already supports (§12.7). Decide by looking, not in advance. |
| `white_floor` set too low for the tracker | Medium — shows up as intermittent hand detection over dark parts of the fluid, which looks like a tracker bug and will be debugged as one | Sweep `k` at M5 against `shuimo`, the worst case (§13.2, §14.3). Record the value at which detection starts dropping, not just the value chosen. |
| Model files missing on demo day | Medium | The stub backends stay forever and are selectable in config (§19.4). |
| Egg and potato spoiling | Low but certain | Placeholders during development; the real items only on demo day, as already planned. |

---

## 24. WHAT IS STILL DELIBERATELY UNDECIDED

Everything else in this document is settled. These are not, and each is small enough to decide when it is reached:

1. **Exact dot pattern for calibration** — count, spacing, and whether to run two passes at different densities. Decide at M4 with the real camera field of view in front of you.
2. **Broth as a one-time or mid-session choice.** Currently specified as a step in the checkout flow, which makes it one-time. If it should be changeable mid-session, the FSM gains a `current_broth` field and BROTH becomes reachable from SELECTING. Cheap either way; do not decide it in the abstract.
3. **Whether the third fluid style ships.** Two would be enough. `shuimo` is the highest-value and lowest-cost of the three, so if only two ship, ship `mala` and `shuimo`.
4. **Multi-diner attribution.** Assumed out of scope: at a weigh-by-weight counter people queue with their own bowl rather than crowd. Confirm by watching the real usage pattern before building anything for it.
5. **Kitchen handoff beyond the staff view queue.** A printed ticket or a kitchen screen is a possible P3 addition. The order is already in SQLite, so this is a rendering problem, not an architecture one.

---

## 25. FIRST INSTRUCTION TO A CLAUDE CODE INSTANCE

> Read `docs/HOTPOT_ARCHITECTURE_v3.md` in full before writing anything.
> Then start **M0, step 1** only: the repository restructure per §7, deleting everything listed in §7.1 as "delete outright" and preserving `firmware/` untouched.
> Do not write `wire.py`. Do not write `run.py`. Do not touch the oF app beyond deleting the logic listed in §7.1.
> Commit with the message `M0.1 restructure repository for v3 architecture`.
> Stop after the commit and report back.
