#pragma once

#include "ofMain.h"

#include <array>
#include <functional>
#include <string>
#include <vector>

// v3 doc §13.2's FBO stack, minus the fluid pass — FluidLayer does not
// exist yet (M8's build item), so step 1 of the four below is skipped for
// now and the composite starts from a flat paper-white background instead.
// Steps 3 and 4 still run every frame, unconditionally, because I9 is a
// hard invariant (CLAUDE.md) and not something that starts applying only
// once a fluid exists to need protecting from:
//
//   1. (fluidFBO — deferred to M8)
//   2. uiFBO       — UiLayer draws here (labels, prices, plates, total)
//      ───────────────────────────────────────────────────────────────
//   3. FLOOR LIFT  — out = k + (1-k)*in, per pixel, on the composite so far
//   4. LIGHT PASS  — flat pure-white over every tray cutout, stamped LAST
//      ───────────────────────────────────────────────────────────────
//      → keystone warp → screen
//
// Steps 3 and 4 are implemented with plain alpha-blended rectangles, not a
// shader. That is not a shortcut: a standard SRC_ALPHA/ONE_MINUS_SRC_ALPHA
// blend of opaque white at alpha=k over the existing composite computes
// exactly white*k + in*(1-k) = k + (1-k)*in per channel — the doc's floor
// lift formula, exactly, because "in" and "out" there are the normalised
// 0..1 channel values a blend already operates on. Reaching for a GLSL
// pass would add a version dependency (this app draws everything else with
// oF's immediate-mode calls, no shader anywhere yet) to recompute something
// alpha blending already does.
//
// docs/VISUAL_LAYER.md §9 step 1 (2026-08-14) repainted step 2's background
// from flat white to #E8E6E1 — see Stage.cpp's kTableBackground — but left
// steps 3/4 untouched. The floor lift still mixes the whole composite toward
// literal white by kWhiteFloor (ofApp.cpp), which means the table's PROJECTED
// colour is lighter than #E8E6E1 by that fraction, not the hex value exactly.
// The bins are unaffected (the light pass stamps them opaque AFTER the lift).
// VISUAL_LAYER.md §5's own 5-layer order has no floor-lift step at all —
// reconciling that is explicitly step 5, "Layer reorder," not this one.
class Stage {
public:
	// stageW/H default to v3 §5.1's canonical stage space, 1920x1080 —
	// also PROJ_W_PX/PROJ_H_PX in TableGeometry.h. Two names for the same
	// number on purpose: "stage space" is the doc's coordinate-system term,
	// PROJ_*_PX is the older physical-rig term this file predates.
	void setup(int stageW = 1920, int stageH = 1080,
		const std::string & keystonePath = "keystone.json");

	// Bracket UiLayer's drawing. Content lands in the un-keystoned FBO,
	// which IS stage space (v3 §5.2 — "oF's un-keystoned framebuffer").
	//
	// invertedField is I9's ONE documented exception, dot calibration:
	// "black field, white dots... a white field there puts the dots on a
	// background as bright as they are and the solve finds nothing."
	// It clears to black instead of paper white, and the matching
	// compositeAndWarp() call must be given the same flag or the light
	// pass will stamp eight white rectangles into the camera's view of
	// the pattern — which is the failure this parameter exists to make
	// impossible to forget, since forgetting it produces a calibration
	// that fails for a reason nothing on screen explains.
	void beginContent(bool invertedField = false);
	void endContent();

	// Runs the floor lift and light pass on the FBO, then warps it onto
	// the real window through the loaded keystone quad. cutoutsMM are the
	// bin fill rects in table mm (TableGeometry.h's binFillRectMM), because
	// this class does the one mm->px conversion the light pass needs and
	// nothing calling it should have to duplicate that math.
	// invertedField skips BOTH the floor lift and the light pass — see
	// beginContent(). The keystone warp still runs: doc §5.2 is explicit
	// that "H_cam->stage implicitly contains the keystone", because the
	// dots are drawn at known stage coordinates and keystoned onto the
	// table by the same warp that will later carry the UI. Solving
	// through an un-warped pattern would produce a homography that is
	// correct for a table nobody is projecting onto.
	// drawAboveLightPass, if given, is called on the content FBO AFTER the
	// light pass has stamped its cutout rectangles — 2026-08-12, a
	// deliberate, narrow carve-out from I9's "nothing drawn after the
	// light pass can put anything but flat white into a cutout" rule,
	// for exactly one thing: the hand cursor, and only while serving
	// (ofApp is what decides that and passes nullptr otherwise). This is
	// safe because the classifier (the reason I9 exists at all — a
	// cutout must be flat and unpatterned for the training photos it
	// takes) and the cursor (only exists while hand-tracking is live)
	// are mutually exclusive by MODE: the classifier only ever runs
	// during setting mode (doc §12.7's capture refusal), the cursor only
	// ever exists during serving mode. Nothing else about I9 changes —
	// the rest of every cutout is still stamped flat white, always.
	// cutoutCornerRadiusPx rounds every cutout's corners (0 = square, the old
	// behaviour). One radius for all bins — TableGeometry.h's
	// CUTOUT_CORNER_RADIUS_MM converted to px by the caller.
	void compositeAndWarp(float whiteFloor, const std::vector<ofRectangle> & cutoutsPx,
		float cutoutCornerRadiusPx = 0.0f,
		bool invertedField = false,
		const std::function<void()> & drawAboveLightPass = nullptr);

	int stageWidth() const { return _w; }
	int stageHeight() const { return _h; }

	// A short hex digest of the loaded corners. Doc §8.5: core will one day
	// compare this against what solved the current homography and flag
	// "calibration stale" if they disagree — see StateLink::sendStat.
	const std::string & keystoneFingerprint() const { return _fingerprint; }

private:
	void loadKeystone(const std::string & path);

	int _w = 1920;
	int _h = 1080;
	ofFbo _fbo;
	std::array<glm::vec2, 4> _corners;   // TL, TR, BR, BL, in window px
	std::string _fingerprint;
};
