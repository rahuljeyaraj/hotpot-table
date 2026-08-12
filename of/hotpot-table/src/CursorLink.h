#pragma once

#include "ofJson.h"

#include <cstdint>
#include <string>
#include <vector>

class ofxUDPManager;

// The cursor link, oF's side (v3 doc §4, §4.6, §11.4; doc §21 M5 build item
// 3). The C++ mirror of python/hotpot/common/cursorbus.py, and it has to
// agree with that file on every byte, because the two are the only things
// that ever speak this protocol:
//
//   - ONE datagram is ONE whole message. No framing, no newline, no `t`
//     field — this socket carries exactly one kind of message and always
//     will, so there is nothing to demultiplex.
//   - {"seq":<int>,"ts":<float>,"hands":[{"id":..,"role":"pointer"|"ambient",
//     "x":<stage px>,"y":<stage px>,"conf":<float>}]}
//   - x,y are STAGE space (doc §5.1's canonical space). The tracker applies
//     the camera->stage homography before sending, so oF and core receive
//     identical coordinates and cannot disagree about where a hand is.
//
// THE RECEIVER RULE, and it is the entire reason cursors are UDP at all
// (doc §4): **drain to latest.** Read the socket until it is empty, keep
// the highest seq, discard the rest. TCP would queue stale cursors and a
// 200ms hiccup would then deliver a burst in order — the hand visibly
// replaying through its own history, which is exactly the jitter six
// processes exist to avoid. There is deliberately no "read one packet"
// method on this class, for the same reason cursorbus.Receiver has none.
//
// Two rules, not one, and they are different — cursorbus.py's docstring
// argues this at length and it is repeated here because a future edit to
// one side must be made to the other:
//   1. WITHIN one drain: keep the highest seq.
//   2. ACROSS drains: never accept a seq at or below one already accepted.
//      UDP may reorder, so a datagram that lost a race arrives on the NEXT
//      frame, after its successor has been drawn. Taking it would step the
//      cursor backwards for one frame.
//
// NO THREAD, unlike StateLink — and that is a deliberate difference rather
// than an inconsistency. StateLink needs its own thread because
// ofxTCPManager::Connect() blocks even in non-blocking mode, and a slow
// core would freeze the table before a frame drew. A non-blocking UDP
// recvfrom cannot block, so draining from update() is both simpler and
// strictly better: it needs no mutex, it costs one fewer thread on a
// 4-core board with no spare (doc §10.4), and if the render stalls the
// packets pile up in the kernel where drain-to-latest throws them away
// correctly, rather than in a queue this class would have to manage.
class CursorLink {
public:
	struct Hand {
		int id = 0;
		bool pointer = false;   // role == "pointer"; ambient otherwise
		float x = 0.0f, y = 0.0f;
		float conf = 0.0f;
	};

	// port = doc §4.1's `cursor.of_port`, default 8770.
	void setup(int port);
	void close();

	// Drain the socket. Call once per frame, from update(). Returns true if
	// a NEW frame was accepted this call.
	//
	// False does NOT mean "no hands" — it means nothing newer arrived, and
	// the last frame stands. An empty `hands` array in an accepted datagram
	// is a real statement (the table is empty) and does clear them. A
	// dropped packet must not clear the cursor; an empty table must.
	bool update();

	const std::vector<Hand> & hands() const { return _hands; }

	// The pointer hand, or nullptr. doc §11.4: oF draws NO cursor and NO
	// dwell ring for ambient hands. They are still received, because M8's
	// fluid injects forces at every hand's position — the isolation is
	// about what is DRAWN as a cursor, not about what is known.
	const Hand * pointer() const;

	// Seconds since the last accepted datagram. Large, not 0, before the
	// first one ever arrives — "the tracker has never spoken" and "the
	// tracker just spoke" must not read the same. Used to hide a stale
	// cursor rather than leave a hand frozen on the table.
	float secondsSinceLastFrame() const;

	bool hasEverReceived() const { return _lastSeq >= 0; }
	int64_t droppedStale() const { return _droppedStale; }

private:
	bool parse(const char * data, int len, int64_t & seqOut,
		std::vector<Hand> & handsOut) const;

	ofxUDPManager * _udp = nullptr;   // owned; opaque here to keep ofxNetwork out of this header
	int _port = 8770;

	std::vector<Hand> _hands;
	int64_t _lastSeq = -1;
	int64_t _droppedStale = 0;
	uint64_t _lastFrameAtMs = 0;
	bool _open = false;
};
