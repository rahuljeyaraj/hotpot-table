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
		Spring scale{0.15f};
		Spring colR{0.15f}, colG{0.15f}, colB{0.15f};
		float lastPicked = 0.0f;
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
	static void drawRing(const ofRectangle & cut, float widthX, float widthY,
		const ofColor & colour);
	// A FILLED annulus, and an arc of one — doc §13.4: "circular rings —
	// the M5 dwell ring, M8's halos: a filled ofPath built from an outer
	// arc and an inner arcNegative. Never two ofDrawCircle calls with the
	// background colour punched through the middle: over a fluid there is
	// no background colour to punch with."
	static void drawAnnulus(float cx, float cy, float rOuter, float rInner,
		const ofColor & colour, float startDeg = 0.0f, float endDeg = 360.0f);
	void drawWidgets(const StateLink::State & state) const;
	void drawWidget(const StateLink::Widget & w) const;
	void drawCursor(const CursorLink::Hand & pointer, float dwell) const;
	float dwellFraction(const StateLink::State & state) const;
	static ofColor highlightColour(const std::string & hl);
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

	ofTrueTypeFont _nameFont;      // 36px, doc §13.4 "Bin item name"
	ofTrueTypeFont _detailFont;    // 26px, "Bin weight / unit price"
	ofTrueTypeFont _totalNumFont;  // 80px, "Running total, numeral"
	ofTrueTypeFont _totalLabelFont;// 28px, "Total label"
	ofTrueTypeFont _devFont;       // 16px, "Developer overlay"
	bool _fontsLoaded = false;

	ofImage _brandLogo;   // "The Hotpottery" mark — see drawBrandMark
	bool _brandLogoLoaded = false;

	std::array<BinTween, 8> _bins;
	Spring _totalAmount{0.15f};

	// The stage-space rects core last sent, and whether it sent any. An
	// absence is NOT "a rect at the origin" — see StateLink::Bin::hasRect.
	std::array<ofRectangle, 8> _coreRects;
	std::array<bool, 8> _hasCoreRect{};
};
