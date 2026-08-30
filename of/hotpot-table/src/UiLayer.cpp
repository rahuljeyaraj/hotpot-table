#include "UiLayer.h"
#include "TableGeometry.h"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <sstream>

namespace {
	const char * kTag = "UiLayer";

	// Doc §13.4: load each font at its final display size — projected text
	// scaled up at draw time is mud. Inter, the doc's specified `en` face,
	// is not present in this repo or the oF distribution; DejaVuSans-Bold
	// ships in bin/data/fonts/ and satisfies the rule underneath the font
	// choice (dark ink on a light field, set bold). Swap this one line if
	// the specified face is ever added.
	const std::string kFontFile = "fonts/DejaVuSans-Bold.ttf";

	// The plate rate line's face. DejaVuSansMono is the same family as
	// kFontFile above (Bitstream Vera/DejaVu, permissively licensed) and
	// gives two things at once: a genuine regular weight, so the rate line
	// is not bold-on-bold against the name above it, and monospace digits
	// so a price line's width does not twitch as the digits change on a
	// pick.
	const std::string kMonoFontFile = "fonts/DejaVuSansMono.ttf";

	// A regular proportional weight, so the table has a hierarchy rather
	// than drawing every heading, label, note and caption at one weight.
	//
	// The rule is the ordinary typographic one. BOLD is for things read at
	// a distance or read first: a plate's name, the info box's name, a
	// button, the total's figure. REGULAR is for prose and for anything the
	// eye should land on second: the info box's note, cart row names, the
	// total's label. MONO is for numbers that must not jitter as their
	// digits change: the plate rate, cart amounts. Same DejaVu family
	// throughout, so this is one voice at three weights rather than three
	// typefaces arguing.
	const std::string kRegularFontFile = "fonts/DejaVuSans.ttf";
	const std::string kMonoBoldFontFile = "fonts/DejaVuSansMono-Bold.ttf";

	// Doc §17.1's `zh` face. None of the four DejaVu files above carry a
	// single CJK glyph, so a fifth file is unavoidable. Unlike the DejaVu
	// family there is no matching bold/mono/regular set for it, only this
	// one weight (Google Fonts, OFL, the variable font's default instance),
	// so every role in loadFonts() converges on this one file when the
	// locale is zh — see that function's comment.
	const std::string kCjkFontFile = "fonts/NotoSansSC-Regular.ttf";

	// The banner headline and subline, and the widget label. Doc §13.4
	// specifies 36px and 26px; these are smaller because catalogue names
	// mostly do not fit at 36px. The bin plate has its own sizes — see
	// kPlateNamePx and kPlateRatePx below.
	const int kNamePx = 28;
	const int kDetailPx = 22;

	// The plate name, at the size where every one of the catalogue's real
	// display names either fits a 200mm bin (252px) on one line or wraps
	// cleanly to two — measured against the real font and the real
	// catalogue with PIL/FreeType, not eyeballed. VISUAL_LAYER.md §3's
	// palette named 40px, at which names overflow the bin and run into the
	// paired bin's name.
	//
	// The catalogue's `names` field is the single source of the text: core
	// sends the full display name and oF wraps it here (see drawBin's wrap
	// call below). There is deliberately no shortened-label field.
	const int kPlateNamePx = 28;
	// The plate rate line. Smaller than the doc's 26px because a mono
	// font's cap-height runs bigger relative to its nominal size than a
	// proportional face's: at 26px, DejaVuSansMono's measured ink height
	// (25px) is TALLER than the 28px bold name's (21px), so the nominally
	// smaller number draws as the visually bigger line. Re-run the same
	// PIL/FreeType measurement rather than guessing if either the face or
	// the size changes again.
	const int kPlateRatePx = 20;
	// #2B2118, VISUAL_LAYER.md §3's palette table exactly.
	const ofColor kPlateNameColor(43, 33, 24);
	// Not the doc's #B8781A amber: that has too high a red-channel share
	// (184:120:26) and reads as RED on this projector rather than as
	// yellow or gold, because the rig's warm white balance pushes it
	// further that way. Deliberately independent of the doc's Halo-idle
	// entry, which still lists the old amber. Unconfirmed by a rig photo.
	const ofColor kPlateRateColor(0xE6, 0x7E, 0x22);

	// VISUAL_LAYER.md §4's fixed plate height. The doc's starting 130px
	// does not hold once the name is allowed to wrap to two lines —
	// setup()'s check measures the real worst case at ~133px — so this
	// carries headroom rather than warning on every boot. setup() verifies
	// it against the actual loaded font metrics.
	const float kPlateHPx = 140.0f;

	// --- VISUAL_LAYER.md §4/§6: the idle halo -------------------------------
	// haloRect is binRect inflated by this margin. The bands are
	// CONTIGUOUS — thickness equals pitch, no gap — which reads as a smooth
	// gradient; gapped bands read as separate slivers and, on the projected
	// table, as a faint noisy smudge rather than a halo.
	const float kHaloMarginPx = 14.0f;
	// The doc asks for ~16 nested rounded-rect strokes, each 2-3px further
	// out; this uses 24 rings at 1.5px pitch (equal to thickness, so they
	// are contiguous) for a smoother gradient over roughly the same total
	// span, 36px against 40px.
	const int kHaloRingCount = 24;
	const float kHaloRingPitchPx = 1.5f;
	const float kHaloRingThicknessPx = 1.5f;
	// Not §3's "Halo — idle #B8781A", which projects as muddy brown on this
	// rig, and not the #FFC800 between them, which reads as orange. This
	// rig's warm-shifted white balance means an amber has to be pushed
	// noticeably brighter and greener than looks right off-projector to
	// land as amber on the table; (255,235,0) is near the top of what still
	// reads as amber or gold rather than as a flat yellow. §3 still lists
	// the original; sync it once a photo confirms this.
	const ofColor kHaloIdleColor(0xFF, 0xEB, 0x00);
	// A slow breathing sine on alpha. The doc gives no period; 3s is slow
	// enough to read as breathing rather than as a strobe. drawHalo's floor
	// is well above zero so a bin never reads as fully faded out
	// mid-breath, which otherwise leaves several bins looking dead at once
	// whenever they happen to be near their low point together.
	const float kHaloBreathPeriodS = 3.0f;
	// The halo's outward reach (14px to 14+24*1.5=50px from the bin edge)
	// is not small next to how close the plate's rate line sits on the same
	// axis (drawBin's ringTop/ringBottom, roughly 19px out before the rate
	// line's clearance and ascender stack further beyond it). The two were
	// tuned independently and may overlap on the near/far axis. Doc §4 is
	// explicit that the halo wraps the BIN ONLY, never the plate, so if it
	// reaches into the plate's text the fix belongs here — kHaloMarginPx or
	// the ring span — not in drawBin's clearance.

	// --- VISUAL_LAYER.md §4/§6: the active fire ring -----------------------
	// fireRect is binRect inflated by FIRE_RING. Inner
	// edge matches the halo's own margin on purpose — the fire ring picks
	// up right where the halo's innermost band sits, so the crossfade
	// (drawHalo's fireFade, fireEmitters()'s intensity — the same spring)
	// never leaves a visible gap or a double-covered sliver between the two
	// as one fades and the other fades in.
	const float kFireRingInnerPx = kHaloMarginPx;
	const float kFireRingOuterPx = 52.0f;

	// Label clearance and line gap. Defined here rather than in
	// TableGeometry.h, which is kept for CAD geometry only.
	const float kLabelClearanceMM = 10.0f;
	const float kLabelLineGapMM = 4.0f;

	// Nothing draws a ring of this width: the idle halo occupies that
	// visual role, and VISUAL_LAYER.md §3's palette lists only the
	// halo/fire pair. It survives as a named constant because drawBin's
	// label positions measure their clearance from where that ring's outer
	// edge sat (see ringRestY), and dropping it would pull every label
	// closer to the cutout as a side effect.
	const float kRingMM = 6.0f;

	// doc §13.4: dark ink on a light field, set bold. The field is
	// near-white by construction (I9's white floor), so text has to win on
	// stroke weight rather than on brightness. Near-black rather than pure
	// 0,0,0 — full black on a face this bold at these sizes reads harsh.
	const ofColor kInkColor(20, 20, 20);

	// The fault overlay (`state.overlay.kind == "error"`), set by core when
	// a bin that was billing off real weight goes dark — doc §9.5: no
	// billing occurs from the frozen reading. Reuses the staff view's fault
	// palette (its --red #e05d5d and the dark-red-on-red ink of its red
	// pip, #2a0000) so the same failure reads the same way on both surfaces
	// rather than inventing a second red for this table.
	const ofColor kErrorBannerFill(224, 93, 93);   // #e05d5d
	const ofColor kErrorBannerInk(42, 0, 0);       // #2a0000

	// Setting mode's banner: doc §14.5's persistent strip along the top
	// edge. Amber for the same reason the error banner is red — it is the
	// staff view's --amber (#e8b33d) and the ink of its amber pip
	// (#2a1f00), so the header chip on the tablet and the strip on the
	// table are visibly the same statement. Per I8, modes are distinguished
	// by HUE and never by brightness, so this hue is luminance-matched to
	// the red rather than being brighter or dimmer than it.
	const ofColor kSettingBannerFill(232, 179, 61);   // #e8b33d
	const ofColor kSettingBannerInk(42, 31, 0);       // #2a1f00

	// `overlay.kind == "uncalibrated"`, doc §9.1's first-boot state.
	// A THIRD hue rather than reusing amber or red,
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

	// The brand mark sits ABOVE the banner in the same centre column and
	// never shares its strip — see drawBrandMark and drawBanner's yTop.
	// The top margin is clearance from the table's far edge; the gap is
	// breathing room between the mark's bottom and the banner's top when
	// both are up.
	//
	// These two gaps, `kStepDotsRowGapPx` below and `kPageTitlePx` are
	// tuned together: the header block (mark, page title, step dots) has to
	// read as three separate things rather than one crowded stack, and the
	// room comes from the title's size rather than from the mark, which
	// keeps its height because shrinking the one graphic element would make
	// the header smaller rather than airier.
	const float kBrandHeightPx = 170.0f;
	const float kBrandTopMarginPx = 20.0f;
	const float kBrandBannerGapPx = 26.0f;

	// --- VISUAL_LAYER.md §8/§9: the cart panel -----------------------------
	// Lives in the same centre column as the brand mark and the mode
	// banner (drawBrandMark/drawBanner's gapLeftMM/gapRightMM — the pot
	// gap, the one horizontal span with no bin in it), stacked below both.
	//
	// The cart has NO panel fill and no border, which extends doc §4's rule
	// for the plate — no fill, no border, text sitting directly on the
	// table background — to the cart, against the §3 palette rows for
	// "Cart panel fill" and "Cart border". `kCartBorderColor` survives
	// because the divider above the total still uses it — that one is a
	// rule in a receipt, not a container around it.
	const ofColor kCartBorderColor(0xC9, 0xC5, 0xBC);    // #C9C5BC
	const ofColor kCartRowDetailColor(0x6E, 0x6A, 0x62); // #6E6A62

	// --- the table's accent ink, and why it is not gold --------------------
	// Gold does not work as INK on this table, and the reason is arithmetic
	// rather than taste. Two separate failures rule out the whole family:
	//
	//   - A warm hue slides to red. #B8781A sits at hue ~35 degrees, and
	//     this rig's projector has warm-shifted a 35-degree ink into "red"
	//     twice — the plate rate line first, then this.
	//   - A hue cool enough to survive that shift is too light to read.
	//     #FFEB00 (kHaloIdleColor's hue) has relative luminance ~0.808
	//     against the table background's ~0.792, a contrast ratio of about
	//     1.02. It works on a bin because a halo is light spilling onto the
	//     field, not text read against it; as a GLYPH it is invisible.
	//
	// This is a deep TEAL instead. It is far from the projector's warm
	// shift, so it cannot slide toward red the way every amber has; it is
	// nowhere near the green of Confirm or the red of Cancel; and it is
	// dark enough to read as ink on #E8E6E1 — relative luminance ~0.13
	// against the field's ~0.79, a contrast ratio near 6:1, where the best
	// gold managed 3.3.
	const ofColor kAccentInk(0x0E, 0x6B, 0x78);
	// Not the doc's original "Total value" hex — see kAccentInk above on
	// why no gold survives this projector.
	const ofColor kCartTotalValueColor = kAccentInk;
	// The total has NO glow, deliberately. drawGlow emits nested
	// ROUNDED-RECT bands around a bounding box, which reads as a halo
	// around a bin — where the thing inside really is a rectangle — and as
	// an inexplicable box around a number, where it is not.

	// The rule above the total, and the one inside the info box: a plain
	// alpha fade at CONSTANT thickness.
	//
	// Tapering the height as well as the alpha breaks it. The core clamps
	// to a 1px minimum, so the last stretch at each end becomes a row of
	// 1px stubs whose alpha has already rounded to nothing — a dashed line
	// rather than a fade.
	const float kRuleThickPx = 2.0f;
	const int kRuleAlpha = 150;
	// Smaller than §3's 26px for both cart-row columns, because at 26px the
	// widest catalogue name ("Button Mushrooms") measures 279px and the
	// detail column's worst case ("500g  $17.50") 191px — 486px of content
	// against the doc's 460px panel, so every long name truncates and loses
	// its tail. Measured with PIL/FreeType against the real .ttf and the
	// real catalogue, not guessed. Both the size and the panel width moved
	// (see kCartWidthPx), because either alone was marginal. §3 is not
	// edited to match; confirm on a photo first.
	const int kCartRowPx = 21;
	// The grams/price column, mono. Smaller than the name it sits beside
	// because mono's cap height runs larger for the same nominal size
	// (measured, PIL/FreeType against the real .ttf), so equal numbers here
	// would not look equal — the same trap as kPlateRatePx.
	const int kCartDetailPx = 17;
	// Wide enough that the name column clears the 280px the longest
	// catalogue name needs — setup()'s check measures it, and the margin
	// here is only a few px, which is exactly the kind of thing that fails
	// silently without that check. MIRRORED in core/hover.py's
	// CART_WIDTH_PX, from which the buttons and the option plates derive.
	// Still inside the 554px centre column.
	const float kCartWidthPx = 520.0f;
	// Smaller than the doc's 44px so the cart fits alongside the near row.
	// Eight 44px rows plus the footer run 394px, which cannot fit the near
	// row's 301mm band however it is positioned; at 32px the rows are 256px
	// and the whole cart is ~348px, which sits inside the band plus the
	// empty 50mm gap above it. 32px still clears the row font's ink height
	// (21px ascender + 6px descender).
	const float kCartRowHeightPx = 32.0f;
	const float kCartBorderWidthPx = 2.0f;   // filled bars, not ofSetLineWidth
	                                          // — see the halo's own comment
	                                          // above on why a stroke width
	                                          // is unusable on this rig.
	const float kCartPadXPx = 20.0f;
	const float kCartRowMidGapPx = 16.0f;    // name column <-> detail column
	const float kCartDividerGapPx = 12.0f;   // rows -> divider -> total

	// The right-hand column is a FIXED width, measured once at setup() from
	// this string.
	//
	// Sizing the name column against whatever the detail column measures
	// THAT ROW does not work: "5g  $0.18" leaves the name plenty of room
	// and "125g  $4.50" takes it away again, so a name that fits at 5 grams
	// loses its tail at 125. A column whose width depends on the number in
	// it is a column that moves, and the thing beside it pays for it.
	//
	// Reserving the worst case costs the same 160px on every row and can
	// never move: 500 - 2*20 pad - 16 gap - 160 leaves 284px for the name,
	// against the widest catalogue name's 238px. `truncateToWidth` stays as
	// the net, and should never fire.
	const char * kCartDetailWorstCase = "999g  $99.99";
	// oF measures text WIDER than PIL/FreeType does for the same string in
	// the same face at the same size, and any column sized from the PIL
	// number alone comes out too narrow on the actual table.
	//
	// The ratio is measured, not estimated. setup() logs both numbers at
	// every boot:
	//
	//     "999g  $99.99" at 17px DejaVuSansMono
	//         PIL 120.0px      oF 168.0px      ratio 1.400
	//
	// Cross-checked against "Button Mushrooms" at 21px regular:
	// PIL 197.0 x 1.400 = 275.8, against the 274-280 kCartMinNameSpacePx
	// was set to by hand. Two independent faces, two sizes, one ratio.
	//
	// This is the number to size any new text column against. It is not
	// magic — oF measures a bounding box where PIL reports an advance, and
	// the two differ by bearings and by oF's atlas padding — but it is
	// stable enough across these faces to design with, and every use of it
	// is still backed by a runtime warning rather than trusted outright.
	const float kOfWidthRatio = 1.400f;
	const float kCartMinNameSpacePx = 280.0f;

	// The cart is anchored UPWARD from the near row's bottom edge, never
	// downward from the banner above it. Growing the block downward leaves
	// the total and the buttons crowding each other at the diner's edge;
	// this derivation puts the cart's last pixel a fixed gap above the near
	// row whatever happens to the info box or the banner, and leaves the
	// whole 209px below the near row free for the buttons.
	//
	// `kCartFooterHeightPx` is the divider gap + rule + gap + the total's
	// ascender-plus-descender block. It is a reserved budget rather than a
	// font measurement, because these are namespace constants and the fonts
	// are not loaded yet; `setup()` measures the real thing and warns if
	// this number is short. Do not shrink it without watching that
	// warning.
	const float kCartFooterHeightPx = 92.0f;
	const float kCartBottomGapPx = 16.0f;
	const float kNearRowBottomPx = mmToPxY(BINS[4].yMM + BIN_H_MM);
	const float kCartRowsBottomPx = kNearRowBottomPx - kCartBottomGapPx
		- kCartFooterHeightPx;
	// Fixed, never a function of whether the mode banner or the info box
	// happens to be showing — doc §8's "never moves" applies as much to
	// appearing as it does to growing.
	const float kCartTopPx = kCartRowsBottomPx - kCartRowHeightPx * 8.0f;

	// --- VISUAL_LAYER.md §8: the info box ----------------------------------
	// The box sits ABOVE the cart at a fixed height and never pushes the
	// cart down. Its band is everything between the brand mark and the
	// cart.
	//
	// It starts where the mode banner starts, and the two never share a
	// frame: the banner only exists when the table is NOT serving, so there
	// is no hover and no info box. `drawInfoBox` refuses to draw while a
	// banner is up rather than relying on that being true, which is also
	// doc §14.5's precedence rule — the state that changes what the table
	// is DOING outranks anything else in the centre column.
	//
	// The height is derived from the cart's own top, so the two cannot be
	// edited into overlapping.
	const float kInfoBoxTopPx = kBrandTopMarginPx + kBrandHeightPx
		+ kBrandBannerGapPx;
	const float kInfoBoxCartGapPx = 12.0f;
	const float kInfoBoxHeightPx = kCartTopPx - kInfoBoxCartGapPx - kInfoBoxTopPx;
	// The item's NAME leads the box. Nothing else on the table says which
	// bin the box is about: the plate's own label is at the far end of a
	// 1.5m table from the reader.
	const int kInfoBoxNamePx = 30;
	// Sized down against the option screens, where the box's band is
	// shorter by the height of the page header and the broth/spice notes
	// need all three of kInfoBoxNoteMaxLines' lines. Measured — see that
	// constant.
	const int kInfoBoxTextPx = 18;
	// The kcal figure, deliberately larger than the body text — see
	// UiLayer.h's _infoKcalFont.
	const int kInfoBoxKcalPx = 22;   // mono now, which reads larger than
	                                  // the same nominal size in the sans
	const int kInfoDietPx = 17;
	const float kInfoBoxPadXPx = 24.0f;
	// Both are tight for the same reason kInfoBoxTextPx is: the line gap
	// appears seven and a half times in the box's height sum — once after
	// the name, twice around the rule, one and a half after the diet line,
	// three inside the note — so a single point off it buys more here than
	// anywhere else on the table.
	//
	// The padding and line gap are the slack that absorbs changes to the
	// header above: whenever `kBrandTopMarginPx`, `kBrandBannerGapPx` or
	// `kStepDotsRowGapPx` grow, this band shrinks, and setup()'s check
	// refuses to ship an overflow. Take it out of the gap and the pad
	// rather than out of a font size — this is the screen the diner reads
	// longest.
	const float kInfoBoxPadYPx = 7.0f;
	const float kInfoBoxLineGapPx = 3.0f;
	// No fill, no border, no panel — the box is type on the table
	// background, the same rule doc §4 sets for the plate and doc §8 sets
	// for this element outright ("Idle: invisible. No fill, no border").
	// What groups it instead is the faded rule and the shared left margin
	// with the cart below it.
	const ofColor kInfoBoxTextColor(0x4A, 0x42, 0x38);   // the note line
	const ofColor kInfoBoxNameColor(0x2B, 0x21, 0x18);   // the plate's own ink
	const ofColor kInfoBoxKcalColor(0x56, 0x4D, 0x3A);
	// The note wraps to at most this many lines, and this is exactly the
	// requirement rather than headroom. The ingredient notes in
	// `catalogue.json` measure two lines, but the broth and spice notes in
	// `menu.json` are longer sentences and the worst of them takes all
	// three. The box's band therefore has to fit three lines, which is what
	// the padding, line gap and text size above are tuned for.
	//
	// Nothing may truncate: `wrapToLines`' ellipsis is the net, and
	// setup()'s check is what says whether the net is about to be needed.
	const int kInfoBoxNoteMaxLines = 3;

	// --- doc §18.1's CHECKOUT screen -------------------------------------
	// The QR's quiet zone, in MODULES, which is the unit the spec states
	// it in — 4 is the standard minimum and going below it is the usual
	// reason a projected code will not scan. Drawn as a white plate under
	// the code (see drawCheckout) because this table's background is
	// never blank.
	const float kQrQuietModules = 4.0f;
	const float kQrCaptionGapPx = 14.0f;

	// The whole code, quiet zone included, is deliberately SMALLER THAN A
	// BIN — 200px is 159mm on the plywood against a bin's 200mm.
	//
	// Bigger is worse, not better: a QR is scanned at the distance where it
	// fills the phone's frame, so a larger projected code makes the diner
	// stand FURTHER back, which on a 1.5m table means leaning away from the
	// thing they are scanning.
	//
	// At 29 modules plus 8 of quiet zone that is a 5px module, ~4mm
	// physical, comfortably above what a phone camera resolves at arm's
	// length. The code is projected at full contrast onto a white plate
	// (below), which is the part that actually decides whether a scan
	// succeeds.
	//
	// Note when resizing that the quiet zone is 8 MODULES wide, not a fixed
	// pixel allowance: solving for a module size against a fixed margin and
	// then laying out `module * n + 2 * (module * 4)` overflows the centre
	// column and puts the code on the trays either side of it.
	const float kQrTargetSidePx = 200.0f;
	// The token, once it exists. Big, because it is the one thing a diner
	// carries away from this screen — and it exists ONLY after payment,
	// which is core's rule, not this file's (see StateLink::Qr::token).
	const int kTokenPx = 88;
	// The two lines under the token. The first gap is larger than the
	// second so the pair reads as ONE block hung off the token rather than
	// as three evenly spaced lines: the token is the thing, the two lines
	// are its caption.
	const float kTokenHintGapPx = 18.0f;
	const float kTokenHintLineGapPx = 8.0f;
	// Line two ("we'll call this number") is a promise, not an
	// instruction; see drawCheckout for why it is not drawn at the same
	// weight as line one.
	const int kTokenHint2Alpha = 170;

	// The veg/non-veg dot. Green and red are the same two the cart's own
	// buttons use (kWidgetPrimary/kWidgetDanger) rather than a third
	// pair — one green and one red on this table, not several. Egg is
	// neither, and gets its own amber rather than being rounded into one
	// of them; see `pricing.VALID_DIETS`' own comment for why the wire
	// carries three values and not two.
	const ofColor kInfoDietEggColor(0xD9, 0x82, 0x2B);
	const float kInfoDietDotRadiusPx = 8.0f;
	const float kInfoDietDotGapPx = 10.0f;

	// The broth card's own, tighter vertical rhythm. Under the shared
	// `kInfoBoxPadYPx`/`kInfoBoxLineGapPx` the longer broth notes overrun
	// `drawOptionPlate`'s card by one short line. That shared rhythm is
	// left alone, since the bins are measured against it, and the broth
	// card takes a smaller pad and note line-gap instead — enough to clear
	// a third note line without shrinking any text.
	const float kBrothCardPadYPx = 7.0f;
	const float kBrothCardNoteLineGapPx = 3.0f;
	// The option card's own note size — see `drawOptionPlate`'s comment on
	// `_cardNoteFont` for why the card does not simply share the info
	// box's `kInfoBoxTextPx`.
	const int kCardNotePx = 16;
	// "No line cap", for the measuring wrap that decides whether the note
	// was truncated. `wrapToLines` reads 0 as "no lines at all", so this
	// has to be a number no menu note can reach rather than zero.
	const size_t kCardNoteLineCap = 64;

	// --- the raw-skeleton diagnostic ---------------------------------------
	// Deliberately its own palette: this has to read as a different thing
	// from a real hand indicator at a glance, since the whole point is
	// telling the two apart on the same table. The same lime and gold
	// pairing the staff view's own landmark overlay uses, so anyone who
	// has looked at that view recognises this one.
	const ofColor kSkeletonLineColor(64, 200, 120, 200);
	const ofColor kSkeletonJointColor(78, 224, 138);
	const ofColor kSkeletonTrackedColor(255, 217, 60);
	const float kSkeletonJointRadius = 4.0f;
	const float kSkeletonTrackedRadius = 7.0f;
	const float kSkeletonLineWidth = 2.0f;
	// backend_mediapipe.py's CURSOR_LANDMARK (index 8) — the tracked point
	// `_to_stage` builds the real cursor from — drawn larger, matching the
	// staff view's own highlight of the same landmark.
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

	// --- the projected buttons ---------------------------------------------
	// A button is drawn the same way a plate is — a filled rect ring with
	// the label inside it — so the two read as one system rather than as a
	// UI pasted onto a table. 5mm rather than the plate's 6mm because a
	// button's ring encloses text rather than a physical hole and a heavier
	// frame starts to compete with its own label.
	const float kWidgetRingMM = 5.0f;

	// A ROUNDED RECTANGLE, not a pill. Half-height corners — the roundest a
	// rect can be — make a lozenge whose end caps are wider than the space
	// the word sits in, which reads as a badge rather than as a button.
	// 18px of radius on a 76px button is about what every kiosk, phone and
	// ticket machine a diner has already used puts on a primary action:
	// unmistakably a button, still soft enough not to fight the fluid and
	// the halos around it.
	//
	// The option plates use the SAME radius rather than a proportional
	// one, so a 520x74 broth plate and a 155x76 Next button read as the
	// same family of control at two sizes. A radius that scaled with the
	// shape would make the wide plates look flatter than the buttons.
	const float kWidgetCornerPx = 18.0f;

	// Deliberately NOT a traffic-light green/red pair. This table has a
	// palette already — a warm near-white field (#E8E6E1), amber halos
	// breathing around the bins, orange fire, and one deep teal accent that
	// is the only ink to survive the projector's warm shift (see
	// kAccentInk). Saturated green and fire-engine red belong to a
	// different design: they read as a web form dropped onto the plywood,
	// and the red competes with the actual fire.
	//
	// What replaces them is a hierarchy rather than two opposed signals,
	// which is what a restaurant kiosk does — the confirming action is the
	// large, bold one, and Cancel and Back are kept smaller and less
	// prominent:
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
	// The dwell sweep, drawn INSIDE a widget (see drawWidget). This is the
	// ONLY place dwell progress is shown; there is no progress ring on the
	// pointer. The alpha is low because this is a tint under dark text.
	const ofColor kWidgetDwellFill(200, 120, 0, 80);

	// A glow drawn in the same dark ink as a button's border reads as a
	// SHADOW, not a halo.
	//
	// The bins' halo never has this problem because it never glows in its
	// own ink: `kHaloIdleColor` is bright and saturated, a hue the ink
	// palette does not otherwise carry, at up to full alpha.
	// `kWidgetPrimary`, `kWidgetDanger` and `kWidgetSecondary` are tuned
	// the opposite way — deliberately dark and muted so they read as ink on
	// a light field — and those are exactly the properties that make a
	// diffuse blur of them look like a drop shadow rather than light.
	//
	// Rather than adding a fourth hex per hue, this pushes the SAME hue
	// toward full brightness and saturation for the glow only, leaving
	// every ink use (text, borders) untouched. A light, saturated version
	// of a colour is unambiguously light; a dark, muted one is not.
	ofColor glowTint(const ofColor & ink){
		ofColor c = ink;
		c.setSaturation(215.0f);
		c.setBrightness(235.0f);
		return c;
	}

	// --- the glow, and why it BREATHES ------------------------------------
	//
	// The reach and band count match the bins' halo: a 24px, 9-band version
	// sits under the visible threshold on this field.
	//
	// The glow runs the same breathing sine as the bins, at the same period
	// and off the same clock, so the whole table breathes once rather than
	// in two rhythms. A button at constant alpha beside breathing bins
	// reads as dead rather than as quiet. `kWidgetBreathFloor` is higher
	// than the halo's floor because a button must never be at its dimmest
	// when a diner first looks for it: the swing is smaller and the floor
	// higher, so it reads as steady-with-a-pulse rather than as fading in
	// and out.
	//
	// Hovering pins it to full and STOPS the breathing. A control the hand
	// is on should be steady rather than pulsing under it, and that step
	// change from breathing to solid is itself the "yes, this one"
	// feedback, before the dwell sweep has moved at all.
	const float kWidgetGlowReachPx = 40.0f;
	const int kWidgetGlowBands = 20;
	const float kWidgetBreathPeriodS = kHaloBreathPeriodS;   // one breath, table-wide
	const float kWidgetBreathFloor = 0.78f;
	// The primary action is louder than the other two, deliberately — see
	// the palette block above on kiosk button hierarchy. These are the
	// peak alphas the breath multiplies.
	const int kWidgetFillAlpha = 26;
	// High because `glowTint` makes the glow LIGHT, and a light colour over
	// #E8E6E1 needs the alpha to read as lit rather than smudged.
	const int kWidgetGlowAlpha = 150;
	const int kWidgetPrimaryFillAlpha = 52;
	const int kWidgetPrimaryGlowAlpha = 205;

	// --- the option plates (broth, spice) ---------------------------------
	// A selection is LOCKED IN and stays locked with no hand near it, so it
	// has to be readable from across the table rather than only under a
	// hover.
	//
	// Selection is signalled by SHAPE and by glow, never by recolouring the
	// card. The ring goes to nearly twice `kWidgetRingMM`
	// (`kOptionSelectedRingMM`, drawOptionPlate) and `drawOptionPlate`
	// tints ONLY the glow with `kWidgetPrimary`; the fill, the ring colour
	// and the name ink stay the plate's ordinary neutral whether it is
	// selected or not. A subtler shade of the same near-white does not
	// carry the difference — it reads as a hairline hue shift rather than
	// as a locked-in state.
	const int kOptionSelectedGlowAlpha = 210;

	// The dwell sweep, and the inverted ink behind it.
	//
	// The sweep is a NEAR-SOLID dark band that carries the text with it:
	// `drawStringLitTo` redraws every string in the lit inks up to the
	// sweep's edge, so the progress is legible instead of blacking the card
	// out as it fills. The residue of the card's own fill underneath keeps
	// the band from reading as a printed black box on a projected surface.
	//
	// The lit inks are off-white rather than #FFFFFF for the same reason
	// the table ground is #E8E6E1: a pure-white glyph on a projector blooms
	// into its neighbours. The note ink stays a step below the name ink,
	// preserving the hierarchy `kInfoBoxNameColor`/`kInfoBoxTextColor` sets
	// on the light side.
	const ofColor kOptionSweepColor(20, 20, 20, 235);
	const ofColor kOptionNameLitColor(0xFB, 0xF9, 0xF5);
	const ofColor kOptionNoteLitColor(0xDC, 0xD6, 0xCC);
	// The moving edge, in the same amber `kWidgetDwellFill` uses on the
	// plain buttons, so a filling card and a filling button still read as
	// one mechanism.
	const ofColor kOptionSweepEdgeColor(200, 120, 0);
	const float kOptionSweepEdgePx = 4.0f;
	// The card's opaque base — Stage's own `kTableBackground`, restated
	// here rather than shared because Stage paints the whole table with it
	// and this paints one card with it, and the two would not want to move
	// together if either ever changed. See `drawWidget`'s own comment.
	const ofColor kCardBaseColor(0xE8, 0xE6, 0xE1);

	// --- the spice card's chilli count ------------------------------------
	// A COUNT, not a gauge: `hover.spice_widgets` sends the number as
	// `icon_count` and `drawOptionPlate` draws exactly that many peppers,
	// right-aligned, with no empty outline peppers behind them.
	//
	// The height is NOT a constant — it is the name's cap height, measured
	// off `nameFace` at draw time, so a pepper is exactly as tall as the
	// word beside it and the two stay in step if the name font is ever
	// resized. This block carries only the proportions.
	//
	// Those proportions are measured off the artwork rather than guessed.
	// In the 512x512 file the opaque pixels run y 0..511 — the FULL height
	// — and x 34..477, so the pepper is centred with equal transparent
	// margins left and right and none at all top or bottom. Two things
	// follow, and both matter:
	//   - drawing the square H tall makes the VISIBLE pepper exactly H
	//     tall. Sizing to the file's box would be sizing to a vertical
	//     margin that is not there.
	//   - the pepper is only 444/512 of the square WIDE, so the strip
	//     arithmetic below must reserve the INK width, not the draw width.
	//     Otherwise the last pepper floats a transparent 8% of its height
	//     short of the card's right pad and the three cards stop reading as
	//     one right-aligned scale.
	const float kChilliWidthFactor = 444.0f / 512.0f;
	const float kChilliGapFactor = 0.26f;  // between peppers, x height

	// --- the idle-table wave prompt ---------------------------------------
	// A waving hand inviting a passer-by to wave back and start. drawIdleHand
	// rotates the one loaded `_idleHandIcon` about its wrist each frame
	// rather than swapping in a second image or rebuilding the shape.
	//
	// Sized well short of the pot-gap column's width (drawBrandMark's
	// gapRightMM - gapLeftMM, 440mm / ~554px) so the swept arc of the wave
	// never grazes a bin either side of it. Centred on the table's true
	// geometric middle (TABLE_W_MM/2, TABLE_H_MM/2): the one point both rows
	// of bins leave clear, and where a hand offered over the table lands.
	const float kIdleHandHeightPx = 220.0f;
	// A hello-wave: side to side, not a full spin — +-kIdleHandWaveDeg about
	// the wrist, one full swing every kIdleHandWavePeriodS.
	const float kIdleHandWaveDeg = 16.0f;
	const float kIdleHandWavePeriodS = 1.4f;
	// "Wave to start" — the prompt's own text, drawn below the icon by
	// drawIdleHand. Size picked to read at the hand icon's own distance
	// (across the room, not arm's reach), same reasoning as
	// kIdleHandHeightPx; not shared with `kPageTitlePx` (23px), which is
	// sized for a header a diner is already standing at the table for.
	const int kIdleHandTextPx = 32;
	const float kIdleHandTextGapPx = 24.0f;   // wrist to the text's cap line
	const ofColor kIdleHandTextColor(0x2B, 0x21, 0x18);   // the plate's own ink
	// The sweep's own fall clock — see `sweep01For`. The sweep rises with
	// the wire value but falls only on this renderer's time: nothing for
	// `kSweepFallDelayS` (long enough to swallow a tracker dropout or a
	// state-message gap, short enough that a diner who moved off does not
	// think the table is still counting), then an ease to the new value
	// over `kSweepFallS` rather than a jump.
	const float kSweepFallDelayS = 0.30f;
	const float kSweepFallS = 0.22f;
	// Broth and spice share ONE card shape: spice draws through exactly the
	// broth-card branch below, and `kOptionLabelPx` (`_optionFont`'s size)
	// is the one constant that branch needs from this block.
	const int kOptionLabelPx = 20;

	// --- the page header (title + step dots) -------------------------------
	// One sentence naming the task, and where the diner is in the
	// sequence. See StateLink::Screen for why this exists at all.
	//
	// Bounded by the 554px centre column, not by the cart: the title is
	// centred and free of the cart's 520px. The longest title core sends
	// ("Choose Your Spice") measures ~389px at 26px as oF measures it, so
	// ~344px at this size — comfortable margin. The size is deliberately
	// below the column's limit, because the header's three elements need
	// the vertical room more than the title needs the extra points.
	const int kPageTitlePx = 23;
	// The header's HEIGHT is measured at setup(), never fixed here — see
	// `_pageHeaderPx`. As a constant it once put 244.2px of content in a
	// 228.5px band, overflowing the info box on exactly the two screens the
	// header exists for. A height guessed ahead of the font metrics is the
	// same class of mistake `kCartFooterHeightPx` carries a warning for.
	const float kPageHeaderGapPx = 10.0f;   // header block -> info box
	// The dots sit UNDER the title, on their own line. A progress indicator
	// under a heading is what every checkout a diner has already used looks
	// like; beside it, the pair has to be centred as a group, which puts
	// the TITLE itself off-centre by half the dots' width — and by a
	// different amount on each screen, since the titles differ in length.
	//
	// Stacking costs ~18px of a band the info box's three-line note already
	// fills, which `kBrandTopMarginPx` and `kBrandBannerGapPx` give back.
	const float kStepDotRadiusPx = 5.0f;
	const float kStepDotGapPx = 20.0f;
	// The title's DESCENDER line -> the dots' top edge. Measured off the
	// descender rather than the baseline so a title with descenders
	// ("Choose Your Spice") cannot reach into the dots — and the
	// descender is a font metric, not a per-string one, so the dots
	// still sit at the same height on every screen.
	//
	// This and the brand margins move together as the header's breathing
	// room; `setup()`'s band check is what says whether they have gone too
	// far. See `kBrandTopMarginPx`.
	const float kStepDotsRowGapPx = 18.0f;
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
	// Pops exactly one whole UTF-8 codepoint off the end. `std::string::
	// pop_back()` alone removes one BYTE, which cuts a multi-byte CJK
	// codepoint in half and hands FreeType a dangling lead byte or a
	// stray continuation byte — not a defined "one character shorter."
	// Without this, a note that needs truncateToWidth's ellipsis fallback
	// comes back with a mangled trailing glyph on top of being cut short.
	// See tokenizeForWrap for the other half of CJK-safe wrapping.
	void popBackUtf8(std::string & s){
		if(s.empty()){
			return;
		}
		s.pop_back();
		while(!s.empty() && ((unsigned char)s.back() & 0xC0) == 0x80){
			s.pop_back();
		}
	}

	std::string truncateToWidth(const ofTrueTypeFont & font, const std::string & text,
		float maxWidthPx){
		if(!font.isLoaded() || font.getStringBoundingBox(text, 0, 0).width <= maxWidthPx){
			return text;
		}
		const std::string ellipsis = "...";
		std::string result = text;
		while(!result.empty()){
			popBackUtf8(result);
			std::string candidate = result + ellipsis;
			if(font.getStringBoundingBox(candidate, 0, 0).width <= maxWidthPx){
				return candidate;
			}
		}
		return ellipsis;
	}

	// The wrap helpers below break a string into "words" at whitespace,
	// which is what English uses to mark a legal line break and what
	// Chinese does not have at all. A zh note carries no spaces, so a
	// whitespace tokenizer reads a whole sentence as ONE word, leaves the
	// wrap nothing to break on, and lets the line overflow into the
	// ellipsis — the text is then truncated rather than wrapped.
	//
	// This is not a special case for Chinese; it is what CJK line-breaking
	// allows. Almost any character boundary is a legal break point, so ONE
	// CJK CHARACTER IS ITS OWN TOKEN, exactly as breakable as an English
	// word. An ASCII run still tokenizes on whitespace, so English text —
	// and the ASCII half of a mixed string like "0级" or a page title —
	// wraps unchanged.
	bool tokenIsCjk(const std::string & token){
		return !token.empty() && (unsigned char)token[0] >= 0x80;
	}

	// A label carrying both an ASCII letter/digit and a non-ASCII byte —
	// today, only the Language button's literal "EN | 中文" (hover.py's
	// own comment on why it is never translated per-locale). Used by
	// `drawWidget` to route a button's label through
	// `drawBilingualCenteredLitTo` instead of the ordinary single-font
	// `drawCenteredLitTo`, since no font this table loads carries both
	// scripts (loadFonts() swaps the whole set by locale).
	bool hasMixedScript(const std::string & s){
		bool sawAscii = false, sawNonAscii = false;
		for(unsigned char c : s){
			if(c >= 0x80){
				sawNonAscii = true;
			} else if(std::isalnum(c)){
				sawAscii = true;
			}
			if(sawAscii && sawNonAscii){
				return true;
			}
		}
		return false;
	}

	std::vector<std::string> tokenizeForWrap(const std::string & text){
		std::vector<std::string> words;
		std::string cur;
		for(size_t i = 0; i < text.size(); ){
			const unsigned char c = (unsigned char)text[i];
			if(c < 0x80){
				if(std::isspace(c)){
					if(!cur.empty()){
						words.push_back(cur);
						cur.clear();
					}
					i++;
					continue;
				}
				cur += (char)c;
				i++;
				continue;
			}
			// Non-ASCII: flush any pending ASCII run, then take exactly
			// one UTF-8 codepoint (by its lead byte's own length) as a
			// token of its own — never split mid-codepoint.
			if(!cur.empty()){
				words.push_back(cur);
				cur.clear();
			}
			size_t len = 1;
			if((c & 0xE0) == 0xC0){
				len = 2;
			} else if((c & 0xF0) == 0xE0){
				len = 3;
			} else if((c & 0xF8) == 0xF0){
				len = 4;
			}
			len = std::min(len, text.size() - i);
			words.push_back(text.substr(i, len));
			i += len;
		}
		if(!cur.empty()){
			words.push_back(cur);
		}
		return words;
	}

	// Appends `token` to the growing line `cur`, WITHOUT a space when
	// both sides of the join are CJK — two adjacent Chinese characters
	// never take a space between them, that is what makes Chinese read
	// as Chinese rather than as Chinese-with-gaps. Every other join
	// (English-English, English-CJK, CJK-English) keeps the ordinary
	// single space, so a mixed string like "千卡 / 100克" — a literal
	// space in the source text either side of the ASCII "/" — still
	// reassembles with that same spacing rather than losing it because
	// one neighbour happened to be CJK.
	void appendToken(std::string & cur, const std::string & token){
		if(cur.empty()){
			cur = token;
			return;
		}
		const bool prevIsCjk = (unsigned char)cur.back() >= 0x80;
		if(prevIsCjk && tokenIsCjk(token)){
			cur += token;
		} else {
			cur += " " + token;
		}
	}

	// Nothing draws a rectangular BORDER on this table. The rule that
	// governed them still applies to the divider above the total and to the
	// info box's hairline: every line on this surface is a FILLED rect,
	// never an ofSetLineWidth or ofPath stroke, because stroke width is
	// driver-capped at 1px here and ignored outright on the programmable
	// renderer.

	// Bin item names ("Button Mushrooms", "Lotus Root Slices") can render
	// wider than a 200mm bin (252px) at kPlateNamePx, where they overflow
	// into the neighbouring bin's label. This is what makes the "at most
	// two lines" rule true: a greedy word-wrap rather than a font shrink,
	// which would abandon the measured kPlateNamePx. A single word wider
	// than maxWidthPx is still returned whole — this never breaks mid-word,
	// matching the rest of this file, which does no character-level
	// layout.
	std::vector<std::string> wrapNameToTwoLines(const ofTrueTypeFont & font,
		const std::string & text, float maxWidthPx){
		if(!font.isLoaded() || font.getStringBoundingBox(text, 0, 0).width <= maxWidthPx){
			return {text};
		}
		std::vector<std::string> words = tokenizeForWrap(text);
		if(words.empty()){
			return {text};
		}
		std::string line1;
		size_t i = 0;
		for(; i < words.size(); i++){
			std::string candidate = line1;
			appendToken(candidate, words[i]);
			if(!line1.empty() && font.getStringBoundingBox(candidate, 0, 0).width > maxWidthPx){
				break;
			}
			line1 = candidate;
		}
		if(line1.empty()){
			line1 = words[i++];   // one overlong word/character — take it anyway, never emit an empty line
		}
		std::string line2;
		for(; i < words.size(); i++){
			appendToken(line2, words[i]);
		}
		if(line2.empty()){
			return {line1};
		}
		return {line1, line2};
	}

	// The same greedy word-wrap with a caller-chosen line cap instead of a
	// hard 2 — the info box's note takes three.
	//
	// Kept separate from wrapNameToTwoLines rather than replacing it,
	// because the two differ in what they do when they run out of lines.
	// That one dumps every remaining word onto line 2, since a bin label is
	// short and overflowing is louder than truncating; this one truncates
	// the last line with an ellipsis so a long note cannot run out of its
	// band. Nothing on this table may truncate in practice, so that
	// ellipsis is a net rather than the mechanism.
	std::vector<std::string> wrapToLines(const ofTrueTypeFont & font,
		const std::string & text, float maxWidthPx, size_t maxLines){
		std::vector<std::string> lines;
		if(!font.isLoaded() || text.empty() || maxLines == 0){
			return lines;
		}
		std::vector<std::string> words = tokenizeForWrap(text);
		std::string cur;
		for(size_t i = 0; i < words.size(); i++){
			std::string candidate = cur;
			appendToken(candidate, words[i]);
			if(!cur.empty()
				&& font.getStringBoundingBox(candidate, 0, 0).width > maxWidthPx){
				lines.push_back(cur);
				cur = words[i];
				if(lines.size() == maxLines - 1){
					// Last line left: take everything remaining and let
					// truncateToWidth cut it, rather than dropping words
					// silently.
					for(size_t j = i + 1; j < words.size(); j++){
						appendToken(cur, words[j]);
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

	// Every Chinese character actually reachable from the table's content,
	// rather than `ofUnicode::CJKUnified`. Doc §17.1 warns that baking the
	// whole ~20,950-glyph block at 42px produces a very large atlas, and
	// this file loads a CJK-capable font at up to 19 sizes — so the full
	// block would mean 19 very large atlases, built at every startup and
	// again on every language toggle.
	//
	// Generated by scanning every zh string this table can show, for
	// characters at or above U+2E80: `data/locales/zh.json`'s values,
	// `data/catalogue.json`'s `names.zh` and `description_zh`,
	// `data/menu.json`'s `names.zh`, `meta_zh` and `note_zh`, PLUS the one
	// CJK string that lives outside any locale file — the Language button's
	// literal "EN | 中文", which is deliberately never translated and so
	// appears in no locale file for a scan to find.
	//
	// REGENERATE this array whenever any of those files' zh text changes,
	// or when that button's literal changes. A character used on the table
	// but missing from this list draws as a missing-glyph box (FreeType's
	// .notdef), which is exactly the failure a curated range exists to
	// avoid. A superset is cheap — fewer than a thousand extra glyphs at
	// worst; a subset is a silent box on the projected table.
	const std::uint32_t kCjkCodepoints[] = {
		0x3002, 0x4E00, 0x4E0B, 0x4E0D, 0x4E2A, 0x4E2D, 0x4E38, 0x4E3A, 0x4E4B, 0x4E73,
		0x4EA4, 0x4EBA, 0x4ED6, 0x4ED8, 0x4EE5, 0x4EEC, 0x4F1A, 0x4F5C, 0x4F9D, 0x4FBF,
		0x5145, 0x5148, 0x514B, 0x5165, 0x5168, 0x5176, 0x5178, 0x518D, 0x5206, 0x5230,
		0x5236, 0x52B2, 0x5316, 0x5341, 0x5343, 0x5355, 0x5361, 0x5374, 0x5377, 0x539A,
		0x539F, 0x53D6, 0x53D7, 0x53D8, 0x53E3, 0x53EB, 0x53F7, 0x540E, 0x542B, 0x5438,
		0x5458, 0x5468, 0x5473, 0x548C, 0x54B8, 0x54CD, 0x559C, 0x56BC, 0x56DB, 0x56DE,
		0x56F4, 0x571F, 0x5730, 0x573A, 0x591A, 0x591F, 0x5929, 0x5934, 0x597D, 0x59A5,
		0x5AE9, 0x5B8C, 0x5B9A, 0x5B9E, 0x5C06, 0x5C0F, 0x5C11, 0x5C1D, 0x5DDD, 0x5DE5,
		0x5DF1, 0x5DF2, 0x5E26, 0x5E38, 0x5E95, 0x5EA6, 0x5EFA, 0x5F39, 0x5F3A, 0x5F88,
		0x5FAE, 0x5FEB, 0x603B, 0x60A8, 0x611F, 0x6162, 0x6210, 0x6211, 0x626B, 0x62C9,
		0x62E9, 0x63A5, 0x63D0, 0x652F, 0x6536, 0x6587, 0x65B9, 0x65E0, 0x65E7, 0x65F6,
		0x6613, 0x662F, 0x66F4, 0x6700, 0x6709, 0x6761, 0x6765, 0x677E, 0x684C, 0x6912,
		0x6B21, 0x6B3E, 0x6B63, 0x6B65, 0x6BD4, 0x6C41, 0x6C42, 0x6C64, 0x6D53, 0x6D88,
		0x6DC0, 0x6DE1, 0x6E05, 0x6E29, 0x6ED1, 0x6EE1, 0x706B, 0x70B9, 0x70C8, 0x70DF,
		0x7136, 0x716E, 0x718F, 0x71AC, 0x723D, 0x7247, 0x724C, 0x7259, 0x725B, 0x7279,
		0x751C, 0x767D, 0x7684, 0x76C8, 0x771F, 0x77E5, 0x7801, 0x786E, 0x7897, 0x7A0D,
		0x7A20, 0x7A33, 0x7C73, 0x7C89, 0x7C97, 0x7CEF, 0x7D20, 0x7D27, 0x7EA7, 0x7EC6,
		0x7ECF, 0x7ED9, 0x7F8E, 0x800C, 0x8089, 0x80F6, 0x80FD, 0x8106, 0x817B, 0x81EA,
		0x8272, 0x82B1, 0x83C7, 0x83CC, 0x84EC, 0x85D5, 0x8611, 0x867D, 0x867E, 0x86CB,
		0x8A00, 0x8BA1, 0x8BA2, 0x8BA4, 0x8BA9, 0x8BAE, 0x8BD5, 0x8BED, 0x8BF7, 0x8C46,
		0x8DB3, 0x8F6F, 0x8F7B, 0x8F9B, 0x8FA3, 0x8FC7, 0x8FD4, 0x9000, 0x9002, 0x9009,
		0x901A, 0x9053, 0x90C1, 0x91CD, 0x91CF, 0x94C3, 0x968F, 0x96C5, 0x975E, 0x9762,
		0x9876, 0x9879, 0x98DF, 0x9971, 0x997C, 0x9999, 0x9AA8, 0x9C7C, 0x9C9C, 0x9CDD,
		0x9E21, 0x9EBB,
	};

	// Same Latin1Supplement/CurrencySymbols pair `loadUiFont` uses, PLUS
	// the codepoints above — a zh string still mixes in ASCII digits and
	// punctuation ("0级", "/100克"), so the Chinese font needs everything
	// the English one has as well as the glyphs the English one lacks.
	bool loadCjkFont(ofTrueTypeFont & font, const std::string & file, int size){
		ofTrueTypeFontSettings settings(file, size);
		settings.ranges = {ofUnicode::Latin1Supplement, ofUnicode::CurrencySymbols};
		for(std::uint32_t cp : kCjkCodepoints){
			settings.addRange(ofUnicode::range(cp, cp));
		}
		return font.load(settings);
	}
}

// Loads every font member for one locale. Called from setup() and again
// from update() whenever `state.locale` changes.
//
// Every role converges on `kCjkFontFile` when `locale == "zh"`, whichever
// of the four English files it normally loads: there is one bundled CJK
// weight, not four — no bold, no mono, no separate regular — so the
// bold/regular/mono distinction the comments below draw is real for English
// and simply absent for Chinese until a second CJK weight is sourced.
//
// Sizes are the SAME px as the English role in every case. Doc §17.1 asks
// for CJK 15% larger at equal cap height, but every layout constant this
// file measures once against these metrics (`_pageHeaderPx`, the cart and
// info-box bands checked in setup()) is measured against English and never
// re-measured on a locale switch. Bumping the CJK sizes here would silently
// overflow all of those bands the first time a diner presses Language.
// Matching size rather than doc-perfect proportion is the safe trade until
// those bands read `_loadedFontLocale` themselves.
bool UiLayer::loadFonts(const std::string & locale){
	const bool zh = (locale == "zh");
	auto load = [zh](ofTrueTypeFont & font, const std::string & latinFile, int size){
		return zh ? loadCjkFont(font, kCjkFontFile, size)
		          : loadUiFont(font, latinFile, size);
	};
	bool ok = true;
	ok = load(_nameFont, kFontFile, kNamePx) && ok;
	ok = load(_detailFont, kFontFile, kDetailPx) && ok;
	ok = load(_plateNameFont, kFontFile, kPlateNamePx) && ok;
	ok = load(_plateRateFont, kMonoFontFile, kPlateRatePx) && ok;
	// VISUAL_LAYER.md §3: "Total value" 48px bold, "Total label" 30px —
	// sized for the single receipt-style line in the cart footer
	// (drawCart/drawTotal), not for a free-standing numeral.
	ok = load(_totalNumFont, kMonoBoldFontFile, 48) && ok;
	// "Total" is a caption for the figure beside it, so it is regular —
	// the number is what should be loud.
	ok = load(_totalLabelFont, kRegularFontFile, 30) && ok;
	// Regular, not bold: a cart row is a list of things the diner already
	// chose, read at arm's length, and eight bold lines in a column read
	// as eight headings.
	ok = load(_cartRowFont, kRegularFontFile, kCartRowPx) && ok;
	// Mono for the grams/price column so the numbers do not re-flow as
	// digits change, and so the column reads as data next to prose.
	ok = load(_cartDetailFont, kMonoFontFile, kCartDetailPx) && ok;
	ok = load(_infoNameFont, kFontFile, kInfoBoxNamePx) && ok;
	// The note is prose, so it takes the regular weight.
	ok = load(_infoFont, kRegularFontFile, kInfoBoxTextPx) && ok;
	// The diet word stays BOLD and small: it is a label, not prose, and
	// it is the one line on the box somebody may act on.
	ok = load(_infoDietFont, kFontFile, kInfoDietPx) && ok;
	ok = load(_infoKcalFont, kMonoFontFile, kInfoBoxKcalPx) && ok;
	// A button's label is read first, so it stays bold, but sized to the
	// button rather than to the page. Three buttons share the cart's 520px
	// (core/hover.py's `button_row`), leaving each one 154.7px, and the
	// widest label ("Cancel") measures ~114px as oF measures it — 40px of
	// margin. At `_nameFont`'s 28px it is ~148px in a 155px button, which
	// is no margin at all.
	ok = load(_buttonFont, kFontFile, 22) && ok;
	ok = load(_pageTitleFont, kFontFile, kPageTitlePx) && ok;
	// See kOptionLabelPx: 20px is what the plate's own arithmetic allows,
	// not a preference.
	ok = load(_optionFont, kFontFile, kOptionLabelPx) && ok;
	ok = load(_cardNoteFont, kRegularFontFile, kCardNotePx) && ok;
	ok = load(_tokenFont, kMonoBoldFontFile, kTokenPx) && ok;
	ok = load(_devFont, kFontFile, 16) && ok;
	ok = load(_idleHandFont, kFontFile, kIdleHandTextPx) && ok;
	return ok;
}

void UiLayer::setup(){
	_fontsLoaded = loadFonts("en");
	_loadedFontLocale = "en";
	if(!_fontsLoaded){
		ofLogError(kTag) << "could not load " << kFontFile << " or " << kMonoFontFile
			<< " at one or more sizes — labels will not draw";
	}
	// A boot-time probe, not a real switch. It confirms `kCjkFontFile` and
	// `kCjkCodepoints` load together, so a missing font file or a stale
	// codepoint list (see that array's comment on regenerating it) shows up
	// in the log at startup rather than the first time a diner dwells
	// Language and gets back a row of missing-glyph boxes. Reloads straight
	// back to "en", the boot locale, so this costs one extra font-bake pass
	// at startup and nothing at runtime.
	if(!loadFonts("zh")){
		ofLogWarning(kTag) << "the zh font set (" << kCjkFontFile << ") failed to"
			<< " load at one or more sizes — dwelling Language on the real"
			<< " table will switch to boxes/blanks until this is fixed";
	}
	loadFonts("en");

	// The Language button's own "中文" half — loaded here, once, and
	// never reloaded by the locale switch above: it has to stay lit
	// regardless of which set `loadFonts()` last chose, since the
	// button's label mixes both scripts in either locale. Same size and
	// same file `loadFonts` would use for a zh `_buttonFont`, just not
	// swapped away when the table goes back to English.
	if(!loadCjkFont(_buttonFontCjk, kCjkFontFile, 22)){
		ofLogWarning(kTag) << "could not load the Language button's CJK half ("
			<< kCjkFontFile << ") — its \"中文\" side will not draw";
	}

	// The page header's real height, from the face that draws it: one line
	// of title, then the step dots on their own line (see
	// kStepDotsRowGapPx), then the gap to the info box below.
	//
	// This is the one place the dots' height enters the layout, and it must
	// agree TERM FOR TERM with drawPageHeader's own dotsY — otherwise the
	// box below is measured against a header that is not the one being
	// drawn. Same terms, same order, both from the same two font
	// metrics.
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
	// Logged, not assumed. A reserve that is comfortable on paper has still
	// truncated a name on the real table, which means one of these three
	// numbers was not what this file thought it was. Printing them at boot
	// is what lets the next report be diagnosed from the log rather than
	// from arithmetic done here.
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
		// hover.py's BUTTONS_TOP_PX is DERIVED, not chosen: it centres the
		// button row in the near margin, so it moves whenever BUTTON_H_PX
		// does. Re-derive this literal from that file's arithmetic, never
		// by eye, whenever either number moves.
		const float kHoverButtonsTopPx = 937.0f;   // core/hover.py BUTTONS_TOP_PX
		if(cartBottomPx() > kHoverButtonsTopPx){
			ofLogWarning(kTag) << "cart bottom measures " << cartBottomPx()
				<< "px, below core/hover.py's button band at " << kHoverButtonsTopPx
				<< "px — the Confirm/Cancel buttons will overlap the total";
		}
		// The cart has to fit alongside the near row. kCartFooterHeightPx
		// is a budget reserved ahead of the font metrics, as it must be —
		// these are namespace constants — and this is where that budget is
		// checked against what the total actually measures.
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
		// Measured against the TIGHTER of the two bands. Every screen in
		// the ordering sequence puts a page header above the box, so they
		// get `_pageHeaderPx` less than a bare table does — checking the
		// roomy one passes while the broth screen overflows.
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

	// Pre-cropped, with a transparent background — see assets/logo/ for the
	// source. The "light" variant (dark ink on a near-white ground) rather
	// than the dark one, matching this surface's hard invariant: the
	// projected field stays above a white floor and is never black, never
	// coloured and never patterned (doc §2).
	_brandLogoLoaded = _brandLogo.load("img/firepot-light-cropped.png");
	if(!_brandLogoLoaded){
		ofLogError(kTag) << "could not load img/firepot-light-cropped.png"
			<< " — no brand mark will draw";
	}

	// The spice cards' pepper — see the kChilliWidthFactor block for the
	// measurements the layout depends on.
	_chilliIconLoaded = _chilliIcon.load("img/chilli.png");
	if(!_chilliIconLoaded){
		ofLogError(kTag) << "could not load img/chilli.png — the spice"
			<< " cards will draw no peppers";
	} else {
		// Pre-scaled on the CPU, because the GPU does this one badly. The
		// file is 512px tall and the pepper draws at the option label's cap
		// height, about 14px on this table — a ~36x minification. oF hands
		// textures to GL as ARB rectangle textures, which cannot carry
		// mipmaps, so that reduces to a single bilinear tap over 4 of 512
		// texels: the artwork's thin black outline breaks into speckle, and
		// the speckle crawls as the card breathes.
		//
		// ofImage::resize goes through FreeImage's filtered rescale once,
		// here, rather than per frame, leaving GL a mild 4x minification it
		// handles well. Targeting 4x the drawn height rather than 1x is
		// headroom for a larger label font later without going soft, and
		// the 48px floor keeps the shape readable if `_optionFont` fails to
		// load and the fallback face measures small.
		const ofTrueTypeFont & labelFace =
			_optionFont.isLoaded() ? _optionFont : _nameFont;
		const float capPx = labelFace.isLoaded()
			? labelFace.getStringBoundingBox("Hot", 0, 0).height
			: (float)kOptionLabelPx;
		const int target = std::max(48, (int)ceilf(capPx * 4.0f));
		if(target < (int)_chilliIcon.getWidth()){
			_chilliIcon.resize(target, (int)roundf(target
				* (float)_chilliIcon.getHeight()
				/ (float)_chilliIcon.getWidth()));
		}
	}

	// The idle-table wave prompt — see drawIdleHand. No CPU pre-scale here
	// the way the chilli gets one: that icon shrinks ~36x to a 14px glyph,
	// where a single bilinear tap breaks its outline into speckle. This one
	// draws close to native size (kIdleHandHeightPx), so GL's own bilinear
	// minification is nowhere near that regime and a resize would only cost
	// sharpness for nothing.
	_idleHandIconLoaded = _idleHandIcon.load("img/idle-hand.png");
	if(!_idleHandIconLoaded){
		ofLogError(kTag) << "could not load img/idle-hand.png — the idle"
			<< " table will show no wave prompt";
	}

	// The halo's per-bin phase offset (VISUAL_LAYER.md §6) is not
	// staggered independent breathing: it is ONE highlight rotating around
	// each island's 2x2 bins, a quarter turn per bin.
	//
	// TableGeometry.h's BINS table gives the layout. The LEFT island is
	// 0=TL, 1=TR, 5=BR, 4=BL — bins 0/1 are the far row's two leftmost,
	// 4/5 the near row's, on the same x columns — so the sequence below is
	// TL->TR->BR->BL, clockwise around that island's perimeter.
	//
	// The RIGHT island (2=TL, 3=TR, 7=BR, 6=BL) is the left island's
	// mirror across the table's vertical centreline, and this codebase
	// treats that axis as bilateral mirror symmetry about the pot gap
	// rather than identical absolute motion — the same convention that
	// makes both bin rows read outward from the pot rather than
	// top-to-bottom. So the right island rotates counter-clockwise
	// (2 -> 6 -> 7 -> 3), mirroring the left rather than matching it. That
	// is a call, not a certainty: swapping this island's middle two phases
	// (6 and 7) makes both spin the same way.
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
	// `_coreRects[i]` is core's PROJECTOR grid (core/bin_grid.py), which
	// by design has no homography anywhere in its chain: a human drags or
	// nudges it while looking straight at THIS space — the real light on
	// the real table — rather than at a proxy for it. So "core has a rect"
	// and "core's rect is trustworthy" cannot come apart here, because
	// nothing is derived; every value is a number a person set by watching
	// the effect directly, which is doc §5.3's own cure for its TRAP.
	//
	// The kill switch stays as a named constant rather than being folded
	// away, because a rect derived through a homography would need
	// distrusting again. The CAD layout is the fallback for the ordinary
	// case: no projector grid has been set yet at all.
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
	// Four filled bars, not a stroked path.
	//
	// An unfilled ofPath is drawn by ofGLRenderer::draw(const ofPath&),
	// which calls setLineWidth(shape.getStrokeWidth()) -> glLineWidth().
	// ofPath::setStrokeWidth() therefore IS ofSetLineWidth(), the call doc
	// §13.4 says never to use: Mesa on Intel caps it at 1px, and on the
	// programmable renderer the fluid forces this app onto, that
	// glLineWidth call is commented out entirely and the width is ignored.
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
	// A FILLED ofPath — outer arc, then an inner arcNegative. Doc §13.4
	// spells this out, and the reason is not style: a stroked ring is
	// glLineWidth in disguise (see drawRing above), which Mesa on Intel —
	// the deploy board's driver family — caps at 1px and the programmable
	// renderer ignores outright. A stroked ring works on a dev machine and
	// becomes a hairline on the board.
	//
	// Two ofDrawCircle calls with the background punched through the middle
	// is the other tempting version and is also wrong: over the fluid there
	// is no background colour to punch with.
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
	// its inner contour at `base` itself, at offset 0. This lets the inner
	// contour sit further out too, so drawHalo can nest many bands around
	// one bin without each one re-covering the ground the last already did.
	// Same ODD-winding, filled-only technique, and the same reason for it —
	// see drawAnnulus.
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
	// the centre and fades to nothing at both ends.
	//
	// Sliced 1px at a time, and the ALPHA is what fades, never the height.
	// Tapering the height as well clamps to a 1px floor and turns the ends
	// into a dashed line of stubs.
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

void UiLayer::drawChilli(float cx, float cy, float sizePx) const {
	// One pepper, centred on (cx, cy), `sizePx` TALL — the supplied
	// artwork, drawn as it came and only scaled. There is deliberately NO
	// vector fallback behind it: drawing a substitute shape would be worse
	// than drawing nothing, and setup() logs the missing file loudly.
	//
	// `sizePx` is the height of the PEPPER, not of the file. The artwork's
	// opaque pixels run the full height of its square (see
	// kChilliWidthFactor), so the drawn box and the visible pepper are one
	// and the same height and the caller's "exactly the height of the
	// letters on this line" survives the trip through here. It is centred
	// in the square horizontally too, which is why this can centre the box
	// on (cx, cy) and have the INK land centred on it.
	if(!_chilliIconLoaded || sizePx <= 0.0f){
		return;
	}
	const float h = sizePx;
	const float w = h * ((float)_chilliIcon.getWidth()
		/ (float)_chilliIcon.getHeight());
	// Full white, so the artwork's own reds and green come through
	// untinted — the same reason drawOptionPlate's sweep leaves the
	// peppers alone: the colour is the information.
	ofSetColor(255);
	_chilliIcon.draw(cx - w * 0.5f, cy - h * 0.5f, w, h);
}

void UiLayer::drawIdleHand() const {
	// See kIdleHandHeightPx's own block for why here and this size. Only
	// called from draw()'s idleAttract branch — an idle table is the one
	// state with nothing else claiming this spot.
	if(!_idleHandIconLoaded){
		return;
	}
	const float cx = mmToPxX(TABLE_W_MM * 0.5f);
	const float cy = mmToPxY(TABLE_H_MM * 0.5f);
	const float h = kIdleHandHeightPx;
	const float w = h * ((float)_idleHandIcon.getWidth()
		/ (float)_idleHandIcon.getHeight());

	// The artwork's wrist is its bottom edge (fingers point up, arm runs
	// off the bottom of the frame — see the source PNG). Waving pivots at
	// the wrist in life, not at the palm, so the rotation is built around
	// that point: translate there, rotate, then draw the SAME unrotated
	// image offset back up by its own height. No second image, no
	// pre-rotated frame — one texture, transformed on the GPU each frame.
	const float wristX = cx;
	const float wristY = cy + h * 0.5f;
	const float waveDeg = kIdleHandWaveDeg
		* sinf(TWO_PI * ofGetElapsedTimef() / kIdleHandWavePeriodS);

	ofSetColor(255);
	ofPushMatrix();
	ofTranslate(wristX, wristY);
	ofRotateDeg(waveDeg);
	_idleHandIcon.draw(-w * 0.5f, -h, w, h);
	ofPopMatrix();

	// "Wave to start" — outside the pushed matrix on purpose: the prompt
	// reads as an instruction sitting beside the hand, not a limb of it,
	// so it stays put while the hand rocks rather than rotating with it.
	// Anchored off `wristY`, not the hand's rotated extent, for the same
	// reason — a fixed point the wave motion never moves.
	if(_idleHandFont.isLoaded()){
		ofSetColor(kIdleHandTextColor);
		drawCentered(_idleHandFont, "Wave to start", cx,
			wristY + kIdleHandTextGapPx + _idleHandFont.getAscenderHeight());
		ofSetColor(255);
	}
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
	// A floor well above zero — see kHaloBreathPeriodS: a bin dimmed
	// almost to nothing reads as broken rather than as breathing. It goes
	// through the shared `breath` helper so the buttons' glow and the
	// bins' halo cannot drift into two different rhythms.
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
	// The dwell that fires core's `_cycle_locale()` changes `state.locale`
	// on the very next state broadcast, and this is where oF notices and
	// rebakes every font member from the other language's file (see
	// loadFonts() on why one reload beats a second live set of members).
	//
	// Guarded on `hasState` — the enclosing early-return above — rather
	// than trusted from a default-constructed `state`, and on the locale
	// being one this table has a font for: an unrecognised value from a
	// future third locale leaves the CURRENT glyphs on screen instead of
	// silently reloading into (and logging as a failure of) a font file
	// that was never going to exist.
	if(state.locale != _loadedFontLocale
		&& (state.locale == "en" || state.locale == "zh")){
		const bool ok = loadFonts(state.locale);
		_loadedFontLocale = state.locale;
		_fontsLoaded = ok;
		if(!ok){
			ofLogError(kTag) << "could not reload fonts for locale '"
				<< state.locale << "' — labels will not draw";
		}
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
	// A hovered WIDGET outranks a hovered bin. On the BROTH and
	// SPICE screens the diner is choosing between options, not between
	// bins, and the option they are pointing at is the thing the box is
	// about. A bin cannot be hovered on those screens anyway — the
	// pointer is in the centre column — but the ordering is stated here
	// rather than left to that coincidence.
	// Three sources, in this order: hovered widget, SELECTED widget,
	// hovered bin.
	//
	// The middle one matters: a selection is LOCKED IN and stays locked
	// without a hover, so the box has to stay locked with it. Without it
	// the box empties the instant the hand leaves the plate the diner just
	// chose, so the one screen where they most want to re-read what they
	// picked is the one screen that will not show it.
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

	// There is deliberately no solid ring around a bin: the idle halo
	// occupies that visual role, and VISUAL_LAYER.md §3's palette specifies
	// only the halo/fire pair.
	//
	// Known consequence: "picked" has no visual distinction of its own. An
	// idle bin and a picked-but-not-hovered bin render identically — that
	// is the fire ring's job, not something to patch here.
	if(!b.resolved){
		return;   // doc §9.3: unresolved bins render with no label
	}

	const float cx = box.getCenter().x;
	const float clearance = mmToPxY(kLabelClearanceMM);
	const float gap = mmToPxY(kLabelLineGapMM);

	// Labels clear a fixed offset past the CUTOUT, not the bin box, and
	// that offset keeps its own name (kRingMM) even though nothing draws a
	// ring there — dropping it would pull every label up against the cutout
	// edge.
	//
	// The offset is load-bearing: kLabelClearanceMM and CUTOUT_MARGIN_MM
	// are both 10mm, so measuring from box.y puts the far row's baseline
	// exactly on the cutout's edge and the light pass eats every
	// descender.
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

	// b.label is core's full display_name(). Wrapped to the BIN's own
	// footprint, not to the gap between neighbours — that gap (250mm) is
	// wider, but wrapping to the box the label sits over is what keeps
	// every name visually inside its own plate.
	std::vector<std::string> nameLines = wrapNameToTwoLines(_plateNameFont, b.label, mmToPxX(BIN_W_MM));
	const float nameLineGap = 2.0f;   // px between a name's own wrapped lines, tighter than kLabelLineGapMM's block-to-block gap

	if(i < 4){
		// far row: rate strip sits just above the ring, name strip above
		// that — ring → price/grams → name, reading outward from the pot.
		float rateBaseline = ringTop - clearance - rateDescend;
		ofSetColor(kPlateRateColor);
		drawCentered(_plateRateFont, detail, cx, rateBaseline);

		// The gap between the rate line and the name block is measured
		// from the RATE line's ASCENDER — its actual top edge — never from
		// getLineHeight(), which adds internal leading on top of the
		// ascender and silently inflates the gap.
		//
		// The mirrored near-row branch below measures from the DESCENDER
		// for the same reason. Using line height in both directions makes
		// the two rows' gaps visibly different for the same `gap`
		// constant, because an ascender is far taller than a descender in
		// this font.
		//
		// nameLines.back() sits closest to the rate line; earlier lines
		// stack upward, spaced by this font's own line height since both
		// lines share one font and size.
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
	// right), sharing one baseline the way a printed receipt's total line
	// does.
	//
	// cx is the table's own centre, which is also the cart's centre: the
	// pot gap is symmetric about it (TableGeometry.h's X chain), so no
	// separate column math is needed here.
	const float cx = mmToPxX(TABLE_W_MM * 0.5f);
	const float leftX = cx - kCartWidthPx * 0.5f + kCartPadXPx;
	const float rightX = cx + kCartWidthPx * 0.5f - kCartPadXPx;

	// The "Total label" caption, resolved per locale by core
	// (data/locales/<locale>.json's "total" key) and carried on
	// `total.label` — oF draws whatever string arrives and looks nothing
	// up (I2). An empty label draws nothing, the same rule drawCentered's
	// callers follow.
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
	// The dots sit UNDER the title, not beside it — see kStepDotsRowGapPx
	// for what that costs and where the space comes from. It also centres
	// the title properly: drawn inline, the title-plus-dots GROUP is what
	// gets centred, which pushes the title itself off-centre by a
	// different amount on every screen.
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
	// `screen.steps` is core's, never a constant here — changing how many
	// screens the sequence has must not require an oF change, which is the
	// point of sending it.
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
	// VISUAL_LAYER.md §8: the info box sits ABOVE the cart at a fixed
	// height, does not push the cart down, and is invisible when idle — no
	// fill, no border, and not an empty bordered box.
	//
	// The design is text-forward:
	//   - no fill, no border, no panel: type on the table background, the
	//     same as the plate labels and the cart;
	//   - the item's NAME leads, with kcal RIGHT-ALIGNED on the same line
	//     and set larger than the body, since it is the one figure a diner
	//     weighs a choice against;
	//   - one faded rule under that pair (drawFadedRule);
	//   - one note about what the ingredient is LIKE — never an
	//     instruction, since the diner picks here and the kitchen cooks.
	//     See pricing.Item.description.
	//
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
	// Never over a banner. The two share this band, and doc §14.5's
	// precedence rule settles it: the state that changes what the table is
	// DOING outranks everything else in the centre column.
	//
	// A banner and a hover almost never coincide — setting mode disables
	// MediaPipe, and an uncalibrated table has no homography to hit-test
	// with — but `error` is raised while SERVING, which is exactly why this
	// is explicit rather than left to coincidence.
	//
	// `qr` is on the list too: the CHECKOUT screen owns this whole band
	// (drawCheckout), and a leftover info box from whichever bin the
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
	// face runs about 1.8x the point size (measured), which is generous
	// leading for a paragraph and overflows this band — far too
	// airy for six lines that have to share one panel. Every other
	// vertical step in this function is built the same way.
	const float bodyLineH = _infoFont.getAscenderHeight()
		+ fabsf(_infoFont.getDescenderHeight()) + kInfoBoxLineGapPx;
	float y = box.y + kInfoBoxPadYPx;   // the TOP of the next block, never a baseline

	// Line 1 — the item's name, ALONE on its own full-width line, in the
	// plate's own ink so the two read as the same label seen twice.
	//
	// The chosen mockup put kcal on this line, right-aligned, and it
	// does not survive the real font. Measured (PIL/FreeType, the real
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
	// Drawn only when there IS one. A spice level is not food and has
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

	// The cart grows UPWARD from the divider: the newest bound slot always
	// sits in the last row, directly above the total, and the list pushes
	// up as it fills — a receipt printing towards the reader rather than a
	// form filling from the top.
	//
	// This deliberately overrides doc §8's "the SAME slot updates in place
	// — it never moves". A bin's row DOES move upward when a later bin
	// joins the cart. What §8's rule protects is that a row never jumps
	// around as its OWN numbers change, and that still holds:
	// _cartSlotBin's pick order is untouched, so the only thing that moves
	// a row is another item arriving.
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

	// Empty-cart guidance. Doc §8's rule above ("rows above the filled
	// ones draw nothing") leaves a first-time diner looking at blank space
	// under "Your Order" with nothing telling them what to do. Core
	// resolves the wording per I2 (`cart_hint`, `_screen_msg`) and only
	// while the cart is empty — `state.screen.hint` is "" the instant a
	// pick lands, so the guidance and the cart's own rows can never show
	// at once without oF having to re-derive that from `drawn` itself.
	if(drawn.empty() && _infoFont.isLoaded() && !state.screen.hint.empty()){
		const float maxWidth = kCartWidthPx - 2.0f * kCartPadXPx;
		std::vector<std::string> lines =
			wrapToLines(_infoFont, state.screen.hint, maxWidth, 2);
		const float lineH = _infoFont.getAscenderHeight()
			+ fabsf(_infoFont.getDescenderHeight()) + kInfoBoxLineGapPx;
		const float blockH = lineH * (float)lines.size() - kInfoBoxLineGapPx;
		float baselineY = kCartTopPx + (rowsBottom - kCartTopPx - blockH) * 0.5f
			+ _infoFont.getAscenderHeight();
		ofSetColor(kInfoBoxTextColor);
		for(const std::string & line : lines){
			drawCentered(_infoFont, line, cx, baselineY);
			baselineY += lineH;
		}
		ofSetColor(255);
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
		// Reported once per name, not per frame. If a name ever does
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

	// Faded, matching the info box's own rule — one kind of divider on
	// this table, not two.
	drawFadedRule(x + kCartPadXPx, dividerY, kCartWidthPx - 2.0f * kCartPadXPx,
		kRuleThickPx, kAccentInk, kRuleAlpha);
	drawTotal(state.total, totalBaselineY);
	ofSetColor(255);

	// Confirm and Cancel are NOT drawn here. They are real dwell targets,
	// and a dwell target's rect has to be the rect CORE hit-tests against
	// (doc §9.4: core hit-tests, oF times nothing) — so core/hover.py owns
	// both buttons and they arrive on the wire like any other widget, drawn
	// by drawWidgets/drawWidget. Drawing them from a second, oF-local rect
	// would put a button on the table that a hand can miss while looking
	// like it hit.
	//
	// hover.py's CART_* constants mirror kCartWidthPx and the cart's bottom
	// edge here, and setup() logs if the two drift far enough for the
	// buttons to collide with the total. That check is the only thing
	// standing between the two files — read it before moving either.
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
	// loudly without touching the light field. One panel, different hue
	// and words per state.
	//
	// Deliberately NOT the full-width strip along the top edge that §14.5
	// literally describes. The far row's labels are drawn ABOVE their
	// rings, upward into the 177mm far margin — a two-line wrapped name
	// puts ink as high as ~50px — and a full-width strip covers them.
	// Staff have to READ those names to confirm which tray is which,
	// during setting mode above all, which is exactly when this banner is
	// up; covering them defeats the mode the banner is announcing.
	//
	// So the panel is confined to the centre column: the span between
	// bin 1's right edge and bin 2's left edge, the one horizontal span on
	// the table with no bin and no label in it by construction. Derived
	// from BINS rather than hardcoded, so moving a bin moves the panel.
	//
	// Being narrower it is taller and two-line instead. A ~440x88mm block
	// is still unmistakable from three metres, which was the actual goal;
	// the strip shape was only ever one way to get there.
	//
	// Stage's light pass runs after UiLayer and re-stamps every cutout
	// white regardless of what this draws (doc §13.2's "any overlay added
	// later" safety property), so this can never darken a bin patch.
	//
	// Bins and the total keep drawing underneath (draw() calls this after
	// them, not instead of them) — doc §13.3's rule for a dead core link
	// applies just as well to a dead scale link: "It does not black out —
	// a frozen table is far better... than a dead one."
	// yTop, not 0: the brand mark owns the table's far edge
	// (drawBrandMark), and this panel starts where the mark's bottom
	// margin ends, so the two stack rather than one replacing the other.
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
	// An empty subline (setting mode — headline alone is the whole message
	// now) centres the headline on its own rather than leaving it high in
	// a block sized for a second line nobody is drawing.
	if(subline.empty()){
		drawCentered(_nameFont, headline, cx,
			yTop + (h + _nameFont.getAscenderHeight()) * 0.5f);
	}
	else{
		const float lineGap = 8.0f;
		const float blockH = _nameFont.getAscenderHeight() + lineGap
			+ _detailFont.getAscenderHeight();
		const float localTop = (h - blockH) * 0.5f;
		drawCentered(_nameFont, headline, cx, yTop + localTop + _nameFont.getAscenderHeight());
		drawCentered(_detailFont, subline, cx, yTop + h - localTop);
	}
	ofSetColor(255);
}

void UiLayer::drawBrandMark() const {
	// Persistent, always-visible table branding, top-anchored in the
	// pot-gap centre column — the one horizontal span with no bin and no
	// label in it (see drawBanner). Unlike the banner this is never
	// hidden: draw() always calls it once the image has loaded, and
	// drawBanner positions itself below the mark's bottom edge rather than
	// sharing this strip, so the two stack.
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
	// Precedence, doc §14.5's table: uncalibrated > setting > error.
	//
	// The general rule is that the state which changes what the table is
	// DOING outranks a fault reported by a subsystem that state has
	// already disabled.
	//
	// SETTING therefore wins over error. Both claim this strip and both
	// can be true at once — someone knocks the XIAO cable out mid-setup —
	// but nothing bills in setting mode (core's _apply_scale_to_cart
	// returns immediately there), so a scales-offline warning would name a
	// risk that cannot occur while displacing the message that is true.
	// Whoever is doing setting-mode work is holding the tablet, where the
	// Bins tab already reports the dead link; this strip is for everyone
	// NOT holding it.
	//
	// `uncalibrated` outranks `setting` because it SURVIVES setting mode:
	// an operator who exits on a table with no geometry still cannot
	// serve, and `setting` would mask the one message that stays true for
	// the whole time they are trying to fix it.
	//
	// Both banners lead with the SAME headline, deliberately: it is the
	// only part a diner needs and is equally true of both states, and
	// which one it is matters only to the operator, who reads it off the
	// subline and the hue (I8). The wording is "serving" throughout rather
	// than "billing" — one word for the idea, and the one a diner already
	// understands.
	//
	// English only. Doc §14.5 pairs the banner with a Chinese string, and
	// §17.3 is explicit that Chinese judges will read it, so that text
	// must be confirmed by a native speaker rather than guessed at here.
	if(state.overlayKind == "uncalibrated"){
		drawBanner(kUncalBannerFill, kUncalBannerInk,
			"NOT SERVING", "not set up yet");
		return;
	}
	if(state.mode == "setting"){
		drawBanner(kSettingBannerFill, kSettingBannerInk,
			"NOT SERVING", "");
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
		// buttons." A MUTED clay rather than a fire-engine red; see
		// kWidgetDanger.
		ink = kWidgetDanger;
	}
	else if(w.style == "option"){
		// The broth and spice plates. Neutral ink, selected or not — a
		// fourth hue here would make the SCREEN look like it was carrying
		// state, when the only state on it is which one is chosen.
		//
		// Selection is signalled downstream of `ink` instead:
		// `drawOptionPlate` thickens the ring and tints the glow on
		// `sel`, neither of which needs this value to change.
		ink = kInkColor;
	}

	// How lit this control is, 0..1.
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

	// A rounded RECTANGLE, not a pill — see kWidgetCornerPx. The glow is
	// drawHalo's falloff (drawGlow), so a lit button and a lit bin are the
	// same effect at two sizes.
	const float corner = std::min(kWidgetCornerPx,
		std::min(box.width, box.height) * 0.5f);
	const bool primary = w.enabled && w.style == "primary";
	const int fillPeak = primary ? kWidgetPrimaryFillAlpha : kWidgetFillAlpha;

	// The halo is `drawWidgetGlows`, earlier in the frame; the opaque base
	// under the wash is what stops a neighbour's halo reading through this
	// button. Same reasoning as `drawOptionPlate` — see there.
	drawRoundedRectFill(box, corner, kCardBaseColor);
	drawRoundedRectFill(box, corner,
		ofColor(ink, w.enabled ? (int)(fillPeak * (0.55f + 0.45f * glow01))
			: kWidgetFillAlpha / 2));

	// Dwell progress, drawn INSIDE the button, and the ONLY place it is
	// drawn at all. A concentric ring on the cursor sits under the diner's
	// hand — which is exactly where a hand is while dwelling — so it reads
	// as no feedback whatever. `dwell` is core's 0..1 fraction; oF times
	// nothing (doc §9.4).
	//
	// The button row uses the option cards' inverting sweep: the same
	// near-solid dark band, with the label inverting behind its leading
	// edge. Back, Cancel, Pay, Next and Done therefore report progress the
	// same way a broth card does, so the table has one dwell language
	// rather than two.
	//
	// It fills LEFT to RIGHT, which is what a progress bar does everywhere
	// else a diner has seen one. The clamp to the button's own corner is
	// what keeps the sweep inside the frame.
	const float sweep01 = sweep01For(w);
	drawSweep(box, corner, sweep01);

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
	//
	// Behind the sweep's leading edge the label flips to the lit ink —
	// `kOptionNameLitColor`, the same off-white the cards use, rather than
	// a lightened version of each button's own hue: on the swept black the
	// hue is already carried by the ring and the halo around it, and three
	// different near-whites would be three shades of "the same" colour.
	const ofTrueTypeFont & face =
		_buttonFont.isLoaded() ? _buttonFont : _nameFont;
	// The Language button's "EN | 中文" mixes ASCII and CJK in one label,
	// and no single font this table loads carries both — loadFonts() swaps
	// the WHOLE set between the DejaVu files and Noto Sans SC by locale.
	// Routed by CONTENT (hasMixedScript) rather than by widget id, so any
	// future bilingual label gets this for free.
	if(hasMixedScript(w.label) && _buttonFontCjk.isLoaded()){
		drawBilingualCenteredLitTo(face, _buttonFontCjk, w.label,
			box.getCenter().x,
			box.getCenter().y + face.getAscenderHeight() * 0.5f,
			box.x + box.width * sweep01,
			w.enabled ? ink : kWidgetDisabled, kOptionNameLitColor);
	} else {
		drawCenteredLitTo(face, w.label, box.getCenter().x,
			box.getCenter().y + face.getAscenderHeight() * 0.5f,
			box.x + box.width * sweep01,
			w.enabled ? ink : kWidgetDisabled, kOptionNameLitColor);
	}
	ofSetColor(255);
}

void UiLayer::drawOptionPlate(const StateLink::Widget & w, const ofColor & ink,
	float glow01) const {
	// A broth or a spice option: the two draw identically.
	// `hover.spice_widgets` lays out full-height cards through
	// `hover.broth_card_rects`, the same function `hover.broth_widgets`
	// uses, so this one card style is the whole function.
	const ofRectangle box(w.x, w.y, w.w, w.h);
	const float corner = std::min(kWidgetCornerPx,
		std::min(box.width, box.height) * 0.5f);
	const bool sel = w.selected && w.enabled;

	// Selection is a HALO colour, not a card colour: `ink` — the fill,
	// ring and name colour below — stays neutral regardless of `sel` (see
	// drawWidget's `style == "option"` branch), and only the GLOW reaches
	// for `kWidgetPrimary`. A locked-in choice reads as "this one is
	// glowing" rather than "this whole card changed colour".
	//
	// The halo itself is `drawWidgetGlows`, called much earlier — see
	// there for why every glow has to land under the whole column.
	//
	// An OPAQUE base first, then the translucent ink wash on top. The wash
	// is only ~10% ink, so without the base the neighbouring cards' halos
	// read straight through a card drawn over them. The base is the
	// table's own colour, so the card looks unchanged; it just stops being
	// a window.
	drawRoundedRectFill(box, corner, kCardBaseColor);
	drawRoundedRectFill(box, corner,
		ofColor(ink, w.enabled
			? (int)(kWidgetFillAlpha * (0.55f + 0.45f * glow01))
			: kWidgetFillAlpha / 2));

	// The dwell sweep INVERTS the text it crosses. A dark fill that leaves
	// the text at its dark inks reads on the projector as a black hole
	// with the words dissolved inside it, so the sweep goes nearly solid
	// and the text comes with it: everything left of the leading edge is
	// redrawn in the lit inks, and contrast holds the whole way across
	// instead of collapsing at the end.
	const float sweep01 = sweep01For(w);
	const float sweepW = box.width * sweep01;
	drawSweep(box, corner, sweep01);

	// The ring does NOT thicken on selection: a fully swept card carries
	// that state in its own fill, and a ring that jumped width as well
	// would shift the text inside it at the moment of locking.
	drawRing(box, mmToPxX(kWidgetRingMM), mmToPxY(kWidgetRingMM), ink, corner);

	// The card carries the option's own info: `hover.broth_widgets` lays
	// out one FULL-WIDTH row per broth (`hover.broth_card_rects`),
	// spanning the band the shared info box occupies on other screens plus
	// the option row's own band. `UiLayer::draw` skips `drawInfoBox`
	// entirely on these screens (see that call site), so this card is the
	// ONLY place an option's diet and note reach the table.
	//
	// There is no swatch, no icon column and no tick, and `w.meta` and
	// `w.swatch` are not read here at all.
	//
	// The note's line count is SOLVED from the card's own remaining
	// height, never a fixed budget. `drawInfoBox` can use a fixed
	// `kInfoBoxNoteMaxLines` because there is only ever one shared box;
	// this function draws N cards of whatever height `hover.py` divided
	// the band into, for however many options the menu holds — a count
	// that changes with the menu. A card this function was never measured
	// against must still be unable to overflow its own box.
	if(!_infoFont.isLoaded() || !_infoNameFont.isLoaded()){
		ofSetColor(255);
		return;
	}
	const float padX = kInfoBoxPadXPx;
	const float leftX = box.x + padX;
	// The note draws in `_cardNoteFont`, not `_infoFont`: the same face at
	// `kCardNotePx` (16px against the shared box's 18px), which buys back
	// a line and a half of note per card. It is the card's OWN font rather
	// than a smaller `kInfoBoxTextPx`, because the shared info box on the
	// bin screen has its own band arithmetic and no reason to shrink with
	// this.
	const ofTrueTypeFont & noteFace =
		_cardNoteFont.isLoaded() ? _cardNoteFont : _infoFont;
	const float textWidth = box.width - 2.0f * padX;
	const float bodyLineH = noteFace.getAscenderHeight()
		+ fabsf(noteFace.getDescenderHeight()) + kBrothCardNoteLineGapPx;
	float y = box.y + kBrothCardPadYPx;

	// The name uses `_optionFont` (20px), not `_infoNameFont` (32px, sized
	// for a bin's shorter catalogue name). Measured against the real
	// broths at this card's own width: the longest clears this font/width
	// pair with ~30px to spare, where 32px would not.
	const ofTrueTypeFont & nameFace =
		_optionFont.isLoaded() ? _optionFont : _nameFont;
	const float nameBaseline = y + nameFace.getAscenderHeight();
	// The chilli count, right-aligned on the NAME's own line. Drawn before
	// the name so the name's width budget can subtract the strip; a broth
	// sends no icon and loses nothing.
	const int chilliCount = (w.icon == "chilli")
		? std::max(0, std::min(8, w.iconCount)) : 0;
	// Sized to the NAME's own cap height, measured off the label rather
	// than off the font's ascender: "Hot", "Medium" and "Mild" are all
	// caps and x-height with no descender, so the string's own bounding
	// box IS the letter height the pepper has to match.
	const float chilliH = chilliCount > 0
		? nameFace.getStringBoundingBox(w.label, 0, 0).height : 0.0f;
	const float chilliPitch = chilliH * (kChilliWidthFactor + kChilliGapFactor);
	const float chilliStripW = chilliCount > 0
		? (chilliCount - 1) * chilliPitch + chilliH * kChilliWidthFactor
			+ kInfoDietDotGapPx
		: 0.0f;
	const float nameWidth = textWidth - chilliStripW;
	std::string name = w.label;
	const float nameW = nameFace.getStringBoundingBox(name, 0, 0).width;
	if(nameW > nameWidth){
		name = truncateToWidth(nameFace, name, nameWidth);
		if(_truncatedNames.insert(w.label).second){
			// Shared by the broth screen's cards and the spice screen's
			// (`hover.spice_widgets`) — both draw through this branch, so
			// the id is what tells the two apart in a log.
			ofLogWarning(kTag) << "info card " << w.id << " (\""
				<< w.label << "\") needs " << nameW
				<< "px but the card leaves " << nameWidth
				<< "px for the name — truncated";
		}
	}
	// The name's own ink no longer switches on `sel` either — see this
	// function's own comment on `haloInk` above. What it DOES switch on is
	// the sweep: `splitX` is the leading edge, and every string below is
	// drawn dark first and then overdrawn in its lit ink up to that edge.
	const float splitX = box.x + sweepW;
	drawStringLitTo(nameFace, name, leftX, nameBaseline, splitX,
		w.enabled ? kInfoBoxNameColor : kWidgetDisabled, kOptionNameLitColor);

	// The peppers, laid right to left from the card's right pad so the
	// LAST one always lands on the same x whatever the count is — which is
	// what makes the three cards read as a scale rather than as three
	// unrelated rows. Centred on the name's x-height rather than its
	// baseline, so a 40px glyph beside a 20px word sits level with the
	// word instead of hanging off the bottom of it. Their red is left
	// alone by the sweep for the same reason the diet dot's hue is: it is
	// the information.
	if(chilliCount > 0){
		// Vertically centred on the letters themselves — the midpoint
		// between the baseline and the top of the caps — so a pepper the
		// same height as the word sits level with the word rather than
		// riding above or below it.
		const float chilliCy = nameBaseline - chilliH * 0.5f;
		// Right edge of the PEPPER, not of its image box, parked one
		// `padX` off the card's border so it clears the ring by the same
		// margin the name clears it on the other side. The half-width
		// subtracted here is `kChilliWidthFactor` — the INK — which is why
		// that factor is measured off the artwork's alpha rather than
		// taken as 1.0 from its square.
		const float rightCx = box.x + box.width - padX
			- chilliH * kChilliWidthFactor * 0.5f;
		for(int i = 0; i < chilliCount; i++){
			drawChilli(rightCx - i * chilliPitch, chilliCy, chilliH);
		}
	}
	y += nameFace.getAscenderHeight() + fabsf(nameFace.getDescenderHeight())
		+ kInfoBoxLineGapPx;

	// Diet dot + word — the exact pair `drawInfoBox` draws and the exact
	// reason (I8: never a state by colour alone).
	//
	// The row's HEIGHT is only reserved when there IS a row. A spice level
	// has no diet — `hover.spice_widgets` sends `diet: ""`, since a heat
	// level is not food — so advancing `y` unconditionally would open a
	// blank band on every spice card where a broth card carries
	// VEG/NON-VEG. Hence the `y +=` inside the branch.
	if(!w.diet.empty()){
		const float dietBaseline = y + _infoFont.getAscenderHeight();
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
		// The dot keeps its own hue through the sweep — it is the one mark
		// on the card that carries meaning BY colour, so inverting it
		// would be inverting the information. It sits on near-black rather
		// than near-white behind the edge, which all three diet hues have
		// enough chroma to survive.
		ofSetColor(dietColour);
		ofDrawCircle(dotCx, dietBaseline - _infoFont.getAscenderHeight() * 0.35f,
			kInfoDietDotRadiusPx);
		drawStringLitTo(_infoFont, dietWord,
			dotCx + kInfoDietDotRadiusPx + kInfoDietDotGapPx, dietBaseline,
			splitX, dietColour, kOptionNameLitColor);
		y += _infoFont.getAscenderHeight() + fabsf(_infoFont.getDescenderHeight())
			+ kBrothCardNoteLineGapPx;
	}
	// There is no meta row: `w.meta` is not read by this function at all.

	// The note fills whatever is left of the card.
	const float remaining = (box.y + box.height - kBrothCardPadYPx) - y;
	const size_t maxLines = remaining >= bodyLineH
		? (size_t)(remaining / bodyLineH) : 0;
	const std::vector<std::string> noteLines =
		wrapToLines(noteFace, w.desc, textWidth, maxLines);
	for(const std::string & line : noteLines){
		drawStringLitTo(noteFace, line, leftX, y + noteFace.getAscenderHeight(),
			splitX, kInfoBoxTextColor, kOptionNoteLitColor);
		y += bodyLineH;
	}
	// Truncation is a BUG here, not a fallback. `wrapToLines` quietly
	// ellipsises whatever did not fit, which is why an overflowing card
	// reaches the rig unnoticed — so it is logged, once per card, and the
	// next card to outgrow its box is caught on the bench instead.
	//
	// `kCardNoteLineCap` means "no cap": the helper treats 0 as "no lines
	// at all", so an unbounded wrap has to ask for a number no note will
	// ever reach.
	if(!w.desc.empty()){
		const size_t wanted =
			wrapToLines(noteFace, w.desc, textWidth, kCardNoteLineCap).size();
		if(wanted > noteLines.size()
				&& _truncatedNames.insert(w.id + ":desc").second){
			ofLogWarning(kTag) << "info card " << w.id << " note wants "
				<< wanted << " lines but only " << maxLines
				<< " fit — shrink kCardNotePx or the menu copy";
		}
	}
	ofSetColor(255);
}

void UiLayer::drawStringLitTo(const ofTrueTypeFont & f, const std::string & s,
	float x, float baseline, float splitX, const ofColor & dark,
	const ofColor & lit){
	// One string, two inks, split at `splitX` — the dwell sweep's leading
	// edge. Drawn as "the whole string dark, then the part behind the edge
	// again in the lit ink ON TOP", rather than as two substrings laid
	// side by side: a substring drawn at a computed offset would drift
	// from the full string's own glyph advances, and the drift would show
	// as the edge crossed a word. Overdrawing cannot drift, because the
	// lit pass starts at the same `x` with the same font and therefore
	// lands on exactly the same glyph positions.
	//
	// Splits on a CHARACTER boundary, not a pixel one — a half-inverted
	// letter reads as a rendering fault at three metres, where a letter
	// that flips whole reads as the edge passing it. UTF-8 continuation
	// bytes are skipped so a multi-byte codepoint is never cut in half.
	ofSetColor(dark);
	f.drawString(s, x, baseline);
	if(splitX <= x || s.empty()){
		return;
	}
	const float budget = splitX - x;
	std::string prefix;
	for(size_t i = 1; i <= s.size(); ++i){
		if(i < s.size() && (s[i] & 0xC0) == 0x80){
			continue;   // mid-codepoint, not a legal cut
		}
		const std::string cand = s.substr(0, i);
		if(f.getStringBoundingBox(cand, 0, 0).width > budget){
			break;
		}
		prefix = cand;
	}
	if(prefix.empty()){
		return;
	}
	ofSetColor(lit);
	f.drawString(prefix, x, baseline);
}

void UiLayer::drawCenteredLitTo(const ofTrueTypeFont & f, const std::string & s,
	float cx, float baseline, float splitX, const ofColor & dark,
	const ofColor & lit){
	// `drawCentered`'s own arithmetic, lifted rather than shared, because
	// `drawStringLitTo` needs the resolved LEFT edge and drawCentered only
	// ever computes it internally.
	if(s.empty() || !f.isLoaded()){
		return;
	}
	const ofRectangle bb = f.getStringBoundingBox(s, 0, 0);
	drawStringLitTo(f, s, cx - bb.width * 0.5f - bb.x, baseline, splitX,
		dark, lit);
}

void UiLayer::drawBilingualCenteredLitTo(const ofTrueTypeFont & asciiFont,
	const ofTrueTypeFont & cjkFont, const std::string & s,
	float cx, float baseline, float splitX, const ofColor & dark,
	const ofColor & lit){
	if(s.empty() || !asciiFont.isLoaded() || !cjkFont.isLoaded()){
		return;
	}
	// One cut, at the first non-ASCII byte — "EN | 中文" splits into
	// "EN | " and "中文", which is every case this exists for today (see
	// hasMixedScript's own comment on why this is content-detected
	// rather than special-cased by widget id).
	size_t cut = s.size();
	for(size_t i = 0; i < s.size(); i++){
		if((unsigned char)s[i] >= 0x80){
			cut = i;
			break;
		}
	}
	const std::string asciiPart = s.substr(0, cut);
	const std::string cjkPart = s.substr(cut);
	const float asciiW = asciiPart.empty() ? 0.0f
		: asciiFont.getStringBoundingBox(asciiPart, 0, 0).width;
	const float cjkW = cjkPart.empty() ? 0.0f
		: cjkFont.getStringBoundingBox(cjkPart, 0, 0).width;
	const float x0 = cx - (asciiW + cjkW) * 0.5f;
	if(!asciiPart.empty()){
		drawStringLitTo(asciiFont, asciiPart, x0, baseline, splitX, dark, lit);
	}
	if(!cjkPart.empty()){
		drawStringLitTo(cjkFont, cjkPart, x0 + asciiW, baseline, splitX, dark, lit);
	}
}

float UiLayer::sweep01For(const StateLink::Widget & w) const {
	// The diner's dwell while they are hovering, pinned to 1 once the
	// choice is locked — a locked control is simply one whose sweep
	// finished and stayed. That is what makes a full-dark card the
	// READABLE state rather than the unreadable one.
	//
	// The sweep is TIED TO TIME, because the wire value can fall off a
	// cliff mid-fill and a sweep rendered directly from it flickers.
	//
	// Two causes, and a latch only covers the first: core clears `dwell` a
	// tick before it sends `selected`, and — more often — the tracker
	// drops the hand for a frame or two mid-dwell, core sees no hover, and
	// `dwell` arrives as 0 in the middle of a fill.
	//
	// So oF no longer renders the wire value directly. It renders its own
	// per-widget value that RISES instantly (progress must feel immediate
	// under the hand) but can only FALL on this renderer's own clock:
	// nothing happens for `kSweepFallDelayS`, which outlasts any dropout
	// or state-message gap, and only then does it ease to the new target
	// over `kSweepFallS`. A genuinely abandoned dwell still clears — it
	// just takes a fifth of a second and slides instead of blinking.
	//
	// This does not make oF time the DWELL (doc §9.4 — core still owns
	// that, and `w.dwell` is still the only input). It times the
	// animation of a value core already decided, which is the same thing
	// `BinTween` has always done for the bins.
	if(!w.enabled){
		_sweepAnim.erase(w.id);
		return 0.0f;
	}
	const float target = w.selected ? 1.0f : ofClamp(w.dwell, 0.0f, 1.0f);
	const float now = ofGetElapsedTimef();
	SweepAnim & a = _sweepAnim[w.id];
	if(a.t0 <= 0.0f){                 // first sight of this widget
		a.value = target;
		a.fallFrom = target;
		a.t0 = now;
		return a.value;
	}
	if(target >= a.value){
		a.value = target;
		a.fallFrom = target;
		a.t0 = now;                   // resets the fall clock on any rise
		return a.value;
	}
	// Falling. `t0` is when the value last rose, so this is how long the
	// target has been below it.
	const float held = now - a.t0;
	if(held < kSweepFallDelayS){
		return a.value;               // the dropout window — hold, do not blink
	}
	const float k = ofClamp((held - kSweepFallDelayS) / kSweepFallS, 0.0f, 1.0f);
	a.value = ofLerp(a.fallFrom, target, k);
	return a.value;
}

void UiLayer::drawSweep(const ofRectangle & box, float corner, float sweep01){
	const float sweepW = box.width * sweep01;
	if(sweepW > 1.0f){
		// Clipped to the control's own rounded rect by drawing the sweep as
		// a rounded rect of the SAME corner radius and then squaring off its
		// right edge with a plain rect — an intersection would need a
		// stencil, and the sweep's right edge is a straight cut by design.
		//
		// The squaring rect spans the FULL height. Inset by `corner` top
		// and bottom it squares only the middle of the leading edge and
		// leaves the sweep's top-right and bottom-right corners curved, so
		// a half-filled card reads as a black lozenge sliding across
		// rather than as a bar filling. Only the LEFT corners are ever
		// round, and those come from the rounded rect underneath.
		drawRoundedRectFill(ofRectangle(box.x, box.y, sweepW, box.height),
			corner, kOptionSweepColor);
		if(sweepW > corner){
			ofSetColor(kOptionSweepColor);
			ofDrawRectangle(box.x + sweepW - corner, box.y,
				corner, box.height);
		}
	}
	// The leading edge, in the dwell amber — so "how far along am I" is
	// legible even where the sweep happens to be crossing blank space
	// rather than a letter. Drawn only while the sweep is actually moving:
	// at rest (0) and at lock (1) there is no progress to report, and a
	// stray amber bar down a locked control's right edge would read as a
	// second, unexplained state.
	if(sweep01 > 0.01f && sweep01 < 0.99f){
		ofSetColor(kOptionSweepEdgeColor);
		ofDrawRectangle(box.x + sweepW - kOptionSweepEdgePx, box.y,
			kOptionSweepEdgePx, box.height);
	}
}

void UiLayer::drawWidgetGlows(const StateLink::State & state) const {
	// EVERY halo, before ANY of the centre column.
	//
	// A halo reaches `kWidgetGlowReachPx` past its own frame, which on the
	// stacked option cards clears the neighbour above and below and, for
	// the top card, reaches up into the page title's descender — where it
	// reads as a mysterious box cutting off the heading. Splitting
	// `drawWidgets` into two loops is not enough, because the whole of
	// `drawWidgets` runs after `drawPageHeader`; the glow pass has to be
	// out here, before the header, the cart and the info box, so that
	// everything in the column paints on top of every halo.
	//
	// The other half of this is in `drawWidget`: the card fill is only
	// ~10% ink, so without an opaque base a neighbour's halo shows THROUGH
	// a card drawn over it. See `kCardBaseColor`.
	for(const StateLink::Widget & w : state.widgets){
		drawWidgetGlow(w);
	}
}

void UiLayer::drawWidgets(const StateLink::State & state) const {
	// Bodies only — the halos are `drawWidgetGlows`, called much earlier
	// in `draw()`. See there.
	for(const StateLink::Widget & w : state.widgets){
		drawWidget(w);
	}
}

void UiLayer::drawWidgetGlow(const StateLink::Widget & w) const {
	// Pass one of `drawWidgets` — see its own comment. Deliberately
	// recomputes `ink`/`glow01`/`corner` the same way `drawWidget` does
	// rather than caching them across the two loops: they are three cheap
	// expressions off the widget, and a cache would be a second place
	// that has to agree with `drawWidget` about what a widget looks like.
	if(!w.enabled){
		return;
	}
	const float glow01 = (w.hover || w.selected) ? 1.0f
		: breath(kWidgetBreathFloor);
	if(glow01 <= 0.0f){
		return;
	}
	const ofRectangle box(w.x, w.y, w.w, w.h);
	const float corner = std::min(kWidgetCornerPx,
		std::min(box.width, box.height) * 0.5f);
	if(w.kind == "option"){
		// The option plates' own rule: only the GLOW carries selection, and
		// it carries it in the accent hue — see `drawOptionPlate`.
		const bool sel = w.selected;
		drawGlow(box, corner, kWidgetGlowReachPx, kWidgetGlowBands,
			glowTint(sel ? kWidgetPrimary : kInkColor),
			(int)((sel ? kOptionSelectedGlowAlpha : kWidgetGlowAlpha) * glow01));
		return;
	}
	ofColor ink = kWidgetSecondary;
	if(w.style == "primary"){
		ink = kWidgetPrimary;
	}
	else if(w.style == "danger"){
		ink = kWidgetDanger;
	}
	const bool primary = w.style == "primary";
	drawGlow(box, corner, kWidgetGlowReachPx, kWidgetGlowBands, glowTint(ink),
		(int)((primary ? kWidgetPrimaryGlowAlpha : kWidgetGlowAlpha) * glow01));
}

void UiLayer::drawCheckout(const StateLink::State & state) const {
	// doc §18.1's CHECKOUT screen and §18.2's payment mock. The whole
	// purpose is that a diner can scan the code off the projected plywood
	// with their own phone, so this is sized for a camera at arm's length
	// rather than for a reader at three metres.
	//
	// Two screens in one function, and which one shows is decided by
	// `qr.token`, never by `qr.paid`. Core leaves the token empty until
	// the money has landed (see StateLink::Qr::token), so this side cannot
	// draw a number early even by mistake: the rule lives on the wire
	// rather than in a condition here that a later edit could invert.
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
		//
		// TWO hint lines, not one: core sends what to do now in `hint` and
		// what happens next in `hint2` (see StateLink::Screen). Both are
		// counted into the group height BEFORE anything is drawn, so the
		// block stays centred whether core sent one line, two or none — a
		// second line appearing must not push the token off centre.
		const float lineH = _infoFont.isLoaded()
			? _infoFont.getAscenderHeight()
				+ fabsf(_infoFont.getDescenderHeight())
			: 0.0f;
		const bool haveHint = _infoFont.isLoaded()
			&& !state.screen.hint.empty();
		const bool haveHint2 = _infoFont.isLoaded()
			&& !state.screen.hint2.empty();
		const float hintH = (haveHint ? kTokenHintGapPx + lineH : 0.0f)
			+ (haveHint2 ? kTokenHintLineGapPx + lineH : 0.0f);
		const float y = bandTop
			+ (bandBottom - bandTop - blockH - hintH) * 0.5f;
		ofSetColor(kAccentInk);
		drawCentered(big, qr.token, cx, y + big.getAscenderHeight());
		float hintY = y + blockH;
		if(haveHint){
			hintY += kTokenHintGapPx;
			ofSetColor(kInfoBoxTextColor);
			drawCentered(_infoFont, state.screen.hint, cx,
				hintY + _infoFont.getAscenderHeight());
			hintY += lineH;
		}
		if(haveHint2){
			hintY += kTokenHintLineGapPx;
			// Dimmer than the first line, deliberately: line one is an
			// instruction the diner acts on before leaving the table,
			// line two is a promise about later. Equal weight would make
			// the diner read two commands and look for the second thing
			// to do.
			ofSetColor(kInfoBoxTextColor, kTokenHint2Alpha);
			drawCentered(_infoFont, state.screen.hint2, cx,
				hintY + _infoFont.getAscenderHeight());
		}
		ofSetColor(255);
		return;
	}

	// --- unpaid: the code to scan -------------------------------------
	//
	// The quiet zone is drawn, not assumed. A QR needs a margin of
	// blank around it to be found at all, and this table's background is
	// not blank — the fluid layer is underneath and the halos reach in
	// from the bins. So a white plate goes down first, at full strength,
	// exactly like the light-pass cutouts do for the same reason (I9).
	//
	// Sized to kQrTargetSidePx, not to the space available. The old
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

void UiLayer::drawSkeleton(const std::vector<SkeletonLink::Hand> & hands) const {
	// The cursor-lag diagnostic — see this method's declaration.
	// Deliberately the simplest possible draw: no tween, no hysteresis, no
	// role, nothing hidden past a hold time. Whatever SkeletonLink last
	// accepted is drawn exactly as it arrived, so what is on the table
	// this frame is the raw signal for this frame and nothing else.
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
	bool connected, float fps, bool audioMuted) const {
	char buf[160];
	snprintf(buf, sizeof(buf), "fps %.0f  link %s  seq %lld  audio %s",
		fps, connected ? "up" : "down", hasState ? (long long)state.seq : -1LL,
		audioMuted ? "MUTED (m)" : "on (m)");
	ofSetColor(140, 140, 140);
	_devFont.drawString(buf, 16, 1080.0f - 16.0f);
	ofSetColor(255);
}

void UiLayer::draw(bool hasState, const StateLink::State & state,
	bool connected, float staleSeconds, float fps, bool showDevOverlay,
	const std::vector<CursorLink::Hand> & hands,
	const CursorLink::Hand * pointer, bool audioMuted) const {
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

	// On an idle table everything hides except the bin halos and the brand
	// mark, so that the hidden UI is itself the signal that the table is
	// idle and the wandering fireball is the only thing left to look at.
	//
	// Halo (layer 4, just above) and the brand mark (immediately above
	// this) are drawn OUTSIDE this gate on purpose — they are the two
	// things that must survive it. Everything else in layer 5 (plates,
	// cart, widgets, banner, info box) is exactly this one block, so
	// gating its entry is the whole mechanism and nothing inside needs to
	// know about idle attract at all.
	if(hasState && !state.idleAttract){
		// Once per frame, ahead of the bins: drawBin's price line and
		// drawTotal's numeral both format off this same prefix/decimals
		// pair, pulled from the one locale-resolved string the wire gives
		// oF (state.total.text) — see splitCurrencyText's comment.
		splitCurrencyText(state.total.text, _currencyPrefix, _currencyDecimals);
		for(int i = 0; i < 8 && i < (int)state.bins.size(); i++){
			drawBin(i, state.bins[i], _bins[i]);
		}

		// The centre column is a stack of PAGES, and exactly one is up at
		// a time. The option widgets' rects live in the cart's own band
		// (core/hover.py's `_cart_band_px`) and the cart stops drawing on
		// the screens that are not the cart — both halves are needed, or
		// the option plates land on top of a cart still being drawn
		// underneath them.
		//
		// The band above is the info box on every page EXCEPT the two
		// option pages. There, a page header takes the top of that band
		// (drawPageHeader) and the box either moves down by exactly the
		// header's height or does not draw at all — see `optionPage`.
		const bool optionPage = state.phase == "broth" || state.phase == "spice";
		// Neither option page shares the info box. `hover.broth_widgets`
		// and `hover.spice_widgets` both lay out one full-height card per
		// option through `hover.broth_card_rects`, spanning the info box's
		// band and the option row's band combined, and `drawOptionPlate`
		// draws the name, diet and note directly into that card.
		//
		// Drawing the shared box on top would either duplicate that text
		// or — since nothing is ever hovered on a card that fills its own
		// band — reserve a strip the cards have already grown into. Every
		// option's info shows at once, so a single-level box would be
		// redundant regardless.
		const bool payPage = state.overlayKind == "qr";
		// A banner outranks a header — the same precedence doc §14.5
		// sets for this column, and the one drawInfoBox follows: the state
		// that changes what the table is DOING wins. Without it an `error`
		// overlay raised mid-order, which happens while SERVING, draws the
		// fault banner and the page title on top of each other.
		const bool bannerUp = state.overlayKind == "uncalibrated"
			|| state.overlayKind == "error" || state.mode == "setting";
		// `headed` is one condition and the header/box move together.
		// An earlier cut had the header drawing whenever core sent a
		// title but the box only stepping down on the option pages, which
		// put "Your Order" straight through the top of the info box on
		// the cart screen. They are the same fact and are read from the
		// same bool now.
		const bool headed = !state.screen.title.empty() && !bannerUp;

		// Every widget's halo, ahead of everything else in this column —
		// the header included. See `drawWidgetGlows` for why it cannot sit
		// with the widget bodies further down.
		drawWidgetGlows(state);

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
	else if(hasState && state.idleAttract){
		// The one thing besides the halo and the brand mark allowed on an
		// idle table — see drawIdleHand and the idleAttract gate above.
		drawIdleHand();
	}

	// Nothing is drawn for the cursor. The fluid fire (ofApp's
	// `fluidActive`, on every page) IS the pointer, and dwell progress is
	// reported on the widget rather than under the hand — so `pointer`
	// arrives here as nullptr on purpose. The parameter is kept because
	// ofApp's `_ui.draw(...)` signature and the `drawAboveLightPass` path
	// both still carry it.
	(void)pointer;

	drawConnectionIndicator(connected, staleSeconds);
	if(showDevOverlay){
		drawDevOverlay(hasState, state, connected, fps, audioMuted);
	}
}
