#include "ofApp.h"
#include "TableGeometry.h"

namespace {
	// v3 doc §13.2, `of.white_floor` default. Hardcoded rather than read
	// from config/system.json, matching this repo's own established
	// pattern (core/main.py's CONTROL_PORT etc.) of not building a config
	// loader until something needs more than one key from it.
	const float kWhiteFloor = 0.45f;

	// doc §14.6's vocabulary: stage_size / sim_scale = simulation grid.
	// 4 is the doc's own dev-machine default; no adaptive controller yet
	// (§14.6 build item, not part of getting hand-driven fluid on screen).
	const int kFluidSimScale = 4;

	// doc §4.1 default; core/main.py's CONTROL_PORT.
	const std::string kCoreHost = "127.0.0.1";
	const int kCorePort = 8765;

	// doc §4.1's `cursor.of_port`. Hardcoded to the documented default for
	// the same reason kCorePort is — this app still has no config reader
	// (see kWhiteFloor's comment) and this is the one port oF listens on.
	const int kCursorPort = 8770;

	// skeletonbus.OF_PORT (RIG_FEEDBACK item 11 diagnostic — not in doc
	// §4.1, this transport isn't in the doc at all). Same hardcoded-
	// default reasoning as kCursorPort.
	const int kSkeletonPort = 8772;

	// RIG_FEEDBACK item 11 confirmed fixed on the rig, 2026-08-13 — kill
	// switch, same pattern as UiLayer.cpp's own kUseCoreRects (see
	// CLAUDE.md's M4n note on that one). `SkeletonLink` still listens
	// (tracker/main.py has simply stopped sending to it, its own side of
	// this same call) and `UiLayer::drawSkeleton` still exists —
	// flipping this back to `true` is the whole re-enable if the raw-
	// skeleton-vs-cursor comparison is ever needed again.
	const bool kDrawSkeleton = false;

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
	_fluid.setup(PROJ_W_PX, PROJ_H_PX, kFluidSimScale);

	// who="of" per doc §4.1's process names (health.py's PROCESSES tuple).
	// Runs its own thread from here on — see StateLink's class comment for
	// why setup() itself must never block.
	_link.setup(kCoreHost, kCorePort, "of");
	// The cursor link needs no thread and takes none — see CursorLink's
	// class comment on why it differs from StateLink here.
	_cursor.setup(kCursorPort);
	// RIG_FEEDBACK item 11 diagnostic — see SkeletonLink's class comment.
	_skeleton.setup(kSkeletonPort);
}

//--------------------------------------------------------------
void ofApp::update(){
	float dt = ofGetLastFrameTime();
	// Drained once per frame, before anything reads it. Drain-to-latest, so
	// a frame that arrives late is thrown away rather than replayed
	// (doc §4) — CursorLink::update() is the only place that rule lives on
	// this side.
	_cursor.update();
	// Driven by the real hand cursor(s), never the mouse (ofApp's mouse
	// callbacks are all empty, deliberately — v3 §7.1's deleted OSC hand
	// mock is not coming back as a fluid-testing shortcut). Every hand
	// injects, pointer and ambient alike (doc §14.4).
	_fluid.update(dt, _cursor.hands());
	// RIG_FEEDBACK item 11 diagnostic — same drain-to-latest discipline,
	// see SkeletonLink::update()'s own comment.
	_skeleton.update();
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
	// doc §13.2's FBO stack, step 1: fluid first, UI drawn on top of it.
	// I9 is untouched either way — the floor lift and light pass run on
	// the composite afterward, unconditionally (Stage::compositeAndWarp).
	_fluid.draw(0, 0, PROJ_W_PX, PROJ_H_PX);
	_ui.draw(hasState, state, _link.isConnected(), _link.secondsSinceLastState(),
		ofGetFrameRate(), _devOverlayVisible,
		_cursor.hands(), _cursor.pointer());
	// RIG_FEEDBACK item 11 diagnostic: confirmed fixed on the rig,
	// 2026-08-13 — see kDrawSkeleton's own comment.
	if(kDrawSkeleton){
		_ui.drawSkeleton(_skeleton.hands());
	}
	_stage.endContent();

	// 2026-08-12: the cursor is allowed to survive on top of a bin cutout
	// during serving mode — see Stage::compositeAndWarp's own comment on
	// `drawAboveLightPass` for why this does not weaken I9, and
	// UiLayer::draw's own cursor block for the other half of this: it
	// skips drawing the cursor itself under this exact condition, so
	// there is exactly one draw site per frame, never two. Checked on
	// `state.mode` alone, with no separate `hasState` guard — `state`
	// defaults to mode "serving" (StateLink::State's own default) when
	// there is no live link yet, and that default is exactly right here
	// too: it is what keeps this condition and UiLayer::draw's the same
	// single test, so the two can never disagree about which one of them
	// is responsible for this frame's cursor.
	const CursorLink::Hand * pointer = _cursor.pointer();
	std::function<void()> aboveLightPass = nullptr;
	if(state.mode == "serving" && pointer != nullptr){
		aboveLightPass = [this, &state, pointer](){
			_ui.drawCursorAboveLightPass(state, pointer);
		};
	}
	_stage.compositeAndWarp(kWhiteFloor, _ui.cutoutRectsPx(),
		mmToPxX(CUTOUT_CORNER_RADIUS_MM), false, aboveLightPass);

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
	_skeleton.close();
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
