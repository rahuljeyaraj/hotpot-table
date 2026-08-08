#include "ofApp.h"
#include "TableGeometry.h"

namespace {
	// calibration dot appearance
	const float kDotRadiusPx = 20.0f;

	// Dot 0 is drawn oversized so the solver can tell it from the other eight.
	// The nine centres are evenly spaced on both axes, so the pattern maps onto
	// itself under a 180 degree rotation and a flipped homography reprojects
	// perfectly - error cannot break the tie, and neither can looking at it.
	// One physically larger dot can. Radius only: the centre does not move, so
	// the geometry every other step depends on is unchanged.
	const float kMarkerDotRadiusPx = 30.0f;
	const size_t kMarkerDotIndex = 0;

	// dot centres in table mm - all nine sit on solid plywood, clear of every
	// tray cutout. Do not move these without re-measuring the cutouts.
	const float kCalibXMM[] = { 44.0f, 762.0f, 1480.0f };
	const float kCalibYMM[] = { 86.0f, 457.0f, 828.0f };

	// --- bins ----------------------------------------------------------------
	// Outline width in table mm, so it stays a fixed physical width on the
	// plywood rather than a fixed pixel width.
	const float kBinOutlineMM = 3.0f;

	// Nudge steps in table mm. 1 mm is finer than the calibration's own 3.66 px
	// mean reprojection error (~2.9 mm at table scale), so the useful limit is
	// what can be seen on the plywood, not the step.
	const float kNudgeStepMM = 1.0f;
	const float kNudgeFastStepMM = 5.0f;

	// Lives in bin/data/ alongside the other rig state.
	const char * kOffsetsFile = "bin_offsets.json";

	// --- hand tracking -------------------------------------------------------
	// Must match --osc-port in tools/tracker/track_hands.py.
	const int kOscPort = 12345;

	const float kHandRadiusPx = 40.0f;

	// A hand that has not been heard from in this long is dropped rather than
	// left frozen on the table. Covers both the tracker losing the hand and the
	// tracker not running at all.
	//
	// 500 ms is roughly 15 frames of a 30 fps tracker, so a one- or two-frame
	// detection dropout - which is common and means nothing - does not blink
	// the dot out. Deliberately not a smoothing filter: stage 1 shows raw
	// positions so the true jitter stays visible.
	const uint64_t kHandTimeoutMS = 500;

	// One colour per hand id, purely so two hands can be told apart while the
	// loop is being evaluated. This is NOT the blob cursor of stage 4, whose
	// colour is fixed and reserved exclusively for progress indication.
	const ofColor kHandColours[] = {
		ofColor(0, 200, 255),   // id 0, cyan
		ofColor(255, 160, 0),   // id 1, amber
	};
	const size_t kHandColourCount =
		sizeof(kHandColours) / sizeof(kHandColours[0]);
}

//--------------------------------------------------------------
void ofApp::logWindowState(const std::string & when){
	ofLogNotice("ofApp") << when
		<< ": pos(" << ofGetWindowPositionX() << "," << ofGetWindowPositionY() << ")"
		<< " size " << ofGetWindowWidth() << "x" << ofGetWindowHeight()
		<< " screen " << ofGetScreenWidth() << "x" << ofGetScreenHeight()
		<< " fullscreen=" << (ofGetWindowMode() == OF_FULLSCREEN ? "yes" : "no");
}

//--------------------------------------------------------------
void ofApp::logCalibrationDots(){
	for(size_t i = 0; i < calibDotsMM.size(); i++){
		const glm::vec2 & mm = calibDotsMM[i];
		ofLogNotice("ofApp") << "dot " << i
			<< ": table (" << ofToString(mm.x, 1) << ", " << ofToString(mm.y, 1) << ") mm"
			<< " -> proj (" << (int)roundf(mmToPxX(mm.x))
			<< ", " << (int)roundf(mmToPxY(mm.y)) << ") px"
			<< (i == kMarkerDotIndex ? "  [marker]" : "");
	}
}

//--------------------------------------------------------------
void ofApp::setup(){
	logWindowState("setup before fullscreen");

	ofSetFullscreen(true);

	logWindowState("setup after fullscreen");

	// Everything placed in mm goes through mmToPx*, which scales to a hardcoded
	// PROJ_W_PX x PROJ_H_PX. On any other framebuffer the bins and calibration
	// dots land somewhere else on the plywood. Warn rather than rescale: the
	// homography was solved at this resolution too, so quietly stretching to
	// fit would hide a mismatch that also invalidates the solve.
	if(ofGetWidth() != PROJ_W_PX || ofGetHeight() != PROJ_H_PX){
		ofLogWarning("ofApp") << "framebuffer is " << ofGetWidth() << "x" << ofGetHeight()
			<< " but mm-placed geometry is calibrated to " << PROJ_W_PX << "x" << PROJ_H_PX
			<< " - bins and calibration dots will NOT line up with the table."
			<< " Not rescaling.";
	}

	ofBackground(0);

	// row-major, top row first
	for(float y : kCalibYMM){
		for(float x : kCalibXMM){
			calibDotsMM.push_back(glm::vec2(x, y));
		}
	}

	loadOffsets();

	ofSetCircleResolution(64);

	oscReceiver.setup(kOscPort);
	ofLogNotice("ofApp") << "listening for hand positions on OSC port " << kOscPort;
}

//--------------------------------------------------------------
void ofApp::receiveOsc(){
	// Drain the queue every frame. The tracker sends one message per hand per
	// frame and may well run faster than this app draws, so leaving anything
	// buffered would mean drawing a stale position.
	ofxOscMessage m;
	while(oscReceiver.getNextMessage(m)){
		uint64_t now = ofGetElapsedTimeMillis();

		if(m.getAddress() == "/hand"){
			if(m.getNumArgs() < 3){
				ofLogWarning("ofApp") << "/hand with " << m.getNumArgs()
					<< " args, expected 3";
				continue;
			}

			// Already projector pixels - the tracker owns the homography, so
			// nothing here needs to know the camera exists.
			int id = m.getArgAsInt(0);
			Hand & hand = hands[id];
			hand.posPx = glm::vec2(m.getArgAsFloat(1), m.getArgAsFloat(2));
			hand.lastSeenMS = now;

			lastMessageMS = now;
			everReceived = true;
		}
		else if(m.getAddress() == "/hand/none"){
			// Deliberately not an instant clear. This is a liveness beat: it
			// says the tracker is alive and currently sees nothing. Existing
			// hands are left to time out on their own, so a single dropped
			// detection frame does not blink the dot.
			lastMessageMS = now;
			everReceived = true;
		}
	}
}

//--------------------------------------------------------------
void ofApp::nudgeOffset(float dxMM, float dyMM){
	offsetXMM += dxMM;
	offsetYMM += dyMM;

	// Logged as well as drawn: the on-screen readout is on the projector, which
	// is across the room from whoever is holding the arrow keys.
	ofLogNotice("ofApp") << "bin offset (" << ofToString(offsetXMM, 1) << ", "
		<< ofToString(offsetYMM, 1) << ") mm - unsaved, press s to keep it";
}

//--------------------------------------------------------------
void ofApp::saveOffsets(){
	ofJson j;
	j["offsetXMM"] = offsetXMM;
	j["offsetYMM"] = offsetYMM;

	const std::string path = ofToDataPath(kOffsetsFile);
	if(ofSaveJson(path, j)){
		ofLogNotice("ofApp") << "saved bin offset (" << ofToString(offsetXMM, 1) << ", "
			<< ofToString(offsetYMM, 1) << ") mm to " << path;
	}
	else {
		ofLogError("ofApp") << "could not write " << path
			<< " - the nudge is still applied, but only until this run ends";
	}
}

//--------------------------------------------------------------
void ofApp::loadOffsets(){
	const std::string path = ofToDataPath(kOffsetsFile);

	// Checked rather than left to ofLoadJson, which logs an error for a missing
	// file. No offsets yet is the normal state on a fresh clone, not a fault.
	if(!ofFile::doesFileExist(path)){
		ofLogNotice("ofApp") << "no " << kOffsetsFile
			<< ", bins start at their CAD positions";
		return;
	}

	ofJson j = ofLoadJson(path);

	// A file that exists but does not parse must be loud. Falling back to zero
	// silently would look exactly like a nudge that was never saved, and the
	// natural response to that is to re-measure a rig that was already right.
	if(!j.contains("offsetXMM") || !j.contains("offsetYMM")
		|| !j["offsetXMM"].is_number() || !j["offsetYMM"].is_number()){
		ofLogError("ofApp") << path << " has no numeric offsetXMM/offsetYMM"
			<< " - ignoring it and starting at the CAD positions";
		return;
	}

	offsetXMM = j["offsetXMM"].get<float>();
	offsetYMM = j["offsetYMM"].get<float>();
	ofLogNotice("ofApp") << "loaded bin offset (" << ofToString(offsetXMM, 1) << ", "
		<< ofToString(offsetYMM, 1) << ") mm from " << path;
}

//--------------------------------------------------------------
void ofApp::drawBinCutouts(){
	// Section 8 of CLAUDE.md: the projector must put near-zero light into the
	// bins. Projected colour landing on the food contaminates the classifier's
	// input, which was trained on plain ingredients under ambient light.
	//
	// So these are unconditional - no toggle. Anything that can be seen inside
	// a cutout is a bug, which is why this runs after the background content
	// rather than before it: drawing black first and a test pattern second
	// would put the grid straight back into the bins.
	//
	// One stroke width has to serve both axes, and the axes do not scale
	// equally (3 mm is 3.78 px across, 3.54 px down). Taking X makes the
	// outline a touch heavy vertically - invisible at this width, and the
	// alternative is stroking each edge separately for no real gain.
	const float strokePx = mmToPxX(kBinOutlineMM);

	for(int i = 0; i < BIN_COUNT; i++){
		// grown by CUTOUT_MARGIN_MM, so the black covers the real cutout even
		// with the homography's residual error and a saw cut that wandered
		const BinRect fill = binFillRectMM(BINS[i]);

		// mmToPx* are pure scales with no offset, so they convert spans as
		// well as positions. The nudge shifts the origin only - the cutouts do
		// not change size just because the pattern sits a few mm off.
		const float x = mmToPxX(fill.xMM + offsetXMM);
		const float y = mmToPxY(fill.yMM + offsetYMM);
		const float w = mmToPxX(fill.wMM);
		const float h = mmToPxY(fill.hMM);

		ofFill();
		ofSetColor(0);
		ofDrawRectangle(x, y, w, h);

		// ofPath, not ofSetLineWidth - drivers cap the latter at 1 px
		ofPath outline;
		outline.setFilled(false);
		outline.setStrokeWidth(strokePx);
		outline.setColor(ofColor::white);
		outline.rectangle(x, y, w, h);
		outline.draw();
	}

	ofSetColor(255);
}

//--------------------------------------------------------------
void ofApp::drawHands(){
	// Nothing at all until the tracker has been heard from. A table that has
	// never had a tracker attached stays black rather than showing a stale dot.
	if(!everReceived){
		return;
	}

	uint64_t now = ofGetElapsedTimeMillis();

	for(auto it = hands.begin(); it != hands.end(); ){
		if(now - it->second.lastSeenMS > kHandTimeoutMS){
			it = hands.erase(it);  // gone: tracker lost it, or stopped
			continue;
		}

		// abs() because the id comes off the wire and C++ modulo of a negative
		// is negative, which would index off the front of the palette
		size_t colour = (size_t)std::abs(it->first) % kHandColourCount;
		ofSetColor(kHandColours[colour]);
		ofDrawCircle(it->second.posPx.x, it->second.posPx.y, kHandRadiusPx);
		++it;
	}

	ofSetColor(255);
}

//--------------------------------------------------------------
void ofApp::update(){
	receiveOsc();
}

//--------------------------------------------------------------
void ofApp::draw(){
	// the window is only shown just before the first draw, so read back the
	// real geometry here rather than inferring it from setup()
	uint64_t frame = ofGetFrameNum();
	if(frame == 0 || frame == 30){
		logWindowState("frame " + ofToString(frame));
	}

	ofBackground(0);

	// calibration pattern is otherwise alone on the screen - the camera must see
	// the dots and little else, so no hand dot here either
	if(showCalibration){
		drawBinCutouts();

		ofSetColor(255);
		for(size_t i = 0; i < calibDotsMM.size(); i++){
			const glm::vec2 & mm = calibDotsMM[i];
			float r = (i == kMarkerDotIndex) ? kMarkerDotRadiusPx : kDotRadiusPx;
			ofDrawCircle(roundf(mmToPxX(mm.x)), roundf(mmToPxY(mm.y)), r);
		}
		return;
	}

	float w = ofGetWidth();
	float h = ofGetHeight();

	// 100px grid, dark grey
	ofSetColor(60, 60, 60);
	for(float x = 0; x <= w; x += 100){
		ofDrawLine(x, 0, x, h);
	}
	for(float y = 0; y <= h; y += 100){
		ofDrawLine(0, y, w, y);
	}

	ofSetColor(255);

	// diagonals corner to corner
	ofDrawLine(0, 0, w, h);
	ofDrawLine(w, 0, 0, h);

	// 50px crosshair at exact centre
	float cx = w / 2;
	float cy = h / 2;
	ofDrawLine(cx - 25, cy, cx + 25, cy);
	ofDrawLine(cx, cy - 25, cx, cy + 25);

	// filled 20px circles at all 4 corners
	ofDrawCircle(0, 0, 10);
	ofDrawCircle(w, 0, 10);
	ofDrawCircle(0, h, 10);
	ofDrawCircle(w, h, 10);

	// 2px white rectangle inset 1px from the very edge
	ofPath border;
	border.setFilled(false);
	border.setStrokeWidth(2);
	border.setColor(ofColor::white);
	border.rectangle(1, 1, w - 2, h - 2);
	border.draw();

	// black over the cutouts last of the table-fixed layers, so the grid and
	// diagonals above cannot put light into a bin
	drawBinCutouts();

	// hands on top of the bins, under nothing yet
	drawHands();

	// top-left readout
	ofSetColor(255);
	std::stringstream ss;
	ss << (int)w << " x " << (int)h << "\n" << ofGetFrameRate();
	ss << "\nhands " << hands.size();
	if(!everReceived){
		ss << "  (no tracker yet)";
	}
	ss << "\nbin offset " << ofToString(offsetXMM, 1) << ", "
	   << ofToString(offsetYMM, 1) << " mm  (arrows 1mm, shift 5mm, s saves)";
	ofDrawBitmapString(ss.str(), 10, 20);
}

//--------------------------------------------------------------
void ofApp::keyPressed(int key){
	// Shift is read live rather than from the key code: an arrow key reports the
	// same code either way, so the modifier is the only thing distinguishing a
	// coarse nudge from a fine one.
	const float step = ofGetKeyPressed(OF_KEY_SHIFT) ? kNudgeFastStepMM : kNudgeStepMM;

	// Screen directions, not table directions - whoever is nudging is looking at
	// the projected rectangles. +y runs towards the diner, so up is negative.
	switch(key){
		case OF_KEY_LEFT:  nudgeOffset(-step, 0.0f); return;
		case OF_KEY_RIGHT: nudgeOffset( step, 0.0f); return;
		case OF_KEY_UP:    nudgeOffset( 0.0f, -step); return;
		case OF_KEY_DOWN:  nudgeOffset( 0.0f,  step); return;
	}

	if(key == 's' || key == 'S'){
		saveOffsets();
		return;
	}

	if(key == 'c' || key == 'C'){
		showCalibration = !showCalibration;
		ofLogNotice("ofApp") << "calibration pattern " << (showCalibration ? "on" : "off");
		if(showCalibration){
			logCalibrationDots();
		}
	}
}

//--------------------------------------------------------------
void ofApp::keyReleased(int key){

}

//--------------------------------------------------------------
void ofApp::mouseMoved(int x, int y ){

}

//--------------------------------------------------------------
void ofApp::mouseDragged(int x, int y, int button){

}

//--------------------------------------------------------------
void ofApp::mousePressed(int x, int y, int button){

}

//--------------------------------------------------------------
void ofApp::mouseReleased(int x, int y, int button){

}

//--------------------------------------------------------------
void ofApp::mouseEntered(int x, int y){

}

//--------------------------------------------------------------
void ofApp::mouseExited(int x, int y){

}

//--------------------------------------------------------------
void ofApp::windowResized(int w, int h){

}

//--------------------------------------------------------------
void ofApp::gotMessage(ofMessage msg){

}

//--------------------------------------------------------------
void ofApp::dragEvent(ofDragInfo dragInfo){ 

}
