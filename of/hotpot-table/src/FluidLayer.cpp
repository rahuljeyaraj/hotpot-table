#include "FluidLayer.h"

using namespace flowTools;

namespace {
	// Sim-space px. Small on purpose — this is a fingertip-sized injection,
	// not a hand-sized one; the solver's own dissipation/vorticity spreads
	// it out.
	const float kInjectRadiusSim = 12.0f;

	// doc §14.1's exact encoding: ofFloatColor(d.x*2.0, d.y*2.0, 0).
	const float kVelocityScale = 2.0f;

	// ftFluidFlow's own parameter ranges are 0..1 (verified against the
	// installed ftFluidFlow.cpp constructor, not assumed — its defaults are
	// speed 0.3, dissipation 0.1, viscosityVel 0.5/viscosityDen 0.0,
	// vorticity 1.0). These lean toward doc §14.3's mala style (rolling)
	// without building the full three-style system yet.
	// viscosityVel is left at its constructor default (0.5, already the
	// "low-mid" doc §14.3 wants) rather than set here — ftFluidFlow.h's
	// setViscosityVel/setViscosityDen/setViscosityTmp are declared to
	// return float but their bodies never return one (an addon bug, not a
	// typo here); calling any of the three is what makes MSVC compile and
	// diagnose it (C4716). Not worth patching a shared addon for a value
	// that was already right.
	const float kVorticity = 0.7f;

	// ftFluidFlow.cpp's real update() (read from the installed source, not
	// assumed): each step multiplies existing density by (1 - dt*dissipation)
	// — dissipation is a PER-SECOND fraction, not a per-frame one. And
	// addDensity/addVelocity are pure accumulation (dst = dst + src, no
	// clamp — verified in ftAddMultipliedShader.h). The first version of
	// this file drew a full-alpha (255) circle EVERY frame regardless of
	// dt: at 60fps that is 60 additions/second of full-strength colour
	// against a dissipation draining only ~4%/second — density piled up to
	// roughly 25x saturation within a couple of seconds and stuck there
	// pure white (desaturated grey once floor-lifted), unmoving under
	// whatever hand position it was near. That was the "terrible, static"
	// result seen on the table, not a rendering or tracking bug.
	//
	// Fixed by injecting a per-SECOND rate (scaled by dt, so it's frame-
	// rate independent) instead of a flat per-frame blob, sized so the
	// resting steady state (rate / dissipation) lands around 0.6 — visible
	// colour, nowhere near the 1.0 ceiling — with movement adding on top so
	// a swipe still reads brighter without permanently saturating the
	// pixels it passes through only briefly.
	const float kDissipationDen = 0.6f;     // fraction lost per second
	const float kDissipationVel = 0.4f;     // ditto, prevents slow velocity drift too
	const float kDensityBaseRatePerSec = 0.36f;      // steady state 0.36/0.6 = 0.6
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
	_fluid.setVorticity(kVorticity);
	_fluid.setDissipationVel(kDissipationVel);
	_fluid.setDissipationDen(kDissipationDen);

	_densityInject.allocate(_simW, _simH, GL_RGBA);
	ftUtil::zero(_densityInject);
	// GL_RG32F per doc §14.1: two float channels, x/y velocity, no need for
	// more precision or channels than that.
	_velocityInject.allocate(_simW, _simH, GL_RG32F);
	ftUtil::zero(_velocityInject);
}

void FluidLayer::update(float dt, const std::vector<CursorLink::Hand> & hands){
	const float dtSafe = std::max(dt, 1.0f / 240.0f);   // guards the /dt below on a stall/first frame

	struct HandMotion {
		glm::vec2 pos;
		glm::vec2 delta;
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
		motions.push_back({pos, delta, glm::length(delta) / dtSafe});
		currentSimPos[h.id] = pos;
	}
	_lastSimPos = std::move(currentSimPos);

	_densityInject.begin();
	ofClear(0, 0, 0, 0);
	// Blending DISABLED here too, same reason as the velocity block below:
	// ofEnableAlphaBlending()'s GL_SRC_ALPHA/GL_ONE_MINUS_SRC_ALPHA func
	// applies to every channel uniformly, including alpha itself, so
	// drawing a low-alpha circle onto a cleared-to-zero FBO leaves
	// stored.a = alpha*alpha (compounds toward zero) while stored.rgb =
	// colour*alpha (linear) — verified via a temporary density-texture
	// readback: rgb reached ~0.6 while alpha never passed ~0.06, so the
	// final draw onto the white paper background came out >90% white.
	// Premultiplying by hand and writing straight (no blend) keeps rgb and
	// alpha in the same ratio addDensity's later accumulation expects.
	ofEnableBlendMode(OF_BLENDMODE_DISABLED);
	for(const auto & m : motions){
		const float rate = kDensityBaseRatePerSec + kDensityMotionGainPerSimPxPerSec * m.speedPerSec;
		const float alpha = ofClamp(rate * dt, 0.0f, 1.0f);
		ofSetColor((int)roundf(255 * alpha), (int)roundf(210 * alpha),
			(int)roundf(150 * alpha), (int)roundf(alpha * 255.0f));
		ofDrawCircle(m.pos.x, m.pos.y, kInjectRadiusSim);
	}
	ofEnableAlphaBlending();
	_densityInject.end();

	_velocityInject.begin();
	ofClear(0, 0, 0, 0);
	// doc §14.1: velocity is encoded as colour, drawn with blending
	// disabled — overlapping hands must replace, not accumulate, or a
	// crossing pair of hands would double their combined push.
	ofEnableBlendMode(OF_BLENDMODE_DISABLED);
	for(const auto & m : motions){
		ofSetColor(ofFloatColor(m.delta.x * kVelocityScale, m.delta.y * kVelocityScale, 0.0f));
		ofDrawCircle(m.pos.x, m.pos.y, kInjectRadiusSim);
	}
	ofEnableAlphaBlending();
	_velocityInject.end();

	_fluid.addDensity(_densityInject.getTexture());
	_fluid.addVelocity(_velocityInject.getTexture());
	_fluid.update(dt);
}

void FluidLayer::draw(int x, int y, int w, int h){
	ofSetColor(255);
	_fluid.draw(x, y, w, h);
}
