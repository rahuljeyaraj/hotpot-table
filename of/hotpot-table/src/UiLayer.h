#pragma once

#include "ofMain.h"
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
	void draw(bool hasState, const StateLink::State & state,
		bool connected, float staleSeconds, float fps) const;

	// Stage's light pass needs exactly these rects, in stage px, and they
	// must be the SAME rects the plates are drawn against — that identity
	// is what stops a plate's ink from ever landing inside its own cutout.
	std::vector<ofRectangle> cutoutRectsPx() const;

private:
	struct BinTween {
		Spring picked{0.15f};
		Spring price{0.15f};
		Spring scale{0.15f};
		Spring colR{0.15f}, colG{0.15f}, colB{0.15f};
		float lastPicked = 0.0f;
	};

	static ofRectangle binRectPx(int i);
	static ofColor highlightColour(const std::string & hl);
	void drawBin(int i, const StateLink::Bin & b, const BinTween & tw) const;
	void drawTotal(const StateLink::Total & total) const;
	void drawConnectionIndicator(bool connected, float staleSeconds) const;
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

	std::array<BinTween, 8> _bins;
	Spring _totalAmount{0.15f};
};
