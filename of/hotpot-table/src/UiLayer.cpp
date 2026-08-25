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

	// **2026-08-25: a regular weight, because everything on this table was
	// bold.** Developer, verbatim: "every text font look bulky bold i
	// never asked to use same font through out the table. use better font
	// as needed for each item." This repo genuinely only had ONE
	// proportional face until now, so every heading, label, note and
	// caption drew at the same weight and nothing could be subordinate to
	// anything else — the info box's note shouted as loudly as the item's
	// name above it.
	//
	// The rule now, and it is the ordinary typographic one: BOLD is for
	// things read at a distance or read first (a plate's name, the info
	// box's name, a button, the total's figure); REGULAR is for prose and
	// for anything the eye should land on second (the info box's note,
	// cart row names, the total's label); MONO is for numbers that must
	// not jitter as their digits change (the plate rate, cart amounts).
	// Same DejaVu family throughout, so this is one voice at three
	// weights rather than three typefaces arguing.
	//
	// Vendored from this machine's matplotlib install, same precedent and
	// same permissive licence as the two above.
	const std::string kRegularFontFile = "fonts/DejaVuSans.ttf";
	const std::string kMonoBoldFontFile = "fonts/DejaVuSansMono-Bold.ttf";

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
	//
	// **20 -> 12 and 24 -> 14 (2026-08-25), and the 18px that buys is
	// spent, not saved.** Moving the step dots onto their own line under
	// the title (drawPageHeader, developer's own instruction) grows
	// `_pageHeaderPx` by exactly that much, and the info box's band —
	// which `setup()` measures against real font metrics — had 6px of
	// slack, not 18. Lowering `kInfoBoxTopPx` by 18 gives the band back
	// what the header took, so the box is no tighter than before.
	// Split across both gaps rather than taken out of one, so neither
	// the mark's clearance from the table edge nor its breathing room
	// above the banner collapses on its own.
	const float kBrandHeightPx = 170.0f;
	const float kBrandTopMarginPx = 12.0f;
	const float kBrandBannerGapPx = 14.0f;

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

	// --- the one gold on this table, and why it is not the halo's hex ----
	// Developer, 2026-08-24: "the total you are showing in gold colour is
	// coming as red. if you need gold use the bin's hallow's colour."
	//
	// The instruction is followed on HUE, which is where the fault was.
	// #B8781A sits at hue ~35 degrees (orange), and this rig's projector
	// has now warm-shifted a 35-degree ink into "red" twice — the plate
	// rate line first, then this. kHaloIdleColor is hue ~55, and these
	// constants are that same hue, so a warm shift lands them on gold
	// rather than dragging them into red.
	//
	// **It is NOT literally 0xFFEB00, and that is arithmetic, not taste.**
	// Relative luminance of #FFEB00 is ~0.808 against the table
	// background's ~0.792 (I9's near-white field, Stage's
	// kTableBackground) — a contrast ratio of about 1.02. Halo yellow
	// AS A GLYPH is invisible on this table. It works on a bin because a
	// halo is light spilling onto the field, not text read against it.
	//
	// That reasoning produced a dark ink of the halo's hue, which the
	// developer then saw projected and rejected outright ("dont go with
	// gold, use some other colour"). It is kept here because it is the
	// measurement that rules out the whole gold family on this rig, not
	// just the two hexes that were tried: nothing in that hue can be both
	// legible on #E8E6E1 and still read as gold. kAccentInk below is the
	// answer that followed from it.
	// **2026-08-25: not gold at all any more.** Developer, after seeing
	// the halo-hue version projected: "dont go with gold, use some other
	// colour." Gold has now been tried three ways on this table (#B8781A
	// orange, then the halo's own #FFEB00, then a dark ink of that hue)
	// and every one of them fought the warm projector or the near-white
	// field. This is a deep TEAL, which is the useful direction: it is
	// far from the projector's warm shift (so it cannot slide toward
	// red the way every amber has), it is nowhere near the green of
	// Confirm or the red of Cancel, and it is dark enough to read as ink
	// on #E8E6E1 — relative luminance ~0.13 against the field's ~0.79, a
	// contrast ratio near 6:1, where the best gold managed 3.3.
	const ofColor kAccentInk(0x0E, 0x6B, 0x78);
	// Was #B8781A (the doc's original "Total value" hex) until 2026-08-24,
	// when the developer read it on the table: "the total you are showing
	// in gold colour is coming as red" — and then, once it was a
	// halo-hue gold instead, "dont go with gold, use some other colour."
	// It is kAccentInk, a deep teal; see that constant.
	const ofColor kCartTotalValueColor = kAccentInk;
	// **The total has NO glow.** A `drawGlow` behind the numeral was
	// tried for one build and the developer saw exactly what it is:
	// "there is a wierd rectangle around the total price which doesnt
	// make any sense." drawGlow emits nested ROUNDED-RECT bands around a
	// bounding box — which reads as a halo around a bin, where the thing
	// inside really is a rectangle, and as a mysterious box around a
	// number, where it is not. Deleted rather than dimmed.

	// The rule above the total, and the one inside the info box.
	//
	// **A plain alpha fade at constant thickness.** Developer, 2026-08-25:
	// "the line spereator betwwn cart item and total looks terrible, why
	// did u put that weierd broken lines there. just the fade effect is
	// more than enough." The version they saw tapered its HEIGHT as well
	// as its alpha, and the height taper is what broke it: the core was
	// clamped to a 1px minimum, so the last stretch at each end became a
	// row of 1px stubs whose alpha had already rounded to nothing — a
	// dashed line, not a fade. Height is constant now and only alpha
	// moves, which is the effect that was wanted in the first place.
	const float kRuleThickPx = 2.0f;
	const int kRuleAlpha = 150;
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
	const int kCartRowPx = 21;
	// The grams/price column, mono. Two points smaller than the name it
	// sits beside: mono's cap height runs larger for the same nominal
	// size (measured, PIL/FreeType against the real .ttf — the same thing
	// that made a "smaller" 26px rate line read BIGGER than a 28px name
	// on 2026-08-14), so equal numbers here would not look equal.
	const int kCartDetailPx = 17;
	// 500 -> 520 (2026-08-25). setup()'s own check said the name column
	// was 276px against the 280px the longest catalogue name needs — 4px
	// short, which is exactly the kind of margin that had been failing
	// silently before that check existed. **Mirrored in core/hover.py's
	// CART_WIDTH_PX** (the buttons and M6's option plates derive from it)
	// and still inside the 554px centre column.
	const float kCartWidthPx = 520.0f;
	// 44px -> 32px, 2026-08-24: developer, on the second rig look, "the
	// total and buttons all feels cramped together, reduce the cart size,
	// it should fit with the near row." Eight 44px rows plus the footer
	// ran 394px, which cannot fit the near row's own 301mm-tall band
	// however it is positioned. At 32px the rows are 256px and the whole
	// cart is ~348px, which sits inside the band plus the empty 50mm gap
	// above it — the "may take little space of far row" the same message
	// allowed for. 32px still clears the row font's own ink height (22px
	// bold measures 21px ascender + 6px descender).
	const float kCartRowHeightPx = 32.0f;
	const float kCartBorderWidthPx = 2.0f;   // filled bars, not ofSetLineWidth
	                                          // — see the halo's own comment
	                                          // above on why a stroke width
	                                          // is unusable on this rig.
	const float kCartPadXPx = 20.0f;
	const float kCartRowMidGapPx = 16.0f;    // name column <-> detail column
	const float kCartDividerGapPx = 12.0f;   // rows -> divider -> total

	// **The right-hand column is a FIXED width, measured once at setup()
	// from this string, and that is the fix for the second truncation
	// report.** Developer, 2026-08-24: "the names still gets tuncate when
	// the weight go double didgit, even if it go tripple digit, it should
	// not truncate." The first fix sized the name column against whatever
	// the detail column happened to measure THAT ROW — so "5g $0.18" left
	// the name plenty of room and "125g $4.50" took it away again, and a
	// name that fitted at 5 grams lost its tail at 125. A column whose
	// width depends on the number in it is a column that moves, and the
	// thing next to it pays for it.
	//
	// Reserving the worst case instead costs the same 160px on every row
	// and can never move: 500 - 2*20 pad - 16 gap - 160 leaves 284px for
	// the name, against the widest catalogue name's 238px at 22px
	// (measured, PIL/FreeType against the real .ttf and the real
	// catalogue). `truncateToWidth` stays as the net; it should now never
	// fire.
	const char * kCartDetailWorstCase = "999g  $99.99";
	// What the longest catalogue name needs, and **the number that broke
	// two truncation fixes in a row.**
	//
	// PIL/FreeType against the real .ttf says "Button Mushrooms" is 197px
	// at 21px regular. oF does NOT agree: its own `getStringBoundingBox`
	// comes back substantially wider for the same string in the same face
	// at the same size. Both previous attempts at this bug sized the
	// columns from the PIL number and both left the name column too
	// narrow on the actual table. So this is the PIL measurement scaled
	// by oF's own observed ratio, with headroom, and setup() logs the
	// real numbers at every boot so the next surprise is one grep away
	// rather than a rebuild.
	//
	// **kOfWidthRatio: that ratio is 1.400, and it is now MEASURED rather
	// than estimated (2026-08-25).** It was written here as "~33%" from a
	// single half-remembered pair; the boot log has been printing the
	// real number at every start since setup()'s check landed, and it
	// settles it exactly:
	//
	//     kCartDetailWorstCase "999g  $99.99" at 17px DejaVuSansMono
	//         PIL 120.0px      oF 168.0px (logged)      ratio 1.400
	//
	// Cross-checked against the other pair in the log — "Button
	// Mushrooms" at 21px regular, PIL 197.0 x 1.400 = 275.8, against the
	// 274-280 this constant was set to by hand. Two independent faces,
	// two sizes, one ratio.
	//
	// **This is the number to size any new text column against**, and the
	// option plates below are the first thing to use it deliberately
	// rather than by trial. It is not a magic constant — oF measures a
	// bounding box where PIL reports an advance, and the two differ by
	// bearings and by oF's own atlas padding — but it is stable enough
	// across these faces to design with, and every use of it is still
	// backed by a runtime warning rather than trusted outright.
	const float kOfWidthRatio = 1.400f;
	const float kCartMinNameSpacePx = 280.0f;

	// **The cart is anchored to the NEAR ROW's own bottom edge, not to the
	// banner above it.** Developer, 2026-08-24: "it should fit with the
	// near row... the buttons should be vertically center alligned in the
	// space below the near row bottom edge and the bottom edge of the
	// table." Growing the block downward from the banner is what left the
	// total and the buttons crowding each other at the diner's edge; this
	// derivation puts the cart's last pixel a fixed gap above the near
	// row, whatever happens to the info box or the banner above it, and
	// leaves the whole 209px below the near row free for the buttons.
	//
	// `kCartFooterHeightPx` is the divider gap + rule + gap + the total's
	// own ascender-plus-descender block. It is a reserved budget rather
	// than a font measurement because these are namespace constants and
	// the fonts are not loaded yet — `setup()` measures the real thing and
	// warns if this number is short. Do not shrink it without watching
	// that warning.
	const float kCartFooterHeightPx = 92.0f;
	const float kCartBottomGapPx = 16.0f;
	const float kNearRowBottomPx = mmToPxY(BINS[4].yMM + BIN_H_MM);
	const float kCartRowsBottomPx = kNearRowBottomPx - kCartBottomGapPx
		- kCartFooterHeightPx;
	// Fixed, never a function of whether the mode banner or the info box
	// happens to be showing — doc §8's "never moves" applies as much to
	// appearing as it does to growing.
	const float kCartTopPx = kCartRowsBottomPx - kCartRowHeightPx * 8.0f;

	// --- VISUAL_LAYER.md §8, build item 10: the info box -------------------
	// "Info box sits ABOVE the cart, fixed height, does not push the cart
	// down." Fixed height is still the whole mechanism, but the band it
	// gets is now everything between the brand mark and the cart, rather
	// than a 136px strip with the banner's own band left empty above it.
	//
	// **It starts where the mode banner starts, and the two never share a
	// frame.** Developer, 2026-08-24: "there is ton of space above the box
	// and below the logo unused." That space is the banner's, and the
	// banner only exists when the table is NOT serving — no hover, so no
	// info box. `drawInfoBox` refuses to draw while a banner is up rather
	// than relying on that being true, which is also doc §14.5's own
	// precedence rule: the state that changes what the table is DOING
	// outranks anything else in the centre column.
	//
	// The height is derived from the cart's own top so the two can never
	// be edited into overlapping.
	const float kInfoBoxTopPx = kBrandTopMarginPx + kBrandHeightPx
		+ kBrandBannerGapPx;
	const float kInfoBoxCartGapPx = 12.0f;
	const float kInfoBoxHeightPx = kCartTopPx - kInfoBoxCartGapPx - kInfoBoxTopPx;
	// **The item's NAME leads the box now.** Developer, same message: "it
	// is not telling the food items name in it." Nothing else on the table
	// says which bin the box is about — the plate's own label is at the
	// far end of a 1.5m table from the reader.
	const int kInfoBoxNamePx = 30;
	// 24 -> 19 ("u can reduce the font size") -> 18, 2026-08-25. The last
	// point came off the option screens: the box's band is shorter there
	// by the page header, and the broth/spice notes need all three of
	// kInfoBoxNoteMaxLines' lines (measured — see that constant).
	const int kInfoBoxTextPx = 18;
	// The kcal figure, deliberately larger than the body text — see
	// UiLayer.h's _infoKcalFont for the report that moved it.
	const int kInfoBoxKcalPx = 22;   // mono now, which reads larger than
	                                  // the same nominal size in the sans
	const int kInfoDietPx = 17;
	const float kInfoBoxPadXPx = 24.0f;
	// 14 -> 10 and 6 -> 5, 2026-08-25. Both came off the same measurement
	// as kInfoBoxTextPx above: the line gap appears seven and a half times
	// in the box's height sum (once after the name, twice around the rule,
	// one and a half after the diet line, three inside the note), so a
	// single point off it is worth more here than anywhere else on the
	// table.
	const float kInfoBoxPadYPx = 10.0f;
	const float kInfoBoxLineGapPx = 5.0f;
	// **No fill, no border, no panel.** The pink-fill + fire-glow rounded
	// card that lived here until 2026-08-24 is gone: the developer picked
	// Direction A off the design canvas, which is the text-forward one —
	// the box is type on the table background, exactly as doc §4 already
	// requires of the plate ("no fill and no border. Text sits directly on
	// the table background") and as the cart itself was changed to be
	// earlier the same day. It is also doc §8's own words for this
	// element: "Idle: invisible. No fill, no border."
	//
	// What groups it instead is the tapered gold rule and the shared left
	// margin with the cart below it. kInfoBoxFill/kInfoBoxGlow/
	// kInfoBoxCornerPx and their glow-band counts are deleted outright,
	// this file's usual rule, rather than left dormant at alpha 0.
	const ofColor kInfoBoxTextColor(0x4A, 0x42, 0x38);   // the note line
	const ofColor kInfoBoxNameColor(0x2B, 0x21, 0x18);   // the plate's own ink
	const ofColor kInfoBoxKcalColor(0x56, 0x4D, 0x3A);
	// The note wraps to at most this many lines.
	//
	// **This stopped being headroom when M6 landed, and nothing noticed
	// until 2026-08-25.** It was written as "three, against content that
	// measures two" — true of the ingredient notes in `catalogue.json`,
	// which is all there was then. The broth and spice notes in
	// `menu.json` are longer sentences, and measured against the real face
	// at the real width the worst of them ("Numbing and fiery, built on
	// Sichuan pepper and chilli. The boldest broth here.") takes all
	// three, at 18px and at 19px alike. So the cap is now exactly the
	// requirement, and the box's band has to fit three lines rather than
	// two-plus-slack — which is what the padding, line gap and text size
	// above were retuned for.
	//
	// "No text should get truncated" (developer, 2026-08-24) still holds:
	// `wrapToLines`' ellipsis is the net, and setup()'s own check is what
	// says whether the net is about to be needed.
	const int kInfoBoxNoteMaxLines = 3;

	// --- doc §18.1's CHECKOUT screen -------------------------------------
	// The QR's quiet zone, in MODULES, which is the unit the spec states
	// it in — 4 is the standard minimum and going below it is the usual
	// reason a projected code will not scan. Drawn as a white plate under
	// the code (see drawCheckout) because this table's background is
	// never blank.
	const float kQrQuietModules = 4.0f;
	const float kQrCaptionGapPx = 14.0f;

	// **The whole code, quiet zone included, is 200px — SMALLER THAN A
	// BIN.** Developer, 2026-08-25: "the qr code should be much smaller
	// smaller, now it even overlaps the bins. it should be easily scanned
	// by persons phone. so bigger means they have to move phone much far
	// away. it should be smaler than the bin size."
	//
	// Two separate faults, and this fixes both:
	//
	// 1. The overlap was arithmetic. The old sizing solved for a module
	//    from `avail - 2 * kQrQuietModules * 4` — a fixed 32px allowance
	//    for the margins — and then laid out `module * n + 2 * (module *
	//    4)`, where the quiet zone is 8 MODULES wide, not 32px. At the
	//    module size it picked that came out at 592px against a 554px
	//    centre column, so the code ran off the pot gap and onto the
	//    trays either side of it.
	//
	// 2. The size was wrong even where it fitted. A QR is scanned at the
	//    distance where it fills the phone's frame, so a bigger projected
	//    code makes the diner stand FURTHER back, not closer — which on a
	//    1.5m table means leaning away from the thing they are scanning.
	//
	// 200px is 159mm on the plywood against a bin's 200mm, so it is
	// visibly smaller than the trays beside it. At 29 modules plus 8 of
	// quiet zone that is a 5px module, ~4mm physical — comfortably above
	// what a phone camera resolves at arm's length, and the code is
	// projected at full contrast onto a white plate (below), which is the
	// part that actually decides whether a scan succeeds.
	const float kQrTargetSidePx = 200.0f;
	// The token, once it exists. Big, because it is the one thing a diner
	// carries away from this screen — and it exists ONLY after payment,
	// which is core's rule, not this file's (see StateLink::Qr::token).
	const int kTokenPx = 88;

	// The veg/non-veg dot. Green and red are the same two the cart's own
	// buttons use (kWidgetPrimary/kWidgetDanger) rather than a third
	// pair — one green and one red on this table, not several. Egg is
	// neither, and gets its own amber rather than being rounded into one
	// of them; see `pricing.VALID_DIETS`' own comment for why the wire
	// carries three values and not two.
	const ofColor kInfoDietEggColor(0xD9, 0x82, 0x2B);
	const float kInfoDietDotRadiusPx = 8.0f;
	const float kInfoDietDotGapPx = 10.0f;

	// The broth card's own, tighter vertical rhythm, 2026-08-25: developer,
	// "the broth details is getting truncated... reduce the size of the
	// info to fit in that box." Two of the three real notes (mala,
	// collagen) were overrunning `drawOptionPlate`'s broth-card branch by
	// one short line under the shared `kInfoBoxPadYPx`/`kInfoBoxLineGapPx`
	// rhythm the bins' info box uses — that rhythm is left alone (the bins
	// were already measured against it) and the broth card gets its own,
	// smaller pad and note line-gap instead, which is the whole difference
	// needed to clear a 3rd note line without shrinking any text.
	const float kBrothCardPadYPx = 7.0f;
	const float kBrothCardNoteLineGapPx = 3.0f;

	// --- M5: the pointer cursor and the dwell ring ------------------------
	// Sizes in px because they are screen furniture, not table geometry —
	// nothing about a cursor is measured in millimetres of plywood. The
	// numbers are set against the one thing that does matter physically:
	// a hand is not a mouse, so the cursor has to be visible under a hand
	// that is partly covering it, from three metres, on a near-white field.
	// **A flame, not concentric rings, 2026-08-25** — developer: "instead
	// of the concentric circles as the pointer, can we have a small candle
	// like flame?" `kCursorFlameHPx` is sized to roughly the same visual
	// footprint the old ring pair (13-28px radius) had, so this swap did
	// not also have to re-earn "visible under a hand, from three metres".
	const float kCursorFlameHPx = 46.0f;
	// The dwell ring sits OUTSIDE the flame rather than replacing it: the
	// cursor must not change shape as the ring fills, or the diner reads
	// it as the cursor breaking rather than as progress.
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

	// **A ROUNDED RECTANGLE, not a pill (2026-08-25).** Developer: "also
	// make itrectangle with rounded corners insted of current shape", and
	// on the same shape a message earlier, "still the shape and size tooks
	// bad."
	//
	// The pill was half-height corners, which was the roundest a rect can
	// be — and at 100px tall with a 6-letter label that made a lozenge
	// whose end caps were wider than the space the word sat in. It read as
	// a badge, not as a button. 18px of radius on a 76px button is about
	// what every kiosk, phone and ticket machine a diner has already used
	// puts on a primary action: unmistakably a button, still soft enough
	// not to fight the fluid and the halos around it.
	//
	// The option plates use the SAME radius rather than a proportional
	// one, so a 520x74 broth plate and a 155x76 Next button read as the
	// same family of control at two sizes. A radius that scaled with the
	// shape would make the wide plates look flatter than the buttons.
	const float kWidgetCornerPx = 18.0f;

	// **The green/red pair is gone (2026-08-25).** Developer: "the red and
	// green is not suing well."
	//
	// They were right, and the reason is that this table has a palette
	// already: a warm near-white field (#E8E6E1), amber halos breathing
	// around the bins, orange fire, and one deep teal accent that earned
	// its place by being the only ink that survived three rounds of the
	// projector's warm shift (see kAccentInk). A saturated traffic-light
	// green and a fire-engine red are from a different design — they read
	// as a web form dropped onto the plywood, and the red in particular
	// competed with the actual fire.
	//
	// What replaces them is a hierarchy rather than two opposed signals,
	// which is also what every restaurant kiosk does (QSR Magazine's own
	// guidance: the confirming action is the large, bold one; "Cancel" and
	// "Edit" are kept smaller and less prominent):
	//
	//   PRIMARY (Next / Pay)  the teal accent, the loudest thing in the
	//                         row — filled harder, glowing brighter. It is
	//                         the one button a diner following the flow
	//                         ever needs to find.
	//   DANGER (Cancel)       a muted clay red. Still unmistakably the
	//                         warning colour, but desaturated into the
	//                         table's own warm family so it stops shouting
	//                         over the food.
	//   SECONDARY (Back)      warm graphite. Quiet on purpose: Back is for
	//                         a diner who already knows they want it.
	//
	// All three are dark inks on a near-white field (§13.4's rule), and
	// all three are separated by HUE rather than by brightness (I8) — teal
	// ~188 degrees, clay ~11, graphite neutral.
	const ofColor kWidgetPrimary(0x0E, 0x6B, 0x78);   // = kAccentInk, the table's one accent
	const ofColor kWidgetSecondary(0x6E, 0x6A, 0x62); // warm graphite
	const ofColor kWidgetDanger(0xA8, 0x55, 0x45);    // muted clay
	const ofColor kWidgetDisabled(0xB4, 0xB0, 0xA8);  // warm grey, not blue-grey
	// The dwell sweep, drawn INSIDE a widget (see drawWidget). Same amber
	// as the cursor's own dwell ring (kDwellFillColor) so a filling button
	// and a filling ring read as one mechanism, at the low alpha a tint
	// under dark text has to keep.
	const ofColor kWidgetDwellFill(200, 120, 0, 80);

	// **A glow drawn in the same dark ink as a button's border reads as a
	// SHADOW, not a halo — 2026-08-25, developer: "if u r putting halo
	// around buttons, its not at all clear, it looks like a shado."** The
	// bins' own halo (`drawHalo`) never has this problem because it never
	// glows in its own ink: it uses a bright, saturated colour of its own
	// (`kHaloIdleColor`, a hue the ink palette does not otherwise carry) at
	// up to full 255 alpha. `kWidgetPrimary`/`kWidgetDanger`/
	// `kWidgetSecondary` were tuned the opposite way, deliberately DARK and
	// muted so they read as ink on a light field (see kAccentInk's own
	// comment on that fight) — exactly the properties that make a diffuse
	// blur of them look like an ordinary drop shadow instead of light.
	// Rather than adding a fourth hex per hue (this table's palette has
	// already been through three rounds of "that colour drifts under the
	// projector" — see kAccentInk), this pushes the SAME hue toward full
	// brightness and saturation for the glow only, leaving every ink use
	// (text, borders) untouched. A light, saturated version of a colour is
	// unambiguously light; a dark, muted one is not.
	ofColor glowTint(const ofColor & ink){
		ofColor c = ink;
		c.setSaturation(215.0f);
		c.setBrightness(235.0f);
		return c;
	}

	// --- the glow, and why it BREATHES ------------------------------------
	//
	// Developer, 2026-08-25 (second report on the same thing): "the
	// cancell and confirm button still does not have a active breathing
	// halo."
	//
	// The glow was already here and already the right size — the previous
	// fix widened it to match the bins' own halo (40px of reach, 20 bands)
	// after a 24px/9-band version turned out to be under the visible
	// threshold on this field. What it was not was ALIVE. The bins breathe
	// (drawHalo's `breathe` term, a 3s sine with a 0.65 floor) and the
	// buttons sat at a constant alpha next to them, which on a table where
	// everything else is moving reads as "dead", not as "quiet".
	//
	// So the button's glow now runs the same sine, at the same period, off
	// the same clock — one breath across the whole table rather than two
	// rhythms. `kWidgetBreathFloor` is higher than the halo's 0.65 because
	// a button must never be at its dimmest when a diner first looks for
	// it; the swing is smaller and the floor is higher, so it reads as
	// steady-with-a-pulse rather than as fading in and out.
	//
	// **Hovering pins it to full and stops the breathing.** A control the
	// hand is on should be steady, not pulsing under the finger, and the
	// step change from breathing to solid is itself the "yes, this one"
	// feedback — before the dwell ring has moved at all.
	const float kWidgetGlowReachPx = 40.0f;
	const int kWidgetGlowBands = 20;
	const float kWidgetBreathPeriodS = kHaloBreathPeriodS;   // one breath, table-wide
	const float kWidgetBreathFloor = 0.78f;
	// The primary action is louder than the other two, deliberately — see
	// the palette block above on kiosk button hierarchy. These are the
	// peak alphas the breath multiplies.
	const int kWidgetFillAlpha = 26;
	// Raised alongside `glowTint`, 2026-08-25 — the tint made the glow
	// lighter, and a light colour at the old 105/165 alpha over #E8E6E1
	// was still too washed out to read as lit rather than smudged.
	const int kWidgetGlowAlpha = 150;
	const int kWidgetPrimaryFillAlpha = 52;
	const int kWidgetPrimaryGlowAlpha = 205;

	// --- the option plates (broth, spice) ---------------------------------
	// A selected plate is filled and check-marked rather than merely
	// glowing: the developer's model is "the selection is locked even
	// without hover... then the info als remains locked", and a lock has
	// to be readable from across the table with no hand anywhere near it.
	//
	// **Fill and ring both raised, 2026-08-25** — developer: "the selected
	// button blue colour looks like grey and it is very difficult to see
	// it is selected. we need really contrast colour for selected and not
	// selected." The old 60/255 (~23%) fill was close enough to the
	// unselected card's own 26/255 wash that "selected" read as a hairline
	// hue difference rather than a locked-in state; this is now more than
	// double the unselected fill and paired with a ring nearly TWICE
	// `kWidgetRingMM` thick (`kOptionSelectedRingMM`, drawOptionPlate) so a
	// selected card is unmistakable by shape alone, not just by a subtler
	// shade of the same near-white.
	//
	// **The card's own ink no longer changes on selection, 2026-08-25,
	// later still** — developer: "dont make it blue like done currently.
	// instead change the border thickness and change the halo colour."
	// `kOptionSelectedRingMM` above already carried the border half of
	// that; `drawOptionPlate` now carries the halo half itself, tinting
	// ONLY the glow with `kWidgetPrimary` on selection — the fill, the
	// ring and the name ink all stay the plate's ordinary neutral colour
	// whether it is selected or not.
	const int kOptionSelectedFillAlpha = 140;
	const int kOptionSelectedGlowAlpha = 210;
	const float kOptionSelectedRingMM = 9.0f;
	// **Broth and spice share one card shape now, 2026-08-25, later
	// still.** The chili-strip cell (a separate, narrower layout) is gone
	// with the vertical-slider redesign it belonged to — see
	// `hover.spice_widgets`'s own comment — so spice draws through
	// exactly the broth-card branch below, and `kOptionLabelPx`
	// (`_optionFont`'s size) is the one constant that branch still needs
	// from this block.
	const int kOptionLabelPx = 20;

	// --- the page header (title + step dots) -------------------------------
	// One sentence naming the task, and where the diner is in the
	// sequence. See StateLink::Screen for why this exists at all.
	//
	// 26px against the 554px centre column: the longest title core sends
	// ("Choose Your Spice") measures 278px in PIL, so ~389px as oF will
	// measure it — 165px of margin. The title is centred and free of the
	// cart's 520px, so the column is what bounds it, not the cart.
	const int kPageTitlePx = 26;
	// **The header's HEIGHT is measured at setup(), not fixed here** — see
	// `_pageHeaderPx`. It was a 52px constant for one build and the info
	// box's own check caught what that cost: the band below it came out
	// 228.5px against 244.2px of content, and the box would have
	// overflowed on exactly the two screens the header exists for. A
	// height guessed ahead of the font metrics is the same class of
	// mistake `kCartFooterHeightPx` already carries a warning for.
	const float kPageHeaderGapPx = 10.0f;   // header block -> info box
	// **The dots sit UNDER the title, on their own line** — developer,
	// 2026-08-25: "also it is better to have itsown line instead of same
	// line as the statement discribing the page."
	//
	// They were beside it, and the argument for that was space: stacked,
	// the header takes ~18px more of a band the info box's three-line
	// note already fills. That argument is answered rather than
	// overruled — `kBrandTopMarginPx`/`kBrandBannerGapPx` gave the 18px
	// back (see their own block), so this is a layout change and not a
	// trade against the note.
	//
	// It also reads better than the inline version did: a progress
	// indicator under a heading is what every checkout a diner has
	// already used looks like, and beside it the pair had to be centred
	// as a group, so the TITLE itself sat off-centre by half the dots'
	// width — different amounts on different screens, because the titles
	// are different lengths.
	const float kStepDotRadiusPx = 5.0f;
	const float kStepDotGapPx = 14.0f;
	// The title's DESCENDER line -> the dots' top edge. Measured off the
	// descender rather than the baseline so a title with descenders
	// ("Choose Your Spice") cannot reach into the dots — and the
	// descender is a font metric, not a per-string one, so the dots
	// still sit at the same height on every screen.
	//
	// 8px here is what makes the whole header exactly 18px taller than
	// the inline version was, which is exactly what the brand margins
	// above gave back. Both numbers move together or the info box's own
	// band check (setup()) starts warning.
	const float kStepDotsRowGapPx = 8.0f;
	const ofColor kPageTitleColor(0x2B, 0x21, 0x18);   // the plate's own ink

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

	// `drawRectBorder` lived here and is deleted (2026-08-24): the cart
	// panel's border went with the white panel on the first rig look, and
	// the info box's went with its redesign on the second. Nothing draws a
	// rectangular border on this table any more. The RULE it existed to
	// respect still stands and still applies to the divider above the
	// total and the info box's own hairline: every line on this surface is
	// a FILLED rect, never ofSetLineWidth/ofPath stroke — this file's halo
	// comment found stroke width driver-capped at 1px and ignored outright
	// on the programmable renderer on this rig.

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

	// The same greedy word-wrap, with a caller-chosen line cap instead of
	// a hard 2 — the info box's note takes three. Kept separate from
	// wrapNameToTwoLines rather than replacing it, because the two differ
	// in what they do when they run out of lines: that one dumps every
	// remaining word onto line 2 (a bin label is short and overflowing is
	// louder than truncating), this one truncates the last line with an
	// ellipsis so a long note cannot run out of its band. Every catalogue
	// note fits in 2 lines at kInfoBoxTextPx today against a 3-line cap
	// (measured, PIL/FreeType against the real .ttf), so the truncation
	// is a net that should never fire, not the mechanism — the developer's
	// standing rule on this table is "no text should get truncated."
	std::vector<std::string> wrapToLines(const ofTrueTypeFont & font,
		const std::string & text, float maxWidthPx, size_t maxLines){
		std::vector<std::string> lines;
		if(!font.isLoaded() || text.empty() || maxLines == 0){
			return lines;
		}
		std::istringstream iss(text);
		std::vector<std::string> words;
		std::string w;
		while(iss >> w){
			words.push_back(w);
		}
		std::string cur;
		for(size_t i = 0; i < words.size(); i++){
			std::string candidate = cur.empty() ? words[i] : cur + " " + words[i];
			if(!cur.empty()
				&& font.getStringBoundingBox(candidate, 0, 0).width > maxWidthPx){
				lines.push_back(cur);
				cur = words[i];
				if(lines.size() == maxLines - 1){
					// Last line left: take everything remaining and let
					// truncateToWidth cut it, rather than dropping words
					// silently.
					for(size_t j = i + 1; j < words.size(); j++){
						cur += " " + words[j];
					}
					break;
				}
			}
			else {
				cur = candidate;
			}
		}
		if(!cur.empty()){
			lines.push_back(truncateToWidth(font, cur, maxWidthPx));
		}
		return lines;
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
	ok = loadUiFont(_totalNumFont, kMonoBoldFontFile, 48) && ok;
	// "Total" is a caption for the figure beside it, so it is regular —
	// the number is what should be loud.
	ok = loadUiFont(_totalLabelFont, kRegularFontFile, 30) && ok;
	// Regular, not bold: a cart row is a list of things the diner already
	// chose, read at arm's length, and eight bold lines in a column read
	// as eight headings.
	ok = loadUiFont(_cartRowFont, kRegularFontFile, kCartRowPx) && ok;
	// Mono for the grams/price column so the numbers do not re-flow as
	// digits change, and so the column reads as data next to prose.
	ok = loadUiFont(_cartDetailFont, kMonoFontFile, kCartDetailPx) && ok;
	ok = loadUiFont(_infoNameFont, kFontFile, kInfoBoxNamePx) && ok;
	// The note is prose. Regular weight is most of what "every text font
	// look bulky bold" was about.
	ok = loadUiFont(_infoFont, kRegularFontFile, kInfoBoxTextPx) && ok;
	// The diet word stays BOLD and small: it is a label, not prose, and
	// it is the one line on the box somebody may act on.
	ok = loadUiFont(_infoDietFont, kFontFile, kInfoDietPx) && ok;
	ok = loadUiFont(_infoKcalFont, kMonoFontFile, kInfoBoxKcalPx) && ok;
	// A button's label is read first, so it stays bold — but at 22px, not
	// the 28px `_nameFont` it used to borrow. Three buttons now share the
	// cart's 520px (core/hover.py's own `button_row`), which leaves each
	// one 154.7px, and the widest label ("Cancel") measures ~114px as oF
	// measures it — 40px of margin. At the old 28px it was ~148px in a
	// 155px button, i.e. no margin at all.
	ok = loadUiFont(_buttonFont, kFontFile, 22) && ok;
	ok = loadUiFont(_pageTitleFont, kFontFile, kPageTitlePx) && ok;
	// See kOptionLabelPx: 20px is what the plate's own arithmetic allows,
	// not a preference.
	ok = loadUiFont(_optionFont, kFontFile, kOptionLabelPx) && ok;
	ok = loadUiFont(_tokenFont, kMonoBoldFontFile, kTokenPx) && ok;
	ok = loadUiFont(_devFont, kFontFile, 16) && ok;
	_fontsLoaded = ok;
	if(!_fontsLoaded){
		ofLogError(kTag) << "could not load " << kFontFile << " or " << kMonoFontFile
			<< " at one or more sizes — labels will not draw";
	}

	// The page header's real height, from the face that draws it — one
	// line of title, then the step dots on their OWN line (2026-08-25,
	// developer's instruction; see kStepDotsRowGapPx), then the gap to
	// the info box below.
	//
	// **This is the one place the dots' height enters the layout**, and
	// it has to agree term-for-term with drawPageHeader's own dotsY, or
	// the box below is measured against a header that is not the one
	// being drawn. Same terms, same order, both derived from the same
	// two font metrics.
	//
	// Measured rather than declared, because everything below the header
	// is derived from it: get this wrong high and the info box silently
	// loses a line of the note; get it wrong low and the dots run into
	// the box's first line.
	if(_pageTitleFont.isLoaded()){
		_pageHeaderPx = _pageTitleFont.getAscenderHeight()
			+ fabsf(_pageTitleFont.getDescenderHeight())
			+ kStepDotsRowGapPx + kStepDotRadiusPx * 2.0f
			+ kPageHeaderGapPx;
	}

	// The cart's reserved detail column, measured once from the worst case
	// rather than per row — see kCartDetailWorstCase for why the per-row
	// version was the truncation bug.
	if(_cartDetailFont.isLoaded()){
		_cartDetailColPx =
			_cartDetailFont.getStringBoundingBox(kCartDetailWorstCase, 0, 0).width;
	}
	// **Logged, not assumed.** The reserve above was reasoned to be
	// comfortable and the developer still photographed "Button Mus..." on
	// the table, which means one of these three numbers was not what this
	// file thought it was. Printing them at boot is how the next report
	// gets diagnosed from the log instead of from arithmetic done here.
	if(_cartRowFont.isLoaded() && _cartDetailFont.isLoaded()){
		const float nameSpace = kCartWidthPx - 2.0f * kCartPadXPx
			- _cartDetailColPx - kCartRowMidGapPx;
		ofLogNotice(kTag) << "cart row: detail column " << _cartDetailColPx
			<< "px reserved, " << nameSpace << "px left for the name";
		// Every catalogue name has to fit that space. oF cannot read the
		// catalogue, so the widest one is measured against whatever core
		// actually sends the first time a cart is drawn — see drawCart.
		if(nameSpace < kCartMinNameSpacePx){
			ofLogWarning(kTag) << "cart name column is only " << nameSpace
				<< "px — under the " << kCartMinNameSpacePx
				<< "px the longest catalogue name needs, so names WILL be"
				<< " truncated; widen kCartWidthPx or shrink kCartDetailPx";
		}
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
		// 925 -> 937 (2026-08-25): hover.py's BUTTONS_TOP_PX is derived,
		// not chosen — it centres the row in the near margin — so
		// shrinking BUTTON_H_PX from 100 to 76 moved it down by half the
		// difference. Re-derive this literal from that file's own
		// arithmetic, never by eye, whenever either number moves.
		const float kHoverButtonsTopPx = 937.0f;   // core/hover.py BUTTONS_TOP_PX
		if(cartBottomPx() > kHoverButtonsTopPx){
			ofLogWarning(kTag) << "cart bottom measures " << cartBottomPx()
				<< "px, below core/hover.py's button band at " << kHoverButtonsTopPx
				<< "px — the Confirm/Cancel buttons will overlap the total";
		}
		// The developer's own constraint, 2026-08-24: the cart "should fit
		// with the near row." kCartFooterHeightPx is a reserved budget
		// guessed ahead of the font metrics (it has to be — these are
		// namespace constants), and this is where the guess is checked
		// against what the total actually measures.
		if(cartBottomPx() > kNearRowBottomPx){
			ofLogWarning(kTag) << "cart bottom measures " << cartBottomPx()
				<< "px, past the near row's own bottom edge at " << kNearRowBottomPx
				<< "px — raise kCartFooterHeightPx";
		}
		// And the info box's own band, for the same reason: its content is
		// laid out from real font metrics but its height is derived from
		// constants, so a font change could silently overflow it. The
		// shape mirrors drawInfoBox's own steps exactly — name line, the
		// tapered rule, the diet line, then kInfoBoxNoteMaxLines of note.
		const float infoBodyLineH = _infoFont.getAscenderHeight()
			+ fabsf(_infoFont.getDescenderHeight()) + kInfoBoxLineGapPx;
		const float infoContent = kInfoBoxPadYPx * 2.0f
			+ _infoNameFont.getAscenderHeight()
			+ fabsf(_infoNameFont.getDescenderHeight()) + kInfoBoxLineGapPx
			+ kRuleThickPx + kInfoBoxLineGapPx * 2.0f
			+ std::max(_infoFont.getAscenderHeight()
					+ fabsf(_infoFont.getDescenderHeight()),
				_infoKcalFont.getAscenderHeight()
					+ fabsf(_infoKcalFont.getDescenderHeight()))
			+ kInfoBoxLineGapPx * 1.5f
			+ infoBodyLineH * (float)kInfoBoxNoteMaxLines;
		// **Measured against the TIGHTER of the two bands** — every screen
		// in the ordering sequence puts a page header above the box, so
		// they get `_pageHeaderPx` less than a bare table does. Checking
		// the roomy one would pass while the broth screen overflowed,
		// which is exactly what happened for one build.
		const float tightest = kInfoBoxHeightPx - _pageHeaderPx;
		ofLogNotice(kTag) << "info box: header " << _pageHeaderPx
			<< "px, content " << infoContent << "px, band " << tightest << "px";
		if(infoContent > tightest){
			ofLogWarning(kTag) << "info box content (name + rule + diet + "
				<< kInfoBoxNoteMaxLines << " note lines) measures " << infoContent
				<< "px in a " << tightest << "px band (the header takes "
				<< _pageHeaderPx << "px of " << kInfoBoxHeightPx
				<< "px) — it will overflow; shrink kInfoBoxTextPx, "
				<< "kInfoBoxLineGapPx or kInfoBoxPadYPx";
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

void UiLayer::drawFadedRule(float x, float y, float widthPx,
	float thickPx, const ofColor & colour, int peakAlpha){
	// A horizontal rule at CONSTANT thickness whose alpha is strongest at
	// the centre and fades to nothing at both ends. See the declaration
	// in UiLayer.h for the two reports that shaped it.
	//
	// Sliced 1px at a time, and the width is what fades — never the
	// height. An earlier version tapered both and clamped the height to a
	// 1px floor, which turned the ends into a dashed line of stubs.
	//
	// The falloff is 1 - t*t, the same quadratic drawHalo's own bands use,
	// so this rule and the bin halos are visibly the same kind of light.
	if(peakAlpha <= 0 || thickPx <= 0.0f || widthPx <= 0.0f){
		return;
	}
	const int slices = std::max(1, (int)ceilf(widthPx));
	for(int i = 0; i < slices; i++){
		const float u = ((float)i + 0.5f) / (float)slices;   // 0..1
		const float t = fabsf(2.0f * u - 1.0f);              // 0 centre, 1 ends
		const float taper = 1.0f - t * t;
		const int a = (int)((float)peakAlpha * taper);
		if(a <= 0){
			continue;
		}
		ofSetColor(colour, a);
		ofDrawRectangle(x + (float)i, y, 1.0f, thickPx);
	}
	ofSetColor(255);
}

float UiLayer::breath(float floor01, float phase){
	// One sine, one clock, one period — the bins' halos and the buttons'
	// glows both come through here so the whole table breathes together
	// rather than in two rhythms a diner can see fighting.
	//
	// Returns floor01..1. The FLOOR is the load-bearing parameter: at 0 a
	// thing disappears at the bottom of every breath, which reads as
	// broken rather than as alive (the bins' first rig photo, which is
	// why kHaloBreathPeriodS' own comment raised theirs from 0.1 to
	// 0.35). A control a diner has to find gets a much higher one still.
	const float amp = 1.0f - ofClamp(floor01, 0.0f, 1.0f);
	return ofClamp(floor01, 0.0f, 1.0f) + amp * 0.5f
		* (1.0f + sinf(TWO_PI * ofGetElapsedTimef() / kWidgetBreathPeriodS + phase));
}

void UiLayer::drawFlame(float cx, float cy, float sizePx, const ofColor & body){
	// doc §11.4's pointer, redrawn 2026-08-25 — see kCursorFlameHPx's own
	// comment. A silhouette, not an illustration, same reasoning the old
	// chilli glyph this file used to also draw (deleted 2026-08-25, same
	// day — the spice screen no longer shows one, see drawOptionPlate's
	// own comment) argued for at small sizes: a rounded base tapering to a
	// tip that leans off-centre, one fill, no second tone. The lean is
	// what keeps this reading as a flickering flame instead of an inert
	// teardrop or a raindrop pointing the wrong way.
	if(sizePx <= 0.0f){
		return;
	}
	const float h = sizePx;
	const float w = sizePx * 0.66f;
	const float tipY = cy - h * 0.5f;
	const float baseY = cy + h * 0.5f;

	ofPath path;
	path.setFilled(true);
	path.setFillColor(body);
	path.setCircleResolution(48);
	// Left shoulder: tip down to the rounded base.
	path.moveTo(cx + w * 0.06f, tipY);
	path.bezierTo(cx - w * 0.40f, tipY + h * 0.34f,
		cx - w * 0.50f, baseY - h * 0.34f,
		cx - w * 0.18f, baseY);
	// The rounded bottom.
	path.bezierTo(cx, baseY + h * 0.10f,
		cx + w * 0.22f, baseY + h * 0.06f,
		cx + w * 0.30f, baseY - h * 0.06f);
	// Right shoulder: bulges further than the left on its way back up to
	// the tip, which is what pulls the tip off dead-centre.
	path.bezierTo(cx + w * 0.46f, baseY - h * 0.30f,
		cx + w * 0.30f, tipY + h * 0.40f,
		cx + w * 0.06f, tipY);
	path.close();
	path.draw();
}

void UiLayer::drawRoundedRectFill(const ofRectangle & r, float cornerRadiusPx,
	const ofColor & colour){
	ofPath path;
	path.setFilled(true);
	path.setFillColor(colour);
	path.setCircleResolution(48);
	path.rectRounded(r, std::min(cornerRadiusPx,
		std::min(r.width, r.height) * 0.5f));
	path.draw();
}

void UiLayer::drawGlow(const ofRectangle & r, float cornerRadiusPx,
	float reachPx, int bands, const ofColor & colour, int peakAlpha){
	// drawHalo's own falloff, off the bins: alpha is brightest at the
	// shape's edge and falls off QUADRATICALLY outward, so the glow reads
	// as light coming off the thing rather than as a stack of outlines
	// around it. Contiguous bands (thickness == pitch), which is the fix
	// the halo itself needed on its first rig look — gapped bands read as
	// noise, not as a gradient.
	if(bands <= 0 || reachPx <= 0.0f || peakAlpha <= 0){
		return;
	}
	const float pitch = reachPx / (float)bands;
	for(int i = 0; i < bands; i++){
		const float t = 1.0f - ((float)i + 0.5f) / (float)bands;
		const int a = (int)((float)peakAlpha * t * t);
		if(a <= 0){
			continue;
		}
		drawRoundedBand(r, (float)i * pitch, (float)(i + 1) * pitch,
			ofColor(colour, a), cornerRadiusPx);
	}
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
	// 0.30 floor, not the first attempt's near-zero — see
	// kHaloBreathPeriodS's own comment: a bin dimmed almost to nothing
	// read as broken, not as breathing, in the first photo. (This is the
	// same curve as the `0.65 + 0.35 * sin` it was written as before
	// 2026-08-25, algebraically identical — it goes through the shared
	// `breath` helper now so the buttons' new glow and the bins' halo
	// cannot drift into two different rhythms.)
	const float breathe = breath(0.30f, _haloPhase[i]);
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
	// **A hovered WIDGET outranks a hovered bin** (M6). On the BROTH and
	// SPICE screens the diner is choosing between options, not between
	// bins, and the option they are pointing at is the thing the box is
	// about. A bin cannot be hovered on those screens anyway — the
	// pointer is in the centre column — but the ordering is stated here
	// rather than left to that coincidence.
	// **Three sources, in this order: hovered widget, SELECTED widget,
	// hovered bin.** The middle one is new (2026-08-25) and is what the
	// developer meant by "when the progress fills the selection is locked
	// even without hover. then the info als remains locked."
	//
	// Without it the box emptied the instant the hand left the plate the
	// diner had just chosen, so the one screen where they most want to
	// re-read what they picked was the one screen that would not show it.
	// Hover still outranks selection, because a hand moving to a second
	// broth is asking about that one — the box follows the question, and
	// falls back to the answer when there is no question.
	InfoContent content;
	bool haveContent = false;
	for(const StateLink::Widget & w : state.widgets){
		if(w.hover && w.hasInfo){
			content.name = w.label;
			content.diet = w.diet;
			content.meta = w.meta;
			content.desc = w.desc;
			haveContent = true;
			break;
		}
	}
	if(!haveContent){
		for(const StateLink::Widget & w : state.widgets){
			if(w.selected && w.hasInfo){
				content.name = w.label;
				content.diet = w.diet;
				content.meta = w.meta;
				content.desc = w.desc;
				haveContent = true;
				break;
			}
		}
	}
	if(!haveContent){
		for(int i = 0; i < 8 && i < (int)state.bins.size(); i++){
			if(state.bins[i].hl == "hover" && !state.bins[i].diet.empty()){
				content.name = state.bins[i].label;
				content.diet = state.bins[i].diet;
				content.meta = state.bins[i].meta;
				content.desc = state.bins[i].desc;
				haveContent = true;
				break;
			}
		}
	}
	if(haveContent){
		// Held, not cleared, when nothing is active — the box needs
		// something to draw while it fades out. See _info's own comment.
		_info = content;
	}
	_infoFade.setTarget(haveContent ? 1.0f : 0.0f);
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
		const float tx = rightX - bb.width - bb.x;
		ofSetColor(kCartTotalValueColor);
		_totalNumFont.drawString(text, tx, baselineY);
	}
	ofSetColor(255);
}

void UiLayer::drawPageHeader(const StateLink::Screen & screen) const {
	// The title, then the step dots on their own line under it. Both are
	// core's (I2) — this draws whatever arrived and looks nothing up, so
	// a second locale is a JSON edit and no C++.
	//
	// An empty title draws nothing at all, which is how an idle table
	// gets no header rather than an empty strip: core sends "" on every
	// screen that is not part of the ordering sequence.
	//
	// **Under the title, not beside it** — developer, 2026-08-25: "it is
	// better to have itsown line instead of same line as the statement
	// discribing the page." See kStepDotsRowGapPx for what that cost and
	// where the space came from; the short version is that the title is
	// now genuinely centred, where the inline version centred the
	// title-plus-dots GROUP and so pushed the title itself off-centre by
	// a different amount on every screen.
	if(screen.title.empty() || !_pageTitleFont.isLoaded()){
		return;
	}
	const float cx = mmToPxX(TABLE_W_MM * 0.5f);
	const float baseline = kInfoBoxTopPx + _pageTitleFont.getAscenderHeight();

	const ofRectangle tb = _pageTitleFont.getStringBoundingBox(screen.title, 0, 0);
	ofSetColor(kPageTitleColor);
	_pageTitleFont.drawString(screen.title, cx - tb.width * 0.5f - tb.x, baseline);

	// The dots: filled up to and including the current step, hollow
	// after. A diner who can see how many steps there are and that they
	// are on the second one knows the table is not about to charge them
	// — which is most of what makes a kiosk feel safe to poke at, and it
	// is the thing kiosk guidance means by "logical flow and step
	// progression".
	//
	// Never a "2 / 5" numeral: this table is read at a distance and by
	// people who are not necessarily reading English, and the dots carry
	// the same fact with no reading at all.
	//
	// `screen.steps` is core's, not a constant here — it went 3 -> 5 on
	// 2026-08-25 (the payment and token screens are steps too) and this
	// function needed no change for it, which is the point of sending it.
	if(screen.steps > 0){
		const float pitch = kStepDotRadiusPx * 2.0f + kStepDotGapPx;
		const float dotsW = pitch * (float)(screen.steps - 1)
			+ kStepDotRadiusPx * 2.0f;
		// Own line: clear of the title's descender by kStepDotsRowGapPx,
		// then half a dot down to reach the centre. Same terms as
		// setup()'s `_pageHeaderPx`, in the same order — they measure and
		// draw the same block and must not drift apart.
		const float dotsY = baseline
			+ fabsf(_pageTitleFont.getDescenderHeight())
			+ kStepDotsRowGapPx + kStepDotRadiusPx;
		const float first = cx - dotsW * 0.5f + kStepDotRadiusPx;
		for(int k = 0; k < screen.steps; k++){
			const float x = first + pitch * (float)k;
			if(k < screen.step){
				ofSetColor(kAccentInk);
				ofDrawCircle(x, dotsY, kStepDotRadiusPx);
			}
			else {
				// Hollow, drawn as an annulus rather than a stroked
				// circle — ofPath's stroke is glLineWidth in disguise on
				// this renderer (drawAnnulus' own comment).
				drawAnnulus(x, dotsY, kStepDotRadiusPx,
					kStepDotRadiusPx - 2.0f, ofColor(kAccentInk, 110));
			}
		}
	}
	ofSetColor(255);
}

void UiLayer::drawInfoBox(const StateLink::State & state,
	float topPx, float heightPx) const {
	// VISUAL_LAYER.md §8/§9 build item 10. "Info box sits ABOVE the cart,
	// fixed height, does not push the cart down. Idle: invisible. No fill,
	// no border. Not an empty bordered box."
	//
	// **Direction A, chosen by the developer off the design canvas
	// 2026-08-24** — the text-forward one, after five directions were put
	// on a table-simulating canvas together. What that settled, and what
	// changed here from the rounded pink card this replaced:
	//   - no fill, no border, no panel: type on the table background, the
	//     same as the plate labels and the cart (see kInfoBoxTextColor's
	//     block above);
	//   - the item's NAME still leads, with kcal RIGHT-ALIGNED on the same
	//     line and set larger than the body — "i think the kcal/100g is
	//     too thin to read in option a implement it";
	//   - one faded rule under that pair (drawFadedRule, and the two
	//     reports in its declaration);
	//   - the trivia line is gone from the wire entirely. What is left is
	//     one note about what the ingredient is LIKE, because the diner
	//     picks here and the kitchen cooks — see pricing.Item.description.
	// Every vertical step is laid out from the previous line's own font
	// metrics, and setup() measures the total against the band.
	//
	// The band is reserved unconditionally by kCartTopPx' own arithmetic,
	// so "does not push the cart down" is true by construction rather than
	// by this function being careful — nothing here can move anything.
	const float fade = _infoFade.get();
	if(fade <= 0.005f || _info.name.empty()){
		return;
	}
	// **Never over a banner.** The two share this band, and doc §14.5's
	// precedence rule settles it: the state that changes what the table is
	// DOING outranks everything else in the centre column. In practice a
	// banner and a hover almost never coincide (setting mode disables
	// MediaPipe; an uncalibrated table has no homography to hit-test
	// with) — the one case that does is `error`, which is raised while
	// SERVING, and that is exactly the case worth being explicit about
	// rather than trusting to a coincidence.
	// `qr` is added to that list for M6: the CHECKOUT screen owns this
	// whole band (drawCheckout), and a leftover info box from the bin the
	// diner's hand happened to be over would sit on top of the code they
	// are trying to scan.
	if(state.overlayKind == "uncalibrated" || state.overlayKind == "error"
		|| state.overlayKind == "qr" || state.mode == "setting"){
		return;
	}
	const InfoContent & b = _info;

	const float cx = mmToPxX(TABLE_W_MM * 0.5f);
	const ofRectangle box(cx - kCartWidthPx * 0.5f, topPx,
		kCartWidthPx, heightPx);

	// One alpha for the rule and every glyph — §8 fades the box as one
	// thing, and staggering them would read as a rendering fault rather
	// than as a transition. There is no fill or panel to fade any more.
	const float a01 = ofClamp(fade, 0.0f, 1.0f);
	const int a = (int)(255.0f * a01);

	if(!_infoFont.isLoaded() || !_infoNameFont.isLoaded()
		|| !_infoKcalFont.isLoaded()){
		ofSetColor(255);
		return;
	}
	const float leftX = box.x + kInfoBoxPadXPx;
	const float rightX = box.x + box.width - kInfoBoxPadXPx;
	const float textWidth = box.width - 2.0f * kInfoBoxPadXPx;
	// ascender+descender, NOT getLineHeight(): oF's line height for this
	// face runs about 1.8x the point size (measured — the first build of
	// this box overflowed its band by 37px and setup()'s own check
	// caught it), which is generous leading for a paragraph and far too
	// airy for six lines that have to share one panel. Every other
	// vertical step in this function is built the same way.
	const float bodyLineH = _infoFont.getAscenderHeight()
		+ fabsf(_infoFont.getDescenderHeight()) + kInfoBoxLineGapPx;
	float y = box.y + kInfoBoxPadYPx;   // the TOP of the next block, never a baseline

	// Line 1 — the item's name, ALONE on its own full-width line, in the
	// plate's own ink so the two read as the same label seen twice.
	//
	// **The chosen mockup put kcal on this line, right-aligned, and it
	// does not survive the real font.** Measured (PIL/FreeType, the real
	// .ttf and the real catalogue): the widest name, "Button Mushrooms",
	// is 320px of the 452px available at 30px — and kcal, once set large
	// enough to answer "too thin to read", takes 206px of it. No pairing
	// of readable sizes fits both on one line; at 24px name + 21px kcal
	// it still overflows. So the number moved down to the diet line,
	// where it has 104px to spare, rather than the name being shrunk or
	// clipped to make room. Truncating was not an option — see
	// kInfoBoxNoteMaxLines.
	const float nameBaseline = y + _infoNameFont.getAscenderHeight();
	ofSetColor(kInfoBoxNameColor, a);
	_infoNameFont.drawString(truncateToWidth(_infoNameFont, b.name, textWidth),
		leftX, nameBaseline);
	y += _infoNameFont.getAscenderHeight()
		+ fabsf(_infoNameFont.getDescenderHeight()) + kInfoBoxLineGapPx;

	// The rule, tapered — thick at the centre, gone at both ends. Filled
	// slices, never a stroke: `ofPath::setStrokeWidth()` IS
	// `ofSetLineWidth()` on this renderer and is driver-capped at 1px
	// (this file's halo and plate-ring comments both carry the finding).
	drawFadedRule(leftX, y, textWidth, kRuleThickPx, kAccentInk,
		(int)(kRuleAlpha * a01));
	y += kRuleThickPx + kInfoBoxLineGapPx * 2.0f;

	// The diet dot and word. The dot is not decoration and is never
	// alone: it is paired with the word for the same reason I8 says a
	// state is never carried by colour by itself, and this is the one
	// line on the table somebody may actually act on.
	//
	// **Drawn only when there IS one.** A spice level is not food and has
	// nothing to say about diet, so its info carries an empty `diet` and
	// this line is simply the meta on its own — never a blank dot, which
	// would read as an answer nobody gave.
	const float dietBaseline = y + _infoFont.getAscenderHeight();
	if(!b.diet.empty()){
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
		ofDrawCircle(dotCx, dietBaseline - _infoFont.getAscenderHeight() * 0.35f,
			kInfoDietDotRadiusPx);
		ofSetColor(dietColour, a);
		_infoFont.drawString(dietWord,
			dotCx + kInfoDietDotRadiusPx + kInfoDietDotGapPx, dietBaseline);
	}

	// kcal, right-aligned against the diet mark — the two things a diner
	// weighs a choice against, on one line. Set larger than the note
	// below it (kInfoBoxKcalPx): "i think the kcal/100g is too thin to
	// read in option a implement it." It keeps the diet line's own
	// baseline rather than getting a line of its own, so the box is still
	// four lines tall and the band's arithmetic is unchanged.
	if(!b.meta.empty()){
		ofRectangle kb = _infoKcalFont.getStringBoundingBox(b.meta, 0, 0);
		ofSetColor(kInfoBoxKcalColor, a);
		_infoKcalFont.drawString(b.meta, rightX - kb.width - kb.x, dietBaseline);
	}
	// The taller of the two faces on this line drives the step, so a
	// future size change to either cannot silently overlap the note.
	y += std::max(_infoFont.getAscenderHeight()
			+ fabsf(_infoFont.getDescenderHeight()),
		_infoKcalFont.getAscenderHeight()
			+ fabsf(_infoKcalFont.getDescenderHeight()))
		+ kInfoBoxLineGapPx * 1.5f;

	// The note: what this ingredient is LIKE, so the diner can choose it.
	// Wrapped, never clipped — "no text should get truncated" — with the
	// line cap set as headroom over the real worst case rather than as
	// the mechanism. See kInfoBoxNoteMaxLines.
	ofSetColor(kInfoBoxTextColor, a);
	for(const std::string & line
			: wrapToLines(_infoFont, b.desc, textWidth, kInfoBoxNoteMaxLines)){
		_infoFont.drawString(line, leftX, y + _infoFont.getAscenderHeight());
		y += bodyLineH;
	}
	ofSetColor(255);
}

void UiLayer::drawCart(const StateLink::State & state) const {
	// Same centre column as drawBrandMark/drawBanner (the pot gap), same
	// fixed top (kCartTopPx — see that constant's own comment on why it
	// does not move when the mode banner appears/disappears).
	const float cx = mmToPxX(TABLE_W_MM * 0.5f);
	const float x = cx - kCartWidthPx * 0.5f;

	const float rowsBottom = kCartRowsBottomPx;
	const float dividerY = rowsBottom + kCartDividerGapPx;
	const float totalTop = dividerY + kCartBorderWidthPx + kCartDividerGapPx;
	const float totalBaselineY = totalTop + _totalNumFont.getAscenderHeight();

	// No panel fill and no border — see kCartBorderColor's own comment
	// above. The cart is now text on the table background, the same as
	// every plate label, and the only rule left on it is the divider
	// above the total.

	// **The cart grows UPWARD from the divider.** Developer, 2026-08-24:
	// "let the cart grow from the bottom, as u add more stuff, the older
	// cart items gets pushed upwards." So the newest bound slot always
	// sits in the last row, directly above the total, and the list pushes
	// up as it fills — a receipt printing towards the reader rather than a
	// list filling a form from the top.
	//
	// This deliberately overrides doc §8's "the SAME slot updates in place
	// — it never moves": a bin's row DOES move now, upward, when a later
	// bin joins the cart. What §8's rule was protecting is that a row
	// never jumps around as its own numbers change, and that still holds —
	// _cartSlotBin's pick order is untouched, so the only thing that ever
	// moves a row is another item arriving.
	//
	// Doc §8's other half is untouched: "Slots are blank at startup. No
	// name, no placeholder text, no icon, no border. Just reserved empty
	// space" — the rows above the filled ones draw nothing at all.
	std::vector<int> drawn;
	for(int k = 0; k < 8; k++){
		int binIdx = _cartSlotBin[k];
		if(binIdx < 0 || binIdx >= (int)state.bins.size()){
			continue;
		}
		if(!state.bins[binIdx].resolved){
			continue;
		}
		drawn.push_back(binIdx);
	}

	for(size_t k = 0; k < drawn.size(); k++){
		const int binIdx = drawn[k];
		const StateLink::Bin & b = state.bins[binIdx];
		const BinTween & tw = _bins[binIdx];
		// Bottom-anchored: the last entry lands on the last row whatever
		// `drawn.size()` is, so nothing below the cart ever moves.
		const float rowBottom = rowsBottom
			- (float)(drawn.size() - 1 - k) * kCartRowHeightPx;
		const float baselineY = rowBottom - kCartRowHeightPx * 0.5f
			+ _cartRowFont.getAscenderHeight() * 0.5f;

		// Same "%dg  <price>" composition as drawBin's own post-pick
		// detail line (doc §13.4), so the two never disagree about how a
		// pick is worded — one bin's picked amount, read in two places.
		char g[16];
		snprintf(g, sizeof(g), "%dg", (int)roundf(tw.picked.get()));
		std::string detail = std::string(g) + "  " + _priceText(tw.price.get());
		ofRectangle detailBb = _cartDetailFont.getStringBoundingBox(detail, 0, 0);

		// The name column is measured against the RESERVED width of the
		// detail column, never against this row's own detail string — see
		// kCartDetailWorstCase for why that difference is the whole of the
		// "names still get truncated when the weight goes double digit"
		// report.
		const float nameMaxWidth = kCartWidthPx - 2.0f * kCartPadXPx
			- _cartDetailColPx - kCartRowMidGapPx;
		// **Reported once per name, not per frame.** If a name ever does
		// have to be cut, the log says which and by how much — the last
		// two truncation reports both cost a rebuild to diagnose because
		// nothing recorded the actual widths.
		const float nameW = _cartRowFont.getStringBoundingBox(b.label, 0, 0).width;
		std::string name = b.label;
		if(nameW > nameMaxWidth){
			name = truncateToWidth(_cartRowFont, b.label, nameMaxWidth);
			if(_truncatedNames.insert(b.label).second){
				ofLogWarning(kTag) << "cart: \"" << b.label << "\" needs "
					<< nameW << "px but the name column is " << nameMaxWidth
					<< "px — truncated";
			}
		}

		ofSetColor(kPlateNameColor);
		_cartRowFont.drawString(name, x + kCartPadXPx, baselineY);

		ofSetColor(kCartRowDetailColor);
		_cartDetailFont.drawString(detail,
			x + kCartWidthPx - kCartPadXPx - detailBb.width - detailBb.x, baselineY);
	}

	// Tapered, matching the info box's own rule — one kind of divider on
	// this table, not two. Was a flat grey #C9C5BC bar until 2026-08-24.
	drawFadedRule(x + kCartPadXPx, dividerY, kCartWidthPx - 2.0f * kCartPadXPx,
		kRuleThickPx, kAccentInk, kRuleAlpha);
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
	const float totalTop = kCartRowsBottomPx + kCartDividerGapPx
		+ kCartBorderWidthPx + kCartDividerGapPx;
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
	// `qr` (M6) asks the same question: the state that changes
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
	ofColor ink = kWidgetSecondary;
	if(!w.enabled){
		ink = kWidgetDisabled;
	}
	else if(w.style == "primary"){
		ink = kWidgetPrimary;
	}
	else if(w.style == "danger"){
		// The Cancel button. A style of its own rather than "secondary"
		// grey — I8 wants a state carried by hue, and "this discards your
		// order" is not the same statement as "this is the lesser of two
		// buttons." Since 2026-08-25 it is a MUTED clay rather than a
		// fire-engine red; see kWidgetDanger.
		ink = kWidgetDanger;
	}
	else if(w.style == "option"){
		// M6's broth and spice plates. Neutral ink, selected or not — a
		// fourth hue here would make the SCREEN look like it was carrying
		// state, when the only state on it is which one is chosen.
		//
		// **Selection used to swap this to `kWidgetPrimary` (teal);
		// 2026-08-25, later still, it stopped.** Developer: "dont make it
		// blue like done currently. instead change the border thickness
		// and change the halo colour." Both of those already happen
		// downstream of `ink` (the ring in `drawOptionPlate` goes thicker
		// on `sel`, the glow goes teal-tinted on `sel`) without this
		// value ever needing to change — see that function's own comment.
		ink = kInkColor;
	}

	// **How lit this control is, 0..1 — and the one number the whole
	// "breathing halo" report comes down to.**
	//
	//   hovered        1.0, steady. A control under the hand must not
	//                  pulse; the step from breathing to solid is itself
	//                  the "yes, this one" feedback, and it lands before
	//                  the dwell ring has moved at all.
	//   selected       1.0, steady. A locked-in choice is a fact, not an
	//                  invitation.
	//   enabled        breathing, on the same sine and the same clock as
	//                  the bin halos (see `breath`).
	//   disabled       0. The glow is what says "this is live", and I8
	//                  forbids carrying that in brightness alone — so the
	//                  hue goes grey with it rather than the glow merely
	//                  dimming.
	float glow01 = 0.0f;
	if(w.enabled){
		glow01 = (w.hover || w.selected) ? 1.0f
			: breath(kWidgetBreathFloor);
	}

	if(w.kind == "option"){
		drawOptionPlate(w, ink, glow01);
		return;
	}

	// **A rounded RECTANGLE, not a pill** (kWidgetCornerPx's own comment
	// carries the report). The glow is drawHalo's falloff (drawGlow) so a
	// lit button and a lit bin are the same effect at two sizes.
	const float corner = std::min(kWidgetCornerPx,
		std::min(box.width, box.height) * 0.5f);
	const bool primary = w.enabled && w.style == "primary";
	const int glowPeak = primary ? kWidgetPrimaryGlowAlpha : kWidgetGlowAlpha;
	const int fillPeak = primary ? kWidgetPrimaryFillAlpha : kWidgetFillAlpha;

	if(glow01 > 0.0f){
		drawGlow(box, corner, kWidgetGlowReachPx, kWidgetGlowBands,
			glowTint(ink), (int)(glowPeak * glow01));
	}
	drawRoundedRectFill(box, corner,
		ofColor(ink, w.enabled ? (int)(fillPeak * (0.55f + 0.45f * glow01))
			: kWidgetFillAlpha / 2));

	// Dwell progress, drawn INSIDE the button. The cursor's own ring
	// (drawCursor) already shows the same fraction, but it sits under the
	// diner's hand — which is exactly where a hand is while dwelling — so
	// on the rig it read as no feedback at all (developer, 2026-08-24:
	// "no progress of hover was shown"). `dwell` is core's 0..1 fraction;
	// oF still times nothing (doc §9.4).
	//
	// **It fills LEFT to RIGHT**, which was forced by the old pill shape
	// (a partial-height rounded rect on a pill's bottom edge pokes its
	// corners out through the pill's curve) and is kept now that the
	// shape is a rounded rect, because left-to-right is what a progress
	// bar does everywhere else a diner has seen one. The clamp to the
	// button's own corner is what keeps the sweep inside the frame.
	if(w.enabled && w.dwell > 0.0f){
		const float fillW = box.width * ofClamp(w.dwell, 0.0f, 1.0f);
		if(fillW > 1.0f){
			drawRoundedRectFill(ofRectangle(box.x, box.y, fillW, box.height),
				corner, kWidgetDwellFill);
		}
	}

	const float ringX = mmToPxX(kWidgetRingMM);
	const float ringY = mmToPxY(kWidgetRingMM);
	// drawRing frames the rect from OUTSIDE it, the same annulus rule the
	// plates follow (§14.4), so the label inside is never touched by its
	// own frame however thick the frame becomes. Passing `corner` here is
	// what makes the frame follow the rounded corner instead of squaring
	// it off.
	drawRing(box, ringX, ringY, ink, corner);

	// Dark ink on a light field (§13.4) — and a disabled button's label is
	// greyed rather than hidden, because a button whose label vanished
	// would read as a rendering fault rather than as unavailable. An
	// enabled label takes the frame's own colour: all three live inks are
	// dark enough for §13.4 on their own, and a coloured frame around
	// near-black text carries the hue in a hairline only, which was half
	// of the original "washed out" report.
	ofSetColor(w.enabled ? ink : kWidgetDisabled);
	const ofTrueTypeFont & face =
		_buttonFont.isLoaded() ? _buttonFont : _nameFont;
	drawCentered(face, w.label, box.getCenter().x,
		box.getCenter().y + face.getAscenderHeight() * 0.5f);
	ofSetColor(255);
}

void UiLayer::drawOptionPlate(const StateLink::Widget & w, const ofColor & ink,
	float glow01) const {
	// A broth or a spice option — the two draw identically now,
	// 2026-08-25, later still. Developer: "no need chilli icon, no need
	// slider which was never implemented, instead a 2 button was
	// implemented, remove that and follow exactly what is done with
	// broth do the same for spice boxes as well. just 3 boxes." The
	// chili-strip cell and the vertical-slider layout it grew into
	// (`icon == "chilli"`, `hover.spice_layout_rects`) are both gone —
	// `hover.spice_widgets` now lays out full-height cards through
	// `hover.broth_card_rects`, the exact function `hover.broth_widgets`
	// already used, so this one card style is the whole function.
	const ofRectangle box(w.x, w.y, w.w, w.h);
	const float corner = std::min(kWidgetCornerPx,
		std::min(box.width, box.height) * 0.5f);
	const bool sel = w.selected && w.enabled;

	// **Selection is a halo colour now, not a card colour.** Developer:
	// "when a broth or spicy button gets selected, dont make it blue like
	// done currently. instead change the border thickness and change the
	// halo colour." `ink` (the fill/ring/name colour below) stays neutral
	// regardless of `sel` — see drawWidget's own `style == "option"`
	// branch — and only the GLOW reaches for `kWidgetPrimary`, so a
	// locked-in choice reads as "this one is glowing teal" rather than
	// "this whole card turned blue."
	const ofColor haloInk = sel ? kWidgetPrimary : ink;
	if(glow01 > 0.0f){
		drawGlow(box, corner, kWidgetGlowReachPx, kWidgetGlowBands, glowTint(haloInk),
			(int)((sel ? kOptionSelectedGlowAlpha : kWidgetGlowAlpha) * glow01));
	}
	// A selected plate is FILLED, not merely outlined. Developer,
	// 2026-08-25: "when the progress fills the the selection is locked
	// even without hover." A lock has to be readable across the table
	// with no hand near it, and a slightly brighter outline is not.
	drawRoundedRectFill(box, corner,
		ofColor(ink, w.enabled
			? (int)((sel ? kOptionSelectedFillAlpha : kWidgetFillAlpha)
				* (0.55f + 0.45f * glow01))
			: kWidgetFillAlpha / 2));

	if(w.enabled && w.dwell > 0.0f){
		const float fillW = box.width * ofClamp(w.dwell, 0.0f, 1.0f);
		if(fillW > 1.0f){
			drawRoundedRectFill(ofRectangle(box.x, box.y, fillW, box.height),
				corner, kWidgetDwellFill);
		}
	}

	// The border THICKNESS is the other half of the selection signal —
	// unchanged by this pass, `kOptionSelectedRingMM` is already nearly
	// twice `kWidgetRingMM` (see that constant's own comment).
	const float ringMM = sel ? kOptionSelectedRingMM : kWidgetRingMM;
	drawRing(box, mmToPxX(ringMM), mmToPxY(ringMM), ink, corner);

	// The broth screen's own card style, 2026-08-25. Developer: "there is
	// no info box, instead the whole button is inlarged to contain the
	// info about respective brothes, so u can use the complete vertical
	// space above the next button row... also the coloured circle infront
	// of the broth name has to be removed." `hover.broth_widgets` lays
	// out one FULL-WIDTH row per broth (`hover.broth_card_rects`),
	// spanning the band the shared info box used to occupy plus the old
	// option row's own band — `UiLayer::draw` skips `drawInfoBox`
	// entirely on the broth screen (see that call site), so this card is
	// the ONLY place a broth's diet/note reach the table now. No swatch
	// (the old `kOptionSwatchFrac` circle is gone with it —
	// `parseHexColor`/`w.swatch` are no longer read here at all), no icon
	// column, no tick, and — 2026-08-25, later still — no spice-level
	// row either: the chilli gauge this card's meta slot used to draw is
	// gone (developer: "completely remove the spice icon or words in the
	// broth boxes"), so `w.meta` is no longer read here at all.
	//
	// **The note's line count is SOLVED from the card's own remaining
	// height, not a fixed budget.** `drawInfoBox` can get away with a
	// fixed `kInfoBoxNoteMaxLines` because there is only ever one shared
	// box; this function draws N cards of whatever height `hover.py`
	// divided the band into for however many broths (or spice levels) the
	// menu holds today, and that count has already changed once this
	// session (4 -> 3). A card this function was never measured against
	// must still be unable to overflow its own box.
	if(!_infoFont.isLoaded() || !_infoNameFont.isLoaded()){
		ofSetColor(255);
		return;
	}
	const float padX = kInfoBoxPadXPx;
	const float leftX = box.x + padX;
	const float textWidth = box.width - 2.0f * padX;
	const float bodyLineH = _infoFont.getAscenderHeight()
		+ fabsf(_infoFont.getDescenderHeight()) + kBrothCardNoteLineGapPx;
	float y = box.y + kBrothCardPadYPx;

	// The name — `_optionFont` (20px), not `_infoNameFont` (32px, sized
	// for a bin's shorter catalogue name): measured against the real
	// three broths at this card's own width, "Mushroom Vegan Broth" is
	// the worst case and clears this font/width pair with ~30px to
	// spare, where 32px would not — "measured, not guessed," learned the
	// hard way earlier this session.
	const ofTrueTypeFont & nameFace =
		_optionFont.isLoaded() ? _optionFont : _nameFont;
	const float nameBaseline = y + nameFace.getAscenderHeight();
	std::string name = w.label;
	const float nameW = nameFace.getStringBoundingBox(name, 0, 0).width;
	if(nameW > textWidth){
		name = truncateToWidth(nameFace, name, textWidth);
		if(_truncatedNames.insert(w.label).second){
			// Shared by the broth screen's cards AND, since 2026-08-25,
			// the spice screen's description cards (`hover.spice_widgets`)
			// — both draw through this same branch, so the id is what
			// tells the two apart in a log.
			ofLogWarning(kTag) << "info card " << w.id << " (\""
				<< w.label << "\") needs " << nameW
				<< "px but the card is " << textWidth
				<< "px wide — truncated";
		}
	}
	// The name's own ink no longer switches on `sel` either — see this
	// function's own comment on `haloInk` above.
	ofSetColor(w.enabled ? kInfoBoxNameColor : kWidgetDisabled);
	nameFace.drawString(name, leftX, nameBaseline);
	y += nameFace.getAscenderHeight() + fabsf(nameFace.getDescenderHeight())
		+ kInfoBoxLineGapPx;

	// Diet dot + word — the exact pair `drawInfoBox` draws and the exact
	// reason (I8: never a state by colour alone).
	const float dietBaseline = y + _infoFont.getAscenderHeight();
	if(!w.diet.empty()){
		ofColor dietColour = kInfoDietEggColor;
		std::string dietWord = "EGG";
		if(w.diet == "veg"){
			dietColour = kWidgetPrimary;
			dietWord = "VEG";
		}
		else if(w.diet == "nonveg"){
			dietColour = kWidgetDanger;
			dietWord = "NON-VEG";
		}
		const float dotCx = leftX + kInfoDietDotRadiusPx;
		ofSetColor(dietColour);
		ofDrawCircle(dotCx, dietBaseline - _infoFont.getAscenderHeight() * 0.35f,
			kInfoDietDotRadiusPx);
		_infoFont.drawString(dietWord,
			dotCx + kInfoDietDotRadiusPx + kInfoDietDotGapPx, dietBaseline);
	}
	// **No meta row any more, 2026-08-25, later still.** Developer:
	// "completely remove the spice icon or words in the broth boxes." This
	// used to draw either a chilli-gauge row (`spiceLevelFromMeta`) or the
	// raw `w.meta` text right-aligned on the diet line — both deleted;
	// `w.meta` is simply not read by this function any more.
	y += _infoFont.getAscenderHeight() + fabsf(_infoFont.getDescenderHeight())
		+ kBrothCardNoteLineGapPx;

	// The note fills whatever is left of the card.
	const float remaining = (box.y + box.height - kBrothCardPadYPx) - y;
	const size_t maxLines = remaining >= bodyLineH
		? (size_t)(remaining / bodyLineH) : 0;
	ofSetColor(kInfoBoxTextColor);
	for(const std::string & line
			: wrapToLines(_infoFont, w.desc, textWidth, maxLines)){
		_infoFont.drawString(line, leftX, y + _infoFont.getAscenderHeight());
		y += bodyLineH;
	}
	ofSetColor(255);
}

void UiLayer::drawWidgets(const StateLink::State & state) const {
	for(const StateLink::Widget & w : state.widgets){
		drawWidget(w);
	}
}

void UiLayer::drawCheckout(const StateLink::State & state) const {
	// doc §18.1's CHECKOUT screen and §18.2's payment mock. The whole
	// purpose is that a diner can scan the code off the projected plywood
	// with their own phone, so this is sized for a camera at arm's length
	// rather than for a reader at three metres.
	//
	// **Two screens in one function, and which one is showing is decided
	// by `qr.token` rather than by `qr.paid`.** Developer, 2026-08-25:
	// "the token number should be given only after sucessfull payment."
	// Core leaves the token empty until the money has landed (see
	// StateLink::Qr::token), so this side has no way to draw a number
	// early even by mistake — the rule lives on the wire, not in a
	// condition here that a later edit could invert.
	//
	//   UNPAID   the QR, small, on a white plate, with the total.
	//   PAID     the token, big, and no QR — the code has done its job
	//            and a scannable QR left up beside a paid order is an
	//            invitation to scan it again.
	const StateLink::Qr & qr = state.qr;
	const float cx = mmToPxX(TABLE_W_MM * 0.5f);

	// The band the cart would have occupied. The page header
	// (drawPageHeader) has already had the strip above it, so this screen
	// starts where the info box would.
	const float bandTop = kInfoBoxTopPx + _pageHeaderPx;
	const float bandBottom = kCartRowsBottomPx + kCartFooterHeightPx;

	if(!qr.token.empty()){
		// --- paid ---------------------------------------------------
		// The token, and nothing competing with it. This is the one thing
		// the diner carries away from the table.
		const ofTrueTypeFont & big =
			_tokenFont.isLoaded() ? _tokenFont : _totalNumFont;
		if(!big.isLoaded()){
			return;
		}
		const float blockH = big.getAscenderHeight()
			+ fabsf(big.getDescenderHeight());
		// Centred as a GROUP, token plus hint, rather than the token
		// being centred and the hint hanging off the bottom of it.
		const bool haveHint = _infoFont.isLoaded()
			&& !state.screen.hint.empty();
		const float hintH = haveHint
			? 18.0f + _infoFont.getAscenderHeight()
				+ fabsf(_infoFont.getDescenderHeight())
			: 0.0f;
		const float y = bandTop
			+ (bandBottom - bandTop - blockH - hintH) * 0.5f;
		ofSetColor(kAccentInk);
		drawCentered(big, qr.token, cx, y + big.getAscenderHeight());
		if(haveHint){
			ofSetColor(kInfoBoxTextColor);
			drawCentered(_infoFont, state.screen.hint, cx,
				y + blockH + 18.0f + _infoFont.getAscenderHeight());
		}
		ofSetColor(255);
		return;
	}

	// --- unpaid: the code to scan -------------------------------------
	//
	// **The quiet zone is drawn, not assumed.** A QR needs a margin of
	// blank around it to be found at all, and this table's background is
	// not blank — the fluid layer is underneath and the halos reach in
	// from the bins. So a white plate goes down first, at full strength,
	// exactly like the light-pass cutouts do for the same reason (I9).
	//
	// **Sized to kQrTargetSidePx, not to the space available.** The old
	// version filled whatever was free, which is how it ended up 592px
	// wide in a 554px column and ran onto the trays — and how it ended up
	// asking a diner to hold their phone further away the more room the
	// layout happened to have. See kQrTargetSidePx for both faults.
	const int n = (int)qr.modules.size();
	float y = bandTop;
	if(n > 0){
		// Solve for the module size that lands the WHOLE code — n modules
		// plus two 4-module quiet zones — on the target side. Floored to
		// a whole pixel so every module is the same width: a scanner
		// tolerates a smaller code far better than one whose columns
		// alternate 5px and 6px from rounding.
		const float module = std::max(1.0f,
			floorf(kQrTargetSidePx / ((float)n + 2.0f * kQrQuietModules)));
		const float quiet = module * kQrQuietModules;
		const float side = module * (float)n + 2.0f * quiet;
		const float qx = cx - side * 0.5f;
		// Centred as a GROUP — code, total, hint — so the block sits in
		// the middle of the band rather than the code being centred and
		// the two lines hanging off the bottom of it.
		const float totalBlockH = _totalNumFont.isLoaded()
			? _totalNumFont.getAscenderHeight()
				+ fabsf(_totalNumFont.getDescenderHeight())
			: 0.0f;
		const float hintBlockH = (_infoFont.isLoaded()
				&& !state.screen.hint.empty())
			? _infoFont.getAscenderHeight()
				+ fabsf(_infoFont.getDescenderHeight()) + kQrCaptionGapPx
			: 0.0f;
		const float contentH = side + kQrCaptionGapPx + totalBlockH + hintBlockH;
		y = bandTop + std::max(0.0f, (bandBottom - bandTop - contentH) * 0.5f);

		ofSetColor(255, 255, 255);
		ofDrawRectangle(qx, y, side, side);
		ofSetColor(kInkColor);
		for(int r = 0; r < n; r++){
			const std::vector<bool> & row = qr.modules[r];
			for(int c = 0; c < (int)row.size(); c++){
				if(!row[c]){
					continue;
				}
				// Drawn one module at a time rather than as a merged
				// path: that is ~400 quads, and a scanner cares far more
				// about crisp module edges than this costs to draw.
				ofDrawRectangle(qx + quiet + (float)c * module,
					y + quiet + (float)r * module, module, module);
			}
		}
		y += side + kQrCaptionGapPx;
	}

	// The total, under the code — the number the diner is about to pay,
	// which is the one fact they should be able to check before they
	// scan. Set at the cart's own total size and colour, so it reads as
	// the same number they were looking at one screen ago.
	if(_totalNumFont.isLoaded() && !qr.totalText.empty()){
		ofSetColor(kCartTotalValueColor);
		drawCentered(_totalNumFont, qr.totalText, cx,
			y + _totalNumFont.getAscenderHeight());
		y += _totalNumFont.getAscenderHeight()
			+ fabsf(_totalNumFont.getDescenderHeight()) + kQrCaptionGapPx;
	}

	// "Scan with your phone camera." The one instruction on the screen,
	// last and quietest — a diner who already knows what a QR is never
	// reads it, and the one who does not has nowhere else to find out.
	// Core resolves the wording (I2); an empty hint draws nothing.
	if(_infoFont.isLoaded() && !state.screen.hint.empty()){
		ofSetColor(kInfoBoxTextColor);
		drawCentered(_infoFont, state.screen.hint, cx,
			y + _infoFont.getAscenderHeight());
	}
	ofSetColor(255);
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

	drawFlame(cx, cy, kCursorFlameHPx, kCursorColor);
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

		// **The centre column is a stack of PAGES now, and exactly one of
		// them is up at a time.** Developer, 2026-08-25: "the broth should
		// come like a second page of the selection with an option to go
		// back to cart. now it overlays the cart and it is teribble."
		//
		// It overlaid because this function used to draw the cart on
		// every screen but CHECKOUT, while core sent option widgets whose
		// rects (core/hover.py's old BAND_TOP_PX..BAND_BOTTOM_PX)
		// straddled the info box AND the cart — so four broth plates
		// landed on top of a cart that was still being drawn underneath
		// them. Two changes fixed it together: the option rects moved
		// into the cart's own band (core/hover.py's `_cart_band_px`), and
		// the cart stops drawing on the screens that are not the cart.
		//
		// The band above stays what it always was — the info box — on
		// every page EXCEPT the two option pages, which is the other half
		// of the same instruction: "the top info area should be left to
		// there for broth info and in spicy page, spice info." On the
		// option pages a page header takes the top of that band
		// (drawPageHeader) and the box moves down by exactly its height —
		// or, since 2026-08-25, does not draw at all (see `optionPage`).
		const bool optionPage = state.phase == "broth" || state.phase == "spice";
		// **Neither option page shares the info box any more, 2026-08-25.**
		// Broth stopped first — developer: "there is no info box, instead
		// the whole button is inlarged to contain the info about
		// respective brothes." `hover.broth_widgets` lays each broth's own
		// card across the info box's old band AND the option row's own
		// band combined (`hover.broth_card_rects`) — `drawOptionPlate`
		// draws the name/diet/note directly into that card, so drawing
		// the shared info box on top of it would
		// either duplicate the same text or (since nothing is ever hovered
		// on a card that fills its own band) draw nothing into a reserved
		// strip the broth cards have already grown into.
		//
		// Spice followed the same day, same reason, and — after a same-day
		// vertical-slider detour that got reverted — landed on exactly
		// broth's own shape: `hover.spice_widgets` now lays out one
		// full-height card per level through `hover.broth_card_rects`,
		// and that card is exactly what the shared info box used to draw
		// for whichever ONE level was hovered — now all three show at
		// once, so the old single-level box would be redundant at best.
		const bool payPage = state.overlayKind == "qr";
		// **A banner outranks a header**, the same precedence doc §14.5
		// sets for this column and the same one drawInfoBox already
		// follows: the state that changes what the table is DOING wins
		// over anything else here. Without this an `error` overlay raised
		// mid-order (which happens while SERVING) would draw
		// "SCALES OFFLINE" and "Choose Your Broth" on top of each other.
		const bool bannerUp = state.overlayKind == "uncalibrated"
			|| state.overlayKind == "error" || state.mode == "setting";
		// **`headed` is one condition and the header/box move together.**
		// An earlier cut had the header drawing whenever core sent a
		// title but the box only stepping down on the option pages, which
		// put "Your Order" straight through the top of the info box on
		// the cart screen. They are the same fact and are read from the
		// same bool now.
		const bool headed = !state.screen.title.empty() && !bannerUp;

		if(headed){
			drawPageHeader(state.screen);
		}
		if(!payPage && !optionPage){
			// The payment page owns the whole band below the header —
			// there is no hovered item to describe on it, and a leftover
			// info box from a bin the diner's hand drifted over would sit
			// on top of the code they are trying to scan. (drawInfoBox
			// refuses on `overlayKind == "qr"` too; this is the same
			// answer stated at the call site rather than left to that.)
			// Broth and spice both own that band now too — see
			// `optionPage`'s own comment.
			const float header = headed ? _pageHeaderPx : 0.0f;
			drawInfoBox(state, kInfoBoxTopPx + header,
				kInfoBoxHeightPx - header);
		}

		if(payPage){
			drawCheckout(state);
		}
		else if(!optionPage){
			// The cart is the diner's receipt up to the moment they leave
			// it. On the option pages the same band holds the options
			// instead — that is what makes broth a page rather than an
			// overlay.
			drawCart(state);
		}
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
