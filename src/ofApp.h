#pragma once

#include "ofMain.h"
#include "ofxOsc.h"
#include "TableGeometry.h"

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
		void drawBinCutouts();

		float vLineMM(int i) const;
		float hLineMM(int i) const;
		void nudgeSelection(float dxMM, float dyMM);
		void cycleSelection(int dir);
		std::string selectionLabel() const;
		void drawSelectionHighlight();
		void saveOffsets();
		void loadOffsets();

		// The eight boxes are not eight independent rectangles - they are the
		// cells of a grid, cut by 8 vertical and 4 horizontal lines. Each column
		// owns two vertical lines (its left and right edge), each row two
		// horizontal ones. Two boxes in a column share both vertical lines;
		// four boxes in a row share both horizontal ones.
		//
		// Moving a line is therefore how the boxes are both positioned AND
		// sized: shifting one edge of a cell resizes it, so there is no separate
		// size control and none is wanted.
		static constexpr int kCols = 4;
		static constexpr int kRows = 2;
		static constexpr int kVLines = kCols * 2;
		static constexpr int kHLines = kRows * 2;

		// Per-line corrections in table mm, on top of the CAD positions in
		// TableGeometry.h. Deltas rather than absolute positions so the CAD
		// chain stays the source of truth and its layout asserts keep meaning
		// something.
		float vLineDeltaMM[kVLines] = {};
		float hLineDeltaMM[kHLines] = {};

		// Whole-pattern nudge, applied on top of every line. Kept as its own
		// target because sliding all twelve lines together is the common first
		// move, and doing it one line at a time would be twelve times the work.
		//
		// Deliberately NOT applied to the calibration dots: those are the
		// reference the homography was solved against, and moving them would
		// invalidate it.
		float offsetXMM = 0.0f;
		float offsetYMM = 0.0f;

		// 0 = the whole pattern, 1..kVLines = a vertical line,
		// kVLines+1 .. kVLines+kHLines = a horizontal line.
		int selection = 0;

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
