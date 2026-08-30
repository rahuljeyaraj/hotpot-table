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
// This is a faithful copy of the fireTest example's fluid — its parameters,
// its resolution (1280x720 density / 640x360 sim) and its injection (flat
// alpha=255 every frame, ordinary blend, no rate limiting) alike. The
// resolution in particular is NOT free to change: diffusion
// (viscosity.density/temperature) dilutes to fully invisible — blank, not
// merely faint — on a smaller grid at any value above 0, so a scaled-down
// version of this reads as a sharp comet rather than a flame.
//
// The one deliberate departure is multiple named inputs. fireTest tracks a
// single glm::vec2 (the mouse) frame to frame; this tracks one per hand id
// (_lastDensityPos), because CursorLink can report several hands at once
// and any of them can appear or disappear on a given frame. A hand
// reappearing under the same or a new id must not compute a velocity spike
// from a stale remembered position.
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
		// An ordinal slot into this file's fixed palette (FluidLayer.cpp's
		// kFireRingColours), NOT a bin id: this class knows nothing about
		// bins or `hl` (I2/I3, see this struct's comment above), only which
		// of its colours to use for this ring. Callers happen to pass the
		// bin index, but that is their choice of numbering rather than
		// something this class interprets.
		int colourIndex = 0;
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
	// Same shapes as _densityInject, but a hue-independent red. The fluid's
	// temperature (and so its buoyancy) is a RED-CHANNEL-ONLY field, so
	// sharing the coloured buffer would make each bin's lift depend on its
	// own hue. See FluidLayer.cpp's kFireRingHeat.
	ofFbo _temperatureInject;
	ofFbo _velocityInject;

	// fireTest's resolution numbers, hardcoded — see this class's comment on
	// why they must not be scaled down. simScale is not used to derive
	// them; the setup() parameter is kept for interface compatibility only.
	int _densityW = 1280, _densityH = 720;
	int _simW = 640, _simH = 360;
	float _toDensityX = 1.0f, _toDensityY = 1.0f;   // stage px -> density px

	// Per-hand-id last density-space position, rebuilt fresh every frame so
	// a hand that disappears and reappears (a new id, or the same id after
	// a gap) never computes a velocity spike from a stale remembered spot.
	std::unordered_map<int, glm::vec2> _lastDensityPos;
};
