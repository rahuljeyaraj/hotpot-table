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
	// ofFloatColor(-delta.x * 4.0, -delta.y * 4.0, 0.0, 1.0).
	const float kVelocityScale = 4.0f;
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
	_fluid.getParameters().getGroup("viscosity").getFloat("velocity") = 1.0f;
	_fluid.getParameters().getGroup("viscosity").getFloat("density") = 1.0f;
	_fluid.getParameters().getGroup("viscosity").getFloat("temperature") = 1.0f;
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
	struct HandPos {
		int id;
		glm::vec2 pos;
	};
	std::vector<HandPos> positions;
	positions.reserve(hands.size());
	for(const auto & h : hands){
		positions.push_back({h.id, glm::vec2(h.x * _toSimX, h.y * _toSimY)});
	}

	// fireTest/src/ofApp.cpp::update(): the visible blob at each hand,
	// full-alpha, ordinary alpha blending — no per-frame dt scaling, no
	// manual premultiply. Working as-is in fireTest (verified against a
	// white background there too), reused verbatim rather than the more
	// defensive version this file tried previously.
	const float radiusDen = kInjectRadiusFireTestPx * ((float)_simW / kInjectRadiusFireTestDensityW);
	_densityInject.begin();
	ofClear(0, 0, 0, 0);
	ofEnableBlendMode(OF_BLENDMODE_ALPHA);
	ofSetColor(199, 74, 52, 255);
	for(const auto & p : positions){
		ofDrawCircle(p.pos.x, p.pos.y, radiusDen);
	}
	_densityInject.end();

	// fireTest's mouseVelocityFbo: blending disabled (overlapping hands
	// replace rather than accumulate — a crossing pair must not double
	// their combined push), colour is -delta*4 exactly as fireTest has it.
	std::unordered_map<int, glm::vec2> currentSimPos;
	currentSimPos.reserve(positions.size());
	const float radiusVel = kVelRadiusFireTestPx * ((float)_simW / kVelRadiusFireTestSimW);
	_velocityInject.begin();
	ofClear(0, 0, 0, 0);
	ofEnableBlendMode(OF_BLENDMODE_DISABLED);
	for(const auto & p : positions){
		glm::vec2 last = p.pos;
		auto it = _lastSimPos.find(p.id);
		if(it != _lastSimPos.end()){
			last = it->second;
		}
		const glm::vec2 delta = p.pos - last;
		currentSimPos[p.id] = p.pos;

		ofSetColor(ofFloatColor(-delta.x * kVelocityScale, -delta.y * kVelocityScale, 0.0f, 1.0f));
		ofDrawCircle(p.pos.x, p.pos.y, radiusVel);
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
	_fluid.draw(x, y, w, h);
}
