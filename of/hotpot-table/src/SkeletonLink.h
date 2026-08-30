#pragma once

#include "ofJson.h"

#include <cstdint>
#include <string>
#include <vector>

class ofxUDPManager;

// Cursor-lag diagnostic, oF's side. The C++ mirror of
// python/hotpot/common/skeletonbus.py — read that module's docstring for
// the fuller picture. It draws the raw MediaPipe skeleton directly on the
// projected table so it can be compared side by side with the smoothed
// cursor, which is how a lag or stick introduced by smoothing is told apart
// from one already present in the tracking. Deliberately not part of
// doc §4/§4.6: this transport is not in the doc at all.
//
// Same two drain-to-latest rules as CursorLink (see that class's own
// comment for the full argument):
//   1. WITHIN one drain: keep the highest seq.
//   2. ACROSS drains: never accept a seq at or below one already accepted.
//
// Wire shape: {"seq":<int>,"ts":<float>,"hands":[{"handedness":"Left"|
// "Right"|null,"conf":<float>,"points":[[x,y],...]}]} — x,y are STAGE
// space, same as CursorLink's hands, but there is no `role` and no `id`:
// this is upstream of tracking.py's matching entirely, so there is no
// track to number and no role to have assigned yet.
//
// NO THREAD, same reasoning as CursorLink: a non-blocking UDP recvfrom
// cannot block, so draining from update() needs no mutex and costs no
// extra thread.
class SkeletonLink {
public:
	struct Point {
		float x = 0.0f, y = 0.0f;
	};

	struct Hand {
		std::string handedness;   // "Left", "Right", or empty (unknown)
		float conf = 0.0f;
		std::vector<Point> points;   // stage px, raw, unsmoothed
	};

	// port = skeletonbus.OF_PORT, default 8772.
	void setup(int port);
	void close();

	// Drain the socket. Call once per frame, from update(). Returns true if
	// a NEW frame was accepted this call. False does not mean "no hands" —
	// see CursorLink::update()'s own comment; the same distinction applies
	// here.
	bool update();

	const std::vector<Hand> & hands() const { return _hands; }

	// Seconds since the last accepted datagram — same "large, not 0,
	// before the first one ever arrives" rule as CursorLink.
	float secondsSinceLastFrame() const;

private:
	bool parse(const char * data, int len, int64_t & seqOut,
		std::vector<Hand> & handsOut) const;

	ofxUDPManager * _udp = nullptr;
	int _port = 8772;

	std::vector<Hand> _hands;
	int64_t _lastSeq = -1;
	uint64_t _lastFrameAtMs = 0;
	bool _open = false;
};
