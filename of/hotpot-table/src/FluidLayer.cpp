#include "FluidLayer.h"

#include <algorithm>

using namespace flowTools;

namespace {
	// fireTest/src/ofApp.cpp's own literal constants — see FluidLayer.h's
	// 2026-08-14 class comment for why this file stopped adapting them.
	const float kInjectRadiusDensityPx = 30.0f;
	const float kInjectRadiusVelocityPx = 20.0f;
	const float kVelocityScale = 4.0f;

	// Kill switches, same pattern ofApp.cpp's own kFluidEnabled/
	// kDrawSkeleton use. 2026-08-14: split in two after "hand fireball is
	// missing" — the single kAmbientHandFireEnabled this replaced disabled
	// BOTH the visible hand blob and its velocity push at once, which was
	// more than the wind bug needed disabled. The wind theory was
	// specifically about velocity: every hand shares ONE velocity field
	// across the whole canvas (ftFluidFlow's own single velocityFbo), so a
	// swipe anywhere — including the empty centre gap, nowhere near a bin —
	// can inject a gust the sim propagates everywhere, a ring included.
	// Density has no such reach: it only ever paints a blob at the hand's
	// OWN position, so putting it back gives the table its cursor again
	// without reintroducing anything that could blow a ring around from a
	// distance. Velocity stays off until the ring is confirmed clean with
	// density alone back on; if it's still clean, try flipping this one
	// too — a static-looking blob rather than a flowing trail is the
	// tradeoff of leaving it off.
	const bool kHandFlameDensityEnabled = true;
	const bool kHandFlameVelocityEnabled = false;

	// 2026-08-14, developer request: "various colour flame for each bin."
	// One entry per bin index (FluidLayer::FireRing::colourIndex), `% 8`
	// guarded at the call site below rather than assumed in range. Chosen
	// the same way the earlier blue ring colour was — a low-to-moderate
	// peak channel value under MULTIPLY blend reads as itself rather than
	// washing toward the light #E8E6E1 background — spread across 8
	// distinct hues rather than 8 shades of one. Yellow/pale hues skipped
	// on purpose: this rig's own history (halo's gold, three revisions;
	// the plate rate line's amber reading red) is about ADDITIVE blend,
	// not this MULTIPLY path, but a near-white hue is risky under multiply
	// for the same reason kSparkColourHot would have been if that had
	// shipped — nothing to darken the background with. All unmeasured
	// against the projected table, same as every other colour this
	// session picked; the one thing checked is that they are 8 genuinely
	// different hues, not 8 numbers that happen to differ.
	const ofColor kFireRingColours[8] = {
		ofColor(214, 67, 44),    // 0: red
		ofColor(224, 121, 30),   // 1: orange
		ofColor(46, 163, 68),    // 2: green
		ofColor(30, 166, 160),   // 3: teal
		ofColor(30, 110, 220),   // 4: blue (the earlier single ring colour)
		ofColor(122, 46, 224),   // 5: purple
		ofColor(208, 30, 140),   // 6: magenta
		ofColor(110, 190, 30),   // 7: lime — greener than yellow on purpose
	};

	// **2026-08-14, rig report: "the screen is simply getting saturated
	// with white flame" — developer's own next diagnostic, requested
	// directly: make every bin the same colour and look again.** This is
	// separate from kFireRingHeat's finding above: that was about lift
	// (temperature, the RED channel of a second buffer); this is about the
	// visible density colour itself, per-bin hue, 8 different values. If
	// the white-out persists identically with one colour, hue was never
	// the density side of the story either, the same way it was not the
	// buoyancy side — narrows what is left to the accumulation math itself
	// (persistent additive buffer, doc comment above kFireRingMaxAlpha) or
	// to something upstream in how many bins/how long they are being fed.
	// `true` here, not a deleted array: kFireRingColours stays byte-for-
	// byte so this is a one-line revert once the question is answered,
	// same discipline as every other kill-switch in this file.
	const bool kFireRingSingleColourDiagnostic = true;
	const ofColor kFireRingSingleColour(30, 110, 220);   // the pre-palette blue

	// 2026-08-14, developer question: "is there a way to reduce the white
	// part of the flame and show more colour... when the bin is on fire?"
	// Mechanism, worked out from ftFluidFlow's own add/dissipate shaders
	// rather than guessed: `addDensity` ADDS the injected texture into a
	// PERSISTENT buffer every single frame, and at density dissipation 1.0
	// (retained fraction `1 - dt*1.0`, ~0.967/frame at 30fps) a steady
	// hover's geometric-series steady state is roughly the per-frame
	// injected value divided by (1 - retained) — around 30x the one-frame
	// value. A channel that starts anywhere close to 255 saturates well
	// before that steady state and STAYS saturated; once every channel is
	// pinned at 255, MULTIPLY blend against the background is the
	// identity (colour*255/255 = colour), so the densest part of the ring
	// shows the bare background colour — pale, reads as "white" — while
	// only the thinner, not-yet-saturated edge still shows the injected
	// hue. Capped below 255 rather than raising dissipation further
	// (density is already at fireTest's own max-sane value, and pushing
	// it higher risks the same "diffuses to invisible" failure
	// FluidLayer.h's own 2026-08-14 rewrite note describes): the injected
	// ring alpha now tops out at kFireRingMaxAlpha, so even a long steady
	// hover's ~30x amplification lands under full saturation and the
	// densest part of the ring keeps showing its own colour instead of
	// washing to the background. Unmeasured — unlike a hue, "how white is
	// too white" can only be judged on the projected table; 190 is a
	// starting guess, tunable down further (more colour, less brightness)
	// or up (brighter, more washed) once seen.
	const float kFireRingMaxAlpha = 190.0f;

	// **2026-08-14: the ring's HUE was silently controlling how hard its
	// flame rises.** Traced through the installed addon, not guessed:
	// `addTemperature` writes into ftFluidFlow's `temperatureFbo`, which is
	// allocated **GL_R32F** (ftFluidFlow.cpp's own setup) — a single RED
	// channel, so green and blue are discarded on write. ftBuoyancyShader
	// then reads `texture(tex_temperature, st).x` and applies an UPWARD
	// force linearly proportional to it. Feeding the same coloured texture
	// to addDensity and addTemperature — which this file did — therefore
	// made lift a function of each bin's red channel. kFireRingColours'
	// reds run 30 (teal/blue) to 224 (orange), so bins differed by ~7x in
	// how hard they rose, for no reason anyone chose. Temperature now gets
	// its OWN injection buffer, same geometry, hue-independent red, so a
	// palette edit can never move a flame's lift again.
	//
	// **That coupling is real, but it was NOT the "left bins have too much
	// flame" bug — equalising it on the rig did not fix the asymmetry.**
	// Recorded so the next person does not re-derive this and conclude the
	// same wrong thing: the left/right split survives with every bin on an
	// identical heat, so its cause is somewhere else entirely (it is also
	// not the bin grid and not the homography, both recalibrated first).
	// This buffer is worth keeping on its own merits — hue should not
	// control physics — but it is not the fix.
	//
	// **30 was tried and is WRONG, do not go back to it.** It is the red of
	// the single blue (30,110,220) every ring used before the 8-hue palette,
	// so it looked like the value the flame had been tuned at. On the table
	// it made every ring read THICKER and BRIGHTER, not calmer: low heat is
	// low buoyancy, and a ring the sim never lifts is a ring whose density
	// piles up in place, frame after frame, into the persistent buffer
	// until it saturates — the exact white-out kFireRingMaxAlpha exists to
	// fight. Lift is what carries density away; removing it does not quiet
	// a flame, it puddles one. The usable range is bounded on both ends:
	// too low puddles, too high (the old 214-224) billows more than the
	// developer wants. 120 is a mid-range starting point and nothing more —
	// it has not been looked at on the projected table yet.
	const int kFireRingHeat = 120;

	// The hand blob's heat, deliberately the same 199 its density colour
	// (199,74,52) already carried — the hand's own flame is not what
	// changed today and must not change now. Split out as its own name only
	// so the two can be tuned apart later if the hand ever needs it.
	const int kHandFlameHeat = 199;

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

	// The heat buffer (see kFireRingHeat). Density resolution, not sim, so
	// both injections are drawn in exactly the same coordinates — ftFlow::
	// add() rescales whatever it is handed onto the sim-resolution
	// temperatureFbo either way, so matching density here costs nothing and
	// removes a second coordinate space from this file.
	_temperatureInject.allocate(_densityW, _densityH, GL_RGBA);
	ftUtil::zero(_temperatureInject);
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
	// Two buffers, ONE geometry. The density buffer carries the per-bin hue
	// a diner sees; the heat buffer carries how hard that ring rises, in a
	// red the palette cannot reach into (kFireRingHeat's own comment has
	// the whole reason). Written as one lambda rather than two copies of
	// the loop specifically so the shapes cannot drift apart — a ring that
	// glowed in one buffer and lifted in a slightly different place in the
	// other would be a far nastier bug than the one this is fixing.
	//
	// fireTest/src/ofApp.cpp::update() — the visible "fire" blob at the
	// hand: flat alpha=255, ORDINARY blending, every frame, no rate
	// limiting. Byte-for-byte, generalized from one mouse to N hands.
	//
	// VISUAL_LAYER.md §9 build item 6: the active bin's own emitter, drawn
	// into the SAME buffers as the hand's. `intensity` is UiLayer's own
	// crossfade spring — the same one the halo fades out by — scaling
	// alpha only, never the geometry, so the ring fills in smoothly rather
	// than popping to full strength the instant `hl` flips to "hover". It
	// scales the heat identically, via the same alpha, so a ring fading in
	// gains its lift on exactly the same curve as its colour.
	// Alpha capped at kFireRingMaxAlpha, not 255 — see that constant's own
	// comment on why a full-alpha ring saturates to a white-looking core
	// under a long hover. Colour picked per bin from kFireRingColours,
	// `colourIndex` wrapped with `% 8` here rather than trusted, since
	// this class has no way to know the caller's own bin count.
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

	// fireTest/src/ofApp.cpp::update() — the push, from hand movement.
	// mousePos*0.5 there is DENSITY-space-position*0.5 here (both map
	// density space down into the sim-resolution velocity FBO, which is
	// exactly half of density resolution in both files).
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

	// addTemperature reads ONLY the red channel (ftFluidFlow's temperatureFbo
	// is GL_R32F) — which is exactly why it gets its own buffer now rather
	// than the coloured one. See kFireRingHeat.
	_fluid.addDensity(_densityInject.getTexture());
	_fluid.addTemperature(_temperatureInject.getTexture());
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
