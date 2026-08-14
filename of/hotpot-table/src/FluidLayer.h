#pragma once

#include "ofMain.h"
#include "ofxFlowTools.h"
#include "CursorLink.h"

#include <unordered_map>
#include <vector>

// v3 doc §14.1/§14.4: the fluid reacts to hand position, not the mouse.
// Every hand injects — pointer and ambient alike (doc §14.4: "ambient hands
// inject; they still select nothing" — that restriction is a UI concern,
// which hand gets a drawn cursor, not a fluid one).
//
// Density alone gives a static blob; velocity (the per-frame position
// delta) is what makes it flow. Both are required every frame (doc §14.1).
//
// 2026-08-14: rewritten to be an EXACT copy of
// apps/myApps/fireTest/src/ofApp.cpp's fluid — not just its parameters, its
// resolution (1280x720 density / 640x360 sim) and its injection (flat
// alpha=255 every frame, ordinary blend, no rate limiting) too. Earlier
// versions of this file ported only the parameters and adapted the rest
// (this app's own smaller 480x270 single-resolution grid, a rate-limited/
// premultiplied injection scheme to dodge a saturation bug) — that
// adaptation was diagnosed piece by piece on the rig the same day and each
// piece looked closer to fireTest but never actually WAS fireTest: real
// bugs got fixed (dissipation.temperature, the rate-limiting masking
// motion) but the result still read as a sharp comet, not a flame, because
// diffusion (viscosity.density/temperature) dilutes to fully invisible on
// the smaller grid at any value above 0 — confirmed at both 1.0 and 0.15,
// not just faint, blank. The direct fix is to stop adapting and use
// fireTest's own resolution too, where 1.0 is already proven to work
// (fireTest's own screenshot, and an exact byte-for-byte copy embedded in
// this same window — both curled and rose correctly). The one thing
// fireTest could not have is multiple named inputs: it tracks one
// glm::vec2 (the mouse) frame to frame, where this tracks one glm::vec2
// PER HAND ID (_lastDensityPos), because CursorLink can report several
// hands at once and any of them can appear/disappear on a given frame — a
// hand reappearing under the same or a new id must not compute a velocity
// spike from a stale remembered position.
//
// Replaces the old dot+ring cursor as the on-table sign of a hand's
// position (ofApp.cpp no longer passes a pointer to UiLayer while this is
// enabled) — this IS the hand pointer now, not a decoration next to it.
class FluidLayer {
public:
	// simScale divides stageW/stageH down to the simulation grid, same
	// vocabulary as doc §14.6 (sim_scale in {8,6,4,3,2}).
	void setup(int stageW, int stageH, int simScale);

	// VISUAL_LAYER.md §9 build item 6: the active bin's fire ring, STAGE
	// space (same space `hands` arrives in) — one entry per
	// UiLayer::FireEmitter. A separate, duplicate type rather than a shared
	// header: FluidLayer must stay UI-agnostic (I2/I3, the same reason it
	// already knows nothing about bins or `hl`), so this only ever says
	// "inject here, this hard," never anything about why.
	struct FireRing {
		ofRectangle bin;
		float cornerRadiusPx = 0.0f;
		float innerOffsetPx = 0.0f;
		float outerOffsetPx = 0.0f;
		float intensity = 0.0f;   // 0..1 crossfade — scales injected alpha only
	};

	// hands are in STAGE space (CursorLink::Hand::x/y), same space as
	// everything else UiLayer draws in. fireRings defaults empty — build
	// item 7 ("emitter handoff") is what makes hands/rings mutually
	// exclusive; until then both can inject in the same frame, since the
	// hand is usually still sitting over the bin it just made active.
	void update(float dt, const std::vector<CursorLink::Hand> & hands,
		const std::vector<FireRing> & fireRings = {});

	// Draws the density field stretched to (w,h) — "upscaled to stage
	// size" per doc §13.2's FBO stack, step 1.
	void draw(int x, int y, int w, int h);

private:
	flowTools::ftFluidFlow _fluid;
	ofFbo _densityInject;
	ofFbo _velocityInject;

	// fireTest/src/ofApp.cpp's own resolution numbers, hardcoded — see
	// FluidLayer.cpp's 2026-08-14 rewrite comment. simScale is no longer
	// used to derive these; the setup() parameter is kept for interface
	// compatibility only.
	int _densityW = 1280, _densityH = 720;
	int _simW = 640, _simH = 360;
	float _toDensityX = 1.0f, _toDensityY = 1.0f;   // stage px -> density px

	// Per-hand-id last density-space position, rebuilt fresh every frame so
	// a hand that disappears and reappears (a new id, or the same id after
	// a gap) never computes a velocity spike from a stale remembered spot.
	std::unordered_map<int, glm::vec2> _lastDensityPos;
};
