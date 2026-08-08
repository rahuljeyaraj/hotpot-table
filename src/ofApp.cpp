#include "ofApp.h"
#include "TableGeometry.h"

#include <algorithm>

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

	// --- outline colours, on the white field ---------------------------------
	// The three outline states below are EQUILUMINANT on white, deliberately.
	// Their WCAG relative luminances are 0.102, 0.123 and 0.123, so all three
	// sit near a 6:1 contrast ratio against the field and none of them is the
	// "bright" or the "dim" one. What changes between them is hue and
	// saturation only.
	//
	// That is section 9's rule taken literally rather than approximately. If the
	// states differed in brightness, the thing telling them apart would be the
	// one channel the projector and the room are already fighting over - which
	// is exactly how the dim cyan below failed on the rig. Equal luminance means
	// the distinction cannot be washed out without washing out all three, i.e.
	// without the failure being obvious rather than silent.
	//
	// Grey, not white: this is a dark line on a light field now. Idle is the
	// hueless member of the set, so grey to red reads as colour arriving on an
	// unchanged line, and red to green as that colour changing.
	const ofColor kBinIdleColour(98, 98, 98);

	// Red is the instant one: the outline turns the moment a hand is inside,
	// before any dwell exists. Section 9 allows that precisely because it
	// commits to nothing - the load cell confirms the actual pick, so a halo
	// that lights on a hand passing through cannot have been wrong about
	// anything.
	//
	// MEASURED ON THE RIG, NOT A PREFERENCE: this started as a dim cyan and was
	// rejected at the table for being very difficult to see. That lesson is
	// about brightness carrying the meaning, and it survives the inversion
	// unchanged - only its direction flips. On black the failure was a
	// near-white hue at low value; on white it would be a light hue at full
	// value, which is why the green below had to come down from 255.
	const ofColor kBinEnterColour(200, 0, 0);

	// Green is the earned one, and it does not sit next to the red - it REPLACES
	// it, running along the same line. That makes the leftover red the work
	// still to do, so the same stroke reports progress twice over: how much is
	// green, and how much is still not. A progress colour drawn on unchanged
	// background would only say the first.
	//
	// Red to green also carries the meaning for free, with no legend to learn.
	//
	// 115, not 255. Green carries 0.72 of luminance against red's 0.21, so a
	// full-value green on white is very nearly invisible - about 1.4:1, worse
	// than the cyan that was already rejected once. 115 is the value that puts
	// it at the same luminance as the red it replaces, so the two differ purely
	// in hue and the walk does not appear to brighten as it completes.
	//
	// This is close to the alignment overlay's green further down, which is a
	// known and accepted collision: that one only ever appears on the single
	// line the arrow keys are moving, at double width, during setup - and
	// nobody is dwelling on bins while nudging the grid.
	const ofColor kBinProgressColour(0, 115, 0);

	// --- the white field -----------------------------------------------------
	// Percentages of full projector output, brightest first so index 0 is the
	// default and the key dims as it cycles.
	//
	// This exists because the field is an ILLUMINANT, not a background. Section
	// 8's black rects assume a lit room and keep projector light off the food;
	// in a dark room there is no ambient to fall back on and the same rects
	// leave the food unlit, so the field becomes the only light the classifier
	// gets. That makes its level a rig parameter to be swept against the camera
	// like an exposure, not a look to be chosen once in code.
	const int kFieldLevels[] = { 100, 75, 50, 25, 0 };
	const int kFieldLevelCount = sizeof(kFieldLevels) / sizeof(kFieldLevels[0]);

	// Floor for the pass-over instrumentation below. A hand clipping the corner
	// of a bin rect on its way somewhere else banks a few tens of ms, and those
	// are not pass-overs - they are the edge of the rect being touched at all.
	// Logging them would bury the crossings that actually inform the threshold
	// under a much larger number of meaningless ones.
	const float kPassOverLogFloorMS = 100.0f;

	// Lives in bin/data/ alongside the other rig state.
	const char * kOffsetsFile = "bin_offsets.json";

	// --- ingredient labels ---------------------------------------------------
	const char * kIngredientsFile = "ingredients.json";

	// DejaVu Sans Bold, committed to bin/data/fonts/ rather than pulled from a
	// system path. The reComputer is Linux and the dev machine is Windows, and a
	// font that resolves on one and not the other fails as a blank table.
	//
	// Bold, not regular, and that is not a style preference. This is white text
	// on white plywood lit by a projector in a room that section 21 REQUIRES to
	// stay lit - so the ambient light the classifier needs is also the light
	// washing out the projected UI. Contrast comes from stroke width, which is
	// the one thing a bold face has more of. Same lesson as the dim-cyan bin
	// outline that was rejected at the table further up.
	const char * kFontFile = "fonts/DejaVuSans-Bold.ttf";

	// Font sizes as PHYSICAL heights on the plywood, converted to pixels at
	// load. Stated in mm because that is the dimension legibility depends on;
	// the pixel number is a consequence of the projector, not a choice.
	//
	// Sized for the far row, which is the worst case: a diner standing at the
	// near edge is roughly 1.4 m of slant range from the back label strip
	// (about 1.2 m across the table, about 0.85 m of eye height above it).
	// Signage practice puts the legibility threshold near 1/200 of viewing
	// distance and comfortable reading near 1/100. Nothing here is good
	// conditions - projected light competing with the room lighting section 21
	// mandates - so this takes the comfortable ratio: 1400 / 100 = 14 mm of cap
	// height. DejaVu's cap height is 0.73 em, so 14 / 0.73 = 19 mm of em, taken
	// up to 22 mm for headroom, since the back strip has 175 mm to spend and
	// nothing else wants it.
	const float kNameEmMM = 22.0f;

	// The price is the number the diner is actually here to read, so it drops
	// only enough to establish which line is which - 17/22 is a clear
	// hierarchy while still clearing the 1/100 comfortable ratio (11 mm of em,
	// 12.4 mm of cap, against the 14 mm the far row asks for at 1/100 and the
	// 7 mm it asks for at the 1/200 threshold).
	const float kPriceEmMM = 17.0f;

	// Gap between the name's ink and the price's ink.
	const float kLabelLineGapMM = 4.0f;

	// Clearance between the edge of a bin's black rect and the nearest label
	// ink. The black already overshoots the physical hole by CUTOUT_MARGIN_MM,
	// so 10 mm here leaves the text about 20 mm clear of the actual opening -
	// comfortably past both the 1 mm nudge step the alignment was dialled in
	// with and the few mm of residual it could still be carrying.
	//
	// Not reusing CUTOUT_MARGIN_MM itself: that one is a projector-spill
	// margin and this one is a typographic one, and tying them together would
	// mean a future change to either silently moving the other.
	const float kLabelClearanceMM = 10.0f;

	// Under bin/data/ so ofSaveScreen finds it without path games, but git
	// ignored - these are output to look at, not data the app loads.
	const char * kScreenshotDir = "screenshots";

	// Marks the line being moved. This is a setup overlay, not the diner-facing
	// UI, so it is outside the "colour is reserved for progress" rule in §9 -
	// and green stays clear of both hand-dot colours. Brought down from value
	// 255 for the same reason the progress green was: a light green does not
	// survive a white field.
	const ofColor kSelectionColour(0, 150, 70);

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
	//
	// Both dropped in value along with everything else on the white field. The
	// old 200/255 cyan and 255 amber were chosen against black and land at
	// about 2:1 on white, which is a filled circle you have to look for. Hues
	// are unchanged, so which id is which reads the same as it did.
	const ofColor kHandColours[] = {
		ofColor(0, 110, 160),   // id 0, teal
		ofColor(200, 90, 0),    // id 1, amber
	};
	const size_t kHandColourCount =
		sizeof(kHandColours) / sizeof(kHandColours[0]);

	// The bin outline itself, walked clockwise from the top centre and cut off
	// at `frac` of its total length. This is the progress indicator - there is
	// no ring, arc or bar anywhere, because everything inside the rect is the
	// cutout and has to stay black (section 8). The only geometry available to
	// draw on is the 3 mm line already there, so progress runs along it.
	//
	// Top centre rather than a corner: it is the one point on a rectangle that
	// reads the same from every side of the table, and it puts the closing gap
	// in the middle of the far edge, where a bin nearly full is obvious at a
	// glance instead of hidden in a corner.
	ofPolyline binOutlineProgress(const ofRectangle & r, float frac){
		const float w = r.getWidth();
		const float h = r.getHeight();
		const float midX = r.x + w * 0.5f;

		// clockwise from top centre, round the four corners, back to it
		const glm::vec2 stops[] = {
			{ midX,    r.y     },
			{ r.x + w, r.y     },
			{ r.x + w, r.y + h },
			{ r.x,     r.y + h },
			{ r.x,     r.y     },
			{ midX,    r.y     },
		};
		const size_t stopCount = sizeof(stops) / sizeof(stops[0]);

		ofPolyline line;
		line.addVertex(stops[0].x, stops[0].y);

		// Length still to lay down, in pixels. Walk the segments spending it,
		// and stop part way along whichever one runs out.
		float remaining = ofClamp(frac, 0.0f, 1.0f) * (2.0f * w + 2.0f * h);

		for(size_t s = 1; s < stopCount; s++){
			const glm::vec2 a = stops[s - 1];
			const glm::vec2 b = stops[s];
			const float len = glm::distance(a, b);

			// A zero-length side would divide by zero below. Only reachable
			// with a collapsed rect, which means the grid lines have been
			// nudged past each other rather than anything wrong here.
			if(len <= 0.0f){
				continue;
			}

			if(remaining >= len){
				line.addVertex(b.x, b.y);
				remaining -= len;
				continue;
			}

			if(remaining > 0.0f){
				const glm::vec2 p = a + (b - a) * (remaining / len);
				line.addVertex(p.x, p.y);
			}
			break;
		}

		return line;
	}
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

	loadIngredients();

	// Loaded ONCE, at the size they are drawn at. mmToPxY rather than a literal
	// so the sizes stay the physical heights argued for at the top of this file
	// if the projector ever changes - but still resolved here, before any
	// drawing, so nothing is ever scaled up at draw time (section 7).
	const int nameSizePx = (int)roundf(mmToPxY(kNameEmMM));
	const int priceSizePx = (int)roundf(mmToPxY(kPriceEmMM));

	fontsLoaded = nameFont.load(kFontFile, nameSizePx)
		&& priceFont.load(kFontFile, priceSizePx);

	if(fontsLoaded){
		ofLogNotice("ofApp") << "label fonts: " << kFontFile
			<< " at " << nameSizePx << " px name (" << ofToString(kNameEmMM, 1)
			<< " mm), " << priceSizePx << " px price ("
			<< ofToString(kPriceEmMM, 1) << " mm)";
	}
	else {
		ofLogError("ofApp") << "could not load " << ofToDataPath(kFontFile)
			<< " - bin labels will not be drawn";
	}

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
// Steps the field down one notch, wrapping 0% back to 100%.
//
// Down rather than up because the key is a dimmer: the app boots at full output
// and every press takes light off the table, which is the direction anyone at
// the rig is actually working in. Wrapping means one key can reach every level
// without a second one for the other direction, and there are only five.
void ofApp::cycleFieldLevel(){
	fieldLevel = (fieldLevel + 1) % kFieldLevelCount;

	// To the console as well as the readout: whoever is pressing this is at the
	// keyboard looking at the camera's view of the table, not at the projected
	// readout across the room - the same reason the nudge keys log.
	ofLogNotice("ofApp") << "field brightness " << kFieldLevels[fieldLevel] << "%";
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
// Reads bin/data/ingredients.json into `ingredients`, or leaves it empty.
//
// EVERY failure path leaves it empty and logs an error. There is no partial
// load and no default name, on purpose: the file is the single source of truth
// for what is in the bins, and a C++ fallback would be a second one. A second
// source only ever shows up when the first is broken, which is exactly when
// nobody is watching for it - the table would look fine and read wrong. A blank
// strip is a visible fault; "Mushroom" over a bin of prawns is not.
void ofApp::loadIngredients(){
	ingredients.clear();

	const std::string path = ofToDataPath(kIngredientsFile);

	if(!ofFile::doesFileExist(path)){
		ofLogError("ofApp") << "no " << path
			<< " - bin labels will not be drawn. This file is the only place"
			<< " ingredient names and prices live.";
		return;
	}

	ofJson j = ofLoadJson(path);

	if(!j.is_array()){
		ofLogError("ofApp") << path << " is not a JSON array of bin entries"
			<< " - bin labels will not be drawn";
		return;
	}
	if((int)j.size() < BIN_COUNT){
		ofLogError("ofApp") << path << " has " << j.size() << " entries, need "
			<< BIN_COUNT << " (one per bin) - bin labels will not be drawn";
		return;
	}

	// Filled by the entry's own "bin" field rather than by array position. The
	// field is in the file precisely so the order in it does not matter, and
	// honouring position instead would make the field decorative - and wrong
	// the first time someone sorts the file alphabetically.
	std::vector<Ingredient> loaded(BIN_COUNT);
	std::vector<bool> seen(BIN_COUNT, false);

	for(size_t e = 0; e < j.size(); e++){
		const ofJson & entry = j[e];

		if(!entry.is_object() || !entry.contains("bin") || !entry["bin"].is_number_integer()
			|| !entry.contains("name") || !entry["name"].is_string()
			|| !entry.contains("price_per_100g") || !entry["price_per_100g"].is_number()){
			ofLogError("ofApp") << path << " entry " << e
				<< " needs an integer bin, a string name and a numeric"
				<< " price_per_100g - bin labels will not be drawn";
			return;
		}

		const int bin = entry["bin"].get<int>();
		if(bin < 0 || bin >= BIN_COUNT){
			ofLogError("ofApp") << path << " entry " << e << " has bin " << bin
				<< ", outside 0.." << (BIN_COUNT - 1)
				<< " - bin labels will not be drawn";
			return;
		}
		if(seen[bin]){
			// Two entries claiming one bin has no right answer, and picking
			// either would be a guess about which price to charge.
			ofLogError("ofApp") << path << " has more than one entry for bin "
				<< bin << " - bin labels will not be drawn";
			return;
		}

		const std::string name = entry["name"].get<std::string>();
		if(name.empty()){
			ofLogError("ofApp") << path << " bin " << bin
				<< " has an empty name - bin labels will not be drawn";
			return;
		}

		loaded[bin].name = name;
		loaded[bin].pricePer100g = entry["price_per_100g"].get<float>();
		seen[bin] = true;
	}

	for(int i = 0; i < BIN_COUNT; i++){
		if(!seen[i]){
			ofLogError("ofApp") << path << " has no entry for bin " << i
				<< " - bin labels will not be drawn";
			return;
		}
	}

	ingredients = loaded;
	ofLogNotice("ofApp") << "loaded " << ingredients.size() << " ingredients from " << path;
}

//--------------------------------------------------------------
// Bin i as it actually lands on the plywood: the grid cell bounded by its
// column's two vertical lines and its row's two horizontal ones, so it carries
// the offsets from bin_offsets.json as well as the CAD chain.
//
// Every consumer goes through here: the outline, the label clearance and the
// hover hit test, which is the only way those can be guaranteed to be the same
// rectangle. Hit testing raw BINS[] instead would test the drawing while the
// outline sits on the as-built cutouts, and CLAUDE.md section 17 puts those up
// to ~5 mm apart per edge on top of a 4 mm global offset - enough to hover a
// bin whose outline the hand is not inside.
//
// Losing the black fill changed nothing here. bin_offsets.json still says where
// the openings are, and the outline and the labels are still placed against
// them; only the fill that used to sit inside them is gone.
ofRectangle ofApp::binRectPx(int i) const {
	// BINS is row-major with kCols per row, so bin i is at row i / kCols,
	// column i % kCols - the same walk drawBinOutlines makes.
	const int r = i / kCols;
	const int c = i % kCols;

	const float x = mmToPxX(vLineMM(c * 2));
	const float y = mmToPxY(hLineMM(r * 2));
	return ofRectangle(x, y,
		mmToPxX(vLineMM(c * 2 + 1)) - x,
		mmToPxY(hLineMM(r * 2 + 1)) - y);
}

//--------------------------------------------------------------
void ofApp::drawBinOutlines(){
	// THE BLACK FILL IS GONE ON PURPOSE. Section 8 of CLAUDE.md says the
	// projector must put near-zero light into the bins, and that is right for
	// the lit room section 21 requires - there, ambient light is what the
	// classifier sees by, and projector light on top of it is pure
	// contamination.
	//
	// The demo is shot in a DARK room, where that argument inverts. With no
	// ambient there is no "leave the food alone" option: a black rect over a
	// cutout is not neutral, it is the food in total darkness, which starves
	// the classifier rather than protecting it. The choice is projector light
	// or no light. So the whole table is a flat white field and the cutouts are
	// simply part of it - a constant, controlled illuminant, and one that lights
	// the back of the hand exactly when the hand is over a bin.
	//
	// What section 8 actually observed was a COLOURED, PATTERNED image washing
	// pink and white over the food. Flat white is neither, and the level is
	// swept from the table with the brightness key rather than fixed here.
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

		// Only ever the colour changes. Width stays put: the stroke straddles
		// the cutout edge, so a thicker one puts more coloured light down onto
		// the rim of the food, and the outline is a UI element rather than
		// something the classifier should have to see around.
		const bool handInside = (binHover[i] != HoverState::IDLE);
		const bool hovered = (binHover[i] == HoverState::HOVERED);

		// ofPath, not ofSetLineWidth - drivers cap the latter at 1 px
		ofPath outline;
		outline.setFilled(false);
		outline.setStrokeWidth(strokePx);
		outline.setColor(hovered ? kBinProgressColour
			: handInside ? kBinEnterColour
			: kBinIdleColour);
		outline.rectangle(box);
		outline.draw();

		// HOVERED is the full rect above, not a progress walk that happens to
		// have reached the end. Drawing it as geometry rather than as an
		// arrived-at animation is what makes it stay filled, and it avoids a
		// hairline seam at the top centre where a closed walk meets itself.
		if(!handInside || hovered){
			continue;
		}

		// Progress is read straight off the accumulator every frame, with no
		// animation state of its own. That is the whole reason a reset snaps:
		// there is no second value here that could drain, so the render cannot
		// imply a decay the accumulator does not have.
		const float frac = binDwellMS[i] / HOVER_DWELL_MS;
		if(frac <= 0.0f){
			continue;
		}

		const ofPolyline walk = binOutlineProgress(box, frac);
		const std::vector<glm::vec3> & pts = walk.getVertices();
		if(pts.size() < 2){
			continue;
		}

		// Same width, same line, drawn straight over the red - so the green is
		// the red being consumed rather than a second shape beside it.
		ofPath progress;
		progress.setFilled(false);
		progress.setStrokeWidth(strokePx);
		progress.setColor(kBinProgressColour);
		progress.moveTo(pts[0].x, pts[0].y);
		for(size_t p = 1; p < pts.size(); p++){
			progress.lineTo(pts[p].x, pts[p].y);
		}
		progress.draw();
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
// Ingredient name and price on the plywood strip beside each bin.
//
// WHICH STRIP, AND WHY IT IS SAFE
// The Y chain in TableGeometry.h is 177 + 255 + 50 + 255 + 177.4. The two 177 mm
// terms at the ends are the label strips: solid plywood between the far edge of
// the table and the far row, and between the near row and the diner's edge.
// Neither strip contains any part of any cutout - the far row starts at 177 and
// the near row ends at 737, and the offsets in bin_offsets.json move those by
// single millimetres, not by a strip's width.
//
// That is the whole reason the placement below only has to reason about Y. A
// label confined to its own strip cannot land in a cutout no matter how wide it
// is, because there is no cutout anywhere along that band. Horizontal overflow
// past a bin's own width spills into the 50 mm and 440 mm gaps between columns,
// which are plywood too.
//
// Keeping the text out of the cutouts still matters even though the black fill
// is gone and section 8 is deliberately reversed. The reason changed, not the
// rule: the field is now the classifier's only light source, and black glyphs
// laid across the food would be shadows cut into that illuminant - patterned
// darkness on the ingredients, which is the same contamination section 8
// observed, arriving from the other direction.
//
// ORIENTATION
// Drawn upright in screen space, with no rotation, and that already reads from
// the diner's side. Table +y runs from the far edge towards the diner, and
// mmToPxY maps it straight onto screen +y, so screen-down is diner-wards. A
// diner at the near edge is looking along -y, which puts table +x on their
// right and glyph tops away from them - the orientation of a page laid flat on
// the table in front of them.
void ofApp::drawBinLabels(){
	// Nothing loaded is not an error here - loadIngredients already said so,
	// once, with a reason. Repeating it 60 times a second would bury it.
	if(!fontsLoaded || (int)ingredients.size() != BIN_COUNT){
		return;
	}

	// ofxFlowTools leaves OF_BLENDMODE_ADD set (CLAUDE.md section 7). No fluid
	// yet, but text drawn under ADD washes out to nothing against the grid, and
	// the fix belongs with the text rather than with whatever set the mode.
	ofEnableAlphaBlending();

	// asc is positive and desc is negative, so asc - desc is the full ink
	// height of a line. Used rather than getLineHeight() because line height
	// carries the font's leading, which would put a gap between the two lines
	// that this code has not chosen and cannot see.
	const float nameLineH = nameFont.getAscenderHeight() - nameFont.getDescenderHeight();
	const float priceLineH = priceFont.getAscenderHeight() - priceFont.getDescenderHeight();
	const float lineGapPx = mmToPxY(kLabelLineGapMM);
	const float blockH = nameLineH + lineGapPx + priceLineH;

	const float clearancePx = mmToPxY(kLabelClearanceMM);

	// Black for both lines, at every field level - including 0%, where the text
	// therefore disappears along with the field. That is correct rather than a
	// gap: at 0% the projector is deliberately emitting nothing, and text that
	// stayed readable there would be light going onto the table during the one
	// setting whose whole purpose is to measure the table without any.
	//
	// Size is the only hierarchy, deliberately: section 9 reserves colour for
	// progress indication, and the dim-cyan bin outline further up is the
	// measured proof that separating UI by brightness is how it disappears.
	ofSetColor(0);

	for(int i = 0; i < BIN_COUNT; i++){
		const Ingredient & ing = ingredients[i];

		// The CORRECTED rect, offsets and all - the same one the black is drawn
		// from. Placing against raw BINS[] would clear the drawing while the
		// black sits somewhere else, and section 17 puts those up to ~5 mm per
		// edge apart on top of a global offset.
		const ofRectangle box = binRectPx(i);

		// Row 0 is the far row, so its strip is the one beyond it, towards the
		// far edge of the table. Row 1 is the near row and its strip is between
		// it and the diner. Either way the block hugs its own bin: bottom-
		// anchored above the far row, top-anchored below the near row, so the
		// label is always on the side of the strip nearest the bin it names.
		const bool farRow = (i / kCols) == 0;
		const float blockTop = farRow ? box.getMinY() - clearancePx - blockH
		                              : box.getMaxY() + clearancePx;
		const float blockBottom = blockTop + blockH;

		// Guaranteed clear of the cutout by construction above, and checked
		// anyway. This is the one rule in the app whose violation is not a
		// cosmetic bug: light landing in a bin contaminates the classifier's
		// input (section 8). Cheap enough to run every frame, and it has to,
		// because the nudge keys move `box` at runtime.
		const bool insideCutout = farRow ? (blockBottom > box.getMinY())
		                                 : (blockTop < box.getMaxY());
		const bool offTable = (blockTop < 0.0f) || (blockBottom > (float)PROJ_H_PX);

		if(insideCutout || offTable){
			if(!labelPlacementLogged){
				labelPlacementLogged = true;
				ofLogError("ofApp") << "bin " << i << " label does not fit its "
					<< (farRow ? "back" : "front") << " strip ("
					<< ofToString(blockTop, 1) << ".." << ofToString(blockBottom, 1)
					<< " px, bin " << ofToString(box.getMinY(), 1) << ".."
					<< ofToString(box.getMaxY(), 1) << " px)"
					<< " - not drawing it. Reduce the font size rather than the"
					<< " clearance; nothing may be drawn inside a cutout.";
			}
			continue;
		}

		// Two decimals with no currency symbol, because CLAUDE.md does not name
		// a currency and this is not the place to invent one. Add it to the
		// JSON when it is decided, not here.
		const std::string priceStr = ofToString(ing.pricePer100g, 2) + " / 100g";

		// Ink boxes, not advance widths: centring on the advance leaves the
		// string visibly off-centre for anything with side bearings, and these
		// two lines are stacked so any mismatch between them shows.
		const ofRectangle nameBox = nameFont.getStringBoundingBox(ing.name, 0.0f, 0.0f);
		const ofRectangle priceBox = priceFont.getStringBoundingBox(priceStr, 0.0f, 0.0f);

		const float cx = box.getCenter().x;

		// drawString takes a baseline, so step down from the block top by the
		// ascender to get there.
		nameFont.drawString(ing.name,
			cx - nameBox.getWidth() * 0.5f - nameBox.x,
			blockTop + nameFont.getAscenderHeight());

		priceFont.drawString(priceStr,
			cx - priceBox.getWidth() * 0.5f - priceBox.x,
			blockTop + nameLineH + lineGapPx + priceFont.getAscenderHeight());

		// Advisory only - a wide label overflows onto plywood, never into a
		// cutout, so it is untidy rather than unsafe. Worth saying once,
		// because two long names in the adjacent columns either side of the
		// 50 mm gap will run into each other.
		if(!labelPlacementLogged
			&& std::max(nameBox.getWidth(), priceBox.getWidth()) > box.getWidth()){
			labelPlacementLogged = true;
			ofLogWarning("ofApp") << "bin " << i << " label \"" << ing.name
				<< "\" is wider than its bin (" << ofToString(nameBox.getWidth(), 0)
				<< " px vs " << ofToString(box.getWidth(), 0)
				<< " px) - it overhangs onto the plywood between columns";
		}
	}
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
		// The same rect the outline is drawn from, offsets and all.
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

	// CALIBRATION IS NOT INVERTED, AND MUST NOT BE. Sections 15 and 16 are
	// explicit: the dots are solved for against a dark frame, because they have
	// to stay separable from a white table top, and solve_homography.py runs the
	// camera at a dark exposure to keep them that way. A white field here would
	// put the dots on a background as bright as they are and the solve would
	// find nothing. This branch keeps its black background and its white dots
	// whatever the rest of the app is doing, so it returns before the field
	// below is ever drawn.
	if(showCalibration){
		ofBackground(0);
		drawBinOutlines();

		ofSetColor(255);
		for(size_t i = 0; i < calibDotsMM.size(); i++){
			const glm::vec2 & mm = calibDotsMM[i];
			float r = (i == kMarkerDotIndex) ? kMarkerDotRadiusPx : kDotRadiusPx;
			ofDrawCircle(roundf(mmToPxX(mm.x)), roundf(mmToPxY(mm.y)), r);
		}
		savePendingScreenshot();
		return;
	}

	// The field. One flat grey covering the whole projected surface, cutouts
	// included - in a dark room this is the illuminant, not a backdrop.
	const int fieldGrey = (int)roundf(255.0f * kFieldLevels[fieldLevel] / 100.0f);
	ofBackground(fieldGrey);

	float w = ofGetWidth();
	float h = ofGetHeight();

	// Test pattern, inverted along with everything else so it stays a working
	// test pattern rather than white-on-white. Same geometry, same weights,
	// values mirrored: the grid was 60 on black and is 195 on white, the rest
	// was 255 and is now 0.
	//
	// KNOWN CONFLICT, LEFT IN DELIBERATELY: the diagonals and the grid cross
	// four of the eight cutouts, and dark lines over a cutout are exactly the
	// patterned shadow the flat field exists to avoid. The black rects used to
	// absorb this - they were drawn after the pattern for precisely that reason
	// - and nothing absorbs it now. This is scaffolding due to come out, so it
	// is not worth a clipping path; it is worth knowing about before judging a
	// classifier result taken with it on.
	ofSetColor(195);
	for(float x = 0; x <= w; x += 100){
		ofDrawLine(x, 0, x, h);
	}
	for(float y = 0; y <= h; y += 100){
		ofDrawLine(0, y, w, y);
	}

	ofSetColor(0);

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

	// 2px black rectangle inset 1px from the very edge
	ofPath border;
	border.setFilled(false);
	border.setStrokeWidth(2);
	border.setColor(ofColor::black);
	border.rectangle(1, 1, w - 2, h - 2);
	border.draw();

	// Outlines over the pattern, so a diagonal cannot cut across the one line
	// that reports hover state.
	drawBinOutlines();

	// UI text last of the table-fixed layers, per the layer order in section 7.
	// Before the hand dot rather than after it, so the dot - which is what
	// stage 1 is still being judged on - stays the topmost thing on the table.
	drawBinLabels();

	// hands on top of the bins, under nothing yet
	drawHands();

	// top-left readout, black on the field like the labels
	ofSetColor(0);
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
	ss << "\nfield " << kFieldLevels[fieldLevel] << "%";
	ss << "\n[ ] selects line, 0 selects all, arrows 1mm, shift 5mm, s saves, b dims";

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

	// b for brightness. Clear of c, s and p, and clear of the bracket and arrow
	// keys the alignment uses.
	if(key == 'b' || key == 'B'){
		cycleFieldLevel();
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
