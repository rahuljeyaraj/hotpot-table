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
		void drawBinOutlines();
		void drawBinLabels();
		void updateHover();
		void loadIngredients();
		void cycleFieldLevel();

		// Moves one bin's mock weight and emits the settled delta line. Signed:
		// negative is a pick, positive is a put-back. One function for both, so
		// there is exactly one place a weight change can be reported from.
		void applyBinWeightDelta(int bin, float deltaGrams);

		// Magnitude of the next mock event, walking the cycling set in the .cpp.
		float nextMockDeltaGrams();

		// Steps the display deadband once per frame. Decides WHEN the UI is
		// allowed to catch up to the weights, and nothing else - see
		// displayedWeightGrams for why it cannot be allowed to do more.
		void updateDisplayedWeights();

		// Grams taken out of bin i, given a weight reading for that bin. The
		// reading is a parameter rather than read from a member on purpose:
		// there are two weights in this app - the true one and the displayed
		// one - and every caller has to say which of them it is pricing.
		float removedGrams(int bin, float weightGrams) const;

		// What that many grams out of bin i costs. Prices in ingredients.json
		// are PER 100 GRAMS, so this divides by 100 before multiplying.
		float binPrice(int bin, float removedG) const;

		// The read-only cart, in the back half of the centre column.
		void drawCart();

		// Where the cart is allowed to draw. Derived from the corrected bin
		// rects, not from raw mm, so nudging the grid moves the cart with it.
		ofRectangle cartRectPx() const;

		// The one definition of where bin i is on screen. The outline, the label
		// clearance and the hover hit test all read it, so they cannot drift
		// apart: a hit test against raw BINS[] would be testing the CAD drawing
		// while the outline is drawn on the as-built plywood, and the two are up
		// to a centimetre apart (CLAUDE.md section 17).
		ofRectangle binRectPx(int i) const;

		float vLineMM(int i) const;
		float hLineMM(int i) const;
		void nudgeSelection(float dxMM, float dyMM);
		void cycleSelection(int dir);
		std::string selectionLabel() const;
		void drawSelectionHighlight();
		void saveOffsets();
		void loadOffsets();
		void savePendingScreenshot();

		// Set by the key, acted on at the end of draw(). Grabbing the screen
		// from the key handler would read the back buffer after its swap, when
		// what it holds is undefined.
		bool screenshotPending = false;

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

		// --- the white field -----------------------------------------------
		// How bright the illuminating field is, as an index into kFieldLevels.
		// That array runs 100 down to 0 so index 0 is full output and the key
		// dims as it cycles. Starting anywhere else would be wrong rather than
		// merely different: in a dark room this field IS the light the camera
		// and the classifier get, so less than full output is a choice somebody
		// makes at the table, never a state the app boots into.
		int fieldLevel = 0;

		// --- ingredient labels ---------------------------------------------
		// What is in each bin and what it costs. This is DATA, not rig state:
		// unlike bin_offsets.json it does not describe this particular table,
		// it describes the menu, and it is edited by hand rather than by nudge
		// keys. bin/data/ingredients.json is the ONLY place names and prices
		// live - there is deliberately no hardcoded fallback here, because a
		// fallback is a second source of truth that silently wins whenever the
		// file is wrong, and a table showing the wrong price is worse than a
		// table showing none.
		struct Ingredient {
			std::string name;
			float pricePer100g = 0.0f;
		};

		// Exactly BIN_COUNT entries once loaded, empty if the file was missing
		// or unusable. Empty means nothing is drawn - see above.
		std::vector<Ingredient> ingredients;

		// The symbol in front of every price. ONE field for the whole file, not
		// one per ingredient, because a table cannot be priced in two currencies
		// at once - eight copies of "$" would be eight chances to disagree, and
		// the disagreement would be invisible until someone read two bins side
		// by side. A per-ingredient field would also read as permission to vary
		// it, which is exactly the thing that must not vary.
		//
		// A string rather than a char: it has to survive being "R$" or "kr" or a
		// multi-byte symbol, and ofTrueTypeFont takes UTF-8 either way.
		//
		// Empty until a successful load, and never defaulted to "$" - same rule
		// as the names and prices above. A number the diner reads as dollars
		// because the app assumed dollars is the wrong-price failure, just
		// arriving through the units instead of through the digits.
		std::string currency;

		// Two sizes, both loaded at their final display size. Scaling a font up
		// at draw time blurs it (CLAUDE.md section 7), and this text is being
		// read off plywood by a projector that has no resolution to spare.
		ofTrueTypeFont nameFont;
		ofTrueTypeFont priceFont;
		bool fontsLoaded = false;

		// The label geometry checks below run every frame, because the nudge
		// keys move the rects the labels are placed against. They must not
		// print every frame. One line per run is enough to act on.
		bool labelPlacementLogged = false;

		// --- mock bin weights ----------------------------------------------
		// The CURRENT weight sitting in each bin, in grams.
		//
		// A MOCK STAND-IN FOR A TARED LOAD CELL READING. At stage 3 these eight
		// floats are replaced by the numbers arriving from the eight HX711s
		// over USB serial, and nothing downstream may be able to tell the
		// difference. So this deliberately holds the same quantity a tared load
		// cell reports - what is in the bin right now - and NOT "how much has
		// been taken", which is a derived running figure the pricing FSM will
		// keep for itself. Storing the derived one here would put the mock and
		// the real sensor on different sides of a subtraction, which is the one
		// difference a swap is not allowed to have.
		//
		// Filled in setup() rather than here so the starting value can live in
		// the .cpp beside the other tuned constants.
		float binWeightGrams[BIN_COUNT] = {};

		// Cursor into the cycling set of event sizes in the .cpp. ONE cursor for
		// the whole table, not one per bin: what the set exists to vary is the
		// size of SUCCESSIVE events, and a per-bin cursor would hand every bin
		// the same first pick.
		int mockDeltaIndex = 0;

		// What each bin weighed before anybody touched it. The zero that every
		// price is measured from.
		//
		// A NAMED ARRAY RATHER THAN THE STARTING CONSTANT, because this is the
		// thing a tare overwrites. When the load cells arrive, taring a bin is
		// exactly "copy the current reading into this array", and a bare
		// literal in the subtraction would make that a code change instead of
		// an assignment. It is also per-bin because a real tare is: eight bins
		// with eight different amounts of ingredient in them.
		float startWeightGrams[BIN_COUNT] = {};

		// The weight the UI is currently showing, per bin.
		//
		// THIS IS THE DEADBAND, AND IT IS THE WHOLE OF THE DEADBAND. It gates
		// WHEN the display catches up to binWeightGrams, and it never touches
		// the price arithmetic - see updateDisplayedWeights() for why the
		// obvious "ignore deltas under 10 g" version is a different and wrong
		// thing.
		//
		// Every value in here was the true weight of its bin at some instant,
		// never an approximation of one. That is what keeps the arithmetic
		// honest: a price computed from this array is exact, just slightly old.
		float displayedWeightGrams[BIN_COUNT] = {};

		// --- cart -----------------------------------------------------------
		// Whether the cart is currently taller than the space it is allowed.
		// State rather than a one-shot "already logged" flag on purpose: the
		// shared-flag bug in CLAUDE.md section 22 item 2 is one advisory
		// permanently silencing an unrelated fatal check, and a one-shot here
		// would do the same thing to itself - the first overflow would be the
		// only one ever reported, including after the layout changed. Logging
		// the crossings instead means every entry into overflow is heard, and
		// nothing repeats 60 times a second.
		bool cartOverflowing = false;

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

		// Positions that arrived over OSC since the last time the hover state
		// was stepped. Kept separate from `hands` on purpose: `hands` holds a
		// position for kHandTimeoutMS after the tracker last reported it, so
		// reading it would let a hand that is no longer being detected keep
		// earning dwell. See updateHover().
		std::vector<glm::vec2> freshHandsPx;

		// Whether the tracker said anything at all this frame, /hand or
		// /hand/none. Distinguishes "the tracker is alive and sees no hand",
		// which resets every accumulator, from "the tracker went quiet", which
		// must not.
		bool detectionFrame = false;

		ofxOscReceiver oscReceiver;

		// Wall clock of the last message of ANY kind, /hand or /hand/none.
		// Nothing is drawn until the tracker has been heard from at all, so a
		// stopped tracker leaves a black table rather than a frozen dot.
		uint64_t lastMessageMS = 0;
		bool everReceived = false;

		// --- hover ---------------------------------------------------------
		// One state per bin. DWELLING is not just IDLE-with-a-number: it is the
		// state where a bin is accumulating but has not earned anything, and
		// naming it keeps "the hand is over this bin" and "this bin is hovered"
		// from being confused for each other, which is the entire point of the
		// dwell threshold.
		enum class HoverState { IDLE, DWELLING, HOVERED };

		HoverState binHover[BIN_COUNT] = {};
		float binDwellMS[BIN_COUNT] = {};

		// When a bin entered HOVERED, so leaving it can report how long it was
		// held. Only meaningful while that bin is HOVERED.
		uint64_t binHoveredSinceMS[BIN_COUNT] = {};

		// Wall clock of the last frame that carried a real detection. The dwell
		// accumulator advances by the gap between consecutive values of this -
		// NOT by ofGetLastFrameTime(), which would keep counting through a
		// detection dropout, and NOT by Hand::lastSeenMS, which is the render
		// hold. Two timers, deliberately not shared.
		uint64_t lastDetectionMS = 0;
};
