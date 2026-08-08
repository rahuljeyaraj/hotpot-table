#pragma once

#include "ofMain.h"
#include "ofxOsc.h"

class ofApp : public ofBaseApp{

	public:
		void setup();
		void update();
		void draw();

		void keyPressed(int key);
		void keyReleased(int key);
		void mouseMoved(int x, int y );
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
		void logCalibrationDots();

		void receiveOsc();
		void drawHands();

		// nine calibration points in table mm, row-major, top row first
		std::vector<glm::vec2> calibDotsMM;
		bool showCalibration = false;

		// --- hand tracking, fed by tools/tracker/track_hands.py ------------
		// Positions arrive already in projector pixels; the Python side owns
		// the homography, so nothing here has to know about the camera.
		struct Hand {
			glm::vec2 posPx;
			uint64_t lastSeenMS = 0;
		};

		// Keyed by the id in the OSC message. A map rather than a vector
		// because the id is the tracker's, not an index into anything here -
		// it must survive ids arriving out of order or with gaps.
		std::map<int, Hand> hands;

		ofxOscReceiver oscReceiver;

		// Wall clock of the last message of ANY kind, /hand or /hand/none.
		// Nothing is drawn until the tracker has been heard from at all, so a
		// stopped tracker leaves a black table rather than a frozen dot.
		uint64_t lastMessageMS = 0;
		bool everReceived = false;
};
