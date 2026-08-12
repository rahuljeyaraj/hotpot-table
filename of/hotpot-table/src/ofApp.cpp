#include "ofApp.h"
#include "TableGeometry.h"

namespace {
	// v3 doc §13.2, `of.white_floor` default. Hardcoded rather than read
	// from config/system.json, matching this repo's own established
	// pattern (core/main.py's CONTROL_PORT etc.) of not building a config
	// loader until something needs more than one key from it.
	const float kWhiteFloor = 0.45f;

	// doc §4.1 default; core/main.py's CONTROL_PORT.
	const std::string kCoreHost = "127.0.0.1";
	const int kCorePort = 8765;

	// doc §4.1's `cursor.of_port`. Hardcoded to the documented default for
	// the same reason kCorePort is — this app still has no config reader
	// (see kWhiteFloor's comment) and this is the one port oF listens on.
	const int kCursorPort = 8770;

	// doc §4.5/§12: telemetry cadence. Not the 60Hz state rate — `stat` is
	// a developer/staff-view number, once a second is plenty.
	const float kStatInterval = 1.0f;

	const char * kScreenshotDir = "screenshots";
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
void ofApp::setup(){
	logWindowState("setup before fullscreen");
	ofSetFullscreen(true);
	logWindowState("setup after fullscreen");

	// Stage space is 1920x1080 (v3 §5.1) — the same PROJ_W_PX/PROJ_H_PX
	// TableGeometry.h's mm->px math is calibrated to. Warn rather than
	// rescale if the real framebuffer differs, for the same reason the
	// pre-rewrite app did: the homography (once M4 solves one) is anchored
	// to this resolution too, and quietly stretching to fit would hide a
	// mismatch that also invalidates that solve.
	if(ofGetWidth() != PROJ_W_PX || ofGetHeight() != PROJ_H_PX){
		ofLogWarning("ofApp") << "framebuffer is " << ofGetWidth() << "x" << ofGetHeight()
			<< " but stage space is " << PROJ_W_PX << "x" << PROJ_H_PX
			<< " - plates and the keystone quad will NOT line up with the table.";
	}

	ofSetCircleResolution(64);

	_stage.setup(PROJ_W_PX, PROJ_H_PX, "keystone.json");
	_ui.setup();

	// who="of" per doc §4.1's process names (health.py's PROCESSES tuple).
	// Runs its own thread from here on — see StateLink's class comment for
	// why setup() itself must never block.
	_link.setup(kCoreHost, kCorePort, "of");
	// The cursor link needs no thread and takes none — see CursorLink's
	// class comment on why it differs from StateLink here.
	_cursor.setup(kCursorPort);
}

//--------------------------------------------------------------
void ofApp::update(){
	float dt = ofGetLastFrameTime();
	// Drained once per frame, before anything reads it. Drain-to-latest, so
	// a frame that arrives late is thrown away rather than replayed
	// (doc §4) — CursorLink::update() is the only place that rule lives on
	// this side.
	_cursor.update();
	bool hasState = _link.hasState();
	if(hasState){
		StateLink::State state = _link.getState();
		_ui.update(dt, true, state);
	}

	_statTimer += dt;
	if(_statTimer >= kStatInterval){
		_statTimer = 0.0f;
		_link.sendStat(ofGetFrameRate(), _stage.keystoneFingerprint());
	}
}

//--------------------------------------------------------------
void ofApp::draw(){
	uint64_t frame = ofGetFrameNum();
	if(frame == 0 || frame == 30){
		// the window is only shown just before the first draw, so read back
		// the real geometry here rather than inferring it from setup()
		logWindowState("frame " + ofToString(frame));
	}

	bool hasState = _link.hasState();
	StateLink::State state;   // default-constructed (empty) when !hasState
	if(hasState){
		state = _link.getState();
	}

	_stage.beginContent();
	_ui.draw(hasState, state, _link.isConnected(), _link.secondsSinceLastState(),
		ofGetFrameRate(), _devOverlayVisible,
		_cursor.hands(), _cursor.pointer());
	_stage.endContent();

	_stage.compositeAndWarp(kWhiteFloor, _ui.cutoutRectsPx());

	if(_screenshotPending){
		_screenshotPending = false;
		const std::string dir = ofToDataPath(kScreenshotDir);
		if(!ofDirectory::doesDirectoryExist(dir)){
			ofDirectory::createDirectory(dir, true, true);
		}
		const std::string name = std::string(kScreenshotDir) + "/hotpot-"
			+ ofGetTimestampString("%Y%m%d-%H%M%S") + ".png";
		ofSaveScreen(name);
		ofLogNotice("ofApp") << "screenshot saved to " << ofToDataPath(name);
	}
}

//--------------------------------------------------------------
void ofApp::exit(){
	// The Ctrl-C lesson from CLAUDE.md's FIXED section, restated for this
	// thread: shutdown() must actually join, or the process can outlive
	// its own window with a link thread still trying to reconnect.
	_link.shutdown();
	_cursor.close();
}

//--------------------------------------------------------------
void ofApp::keyPressed(int key){
	// p for screenshot survives the rewrite — everything else keyboard-
	// driven (alignment nudge, calibration pattern, field-level cycling,
	// the weight mock) is deleted per v3 §7.1: alignment moved out with
	// the hit-testing it served (core's FSM, M5), the weight mock moved to
	// the staff view's developer panel (M1 build item 5, not yet wired to
	// oF — mock picks arrive over the same StateLink `state` bins already
	// draw), and the calibration dot pattern is deleted outright — automated
	// dot-projection calibration will never be used (see UiLayer.h history).
	// d added post-M1 acceptance: the fps/link/seq corner readout is a
	// debug tool, not diner-facing, so it defaults off and toggles here
	// rather than drawing unconditionally on the projected table.
	if(key == 'p' || key == 'P'){
		_screenshotPending = true;
	}
	if(key == 'd' || key == 'D'){
		_devOverlayVisible = !_devOverlayVisible;
	}
}

//--------------------------------------------------------------
void ofApp::keyReleased(int key){
}

//--------------------------------------------------------------
void ofApp::mouseMoved(int x, int y){
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
