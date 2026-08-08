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

	// A hovered bin's outline changes colour. Nothing else changes yet - no
	// fill, no name, no price - so the outline is carrying the entire signal
	// and has to read at a glance from standing height.
	//
	// Deliberately not the green of the alignment overlay below: that green
	// means "this is the line the arrow keys move", and the two must not be
	// confused by someone nudging with a hand over the table. Cyan is hand 0's
	// dot colour, which is the direction section 9 heads in anyway - the halo
	// takes the cursor's colour on hover.
	const ofColor kBinHoverColour(0, 200, 255);

	// Floor for the pass-over instrumentation below. A hand clipping the corner
	// of a bin rect on its way somewhere else banks a few tens of ms, and those
	// are not pass-overs - they are the edge of the rect being touched at all.
	// Logging them would bury the crossings that actually inform the threshold
	// under a much larger number of meaningless ones.
	const float kPassOverLogFloorMS = 100.0f;

	// Lives in bin/data/ alongside the other rig state.
	const char * kOffsetsFile = "bin_offsets.json";

	// Under bin/data/ so ofSaveScreen finds it without path games, but git
	// ignored - these are output to look at, not data the app loads.
	const char * kScreenshotDir = "screenshots";

	// Marks the line being moved. This is a setup overlay, not the diner-facing
	// UI, so it is outside the "colour is reserved for progress" rule in §9 -
	// and green stays clear of both hand-dot colours.
	const ofColor kSelectionColour(0, 255, 120);

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
	// Both are the record of THIS frame only, so they start empty every frame.
	// What survives between frames is `hands`, which is the render hold.
	freshHandsPx.clear();
	detectionFrame = false;

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

			// Recorded separately from `hands` because this is the only place
			// a position is known to be a live detection rather than a held
			// one. Ids are not carried: hover asks "is any hand in this bin",
			// and the ids are the tracker's per-frame detection order, which
			// two hands can swap between frames anyway.
			freshHandsPx.push_back(hand.posPx);
			detectionFrame = true;

			lastMessageMS = now;
			everReceived = true;
		}
		else if(m.getAddress() == "/hand/none"){
			// Deliberately not an instant clear. This is a liveness beat: it
			// says the tracker is alive and currently sees nothing. Existing
			// hands are left to time out on their own, so a single dropped
			// detection frame does not blink the dot.
			//
			// For hover it is the opposite: this is the tracker positively
			// reporting an empty table, which is exactly the evidence that no
			// bin is being hovered. freshHandsPx stays empty and every
			// accumulator resets.
			detectionFrame = true;

			lastMessageMS = now;
			everReceived = true;
		}
	}
}

//--------------------------------------------------------------
// Where each grid line starts before any correction: the edges of the
// margin-grown CAD rects. Column c owns vertical lines 2c (left) and 2c+1
// (right); row r owns horizontal lines 2r (far) and 2r+1 (near).
float ofApp::vLineMM(int i) const {
	const BinRect f = binFillRectMM(BINS[i / 2]);
	const float cad = (i % 2 == 0) ? f.xMM : f.xMM + f.wMM;
	return cad + offsetXMM + vLineDeltaMM[i];
}

float ofApp::hLineMM(int i) const {
	// BINS is row-major, so the first bin of row r is at index r * kCols.
	const BinRect f = binFillRectMM(BINS[(i / 2) * kCols]);
	const float cad = (i % 2 == 0) ? f.yMM : f.yMM + f.hMM;
	return cad + offsetYMM + hLineDeltaMM[i];
}

//--------------------------------------------------------------
std::string ofApp::selectionLabel() const {
	if(selection == 0){
		return "ALL";
	}
	if(selection <= kVLines){
		const int i = selection - 1;
		// 1-based for the person at the table, counting left to right
		return "V" + ofToString(i + 1) + " (col " + ofToString(i / 2 + 1)
			+ (i % 2 == 0 ? " left)" : " right)");
	}
	const int i = selection - kVLines - 1;
	return "H" + ofToString(i + 1) + " (row " + ofToString(i / 2 + 1)
		+ (i % 2 == 0 ? " far)" : " near)");
}

//--------------------------------------------------------------
void ofApp::cycleSelection(int dir){
	const int count = 1 + kVLines + kHLines;
	selection = (selection + dir + count) % count;

	ofLogNotice("ofApp") << "selected " << selectionLabel();
}

//--------------------------------------------------------------
void ofApp::nudgeSelection(float dxMM, float dyMM){
	if(selection == 0){
		offsetXMM += dxMM;
		offsetYMM += dyMM;
	}
	else if(selection <= kVLines){
		// A vertical line only has somewhere to go along x. Up and down are
		// ignored rather than remapped - moving a line off its own axis is
		// meaningless, and quietly doing something else would be worse.
		vLineDeltaMM[selection - 1] += dxMM;
	}
	else {
		hLineDeltaMM[selection - kVLines - 1] += dyMM;
	}

	// Logged as well as drawn: the readout is on the projector, which is across
	// the room from whoever is holding the arrow keys.
	ofLogNotice("ofApp") << selectionLabel() << " -> "
		<< (selection == 0
			? "(" + ofToString(offsetXMM, 1) + ", " + ofToString(offsetYMM, 1) + ") mm"
			: ofToString(selection <= kVLines
				? vLineMM(selection - 1)
				: hLineMM(selection - kVLines - 1), 1) + " mm")
		<< " - unsaved, press s to keep it";
}

//--------------------------------------------------------------
void ofApp::saveOffsets(){
	ofJson j;
	j["offsetXMM"] = offsetXMM;
	j["offsetYMM"] = offsetYMM;
	for(int i = 0; i < kVLines; i++){
		j["vLineDeltaMM"][i] = vLineDeltaMM[i];
	}
	for(int i = 0; i < kHLines; i++){
		j["hLineDeltaMM"][i] = hLineDeltaMM[i];
	}

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
void ofApp::savePendingScreenshot(){
	// No-op unless a key asked for one, so draw() can call this at each of its
	// exits without having to know which one it is taking.
	if(!screenshotPending){
		return;
	}
	screenshotPending = false;

	const std::string dir = ofToDataPath(kScreenshotDir);
	if(!ofDirectory::doesDirectoryExist(dir)){
		ofDirectory::createDirectory(dir, true, true);
	}

	// Timestamped rather than a fixed name: the point of a screenshot here is
	// usually to compare before against after, which one file cannot do.
	const std::string name = std::string(kScreenshotDir) + "/hotpot-"
		+ ofGetTimestampString("%Y%m%d-%H%M%S") + ".png";
	ofSaveScreen(name);

	ofLogNotice("ofApp") << "screenshot saved to " << ofToDataPath(name);
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

	// Line deltas are optional: a file written before the grid existed holds
	// only the two offsets, and it still describes a valid alignment. Absent
	// means zero, which is exactly what that file meant.
	auto readDeltas = [&](const char * field, float * out, int count){
		if(!j.contains(field) || !j[field].is_array()){
			return;
		}
		if((int)j[field].size() != count){
			ofLogWarning("ofApp") << field << " has " << j[field].size()
				<< " entries, expected " << count << " - ignoring it";
			return;
		}
		for(int i = 0; i < count; i++){
			if(j[field][i].is_number()){
				out[i] = j[field][i].get<float>();
			}
		}
	};
	readDeltas("vLineDeltaMM", vLineDeltaMM, kVLines);
	readDeltas("hLineDeltaMM", hLineDeltaMM, kHLines);

	ofLogNotice("ofApp") << "loaded bin offset (" << ofToString(offsetXMM, 1) << ", "
		<< ofToString(offsetYMM, 1) << ") mm from " << path;
}

//--------------------------------------------------------------
// Bin i as it actually lands on the plywood: the grid cell bounded by its
// column's two vertical lines and its row's two horizontal ones, so it carries
// the offsets from bin_offsets.json as well as the CAD chain.
//
// Every consumer goes through here. The black fill is drawn from it and the
// hover hit test is done against it, which is the only way those two can be
// guaranteed to be the same rectangle. Hit testing raw BINS[] instead would
// test the drawing while the black sits on the as-built cutouts, and CLAUDE.md
// section 17 puts those up to ~5 mm apart per edge on top of a 4 mm global
// offset - enough to hover a bin whose black the hand is not over.
ofRectangle ofApp::binRectPx(int i) const {
	// BINS is row-major with kCols per row, so bin i is at row i / kCols,
	// column i % kCols - the same walk drawBinCutouts makes.
	const int r = i / kCols;
	const int c = i % kCols;

	const float x = mmToPxX(vLineMM(c * 2));
	const float y = mmToPxY(hLineMM(r * 2));
	return ofRectangle(x, y,
		mmToPxX(vLineMM(c * 2 + 1)) - x,
		mmToPxY(hLineMM(r * 2 + 1)) - y);
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

	// Each box is a grid cell, bounded by its column's two vertical lines and
	// its row's two horizontal ones - so a moved line resizes every box that
	// shares it, which is the whole point of the line model.
	for(int i = 0; i < BIN_COUNT; i++){
		const ofRectangle box = binRectPx(i);

		ofFill();
		ofSetColor(0);
		ofDrawRectangle(box);

		// Only the colour changes on hover. Width stays put: a thicker stroke
		// would grow inwards over the cutout and put light into the food,
		// which section 8 rules out no matter what the UI wants to say.
		const bool hovered = (binHover[i] == HoverState::HOVERED);

		// ofPath, not ofSetLineWidth - drivers cap the latter at 1 px
		ofPath outline;
		outline.setFilled(false);
		outline.setStrokeWidth(strokePx);
		outline.setColor(hovered ? kBinHoverColour : ofColor::white);
		outline.rectangle(box);
		outline.draw();
	}

	drawSelectionHighlight();

	ofSetColor(255);
}

//--------------------------------------------------------------
void ofApp::drawSelectionHighlight(){
	// Only ever redraws an edge the white outline already occupies, never a
	// full-width rule across the table: a line drawn through the other cells
	// would put light straight into their cutouts.
	if(selection == 0){
		return;
	}

	const float strokePx = mmToPxX(kBinOutlineMM) * 2.0f;

	ofPath hi;
	hi.setFilled(false);
	hi.setStrokeWidth(strokePx);
	hi.setColor(kSelectionColour);

	if(selection <= kVLines){
		const int i = selection - 1;
		const float x = mmToPxX(vLineMM(i));
		// the same edge on both boxes in this column
		for(int r = 0; r < kRows; r++){
			const float y0 = mmToPxY(hLineMM(r * 2));
			const float y1 = mmToPxY(hLineMM(r * 2 + 1));
			hi.moveTo(x, y0);
			hi.lineTo(x, y1);
		}
	}
	else {
		const int i = selection - kVLines - 1;
		const float y = mmToPxY(hLineMM(i));
		// the same edge on all four boxes in this row
		for(int c = 0; c < kCols; c++){
			const float x0 = mmToPxX(vLineMM(c * 2));
			const float x1 = mmToPxX(vLineMM(c * 2 + 1));
			hi.moveTo(x0, y);
			hi.lineTo(x1, y);
		}
	}

	hi.draw();
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
// Steps every bin's dwell accumulator once per frame.
//
// THE ACCUMULATOR ADVANCES ONLY ON A REAL DETECTION FRAME. There are two
// timers in this app and they must not be crossed:
//
//   - the render hold (kHandTimeoutMS, Hand::lastSeenMS) keeps the dot painted
//     through a dropout so it does not blink. It is a display convenience.
//   - this accumulator measures how long a hand was actually seen inside one
//     bin. It is the input to a decision about what the diner wants.
//
// Feeding the first into the second would let a hand that stopped being
// detected go on earning hover from a frozen position - the dot sits there
// looking correct while the table decides something the hand is not doing.
// So this reads freshHandsPx, never `hands`.
void ofApp::updateHover(){
	if(!detectionFrame){
		// The tracker said nothing this frame. That is a dropout, not an empty
		// table, and the two are not the same claim: nothing advances, and
		// nothing resets either. The accumulators simply hold where they are
		// until the tracker speaks again.
		return;
	}

	const uint64_t now = ofGetElapsedTimeMillis();

	// Real elapsed wall time since the previous detection, not ofGetLastFrameTime().
	// The app draws at 60 fps and the tracker runs at 30, so an app frame is
	// the wrong unit twice over - it counts frames with no detection in them,
	// and it under-counts the ones that do. Dwell is a wall-clock claim about a
	// hand, so it is measured against the wall clock.
	float dtMS = (lastDetectionMS == 0) ? 0.0f : (float)(now - lastDetectionMS);

	// A gap longer than the render hold means the hand left the table, not
	// that a frame or two was dropped - by then drawHands has already erased
	// the dot. None of that gap is credited, and the bins are forced to reset
	// below so a hand returning to the same bin starts from zero rather than
	// inheriting the dwell it had built up before it vanished.
	const bool handWasGone = (dtMS > (float)kHandTimeoutMS);
	if(handWasGone){
		dtMS = 0.0f;
	}
	lastDetectionMS = now;

	for(int i = 0; i < BIN_COUNT; i++){
		// The same rect the black fill is drawn from, offsets and all.
		const ofRectangle box = binRectPx(i);

		bool inside = false;
		if(!handWasGone){
			for(const glm::vec2 & p : freshHandsPx){
				if(box.inside(p.x, p.y)){
					inside = true;
					break;
				}
			}
		}

		if(!inside){
			// Read before the reset - the accumulated time is the whole point
			// of the instrumentation below, and zeroing first would throw it
			// away.
			const float dwelledMS = binDwellMS[i];

			// Reset, not decay. A hand that left a bin has not partly chosen
			// it, and a decay would let a hand oscillating over two bins earn
			// both. Pass-over is rejected by never letting it bank anything.
			binDwellMS[i] = 0.0f;

			if(binHover[i] == HoverState::HOVERED){
				ofLogNotice("hover") << "bin " << i << " HOVERED -> IDLE after "
					<< (now - binHoveredSinceMS[i]) << " ms held";
			}
			else if(binHover[i] == HoverState::DWELLING
				&& dwelledMS >= kPassOverLogFloorMS){
				// INSTRUMENTATION, NOT DIAGNOSTICS. This exists to set
				// HOVER_DWELL_MS from measurement instead of the guess it
				// currently is, and it should come out once that number is
				// settled.
				//
				// A rejected pass-over is otherwise completely silent - it
				// writes nothing, because nothing happened. That makes a clean
				// log the expected shape of success AND the expected shape of a
				// hover that never fires, which are not the same thing. This is
				// the only line that reports the durations the threshold has to
				// sit above: cross the bins at natural reaching speed, take the
				// longest number this prints, and leave headroom over it.
				ofLogNotice("hover") << "bin " << i << " DWELLING -> IDLE after "
					<< ofToString(dwelledMS, 0) << " ms (pass-over, no hover)";
			}
			binHover[i] = HoverState::IDLE;
			continue;
		}

		binDwellMS[i] += dtMS;

		if(binHover[i] != HoverState::HOVERED){
			if(binDwellMS[i] >= HOVER_DWELL_MS){
				binHover[i] = HoverState::HOVERED;
				binHoveredSinceMS[i] = now;

				// The actual dwell, not HOVER_DWELL_MS: the overshoot past the
				// threshold is one tracker frame, and seeing it is how the
				// tracker's real cadence shows up in the log.
				ofLogNotice("hover") << "bin " << i << " IDLE -> HOVERED after "
					<< ofToString(binDwellMS[i], 0) << " ms dwell";
			}
			else {
				binHover[i] = HoverState::DWELLING;
			}
		}
	}
}

//--------------------------------------------------------------
void ofApp::update(){
	receiveOsc();
	updateHover();
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
		savePendingScreenshot();
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
	   << ofToString(offsetYMM, 1) << " mm";
	ss << "\ntarget " << selectionLabel();
	if(selection > 0){
		const bool vertical = selection <= kVLines;
		ss << "  at " << ofToString(vertical ? vLineMM(selection - 1)
		                                     : hLineMM(selection - kVLines - 1), 1)
		   << " mm  (moves " << (vertical ? "left/right)" : "up/down)");
	}
	ss << "\n[ ] selects line, 0 selects all, arrows 1mm, shift 5mm, s saves";

	// Box sizes follow from where the lines sit, so show what they currently
	// are - the number to compare against a tape measure on the plywood.
	ss << "\nbox " << ofToString(vLineMM(1) - vLineMM(0), 1) << " x "
	   << ofToString(hLineMM(1) - hLineMM(0), 1) << " mm (col1/far)";
	ofDrawBitmapString(ss.str(), 10, 20);

	// Last thing in the frame, so the capture includes everything above.
	savePendingScreenshot();
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
		case OF_KEY_LEFT:  nudgeSelection(-step, 0.0f); return;
		case OF_KEY_RIGHT: nudgeSelection( step, 0.0f); return;
		case OF_KEY_UP:    nudgeSelection( 0.0f, -step); return;
		case OF_KEY_DOWN:  nudgeSelection( 0.0f,  step); return;
	}

	// Bracket keys rather than tab: plain printable keys, and tab is close
	// enough to the arrows to be hit by accident while nudging.
	if(key == ']'){
		cycleSelection(1);
		return;
	}
	if(key == '['){
		cycleSelection(-1);
		return;
	}
	if(key == '0'){
		selection = 0;
		ofLogNotice("ofApp") << "selected " << selectionLabel();
		return;
	}

	if(key == 's' || key == 'S'){
		saveOffsets();
		return;
	}

	// p, not s - s saves the alignment, and losing a dialled-in alignment to a
	// mistyped screenshot would be a bad trade.
	if(key == 'p' || key == 'P'){
		screenshotPending = true;
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
