#include "Stage.h"

#include <cmath>
#include <functional>
#include <sstream>

namespace {
	const char * kTag = "Stage";
}

void Stage::setup(int stageW, int stageH, const std::string & keystonePath){
	_w = stageW;
	_h = stageH;

	// GL_RGBA, not a float format: this is UI colour and text, not the
	// fluid's density/velocity data (which will want float precision when
	// FluidLayer exists). 8-bit is correct for what this FBO holds today.
	_fbo.allocate(_w, _h, GL_RGBA);

	loadKeystone(keystonePath);
}

void Stage::loadKeystone(const std::string & path){
	// Defaults to the untransformed stage rectangle — i.e. no keystone
	// correction at all — if the file is missing or malformed. That is the
	// only honest default: v3 §7.1 says to carry forward "the keystone
	// corner values currently in the oF app," but there are none in the
	// pre-rewrite code (VERIFIED — ofApp.cpp/.h have no quad-warp of any
	// kind; the projector was aimed and fullscreened onto a monitor, never
	// software-keystoned). Identity is therefore not a placeholder standing
	// in for a lost value; it is the actual starting point, to be replaced
	// once someone measures the real corners on the rig.
	_corners = {
		glm::vec2(0.0f, 0.0f),
		glm::vec2((float)_w, 0.0f),
		glm::vec2((float)_w, (float)_h),
		glm::vec2(0.0f, (float)_h),
	};

	if(ofFile::doesFileExist(path)){
		ofJson j = ofLoadJson(path);
		bool ok = j.contains("corners") && j["corners"].is_array() && j["corners"].size() == 4;
		if(ok){
			for(int i = 0; i < 4 && ok; i++){
				const ofJson & c = j["corners"][i];
				if(!c.is_array() || c.size() != 2 || !c[0].is_number() || !c[1].is_number()){
					ok = false;
					break;
				}
				_corners[i] = glm::vec2(c[0].get<float>(), c[1].get<float>());
			}
		}
		if(!ok){
			ofLogError(kTag) << path << " does not hold 4 numeric [x,y] corners"
				<< " — ignoring it and using the untransformed rectangle";
			// fall through with the identity default already set above
		}
		else {
			ofLogNotice(kTag) << "loaded keystone corners from " << path;
		}
	}
	else {
		ofLogNotice(kTag) << "no " << path << " — using the untransformed rectangle"
			<< " (v3 §7.1: measure the real corners on the rig and write them here)";
	}

	// Doc §8.5's keystone_fingerprint: a short digest so core can one day
	// notice the corners changed underneath a solved homography. std::hash
	// is not a cryptographic digest and isn't meant to be one — this only
	// ever has to disagree with its own previous value, on one machine.
	std::ostringstream corners;
	for(const auto & c : _corners){
		corners << c.x << "," << c.y << ";";
	}
	std::ostringstream hex;
	hex << std::hex << std::hash<std::string>{}(corners.str());
	_fingerprint = hex.str();
}

void Stage::beginContent(){
	_fbo.begin();
	// The paper base (§14.3: "the base of every palette is the paper,
	// near-white"). Flat white stands in for FluidLayer until M8 — the
	// same flat-field starting point the pre-rewrite app drew via
	// ofBackground(fieldGrey) at its default full-brightness index.
	ofBackground(255);
	// VERIFY, doc §13.2: "ofxFlowTools leaves the blend mode as
	// OF_BLENDMODE_ADD. Call ofEnableAlphaBlending() explicitly before
	// drawing the UI layer, every frame. Do not assume the state you left
	// it in." No fluid runs yet, so nothing has actually left ADD blending
	// behind today — but UiLayer draws unconditionally in every frame from
	// here on, including after M8 adds FluidLayer, so it must not depend on
	// what ran immediately before it either.
	ofEnableAlphaBlending();
}

void Stage::endContent(){
	_fbo.end();
}

void Stage::compositeAndWarp(float whiteFloor, const std::vector<ofRectangle> & cutoutsPx){
	// --- step 3: floor lift --------------------------------------------
	// out = k + (1-k)*in, applied as a single translucent white rect over
	// the whole FBO — see the class comment for why this equals the doc
	// formula exactly rather than approximating it.
	_fbo.begin();
	ofEnableAlphaBlending();
	ofSetColor(255, 255, 255, (int)roundf(ofClamp(whiteFloor, 0.0f, 1.0f) * 255.0f));
	ofDrawRectangle(0, 0, (float)_w, (float)_h);
	ofSetColor(255);

	// --- step 4: light pass ----------------------------------------------
	// Opaque, always full white regardless of whiteFloor (doc: "the bin
	// patches are always at full level regardless of field_level"), and
	// last — nothing drawn after this point can put anything but flat white
	// into a cutout, which is I9's entire safety property.
	ofDisableAlphaBlending();
	ofSetColor(255);
	for(const auto & r : cutoutsPx){
		ofDrawRectangle(r);
	}
	_fbo.end();

	// --- keystone warp to the real window --------------------------------
	ofClear(0.0f, 255.0f);
	ofSetColor(255);
	_fbo.getTexture().bind();

	ofMesh quad;
	quad.setMode(OF_PRIMITIVE_TRIANGLE_FAN);
	const glm::vec2 uv[4] = {
		_fbo.getTexture().getCoordFromPercent(0.0f, 0.0f),
		_fbo.getTexture().getCoordFromPercent(1.0f, 0.0f),
		_fbo.getTexture().getCoordFromPercent(1.0f, 1.0f),
		_fbo.getTexture().getCoordFromPercent(0.0f, 1.0f),
	};
	for(int i = 0; i < 4; i++){
		quad.addVertex(glm::vec3(_corners[i], 0.0f));
		quad.addTexCoord(uv[i]);
	}
	quad.draw();

	_fbo.getTexture().unbind();
}
