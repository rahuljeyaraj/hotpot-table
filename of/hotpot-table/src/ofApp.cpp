#include "ofApp.h"
#include "TableGeometry.h"

namespace {
	// doc §14.6: stage_size / sim_scale gives the simulation grid. There is
	// no adaptive controller; this is a fixed divisor.
	const int kFluidSimScale = 4;

	// Kill switch, same pattern as kDrawSkeleton below. `_fluid.setup()`
	// always runs so the object stays in a valid state regardless of this
	// flag.
	const bool kFluidEnabled = true;

	// doc §4.1 default; core/main.py's CONTROL_PORT.
	const std::string kCoreHost = "127.0.0.1";
	const int kCorePort = 8765;

	// doc §4.1's `cursor.of_port`. Hardcoded to the documented default —
	// this app has no config reader — and this is the one port oF listens on.
	const int kCursorPort = 8770;

	// skeletonbus.OF_PORT. Not in doc §4.1; this diagnostic transport is not
	// in the doc at all. Same hardcoded-default reasoning as kCursorPort.
	const int kSkeletonPort = 8772;

	// Draws the raw MediaPipe skeleton over the table, for comparing it
	// against the smoothed cursor. Off by default. `SkeletonLink` still
	// listens and `UiLayer::drawSkeleton` still exists, so flipping this to
	// `true` is the whole re-enable; tracker/main.py has to be sending on
	// its side as well.
	const bool kDrawSkeleton = false;

	// doc §4.5/§12: telemetry cadence. Not the 60Hz state rate — `stat` is a
	// developer/staff-view number, once a second is plenty.
	const float kStatInterval = 1.0f;

	const char * kScreenshotDir = "screenshots";

	// Dev-only isolation switch for tuning the fluid with nothing else on
	// screen, in the same window and process run.py already launches and
	// with no dependency on core/tracker/camera being up or calibrated.
	// Tuning happens inside this binary's real Stage/FluidLayer code path,
	// so what is tuned here is what the table will show.
	//
	// True: skips UiLayer/StateLink/CursorLink and Stage's keystone warp,
	// white floor and light pass; drives FluidLayer from the mouse instead
	// of a tracked hand; draws only the fluid, full-window. Window and
	// fullscreen setup are untouched either way. Set false for the normal
	// hand-driven table.
	const bool kFluidDebugMouseOnly = false;

	// Bench test for VISUAL_LAYER.md §9 step 3: which blend mode reads
	// correctly on the light background. Same debug-isolation shape as
	// kFluidDebugMouseOnly — skips Stage/UiLayer/StateLink and draws
	// straight to the window, because it answers one question in isolation
	// rather than testing keystone alignment. Flip to true, rebuild, look at
	// the PROJECTED SURFACE, then flip back.
	const bool kBlendBenchTest = false;

	// §1/§3's table background, duplicated from Stage.cpp's
	// kTableBackground rather than exposed from Stage, since this bench
	// deliberately bypasses Stage altogether.
	const ofColor kBenchTableBackground(0xE8, 0xE6, 0xE1);

	// §2/§3's fire-core colour — the one colour both bench halves render, so
	// the comparison is about the blend mode, not a colour difference.
	const ofColor kBenchCoral(0xC7, 0x4A, 0x34);
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

	// Stage space is 1920x1080 (doc §5.1) — the same PROJ_W_PX/PROJ_H_PX
	// TableGeometry.h's mm->px math is calibrated to. Warn rather than
	// rescale if the real framebuffer differs: the homography is anchored to
	// this resolution too, and quietly stretching to fit would hide a
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
	_audio.setup();

	// who="of" per doc §4.1's process names (health.py's PROCESSES tuple).
	// Runs its own thread from here on — see StateLink's class comment for
	// why setup() itself must never block.
	_link.setup(kCoreHost, kCorePort, "of");
	// The cursor link needs no thread and takes none — see CursorLink's
	// class comment on why it differs from StateLink here.
	_cursor.setup(kCursorPort);
	// Raw-skeleton diagnostic — see SkeletonLink's class comment.
	_skeleton.setup(kSkeletonPort);
}

//--------------------------------------------------------------
void ofApp::update(){
	float dt = ofGetLastFrameTime();

	// The fluid gets a SMOOTHED frame time, not ofGetLastFrameTime()'s raw
	// per-frame delta, and this is not cosmetic. One occasional large raw
	// frame time — a GC pause, a scheduling hiccup — inflates ftFluidFlow's
	// internal timeStep (dt*speed*100), which multiplies straight into the
	// diffusion shader's strength (viscosityDen*timeStep, run 20 times every
	// frame). A single bad frame is enough to blur the just-injected density
	// down to nothing and leave the density buffer empty. The smoothed
	// formula has no single-frame spikes.
	//
	// This applies to BOTH branches below: the real hand-tracking path
	// passes the same dt into the same FluidLayer::update() and hits the
	// identical failure mode on an ordinary frame hitch. UI tweening and
	// `_statTimer` below intentionally keep the raw `dt` — the smoothing is
	// specifically for the fluid sim.
	float fluidDt = 1.0f / std::max(ofGetFrameRate(), 1.f);

	if(kFluidDebugMouseOnly){
		// Real FluidLayer, mouse standing in for a tracked hand, in the same
		// STAGE space (1920x1080) CursorLink would supply — so nothing in
		// FluidLayer needs to know the hand is not real. Scaled by
		// ofGetWidth/Height's ratio to PROJ_W_PX/PROJ_H_PX rather than
		// assuming ofGetMouseX/Y() is already in stage space, in case that
		// is untrue on a different display setup.
		std::vector<CursorLink::Hand> mouseHands;
		CursorLink::Hand h;
		h.id = 1;
		h.pointer = true;
		h.x = (float)ofGetMouseX() * ((float)PROJ_W_PX / (float)ofGetWidth());
		h.y = (float)ofGetMouseY() * ((float)PROJ_H_PX / (float)ofGetHeight());
		h.conf = 1.0f;
		mouseHands.push_back(h);
		if(kFluidEnabled){
			_fluid.update(fluidDt, mouseHands);
		}
		return;
	}

	// Drained once per frame, before anything reads it. Drain-to-latest, so
	// a frame that arrives late is thrown away rather than replayed
	// (doc §4) — CursorLink::update() is the only place that rule lives on
	// this side.
	_cursor.update();
	// Same drain-to-latest discipline — see SkeletonLink::update().
	_skeleton.update();
	bool hasState = _link.hasState();
	// Default-constructed when there is no link yet. StateLink::State's
	// default `phase` is "selecting" (StateLink.h), which is exactly the
	// first-page fluid this default should read as — same reasoning as
	// draw()'s own `state` local below.
	StateLink::State state;
	if(hasState){
		state = _link.getState();
		_ui.update(dt, true, state);
	}

	// doc §15: AudioBus's cue. Drained every frame regardless of `hasState`
	// — an `evt` line (doc §4.4) can arrive independently of the 60Hz
	// `state` stream, so this must not wait on that flag.
	for(const auto & evt : _link.drainEvents()){
		if(evt.kind != "sound"){
			// `burst`/`stream` (doc §4.4). FluidLayer takes hand/UI-driven
			// input instead — §14.4's event-driven injections are
			// UiLayer::fireEmitters() below, not this wire message — so
			// nothing consumes these today.
			continue;
		}
		std::string id = evt.data.value("id", "");
		if(id.empty()){
			continue;
		}
		float gain = evt.data.value("gain", 1.0f);
		// Always plays at the clip's own recorded speed: core sends no
		// dwell-progress sound, so nothing on the wire varies playback rate.
		_audio.play(id, gain);
	}
	// doc §15.2's `attract` loop, driven off the same idle flag the UI
	// already blanks itself on (StateLink::State::idleAttract) rather than a
	// discrete evt — see AudioBus::setAttractActive. `hasState` guards it
	// the same way it guards `_ui.update` above: no link yet must not read
	// as "confirmed idle".
	_audio.setAttractActive(hasState && state.idleAttract);
	// doc §15.2: the bin "burning" loop, sustained for exactly as long as a
	// hand stays inside a bin (`state.fireActive`, core's `self._hover_bin
	// is not None`). The catch/put-out one-shots (`fire_start`/`fire_stop`)
	// go through the `evt` loop above like any other cue; this is only the
	// sustained crackle in between. A one-shot evt has no stop shape, hence
	// the boolean — same reasoning as the attract loop above.
	_audio.setFireBurningActive(hasState && state.fireActive);
	// The roaming fireball's quieter loop, driven straight off CursorLink
	// rather than `state`: the cursor flame is CursorLink-driven and has no
	// dependency on core's link at all (`cursorForUi` below reads
	// `_cursor.hands()` directly). `pointer()` is non-null for a real
	// tracked hand AND for the idle-table phantom hand — the two are
	// indistinguishable on this wire by design, see CursorLink.h — so the
	// ambient crackle follows the same "hand present, real or phantom" rule
	// the visual fireball does.
	_audio.setHandFireActive(_cursor.pointer() != nullptr);
	// Developer mute, 'm' toggles (see ofApp.h on why this is a plain on/off
	// rather than a slider).
	_audio.setMasterVolume(_audioMuted ? 0.0f : 1.0f);
	// Steps any loop currently easing to silence (AudioBus::fadeOutLoop,
	// from one of the three setXActive edges above). Must run every frame
	// regardless of `hasState`, same reasoning as the `evt` drain: a fade in
	// progress does not pause because the link dropped.
	_audio.update(dt);

	// The fluid simulation runs on EVERY page: the fire IS the pointer, on
	// every phase, with no cursor glyph drawn on top of it anywhere (see
	// `cursorForUi` in draw()).
	const bool fluidActive = kFluidEnabled;
	// Driven by the real hand cursor(s), never the mouse — ofApp's mouse
	// callbacks are all deliberately empty (doc §7.1). Every hand injects,
	// pointer and ambient alike (doc §14.4).
	//
	// fireEmitters() must be read AFTER _ui.update() above, never before: it
	// reads this frame's freshly stepped crossfade springs. Safe to call
	// unconditionally — with no state yet, every bin's fire spring is still
	// at its constructed 0, so this returns empty rather than needing its
	// own hasState guard.
	if(fluidActive){
		std::vector<FluidLayer::FireRing> fireRings;
		for(const auto & e : _ui.fireEmitters()){
			fireRings.push_back({e.bin, e.cornerRadiusPx, e.innerOffsetPx, e.outerOffsetPx, e.intensity, e.binIndex});
		}
		_fluid.update(fluidDt, _cursor.hands(), fireRings);
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

	if(kBlendBenchTest){
		ofBackground(kBenchTableBackground);

		const float halfW = (float)ofGetWidth() / 2.0f;
		const float h = (float)ofGetHeight();
		const float rectSize = std::min(halfW, h) * 0.5f;
		const float rectY = h / 2.0f - rectSize / 2.0f;

		// Left half: MULTIPLY. Enabling ADD immediately before it, rather
		// than leaving whatever mode the previous frame ended in, is the
		// point — ofxFlowTools' own draw call leaves ADD set, so this proves
		// the MULTIPLY call actually clears that state rather than happening
		// to look right only because nothing had set ADD first.
		ofEnableBlendMode(OF_BLENDMODE_ADD);
		ofEnableBlendMode(OF_BLENDMODE_MULTIPLY);
		ofSetColor(kBenchCoral);
		ofDrawRectangle(halfW / 2.0f - rectSize / 2.0f, rectY, rectSize, rectSize);

		// Right half: ALPHA, fully opaque colour — §2's fallback if multiply
		// looks wrong on the projected surface.
		ofEnableBlendMode(OF_BLENDMODE_ADD);
		ofEnableBlendMode(OF_BLENDMODE_ALPHA);
		ofSetColor(kBenchCoral, 255);
		ofDrawRectangle(halfW + halfW / 2.0f - rectSize / 2.0f, rectY, rectSize, rectSize);

		ofEnableAlphaBlending();
		ofSetColor(255);
		ofDrawBitmapStringHighlight("MULTIPLY", halfW / 2.0f - 40, rectY + rectSize + 30);
		ofDrawBitmapStringHighlight("ALPHA (opaque)", halfW + halfW / 2.0f - 60, rectY + rectSize + 30);

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
		return;
	}

	if(kFluidDebugMouseOnly){
		ofBackground(255);
		if(kFluidEnabled){
			// PROJ_W_PX/PROJ_H_PX (stage space), not ofGetWidth/Height() —
			// see update()'s comment on DPI scaling. The production draw call
			// below (Stage's content FBO) uses these same fixed constants.
			_fluid.draw(0, 0, PROJ_W_PX, PROJ_H_PX);
		}
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
		return;
	}

	bool hasState = _link.hasState();
	StateLink::State state;   // default-constructed (empty) when !hasState
	if(hasState){
		state = _link.getState();
	}

	// Every page — see update()'s comment on `fluidActive`. Computed twice
	// rather than carried across two functions, matching this file's
	// existing pattern for per-frame locals.
	const bool fluidActive = kFluidEnabled;

	// No drawn cursor glyph at all: the fluid fire above IS the pointer,
	// everywhere, with nothing painted on top of it, so nothing is handed to
	// UiLayer to draw. Dwell progress reads on the WIDGET instead
	// (drawWidget's dwell fill), which is legible from three metres — a ring
	// under a hand is the half a hand covers.
	const CursorLink::Hand * cursorForUi = nullptr;

	// VISUAL_LAYER.md §5's five-layer order: layer 1 (table background)
	// happens inside beginContent(); layers 2 (fluid), 4 (halo) and 5 (UI)
	// are drawn here into the same content pass, in that order; layer 3 (the
	// white-cutout light pass) is Stage::compositeAndWarp()'s job below,
	// after endContent() — see Stage.h for why it is drawn structurally last
	// rather than literally third. I9 is untouched either way: the light
	// pass runs on the composite afterward, unconditionally.
	_stage.beginContent();
	// --- layer 2: fluid -----------------------------------------------
	if(fluidActive){
		// VISUAL_LAYER.md §2: fire must DARKEN the light table, not brighten
		// it, so it draws under OF_BLENDMODE_MULTIPLY rather than whatever
		// ofxFlowTools' internal compositing last left active — it leaves
		// OF_BLENDMODE_ADD set after its draw call, which on this #E8E6E1
		// background washes straight to white. Re-enabling alpha blending
		// immediately after is the other half of that: halo (layer 4) and UI
		// (layer 5) must not inherit this draw call's blend state either.
		ofEnableBlendMode(OF_BLENDMODE_MULTIPLY);
		_fluid.draw(0, 0, PROJ_W_PX, PROJ_H_PX);
		ofEnableAlphaBlending();
	}
	// --- layers 4-5: halo, then UI (both inside UiLayer::draw) ---------
	_ui.draw(hasState, state, _link.isConnected(), _link.secondsSinceLastState(),
		ofGetFrameRate(), _devOverlayVisible,
		_cursor.hands(), cursorForUi, _audioMuted);
	// Raw-skeleton diagnostic — see kDrawSkeleton.
	if(kDrawSkeleton){
		_ui.drawSkeleton(_skeleton.hands());
	}
	_stage.endContent();

	// There is no drawn cursor glyph left to rescue from the light pass, so
	// the `aboveLightPass` hook has nothing to draw. Passing nullptr keeps
	// I9 at full strength, unconditionally, which is the stricter of the two
	// behaviours anyway.
	std::function<void()> aboveLightPass = nullptr;
	_stage.compositeAndWarp(_ui.cutoutRectsPx(),
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
	// shutdown() must actually join, or the process can outlive its own
	// window with a link thread still trying to reconnect — which leaves a
	// Ctrl-C looking like it worked while the process survives.
	_link.shutdown();
	_cursor.close();
	_skeleton.close();
}

//--------------------------------------------------------------
void ofApp::keyPressed(int key){
	// p takes a screenshot; d toggles the fps/link/seq corner readout, which
	// is a debug tool rather than diner-facing and so defaults off. The
	// alignment nudge, calibration dot pattern and weight mock that once
	// lived here are gone: alignment moved out with the hit-testing it
	// served (core's FSM), the weight mock moved to the staff view's
	// developer panel, and automated dot-projection calibration will never
	// be used.
	if(key == 'p' || key == 'P'){
		_screenshotPending = true;
	}
	if(key == 'd' || key == 'D'){
		_devOverlayVisible = !_devOverlayVisible;
	}
	// m controls sound effect volume: this rig has no flat preview surface
	// to put a draggable slider on (see ofApp.h on _audioMuted), so on/off
	// is what a keyboard toggle can offer.
	if(key == 'm' || key == 'M'){
		_audioMuted = !_audioMuted;
		ofLogNotice("ofApp") << "audio: " << (_audioMuted ? "MUTED" : "on");
	}
	// f is the all-bins-lit flame diagnostic — see
	// UiLayer::setForceAllBinsLit() for what it is for and what each outcome
	// rules out. Logged rather than silent, so a screenshot taken while it
	// is on can be told apart from one taken while it is off.
	if(key == 'f' || key == 'F'){
		_ui.setForceAllBinsLit(!_ui.forceAllBinsLit());
		ofLogNotice("ofApp") << "flame diagnostic: all bins lit = "
			<< (_ui.forceAllBinsLit() ? "ON" : "off");
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
