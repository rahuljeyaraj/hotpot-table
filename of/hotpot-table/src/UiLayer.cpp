#include "UiLayer.h"
#include "TableGeometry.h"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdio>
#include <sstream>

namespace {
	const char * kTag = "UiLayer";

	// Doc §13.4: "Load each font at its final display size... projected
	// text at 3x scale is mud." Inter (the doc's specified `en` face) is not
	// present anywhere in this repo or the oF distribution — checked both
	// before reaching for a substitute, not assumed absent. DejaVuSans-Bold
	// already ships in bin/data/fonts/ for the legacy bin labels this
	// rewrite deletes, and it already satisfies the harder rule underneath
	// the font choice ("dark ink on light field, set bold" — doc §13.4).
	// Swap this one line once the real font file exists.
	const std::string kFontFile = "fonts/DejaVuSans-Bold.ttf";

	// 2026-08-14, rig photo: the plate rate line was VISUAL_LAYER.md's
	// "regular" weight, but this repo only ever had DejaVuSans-Bold, so it
	// drew bold-on-bold and the doc's own weight distinction never showed
	// up. DejaVuSansMono is the same font family (Bitstream Vera/DejaVu
	// license, permissive — the same terms as kFontFile above, already
	// vendored in this repo) and gives two things at once: a genuine
	// regular weight, and monospace digits so a price line's width doesn't
	// twitch as the digits change on a pick (developer request, same
	// session). Copied from this machine's matplotlib install
	// (mpl-data/fonts/ttf/DejaVuSansMono.ttf) into bin/data/fonts/ — same
	// vendoring precedent as kFontFile itself.
	const std::string kMonoFontFile = "fonts/DejaVuSansMono.ttf";

	// Doc §13.4 fixed these at 36px and 26px, corrected once already
	// (2026-08-11) to 28px/22px after the catalogue names of the day
	// mostly didn't fit at 36px. Still used for the banner headline/
	// subline and the M5 widget label — see kPlateNamePx/kPlateRatePx
	// below for the bin plate's own, VISUAL_LAYER.md-specified sizes.
	const int kNamePx = 28;
	const int kDetailPx = 22;

	// VISUAL_LAYER.md section 3's palette named 40px for the plate name.
	// **2026-08-14, corrected the same day from a real rig photo**: at
	// 40px DejaVuSans-Bold, catalogue names overflowed a 200mm bin (252px)
	// and ran into the paired bin's own name — "Noodles" and "Wheat
	// Noodles" merged into one unreadable run. 28px is where every one of
	// the catalogue's real display names either fits on one line or wraps
	// cleanly to two (measured against the real font and the real
	// catalogue, PIL/FreeType, not eyeballed) — see drawBin's wrap call
	// below. **§3's palette table is corrected to match.**
	//
	// A same-day `shortLabel` catalogue field (core/pricing.py,
	// data/catalogue.json) briefly existed so this could stay one line
	// with no wrap at all — **deleted the same day, developer instruction:
	// "remove the short label idea... show the original label, max 2
	// lines."** The catalogue's `names` field is the single source again;
	// core/main.py's `_bin_msg` sends the full display name and oF wraps
	// it here.
	const int kPlateNamePx = 28;
	// 2026-08-14, second rig photo: at the doc's 26px, DejaVuSansMono's
	// ink height (25px, measured) was actually TALLER than the 28px bold
	// name's (21px) — a mono font's cap-height runs bigger relative to its
	// nominal size than a proportional face's, so the "smaller" number was
	// the visually bigger line. Developer: "make it smaller." Dropped to
	// 18px (measured ink height 18px, 86% of the name's — clearly
	// secondary now, still legible up close where a diner reads this
	// line). Re-run the same PIL/FreeType measurement rather than
	// re-guessing if either face or size changes again.
	// 2026-08-14, developer's own follow-up call, unmeasured: 20px.
	const int kPlateRatePx = 20;
	// #2B2118, VISUAL_LAYER.md section 3's palette table exactly.
	const ofColor kPlateNameColor(43, 33, 24);
	// 2026-08-14, second rig photo: the doc's #B8781A amber read as RED on
	// the projector, not yellow/gold — high enough red-channel share
	// (184:120:26) that a warm projector white balance pushed it further
	// that way (this exact rig's camera has needed repeated yellow-cast
	// fixes — see CLAUDE.md's M4h/M4p). Developer tried a mid green
	// (#6AA84F) then a blue (#0f26b8) in this same session; both
	// superseded, same day, by an orange, #E67E22 — the developer's own
	// call each time, none yet confirmed by a rig photo. Deliberately NOT
	// tied to the doc's Halo-idle entry, which still lists the old amber —
	// halo is unbuilt (build item 4) and has no rig evidence of its own.
	const ofColor kPlateRateColor(0xE6, 0x7E, 0x22);

	// VISUAL_LAYER.md section 4: "plateRect = fixed height PLATE_H (start
	// at 130px)... Halo wraps the BIN ONLY, never the plate" — this app has
	// no halo or fire geometry yet (that's build items 4/6), so nothing
	// reads this constant back out today. Recorded now, at the step that
	// pins the font sizes it has to fit, and checked once at setup() below
	// against the actual loaded metrics rather than left as an unverified
	// guess for whichever later step is the first to consume it.
	// 2026-08-14: the doc's starting 130px genuinely doesn't hold once the
	// name is allowed to wrap to 2 lines (max 2 lines, developer
	// instruction) — setup()'s own check measured the real worst case at
	// ~133px. Bumped to 140px for headroom rather than left to warn on
	// every boot; whoever picks up build item 4/6 should re-measure
	// against the actual halo/fire geometry once it exists, not trust
	// this number blindly either.
	const float kPlateHPx = 140.0f;

	// --- VISUAL_LAYER.md §4/§6, build item 4: the idle halo -----------------
	// 2026-08-14, first rig photo: the original 16-ring, 2.5px-pitch,
	// 1.5px-thick version (gapped bands, margin starting at the doc's own
	// 20px) read as a faint, noisy smudge rather than a halo — the old
	// plate ring (now removed, see drawBin) was also up in the same photo
	// and visually dominated it, and the gaps between bands likely added
	// noise of their own on top. Retuned, still unconfirmed by a second
	// photo: CONTIGUOUS bands (thickness == pitch, no gap — a smooth
	// gradient instead of 16 separate slivers) starting closer to the bin
	// (14px, now that there is no ring to clear first) and a brighter
	// floor on the breathing sine so it never dims toward invisible.
	// "haloRect = binRect inflated by HALO_MARGIN" — 14px now, was 20.
	const float kHaloMarginPx = 14.0f;
	// "~16 nested ofPath rounded-rect strokes, each 2-3px further out" —
	// 24 rings at 1.5px pitch (== thickness, contiguous) instead, for a
	// smoother gradient over roughly the same total span (36px vs. 40px).
	const int kHaloRingCount = 24;
	const float kHaloRingPitchPx = 1.5f;
	const float kHaloRingThicknessPx = 1.5f;
	// §3's palette originally: "Halo — idle #B8781A." Two corrections since
	// (both on this rig's own projected evidence, not guessed): #B8781A
	// projected as muddy brown (the same failure the plate rate's own
	// colour hit on this identical hex); the next attempt, #FFC800, was
	// "improved... but now it is orange shade" (developer). Green pushed
	// higher again, closer to red, for #FFEB00 (255,235,0) — near the top
	// of what still reads as "amber/gold" rather than a flat CSS yellow,
	// but each step so far has needed to go brighter/greener than felt
	// necessary off-projector to land where it should on this rig's
	// warm-shifted white balance. §3 is not updated to match yet — see
	// this file's other "doc still lists the original" notes; sync it
	// once a photo confirms this lands right rather than before.
	const ofColor kHaloIdleColor(0xFF, 0xEB, 0x00);
	// "Slow breathing sine on alpha." No period is given in the doc; 3s is
	// a reasoned starting guess (slow enough to read as breathing, not a
	// strobe), unmeasured, tunable once seen projected. The floor was
	// raised from the first attempt's 0.1 to 0.35 (drawHalo's own formula)
	// so a bin never reads as fully faded out mid-breath — the first photo
	// looked weak partly because it likely caught several bins near their
	// low point at once.
	const float kHaloBreathPeriodS = 3.0f;
	// Geometric note, not yet checked against a photograph: the halo's own
	// outward reach (14px to 14+24*1.5=50px from the bin edge) is not
	// small next to how close the plate's rate line sits to the bin on
	// this same axis (drawBin's ringTop/ringBottom, roughly 19px out
	// before the rate line's own clearance/ascender stack further beyond
	// it) — the two were tuned independently and may turn out to overlap
	// on the near/far axis once both are on the projected table at once.
	// Doc §4 says "Halo wraps the BIN ONLY, never the plate"; if a photo
	// shows the halo reaching into the plate's own text, the fix is here
	// (kHaloMarginPx or the ring span), not in drawBin's clearance, which
	// several rig photos have already tuned for other reasons.

	// --- VISUAL_LAYER.md §4/§6, build item 6: the active fire ring ---------
	// "fireRect = binRect inflated by FIRE_RING (start at 52px)." Inner
	// edge matches the halo's own margin on purpose — the fire ring picks
	// up right where the halo's innermost band sits, so the crossfade
	// (drawHalo's fireFade, fireEmitters()'s intensity — the same spring)
	// never leaves a visible gap or a double-covered sliver between the two
	// as one fades and the other fades in.
	const float kFireRingInnerPx = kHaloMarginPx;
	const float kFireRingOuterPx = 52.0f;

	// doc's old kBinOutlineMM/kLabelClearanceMM/kLabelLineGapMM (ofApp.cpp,
	// now deleted). Redefined here rather than resurrected in
	// TableGeometry.h, which v3 §7.1 keeps for CAD geometry only.
	const float kLabelClearanceMM = 10.0f;
	const float kLabelLineGapMM = 4.0f;

	// Formerly the plate ring's own width — the band of colour that framed
	// a bin's cutout and carried doc §4.3's `hl` state (I8 — "distinguish
	// states by hue"). **2026-08-14: that ring is deleted outright**, now
	// that the idle halo occupies the same visual role (see drawBin's own
	// comment) — VISUAL_LAYER.md's palette (§3) never listed this grey/
	// green ring at all, only the halo/fire pair. Kept as a named constant
	// purely because drawBin's label positions still measure their
	// clearance from where the ring's outer edge used to sit (see
	// ringRestY there) — removing the ring shouldn't also pull every label
	// closer to the cutout as a side effect nobody asked for.
	const float kRingMM = 6.0f;

	// doc §13.4: "Dark ink on a light field, and set bold" — the field is
	// near-white by construction (I9's white floor), so text has to win on
	// stroke weight, not brightness. Near-black rather than pure 0,0,0: full
	// black on this bold a face at these sizes reads harsher than the doc's
	// own comparison point (dark plates on the pre-rewrite app).
	const ofColor kInkColor(20, 20, 20);

	// M2 doc §21 acceptance test's "fault overlay" (`state.overlay.kind ==
	// "error"`, set by core/main.py's _overlay_msg when a bin that was
	// billing off real weight goes dark — doc §9.5's "no billing occurs
	// from the frozen reading"). Reuses the staff view's own fault palette
	// (web/static/index.html's --red #e05d5d, and the dark-red-on-red ink
	// its red pip already uses, #2a0000) so the same failure reads the same
	// way on both surfaces instead of inventing a second "red" for this
	// table.
	const ofColor kErrorBannerFill(224, 93, 93);   // #e05d5d
	const ofColor kErrorBannerInk(42, 0, 0);       // #2a0000

	// M2.6: setting mode's banner (doc §14.5, "a persistent banner strip
	// along the top edge"). Amber for the same reason the error banner is
	// red — it is the staff view's own --amber (#e8b33d) and the ink its
	// amber pip already uses (#2a1f00), so the header chip on the tablet
	// and the strip on the table are visibly the same statement. I8: modes
	// are distinguished by HUE, never by brightness, and this hue is
	// luminance-matched to the red one rather than being brighter or
	// dimmer than it.
	//
	// The rest of doc §14.5 — fluid off, amber chrome throughout, the
	// 100mm grid — stays M8 build item 6. Most of it is a no-op today
	// since no fluid exists. The banner alone is what makes this
	// milestone's acceptance test visible from three metres.
	const ofColor kSettingBannerFill(232, 179, 61);   // #e8b33d
	const ofColor kSettingBannerInk(42, 31, 0);       // #2a1f00

	// M4 build item 6: `overlay.kind == "uncalibrated"` (doc §9.1's
	// first-boot state). A THIRD hue rather than reusing amber or red,
	// because I8 distinguishes states by hue and this is a genuinely
	// different state from both: it is not a fault in a subsystem (red)
	// and it is not staff working on a table that is otherwise fine
	// (amber) — it is a table that has never been set up at all.
	//
	// Violet, and specifically NOT green/blue: the staff view's pips
	// already own green for health, and a cool blue on a near-white field
	// reads as "off" rather than as a state. #7c5cd6 against the same
	// dark ink pattern the other two use, and luminance-matched to them
	// by eye against the same field (I8's "or a state change reads as the
	// table brightening rather than as the state changing").
	const ofColor kUncalBannerFill(124, 92, 214);     // #7c5cd6
	const ofColor kUncalBannerInk(20, 8, 48);         // #140830

	// The banner panel. Height is in px, not mm, because it is sized to
	// the two font sizes it holds rather than to anything physical.
	// The inset is the breathing room from the pot-gap edges — see
	// drawBanner for why the panel lives in that gap at all.
	const float kBannerHeightPx = 104.0f;
	const float kBannerInsetMM = 10.0f;

	// The brand mark sits ABOVE the banner in the same centre column,
	// never sharing its strip — see drawBrandMark and drawBanner's yTop.
	// Height is developer-tuned (not a doc value); top margin is
	// clearance from the table's far edge; the gap is breathing room
	// between the mark's bottom and the banner's top when both are up.
	const float kBrandHeightPx = 170.0f;
	const float kBrandTopMarginPx = 20.0f;
	const float kBrandBannerGapPx = 24.0f;

	// --- VISUAL_LAYER.md §8/§9, build item 9: the cart panel ----------------
	// Lives in the same centre column as the brand mark and the mode
	// banner (drawBrandMark/drawBanner's own gapLeftMM/gapRightMM — the
	// pot gap, the one horizontal span with no bin in it), stacked below
	// both. Doc: "Cart width ~460px, row height 44px... Total sits at a
	// fixed position and never moves."
	//
	// **2026-08-24, first rig look: the white panel fill and its 2px
	// border are GONE** — developer: "cart now have a white background, it
	// is not needed, it should remain like all other text written in the
	// table." That is doc §4's own rule for the plate ("Plate has no fill
	// and no border. Text sits directly on the table background") applied
	// to the cart, which the §3 palette rows for "Cart panel fill"/"Cart
	// border" contradicted. `kCartPanelFill` is deleted outright rather
	// than left dormant; `kCartBorderColor` survives because the divider
	// above the total (its own §3 row) still uses it — that one is a rule
	// in a receipt, not a container around it.
	const ofColor kCartBorderColor(0xC9, 0xC5, 0xBC);    // #C9C5BC
	const ofColor kCartRowDetailColor(0x6E, 0x6A, 0x62); // #6E6A62
	// #B8781A — the doc's original "Total value" hex. Deliberately NOT
	// reusing kPlateRateColor: that constant has already been corrected
	// twice this session (green, then blue, then orange) chasing how
	// amber/gold reads on THIS rig's projector, and none of those photos
	// were of this hex in this position. Keeping the total's colour
	// independent means a future correction to one does not silently
	// drag the other along for a reason nobody checked.
	const ofColor kCartTotalValueColor(0xB8, 0x78, 0x1A);
	// §3's palette says 26px for both cart-row columns. **Corrected to
	// 22px, 2026-08-24, from the developer's first look at a filled cart:
	// "the full name of item is not coming, that is unacceptable."** At
	// 26px the widest catalogue name ("Button Mushrooms") measures 279px
	// and the detail column's own worst case ("500g  $17.50") 191px — 486px
	// of content against the doc's own 460px panel, so every long name hit
	// truncateToWidth below and lost its tail. Measured, not guessed (the
	// same PIL/FreeType script against the same real .ttf and the real
	// catalogue that fixed the plate name's own overflow earlier this
	// month): at 22px those are 238px and 160px, which fit inside a 500px
	// panel with 46px to spare. Both numbers moved — the size DOWN and the
	// width UP — because either alone was marginal. §3 is not edited to
	// match yet, same "flag it, confirm on a photo first" rule this file
	// already uses for the halo's own colour.
	const int kCartRowPx = 22;
	const float kCartWidthPx = 500.0f;
	const float kCartRowHeightPx = 44.0f;
	const float kCartBorderWidthPx = 2.0f;   // filled bars, not ofSetLineWidth
	                                          // — see the halo's own comment
	                                          // above on why a stroke width
	                                          // is unusable on this rig.
	const float kCartPadXPx = 20.0f;
	const float kCartRowMidGapPx = 16.0f;    // name column <-> detail column
	const float kCartDividerGapPx = 12.0f;   // rows -> divider -> total

	// --- VISUAL_LAYER.md §8, build item 10: the info box -------------------
	// "Info box sits ABOVE the cart, fixed height, does not push the cart
	// down." Fixed height is the whole mechanism: the space is reserved
	// from boot whether or not a bin is active, so the cart's own top
	// (kCartTopPx below) is a constant and cannot move when the box fades
	// in. Sized to three 24px lines (§3's "Info box text") plus padding —
	// one header line (veg/non-veg + kcal) and up to two description lines.
	//
	// 2026-08-24, developer: "there is no more space to show info box above
	// the cart. so the cart and the buttons should be pulled down." That is
	// what this block does — everything below it shifts by
	// kInfoBoxHeightPx + kInfoBoxCartGapPx.
	const float kInfoBoxTopPx = kBrandTopMarginPx + kBrandHeightPx
		+ kBrandBannerGapPx + kBannerHeightPx + 24.0f;
	const float kInfoBoxHeightPx = 136.0f;
	const float kInfoBoxCartGapPx = 20.0f;
	const int kInfoBoxTextPx = 24;
	const float kInfoBoxPadXPx = 18.0f;
	const float kInfoBoxPadYPx = 14.0f;
	const float kInfoBoxLineGapPx = 6.0f;
	const float kInfoBoxBorderWidthPx = 2.0f;
	const ofColor kInfoBoxFill(0xF7, 0xE4, 0xDC);        // §3 "Info box fill"
	const ofColor kInfoBoxBorderColor(0xC7, 0x4A, 0x34); // §3 "Info box border"
	const ofColor kInfoBoxTextColor(0x8A, 0x35, 0x24);   // §3 "Info box text"
	// The veg/non-veg dot. Green and red are the same two the cart's own
	// buttons use (kWidgetPrimary/kWidgetDanger) rather than a third
	// pair — one green and one red on this table, not several. Egg is
	// neither, and gets its own amber rather than being rounded into one
	// of them; see `pricing.VALID_DIETS`' own comment for why the wire
	// carries three values and not two.
	const ofColor kInfoDietEggColor(0xD9, 0x82, 0x2B);
	const float kInfoDietDotRadiusPx = 9.0f;
	const float kInfoDietDotGapPx = 12.0f;

	// Fixed, never a function of whether the mode banner happens to be
	// showing — doc §8's "never moves" applies as much to appearing as it
	// does to growing, so this sits below the banner's own footprint
	// (kBannerHeightPx) whether or not drawTopBanner actually draws one
	// this frame, and below the info box's reserved band whether or not a
	// bin is active.
	const float kCartTopPx = kInfoBoxTopPx + kInfoBoxHeightPx + kInfoBoxCartGapPx;

	// --- M5: the pointer cursor and the dwell ring ------------------------
	// Sizes in px because they are screen furniture, not table geometry —
	// nothing about a cursor is measured in millimetres of plywood. The
	// numbers are set against the one thing that does matter physically:
	// a hand is not a mouse, so the cursor has to be visible under a hand
	// that is partly covering it, from three metres, on a near-white field.
	const float kCursorDotRadius = 13.0f;    // the solid centre
	const float kCursorRingInner = 22.0f;    // thin ring around it, so the
	const float kCursorRingOuter = 28.0f;    // dot reads even over a label
	// The dwell ring sits OUTSIDE the cursor's own ring rather than
	// replacing it: the cursor must not change shape as the ring fills, or
	// the diner reads it as the cursor breaking rather than as progress.
	const float kDwellRingInner = 36.0f;
	const float kDwellRingOuter = 52.0f;

	// I8: hue at full chroma, never brightness — the field is near-white by
	// requirement (I9) so a "dimmer" cursor would read as a rendering
	// fault. Dark ink for the cursor itself (doc §13.4's rule for anything
	// that has to be read on a light field) and the `picking` amber from
	// highlightColour() for the dwell sweep, so a filling ring on the table
	// is the same hue as a bin mid-pick.
	const ofColor kCursorColor(24, 24, 24);
	const ofColor kDwellTrackColor(150, 150, 150);
	const ofColor kDwellFillColor(200, 120, 0);

	// --- RIG_FEEDBACK item 11 diagnostic: the raw skeleton ----------------
	// Deliberately NOT reusing kCursorColor — this must read as a different
	// thing from the real cursor at a glance, since the whole point is
	// telling the two apart on the same table. Same lime/gold pairing the
	// staff view's Developer tab already uses for this
	// (index.html's drawLandmarks: "rgba(64,200,120,0.8)" lines,
	// "#4ee08a" joints, "#ffd93c" the tracked landmark) so a person who has
	// looked at that view recognises this one.
	const ofColor kSkeletonLineColor(64, 200, 120, 200);
	const ofColor kSkeletonJointColor(78, 224, 138);
	const ofColor kSkeletonTrackedColor(255, 217, 60);
	const float kSkeletonJointRadius = 4.0f;
	const float kSkeletonTrackedRadius = 7.0f;
	const float kSkeletonLineWidth = 2.0f;
	// backend_mediapipe.py's own CURSOR_LANDMARK (index 8) — the tracked
	// point `_to_stage` builds the real cursor from — drawn larger, same
	// as the Developer tab's own CURSOR_LANDMARK highlight.
	const int kSkeletonCursorLandmark = 8;
	// Standard 21-point hand topology, byte-for-byte the same pairs as
	// index.html's HAND_CONNECTIONS — kept identical on purpose so the two
	// views draw the same skeleton shape.
	const int kSkeletonConnections[][2] = {
		{0, 1}, {1, 2}, {2, 3}, {3, 4},
		{0, 5}, {5, 6}, {6, 7}, {7, 8},
		{5, 9}, {9, 10}, {10, 11}, {11, 12},
		{9, 13}, {13, 14}, {14, 15}, {15, 16},
		{13, 17}, {17, 18}, {18, 19}, {19, 20},
		{0, 17},
	};

	// --- M5: the projected buttons ----------------------------------------
	// A button is drawn the same way a plate is — a filled rect ring with
	// the label inside it — so the two read as one system rather than as a
	// UI pasted onto a table. 5mm rather than the plate's 6mm because a
	// button's ring encloses text rather than a physical hole and a heavier
	// frame starts to compete with its own label.
	const float kWidgetRingMM = 5.0f;
	const ofColor kWidgetPrimary(0, 115, 0);      // the `picked` green, equiluminant
	const ofColor kWidgetSecondary(98, 98, 98);
	// **2026-08-24, developer, on the cart's own Confirm/Cancel pair:
	// "confirm and cancell button looks washed out and i think it need
	// green and red colour respectively."** They were drawn in
	// kWidgetDisabled (190,190,190) — light grey on a near-white field,
	// which is exactly the "washed out" report and was itself deliberate
	// (doc §8: "Inactive for now — placeholder only"). They are real
	// dwell targets now (core/hover.py's own widget set), so the disabled
	// grey is wrong twice over.
	//
	// Green is kWidgetPrimary above, unchanged — already chosen as
	// equiluminant with the greys per I8 ("distinguish states by hue,
	// never by brightness"). Red is a DEEP red, not the error banner's
	// #e05d5d: that one is a FILL with dark ink on top, where this is ink
	// on the near-white table and a light red would read as pink and lose
	// the same contrast the grey just lost. #C0392B is luminance-close to
	// kWidgetPrimary and shares a family with §3's own fire-core #C74A34.
	const ofColor kWidgetDanger(0xC0, 0x39, 0x2B);
	const ofColor kWidgetDisabled(190, 190, 190);
	// The dwell sweep, drawn INSIDE a widget as a rising fill (see
	// drawWidget). Same amber as the cursor's own dwell ring
	// (kDwellFillColor) so a filling button and a filling ring read as one
	// mechanism, at the low alpha a tint under dark text has to keep.
	const ofColor kWidgetDwellFill(200, 120, 0, 70);

	void drawCentered(const ofTrueTypeFont & font, const std::string & text,
		float cx, float baselineY){
		if(text.empty() || !font.isLoaded()){
			return;
		}
		ofRectangle bb = font.getStringBoundingBox(text, 0, 0);
		font.drawString(text, cx - bb.width * 0.5f - bb.x, baselineY);
	}

	// VISUAL_LAYER.md §8: a cart row is ONE fixed 44px line, unlike the
	// plate's own name which is allowed to wrap to 2 (wrapNameToTwoLines,
	// above) — wrapping here would break the "same 44px height as a
	// filled row" promise every slot makes. Truncated character-by-
	// character (not word-by-word like the wrap helper) so a single very
	// long word still yields something rather than overflowing whole.
	std::string truncateToWidth(const ofTrueTypeFont & font, const std::string & text,
		float maxWidthPx){
		if(!font.isLoaded() || font.getStringBoundingBox(text, 0, 0).width <= maxWidthPx){
			return text;
		}
		const std::string ellipsis = "...";
		std::string result = text;
		while(!result.empty()){
			result.pop_back();
			std::string candidate = result + ellipsis;
			if(font.getStringBoundingBox(candidate, 0, 0).width <= maxWidthPx){
				return candidate;
			}
		}
		return ellipsis;
	}

	// The cart panel's border and the divider above its total (doc §3:
	// "2px #C9C5BC" for both) are drawn as filled bars, never
	// ofSetLineWidth/ofPath stroke — this file's own halo comment already
	// found that stroke width is driver-capped at 1px and ignored outright
	// on the programmable renderer on this rig; a hairline "2px" border
	// that silently draws at 1px is exactly the kind of unverified-until-
	// photographed mismatch this session keeps flagging rather than risking
	// again here.
	void drawRectBorder(const ofRectangle & r, float thicknessPx, const ofColor & colour){
		ofSetColor(colour);
		ofDrawRectangle(r.x, r.y, r.width, thicknessPx);
		ofDrawRectangle(r.x, r.y + r.height - thicknessPx, r.width, thicknessPx);
		ofDrawRectangle(r.x, r.y, thicknessPx, r.height);
		ofDrawRectangle(r.x + r.width - thicknessPx, r.y, thicknessPx, r.height);
	}

	// Bin item names (e.g. "Button Mushrooms", "Lotus Root Slices") can
	// render wider than a 200mm bin (252px) at kPlateNamePx — that
	// overflowed into the neighbour's label before this existed (a
	// 2026-08-14 rig photo). 2026-08-14, reinstated the same day after a
	// same-day `shortLabel` detour was deleted on developer instruction
	// ("show the original label, max 2 lines") — this is the mechanism
	// that makes "max 2 lines" true. Greedy word-wrap to at most 2 lines
	// instead of shrinking the font, which would abandon the measured
	// kPlateNamePx. A single word wider than maxWidthPx on its own is
	// still returned whole — this never breaks mid-word, matching how
	// nothing else in this file does character-level layout.
	std::vector<std::string> wrapNameToTwoLines(const ofTrueTypeFont & font,
		const std::string & text, float maxWidthPx){
		if(!font.isLoaded() || font.getStringBoundingBox(text, 0, 0).width <= maxWidthPx){
			return {text};
		}
		std::vector<std::string> words;
		std::istringstream iss(text);
		std::string w;
		while(iss >> w){
			words.push_back(w);
		}
		if(words.empty()){
			return {text};
		}
		std::string line1;
		size_t i = 0;
		for(; i < words.size(); i++){
			std::string candidate = line1.empty() ? words[i] : line1 + " " + words[i];
			if(!line1.empty() && font.getStringBoundingBox(candidate, 0, 0).width > maxWidthPx){
				break;
			}
			line1 = candidate;
		}
		if(line1.empty()){
			line1 = words[i++];   // one overlong word — take it anyway, never emit an empty line
		}
		std::string line2;
		for(; i < words.size(); i++){
			line2 = line2.empty() ? words[i] : line2 + " " + words[i];
		}
		if(line2.empty()){
			return {line1};
		}
		return {line1, line2};
	}

	// Pulls the currency symbol and decimal count out of core's already-
	// resolved `total.text` (e.g. "\xE2\x82\xB9""41.20") instead of oF
	// hardcoding either. Doc I2: "oF does no lookup" — this is not a
	// lookup, it is reusing the one locale-resolved string the wire
	// already gives oF, so the same prefix can dress up the per-bin price
	// line below, which core sends as a bare number with no text of its
	// own to borrow from.
	void splitCurrencyText(const std::string & text, std::string & prefix, int & decimals){
		size_t i = 0;
		while(i < text.size() && !(std::isdigit((unsigned char)text[i]) || text[i] == '-')){
			i++;
		}
		prefix = text.substr(0, i);
		size_t dot = text.find('.', i);
		decimals = (dot == std::string::npos) ? 0 : (int)(text.size() - dot - 1);
	}

	std::string formatCurrency(double amount, const std::string & prefix, int decimals){
		char buf[32];
		snprintf(buf, sizeof(buf), "%.*f", decimals, amount);
		return prefix + buf;
	}
}

namespace {
	// The plain font.load(file, size) overload only requests
	// ofUnicode::Latin — ASCII 32-127 — so ₹ (U+20B9) silently has no glyph
	// and drawString skips it, even though core sends it correctly (doc
	// §17.2 currency text is "<symbol><amount>"). Latin1Supplement adds the
	// yen/yuan sign and other Latin-1 symbols for the same reason, cheaply,
	// ahead of any locale actually needing them.
	bool loadUiFont(ofTrueTypeFont & font, const std::string & file, int size){
		ofTrueTypeFontSettings settings(file, size);
		settings.ranges = {ofUnicode::Latin1Supplement, ofUnicode::CurrencySymbols};
		return font.load(settings);
	}
}

void UiLayer::setup(){
	bool ok = true;
	ok = loadUiFont(_nameFont, kFontFile, kNamePx) && ok;
	ok = loadUiFont(_detailFont, kFontFile, kDetailPx) && ok;
	ok = loadUiFont(_plateNameFont, kFontFile, kPlateNamePx) && ok;
	ok = loadUiFont(_plateRateFont, kMonoFontFile, kPlateRatePx) && ok;
	// VISUAL_LAYER.md §3: "Total value" 48px bold / "Total label" 30px —
	// was 80/28 (the pre-cart free-standing numeral's own sizes) until
	// build item 9 folded the total into the cart footer's single
	// receipt-style line (drawCart/drawTotal).
	ok = loadUiFont(_totalNumFont, kFontFile, 48) && ok;
	ok = loadUiFont(_totalLabelFont, kFontFile, 30) && ok;
	ok = loadUiFont(_cartRowFont, kFontFile, kCartRowPx) && ok;
	ok = loadUiFont(_infoFont, kFontFile, kInfoBoxTextPx) && ok;
	ok = loadUiFont(_devFont, kFontFile, 16) && ok;
	_fontsLoaded = ok;
	if(!_fontsLoaded){
		ofLogError(kTag) << "could not load " << kFontFile << " or " << kMonoFontFile
			<< " at one or more sizes — labels will not draw";
	}

	// VISUAL_LAYER.md §4's PLATE_H budget, checked once against what the
	// two fonts above actually measure — the WORST case (name wrapped to
	// its full 2 lines, drawBin's own cap), the rate row, the same
	// clearance/gap the far/near rows use in drawBin. Approximates the
	// 2-line name block as 2x line height rather than doing drawBin's
	// exact ascender/descender walk — slightly conservative (safe
	// direction for a warn-if-over check), and simpler than duplicating
	// that per-line math here. Not a hard clip (nothing here can shrink a
	// font that already loaded, or pre-wrap a string with no state to
	// wrap); a warning is the honest amount of enforcement a startup
	// check can do for a per-frame draw call.
	if(_fontsLoaded){
		const float nameLineGap = 2.0f;   // matches drawBin's own constant
		const float worstCaseNameBlock = _plateNameFont.getLineHeight() * 2.0f + nameLineGap;
		const float measured =
			worstCaseNameBlock
			+ mmToPxY(kLabelLineGapMM)
			+ _plateRateFont.getAscenderHeight() + fabsf(_plateRateFont.getDescenderHeight())
			+ mmToPxY(kLabelClearanceMM);
		if(measured > kPlateHPx){
			ofLogWarning(kTag) << "plate label block (worst case, 2-line name) measures "
				<< measured << "px, over VISUAL_LAYER.md's " << kPlateHPx << "px PLATE_H budget";
		}
	}

	// The one check standing between this file's cart layout and
	// core/hover.py's Confirm/Cancel band, which are in two languages and
	// cannot share a constant. hover.py's `BUTTONS_TOP_PX` is mirrored
	// here as a literal on purpose — if either side moves and the other
	// does not, the buttons either collide with the total or float away
	// from the cart, and both are visible on the table but neither is
	// visible in a diff. A warning rather than a clamp: the rect that
	// actually gets drawn (and hit-tested) is core's, and oF quietly
	// moving a button core is still hit-testing elsewhere is the exact
	// failure this whole arrangement exists to prevent.
	if(_fontsLoaded){
		const float kHoverButtonsTopPx = 952.0f;   // core/hover.py BUTTONS_TOP_PX
		if(cartBottomPx() > kHoverButtonsTopPx){
			ofLogWarning(kTag) << "cart bottom measures " << cartBottomPx()
				<< "px, below core/hover.py's button band at " << kHoverButtonsTopPx
				<< "px — the Confirm/Cancel buttons will overlap the total";
		}
	}

	// Pre-cropped, background-already-transparent — see assets/logo/ in the
	// repo root for the source and how it was derived. "light" (dark ink,
	// near-white background) rather than "dark", matching this surface's
	// own hard invariant: the projected field stays above a white floor
	// (doc §2, CLAUDE.md's "never black, never coloured, never patterned").
	_brandLogoLoaded = _brandLogo.load("img/hotpottery-light-cropped.png");
	if(!_brandLogoLoaded){
		ofLogError(kTag) << "could not load img/hotpottery-light-cropped.png"
			<< " — no brand mark will draw";
	}

	// VISUAL_LAYER.md §6's "phase-offset by a per-bin random seed" went
	// through two revisions already (`ofRandom(TWO_PI)` per bin, then
	// evenly-spaced-plus-jitter — both replaced entirely now, see this
	// member's own comment in UiLayer.h). **2026-08-14, third revision,
	// developer's own design:** not staggered independent breathing at
	// all — one highlight ROTATING around each island's 2x2 bins.
	// TableGeometry.h's BINS table gives the physical layout: the LEFT
	// island is 0=TL, 1=TR, 5=BR, 4=BL (bins 0/1 are the far row's two
	// leftmost, 4/5 the near row's, same x columns). Developer's sequence
	// — "bin 0 starts, then 90 degrees bin 1, then 90 degrees bin 5,
	// then finally bin 4 after 90 degrees so 360" — is TL->TR->BR->BL,
	// clockwise around that island's own perimeter.
	//
	// The RIGHT island (2=TL, 3=TR, 7=BR, 6=BL) is the left island's
	// mirror image across the table's vertical centreline, and this
	// codebase already has a standing convention for that axis: bilateral
	// mirror symmetry about the pot gap, not identical absolute motion
	// (M2.6g's plate-label precedent — both rows read "ring ->
	// price/grams -> name" OUTWARD FROM THE POT, a mirror of each other,
	// not a copy). Applied here: the right island rotates the OPPOSITE
	// way, counter-clockwise (2 -> 6 -> 7 -> 3), so the two islands'
	// motion mirrors rather than matches — a call, not a certainty; if it
	// reads wrong on the table, swapping this island's middle two phases
	// (6 and 7) is the one-line undo to make both spin the same way.
	const float kQuarterTurn = HALF_PI;
	_haloPhase[0] = 0.0f;
	_haloPhase[1] = kQuarterTurn;
	_haloPhase[5] = kQuarterTurn * 2.0f;
	_haloPhase[4] = kQuarterTurn * 3.0f;

	_haloPhase[2] = 0.0f;
	_haloPhase[6] = kQuarterTurn;
	_haloPhase[7] = kQuarterTurn * 2.0f;
	_haloPhase[3] = kQuarterTurn * 3.0f;
}

ofRectangle UiLayer::cadBinRectPx(int i){
	const BinRect & b = BINS[i];
	float x = mmToPxX(b.xMM);
	float y = mmToPxY(b.yMM);
	return ofRectangle(x, y, mmToPxX(b.xMM + b.wMM) - x, mmToPxY(b.yMM + b.hMM) - y);
}

ofRectangle UiLayer::binRectPx(int i) const {
	// **kUseCoreRects was a deliberate kill-switch, OFF from 2026-08-12
	// through M4m; flipped back ON in M4n and now `_coreRects[i]` is the
	// PROJECTOR grid, not the old rect this switch was built to distrust.**
	//
	// The TRAP this switch guarded against was specific to the deleted
	// dot-calibration flow: a value computed by fitting dots in CAMERA
	// space, carried into stage space through a homography nobody had
	// re-verified in the space it actually lands — `geometry.calibrated:
	// true, rms_px: 0.0, n_points: 4`, a solve that LOOKS perfect while
	// pointing nowhere near the real trays (doc §5.3's TRAP, arriving
	// exactly as warned; see CLAUDE.md's M4h/M4i). Two things about that
	// no longer hold for what `_coreRects` carries now. First, dot
	// calibration is gone outright (CLAUDE.md's M4k) — nothing derives a
	// bin position from a homography and a marker fit any more. Second,
	// and load-bearing here: `bins[].rect` is `core/bin_grid.py`'s
	// PROJECTOR grid (M4n), which by design has no homography in its
	// chain at all — a human drags or nudges it while looking straight at
	// THIS space, the real light on the real table, not at a proxy for
	// it. "Core has a rect" and "core's rect is trustworthy" cannot come
	// apart the way they did for the old rect, because nothing here is
	// derived — every value core sends is a number a person put there by
	// looking at the effect directly, the doc §5.3 TRAP's own cure. The
	// CAD layout remains the fallback for the ordinary case this switch
	// was never about: no projector grid has been set yet at all.
	constexpr bool kUseCoreRects = true;
	if(kUseCoreRects && _hasCoreRect[i]){
		return _coreRects[i];
	}
	return cadBinRectPx(i);
}

ofRectangle UiLayer::cutoutRectPx(int i) const {
	// The bin grown by CUTOUT_MARGIN_MM on all four sides — the same
	// growth TableGeometry.h::binFillRectMM applies, but expressed here
	// in px so it works on a core-sent rect too (which has no mm form:
	// it came from a camera through a homography, not from the drawing).
	//
	// The margin absorbs the saw kerf on the real cutout and the
	// homography's residual error, and it is the safe direction: a patch
	// slightly too big spills white onto the table, one slightly too
	// small leaves a dark crescent on the food (I9).
	const ofRectangle b = binRectPx(i);
	const float mx = mmToPxX(CUTOUT_MARGIN_MM);
	const float my = mmToPxY(CUTOUT_MARGIN_MM);
	return ofRectangle(b.x - mx, b.y - my, b.width + 2.0f * mx, b.height + 2.0f * my);
}

std::vector<ofRectangle> UiLayer::cutoutRectsPx() const {
	// Built from the same cutoutRectPx() drawBin() frames its ring against,
	// so the ring and the light pass cannot drift apart — see UiLayer.h.
	std::vector<ofRectangle> out;
	out.reserve(BIN_COUNT);
	for(int i = 0; i < BIN_COUNT; i++){
		out.push_back(cutoutRectPx(i));
	}
	return out;
}

void UiLayer::drawRing(const ofRectangle & cut, float widthX, float widthY,
	const ofColor & colour, float cornerRadiusPx){
	if(cornerRadiusPx > 0.0f){
		// Rounded cutout (Stage's light pass now stamps one) needs a ring
		// that follows the same corner, or square bar corners would poke
		// out past a rounded hole. Two filled rounded-rect contours, ODD
		// winding, same "filled only" rule as the bars below and doc
		// §13.4's circular-ring annulus — the ring is the area between
		// them, not a stroke.
		const ofRectangle outer(cut.x - widthX, cut.y - widthY,
			cut.width + 2.0f * widthX, cut.height + 2.0f * widthY);
		const float widthAvg = 0.5f * (widthX + widthY);
		const float rOuter = std::min(cornerRadiusPx + widthAvg,
			std::min(outer.width, outer.height) * 0.5f);
		const float rInner = std::min(cornerRadiusPx,
			std::min(cut.width, cut.height) * 0.5f);
		ofPath path;
		path.setFilled(true);
		path.setFillColor(colour);
		path.setCircleResolution(24);
		path.setPolyWindingMode(OF_POLY_WINDING_ODD);
		path.rectRounded(outer, rOuter);
		path.rectRounded(cut, rInner);
		path.draw();
		return;
	}
	// Four filled bars, not a stroked path. VERIFIED in the installed oF
	// rather than assumed, because assuming is what put the ring under the
	// light pass in the first place: an unfilled ofPath is drawn by
	// ofGLRenderer::draw(const ofPath&), which calls
	// setLineWidth(shape.getStrokeWidth()) -> glLineWidth(). So
	// ofPath::setStrokeWidth() IS ofSetLineWidth(), the exact call doc
	// §13.4 says never to use because Mesa on Intel caps it at 1px — and
	// on the programmable renderer (which M8's fluid will force this app
	// onto) that glLineWidth call is commented out entirely, so the width
	// is ignored outright. §13.4 has been corrected to say so.
	//
	// Top and bottom span the full outer width so the corners are covered
	// once each; left and right fill only the gap between them. Nothing
	// overlaps, so this stays correct if the colour ever carries alpha.
	ofSetColor(colour);
	ofDrawRectangle(cut.x - widthX, cut.y - widthY,
		cut.width + 2.0f * widthX, widthY);                       // top
	ofDrawRectangle(cut.x - widthX, cut.y + cut.height,
		cut.width + 2.0f * widthX, widthY);                       // bottom
	ofDrawRectangle(cut.x - widthX, cut.y, widthX, cut.height);   // left
	ofDrawRectangle(cut.x + cut.width, cut.y, widthX, cut.height);// right
}

void UiLayer::drawAnnulus(float cx, float cy, float rOuter, float rInner,
	const ofColor & colour, float startDeg, float endDeg){
	// **A FILLED ofPath — outer arc, then an inner arcNegative.** Doc §13.4
	// spells this out and the reason is not style: an UNfilled ofPath is
	// drawn by ofGLRenderer::draw(const ofPath&), which calls
	// setLineWidth(shape.getStrokeWidth()) -> glLineWidth(). So
	// ofPath::setStrokeWidth() IS ofSetLineWidth(), which Mesa on Intel
	// (the ODYSSEY's driver family) caps at 1px — and on the programmable
	// renderer M8's fluid will force, that glLineWidth call is commented
	// out entirely and the width is ignored outright. A stroked ring would
	// therefore work on this dev machine today and become a hairline on
	// the deploy board, on the day the fluid lands, for reasons nobody
	// would connect.
	//
	// Two ofDrawCircle calls with the background punched through the middle
	// is the other tempting version and is also wrong: over M8's fluid
	// there is no background colour to punch with.
	if(endDeg <= startDeg || rOuter <= rInner){
		return;
	}
	ofPath path;
	path.setFilled(true);
	path.setFillColor(colour);
	path.setCircleResolution(96);   // 64 shows facets at this radius
	path.arc(cx, cy, rOuter, rOuter, startDeg, endDeg);
	path.arcNegative(cx, cy, rInner, rInner, endDeg, startDeg);
	path.close();
	path.draw();
}

void UiLayer::drawRoundedBand(const ofRectangle & base, float innerOffsetPx,
	float outerOffsetPx, const ofColor & colour, float baseCornerRadiusPx){
	// drawRing's rounded-corner branch, generalised: that one always starts
	// its inner contour at `base` itself (offset 0). This lets the inner
	// contour sit further out too, so drawHalo can nest many nested bands
	// around one bin without every band re-covering the ground the last one
	// already did. Same ODD-winding, filled-only technique, same reason
	// (drawAnnulus's comment above: an unfilled ofPath's "stroke" is
	// glLineWidth in disguise, capped or ignored depending on the renderer).
	const ofRectangle outer(base.x - outerOffsetPx, base.y - outerOffsetPx,
		base.width + 2.0f * outerOffsetPx, base.height + 2.0f * outerOffsetPx);
	const ofRectangle inner(base.x - innerOffsetPx, base.y - innerOffsetPx,
		base.width + 2.0f * innerOffsetPx, base.height + 2.0f * innerOffsetPx);
	const float rOuter = std::min(baseCornerRadiusPx + outerOffsetPx,
		std::min(outer.width, outer.height) * 0.5f);
	const float rInner = std::min(baseCornerRadiusPx + innerOffsetPx,
		std::min(inner.width, inner.height) * 0.5f);
	ofPath path;
	path.setFilled(true);
	path.setFillColor(colour);
	path.setCircleResolution(24);
	path.setPolyWindingMode(OF_POLY_WINDING_ODD);
	path.rectRounded(outer, rOuter);
	path.rectRounded(inner, rInner);
	path.draw();
}

void UiLayer::drawHalo(int i) const {
	// VISUAL_LAYER.md §6, Idle: "Halo only, no simulation... Alpha falls
	// off quadratically from the bin edge outward (brightest at edge)...
	// Slow breathing sine on alpha, each bin phase-offset by a per-bin
	// random seed so the 8 do not pulse in sync." All 8 bins draw this by
	// default — "idle" is every bin's resting state.
	//
	// Active (build item 6): "Gold halo crossfades OUT as the fire ring
	// crossfades IN... Never both at once in the same annulus — they go
	// muddy." `_bins[i].fire` is the exact same crossfade spring
	// fireEmitters() reads to drive the fluid's own ring injection — one
	// spring, read by both halves of the crossfade, so halo and fire can
	// never disagree about how far along the transition is.
	const float fireFade = 1.0f - _bins[i].fire.get();
	if(fireFade <= 0.001f){
		return;   // fully active — nothing left of the idle halo to draw
	}
	const ofRectangle bin = binRectPx(i);
	const float baseCornerRadiusPx = mmToPxX(CUTOUT_CORNER_RADIUS_MM);
	// 0.35 floor, not the first attempt's 0.1 — see kHaloBreathPeriodS's
	// own comment: a bin dimmed almost to nothing read as broken, not as
	// breathing, in the first photo.
	const float breathe = 0.65f + 0.35f
		* sinf(TWO_PI * ofGetElapsedTimef() / kHaloBreathPeriodS + _haloPhase[i]);
	for(int k = 0; k < kHaloRingCount; k++){
		const float innerPx = kHaloMarginPx + (float)k * kHaloRingPitchPx;
		const float outerPx = innerPx + kHaloRingThicknessPx;
		// k=0 (closest to the bin edge) is brightest; quadratic falloff
		// outward, same shape doc §6 asks for on the fire ring's own fumes.
		const float edgeFrac = (float)k / (float)(kHaloRingCount - 1);
		const float alpha = 255.0f * breathe * fireFade * (1.0f - edgeFrac) * (1.0f - edgeFrac);
		if(alpha < 1.0f){
			continue;   // skip a band nobody would see rather than draw it at 0 alpha
		}
		drawRoundedBand(bin, innerPx, outerPx,
			ofColor(kHaloIdleColor, alpha), baseCornerRadiusPx);
	}
}

std::vector<UiLayer::FireEmitter> UiLayer::fireEmitters() const {
	std::vector<FireEmitter> out;
	for(int i = 0; i < 8; i++){
		const float intensity = _bins[i].fire.get();
		if(intensity < 0.01f){
			continue;   // skip an emitter nobody would see rather than inject at ~0 alpha
		}
		out.push_back({binRectPx(i), mmToPxX(CUTOUT_CORNER_RADIUS_MM),
			kFireRingInnerPx, kFireRingOuterPx, intensity, i});
	}
	return out;
}

void UiLayer::update(float dt, bool hasState, const StateLink::State & state){
	if(!hasState){
		return;
	}
	for(int i = 0; i < 8 && i < (int)state.bins.size(); i++){
		const StateLink::Bin & b = state.bins[i];
		BinTween & tw = _bins[i];

		// Cached here, not read at draw time: cutoutRectsPx() is called
		// by ofApp after endContent(), with no `state` in scope. Not
		// tweened either — a bin rect is rig calibration, not animation,
		// and springing it would smear the light-pass cutout across the
		// table for 150ms after every save.
		_hasCoreRect[i] = b.hasRect;
		if(b.hasRect){
			_coreRects[i] = ofRectangle(b.rx, b.ry, b.rw, b.rh);
		}

		tw.picked.setTarget(b.picked);
		tw.price.setTarget((float)b.price);
		// VISUAL_LAYER.md §6 Active / build item 6: "max 1 bin at a time,"
		// enforced by core (StateLink::Bin::hl comes from a single hover
		// pointer — core/main.py's _bin_msg) rather than re-checked here;
		// this spring just crossfades whichever bin(s) hl currently says
		// are hovered.
		// `_forceAllBinsLit` is the 'f' diagnostic (UiLayer.h) — it drives
		// this ONE spring rather than being special-cased downstream, so
		// every consumer of "this bin is active" (the halo's crossfade out,
		// fireEmitters()'s injection, the ring itself) sees the forced
		// state through exactly the path a real hover uses. A diagnostic
		// that took a different route than the thing it is diagnosing would
		// be worth nothing.
		tw.fire.setTarget((_forceAllBinsLit || b.hl == "hover") ? 1.0f : 0.0f);

		tw.picked.update(dt);
		tw.price.update(dt);
		tw.fire.update(dt);
	}
	_totalAmount.setTarget((float)state.total.amount);
	_totalAmount.update(dt);

	// VISUAL_LAYER.md §8, build item 10: which bin the info box is about,
	// and how far it has faded in. The active bin is `hl == "hover"` —
	// the same field the fire ring's own crossfade reads, so the box and
	// the flame can never disagree about which bin is active.
	//
	// `_forceAllBinsLit` (the 'f' diagnostic) is deliberately NOT honoured
	// here, unlike the fire spring above: that switch exists to light
	// every ring at once, and "every bin is active" has no answer for a
	// box that shows exactly one bin's facts. Picking the first would put
	// one arbitrary bin's kcal on the table for as long as the diagnostic
	// is on, which is worse than the box simply not appearing.
	int active = -1;
	for(int i = 0; i < 8 && i < (int)state.bins.size(); i++){
		if(state.bins[i].hl == "hover" && !state.bins[i].diet.empty()){
			active = i;
			break;
		}
	}
	if(active >= 0){
		// Held, not cleared, when nothing is active — the box needs
		// something to draw while it fades out. See _infoBin's own comment.
		_infoBin = active;
	}
	_infoFade.setTarget(active >= 0 ? 1.0f : 0.0f);
	_infoFade.update(dt);

	// VISUAL_LAYER.md §8, build item 9: bind cart slots in PICK ORDER, not
	// bin order. `picked` is already core's deadbanded, snapped-to-truth
	// integer (core/main.py's _bin_msg — pricing.display_grams(shown_g)),
	// so ">0" is the same crossing core itself already treats as "this
	// bin is picked" (it is exactly the condition _bin_msg uses for
	// `hl: "picked"`) — no separate epsilon of oF's own invention needed.
	//
	// Reset condition: every bin at picked<=0 simultaneously. That is
	// true at boot (nothing picked yet) and true again once an order
	// finishes and the next diner's session re-baselines everything back
	// to 0 (I6's reset_session) — the same "all 8 empty" state either
	// way, so treating both as "clear the cart" is correct without needing
	// a dedicated session-boundary field on the wire. The doc's own "never
	// unbind a bound slot" rule (a put-back keeps its row) still holds for
	// every OTHER case: this only clears when the WHOLE cart reads empty
	// at once, not when one bin among several does.
	bool anyPicked = false;
	for(int i = 0; i < 8 && i < (int)state.bins.size(); i++){
		if(state.bins[i].picked > 0.0f){
			anyPicked = true;
			break;
		}
	}
	if(!anyPicked){
		_cartSlotBin.fill(-1);
	}
	else {
		for(int i = 0; i < 8 && i < (int)state.bins.size(); i++){
			if(state.bins[i].picked <= 0.0f){
				continue;
			}
			bool alreadyBound = false;
			for(int k = 0; k < 8; k++){
				if(_cartSlotBin[k] == i){
					alreadyBound = true;
					break;
				}
			}
			if(alreadyBound){
				continue;
			}
			for(int k = 0; k < 8; k++){
				if(_cartSlotBin[k] == -1){
					_cartSlotBin[k] = i;
					break;
				}
			}
		}
	}
}

void UiLayer::drawBin(int i, const StateLink::Bin & b, const BinTween & tw) const {
	ofRectangle box = binRectPx(i);
	ofRectangle cut = cutoutRectPx(i);

	// 2026-08-14, developer instruction: the solid plate ring (the M1-era
	// grey/green frame that used to carry doc §4.3's `hl` state — I8, "hue
	// carries state") is REMOVED outright, now that the idle halo exists to
	// occupy that same visual role. It never appears in VISUAL_LAYER.md's
	// own palette table (§3), which only ever specified the halo/fire pair —
	// this was the pre-M8 mechanism the new one supersedes, not a second
	// state channel meant to coexist with it. `highlightColour()` and
	// `BinTween`'s scale/colR/colG/colB springs went with it — nothing else
	// read them. **Consequence, not yet answered: "picked" now has no
	// visual distinction of its own** until the fire ring (build item 6/7)
	// exists — an idle-halo'd bin and a picked-but-otherwise-idle bin
	// currently render identically. That is expected to be fire's job, not
	// a gap to patch here.
	if(!b.resolved){
		return;   // doc §9.3: unresolved bins render with no label
	}

	const float cx = box.getCenter().x;
	const float clearance = mmToPxY(kLabelClearanceMM);
	const float gap = mmToPxY(kLabelLineGapMM);

	// Labels clear a fixed offset past the CUTOUT, not the bin box — kept
	// as its own named gap (kRingMM) even though nothing draws a ring there
	// any more (2026-08-14, see this function's own comment above), because
	// removing it would pull the label right up against the cutout edge,
	// a layout change nobody asked for. This is the bug the offset
	// originally fixed: kLabelClearanceMM and CUTOUT_MARGIN_MM are both
	// 10mm, so measuring from box.y put the far row's baseline exactly on
	// the cutout's edge and the light pass ate every descender — the "g"
	// in both "45g" and "₹18.00/100g".
	const float ringRestY = mmToPxY(kRingMM);
	const float ringTop = cut.y - ringRestY;
	const float ringBottom = cut.y + cut.height + ringRestY;
	// Negative for descenders below the baseline, per ofTrueTypeFont.h's
	// own doc comment (it is FreeType's face->descender). fabsf rather
	// than negation so a font that reported it the other way still clears.
	const float rateDescend = fabsf(_plateRateFont.getDescenderHeight());

	std::string detail = b.sub;
	if(b.picked > 0.5f){
		// doc §13.4 names one 26px row, "Bin weight / unit price" — read
		// literally as one line whose content switches, not two lines: the
		// unit price matters before a pick, the picked amount and its
		// running price matter after one.
		char g[16];
		snprintf(g, sizeof(g), "%dg", (int)roundf(tw.picked.get()));
		detail = std::string(g) + "  " + _priceText(tw.price.get());
	}

	// b.label is core's display_name() again (core/main.py's `_bin_msg`) —
	// 2026-08-14, reverted the same day from a `shortLabel`-only, no-wrap
	// design (see kPlateNamePx's comment above) on developer instruction:
	// "remove the short label idea... show the original label, max 2
	// lines." Wrapped to the bin's own footprint, not the gap to its
	// neighbour — the neighbour gap (250mm) is wider, but wrapping to the
	// box the label sits over keeps every name visually inside its own
	// plate.
	std::vector<std::string> nameLines = wrapNameToTwoLines(_plateNameFont, b.label, mmToPxX(BIN_W_MM));
	const float nameLineGap = 2.0f;   // px between a name's own wrapped lines, tighter than kLabelLineGapMM's block-to-block gap

	if(i < 4){
		// far row: rate strip sits just above the ring, name strip above
		// that — ring → price/grams → name, reading outward from the pot.
		float rateBaseline = ringTop - clearance - rateDescend;
		ofSetColor(kPlateRateColor);
		drawCentered(_plateRateFont, detail, cx, rateBaseline);

		// The visual gap between the rate line and the name block has to
		// be measured from the RATE line's ascender (its actual top
		// edge), not its getLineHeight() — line height includes internal
		// leading on top of the ascender, so using it here was quietly
		// inflating this gap. See the mirrored near-row branch below: it
		// made the same mistake in the other direction with a much bigger
		// error (an ascender is far taller than a descender in this
		// font), which is why a 2026-08-14 rig photo showed the near
		// row's label-to-price gap visibly larger than the far row's for
		// the identical `gap` constant — this fixes both to the same real
		// gap. nameLines.back() sits closest to the rate line; earlier
		// lines stack upward, spaced by this font's own line height since
		// both lines share one font/size.
		float lastLineBaseline = rateBaseline - _plateRateFont.getAscenderHeight() - gap
			- fabsf(_plateNameFont.getDescenderHeight());
		ofSetColor(kPlateNameColor);
		for(int li = (int)nameLines.size() - 1; li >= 0; li--){
			float y = lastLineBaseline - (float)(nameLines.size() - 1 - li) * (_plateNameFont.getLineHeight() + nameLineGap);
			drawCentered(_plateNameFont, nameLines[li], cx, y);
		}
	}
	else {
		// near row: label strip is BELOW the ring, into the 177.4mm near
		// margin — the diner's own side of the table.
		//
		// Detail sits closest to the ring and the name outside it, which is
		// the MIRROR of the far row above, not a copy of its top-to-bottom
		// order. Reading outward from the pot, both rows now go ring →
		// price/grams → name, so the two halves of the table are symmetric
		// about the centre the way the bins themselves are. The previous
		// version put the name nearest the ring down here and the detail
		// nearest it up there, so the same two rows of text were in
		// opposite orders on the two sides of one table.
		float rateBaseline = ringBottom + clearance + _plateRateFont.getAscenderHeight();
		ofSetColor(kPlateRateColor);
		drawCentered(_plateRateFont, detail, cx, rateBaseline);

		// Mirrors the far row's fix above: measured from the rate line's
		// DESCENDER (its real bottom edge), not getLineHeight() — see that
		// branch's comment for the bug this replaces. nameLines.front()
		// sits closest to the rate line; later lines stack downward.
		float firstLineBaseline = rateBaseline + rateDescend + gap
			+ _plateNameFont.getAscenderHeight();
		ofSetColor(kPlateNameColor);
		for(size_t li = 0; li < nameLines.size(); li++){
			drawCentered(_plateNameFont, nameLines[li], cx,
				firstLineBaseline + (float)li * (_plateNameFont.getLineHeight() + nameLineGap));
		}
	}
	ofSetColor(255);
}

std::string UiLayer::_priceText(double amount) const {
	return formatCurrency(amount, _currencyPrefix, _currencyDecimals);
}

void UiLayer::drawTotal(const StateLink::Total & total, float baselineY) const {
	// VISUAL_LAYER.md §3/§8: one receipt-style line inside the cart
	// footer — "Total label" (30px, left) and "Total value" (48px bold,
	// right), sharing one baseline the way a printed receipt's total
	// line does. This replaces the old free-standing centred numeral
	// near the table's diner edge (pre-M8; this function's own git
	// history) now that build item 9 gives the total a permanent home
	// inside the cart panel instead. cx is still the table's own centre,
	// which is also the cart's centre — the pot gap is symmetric about
	// it (TableGeometry.h's X chain), so no separate column math is
	// needed here.
	const float cx = mmToPxX(TABLE_W_MM * 0.5f);
	const float leftX = cx - kCartWidthPx * 0.5f + kCartPadXPx;
	const float rightX = cx + kCartWidthPx * 0.5f - kCartPadXPx;

	// doc §13.4's original "Total label" caption, still core-resolved per
	// locale (data/locales/<locale>.json's "total" key) and put on
	// `total.label` — oF only draws whatever string arrives (I2: no
	// lookup here). Empty on an older core (StateLink defaults it to "")
	// draws nothing, same rule drawCentered's own callers already follow.
	if(!total.label.empty() && _totalLabelFont.isLoaded()){
		ofSetColor(kPlateNameColor);
		_totalLabelFont.drawString(total.label, leftX, baselineY);
	}

	std::string text = formatCurrency(_totalAmount.get(), _currencyPrefix, _currencyDecimals);
	if(_totalNumFont.isLoaded()){
		ofRectangle bb = _totalNumFont.getStringBoundingBox(text, 0, 0);
		ofSetColor(kCartTotalValueColor);
		_totalNumFont.drawString(text, rightX - bb.width - bb.x, baselineY);
	}
	ofSetColor(255);
}

void UiLayer::drawInfoBox(const StateLink::State & state) const {
	// VISUAL_LAYER.md §8/§9 build item 10. "Info box sits ABOVE the cart,
	// fixed height, does not push the cart down. Idle: invisible. No fill,
	// no border. Not an empty bordered box. Active: fill + border + text
	// fade in. Shows veg/non-veg, kcal, short description for the active
	// bin."
	//
	// The band is reserved unconditionally by kCartTopPx' own arithmetic,
	// so "does not push the cart down" is true by construction rather than
	// by this function being careful — nothing here can move anything.
	const float fade = _infoFade.get();
	if(fade <= 0.005f || _infoBin < 0 || _infoBin >= (int)state.bins.size()){
		return;
	}
	const StateLink::Bin & b = state.bins[_infoBin];
	if(b.diet.empty()){
		return;
	}

	const float cx = mmToPxX(TABLE_W_MM * 0.5f);
	const ofRectangle box(cx - kCartWidthPx * 0.5f, kInfoBoxTopPx,
		kCartWidthPx, kInfoBoxHeightPx);

	// One alpha for fill, border and every glyph — §8 fades the box as one
	// thing, and staggering them would read as a rendering fault rather
	// than as a transition.
	const int a = (int)(255.0f * ofClamp(fade, 0.0f, 1.0f));
	ofSetColor(kInfoBoxFill, a);
	ofDrawRectangle(box);
	drawRectBorder(box, kInfoBoxBorderWidthPx,
		ofColor(kInfoBoxBorderColor, a));

	if(!_infoFont.isLoaded()){
		ofSetColor(255);
		return;
	}
	const float lineH = _infoFont.getLineHeight() + kInfoBoxLineGapPx;
	const float leftX = box.x + kInfoBoxPadXPx;
	float baselineY = box.y + kInfoBoxPadYPx + _infoFont.getAscenderHeight();

	// Line 1: the diet dot and word on the left, kcal on the right. The
	// dot is not decoration and is not alone — it is paired with the word
	// for the same reason I8 says a state is never carried by colour by
	// itself, and this is the one line on the table somebody may actually
	// act on.
	ofColor dietColour = kInfoDietEggColor;
	std::string dietWord = "EGG";
	if(b.diet == "veg"){
		dietColour = kWidgetPrimary;
		dietWord = "VEG";
	}
	else if(b.diet == "nonveg"){
		dietColour = kWidgetDanger;
		dietWord = "NON-VEG";
	}
	const float dotCx = leftX + kInfoDietDotRadiusPx;
	ofSetColor(dietColour, a);
	ofDrawCircle(dotCx, baselineY - _infoFont.getAscenderHeight() * 0.4f,
		kInfoDietDotRadiusPx);
	_infoFont.drawString(dietWord,
		dotCx + kInfoDietDotRadiusPx + kInfoDietDotGapPx, baselineY);

	if(!b.kcal.empty()){
		ofRectangle kb = _infoFont.getStringBoundingBox(b.kcal, 0, 0);
		ofSetColor(kInfoBoxTextColor, a);
		_infoFont.drawString(b.kcal,
			box.x + kCartWidthPx - kInfoBoxPadXPx - kb.width - kb.x, baselineY);
	}

	// Lines 2-3: the description, wrapped by the same greedy word-wrap the
	// plate's own name uses (wrapNameToTwoLines) rather than a second
	// wrapper — two lines is also this box's own budget at its fixed
	// height, so the cap and the layout agree by construction.
	ofSetColor(kInfoBoxTextColor, a);
	const float textWidth = kCartWidthPx - 2.0f * kInfoBoxPadXPx;
	std::vector<std::string> lines = wrapNameToTwoLines(_infoFont, b.desc, textWidth);
	for(size_t k = 0; k < lines.size() && k < 2; k++){
		baselineY += lineH;
		_infoFont.drawString(lines[k], leftX, baselineY);
	}
	ofSetColor(255);
}

void UiLayer::drawCart(const StateLink::State & state) const {
	// Same centre column as drawBrandMark/drawBanner (the pot gap), same
	// fixed top (kCartTopPx — see that constant's own comment on why it
	// does not move when the mode banner appears/disappears).
	const float cx = mmToPxX(TABLE_W_MM * 0.5f);
	const float x = cx - kCartWidthPx * 0.5f;

	const float rowsBottom = kCartTopPx + kCartRowHeightPx * 8.0f;
	const float dividerY = rowsBottom + kCartDividerGapPx;
	const float totalTop = dividerY + kCartBorderWidthPx + kCartDividerGapPx;
	const float totalBaselineY = totalTop + _totalNumFont.getAscenderHeight();

	// No panel fill and no border — see kCartBorderColor's own comment
	// above. The cart is now text on the table background, the same as
	// every plate label, and the only rule left on it is the divider
	// above the total.

	// The 8 fixed row slots, doc §8: "Slots are blank at startup. No
	// name, no placeholder text, no icon, no border. Just reserved empty
	// space" — an unbound slot draws nothing at all, not even a
	// separator, which is what makes it read as reserved space rather
	// than a rendering gap.
	for(int k = 0; k < 8; k++){
		int binIdx = _cartSlotBin[k];
		if(binIdx < 0 || binIdx >= (int)state.bins.size()){
			continue;
		}
		const StateLink::Bin & b = state.bins[binIdx];
		if(!b.resolved){
			continue;
		}
		const BinTween & tw = _bins[binIdx];
		const float rowTop = kCartTopPx + (float)k * kCartRowHeightPx;
		const float baselineY = rowTop + kCartRowHeightPx * 0.5f
			+ _cartRowFont.getAscenderHeight() * 0.5f;

		// Same "%dg  <price>" composition as drawBin's own post-pick
		// detail line (doc §13.4), so the two never disagree about how a
		// pick is worded — one bin's picked amount, read in two places.
		char g[16];
		snprintf(g, sizeof(g), "%dg", (int)roundf(tw.picked.get()));
		std::string detail = std::string(g) + "  " + _priceText(tw.price.get());
		ofRectangle detailBb = _cartRowFont.getStringBoundingBox(detail, 0, 0);

		const float nameMaxWidth = kCartWidthPx - 2.0f * kCartPadXPx
			- detailBb.width - kCartRowMidGapPx;
		std::string name = truncateToWidth(_cartRowFont, b.label, nameMaxWidth);

		ofSetColor(kPlateNameColor);
		_cartRowFont.drawString(name, x + kCartPadXPx, baselineY);

		ofSetColor(kCartRowDetailColor);
		_cartRowFont.drawString(detail,
			x + kCartWidthPx - kCartPadXPx - detailBb.width - detailBb.x, baselineY);
	}

	ofSetColor(kCartBorderColor);
	ofDrawRectangle(x, dividerY, kCartWidthPx, kCartBorderWidthPx);
	drawTotal(state.total, totalBaselineY);
	ofSetColor(255);

	// **Confirm/Cancel are NOT drawn here any more.** They were static
	// placeholders in this function until 2026-08-24, when the developer
	// reported the obvious consequence: "the confirm and cancell button
	// didnt work and no progress of hover was shown." They are real dwell
	// targets now, and a dwell target's rect has to be the rect CORE
	// hit-tests against (doc §9.4: core hit-tests, "oF does not time
	// anything") — so core/hover.py owns both buttons outright and they
	// arrive on the wire like any other widget, drawn by drawWidgets/
	// drawWidget. Drawing them from a second, oF-local rect would put a
	// button on the table that a hand could miss while looking like it hit
	// it, which is worse than the placeholder was.
	//
	// hover.py's CART_* constants mirror kCartWidthPx/the cart's own
	// bottom edge here, and setup() logs if the two ever drift far enough
	// for the buttons to collide with the total — that check is the only
	// thing standing between the two files, so read it before moving
	// either.
}

float UiLayer::cartBottomPx() const {
	// The lowest ink the cart itself draws — the total's own descender.
	// Public-ish (private, but read by setup()'s cross-file check against
	// core/hover.py's button band) precisely so the number that has to
	// agree with the other language is computed once, here, rather than
	// re-derived by eye at the check.
	const float rowsBottom = kCartTopPx + kCartRowHeightPx * 8.0f;
	const float totalTop = rowsBottom + kCartDividerGapPx + kCartBorderWidthPx
		+ kCartDividerGapPx;
	return totalTop + _totalNumFont.getAscenderHeight()
		+ fabsf(_totalNumFont.getDescenderHeight());
}

void UiLayer::drawConnectionIndicator(bool connected, float staleSeconds) const {
	// Doc §13.3: frozen state, never a black table, plus "a small
	// connection-lost indicator in a corner." This is diner-facing (drawn
	// on the table itself, not gated behind any dev flag) because a stalled
	// table with no explanation reads as broken, not paused.
	if(connected && staleSeconds <= 0.5f){
		return;
	}
	const float w = 1920.0f, h = 1080.0f;
	const float pad = 24.0f;
	ofSetColor(200, 0, 0);
	ofDrawCircle(w - pad - 8, pad + 8, 8);
	ofSetColor(60, 60, 60);
	_devFont.drawString("NO SIGNAL", w - pad - 118, pad + 14);
	ofSetColor(255);
}

void UiLayer::drawBanner(const ofColor & fill, const ofColor & ink,
	const std::string & headline, const std::string & subline) const {
	// Doc §14.5's pattern for naming a persistent, whole-table state
	// loudly without touching the light field. Built for `overlay.kind ==
	// "error"` at M2 and generalised at M2.6, when setting mode became the
	// second thing that needed exactly this — same panel, different hue
	// and words.
	//
	// **NOT a full-width strip along the top edge, which is what §14.5
	// literally said and what this drew until it was seen on the table.**
	// The far row's labels are drawn ABOVE their rings, upward into the
	// 177mm far margin: a two-line wrapped name (which several catalogue
	// names are, at 36px in a 200mm box) puts ink as high as ~50px, and a
	// 72px full-width strip covered it. Staff have to READ those names to
	// confirm which tray is which — during setting mode above all, which
	// is exactly when this banner is up. Covering them defeated the mode.
	//
	// So the panel is confined to the centre column: the span between
	// bin 1's right edge and bin 2's left edge, which TableGeometry.h
	// calls "a wide gap up the middle for the pot" and which is the one
	// horizontal span on the table with no bin and no label in it, by
	// construction. Derived from BINS rather than hardcoded, so moving a
	// bin moves the panel with it.
	//
	// Being narrower, it is taller and two-line instead — a ~440x88mm
	// amber block is still unmistakable from three metres, which was the
	// actual goal, and the strip shape was only ever one way to get there.
	//
	// Stage's light pass runs after UiLayer and re-stamps every cutout
	// white regardless of what this draws (doc §13.2's "any overlay added
	// later" safety property), so this can never darken a bin patch.
	//
	// Bins and the total keep drawing underneath (draw() calls this after
	// them, not instead of them) — doc §13.3's rule for a dead core link
	// applies just as well to a dead scale link: "It does not black out —
	// a frozen table is far better... than a dead one."
	// yTop, not 0: the strip used to start at the table's far edge; now
	// the brand mark owns that edge (drawBrandMark) and this panel starts
	// wherever the mark's own bottom margin ends, so the two stack instead
	// of one replacing the other.
	const float gapLeftMM = BINS[1].xMM + BINS[1].wMM;
	const float gapRightMM = BINS[2].xMM;
	const float insetPx = mmToPxX(kBannerInsetMM);
	const float x = mmToPxX(gapLeftMM) + insetPx;
	const float w = mmToPxX(gapRightMM - gapLeftMM) - 2.0f * insetPx;
	const float h = kBannerHeightPx;
	const float cx = x + w * 0.5f;
	const float yTop = kBrandTopMarginPx + kBrandHeightPx + kBrandBannerGapPx;

	ofSetColor(fill);
	ofDrawRectangle(x, yTop, w, h);

	ofSetColor(ink);
	// Two lines, centred as a block: the headline is what a diner reads
	// from across the room and says nothing about modes or billing; the
	// subline is the operator's word for which state it is. Both audiences
	// are looking at the same table (doc §12.1's no-jargon rule applies
	// here more than anywhere — this surface has no operator filter on it).
	const float lineGap = 8.0f;
	const float blockH = _nameFont.getAscenderHeight() + lineGap
		+ _detailFont.getAscenderHeight();
	const float localTop = (h - blockH) * 0.5f;
	drawCentered(_nameFont, headline, cx, yTop + localTop + _nameFont.getAscenderHeight());
	drawCentered(_detailFont, subline, cx, yTop + h - localTop);
	ofSetColor(255);
}

void UiLayer::drawBrandMark() const {
	// Developer request, not doc §14.5: persistent "always visible" table
	// branding, top-anchored in the pot-gap centre column — "the one
	// horizontal span on the table with no bin and no label in it, by
	// construction" (see drawBanner). Unlike the banner this is never
	// hidden — draw() always calls it when the image loaded — and
	// drawBanner positions itself below the mark's bottom edge rather
	// than sharing this strip, so the two stack instead of one replacing
	// the other.
	if(!_brandLogoLoaded){
		return;
	}
	const float gapLeftMM = BINS[1].xMM + BINS[1].wMM;
	const float gapRightMM = BINS[2].xMM;
	const float insetPx = mmToPxX(kBannerInsetMM);
	const float x = mmToPxX(gapLeftMM) + insetPx;
	const float w = mmToPxX(gapRightMM - gapLeftMM) - 2.0f * insetPx;
	const float cx = x + w * 0.5f;

	// Height-bound, not width-bound — the column is much wider than the
	// logo needs, and the logo is much wider than it is tall.
	const float drawH = kBrandHeightPx;
	const float drawW = drawH * ((float)_brandLogo.getWidth() / (float)_brandLogo.getHeight());

	ofSetColor(255);
	_brandLogo.draw(cx - drawW * 0.5f, kBrandTopMarginPx, drawW, drawH);
}

void UiLayer::drawTopBanner(const StateLink::State & state) const {
	// **Precedence: SETTING wins over error.** Both claim the same strip
	// and both can be true at once — in setting mode, someone knocks the
	// XIAO cable out. Nothing bills in setting mode (core's
	// _apply_scale_to_cart returns immediately there), so
	// "SCALES OFFLINE — NOT BILLING" would be warning about a risk that
	// cannot occur, while displacing the one message that is true. The
	// person doing setting-mode work is holding the tablet, and the staff
	// view's Bins tab already shows "Load cells: no connection"; this
	// strip is for everyone *not* holding the tablet.
	//
	// This is the general rule for the strip, established here because
	// `recap`/`qr` (M6) will ask the same question: the state that changes
	// what the table is DOING outranks the state that reports a fault in
	// a subsystem that state has already disabled.
	//
	// English only, matching UiLayer's current scope. Doc §14.5 pairs the
	// banner with a Chinese string; zh locale data does not exist yet (M1
	// is English-only end to end) and §17.3 is explicit that Chinese
	// judges will read this, so the zh text must be confirmed by a native
	// speaker before it ships rather than guessed at here.
	// Both banners lead with the SAME headline, deliberately. "NOT
	// SERVING" is the only part a diner needs, and it is equally true of
	// both states; which one it is only matters to the operator, who gets
	// it from the subline and from the hue (I8). The old wording said
	// "NOT BILLING" — an internal word for an external surface, and the
	// system's own second word for the same idea. There is one word now,
	// "serving", and it is the one a diner already understands.
	// **Order, doc §14.5's precedence table:**
	//   uncalibrated > setting > error
	//
	// `uncalibrated` outranks `setting` because it SURVIVES setting mode:
	// an operator who exits setting mode on a table with no geometry
	// still cannot serve, and `setting` would mask the one message that
	// is still true for the whole time they are trying to fix it.
	if(state.overlayKind == "uncalibrated"){
		drawBanner(kUncalBannerFill, kUncalBannerInk,
			"NOT SERVING", "not set up yet");
		return;
	}
	if(state.mode == "setting"){
		drawBanner(kSettingBannerFill, kSettingBannerInk,
			"NOT SERVING", "setting the table");
		return;
	}
	if(state.overlayKind == "error"){
		drawBanner(kErrorBannerFill, kErrorBannerInk,
			"NOT SERVING", "scales offline");
	}
}

void UiLayer::drawWidget(const StateLink::Widget & w) const {
	const ofRectangle box(w.x, w.y, w.w, w.h);
	ofColor ring = kWidgetSecondary;
	if(!w.enabled){
		ring = kWidgetDisabled;
	}
	else if(w.style == "primary"){
		ring = kWidgetPrimary;
	}
	else if(w.style == "danger"){
		// 2026-08-24: the cart's Cancel. A third style rather than
		// reusing "secondary" grey — I8 wants a state carried by hue, and
		// "this discards your order" is not the same statement as "this
		// is the lesser of two buttons."
		ring = kWidgetDanger;
	}

	// Dwell progress, drawn INSIDE the button as a rising fill. The
	// cursor's own ring (drawCursor) already shows the same fraction, but
	// it sits under the diner's hand — which is exactly where a hand is
	// while dwelling — so on the rig it reads as no feedback at all
	// (developer, 2026-08-24: "no progress of hover was shown"). The
	// button fills from the BOTTOM up: it is the one direction that stays
	// visible past the edge of a hand covering the middle of the button.
	// `dwell` is core's 0..1 fraction; oF still times nothing (doc §9.4).
	if(w.enabled && w.dwell > 0.0f){
		const float fillH = box.height * ofClamp(w.dwell, 0.0f, 1.0f);
		ofSetColor(kWidgetDwellFill);
		ofDrawRectangle(box.x, box.y + box.height - fillH, box.width, fillH);
	}

	const float ringX = mmToPxX(kWidgetRingMM);
	const float ringY = mmToPxY(kWidgetRingMM);
	// drawRing frames the rect from OUTSIDE it, the same annulus rule the
	// plates follow (§14.4), so the label inside is never touched by its
	// own frame however thick the frame becomes.
	drawRing(box, ringX, ringY, ring);

	// Dark ink on a light field (§13.4) — and a disabled button's label is
	// greyed rather than hidden, because a button whose label vanished
	// would read as a rendering fault rather than as unavailable.
	//
	// 2026-08-24: an ENABLED label now takes the ring's own colour rather
	// than kInkColor. Both live colours (kWidgetPrimary's green,
	// kWidgetDanger's red) are dark enough for §13.4's rule on their own,
	// and a green ring around near-black text carries the hue in a thin
	// frame only — half of what the developer's "washed out" report was
	// about. kInkColor is still what a plain "secondary" widget gets, so
	// nothing that was neutral becomes coloured by this.
	ofSetColor(w.enabled ? (w.style == "primary" || w.style == "danger" ? ring : kInkColor)
		: kWidgetDisabled);
	drawCentered(_nameFont, w.label, box.getCenter().x,
		box.getCenter().y + _nameFont.getAscenderHeight() * 0.5f);
	ofSetColor(255);
}

void UiLayer::drawWidgets(const StateLink::State & state) const {
	for(const StateLink::Widget & w : state.widgets){
		drawWidget(w);
	}
}

void UiLayer::drawCursor(const CursorLink::Hand & pointer, float dwell) const {
	// doc §11.4: "oF draws NO cursor and NO dwell ring for [ambient hands]."
	// The caller only ever passes the pointer, and that is where the
	// isolation lives on this side — ofApp asks CursorLink for pointer()
	// and never iterates hands looking for one.
	const float cx = pointer.x;
	const float cy = pointer.y;

	// Dwell first, so the cursor itself is never drawn under its own ring.
	if(dwell > 0.0f){
		// The unfilled track, then the filled sweep on top of it. The track
		// matters: without it a 5%-full ring is a tiny stub with nothing to
		// read it against, and the diner cannot tell "the table has started
		// counting" from "the table has not noticed me".
		drawAnnulus(cx, cy, kDwellRingOuter, kDwellRingInner, kDwellTrackColor);
		// -90 so the sweep starts at 12 o'clock rather than at 3 o'clock,
		// which is what every progress ring a person has ever seen does.
		const float start = -90.0f;
		drawAnnulus(cx, cy, kDwellRingOuter, kDwellRingInner, kDwellFillColor,
			start, start + 360.0f * dwell);
	}

	drawAnnulus(cx, cy, kCursorRingOuter, kCursorRingInner, kCursorColor);
	ofSetColor(kCursorColor);
	ofDrawCircle(cx, cy, kCursorDotRadius);
	ofSetColor(255);
}

float UiLayer::dwellFraction(const StateLink::State & state) const {
	// The dwell fraction comes from CORE, per widget (doc §9.4: "oF does
	// not time anything"). Looked up by the widget the pointer is inside
	// rather than by remembering which one core said was active — there is
	// no such field on the wire, and adding one would be a second source
	// of truth for something already implied. Shared by `draw()`'s own
	// cursor pass and `drawCursorAboveLightPass()` so the two never
	// compute two different dwell fractions for the same frame.
	float dwell = 0.0f;
	for(const StateLink::Widget & w : state.widgets){
		if(w.dwell > dwell){
			dwell = w.dwell;
		}
	}
	return dwell;
}

void UiLayer::drawCursorAboveLightPass(const StateLink::State & state,
	const CursorLink::Hand * pointer) const {
	// The ONLY place the cursor is drawn while serving — `draw()`'s own
	// cursor block above explicitly skips it in that mode so the two call
	// sites can never both fire for the same frame (a cursor drawn twice
	// was tried and rejected: one draw site per mode, not two draws
	// layered on top of each other).
	//
	// Safe specifically because ofApp only ever builds the
	// `drawAboveLightPass` callback this feeds while `state.mode ==
	// "serving"` (see Stage::compositeAndWarp's own comment) — the
	// classifier can never be running then (doc §12.7's capture refusal
	// requires setting mode), so nothing drawn here after the light pass
	// can ever land in a photo the classifier takes. The null check below
	// is the ordinary "no pointer this frame" case, same as `draw()`'s own.
	if(pointer == nullptr){
		return;
	}
	drawCursor(*pointer, dwellFraction(state));
}

void UiLayer::drawSkeleton(const std::vector<SkeletonLink::Hand> & hands) const {
	// RIG_FEEDBACK item 11 diagnostic — see this method's own header
	// comment. Deliberately the simplest possible draw: no tween, no
	// hysteresis, no role, nothing hidden past a hold time — whatever
	// SkeletonLink last accepted is drawn exactly as it arrived, so what
	// is on the table this frame is the raw signal for this frame and
	// nothing else.
	for(const SkeletonLink::Hand & h : hands){
		ofSetColor(kSkeletonLineColor);
		ofSetLineWidth(kSkeletonLineWidth);
		for(const auto & pair : kSkeletonConnections){
			size_t a = (size_t)pair[0], b = (size_t)pair[1];
			if(a >= h.points.size() || b >= h.points.size()){
				continue;
			}
			ofDrawLine(h.points[a].x, h.points[a].y,
				h.points[b].x, h.points[b].y);
		}
		for(size_t i = 0; i < h.points.size(); i++){
			bool isTracked = ((int)i == kSkeletonCursorLandmark);
			ofSetColor(isTracked ? kSkeletonTrackedColor : kSkeletonJointColor);
			ofDrawCircle(h.points[i].x, h.points[i].y,
				isTracked ? kSkeletonTrackedRadius : kSkeletonJointRadius);
		}
	}
	ofSetColor(255);
}

void UiLayer::drawDevOverlay(bool hasState, const StateLink::State & state,
	bool connected, float fps) const {
	char buf[128];
	snprintf(buf, sizeof(buf), "fps %.0f  link %s  seq %lld",
		fps, connected ? "up" : "down", hasState ? (long long)state.seq : -1LL);
	ofSetColor(140, 140, 140);
	_devFont.drawString(buf, 16, 1080.0f - 16.0f);
	ofSetColor(255);
}

void UiLayer::draw(bool hasState, const StateLink::State & state,
	bool connected, float staleSeconds, float fps, bool showDevOverlay,
	const std::vector<CursorLink::Hand> & hands,
	const CursorLink::Hand * pointer) const {
	if(!_fontsLoaded){
		drawConnectionIndicator(connected, staleSeconds);
		return;
	}

	// VISUAL_LAYER.md §9 build item 5 ("Layer reorder"): everything from
	// here down is layers 4 (halo) and 5 (UI — plate text, logo, cart,
	// info box), in that order, matching §5's "bottom to top" list.
	// Layers 1-3 (table background, fluid, the white-cutout light pass)
	// are Stage's job, not UiLayer's — see Stage.h's own header comment
	// for why layer 3 is drawn structurally LAST of the whole frame
	// (after this method returns) rather than literally third: I9
	// requires nothing drawn afterward to survive inside a cutout, and
	// that only holds if the light pass is the final write, not a
	// mid-frame one. Halo and UI never draw INTO a cutout by design
	// (halo wraps the bin only; plate text sits outside it), so drawing
	// them here, ahead of the light pass in wall-clock terms, is safe —
	// the light pass punches them back to white if that geometry is ever
	// wrong, rather than relying on draw order alone to keep it true.
	//
	// --- layer 4: halo ----------------------------------------------------
	if(hasState){
		for(int i = 0; i < 8 && i < (int)state.bins.size(); i++){
			drawHalo(i);
		}
	}

	// --- layer 5: UI --------------------------------------------------
	// Brand mark first among the UI elements — always on when loaded,
	// never hidden by the banner, which positions itself below the
	// mark's bottom edge instead of sharing its strip (see
	// drawBrandMark/drawBanner). Drawn outside the hasState gate too: a
	// table with no core link yet still has no banner to show
	// (state.mode/overlayKind don't exist without state), so the brand
	// mark is the "always visible" default from boot — this is why it
	// cannot simply move inside the `if(hasState)` block below alongside
	// the rest of layer 5.
	drawBrandMark();

	if(hasState){
		// Once per frame, ahead of the bins: drawBin's price line and
		// drawTotal's numeral both format off this same prefix/decimals
		// pair, pulled from the one locale-resolved string the wire gives
		// oF (state.total.text) — see splitCurrencyText's comment.
		splitCurrencyText(state.total.text, _currencyPrefix, _currencyDecimals);
		for(int i = 0; i < 8 && i < (int)state.bins.size(); i++){
			drawBin(i, state.bins[i], _bins[i]);
		}
		drawInfoBox(state);
		drawCart(state);
		drawWidgets(state);
		drawTopBanner(state);
	}

	// The cursor goes on LAST of everything the diner reads, so it is never
	// buried under a label or a button it is sitting on top of. Over a bin
	// cutout specifically it is NOT drawn here at all while serving — see
	// the condition below and `drawCursorAboveLightPass`'s own comment.
	// Exactly one of those two call sites ever draws the cursor for a
	// given frame; which one depends on mode, never both — a cursor drawn
	// twice per frame was tried and rejected as the wrong design.
	//
	// Every mode OTHER than serving keeps the original, single-pass
	// behaviour unchanged: drawn here, and erased by Stage's light pass if
	// it lands on a cutout (I9, full strength — a bin cutout must stay
	// unpatterned while the classifier could be running, which is exactly
	// setting mode). Serving is the one mode the classifier can never be
	// active in (doc §12.7's capture refusal), so it is the one mode where
	// skipping this draw and doing it after the light pass instead is
	// safe — see ofApp::draw's own comment on why it only ever builds the
	// `aboveLightPass` callback when `state.mode == "serving"`.
	if(pointer != nullptr && state.mode != "serving"){
		drawCursor(*pointer, dwellFraction(state));
	}

	drawConnectionIndicator(connected, staleSeconds);
	if(showDevOverlay){
		drawDevOverlay(hasState, state, connected, fps);
	}
}
