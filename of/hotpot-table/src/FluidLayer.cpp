#include "FluidLayer.h"

#include <algorithm>

using namespace flowTools;

namespace {
	// fireTest/src/ofApp.cpp's own literal constants — see FluidLayer.h's
	// 2026-08-14 class comment for why this file stopped adapting them.
	const float kInjectRadiusDensityPx = 30.0f;
	const float kInjectRadiusVelocityPx = 20.0f;
	const float kVelocityScale = 4.0f;

	// VISUAL_LAYER.md §9 build item 6: the fire ring's own injection shape —
	// same filled, ODD-winding rounded-rect-band technique UiLayer's own
	// drawRoundedBand uses (that file's comment: an unfilled ofPath's own
	// "stroke" is glLineWidth in disguise, capped on this rig's driver and
	// ignored outright on the programmable renderer this app already runs
	// on). Duplicated rather than shared — FluidLayer must not depend on
	// UiLayer (I2/I3: layer 2 is core-agnostic, layer 4/5 is UI, and nothing
	// about this one shape justifies a coupling neither file needs
	// otherwise).
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

	// fireTest/src/ofApp.cpp::setup(): fluidFlow.setup(simulationWidth,
	// simulationHeight, densityWidth, densityHeight) — dual-resolution,
	// byte-for-byte the same call shape.
	_fluid.setup(_simW, _simH, _densityW, _densityH);

	// fireTest/src/ofApp.cpp::setup(), byte-for-byte — all eleven values.
	// **2026-08-14, build item 6 rig report: a near-row bin's fire, left
	// hovering, drifted up past its own ring into the far row.** Traced to
	// ftFluidFlow.cpp's own dissipation formula — VERIFIED in the
	// installed addon, not assumed — `1.0 - deltaTime * dissipation`, so
	// the retained-per-frame FRACTION shrinks as the *parameter* grows;
	// "dissipation" is a decay RATE, not a 0..1 amount-remaining knob. At
	// this app's ~30fps, `velocity`/`temperature` at fireTest's own 0.1
	// have an ~7s half-life — `density`'s own 1.0 already decays in ~1s,
	// so the visible puff fades quickly, but the invisible temperature/
	// velocity fields it left behind keep pushing for another six seconds,
	// carrying every newly-injected frame's density further than the one
	// before it the longer a hand keeps hovering. fireTest never showed
	// this because its one blob already filled most of the screen — there
	// was nowhere further for a long hover to carry it into. `temperature`
	// raised to match `density`'s own decay (no field should outlive the
	// density it is supposed to be pushing); `velocity` raised to 0.6, not
	// all the way to 1.0, so the flame keeps some persistence/flicker
	// rather than reading as inert. Unmeasured against the actual FIRE_RING
	// geometry — tunable further once seen projected, same as every other
	// build-item-6 constant.
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

	// fireTest's mouseDensityFbo/mouseVelocityFbo, same resolutions
	// (density at density res, velocity at sim res — fireTest injects
	// velocity at mousePos*0.5 into a sim-resolution FBO because its sim is
	// exactly half its density resolution).
	_densityInject.allocate(_densityW, _densityH, GL_RGBA);
	ftUtil::zero(_densityInject);
	_velocityInject.allocate(_simW, _simH, GL_RG32F);
	ftUtil::zero(_velocityInject);
}

void FluidLayer::update(float dt, const std::vector<CursorLink::Hand> & hands,
	const std::vector<FireRing> & fireRings){
	// fireTest/src/ofApp.cpp::update() tracks one glm::vec2 (the mouse)
	// frame to frame; this tracks one per hand id — see FluidLayer.h's
	// class comment for why. Each hand's position here is fireTest's own
	// `mousePos`, in DENSITY space (stage px * _toDensityX/Y).
	//
	// dt is the caller's responsibility to get right, and it matters more
	// than it looks: fireTest computes its own dt as
	// 1.0/max(ofGetFrameRate(),1.f) (a smoothed value), not a raw per-frame
	// delta. ofApp.cpp's debug branch originally passed ofGetLastFrameTime()
	// instead — 2026-08-14 rig test found this was the actual reason a
	// byte-for-byte copy of fireTest's own code still came up with a fully
	// empty density buffer: an occasional large raw frame time inflates
	// ftFluidFlow's internal timeStep (dt*speed*100), which multiplies
	// directly into the diffusion shader's strength (viscosityDen*timeStep,
	// run 20 times/frame) — one bad frame is enough to blur the just-
	// injected density down to nothing. The smoothed fireTest formula does
	// not have single-frame spikes. Fixed in ofApp.cpp, not here, since
	// this function correctly does whatever dt it's given — noted here so
	// the next person touching either file sees why it matters.
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
	// fireTest/src/ofApp.cpp::update() — the visible "fire" blob at the
	// hand: flat alpha=255, ORDINARY blending, every frame, no rate
	// limiting. Byte-for-byte, generalized from one mouse to N hands.
	_densityInject.begin();
	ofClear(0, 0, 0, 0);
	ofEnableBlendMode(OF_BLENDMODE_ALPHA);
	ofSetColor(199, 74, 52, 255);
	for(const auto & p : positions){
		ofDrawCircle(p.pos.x, p.pos.y, kInjectRadiusDensityPx);
	}
	// VISUAL_LAYER.md §9 build item 6: the active bin's own emitter, drawn
	// into the SAME density buffer as the hand's (and, via addTemperature
	// below, the same buoyancy source too). `intensity` is UiLayer's own
	// crossfade spring — the same one the halo fades out by — scaling
	// alpha only, never the geometry, so the ring fills in smoothly rather
	// than popping to full strength the instant `hl` flips to "hover".
	for(const auto & ring : fireRings){
		const float scale = 0.5f * (_toDensityX + _toDensityY);
		const ofRectangle b(ring.bin.x * _toDensityX, ring.bin.y * _toDensityY,
			ring.bin.width * _toDensityX, ring.bin.height * _toDensityY);
		const ofColor colour(199, 74, 52,
			(unsigned char)(255.0f * ofClamp(ring.intensity, 0.0f, 1.0f)));
		drawRoundedBand(b, ring.innerOffsetPx * scale, ring.outerOffsetPx * scale,
			ring.cornerRadiusPx * scale, colour);
	}
	_densityInject.end();

	// fireTest/src/ofApp.cpp::update() — the push, from hand movement.
	// mousePos*0.5 there is DENSITY-space-position*0.5 here (both map
	// density space down into the sim-resolution velocity FBO, which is
	// exactly half of density resolution in both files).
	_velocityInject.begin();
	ofClear(0, 0, 0, 0);
	ofEnableBlendMode(OF_BLENDMODE_DISABLED);
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
	_velocityInject.end();
	ofEnableBlendMode(OF_BLENDMODE_ALPHA);

	_fluid.addDensity(_densityInject.getTexture());
	_fluid.addTemperature(_densityInject.getTexture());
	_fluid.addVelocity(_velocityInject.getTexture());
	_fluid.update(dt);

	_lastDensityPos = std::move(currentDensityPos);
}

void FluidLayer::draw(int x, int y, int w, int h){
	// fireTest/src/ofApp.cpp::draw(): ordinary alpha blending (whatever
	// update() last left active), no special premultiplied blend func —
	// that was only needed for the rate-limited/premultiplied injection
	// this file no longer uses.
	_fluid.draw(x, y, w, h);
}
