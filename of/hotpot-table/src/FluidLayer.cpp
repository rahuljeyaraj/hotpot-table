#include "FluidLayer.h"

#include <algorithm>

using namespace flowTools;

namespace {
	// Injection constants, taken from the fireTest example this layer is
	// modelled on. See FluidLayer.h for why they are used literally rather
	// than scaled to the stage.
	const float kInjectRadiusDensityPx = 30.0f;
	const float kInjectRadiusVelocityPx = 20.0f;
	const float kVelocityScale = 4.0f;

	// Kill switches, same pattern as ofApp.cpp's kFluidEnabled/kDrawSkeleton.
	//
	// Density and velocity are split because their blast radius differs.
	// Density only ever paints a blob at the hand's own position. Velocity
	// is shared: ftFluidFlow keeps ONE velocity field for the whole canvas,
	// so a swipe anywhere — including the empty centre gap, nowhere near a
	// bin — injects a gust the sim propagates everywhere, fire rings
	// included. Velocity is off for that reason; turning it on restores the
	// flowing trail behind a moving hand at the cost of that coupling.
	const bool kHandFlameDensityEnabled = true;
	const bool kHandFlameVelocityEnabled = false;

	// One flame colour per bin index (FluidLayer::FireRing::colourIndex),
	// four hues paired across the two islands: 0-6, 4-2, 1-7, 5-3. The
	// pairing is a point reflection rather than a mirror — bin i's partner
	// is diagonally opposite it (col 0<->2 / 1<->3 and row 0<->1) — which is
	// what puts all four colours on both islands instead of two per island.
	// Colours are also arranged so no two adjacent bins share a hue; only a
	// bin's diagonal is far enough away to count as separated, since any
	// bin sharing an edge stays adjacent however the pairs are rotated.
	//
	// Hues are constrained by the MULTIPLY blend these rings draw under.
	// Near-white hues are avoided: multiply cannot darken the #E8E6E1
	// background with a channel near 255, so a pale hue washes out. A
	// channel truly at 0 can never accumulate however long a hover runs, so
	// it anchors the ring against the white-out described at
	// kFireRingMaxAlpha — blue (0,34,255) and orange (255,85,0) are anchored
	// this way; yellow and violet are not.
	//
	// Luminance is deliberately NOT matched across the four. The
	// "distinguish by hue, never brightness" rule is about signalling STATE,
	// and all four colours mean the same state (one bin, on fire), so hue is
	// identity here rather than status.
	//
	// None of the four has been checked against the projector. This rig has
	// turned an authored amber into red and a gold into muddy brown, so
	// treat every hex here as unverified until somebody looks at the table.
	const ofColor kFireRingColours[8] = {
		ofColor(0, 34, 255),      // 0: cobalt blue          — pairs with 6
		ofColor(255, 85, 0),      // 1: bright flame orange  — pairs with 7
		ofColor(255, 235, 59),    // 2: bright canary yellow — pairs with 4
		ofColor(156, 39, 176),    // 3: velvet violet        — pairs with 5
		ofColor(255, 235, 59),    // 4: bright canary yellow — pairs with 2
		ofColor(156, 39, 176),    // 5: velvet violet        — pairs with 3
		ofColor(0, 34, 255),      // 6: cobalt blue          — pairs with 0
		ofColor(255, 85, 0),      // 7: bright flame orange  — pairs with 1
	};

	// Diagnostic: forces every ring onto one colour, which takes per-bin hue
	// out of the picture when a difference between bins needs explaining.
	// kFireRingColours is left intact so this is a one-line flip either way.
	const bool kFireRingSingleColourDiagnostic = false;
	const ofColor kFireRingSingleColour(30, 110, 220);   // the pre-palette blue

	// Caps the injected ring alpha below 255 to stop a long hover washing
	// the ring's core out to the background colour.
	//
	// The mechanism is in ftFluidFlow's add/dissipate shaders: `addDensity`
	// ADDS the injected texture into a PERSISTENT buffer every frame, and at
	// density dissipation 1.0 (retained fraction `1 - dt*1.0`, ~0.967/frame
	// at 30fps) a steady hover's geometric-series steady state is roughly
	// the per-frame injected value divided by (1 - retained) — around 30x
	// the one-frame value. A channel starting near 255 saturates well before
	// that and stays saturated; once every channel is pinned at 255,
	// MULTIPLY blend is the identity (colour*255/255 = colour), so the
	// densest part of the ring shows the bare background and reads as white
	// while only the thinner edge still shows the injected hue.
	//
	// Capping is preferred to raising dissipation further, which is already
	// at its maximum sane value and risks the ring diffusing to invisible.
	// 190 is a starting value, not a measured one: how white is too white
	// can only be judged on the projected table.
	const float kFireRingMaxAlpha = 190.0f;

	// The fire ring's buoyancy, injected into its own buffer rather than
	// sharing the density colour.
	//
	// `addTemperature` writes into ftFluidFlow's `temperatureFbo`, which is
	// allocated GL_R32F — a single RED channel, so green and blue are
	// discarded on write. ftBuoyancyShader then reads
	// `texture(tex_temperature, st).x` and applies an upward force linearly
	// proportional to it. Feeding the same coloured texture to both
	// addDensity and addTemperature therefore makes lift a function of each
	// bin's red channel, so a palette edit silently changes how hard a
	// flame rises. A separate hue-independent buffer removes that coupling.
	//
	// The usable range is bounded at both ends. Too low puddles: lift is
	// what carries density away, so a ring the sim never lifts piles up in
	// the persistent buffer until it saturates — the exact white-out
	// kFireRingMaxAlpha exists to fight, which is why the value must not go
	// back down to 30. Too high billows more than intended. 120 is a
	// mid-range value and has not been looked at on the projected table.
	const int kFireRingHeat = 120;

	// The hand blob's heat, matching the 199 its density colour (199,74,52)
	// already carried. Split out as its own name only so the hand and the
	// rings can be tuned apart later.
	const int kHandFlameHeat = 199;

	// The fire ring's injection shape: a filled, ODD-winding rounded-rect
	// band, the same technique as UiLayer's own drawRoundedBand. An unfilled
	// ofPath's stroke is glLineWidth in disguise, which this rig's driver
	// caps and the programmable renderer ignores outright, so bands are
	// always drawn as a filled shape.
	//
	// Duplicated rather than shared: FluidLayer must not depend on UiLayer
	// (layer 2 is core-agnostic, layers 4/5 are UI), and one shape does not
	// justify a coupling neither file needs otherwise.
	void drawRoundedBand(const ofRectangle & base, float innerOffsetPx,
		float outerOffsetPx, float cornerRadiusPx, const ofColor & colour){
		const ofRectangle outer(base.x - outerOffsetPx, base.y - outerOffsetPx,
			base.width + 2.0f * outerOffsetPx, base.height + 2.0f * outerOffsetPx);
		const ofRectangle inner(base.x - innerOffsetPx, base.y - innerOffsetPx,
			base.width + 2.0f * innerOffsetPx, base.height + 2.0f * innerOffsetPx);
		const float rOuter = std::min(cornerRadiusPx + outerOffsetPx,
			std::min(outer.width, outer.height) * 0.5f);
		const float rInner = std::min(cornerRadiusPx + innerOffsetPx,
			std::min(inner.width, inner.height) * 0.5f);
		ofPath path;
		path.setFilled(true);
		path.setFillColor(colour);
		path.setCircleResolution(24);
		path.setPolyWindingMode(OF_POLY_WINDING_ODD);
		path.rectRounded(outer, rOuter);
		path.rectRounded(inner, rInner);
		path.draw();
	}
}

void FluidLayer::setup(int stageW, int stageH, int simScale){
	(void)simScale;   // no longer used — see FluidLayer.h's class comment

	_toDensityX = (float)_densityW / (float)stageW;
	_toDensityY = (float)_densityH / (float)stageH;

	// Dual-resolution setup: simulation and density have separate sizes.
	_fluid.setup(_simW, _simH, _densityW, _densityH);

	// Simulation parameters.
	//
	// `dissipation` is a decay RATE, not a 0..1 amount-remaining knob:
	// ftFluidFlow computes the retained-per-frame fraction as
	// `1.0 - deltaTime * dissipation`, so the fraction retained SHRINKS as
	// the parameter grows. At ~30fps a dissipation of 0.1 is an ~7s
	// half-life while 1.0 decays in ~1s.
	//
	// `temperature` is raised to match `density` so no field outlives the
	// density it is meant to be pushing — otherwise the visible puff fades
	// in a second while the invisible temperature and velocity fields keep
	// pushing for several more, carrying each newly injected frame further
	// than the last and drifting a bin's fire up out of its own ring.
	// `velocity` stops at 0.6 rather than 1.0 so the flame keeps some
	// persistence and flicker rather than reading as inert.
	_fluid.getParameters().getFloat("speed") = 0.3f;
	_fluid.getParameters().getGroup("dissipation").getFloat("velocity") = 0.6f;
	_fluid.getParameters().getGroup("dissipation").getFloat("density") = 1.0f;
	_fluid.getParameters().getGroup("dissipation").getFloat("temperature") = 1.0f;
	_fluid.getParameters().getGroup("dissipation").getFloat("pressure") = 0.1f;
	_fluid.getParameters().getGroup("viscosity").getFloat("velocity") = 1.0f;
	_fluid.getParameters().getGroup("viscosity").getFloat("density") = 1.0f;
	_fluid.getParameters().getGroup("viscosity").getFloat("temperature") = 1.0f;
	_fluid.getParameters().getFloat("vorticity") = 1.0f;
	_fluid.getParameters().getGroup("smoke buoyancy").getFloat("buoyancy") = 0.6f;
	_fluid.getParameters().getGroup("smoke buoyancy").getFloat("weight") = 0.2f;
	_fluid.getParameters().getGroup("smoke buoyancy").getFloat("ambient temperature") = 0.2f;

	// Injection buffers. Density is at density resolution; velocity is at
	// simulation resolution, which is exactly half of it in both dimensions.
	_densityInject.allocate(_densityW, _densityH, GL_RGBA);
	ftUtil::zero(_densityInject);
	_velocityInject.allocate(_simW, _simH, GL_RG32F);
	ftUtil::zero(_velocityInject);

	// The heat buffer (see kFireRingHeat), allocated at density resolution
	// rather than sim resolution so both injections are drawn in the same
	// coordinates. ftFlow::add() rescales onto the sim-resolution
	// temperatureFbo either way, so matching density costs nothing and
	// removes a second coordinate space from this file.
	_temperatureInject.allocate(_densityW, _densityH, GL_RGBA);
	ftUtil::zero(_temperatureInject);
}

void FluidLayer::update(float dt, const std::vector<CursorLink::Hand> & hands,
	const std::vector<FireRing> & fireRings){
	// One tracked position per hand id, in DENSITY space (stage px scaled by
	// _toDensityX/Y).
	//
	// `dt` is the caller's responsibility and it matters more than it looks:
	// it must be a SMOOTHED frame time (1.0/max(ofGetFrameRate(),1.f)), not
	// a raw per-frame delta. An occasional large raw frame time inflates
	// ftFluidFlow's internal timeStep (dt*speed*100), which multiplies
	// directly into the diffusion shader's strength (viscosityDen*timeStep,
	// run 20 times per frame) — one bad frame is enough to blur the
	// just-injected density down to nothing, leaving the density buffer
	// empty. This function correctly uses whatever dt it is given; the
	// choice is made in ofApp.cpp.
	struct HandPos {
		int id;
		glm::vec2 pos;
	};
	std::vector<HandPos> positions;
	positions.reserve(hands.size());
	std::unordered_map<int, glm::vec2> currentDensityPos;
	currentDensityPos.reserve(hands.size());
	for(const auto & h : hands){
		const glm::vec2 pos(h.x * _toDensityX, h.y * _toDensityY);
		positions.push_back({h.id, pos});
		currentDensityPos[h.id] = pos;
	}
	// Two buffers, ONE geometry: the density buffer carries the per-bin hue
	// a diner sees, the heat buffer carries how hard that ring rises in a
	// red the palette cannot reach into (see kFireRingHeat). Written as one
	// lambda rather than two copies of the loop specifically so the shapes
	// cannot drift apart — a ring that glowed in one place and lifted in
	// another would be a nastier bug than the one this avoids.
	//
	// `intensity` is UiLayer's crossfade spring, the same one the halo fades
	// out by. It scales alpha only, never the geometry, so the ring fills in
	// smoothly rather than popping to full strength the instant the bin
	// becomes hovered, and it scales heat through that same alpha so a ring
	// gains its lift on exactly the same curve as its colour.
	//
	// `colourIndex` is wrapped with `% 8` here rather than trusted, since
	// this class has no way to know the caller's bin count.
	auto injectShapes = [&](bool heat){
		ofEnableBlendMode(OF_BLENDMODE_ALPHA);
		if(heat){
			ofSetColor(kHandFlameHeat, 0, 0, 255);
		}
		else {
			ofSetColor(199, 74, 52, 255);
		}
		if(kHandFlameDensityEnabled){
			for(const auto & p : positions){
				ofDrawCircle(p.pos.x, p.pos.y, kInjectRadiusDensityPx);
			}
		}
		for(const auto & ring : fireRings){
			const float scale = 0.5f * (_toDensityX + _toDensityY);
			const ofRectangle b(ring.bin.x * _toDensityX, ring.bin.y * _toDensityY,
				ring.bin.width * _toDensityX, ring.bin.height * _toDensityY);
			const unsigned char alpha =
				(unsigned char)(kFireRingMaxAlpha * ofClamp(ring.intensity, 0.0f, 1.0f));
			const int idx = ((ring.colourIndex % 8) + 8) % 8;
			const ofColor densityColour = kFireRingSingleColourDiagnostic
				? kFireRingSingleColour : kFireRingColours[idx];
			const ofColor colour = heat ? ofColor(kFireRingHeat, 0, 0, alpha)
			                            : ofColor(densityColour, alpha);
			drawRoundedBand(b, ring.innerOffsetPx * scale, ring.outerOffsetPx * scale,
				ring.cornerRadiusPx * scale, colour);
		}
	};

	_densityInject.begin();
	ofClear(0, 0, 0, 0);
	injectShapes(false);
	_densityInject.end();

	_temperatureInject.begin();
	ofClear(0, 0, 0, 0);
	injectShapes(true);
	_temperatureInject.end();

	// The push, from hand movement. Positions are halved because density
	// space maps down into the sim-resolution velocity FBO, which is exactly
	// half of density resolution.
	_velocityInject.begin();
	ofClear(0, 0, 0, 0);
	ofEnableBlendMode(OF_BLENDMODE_DISABLED);
	if(kHandFlameVelocityEnabled){
		for(const auto & p : positions){
			glm::vec2 last = p.pos;
			auto it = _lastDensityPos.find(p.id);
			if(it != _lastDensityPos.end()){
				last = it->second;
			}
			const glm::vec2 delta = p.pos - last;
			ofFloatColor velColor(-delta.x * kVelocityScale, -delta.y * kVelocityScale, 0.0f, 1.0f);
			ofSetColor(velColor);
			ofDrawCircle(p.pos.x * 0.5f, p.pos.y * 0.5f, kInjectRadiusVelocityPx);
		}
	}
	_velocityInject.end();
	ofEnableBlendMode(OF_BLENDMODE_ALPHA);

	// addTemperature reads ONLY the red channel (ftFluidFlow's
	// temperatureFbo is GL_R32F), which is why heat gets its own buffer
	// rather than sharing the coloured one. See kFireRingHeat.
	_fluid.addDensity(_densityInject.getTexture());
	_fluid.addTemperature(_temperatureInject.getTexture());
	_fluid.addVelocity(_velocityInject.getTexture());
	_fluid.update(dt);

	_lastDensityPos = std::move(currentDensityPos);
}

void FluidLayer::draw(int x, int y, int w, int h){
	// Ordinary alpha blending, whatever update() last left active. The
	// premultiplied blend func this once used went with the rate-limited
	// injection path that no longer exists.
	_fluid.draw(x, y, w, h);
}
