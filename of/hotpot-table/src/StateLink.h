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
