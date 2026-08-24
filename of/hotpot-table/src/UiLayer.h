#pragma once

#include "ofMain.h"
#include "CursorLink.h"
#include "SkeletonLink.h"
#include "Spring.h"
#include "StateLink.h"

#include <array>
#include <string>
#include <vector>

// v3 doc §13: draw `state`, tweened, onto Stage's content FBO. UiLayer never
// touches a socket or a frame (I2/I3) — everything it draws comes from the
// StateLink::State ofApp hands it every update().
//
// English only (M1.4's scope per doc §21's build item 4). Font sizes are
// the literal px column of §13.4's table, not a runtime mm conversion —
// that table already did the mm->px math once, at 1.26 px/mm, and gave a
// fixed answer per element; recomputing it per element here would just be
// the same numbers with more places to get the rounding wrong.
class UiLayer {
public:
	void setup();

	// dtSeconds steps every spring; state/connected/staleSeconds decide what
	// the springs' targets and the connection indicator are this frame.
	void update(float dtSeconds, bool hasState, const StateLink::State & state);

	// Must be called with Stage's content FBO already begin()'d — this
	// class only ever draws, it never owns or clears a framebuffer itself.
	// showDevOverlay gates only the fps/link/seq corner readout — off by
	// default (diner-facing table, not a debug console), toggled by ofApp
	// on a keypress.
	// `hands` is CursorLink's latest, already gated to nothing when the
	// tracker has gone quiet. UiLayer draws a cursor for the POINTER only
	// (doc §11.4) — an ambient hand is passed in because M8's fluid will
	// want its position, not because anything is drawn at it.
	void draw(bool hasState, const StateLink::State & state,
		bool connected, float staleSeconds, float fps, bool showDevOverlay,
		const std::vector<CursorLink::Hand> & hands = {},
		const CursorLink::Hand * pointer = nullptr) const;

	// Stage's light pass needs exactly these rects, in stage px, and they
	// must be the SAME rects the plates are drawn against — that identity
	// is what stops a plate's ink from ever landing inside its own cutout.
	std::vector<ofRectangle> cutoutRectsPx() const;

	// VISUAL_LAYER.md §9 build item 6: one entry per bin currently
	// crossfading toward "active" (StateLink::Bin::hl == "hover"), for
	// ofApp to hand to FluidLayer::update() so the sim emits into the same
	// annulus this bin's halo is fading out of (drawHalo reads the same
	// spring). `bin` is binRectPx(i) — the same rect drawHalo/drawBin use —
	// so the fluid, the halo and the light-pass cutout can never disagree
	// about where a bin actually is. Empty when no bin is active.
	struct FireEmitter {
		ofRectangle bin;
		float cornerRadiusPx = 0.0f;
		float innerOffsetPx = 0.0f;
		float outerOffsetPx = 0.0f;
		float intensity = 0.0f;
		// 2026-08-14: which bin this is, 0-7 — so ofApp can pick this
		// bin's own colour out of FluidLayer's palette (developer request,
		// "various colour flame for each bin"). Not read by FluidLayer
		// itself as a bin id, only forwarded as FireRing::colourIndex —
		// see that struct's own comment.
		int binIndex = 0;
	};
	std::vector<FireEmitter> fireEmitters() const;

	// **Diagnostic, 2026-08-14 — off unless a human presses 'f'.** Pins
	// every bin's fire spring to full, ignoring `hl` entirely, so all 8
	// rings crossfade in and inject at exactly the same intensity at the
	// same time.
	//
	// It exists to bisect the "left bins have too much flame" report, which
	// has now outlived a bin-grid recalibration, a homography
	// recalibration, and the hue/buoyancy fix in FluidLayer.cpp — three
	// plausible causes, each confirmed real, none of them it. With hover
	// taken out of the loop and every bin driven identically, a screenshot
	// ('p') splits what is left in half: 8 matching rings means the
	// asymmetry rides in on hover/tracking, upstream of this file; 8
	// different-looking rings means it is in the fluid or the geometry,
	// here. Either answer eliminates half the remaining search space, which
	// is the thing three rounds of reasoning from code have not managed.
	//
	// A key toggle rather than a compile-time switch (ofApp.cpp's own
	// kFluidDebugMouseOnly pattern) for two reasons: it defaults OFF so it
	// can never reach a diner, and it needs no rebuild to use — the rebuild
	// loop here costs the whole process tree.
	void setForceAllBinsLit(bool on){ _forceAllBinsLit = on; }
	bool forceAllBinsLit() const { return _forceAllBinsLit; }

	// 2026-08-12: draws ONLY the pointer cursor + dwell ring, with no
	// cutout it cannot reach. This is the ONE place the cursor is drawn
	// while serving — `draw()` itself skips its own cursor block in that
	// mode, specifically so the cursor is never drawn twice in a frame.
	// Meant to be passed as `Stage::compositeAndWarp`'s
	// `drawAboveLightPass` callback, and ONLY while `state.mode ==
	// "serving"` (ofApp's call site decides that, this method does not
	// check mode itself) — see that parameter's own comment for why this
	// is safe. A no-op when `pointer` is null, same as `draw()`'s own.
	void drawCursorAboveLightPass(const StateLink::State & state,
		const CursorLink::Hand * pointer) const;

	// RIG_FEEDBACK item 11 diagnostic (SkeletonLink.h's own docstring): the
	// raw, unsmoothed MediaPipe skeleton, drawn plainly — no tween, no
	// dwell ring, no role — for a side-by-side comparison against the real
	// (processed) cursor on the same table. Called from ofApp right after
	// draw(), inside the same content pass, so it is subject to the same
	// keystone warp and the same light-pass erasure over a bin cutout that
	// the ordinary cursor was subject to before item 1's fix — seeing that
	// happen here IS part of the diagnostic. Must be called with Stage's
	// content FBO already begin()'d, same requirement as draw().
	void drawSkeleton(const std::vector<SkeletonLink::Hand> & hands) const;

private:
	// See setForceAllBinsLit(). Never persisted, never on the wire — a
	// diagnostic lives and dies inside one run of the app.
	bool _forceAllBinsLit = false;

	struct BinTween {
		Spring picked{0.15f};
		Spring price{0.15f};
		// VISUAL_LAYER.md §6 Active: "Gold halo crossfades OUT as the fire
		// ring crossfades IN." Slower than picked/price's 150ms — those
		// track a fact (weight, cost) that should read as near-instant;
		// this is a deliberate cross-dissolve, and 150ms reads as a flicker
		// at the alpha ranges drawHalo/fireEmitters() use. 350ms is an
		// unmeasured starting guess, tunable once seen projected, same as
		// every other new VISUAL_LAYER constant this session.
		Spring fire{0.35f};
	};

	// Not static any more (M4 build item 4): the bin rects come from core
	// when it has them (StateLink::Bin::hasRect — doc §5.3, core owns
	// rects in both spaces) and fall back to TableGeometry.h's CAD layout
	// when it does not. Cached in update() rather than read out of
	// `state` at draw time, because cutoutRectsPx() is called by ofApp
	// AFTER endContent() and has no state to read.
	ofRectangle binRectPx(int i) const;
	ofRectangle cutoutRectPx(int i) const;
	static ofRectangle cadBinRectPx(int i);
	// cornerRadiusPx rounds the ring's corners to match a rounded cutout
	// (0 = square, the old four-bars behaviour — what widgets still use).
	static void drawRing(const ofRectangle & cut, float widthX, float widthY,
		const ofColor & colour, float cornerRadiusPx = 0.0f);
	// A FILLED annulus, and an arc of one — doc §13.4: "circular rings —
	// the M5 dwell ring, M8's halos: a filled ofPath built from an outer
	// arc and an inner arcNegative. Never two ofDrawCircle calls with the
	// background colour punched through the middle: over a fluid there is
	// no background colour to punch with."
	static void drawAnnulus(float cx, float cy, float rOuter, float rInner,
		const ofColor & colour, float startDeg = 0.0f, float endDeg = 360.0f);
	// VISUAL_LAYER.md §6 idle state, build item 4: the ~16 nested "strokes"
	// around a bin, breathing, phase-offset by _haloPhase[i]. A generalised
	// drawRing — same filled-band, ODD-winding technique (drawRing's own
	// comment on why an actual ofPath stroke is unusable here), but with a
	// nonzero INNER offset too, so many bands can nest around one rect
	// without each one redrawing the disc drawRing itself always starts
	// from.
	static void drawRoundedBand(const ofRectangle & base, float innerOffsetPx,
		float outerOffsetPx, const ofColor & colour, float baseCornerRadiusPx);
	void drawHalo(int i) const;
	void drawWidgets(const StateLink::State & state) const;
	void drawWidget(const StateLink::Widget & w) const;
	void drawCursor(const CursorLink::Hand & pointer, float dwell) const;
	float dwellFraction(const StateLink::State & state) const;
	void drawBin(int i, const StateLink::Bin & b, const BinTween & tw) const;
	// VISUAL_LAYER.md §8/§9 build item 9: the running total now draws as
	// one receipt-style line (label left, value right) inside the cart
	// footer drawCart lays out, not the old standalone centred numeral
	// near the table's diner edge — baselineY is drawCart's to compute
	// since it is the one call site now.
	void drawTotal(const StateLink::Total & total, float baselineY) const;
	// VISUAL_LAYER.md §8, build item 9: the cart panel — 8 fixed row
	// slots bound to bins in PICK ORDER (see update()'s own binding
	// logic and _cartSlotBin's comment below), the divider and the total
	// (via drawTotal above). **Confirm/Cancel are NOT drawn here** — they
	// are real widgets on the wire now (core/hover.py), drawn by
	// drawWidget like any other, so the rect a hand is hit-tested against
	// is the same rect it sees. See drawCart's own closing comment.
	void drawCart(const StateLink::State & state) const;
	// The lowest px the cart's own ink reaches. Exists so setup()'s
	// cross-file check against core/hover.py's button band measures the
	// same number drawCart lays out from, rather than a second estimate.
	float cartBottomPx() const;
	void drawConnectionIndicator(bool connected, float staleSeconds) const;
	void drawBanner(const ofColor & fill, const ofColor & ink,
		const std::string & headline, const std::string & subline) const;
	void drawTopBanner(const StateLink::State & state) const;
	void drawBrandMark() const;
	void drawDevOverlay(bool hasState, const StateLink::State & state,
		bool connected, float fps) const;
	std::string _priceText(double amount) const;

	// Set once per draw() from state.total.text (the one locale-resolved
	// string the wire gives oF) and read by both drawBin and drawTotal —
	// see UiLayer.cpp's splitCurrencyText for why. mutable because they are
	// draw-time formatting cache, not state, and draw() is const.
	mutable std::string _currencyPrefix;
	mutable int _currencyDecimals = 2;

	// 28px/22px — no longer the bin plate's own fonts (see _plateNameFont/
	// _plateRateFont below, VISUAL_LAYER.md build item 2). Kept at this
	// size for the banner headline/subline and the M5 widget label, none
	// of which VISUAL_LAYER.md has resized.
	ofTrueTypeFont _nameFont;
	ofTrueTypeFont _detailFont;
	// VISUAL_LAYER.md section 3: the bin plate's own two lines. Separate
	// font objects from _nameFont/_detailFont above so retyping the plate
	// (this step) cannot also resize the banner or a widget label as a
	// side effect — nothing in the doc's step 2 asks for that.
	// 2026-08-14, two rig photos, same day: 40px overflowed a bin (rig
	// photo — see kPlateNamePx in UiLayer.cpp) and is now 28px, with
	// core's `label` wrapped to at most 2 lines (drawBin's own
	// wrapNameToTwoLines) rather than pre-shortened — a same-day
	// `shortLabel` catalogue field was tried and deleted on developer
	// instruction. Rate line is DejaVuSansMono (kMonoFontFile), not the
	// bold face — regular weight per the doc, plus monospace so a picked
	// price's width doesn't shift digit to digit — but at a smaller size
	// than the doc's 26px (see kPlateRatePx): a second rig photo showed it
	// reading visually BIGGER than the name despite the smaller nominal
	// size, because DejaVuSansMono's cap-height runs larger relative to
	// its point size than the proportional bold face's does.
	ofTrueTypeFont _plateNameFont;  // 28px bold DejaVuSans, ink #2B2118
	ofTrueTypeFont _plateRateFont;  // 18px regular DejaVuSansMono, ink #6AA84F
	// VISUAL_LAYER.md §3: "Total value" 48px bold / "Total label" 30px —
	// resized from the pre-cart 80px/28px (this file's own git history)
	// now that both draw as one receipt line inside the cart footer
	// (drawCart/drawTotal) instead of the old free-standing giant numeral.
	ofTrueTypeFont _totalNumFont;   // 48px bold, "Total value"
	ofTrueTypeFont _totalLabelFont; // 30px, "Total label"
	// VISUAL_LAYER.md §3: "Cart row — filled name" / "— filled g + cost,"
	// both 26px, one face — only the ink colour differs between the two
	// (drawCart sets it per column), so one font object serves both.
	ofTrueTypeFont _cartRowFont;    // 26px bold, cart row name + detail
	ofTrueTypeFont _devFont;       // 16px, "Developer overlay"
	bool _fontsLoaded = false;

	ofImage _brandLogo;   // "The Hotpottery" mark — see drawBrandMark
	bool _brandLogoLoaded = false;

	std::array<BinTween, 8> _bins;
	Spring _totalAmount{0.15f};
	// VISUAL_LAYER.md §6's "phase-offset by a per-bin random seed" started
	// as literal per-bin randomness, then a deterministic even spacing
	// (both superseded — see setup()'s own comment on why). 2026-08-14,
	// developer's own design: a fixed rotation around each 2x2 island, set
	// once in setup(), not per frame — a phase that itself moved would
	// defeat the point of a fixed per-bin offset.
	std::array<float, 8> _haloPhase{};

	// The stage-space rects core last sent, and whether it sent any. An
	// absence is NOT "a rect at the origin" — see StateLink::Bin::hasRect.
	std::array<ofRectangle, 8> _coreRects;
	std::array<bool, 8> _hasCoreRect{};

	// VISUAL_LAYER.md §8, build item 9: which bin (0-7, or -1 if the slot
	// is still blank) each of the cart's 8 fixed row SLOTS is bound to.
	// Index is the SLOT (vertical position, top to bottom), value is the
	// BIN — the inverse of everything else in this class, which is always
	// indexed by bin. Bound the first time a bin's `picked` crosses above
	// 0 (update()'s own logic), in slot order; stays bound afterward even
	// if that bin's picked amount returns to 0 (doc §8: "the SAME slot
	// updates in place — it never creates a second row and never moves").
	// Purely a rendering decision — core sends no "pick order" field and
	// does not need one, since nothing about pricing or the FSM depends
	// on which row a bin's numbers happen to sit in.
	std::array<int, 8> _cartSlotBin{{-1, -1, -1, -1, -1, -1, -1, -1}};
};
