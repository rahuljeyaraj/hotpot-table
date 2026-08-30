#pragma once

#include "ofMain.h"
#include "CursorLink.h"
#include "SkeletonLink.h"
#include "Spring.h"
#include "StateLink.h"

#include <array>
#include <map>
#include <set>
#include <string>
#include <vector>

// doc §13: draw `state`, tweened, onto Stage's content FBO. UiLayer never
// touches a socket or a frame (I2/I3) — everything it draws comes from the
// StateLink::State ofApp hands it every update().
//
// Font sizes are the literal px column of doc §13.4's table, not a runtime
// mm conversion: that table already did the mm->px math once, at 1.26 px/mm,
// and gave a fixed answer per element. Recomputing it here would produce the
// same numbers with more places to get the rounding wrong.
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
	// (doc §11.4); an ambient hand is passed in because the fluid wants its
	// position, not because anything is drawn at it.
	void draw(bool hasState, const StateLink::State & state,
		bool connected, float staleSeconds, float fps, bool showDevOverlay,
		const std::vector<CursorLink::Hand> & hands = {},
		const CursorLink::Hand * pointer = nullptr,
		bool audioMuted = false) const;

	// Stage's light pass needs exactly these rects, in stage px, and they
	// must be the SAME rects the plates are drawn against — that identity
	// is what stops a plate's ink from ever landing inside its own cutout.
	std::vector<ofRectangle> cutoutRectsPx() const;

	// One entry per bin currently crossfading toward "active"
	// (StateLink::Bin::hl == "hover"), for
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
		// Which bin this is, 0-7, so ofApp can pick this bin's colour out
		// of FluidLayer's palette. Not read by FluidLayer as a bin id, only
		// forwarded as FireRing::colourIndex — see that struct's comment.
		int binIndex = 0;
	};
	std::vector<FireEmitter> fireEmitters() const;

	// Diagnostic, off unless a human presses 'f'. Pins every bin's fire
	// spring to full, ignoring `hl` entirely, so all 8 rings crossfade in
	// and inject at the same intensity at the same time.
	//
	// It exists to bisect a per-bin difference in flame strength. With hover
	// taken out of the loop and every bin driven identically, a screenshot
	// ('p') splits the search space in half: 8 matching rings mean the
	// asymmetry rides in on hover or tracking, upstream of this file; 8
	// different-looking rings mean it is in the fluid or the geometry, here.
	//
	// A key toggle rather than a compile-time switch, for two reasons: it
	// defaults off so it can never reach a diner, and it needs no rebuild to
	// use, where a rebuild costs the whole process tree.
	void setForceAllBinsLit(bool on){ _forceAllBinsLit = on; }
	bool forceAllBinsLit() const { return _forceAllBinsLit; }

	// Cursor-lag diagnostic (see SkeletonLink.h): the raw, unsmoothed
	// MediaPipe skeleton, drawn plainly — no tween, no dwell ring, no role —
	// for a side-by-side comparison against the processed cursor on the same
	// table. Called from ofApp right after draw(), inside the same content
	// pass, so it is subject to the same keystone warp and the same
	// light-pass erasure over a bin cutout; seeing that happen IS part of
	// the diagnostic. Must be called with Stage's content FBO already
	// begin()'d, same requirement as draw().
	void drawSkeleton(const std::vector<SkeletonLink::Hand> & hands) const;

private:
	// See setForceAllBinsLit(). Never persisted, never on the wire — a
	// diagnostic lives and dies inside one run of the app.
	bool _forceAllBinsLit = false;

	struct BinTween {
		Spring picked{0.15f};
		Spring price{0.15f};
		// VISUAL_LAYER.md §6: the gold halo crossfades OUT as the fire ring
		// crossfades IN. Slower than picked/price's 150ms, which track a
		// fact (weight, cost) that should read as near-instant. This is a
		// deliberate cross-dissolve, and 150ms reads as a flicker at the
		// alpha ranges drawHalo and fireEmitters() use. 350ms is a starting
		// value, tunable once seen projected.
		Spring fire{0.35f};
	};

	// Not static: the bin rects come from core
	// when it has them (StateLink::Bin::hasRect — doc §5.3, core owns
	// rects in both spaces) and fall back to TableGeometry.h's CAD layout
	// when it does not. Cached in update() rather than read out of
	// `state` at draw time, because cutoutRectsPx() is called by ofApp
	// AFTER endContent() and has no state to read.
	ofRectangle binRectPx(int i) const;
	ofRectangle cutoutRectPx(int i) const;
	static ofRectangle cadBinRectPx(int i);
	// cornerRadiusPx rounds the ring's corners to match a rounded cutout
	// (0 = square four bars, which is what widgets use).
	static void drawRing(const ofRectangle & cut, float widthX, float widthY,
		const ofColor & colour, float cornerRadiusPx = 0.0f);
	// A FILLED annulus, and an arc of one. doc §13.4: circular rings are a
	// filled ofPath built from an outer arc and an inner arcNegative, never
	// two ofDrawCircle calls with the background colour punched through the
	// middle — over a fluid there is no background colour to punch with.
	static void drawAnnulus(float cx, float cy, float rOuter, float rInner,
		const ofColor & colour, float startDeg = 0.0f, float endDeg = 360.0f);
	// VISUAL_LAYER.md §6's idle state: the ~16 nested "strokes" around a
	// bin, breathing, phase-offset by _haloPhase[i]. A generalised
	// drawRing — same filled-band, ODD-winding technique (drawRing's own
	// comment on why an actual ofPath stroke is unusable here), but with a
	// nonzero INNER offset too, so many bands can nest around one rect
	// without each one redrawing the disc drawRing itself always starts
	// from.
	static void drawRoundedBand(const ofRectangle & base, float innerOffsetPx,
		float outerOffsetPx, const ofColor & colour, float baseCornerRadiusPx);
	// A filled rounded rect, and the soft outward glow around one.
	// `drawGlow` is drawHalo's own falloff — quadratic, brightest at the
	// edge — generalised off the bins, so the cart's box and buttons are
	// lit by the same primitive the table already breathes with rather
	// than by a second, similar-looking one.
	static void drawRoundedRectFill(const ofRectangle & r, float cornerRadiusPx,
		const ofColor & colour);
	static void drawGlow(const ofRectangle & r, float cornerRadiusPx,
		float reachPx, int bands, const ofColor & colour, int peakAlpha);
	// A horizontal rule whose ALPHA fades from the centre to nothing at
	// both ends, at constant thickness.
	//
	// The thickness is CONSTANT and only the alpha fades. A rule that
	// tapers in height reads as a broken line rather than a soft one.
	static void drawFadedRule(float x, float y, float widthPx,
		float thickPx, const ofColor & colour, int peakAlpha);
	// One chilli pepper, centred on (cx, cy), `sizePx` tall — the spice
	// card's count glyph. Draws the `_chilliIcon` artwork, which is why
	// this is a member rather than static.
	void drawChilli(float cx, float cy, float sizePx) const;
	// The idle-table wave prompt, inviting a passer-by to wave to start.
	// Draws `_idleHandIcon` (img/idle-hand.png) exactly as `drawChilli`
	// draws the pepper: the wave is a rotation applied at draw time about
	// the wrist, on the same loaded image every frame, never a second copy
	// or a re-rendered frame. See the definition and the idleAttract gate
	// in draw().
	void drawIdleHand() const;
	// The breathing term the buttons and the bin halos share — one sine,
	// one clock, one period, so the whole table breathes together rather
	// than in two rhythms. `phase` offsets it (the bins use a per-island
	// rotation); the buttons pass 0.
	static float breath(float floor01, float phase = 0.0f);
	void drawHalo(int i) const;
	void drawWidgets(const StateLink::State & state) const;
	// Every widget's halo and nothing else. Called from `draw()` ahead of
	// the page header, the info box, the cart AND the widget bodies, so a
	// halo can never land on top of any of them. See the definition.
	void drawWidgetGlows(const StateLink::State & state) const;
	void drawWidgetGlow(const StateLink::Widget & w) const;
	void drawWidget(const StateLink::Widget & w) const;
	// An `option` widget — a broth or a spice plate. Split out of
	// drawWidget because the two shapes share only their frame: a button
	// is a centred word, a plate is an icon column, a left-aligned name
	// and a tick, and folding both into one function meant three
	// `if(kind == "option")` branches interleaved through it.
	void drawOptionPlate(const StateLink::Widget & w, const ofColor & ink,
		float glow01) const;
	// One string in two inks, split at `splitX` — the dwell sweep's
	// leading edge. Everything left of the edge lands on the swept dark
	// band and is drawn in `lit`; everything right of it stays `dark` on
	// the plain card. See the definition for why it overdraws rather than
	// drawing two substrings.
	static void drawStringLitTo(const ofTrueTypeFont & f, const std::string & s,
		float x, float baseline, float splitX, const ofColor & dark,
		const ofColor & lit);
	// The same pair, centred on `cx` the way `drawCentered` centres one.
	static void drawCenteredLitTo(const ofTrueTypeFont & f, const std::string & s,
		float cx, float baseline, float splitX, const ofColor & dark,
		const ofColor & lit);
	// `drawCenteredLitTo`'s own sweep, split across TWO fonts for a label
	// mixing ASCII and CJK bytes (today: the Language button's literal
	// "EN | 中文" — see hasMixedScript). Splits the STRING once, at the
	// first non-ASCII byte, and draws the ASCII half in `asciiFont`, the
	// rest in `cjkFont`, both through `drawStringLitTo` against the same
	// `splitX` so the sweep still reads as one continuous edge crossing
	// the whole label rather than two buttons glued together.
	static void drawBilingualCenteredLitTo(const ofTrueTypeFont & asciiFont,
		const ofTrueTypeFont & cjkFont, const std::string & s,
		float cx, float baseline, float splitX, const ofColor & dark,
		const ofColor & lit);
	// How far the dwell sweep has crossed this widget, 0..1 — latched
	// against the one-frame gap between core clearing `dwell` and core
	// marking `selected`. See `_sweepHoldUntil`.
	float sweep01For(const StateLink::Widget & w) const;
	// The dark band itself, clipped to the widget's rounded corner, plus
	// the amber leading edge while it is still moving.
	static void drawSweep(const ofRectangle & box, float corner, float sweep01);
	void drawBin(int i, const StateLink::Bin & b, const BinTween & tw) const;
	// VISUAL_LAYER.md §8/§9: the running total, drawn as one receipt-style
	// line (label left, value right) inside the cart footer drawCart lays
	// out. baselineY is drawCart's to compute, since it is the only call
	// site.
	void drawTotal(const StateLink::Total & total, float baselineY) const;
	// VISUAL_LAYER.md §8: the cart panel — 8 fixed row slots bound to bins
	// in PICK ORDER (see update()'s binding logic and _cartSlotBin below),
	// the divider, and the total via drawTotal above.
	//
	// Confirm and Cancel are NOT drawn here. They are real widgets on the
	// wire (core/hover.py) drawn by drawWidget like any other, so the rect
	// a hand is hit-tested against is the same rect it sees.
	void drawCart(const StateLink::State & state) const;
	// VISUAL_LAYER.md §8: the info box above the cart —
	// veg/non-veg, kcal and one short description for the ACTIVE bin
	// (`hl == "hover"`), faded in and out. Draws nothing at all when idle
	// (not an empty bordered box), and cannot move the cart: the band it
	// sits in is reserved by kCartTopPx' own arithmetic whether anything
	// is active or not.
	//
	// `topPx`/`heightPx` are passed in rather than read from a constant
	// because the option screens put a page header above this band and
	// the cart screen does not — the box is the same box, one step
	// further down. See drawPageHeader.
	void drawInfoBox(const StateLink::State & state,
		float topPx, float heightPx) const;
	// The title telling the diner what this screen is for, plus the step
	// dots. Drawn on the option and payment screens, never on an idle
	// table. See StateLink::Screen.
	void drawPageHeader(const StateLink::Screen & screen) const;
	// doc §18.1's CHECKOUT screen: the projected QR, the total, and — only
	// once the payment has landed — the token. Replaces the cart for that
	// one screen; see the call site in draw().
	void drawCheckout(const StateLink::State & state) const;
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
		bool connected, float fps, bool audioMuted) const;
	std::string _priceText(double amount) const;

	// Set once per draw() from state.total.text (the one locale-resolved
	// string the wire gives oF) and read by both drawBin and drawTotal —
	// see UiLayer.cpp's splitCurrencyText for why. mutable because they are
	// draw-time formatting cache, not state, and draw() is const.
	mutable std::string _currencyPrefix;
	mutable int _currencyDecimals = 2;

	// 28px/22px. Not the bin plate's fonts — those are _plateNameFont and
	// _plateRateFont below. These serve the banner headline and subline
	// and the widget label.
	ofTrueTypeFont _nameFont;
	ofTrueTypeFont _detailFont;
	// VISUAL_LAYER.md §3: the bin plate's two lines. Separate font objects
	// from _nameFont/_detailFont above, so retyping the plate cannot resize
	// the banner or a widget label as a side effect.
	//
	// The name is 28px and core's `label` is wrapped to at most two lines
	// (drawBin's wrapNameToTwoLines) rather than pre-shortened; 40px
	// overflows a bin. The rate line is DejaVuSansMono rather than the bold
	// face — a regular weight per the doc, and monospace so a picked
	// price's width does not shift digit to digit — at a smaller size than
	// the doc's 26px (see kPlateRatePx), because DejaVuSansMono's
	// cap-height runs larger relative to its point size than the
	// proportional bold face's does and it otherwise reads bigger than the
	// name above it.
	ofTrueTypeFont _plateNameFont;  // 28px bold DejaVuSans, ink #2B2118
	ofTrueTypeFont _plateRateFont;  // 18px regular DejaVuSansMono, ink #6AA84F
	// VISUAL_LAYER.md §3: "Total value" 48px bold, "Total label" 30px.
	// Both draw as one receipt line inside the cart footer
	// (drawCart/drawTotal).
	ofTrueTypeFont _totalNumFont;   // 48px bold, "Total value"
	ofTrueTypeFont _totalLabelFont; // 30px, "Total label"
	// VISUAL_LAYER.md §3: "Cart row — filled name" / "— filled g + cost,"
	// both 26px, one face — only the ink colour differs between the two
	// (drawCart sets it per column), so one font object serves both.
	ofTrueTypeFont _cartRowFont;    // 22px bold, cart row name + detail
	// The RESERVED width of a cart row's right-hand column, measured once
	// in setup() from kCartDetailWorstCase. Not per-row: a column whose
	// width depends on the number in it moves the name column beside it
	// every time a weight gains a digit, truncating the name.
	float _cartDetailColPx = 0.0f;
	// VISUAL_LAYER.md §3's "Info box text" at 20px, plus a larger face for
	// the item name that leads the box. Separate objects from the cart
	// row's: the two are different sizes in the doc's palette, and resizing
	// the cart must not drag this box along with it.
	ofTrueTypeFont _infoNameFont;   // 32px, the item's name
	ofTrueTypeFont _infoFont;       // 20px, info box body
	// Its own face purely so it can be BIGGER than the body text: this is
	// the one number on the box a diner weighs a choice against, and at
	// body size next to a 30px name it is too thin to read.
	ofTrueTypeFont _infoKcalFont;   // info box, the right-hand meta figure
	ofTrueTypeFont _infoDietFont;   // info box, the VEG/NON-VEG label (bold)
	ofTrueTypeFont _cartDetailFont; // cart, the grams/price column (mono)
	// `_buttonFont` is sized to the control rather than to the page: three
	// buttons share the cart's width at core/hover.py's BUTTON_H_PX, and
	// "Cancel" at `_nameFont`'s 28px leaves no margin inside a 155px
	// button. Still bold, still read first, just smaller.
	ofTrueTypeFont _buttonFont;     // 22px bold, a button's label
	// The Language button's "中文" half. Loaded ONCE in setup() and never
	// touched by loadFonts()'s per-locale reload: unlike every other font
	// member, this one label needs the ASCII face and the CJK face on
	// screen AT THE SAME TIME whichever locale is active, since "EN | 中文"
	// reads the same in either. See drawWidget's hasMixedScript branch.
	ofTrueTypeFont _buttonFontCjk; // 22px, Noto Sans SC, always loaded
	ofTrueTypeFont _pageTitleFont;  // 26px bold, "Choose Your Broth"
	// 20px bold, a broth/spice plate's name. Its own face rather than the
	// title's, because the size is decided by the plate's label column
	// (kOptionLabelPx' own arithmetic) and retitling a page must not be
	// able to clip four menu names as a side effect.
	ofTrueTypeFont _optionFont;
	// The option card's note face — `kCardNotePx`, a step under the shared
	// info box's own text size. See `drawOptionPlate`.
	ofTrueTypeFont _cardNoteFont;
	// The token, and it is mono for the reason every fixed number on this
	// table is: a diner reads this one character by character to somebody
	// at a counter, and proportional digits at this size run together.
	ofTrueTypeFont _tokenFont;      // 88px mono bold, the paid token
	// Names already reported as too wide for the cart's name column, so
	// the warning is one line per name rather than one per frame at 60Hz.
	// Mutable because drawCart is const and this is diagnostics, not state.
	mutable std::set<std::string> _truncatedNames;
	// The time-driven dwell sweep (see `sweep01For`). One entry per
	// widget id: `value` is what actually draws, `fallFrom` is where a
	// fall started, and `t0` is when the value last ROSE — which is what
	// the fall delay is measured against. `t0 <= 0` marks a fresh entry,
	// so the first frame a widget is seen snaps to its wire value rather
	// than animating up from nothing. The map holds a handful of ids and
	// an entry is dropped as soon as its widget goes disabled.
	struct SweepAnim {
		float value = 0.0f;
		float fallFrom = 0.0f;
		float t0 = 0.0f;
	};
	mutable std::map<std::string, SweepAnim> _sweepAnim;
	ofTrueTypeFont _devFont;       // 16px, "Developer overlay"
	// "Wave to start" — the idle-hand prompt's own label, below the icon.
	// Its own face rather than reusing `_pageTitleFont`: that one is sized
	// for a header a diner is already standing at the table reading, and
	// this line has to read from across the room, the same distance the
	// hand icon itself is sized for. See drawIdleHand.
	ofTrueTypeFont _idleHandFont;  // 32px bold DejaVuSans
	bool _fontsLoaded = false;

	// Locale-switched fonts. Doc §17.1 asks for two ofTrueTypeFont
	// instances per size selected by state.locale; this is done instead as
	// ONE instance per size, RELOADED from the other language's file when
	// `state.locale` changes. That keeps every draw call above
	// (`_nameFont`, `_buttonFont`, ...) reading exactly as it does, and
	// only setup() and update() know a second locale exists. See
	// loadFonts() for what the reload costs.
	//
	// Empty until setup() runs, which is also the sentinel that makes the
	// FIRST update() after boot a no-op reload check rather than an
	// unconditional one — see the call site.
	std::string _loadedFontLocale;
	// Loads every font member above from either the English (DejaVu)
	// files or the single bundled Chinese one (Noto Sans SC), at each
	// role's EXISTING px size — see UiLayer.cpp for why CJK does not get
	// doc §17.1's "15% larger" bump. Returns whether every load succeeded,
	// the same contract setup() has for `_fontsLoaded`.
	bool loadFonts(const std::string & locale);

	// How tall the page header actually is, measured from the loaded title
	// face in setup() rather than fixed as a constant. The info box's band
	// on the option and payment screens is `kInfoBoxHeightPx` minus this,
	// so a header guessed too generously silently squeezes the note out of
	// the box — a fixed constant here once put 244.2px of content in a
	// 228.5px band. 0 until setup() runs; nothing draws before then.
	float _pageHeaderPx = 0.0f;

	ofImage _brandLogo;   // "The Firepot" mark — see drawBrandMark
	bool _brandLogoLoaded = false;
	// The spice card's pepper — img/chilli.png, pre-scaled once at load.
	// See drawChilli and the load in setup().
	ofImage _chilliIcon;
	bool _chilliIconLoaded = false;
	// The idle-table wave prompt — img/idle-hand.png, loaded and reused the
	// same way as the chilli. See drawIdleHand and the load in setup().
	ofImage _idleHandIcon;
	bool _idleHandIconLoaded = false;

	std::array<BinTween, 8> _bins;
	Spring _totalAmount{0.15f};
	// VISUAL_LAYER.md §6's per-bin phase offset, implemented as a fixed
	// rotation around each 2x2 island and set once in setup(), never per
	// frame — a phase that itself moved would defeat the point of a fixed
	// per-bin offset. See setup().
	std::array<float, 8> _haloPhase{};

	// The stage-space rects core last sent, and whether it sent any. An
	// absence is NOT "a rect at the origin" — see StateLink::Bin::hasRect.
	std::array<ofRectangle, 8> _coreRects;
	std::array<bool, 8> _hasCoreRect{};

	// VISUAL_LAYER.md §8: which bin (0-7, or -1 if the slot is still
	// blank) each of the cart's 8 fixed row SLOTS is bound to.
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

	// VISUAL_LAYER.md §8: what the info box is currently ABOUT — a hovered
	// bin, or a hovered broth/spice option. Held as resolved CONTENT rather
	// than a bin index, because the box takes either without caring which
	// it got and a widget has no index into `state.bins` to be named by.
	//
	// Deliberately LEFT SET when nothing is hovered any more, so the box
	// has something to draw while it fades out. Clearing it on
	// deactivation would blank the text a frame before the box itself
	// finished going, which reads as the box breaking rather than as it
	// closing.
	struct InfoContent {
		std::string name;
		std::string diet;   // veg|nonveg|egg, or "" — a spice level is not food
		std::string meta;
		std::string desc;
	};
	InfoContent _info;
	// 250ms: slower than the 150ms a fact-tracking spring uses
	// (BinTween::picked tracks a number that should feel instant), faster
	// than the halo/fire cross-dissolve's 350ms.
	Spring _infoFade{0.25f};
};
