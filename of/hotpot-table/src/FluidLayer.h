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
// First pass only: one fixed look, no style presets (§14.3), no event
// injections (§14.4), no adaptive quality (§14.6), no setting-mode-off
// (§14.5). Those are separate follow-ups, not part of getting hand-driven
// fluid on the table.
class FluidLayer {
public:
	// simScale divides stageW/stageH down to the simulation grid, same
	// vocabulary as doc §14.6 (sim_scale in {8,6,4,3,2}).
	void setup(int stageW, int stageH, int simScale);

	// hands are in STAGE space (CursorLink::Hand::x/y), same space as
	// everything else UiLayer draws in.
	void update(float dt, const std::vector<CursorLink::Hand> & hands);

	// Draws the density field stretched to (w,h) — "upscaled to stage
	// size" per doc §13.2's FBO stack, step 1.
	void draw(int x, int y, int w, int h);

private:
	flowTools::ftFluidFlow _fluid;
	ofFbo _densityInject;
	ofFbo _velocityInject;

	int _simW = 0, _simH = 0;
	float _toSimX = 1.0f, _toSimY = 1.0f;   // stage px -> sim px

	// Per-hand-id last sim-space position, rebuilt fresh every frame so a
	// hand that disappears and reappears (a new id, or the same id after a
	// gap) never computes a velocity spike from a stale remembered spot.
	std::unordered_map<int, glm::vec2> _lastSimPos;
};
