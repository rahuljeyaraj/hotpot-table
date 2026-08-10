#pragma once

#include "ofMain.h"

#include <array>
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
	void beginContent();
	void endContent();

	// Runs the floor lift and light pass on the FBO, then warps it onto
	// the real window through the loaded keystone quad. cutoutsMM are the
	// bin fill rects in table mm (TableGeometry.h's binFillRectMM), because
	// this class does the one mm->px conversion the light pass needs and
	// nothing calling it should have to duplicate that math.
	void compositeAndWarp(float whiteFloor, const std::vector<ofRectangle> & cutoutsPx);

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
