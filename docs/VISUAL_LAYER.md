# Visual layer spec — bins, halo, fire, cart

Renderer-side only. oF stays dumb: it receives semantic state from Python core
and draws. No pricing, no FSM, no deadband logic in oF.

---

## 1. Background is LIGHT, not dark

The projector is the room light. The table surface must be bright enough to
illuminate food in a dark venue.

- Table background: `#E8E6E1`
- Bin interior: `#FFFFFF` — pure white, all 8 bins, all modes, always

The bin interior is white in every mode. Two independent reasons:
1. The classifier needs light on the food (startup scan, staff-exit rescan).
2. The diner needs to see what they are reaching for during serving.

Fire NEVER enters a bin interior. This is a constraint, not a preference.

---

## 2. Blending — read this before writing any fire code

ofxFlowTools leaves `OF_BLENDMODE_ADD` set after its draw call.

Additive blending on a light background produces WHITE. Fire rendered
additively over `#E8E6E1` will be invisible.

Fire must DARKEN the surface, not brighten it:
- Use `OF_BLENDMODE_MULTIPLY`, or `OF_BLENDMODE_ALPHA` with opaque saturated colour
- Density → colour mapping: high density = `#C74A34`, low density = fully transparent
- Do NOT map low density to a pale colour — pale on light = nothing

Call `ofEnableAlphaBlending()` at the start of layer 3 (below) so every layer
above the fluid uses normal blending.

Bench-test this before building the rest. If multiply looks wrong, try alpha
with fully opaque colour. Report which works.

---

## 3. Palette (light background)

| Element | Colour | Size |
|---|---|---|
| Table background | `#E8E6E1` | — |
| Bin interior | `#FFFFFF` | — |
| Plate name | `#2B2118` | 28px bold |
| Plate rate | `#B8781A` | 26px regular, monospace |
| Halo — idle | `#B8781A` | — |
| Fire ring — active | `#C74A34` core, `#D9822B` tips | — |
| Fumes | `#A8A49C` | — |
| Cart panel fill | `#FFFFFF` | — |
| Cart border | 2px `#C9C5BC` | — |
| Cart row — filled name | `#2B2118` | 26px |
| Cart row — filled g + cost | `#6E6A62` | 26px |
| Divider above total | 2px `#C9C5BC` | — |
| Total label | `#2B2118` | 30px |
| Total value | `#B8781A` | 48px bold |
| Info box fill | `#F7E4DC` | — |
| Info box border | 2px `#C74A34` | — |
| Info box text | `#8A3524` | 24px |

All fonts loaded via `ofTrueTypeFont` at final display size. Never scale up.

**Plate name corrected from 40px to 28px, 2026-08-14, from a real rig
photo (step 2, below): 40px overflowed a 200mm bin and ran into the
paired bin's own name.** Re-measured against the real catalogue's
`shortLabel`s and the real font (PIL/FreeType) rather than re-guessed —
28px is the largest size at which all of them fit inside one bin's
width. Plate rate is monospace (developer request, same session) so a
picked price's width doesn't shift digit to digit; see CLAUDE.md's M8
section for the font file and the measurement.

---

## 4. Geometry

All derived from the calibrated bin rect. No new calibration step.

- `haloRect = binRect` inflated by `HALO_MARGIN` (start at 20px)
- `fireRect = binRect` inflated by `FIRE_RING` (start at 52px)
- `plateRect` = fixed height `PLATE_H` (start at 130px), sits above the bin on
  the top row, below the bin on the bottom row
- Halo wraps the BIN ONLY, never the plate

Plate has no fill and no border. Text sits directly on the table background.
It stays readable because fire never reaches it — see layer order.

Constants go in a JSON config, not hardcoded.

---

## 5. Layer order

Bottom to top:

1. Table background `#E8E6E1`
2. Fluid FBO — fire ring + fumes
3. `ofEnableAlphaBlending()`, then all 8 white bin rects, opaque
4. Halo strokes
5. UI layer — plate text, logo, cart, info box

Layer 3 punches fire out of all 8 bin interiors. Layer 5 guarantees text is
never covered by fire.

No fluid-sim obstacles needed. Draw order does the occlusion. Do not try to
add obstacle geometry to the sim.

No keystone in software. The projector handles keystone.

---

## 6. States

### Idle (7 bins, and all 8 when nothing is active)
- Halo only, no simulation
- ~16 nested `ofPath` rounded-rect strokes, each 2–3px further out
- Alpha falls off quadratically from the bin edge outward (brightest at edge)
- Use `setStrokeWidth()`, NOT `ofSetLineWidth()` — the latter is driver-capped at 1px
- Draw halo BEFORE the white bin rect so the rect cuts the inner edge cleanly
- Slow breathing sine on alpha, each bin phase-offset by a per-bin random seed
  so the 8 do not pulse in sync
- Cost: pure vector, no simulation

### Active (max 1 bin at a time)
- Gold halo crossfades OUT as the fire ring crossfades IN
- Never both at once in the same annulus — they go muddy
- Fluid sim emits into the fire ring annulus only
- Fumes rise from the top edge of the ring, past the plate on both sides,
  never across it
- Fireball cursor is HIDDEN while the hand is over the active bin
- Cursor reappears when the hand leaves

### Emitter handoff
Exactly ONE fluid emitter exists at any time: either the cursor or one bin
ring. Never two. This keeps GPU cost flat and is also the correct visual
(the cursor collapses into the bin edge, the bin ignites).

---

## 7. Plate content never changes

Name + rate, always, in every state.

Do NOT swap the rate for a live weight/cost readout on activation. The cart is
the diner's record. Activation is signalled by fire and by the cart row
filling in — not by plate text changing.

If existing code has a live-readout branch on the plate, remove it.

---

## 8. Cart and info box

Both live in the centre column, between the bin pairs (~549px wide).

- Cart is FIXED SIZE, never grows. 8 row SLOTS reserved from startup, by
  vertical position — not one slot per bin.
- Slots are blank at startup. No name, no placeholder text, no icon, no
  border. Just reserved empty space, same 44px height as a filled row.
- Slots fill in PICK ORDER, top to bottom, not bin order. The first bin
  touched claims slot 1, whichever bin that is. Second distinct bin picked
  claims slot 2. And so on.
- Once a slot is bound to a bin it stays bound. If that bin's weight changes
  again later (more taken, or put back), the SAME slot updates in place —
  it never creates a second row and never moves.
- Total sits at a fixed position and never moves, from 0 picks to 8.
- Cart width ~460px, row height 44px, whether blank or filled.
- Two buttons below the cart: Confirm, Cancel. Inactive for now — placeholder
  only, behaviour and styling TBD.

Info box sits ABOVE the cart, fixed height, does not push the cart down.
- Idle: invisible. No fill, no border. Not an empty bordered box.
- Active: fill + border + text fade in
- Shows veg/non-veg, kcal, short description for the active bin

Cart never moves, never animates, never gets obstructed. It is the diner's
only receipt until an order-finalisation step exists.

---

## 9. Implementation order

One commit per step. Verify each on the PROJECTED SURFACE, not a framebuffer
screenshot, before moving on.

1. **Repaint background and bins.** Table `#E8E6E1`, bins `#FFFFFF`.
   Verify: food in the bins is clearly visible with room lights off.
2. **Retype the plates.** New sizes and colours. Fixed `PLATE_H` for all 8 so
   the bins line up. Add `shortLabel` to the ingredient catalogue and use it.
   Verify: all 8 plates same height, longest name does not wrap or clip.
3. **Blending bench test.** Render a static coral rect over the light
   background under `OF_BLENDMODE_MULTIPLY` and again under
   `OF_BLENDMODE_ALPHA`. Report which reads better projected. Do not proceed
   until one is chosen.
4. **Idle halo.** All 8 bins, nested strokes, breathing, phase-offset.
   Verify: projected halos are visible and do not look synchronised.
5. **Layer reorder.** Restructure draw into the 5 layers above.
   Verify: existing fireball cursor is now occluded by the bin rects and
   cannot appear inside any bin interior.
6. **Fire ring.** Emit into the annulus on activation. Crossfade halo out.
   Verify: fire is confined to the ring, bin interior stays white.
7. **Emitter handoff.** Hide cursor over the active bin, hand emission to the
   ring, restore on exit.
   Verify: only one emitter visibly active at any moment.
8. **Fumes.** Rise from the ring top edge, routed around the plate.
   Verify: plate text is never crossed.
9. **Cart panel.** Fixed 8 rows, empty state, total pinned.
   Verify: total does not move between 0 picks and 8 picks.
10. **Info box.** Fixed height, invisible when idle, fades in on activation.
    Verify: cart does not shift when the info box appears.

---

## 10. What NOT to do

- Do not put fire inside a bin interior, in any mode
- Do not use additive blending for fire on the light background
- Do not extend the halo around the plate — bin only
- Do not add a live weight/cost readout to the plate
- Do not let the cart grow, scroll, animate, or move
- Do not show a bin's name in the cart before that bin has been picked
- Do not assign cart slots by bin position — assign by pick order
- Do not add a keystone warp in software
- Do not run fluid simulation on idle bins — halo is vector only
- Do not run two fluid emitters at once
- Do not add obstacle geometry to the fluid sim — draw order handles occlusion
- Do not change the calibrated bin rects to fix a visual problem; they are
  functional geometry shared with the classifier
- Do not use `ofSetLineWidth()` for halo strokes
- Do not verify by framebuffer screenshot; verify on the projected surface
