#pragma once

#include "ofJson.h"

#include <atomic>
#include <chrono>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

class ofxTCPClient;

// The control link, oF's side (v3 doc §4, §20). oF is a TCP client; core is
// the one listener in the system (python/hotpot/common/wire.py's docstring
// says this outright: "core is the TCP server for every control link,
// everyone else is a client and reconnects with backoff"). This class is
// the C++ mirror of wire.Client, and it has to agree with wire.py on every
// byte, because the two are the only things that ever talk this protocol:
//
//   - one JSON object per line, newline-terminated, UTF-8, no other framing.
//   - a client opens with `hello`, waits for `welcome`, then heartbeats
//     every 1000ms (doc §4.2) until the link drops.
//   - reconnect backoff starts at 1s, doubles, caps at 10s (doc §20.2),
//     and a client must never give up — core not being up yet is the
//     normal state of the world at boot.
//
// VERIFIED against the installed ofxNetwork rather than assumed: ofxTCPClient
// has its own framing (send() appends "[/TCP]" plus a literal NUL byte,
// unconditionally, regardless of setMessageDelimiter — see ofxTCPClient.cpp).
// That NUL would land inside the next line from wire.py's point of view and
// fail json.loads on it. So this class never calls send()/receive() —
// only sendRaw() and receiveRawBytes(), and does its own newline splitting
// below, the same job LineReader does in wire.py.
//
// Runs its own thread, deliberately, the same way wire.Client does: a
// blocking connect() (ofxTCPManager::Connect() blocks even in "non-blocking"
// mode, per its own setup() — SetNonBlocking() is only called *after*
// Connect() returns) must never happen on oF's render thread, or a core
// that is slow to start freezes the table before a single frame draws.
// ofApp only ever reads the latest parsed state through a mutex; it must
// never block waiting for one.
class StateLink {
public:
	struct Bin {
		int i = 0;
		std::string label;
		std::string sub;
		float grams = 0.0f;
		float picked = 0.0f;
		double price = 0.0;
		std::string hl = "none";        // none|hover|picking|picked|lowstock|disabled
		std::string stock = "ok";
		bool resolved = false;

		// doc §4.3's `rect`, in STAGE space, [x,y,w,h] (M4 build item 4).
		// Core derives it from the camera-space rect staff dragged, through
		// H_cam->stage (doc §5.3), and it is what oF frames the plate and
		// the light-pass cutout against.
		//
		// hasRect is false until core has both a homography and eight
		// saved rects. It has to be an absence, not a plausible rectangle
		// at the origin: an uncalibrated table falls back to
		// TableGeometry.h's CAD layout, which is visibly approximately
		// right, whereas a rect at 0,0 would look like a rendering bug.
		bool hasRect = false;
		float rx = 0.0f, ry = 0.0f, rw = 0.0f, rh = 0.0f;

		// doc §4.3's `info` (VISUAL_LAYER.md §8's info box, build item
		// 10). All three arrive ALREADY RESOLVED, including the "kcal /
		// 100g" unit, for the same reason `sub` carries "/100g" rather
		// than oF appending it: I2 puts every diner-facing word on core's
		// side of the wire, so a second locale changes one JSON file and
		// no C++.
		//
		// `diet` is the one machine-readable value of the three — "veg",
		// "nonveg" or "egg" — because oF picks a colour and a dot from it
		// (drawInfoBox) rather than printing it. Empty on an unresolved
		// bin, and empty means DRAW NOTHING: doc §8's "Idle: invisible.
		// No fill, no border. Not an empty bordered box."
		std::string diet;   // veg|nonveg|egg, or "" for an unresolved bin
		std::string meta;   // right-hand slot: "74 kcal / 100g", resolved
		// One sentence on what the ingredient is LIKE, so a diner can
		// choose it. Never an instruction — the kitchen cooks this food,
		// not the table. See pricing.Item.description for the full rule.
		// A second `fact` field carried trivia until 2026-08-24; it is
		// gone from the wire, not blanked.
		std::string desc;
	};

	// doc §4.3's `widgets`, and §9.4's dwell fraction (M5 build item 3).
	// oF draws these and times NOTHING: `dwell` arrives as a 0..1 fraction
	// core computed, so the ring's fill is core's answer, not a second
	// clock here that could disagree with the one that actually fires.
	struct Widget {
		std::string id;      // cancel|confirm|broth:<id>|spice:<n>
		std::string kind = "button";   // "button" | "option"
		std::string label;   // already resolved in the current locale (I2)
		float x = 0.0f, y = 0.0f, w = 0.0f, h = 0.0f;   // stage space
		float dwell = 0.0f;  // 0..1
		bool enabled = true;
		std::string style = "primary";
		// M6. Whether the pointer is inside this widget RIGHT NOW, which
		// is core's answer (it owns the hit test, doc §9.4) rather than a
		// second one derived here from `dwell > 0`. That derivation would
		// be wrong for the first frame of a hover, when the accumulator
		// is still at zero.
		bool hover = false;
		// 2026-08-25. Whether this option is the one the diner has LOCKED
		// IN — a completed dwell, held until they complete a dwell on a
		// different one. Independent of `hover`, and that independence is
		// the whole point: a diner can hover a second broth to read its
		// info box and take their hand away without changing anything.
		//
		// Core owns it (`core/hover.Widget.selected`) for the same reason
		// core owns `hover`: it is the thing that will be written to the
		// order, and a second answer derived on this side could disagree
		// with the one that bills.
		bool selected = false;
		// A glyph oF draws itself, `iconCount` times. "chilli" is the only
		// one today — doc §18.1's "four plates, 0-3, with chilli glyphs".
		//
		// A NAME and a COUNT, never a character: the fonts this app loads
		// are DejaVu at Latin + Latin1Supplement + CurrencySymbols, and
		// U+1F336 is in none of them, so a literal pepper would silently
		// draw nothing at all. drawWidget builds the shape from an ofPath
		// instead — see drawChilli.
		std::string icon;
		int iconCount = 0;
		// 2026-08-25's chili-strip: how many total icon SLOTS this cell
		// draws, of which the first `iconCount` are lit and the rest
		// drawn grey — so a cell reads as a gauge (1 red + 2 grey for
		// Mild) rather than a lone chilli with nothing to compare it
		// against. 0 (the default, and every non-spice widget) means
		// "no shared total" — drawWidget/drawOptionPlate fall back to the
		// old single-count behaviour (just `iconCount` lit, nothing else
		// drawn) when this is 0, so a broth's swatch and an older core
		// with no such field are both unaffected.
		int maxIconCount = 0;
		// The info box's content while this widget is hovered — a broth
		// or a spice level has one, Cancel and Confirm do not. Same three
		// fields and the same rules as Bin's, so drawInfoBox can take
		// either without caring which it got.
		bool hasInfo = false;
		std::string diet;    // veg|nonveg|egg, or "" (a spice level is not food)
		std::string meta;    // the right-hand slot: "22 kcal / 100g", "Very spicy"
		std::string desc;
		// doc §18.1's "a colour swatch each", hex, empty for no swatch.
		std::string swatch;
	};

	// doc §18.1's CHECKOUT screen, carried on `overlay` (kind == "qr").
	struct Qr {
		std::string code;         // the short human code, e.g. "A17"
		std::string url;          // what the modules below encode
		std::string totalText;    // resolved by core (I2)
		bool paid = false;
		// **The diner-facing token, and it is EMPTY until the money has
		// landed.** Developer, 2026-08-25: "the token number should be
		// given only after sucessfull payment." `code` above is still
		// always populated — the URL is built from it and the staff view
		// lists it — but this is the field the table draws, and core
		// leaves it blank on an unpaid order so oF cannot show a number a
		// diner could walk to the counter with before paying.
		//
		// A separate field rather than oF checking `paid` itself: the
		// rule is a product decision about what a diner may be told, and
		// keeping it on core's side means it holds for any future surface
		// too, not just this one draw call.
		std::string token;
		// Square, row-major, true == a dark module. Core sends the
		// matrix and oF draws filled rects: I2 again — core owns the
		// data, oF owns the pixels, and a matrix scales to whatever
		// module size the projector needs with no resample.
		std::vector<std::vector<bool>> modules;
	};

	struct Fluid {
		std::string style = "mala";
		bool enabled = false;
		float intensity = 0.0f;
	};

	struct Total {
		double amount = 0.0;
		std::string text;
		std::string label;   // e.g. "Total"/"总计" — I2: resolved by core, oF never looks it up
	};

	// 2026-08-25: the page header. A sentence naming the task, plus where
	// the diner is in the sequence.
	//
	// Every restaurant kiosk a diner has already used leads its screen
	// with one of these, and without it the broth page is four unlabelled
	// plates and a Next button — which is the opposite of the developer's
	// own standard for this table ("any non techy person should be able
	// to understand it").
	//
	// `step`/`steps` are 0 when there is no sequence to be in (IDLE,
	// setting mode), and `title` is empty on exactly those screens, so a
	// header that should not exist draws nothing rather than drawing an
	// empty strip. Resolved by core, per I2.
	struct Screen {
		std::string title;
		std::string hint;
		int step = 0;
		int steps = 0;
	};

	// doc §4.3's `state` message, decoded. Bins is always resized to
	// exactly 8 by the parser (padding/truncating a malformed line rather
	// than trusting it) so UiLayer never has to range-check it.
	struct State {
		int64_t seq = -1;
		double ts = 0.0;
		// "serving" | "setting" (doc §4.3, M2.6). Defaulting to the
		// billing mode rather than the not-billing one is deliberate: a
		// `state` line that somehow arrived without the field must not
		// paint SETTING — NOT BILLING over a table that is billing.
		std::string mode = "serving";
		// M6: which screen of doc §9.1's chain the table is on —
		// idle|selecting|broth|spice|checkout|setting|uncalibrated.
		// ("recap" was a ninth value until 2026-08-25; that state is
		// deleted — see python/hotpot/core/fsm.py's module docstring.)
		// **Alongside `mode`, never inside it.** doc §4.3 fixes `mode` at
		// serving|setting and the banner branches on it, so folding the
		// checkout screens in there would make every one of them paint a
		// NOT SERVING banner over a table that is very much serving.
		// Defaults to "selecting" for the same reason `mode` defaults to
		// the billing value: a line that arrived without it must not put
		// the table into a checkout screen nobody asked for.
		std::string phase = "selecting";
		Screen screen;
		std::string locale = "en";
		Fluid fluid;
		std::vector<Bin> bins;
		std::vector<Widget> widgets;
		Total total;
		std::string overlayKind = "none";
		Qr qr;
	};

	// who="of" (doc §4.1 process names / health.py's PROCESSES tuple).
	void setup(const std::string & host, int port, const std::string & who = "of");
	void shutdown();

	// Thread-safe. False until the first `state` line has ever been parsed.
	bool hasState() const;
	State getState() const;

	// Wall-clock seconds since the last `state` line was parsed. Doc §13.3:
	// "if no `state` message has arrived for 500ms, oF freezes the last
	// state and draws a small connection-lost indicator." Returns a large
	// number, not 0, before the first state ever arrives — silence before
	// any connection is not the same claim as silence after one.
	float secondsSinceLastState() const;

	// True only once welcome has arrived on the current link (mirrors
	// wire.Client.connected — "connected-but-unwelcomed is not usable").
	bool isConnected() const;

	// Queued and sent on the link thread on its next tick. Never blocks;
	// silently dropped while the link is down, same drop rule as wire.py.
	// keystoneFingerprint matches doc §12's developer-overlay field list
	// and lets core's future calibration-staleness check (§8.5, M4) work
	// against a live oF the day it's built, rather than needing this wire
	// field added retroactively.
	void sendStat(float fps, const std::string & keystoneFingerprint);

private:
	void threadLoop();
	// Sleeps in small slices, checking _stop between them, so shutdown()
	// never has to wait out a multi-second backoff or handshake timeout —
	// the exact bug class CLAUDE.md's FIXED section already paid for once
	// on the Python side (run.py's Ctrl-C fix).  True if _stop fired early.
	bool interruptibleSleep(float seconds);
	bool connectOnce();                    // one full attempt incl. handshake
	void pollIncoming(ofxTCPClient & tcp, std::string & recvBuf);
	void sendLine(ofxTCPClient & tcp, const ofJson & obj);
	static bool parseState(const ofJson & j, State & out);

	std::string _host = "127.0.0.1";
	int _port = 8765;
	std::string _who = "of";

	std::thread _thread;
	std::atomic<bool> _stop{false};

	mutable std::mutex _stateMx;
	State _latest;
	bool _hasState = false;
	std::chrono::steady_clock::time_point _lastStateAt;

	std::atomic<bool> _connected{false};

	std::mutex _outMx;
	std::vector<ofJson> _outQueue;   // e.g. `stat` telemetry, main thread -> link thread
};
