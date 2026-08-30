#pragma once

#include "ofMain.h"
#include "AudioBus.h"
#include "CursorLink.h"
#include "FluidLayer.h"
#include "SkeletonLink.h"
#include "StateLink.h"
#include "Stage.h"
#include "UiLayer.h"

// The oF app, built around StateLink (control link) + Stage (FBO stack,
// keystone) + UiLayer (8 plates, total, tweening).
//
// Nothing here holds pricing, cart or bin-map state, and nothing touches a
// socket except through StateLink (I1/I2/I3): ofApp wires the classes
// together and owns the window, and that is all.
class ofApp : public ofBaseApp {
public:
	void setup();
	void update();
	void draw();
	void exit();

	void keyPressed(int key);
	void keyReleased(int key);
	void mouseMoved(int x, int y);
	void mouseDragged(int x, int y, int button);
	void mousePressed(int x, int y, int button);
	void mouseReleased(int x, int y, int button);
	void mouseEntered(int x, int y);
	void mouseExited(int x, int y);
	void windowResized(int w, int h);
	void dragEvent(ofDragInfo dragInfo);
	void gotMessage(ofMessage msg);

private:
	void logWindowState(const std::string & when);

	StateLink _link;
	CursorLink _cursor;
	FluidLayer _fluid;
	AudioBus _audio;   // doc §15
	// Diagnostic (see SkeletonLink.h): the raw, unsmoothed MediaPipe
	// skeleton, drawn on the projected table alongside the real cursor for
	// a side-by-side comparison. Not part of the documented wire protocol.
	SkeletonLink _skeleton;
	Stage _stage;
	UiLayer _ui;

	bool _screenshotPending = false;
	bool _devOverlayVisible = false;   // 'd' toggles; off by default (diner-facing table)
	float _statTimer = 0.0f;

	// Developer sound effect mute, 'm' toggles. A draggable ofxGui volume
	// slider was tried first but doesn't work on this rig: everything
	// past Stage::compositeAndWarp() draws in raw window pixels, never
	// keystoned onto the table, so a widget drawn there (unlike the fps/
	// link/seq text, which is drawn INSIDE beginContent/endContent and so
	// gets warped like everything else) lands off the visible table
	// entirely. A plain on/off mute has no positioning problem — its
	// state just prints as text in the same already-warped readout.
	bool _audioMuted = false;
};
