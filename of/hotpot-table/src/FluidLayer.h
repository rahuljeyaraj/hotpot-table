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
// Injection style and every ftFluidFlow parameter are ported verbatim from
// apps/myApps/fireTest/src/ofApp.cpp — that app's mouse-driven fluid, tuned
// live against a white background (fireTest's own draw() calls
// ofBackground(255), same paper tone this app uses), reused as-is rather
// than re-derived. The one thing fireTest could not have is multiple named
// inputs: it tracks one glm::vec2 (the mouse) frame to frame, where this
// tracks one glm::vec2 PER HAND ID (_lastSimPos), because CursorLink can
// report several hands at once and any of them can appear/disappear on a
// given frame — a hand reappearing under the same or a new id must not
// compute a velocity spike from a stale remembered position.
//
// Replaces the old dot+ring cursor as the on-table sign of a hand's
// position (ofApp.cpp no longer passes a pointer to UiLayer while this is
// enabled) — this IS the hand pointer now, not a decoration next to it.
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
