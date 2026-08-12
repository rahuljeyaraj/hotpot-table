#pragma once

// Physical table surface and the projector image mapped onto it.
// Everything in the app that needs a table position works in mm and
// converts here, so a projector or table change is a one-place edit.
//
// M1.4 note: this is the CAD layout only — no per-line nudge, no
// bin_offsets.json. v3 §7.1 keeps the *measured* offsets, but as the seed
// for core's bin grids (core/bin_grid.py, M4m/M4n — state/bin_grid_camera
// .json and state/bin_grid_projector.json), not as something oF applies to
// itself; oF has no way to hit-test a hand against a bin any more (that
// moved to core's FSM at M5), so the alignment tool that used to serve
// that hit test moved out with it. The raw CAD rects below remain the
// fallback for the 8 plates and the light-pass cutouts whenever core has
// not sent a projector-grid rect for a bin yet (UiLayer::binRectPx) — as
// of M4n core DOES put one on the wire, in `state.bins[].rect`, once a
// human has set the projector grid; before that this file is still what
// draws.

// Plywood top: 60 x 36 inches.
// constexpr rather than const so the bin layout below can be checked against
// these at compile time.
static constexpr float TABLE_W_MM = 1524.0f;
static constexpr float TABLE_H_MM = 914.4f;

// Projector native resolution, image assumed to cover the whole table.
static const int PROJ_W_PX = 1920;
static const int PROJ_H_PX = 1080;

// mm along the table -> projector pixel. Axis-independent scales because
// the table's aspect (1.667) is not the projector's (1.778).
inline float mmToPxX(float mm){
	return mm * (float)PROJ_W_PX / TABLE_W_MM;
}

inline float mmToPxY(float mm){
	return mm * (float)PROJ_H_PX / TABLE_H_MM;
}

// --- bins -----------------------------------------------------------------
// Eight bins, two rows of four, symmetric about the table centre with a wide
// gap up the middle for the pot.
//
// The two chains below are the source of truth for the layout. Each is a
// left-to-right (or far-to-near) walk across the table, and each MUST sum to
// the table dimension - a chain that does not span the table means a bin has
// been placed off the plywood. The static_asserts at the bottom enforce this.
//
//   X: 92 +200+50+200 +440 +200+50+200+ 92 = 1524   (TABLE_W_MM)
//      ^   ^^^ ^^ ^^^  ^^^                  ^^
//      |   bin gap bin  |                   edge margin
//      edge margin      centre gap (pot)
//
//   Y: 177 +255+50+255+ 177.4 = 914.4               (TABLE_H_MM)
//       ^    ^^^ ^^ ^^^  ^^^^^
//       |    far gap near  near edge margin
//       far edge margin
//
// The xMM/yMM values in BINS are running totals of these chains, not
// independent measurements. Change a chain term and the origins must be
// recomputed to match.

static constexpr float BIN_W_MM = 200.0f;
static constexpr float BIN_H_MM = 255.0f;

// The projector must not spill light into a physical bin cutout, so the black
// fill is drawn slightly larger than the bin itself. Absorbs both the
// homography's residual error and the saw kerf on the real cutout.
static constexpr float CUTOUT_MARGIN_MM = 10.0f;

// A bin footprint in table mm. Origin is the corner nearest the far-left of
// the table, matching the mm axes: +x to the right, +y from the far edge
// towards the diner - which is why the far row below has the smaller yMM.
struct BinRect {
	float xMM, yMM, wMM, hMM;
};

// Indices 0-3 are the far row left to right, 4-7 the near row left to right,
// so bin N and bin N+4 share a column. Same order as the calibration dots:
// row-major, far row first.
static constexpr int BIN_COUNT = 8;

static constexpr BinRect BINS[BIN_COUNT] = {
	{   92.0f, 177.0f, BIN_W_MM, BIN_H_MM },  // 0  far left
	{  342.0f, 177.0f, BIN_W_MM, BIN_H_MM },  // 1  far centre-left
	{  982.0f, 177.0f, BIN_W_MM, BIN_H_MM },  // 2  far centre-right
	{ 1232.0f, 177.0f, BIN_W_MM, BIN_H_MM },  // 3  far right
	{   92.0f, 482.0f, BIN_W_MM, BIN_H_MM },  // 4  near left
	{  342.0f, 482.0f, BIN_W_MM, BIN_H_MM },  // 5  near centre-left
	{  982.0f, 482.0f, BIN_W_MM, BIN_H_MM },  // 6  near centre-right
	{ 1232.0f, 482.0f, BIN_W_MM, BIN_H_MM },  // 7  near right
};

// The rect to fill black for a bin: the bin grown by CUTOUT_MARGIN_MM on all
// four sides, so the origin moves back by the margin and each span grows by
// twice it.
inline constexpr BinRect binFillRectMM(const BinRect & bin){
	return {
		bin.xMM - CUTOUT_MARGIN_MM,
		bin.yMM - CUTOUT_MARGIN_MM,
		bin.wMM + 2.0f * CUTOUT_MARGIN_MM,
		bin.hMM + 2.0f * CUTOUT_MARGIN_MM
	};
}

// --- layout checks --------------------------------------------------------
// Exact float equality would be a coin flip: neither 177.4 nor 914.4 is
// representable in binary, so the Y chain lands within an ulp of TABLE_H_MM
// rather than exactly on it. 0.01 mm is far tighter than anything that
// matters on plywood and far looser than float rounding at this magnitude.
inline constexpr bool sameMM(float a, float b){
	return (a - b) < 0.01f && (b - a) < 0.01f;
}

// The two chains span the table.
static_assert(sameMM(92.0f + BIN_W_MM + 50.0f + BIN_W_MM + 440.0f
                          + BIN_W_MM + 50.0f + BIN_W_MM + 92.0f, TABLE_W_MM),
	"X bin layout chain does not sum to TABLE_W_MM");

static_assert(sameMM(177.0f + BIN_H_MM + 50.0f + BIN_H_MM + 177.4f, TABLE_H_MM),
	"Y bin layout chain does not sum to TABLE_H_MM");

// The BINS origins are the running totals those chains imply. These catch a
// bin size edited without moving the bins that follow it.
static_assert(sameMM(BINS[1].xMM, BINS[0].xMM + BIN_W_MM + 50.0f),
	"far row left pair does not match the 50 mm gap in the X chain");
static_assert(sameMM(BINS[2].xMM, BINS[1].xMM + BIN_W_MM + 440.0f),
	"centre gap does not match the 440 mm term in the X chain");
static_assert(sameMM(BINS[3].xMM + BIN_W_MM + 92.0f, TABLE_W_MM),
	"far row right edge does not close the X chain");
static_assert(sameMM(BINS[4].yMM, BINS[0].yMM + BIN_H_MM + 50.0f),
	"near row does not match the 50 mm row gap in the Y chain");
static_assert(sameMM(BINS[4].yMM + BIN_H_MM + 177.4f, TABLE_H_MM),
	"near row does not close the Y chain");

// HOVER_DWELL_MS used to live here. Hover/dwell is v3 §9.4/§11 territory now
// — computed from tracker cursors by core's FSM (M5), not by oF reading OSC
// hand positions itself. Deleted with the rest of the M0.1 hover code
// (ofApp.cpp's updateHover/binHover) rather than carried forward unused;
// v3 §7.1 is explicit that this is a rewrite and "deleting is the point."
