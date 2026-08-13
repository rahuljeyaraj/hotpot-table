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
	// vorticity 1.0). These lean toward doc §14.3's mala style (rolling,
	// trails that linger) without building the full three-style system yet.
	// viscosityVel is left at its constructor default (0.5, already the
	// "low-mid" doc §14.3 wants) rather than set here — ftFluidFlow.h's
	// setViscosityVel/setViscosityDen/setViscosityTmp are declared to
	// return float but their bodies never return one (an addon bug, not a
	// typo here); calling any of the three is what makes MSVC compile and
	// diagnose it (C4716). Not worth patching a shared addon for a value
	// that was already right.
	const float kVorticity = 0.7f;
	const float kDissipationVel = 0.06f;
	const float kDissipationDen = 0.04f;
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
	std::unordered_map<int, glm::vec2> currentSimPos;
	currentSimPos.reserve(hands.size());

	_densityInject.begin();
	ofClear(0, 0, 0, 0);
	ofEnableAlphaBlending();
	ofSetColor(255, 210, 150, 255);
	for(const auto & h : hands){
		ofDrawCircle(h.x * _toSimX, h.y * _toSimY, kInjectRadiusSim);
	}
	_densityInject.end();

	_velocityInject.begin();
	ofClear(0, 0, 0, 0);
	// doc §14.1: velocity is encoded as colour, drawn with blending
	// disabled — overlapping hands must replace, not accumulate, or a
	// crossing pair of hands would double their combined push.
	ofEnableBlendMode(OF_BLENDMODE_DISABLED);
	for(const auto & h : hands){
		const glm::vec2 pos(h.x * _toSimX, h.y * _toSimY);
		glm::vec2 last = pos;
		auto it = _lastSimPos.find(h.id);
		if(it != _lastSimPos.end()){
			last = it->second;
		}
		const glm::vec2 delta = pos - last;
		ofSetColor(ofFloatColor(delta.x * kVelocityScale, delta.y * kVelocityScale, 0.0f));
		ofDrawCircle(pos.x, pos.y, kInjectRadiusSim);
		currentSimPos[h.id] = pos;
	}
	ofEnableAlphaBlending();
	_velocityInject.end();

	_lastSimPos = std::move(currentSimPos);

	_fluid.addDensity(_densityInject.getTexture());
	_fluid.addVelocity(_velocityInject.getTexture());
	_fluid.update(dt);
}

void FluidLayer::draw(int x, int y, int w, int h){
	ofSetColor(255);
	_fluid.draw(x, y, w, h);
}
