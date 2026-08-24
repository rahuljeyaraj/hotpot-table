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
		std::string kcal;   // e.g. "74 kcal / 100g", resolved
		std::string desc;   // one short sentence, resolved
		std::string fact;   // one researched sentence about the real ingredient
	};

	// doc §4.3's `widgets`, and §9.4's dwell fraction (M5 build item 3).
	// oF draws these and times NOTHING: `dwell` arrives as a 0..1 fraction
	// core computed, so the ring's fill is core's answer, not a second
	// clock here that could disagree with the one that actually fires.
	struct Widget {
		std::string id;      // done|cancel|language
		std::string kind = "button";
		std::string label;   // already resolved in the current locale (I2)
		float x = 0.0f, y = 0.0f, w = 0.0f, h = 0.0f;   // stage space
		float dwell = 0.0f;  // 0..1
		bool enabled = true;
		std::string style = "primary";
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
		std::string locale = "en";
		Fluid fluid;
		std::vector<Bin> bins;
		std::vector<Widget> widgets;
		Total total;
		std::string overlayKind = "none";
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
