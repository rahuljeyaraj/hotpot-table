#pragma once

#include "ofMain.h"

#include <array>
#include <functional>
#include <string>
#include <vector>

// doc §13.2's FBO stack; VISUAL_LAYER.md §5 gives the fuller five-layer
// picture. This class owns one content FBO; ofApp draws layers 1-2 and 4-5
// into it (via beginContent()/endContent()), and this class stamps layer 3
// itself, in compositeAndWarp(), AFTER endContent() has been called:
//
//   1. table background   — Stage::beginContent() (kTableBackground)
//   2. fluid               — ofApp::draw(), FluidLayer::draw(), first
//      ───────────────────────────────────────────────────────────────
//   4. halo                 } ofApp::draw() -> UiLayer::draw(), both
//   5. UI (plates/total/…)  } drawn into the SAME content pass as 1-2,
//                             immediately after them
//      ───────────────────────────────────────────────────────────────
//   3. LIGHT PASS  — flat pure-white over every tray cutout, stamped LAST
//      ───────────────────────────────────────────────────────────────
//      → keystone warp → screen
//
// Layer 3 is drawn structurally LAST of the whole frame, not third. This is
// deliberate, and it does not match VISUAL_LAYER.md §5's "bottom to top"
// numbering literally. Invariant I9 requires that NOTHING drawn afterward
// can put anything but flat white into a cutout, and the only way to
// guarantee that by construction — rather than by every future halo or UI
// change happening to avoid painting into a cutout — is for the light pass
// to be the final write of the frame. Halo and UI never draw INTO a cutout
// by design (halo wraps the bin only; plate text sits outside it), so
// nothing in practice sees a difference between "layer 3 third" and "layer
// 3 last", but only the second is safe against a future mistake.
//
// Implemented with a plain filled rectangle per cutout rather than a
// shader, matching the immediate-mode calls this app draws everything else
// with.
//
// There is deliberately no "floor lift" here — no per-frame blend of the
// composite toward white to brighten the projected field for the camera. It
// would brighten whatever colour was already set, so no colour on the table
// would stay the value it was assigned, and VISUAL_LAYER.md §3's palette
// gives exact hex values that nothing may move once drawn. If the table
// needs to be brighter, change the colour constant itself (for example
// kTableBackground in Stage.cpp), never blend on top of it.
class Stage {
public:
	// stageW/H default to doc §5.1's canonical stage space, 1920x1080 —
	// also PROJ_W_PX/PROJ_H_PX in TableGeometry.h. Two names for the same
	// number on purpose: "stage space" is the coordinate-system term,
	// PROJ_*_PX the physical-rig one.
	void setup(int stageW = 1920, int stageH = 1080,
		const std::string & keystonePath = "keystone.json");

	// Bracket UiLayer's drawing. Content lands in the un-keystoned FBO,
	// which IS stage space (doc §5.2).
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

	// Runs the light pass on the FBO, then warps it onto the real window
	// through the loaded keystone quad. cutoutsMM are the bin fill rects in
	// table mm (TableGeometry.h's binFillRectMM), because this class does
	// the one mm->px conversion the light pass needs and nothing calling it
	// should have to duplicate that math.
	// invertedField skips the light pass — see beginContent(). The keystone
	// warp still runs: doc §5.2 is explicit
	// that "H_cam->stage implicitly contains the keystone", because the
	// dots are drawn at known stage coordinates and keystoned onto the
	// table by the same warp that will later carry the UI. Solving
	// through an un-warped pattern would produce a homography that is
	// correct for a table nobody is projecting onto.
	// drawAboveLightPass, if given, is called on the content FBO AFTER the
	// light pass has stamped its cutout rectangles: a deliberate, narrow
	// carve-out from I9's "nothing drawn after the light pass can put
	// anything but flat white into a cutout" rule, for exactly one thing —
	// the hand cursor, and only while serving (ofApp decides that and passes
	// nullptr otherwise). It is safe because the two concerns are mutually
	// exclusive by MODE: the classifier, which is why I9 exists at all since
	// a cutout must be flat and unpatterned for the training photos it
	// takes, runs only during setting mode (doc §12.7's capture refusal),
	// and the cursor exists only during serving mode. Nothing else about I9
	// changes — the rest of every cutout is still stamped flat white,
	// always.
	// cutoutCornerRadiusPx rounds every cutout's corners (0 = square). One
	// radius for all bins — TableGeometry.h's CUTOUT_CORNER_RADIUS_MM,
	// converted to px by the caller.
	void compositeAndWarp(const std::vector<ofRectangle> & cutoutsPx,
		float cutoutCornerRadiusPx = 0.0f,
		bool invertedField = false,
		const std::function<void()> & drawAboveLightPass = nullptr);

	int stageWidth() const { return _w; }
	int stageHeight() const { return _h; }

	// A short hex digest of the loaded corners. Doc §8.5: core compares this
	// against what solved the current homography and flags "calibration
	// stale" if they disagree — see StateLink::sendStat.
	const std::string & keystoneFingerprint() const { return _fingerprint; }

private:
	void loadKeystone(const std::string & path);

	int _w = 1920;
	int _h = 1080;
	ofFbo _fbo;
	std::array<glm::vec2, 4> _corners;   // TL, TR, BR, BL, in window px
	std::string _fingerprint;
};
