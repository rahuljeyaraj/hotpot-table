#include "ofApp.h"
#include "TableGeometry.h"

namespace {
	// doc §14.6's vocabulary: stage_size / sim_scale = simulation grid.
	// 4 is the doc's own dev-machine default; no adaptive controller yet
	// (§14.6 build item, not part of getting hand-driven fluid on screen).
	const int kFluidSimScale = 4;

	// Kill switch, same pattern as kDrawSkeleton below. Was off
	// 2026-08-13 pending a colour-desaturation bug on the rig; re-enabled
	// 2026-08-14 with FluidLayer rebuilt to port fireTest's own
	// injection/parameters verbatim (FluidLayer.h's class comment) rather
	// than the more defensive version that shipped disabled. `_fluid.setup()`
	// always runs so the object is in a valid state regardless of this flag.
	const bool kFluidEnabled = true;

	// doc §4.1 default; core/main.py's CONTROL_PORT.
	const std::string kCoreHost = "127.0.0.1";
	const int kCorePort = 8765;

	// doc §4.1's `cursor.of_port`. Hardcoded to the documented default —
	// this app still has no config reader — and this is the one port oF
	// listens on.
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

	// Dev-only isolation switch, developer's explicit request 2026-08-14:
	// the fluid was not rising on the rig (real hand input) and needed to be
	// tuned with nothing else on screen — same window, same fullscreen
	// process run.py already launches, not a separate app — but with
	// nothing drawn except the fluid and no dependency on core/tracker/
	// camera being up or calibrated. Same reasoning as fireTest's own
	// standalone app, but inside THIS binary's actual Stage/FluidLayer code
	// path, so whatever gets tuned here is exactly what the table will
	// show, not a translation of it.
	// True: skips UiLayer/StateLink/CursorLink and Stage's keystone warp/
	// white floor/light pass, drives FluidLayer from the mouse instead of a
	// tracked hand, draws only the fluid full-window. Window/fullscreen
	// setup is untouched either way. This is the doc §7.1 "OSC hand mock...
	// not coming back as a fluid-testing shortcut" decision deliberately
	// reversed, on the developer's own call, for this same purpose the doc
	// was originally trying to avoid — set false to return to the normal
	// hand-driven table.
	const bool kFluidDebugMouseOnly = false;

	// VISUAL_LAYER.md §9 step 3 / §2: "Bench-test this before building the
	// rest... Do not proceed until one is chosen." Same debug-isolation
	// shape as kFluidDebugMouseOnly above — skips Stage/UiLayer/StateLink
	// entirely and draws straight to the window, because this step is
	// answering one question (which blend mode reads correctly on the
	// light background) in isolation, not testing keystone alignment or
	// anything else. False by default; flip to true, rebuild, look at the
	// PROJECTED SURFACE, then flip back before building layer 3 onward.
	const bool kBlendBenchTest = false;

	// §1/§3's table background — duplicated from Stage.cpp's
	// kTableBackground rather than exposed from Stage, since this bench
	// deliberately bypasses Stage altogether.
	const ofColor kBenchTableBackground(0xE8, 0xE6, 0xE1);

	// §2/§3's fire-core colour — the one colour both bench halves render,
	// so the comparison is about the blend mode, not a colour difference.
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

	// fireTest/src/ofApp.cpp::update() computes the fluid's own dt as
	// 1.0/max(ofGetFrameRate(),1.f) — a SMOOTHED value — not
	// ofGetLastFrameTime()'s raw per-frame delta. This is not cosmetic:
	// 2026-08-14 rig test found that feeding FluidLayer the raw value was
	// the actual reason a byte-for-byte copy of fireTest's own fluid code
	// still came up with a fully empty density buffer here. One occasional
	// large raw frame time (a GC pause, a scheduling hiccup — anything)
	// inflates ftFluidFlow's internal timeStep (dt*speed*100), which
	// multiplies straight into the diffusion shader's strength
	// (viscosityDen*timeStep, run 20 times every single frame) — one bad
	// frame is enough to blur the just-injected density down to nothing.
	// The smoothed formula doesn't have single-frame spikes. Applies to
	// BOTH branches below, not just the debug one: the real hand-tracking
	// path passes the same dt into the same FluidLayer::update() and would
	// hit the identical failure mode on an ordinary frame hitch, which is
	// plausibly why the table's own fluid looked wrong before any of
	// today's other fixes too. UI tweening/`_statTimer` below intentionally
	// keep the raw `dt` — this smoothing is specifically for the fluid sim.
	float fluidDt = 1.0f / std::max(ofGetFrameRate(), 1.f);

	if(kFluidDebugMouseOnly){
		// Real FluidLayer, mouse standing in for a tracked hand — same shape
		// of input CursorLink would give it (STAGE space, 1920x1080), so
		// nothing about FluidLayer itself needs to know it's not a real
		// hand. Scaling by ofGetWidth/Height's own ratio to PROJ_W_PX/
		// PROJ_H_PX (confirmed 2026-08-14: both already read 1920x1080 on
		// this rig, so this is presently a no-op) rather than assuming
		// ofGetMouseX/Y() is already in stage space, in case that ever
		// isn't true on a different display setup.
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
	// RIG_FEEDBACK item 11 diagnostic — same drain-to-latest discipline,
	// see SkeletonLink::update()'s own comment.
	_skeleton.update();
	bool hasState = _link.hasState();
	if(hasState){
		StateLink::State state = _link.getState();
		_ui.update(dt, true, state);
	}
	// Driven by the real hand cursor(s), never the mouse (ofApp's mouse
	// callbacks are all empty, deliberately — v3 §7.1's deleted OSC hand
	// mock is not coming back as a fluid-testing shortcut). Every hand
	// injects, pointer and ambient alike (doc §14.4).
	//
	// 2026-08-14: the active-bin fire ring is gone (see FluidLayer.h's own
	// note) — the fluid layer now only ever carries the ambient hand trail
	// plus the permanent per-bin obstacles below, neither of which needs
	// anything read from `_ui` after its springs stepped, so this no
	// longer has to run after `_ui.update()` for freshness reasons. Left
	// here anyway, unchanged position, since nothing forces it earlier.
	//
	// Every bin is a real wall in the sim (FluidLayer::Obstacle ->
	// ftFluidFlow::setObstacle) — cutoutRectsPx(), the exact rect the
	// light pass already treats as the physical white plate, so the
	// obstacle can never be a different size than the thing it represents.
	if(kFluidEnabled){
		std::vector<FluidLayer::Obstacle> obstacles;
		for(const auto & r : _ui.cutoutRectsPx()){
			obstacles.push_back({r, mmToPxX(CUTOUT_CORNER_RADIUS_MM)});
		}
		_fluid.update(fluidDt, _cursor.hands(), obstacles);
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
		// point — §2 warns ofxFlowTools' own draw call leaves ADD set, so
		// this proves the MULTIPLY call below actually clears that state
		// rather than happening to look right only because nothing had set
		// ADD first.
		ofEnableBlendMode(OF_BLENDMODE_ADD);
		ofEnableBlendMode(OF_BLENDMODE_MULTIPLY);
		ofSetColor(kBenchCoral);
		ofDrawRectangle(halfW / 2.0f - rectSize / 2.0f, rectY, rectSize, rectSize);

		// Right half: ALPHA, fully opaque colour — §2's own fallback if
		// multiply looks wrong on the projected surface.
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
			// see update()'s own comment on DPI scaling. The production
			// draw call below (Stage's content FBO) already uses these
			// same fixed constants; matching it here for the same reason.
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

	// While the fluid is enabled it IS the hand pointer (FluidLayer.h's
	// class comment) — the old dot+ring cursor is suppressed rather than
	// drawn on top of it, so there is still exactly one visual answer to
	// "where is the hand," not two competing ones.
	const CursorLink::Hand * cursorForUi = kFluidEnabled ? nullptr : _cursor.pointer();

	// VISUAL_LAYER.md §9 build item 5 ("Layer reorder") / §5's 5-layer
	// order: layer 1 (table background) happens inside beginContent()
	// itself; layers 2 (fluid), 4 (halo) and 5 (UI) are drawn here, into
	// the same content pass, in that order; layer 3 (the white-cutout
	// light pass) is Stage::compositeAndWarp()'s job below, after
	// endContent() — see Stage.h's own header comment for why it is
	// drawn structurally last rather than literally third. I9 is
	// untouched either way — the light pass runs on the composite
	// afterward, unconditionally.
	_stage.beginContent();
	// --- layer 2: fluid -----------------------------------------------
	if(kFluidEnabled){
		// VISUAL_LAYER.md §2, decided at §9 step 3 (the bench test): fire
		// must DARKEN the light table, not brighten it, so it draws under
		// OF_BLENDMODE_MULTIPLY rather than whatever ofxFlowTools' own
		// internal compositing last left active (§2's own warning: it
		// leaves OF_BLENDMODE_ADD set after its draw call, which on this
		// #E8E6E1 background would wash straight to white). Re-enabling
		// alpha blending right after is the other half of that same
		// instruction — "so every layer above the fluid uses normal
		// blending" — halo (layer 4) and UI (layer 5) below must not
		// inherit whatever blend state this draw call leaves behind either.
		ofEnableBlendMode(OF_BLENDMODE_MULTIPLY);
		_fluid.draw(0, 0, PROJ_W_PX, PROJ_H_PX);
		ofEnableAlphaBlending();
	}
	// --- layers 4-5: halo, then UI (both inside UiLayer::draw) ---------
	_ui.draw(hasState, state, _link.isConnected(), _link.secondsSinceLastState(),
		ofGetFrameRate(), _devOverlayVisible,
		_cursor.hands(), cursorForUi);
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
	// is responsible for this frame's cursor. Uses the same cursorForUi
	// (nullptr while the fluid is enabled) as the call above, for the same
	// reason: the fluid is the pointer now, so this above-the-light-pass
	// path has nothing to draw either.
	std::function<void()> aboveLightPass = nullptr;
	if(state.mode == "serving" && cursorForUi != nullptr){
		const CursorLink::Hand * pointer = cursorForUi;
		aboveLightPass = [this, &state, pointer](){
			_ui.drawCursorAboveLightPass(state, pointer);
		};
	}
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
