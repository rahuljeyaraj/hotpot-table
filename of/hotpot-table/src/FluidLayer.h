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

	// 2026-08-14, rig report: with no obstacle, a near-row bin's fire had
	// nothing physical stopping it drifting up past the far row — the only
	// thing hiding it there was UiLayer's opaque bin plate drawing on TOP
	// of the fluid layer, which only masks the exact bin rect, not the
	// halo/ring margin around it, so a drifting flame still visibly wrapped
	// around a bin it never actually touched. This makes every bin a real
	// wall in the sim itself (ftFluidFlow::setObstacle) — the flow curls
	// around it and cannot advect through it — rather than trusting a
	// paint-over-it clip. `rect` is STAGE space, same as everything else
	// UiLayer hands this class; callers pass UiLayer::cutoutRectsPx() (the
	// exact rect the light pass already treats as the physical white
	// plate), not the wider halo rect — the obstacle should be exactly as
	// big as the real object.
	struct Obstacle {
		ofRectangle rect;
		float cornerRadiusPx = 0.0f;
	};

	// 2026-08-14: the active-bin highlight (build item 6's fire ring) is
	// GONE from this class — developer's own call, after the ring's fire
	// both false-positive-drifted into the far row AND, separately, was
	// judged not to read as "this bin is selected" even where it stayed
	// put. Its replacement (a spark shower) is pure UiLayer geometry with
	// its own ballistic physics, not a fluid emitter, so it needed no
	// FluidLayer API at all — see UiLayer.cpp's drawSparks. What is left
	// here is only what VISUAL_LAYER.md's fluid layer was for from the
	// start: hands are in STAGE space (CursorLink::Hand::x/y), same space
	// as everything else UiLayer draws in, and every hand injects an
	// ambient flame trail regardless of selection. obstacles defaults
	// empty too — kFluidDebugMouseOnly's isolated bench draws no bins at
	// all, so it has none to pass.
	void update(float dt, const std::vector<CursorLink::Hand> & hands,
		const std::vector<Obstacle> & obstacles = {});

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
	// stage px -> SIM px, not density px — ftFluidFlow's own obstacleFbo is
	// allocated at simulationWidth/simulationHeight (ftFluidFlow.cpp), half
	// of density resolution here, same as the velocity FBO.
	float _toSimX = 1.0f, _toSimY = 1.0f;

	// Rebuilt from `obstacles` every update() call, at sim resolution — see
	// Obstacle's own comment. White (opaque) where a bin sits, black
	// elsewhere; ftFluidFlow::setObstacle reads any nonzero channel as
	// "wall" after its own round().
	ofFbo _obstacleMask;

	// Per-hand-id last density-space position, rebuilt fresh every frame so
	// a hand that disappears and reappears (a new id, or the same id after
	// a gap) never computes a velocity spike from a stale remembered spot.
	std::unordered_map<int, glm::vec2> _lastDensityPos;
};
