#include "Stage.h"

#include <algorithm>
#include <functional>
#include <sstream>

namespace {
	const char * kTag = "Stage";

	// docs/VISUAL_LAYER.md §1/§3: "Table background: #E8E6E1" — the
	// projector is the room light, so the table has to read as a warm
	// near-white surface, not paper-white like the bin interiors (which
	// stay literal 255,255,255 via the light pass below). Distinct from
	// the bins by design: on the projected surface the bins must read
	// brighter than the table around them.
	const ofColor kTableBackground(0xE8, 0xE6, 0xE1);
}

void Stage::setup(int stageW, int stageH, const std::string & keystonePath){
	_w = stageW;
	_h = stageH;

	// GL_RGBA, not a float format: this FBO holds UI colour and text, not
	// the fluid's density/velocity data, which keeps its own float buffers.
	// 8-bit is correct for what lands here.
	_fbo.allocate(_w, _h, GL_RGBA);

	loadKeystone(keystonePath);
}

void Stage::loadKeystone(const std::string & path){
	// Defaults to the untransformed stage rectangle — no keystone correction
	// at all — when the file is missing or malformed. Identity is the real
	// starting point rather than a placeholder for a lost value: the
	// projector is aimed and fullscreened onto a monitor, and software
	// keystoning only applies once someone measures the corners on the rig
	// and writes them here.
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

	// Doc §8.5's keystone_fingerprint: a short digest so core can notice the
	// corners changed underneath a solved homography. std::hash is not a
	// cryptographic digest and is not meant to be one — this only ever has
	// to disagree with its own previous value, on one machine.
	std::ostringstream corners;
	for(const auto & c : _corners){
		corners << c.x << "," << c.y << ";";
	}
	std::ostringstream hex;
	hex << std::hex << std::hash<std::string>{}(corners.str());
	_fingerprint = hex.str();
}

void Stage::beginContent(bool invertedField){
	_fbo.begin();
	// VISUAL_LAYER.md §1: the table background, not paper-white. The bin
	// interiors are what stay literal white, via the light pass below, which
	// is unconditionally opaque 255 regardless of this colour.
	//
	// Black instead, and only, for dot calibration — I9's single exception.
	// See the header.
	ofBackground(invertedField ? ofColor(0) : kTableBackground);
	// doc §13.2: ofxFlowTools leaves the blend mode as OF_BLENDMODE_ADD, so
	// alpha blending is enabled explicitly before the UI layer draws, every
	// frame. UiLayer must never depend on the blend state whatever ran
	// immediately before it happened to leave behind.
	ofEnableAlphaBlending();
}

void Stage::endContent(){
	_fbo.end();
}

void Stage::compositeAndWarp(const std::vector<ofRectangle> & cutoutsPx,
	float cutoutCornerRadiusPx, bool invertedField, const std::function<void()> & drawAboveLightPass){
	// --- I9's exception: dot calibration ---------------------------------
	// The light pass does not run. It exists to keep the table lit for the
	// camera, and during a solve the camera is deliberately at a dark
	// exposure looking for bright dots on black — the light pass would
	// stamp eight full-white rectangles across the pattern. This is not a
	// relaxation of I9; it is the case I9 itself carves out, and it is the
	// only one.
	if(!invertedField){
		// --- light pass ----------------------------------------------------
		// Opaque, always full white, and last — nothing drawn after this
		// point can put anything but flat white into a cutout, which is
		// I9's entire safety property. VISUAL_LAYER.md §1: "Bin interior:
		// #FFFFFF — pure white, all 8 bins, all modes, always."
		_fbo.begin();
		ofDisableAlphaBlending();
		ofSetColor(255);
		for(const auto & r : cutoutsPx){
			if(cutoutCornerRadiusPx <= 0.0f){
				ofDrawRectangle(r);
				continue;
			}
			// Filled path, not a stroked rect — same rule as UiLayer's rings
			// (doc §13.4): a filled shape is the only kind that survives the
			// programmable renderer the fluid forces.
			const float radius = std::min(cutoutCornerRadiusPx,
				std::min(r.width, r.height) * 0.5f);
			ofPath path;
			path.setFilled(true);
			path.setFillColor(ofColor(255));
			path.setCircleResolution(24);
			path.rectRounded(r, radius);
			path.draw();
		}

		// --- above the light pass -------------------------------------------
		// See the header comment on `drawAboveLightPass` for the reasoning.
		// `nullptr` — not serving, or no pointer this frame — makes this a
		// no-op, and I9 applies in full.
		if(drawAboveLightPass){
			ofEnableAlphaBlending();
			drawAboveLightPass();
		}
		_fbo.end();
	}

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
