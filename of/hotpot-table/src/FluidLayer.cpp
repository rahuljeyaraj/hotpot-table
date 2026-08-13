#include "FluidLayer.h"

using namespace flowTools;

namespace {
	// fireTest/src/ofApp.cpp: ofDrawCircle(mousePos, 30) at densityWidth
	// 1280. Scaled down by the same ratio to this sim's own resolution
	// rather than reused as a literal 30px, since the sim grid here is
	// much smaller than fireTest's 1280x720 density buffer.
	const float kInjectRadiusFireTestPx = 30.0f;
	const float kInjectRadiusFireTestDensityW = 1280.0f;

	// fireTest/src/ofApp.cpp's velocity injection radius, same scaling.
	const float kVelRadiusFireTestPx = 20.0f;
	const float kVelRadiusFireTestSimW = 640.0f; // fireTest's simulationWidth (densityWidth/2)

	// fireTest's mouseVelocityFbo colour formula, verbatim:
	// ofFloatColor(-delta.x * 4.0, -delta.y * 4.0, 0.0, 1.0). Velocity is
	// naturally self-limiting (each frame's inject reflects the CURRENT
	// delta, replacing rather than compounding — addVelocity accumulates,
	// but a still hand contributes ~0 every frame), so it is ported as-is.
	const float kVelocityScale = 4.0f;

	// Density is NOT self-limiting the way velocity is: addDensity() is a
	// pure accumulation (verified in ftFluidFlow.cpp, no clamp), and
	// fireTest's own injection draws a FULL-ALPHA (255) circle every single
	// frame regardless of dt or motion. Against dissipation.density=1.0
	// (fireTest's own value, kept below) that still loses only a fraction
	// per second, so density piles up unbounded within a couple of seconds
	// — confirmed today via a direct rig test: the fluid was genuinely
	// invisible, not merely dim, because a channel saturated far past 1.0
	// gets GL-clamped to solid white on draw, which is indistinguishable
	// from this app's white paper background. This is the exact "terrible,
	// static... pure white" bug this file's own history already diagnosed
	// and fixed once before being reverted to fireTest's verbatim version —
	// re-fixed here the same way: inject a per-SECOND rate (scaled by dt)
	// instead of a flat per-frame blob, sized so the resting steady state
	// (rate / dissipationDen) lands around 0.6 — visible, nowhere near the
	// clamp ceiling — with hand speed adding on top so a swipe still reads
	// brighter without permanently saturating the pixels it passes through.
	const float kDensityBaseRatePerSec = 0.6f;      // steady state 0.6/1.0 = 0.6
	const float kDensityMotionGainPerSimPxPerSec = 0.02f;
}

void FluidLayer::setup(int stageW, int stageH, int simScale){
	_simW = std::max(1, stageW / simScale);
	_simH = std::max(1, stageH / simScale);
	_toSimX = (float)_simW / (float)stageW;
	_toSimY = (float)_simH / (float)stageH;

	// Single-resolution overload: density/output resolution equals the
	// simulation grid. draw() upscales to stage size.
	_fluid.setup(_simW, _simH);

	// fireTest/src/ofApp.cpp::setup(), verbatim — tuned live against a
	// white background (fireTest's draw() calls ofBackground(255)), so
	// reused as-is rather than re-derived for this app's own paper tone.
	_fluid.getParameters().getFloat("speed") = 0.0969388f;
	_fluid.getParameters().getGroup("dissipation").getFloat("velocity") = 0.0f;
	_fluid.getParameters().getGroup("dissipation").getFloat("density") = 1.0f;
	_fluid.getParameters().getGroup("dissipation").getFloat("temperature") = 1.0f;
	_fluid.getParameters().getGroup("dissipation").getFloat("pressure") = 0.5f;
	// Deliberately NOT fireTest's viscosity.density/temperature = 1.0.
	// ftFluidFlow::update() runs 20 Jacobi diffusion iterations PER FRAME at
	// whatever strength viscosityDen/Tmp hold — at 1.0 that diluted density
	// across the whole sim grid faster than injection could make anything
	// visible, confirmed on the rig (fluid genuinely invisible with these at
	// 1.0, a moving-but-faint grey trail with them at 0). fireTest's own
	// copy of this same code path likely suffers the identical dilution —
	// it is masked there by a SEPARATE bug (an unbounded per-frame
	// full-alpha density injection, see kDensityBaseRatePerSec below) that
	// happens to keep outpacing the dilution with sheer overinjection.
	// viscosity.velocity is kept at fireTest's 1.0 — velocity diffusion
	// smooths the flow rather than erasing a colour channel, and nothing
	// here contradicted it.
	_fluid.getParameters().getGroup("viscosity").getFloat("velocity") = 1.0f;
	_fluid.getParameters().getGroup("viscosity").getFloat("density") = 0.0f;
	_fluid.getParameters().getGroup("viscosity").getFloat("temperature") = 0.0f;
	_fluid.getParameters().getFloat("vorticity") = 0.05f;
	_fluid.getParameters().getGroup("smoke buoyancy").getFloat("buoyancy") = 0.0f;
	_fluid.getParameters().getGroup("smoke buoyancy").getFloat("weight") = 1.0f;
	_fluid.getParameters().getGroup("smoke buoyancy").getFloat("ambient temperature") = 0.0f;

	_densityInject.allocate(_simW, _simH, GL_RGBA);
	ftUtil::zero(_densityInject);
	_velocityInject.allocate(_simW, _simH, GL_RG32F);
	ftUtil::zero(_velocityInject);
}

void FluidLayer::update(float dt, const std::vector<CursorLink::Hand> & hands){
	const float dtSafe = std::max(dt, 1.0f / 240.0f);   // guards the /dt below on a stall/first frame

	struct HandMotion {
		int id;
		glm::vec2 pos;
		float speedPerSec;
	};
	std::vector<HandMotion> motions;
	motions.reserve(hands.size());
	std::unordered_map<int, glm::vec2> currentSimPos;
	currentSimPos.reserve(hands.size());
	for(const auto & h : hands){
		const glm::vec2 pos(h.x * _toSimX, h.y * _toSimY);
		glm::vec2 last = pos;
		auto it = _lastSimPos.find(h.id);
		if(it != _lastSimPos.end()){
			last = it->second;
		}
		const glm::vec2 delta = pos - last;
		motions.push_back({h.id, pos, glm::length(delta) / dtSafe});
		currentSimPos[h.id] = pos;
	}

	// Rate-based, dt-scaled — see the class comment on kDensityBaseRatePerSec
	// for why fireTest's own flat full-alpha-every-frame injection cannot be
	// ported verbatim here. fireTest's circle colour/radius are kept.
	// Blending DISABLED, colour premultiplied by hand: ofEnableAlphaBlending's
	// GL_SRC_ALPHA/GL_ONE_MINUS_SRC_ALPHA blend func applies to alpha too, so
	// a partial-alpha circle drawn onto a cleared-to-zero FBO would leave
	// stored.a = alpha*alpha (compounds toward zero) while stored.rgb stays
	// linear in alpha — this is only invisible in fireTest's own code
	// because fireTest always injects at alpha=255 exactly, where 1*1=1
	// hides the bug; the moment injection is rate-limited to a fraction
	// (needed to avoid the unbounded-accumulation saturation above), the
	// same squaring desaturates every colour toward the paper background.
	// Confirmed today via a direct rig screenshot: identical rate-limited
	// injection through ORDINARY alpha blending rendered as a colourless
	// grey wisp, not the warm tone the maths says it should hold.
	const float radiusDen = kInjectRadiusFireTestPx * ((float)_simW / kInjectRadiusFireTestDensityW);
	_densityInject.begin();
	ofClear(0, 0, 0, 0);
	ofEnableBlendMode(OF_BLENDMODE_DISABLED);
	for(const auto & m : motions){
		const float rate = kDensityBaseRatePerSec + kDensityMotionGainPerSimPxPerSec * m.speedPerSec;
		const float alpha = ofClamp(rate * dt, 0.0f, 1.0f);
		ofSetColor((int)roundf(199 * alpha), (int)roundf(74 * alpha),
			(int)roundf(52 * alpha), (int)roundf(alpha * 255.0f));
		ofDrawCircle(m.pos.x, m.pos.y, radiusDen);
	}
	ofEnableAlphaBlending();
	_densityInject.end();

	// fireTest's mouseVelocityFbo: blending disabled (overlapping hands
	// replace rather than accumulate — a crossing pair must not double
	// their combined push), colour is -delta*4 exactly as fireTest has it.
	// Velocity does not need the density fix above — addVelocity() also
	// accumulates, but each frame's inject reflects the CURRENT delta
	// (replacing what a still hand contributed, ~0, rather than piling a
	// fresh full-strength push on top of an already-large one), and
	// dissipation.velocity is 0 in fireTest's own params specifically
	// because the field is expected to relax through advection, not decay.
	const float radiusVel = kVelRadiusFireTestPx * ((float)_simW / kVelRadiusFireTestSimW);
	_velocityInject.begin();
	ofClear(0, 0, 0, 0);
	ofEnableBlendMode(OF_BLENDMODE_DISABLED);
	for(const auto & m : motions){
		glm::vec2 last = m.pos;
		auto it = _lastSimPos.find(m.id);
		if(it != _lastSimPos.end()){
			last = it->second;
		}
		const glm::vec2 delta = m.pos - last;
		ofSetColor(ofFloatColor(-delta.x * kVelocityScale, -delta.y * kVelocityScale, 0.0f, 1.0f));
		ofDrawCircle(m.pos.x, m.pos.y, radiusVel);
	}
	ofEnableAlphaBlending();
	_velocityInject.end();
	_lastSimPos = std::move(currentSimPos);

	// fireTest adds the same density texture as both density AND
	// temperature — kept, since buoyancy is 0 either way but this is what
	// "ported verbatim" means.
	_fluid.addDensity(_densityInject.getTexture());
	_fluid.addTemperature(_densityInject.getTexture());
	_fluid.addVelocity(_velocityInject.getTexture());
	_fluid.update(dt);
}

void FluidLayer::draw(int x, int y, int w, int h){
	ofSetColor(255);
	// The density texture is premultiplied-alpha (update()'s own comment on
	// the injection): every colour it holds is already colour*alpha, so
	// drawing it with ordinary (GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
	// blending — whatever Stage::beginContent() left active — applies alpha
	// a SECOND time (colour*alpha^2), desaturating everything toward the
	// paper background. Confirmed on the rig: after fixing the injection
	// side, the fluid was clearly visible and tracking correctly but
	// rendered as a colourless grey trail, not the warm tone the density
	// buffer's own maths guarantees it holds. The correct blend func for
	// premultiplied colour is (GL_ONE, GL_ONE_MINUS_SRC_ALPHA); oF has no
	// ofEnableBlendMode() constant for it, so it is bound directly and
	// restored to ordinary alpha blending immediately after for whatever
	// draws next (UiLayer, right after this call in ofApp::draw).
	glEnable(GL_BLEND);
	glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA);
	_fluid.draw(x, y, w, h);
	ofEnableAlphaBlending();
}
