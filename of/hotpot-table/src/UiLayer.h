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
	struct BinTween {
		Spring picked{0.15f};
		Spring price{0.15f};
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
	void drawTotal(const StateLink::Total & total) const;
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
	ofTrueTypeFont _totalNumFont;  // 80px, "Running total, numeral"
	ofTrueTypeFont _totalLabelFont;// 28px, "Total label"
	ofTrueTypeFont _devFont;       // 16px, "Developer overlay"
	bool _fontsLoaded = false;

	ofImage _brandLogo;   // "The Hotpottery" mark — see drawBrandMark
	bool _brandLogoLoaded = false;

	std::array<BinTween, 8> _bins;
	Spring _totalAmount{0.15f};
	// VISUAL_LAYER.md §6: "each bin phase-offset by a per-bin random seed
	// so the 8 do not pulse in sync." Rolled once in setup(), not per
	// frame — a phase that itself moved would defeat the point of a fixed
	// per-bin offset.
	std::array<float, 8> _haloPhase{};

	// The stage-space rects core last sent, and whether it sent any. An
	// absence is NOT "a rect at the origin" — see StateLink::Bin::hasRect.
	std::array<ofRectangle, 8> _coreRects;
	std::array<bool, 8> _hasCoreRect{};
};
