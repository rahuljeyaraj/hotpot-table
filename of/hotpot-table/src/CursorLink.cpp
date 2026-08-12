#include "CursorLink.h"

#include "ofxUDPManager.h"
#include "ofLog.h"

#include <algorithm>
#include <chrono>

namespace {
	const char * kTag = "CursorLink";

	// A cursor datagram is ~150 bytes (doc §4.6). 8 KB matches
	// cursorbus.RECV_BUFFER on the Python side and is far below the ~64 KB
	// a single UDP datagram could carry, so this can never truncate a real
	// packet. (A datagram longer than the buffer is discarded by the
	// kernel, not delivered short — so a mismatch here would look like
	// silence, not corruption.)
	const int kRecvBuffer = 8192;

	// How many datagrams one drain will read before giving up and using the
	// best it has. Matches cursorbus.MAX_DRAIN. Only reachable if the
	// tracker is outrunning the render loop by a wide margin; bounded so a
	// drain can never become an unbounded loop inside one frame, which is
	// how a small stall turns into a wedged table.
	const int kMaxDrain = 512;

	// How long a cursor stays on the table after the last datagram. The
	// tracker emits per camera frame (~30Hz) and goes silent outright when
	// frames are stale (doc §6.4: "sends nothing rather than sending a
	// frozen cursor"), so silence for a third of a second means the hand is
	// gone or the tracker is. Leaving the cursor drawn would put a hand on
	// the table that is not there — worse than no cursor, because a diner
	// would try to use it.
	const float kCursorHoldSeconds = 0.35f;

	uint64_t nowMs(){
		using namespace std::chrono;
		return duration_cast<milliseconds>(
			steady_clock::now().time_since_epoch()).count();
	}
}

void CursorLink::setup(int port){
	_port = port;
	_udp = new ofxUDPManager();
	if(!_udp->Create()){
		ofLogError(kTag) << "could not create the UDP socket";
		return;
	}
	// SetReuseAddress before Bind, not after: it has to be on the socket
	// before the bind that would otherwise fail. This matters on a restart
	// — the previous oF process's socket may still be in the OS's tables.
	_udp->SetReuseAddress(true);
	if(!_udp->Bind((unsigned short)_port)){
		ofLogError(kTag) << "could not bind UDP port " << _port
			<< " — is another oF instance running? No hand cursor will draw.";
		return;
	}
	// VERIFIED in the installed ofxUDPManager rather than assumed: Receive()
	// calls WaitReceive() first whenever a timeout is set, and returns
	// <= 0 when there is nothing waiting. Non-blocking is what makes
	// draining from the render thread safe at all — see the class comment
	// on why this has no thread of its own.
	_udp->SetNonBlocking(true);
	_open = true;
	ofLogNotice(kTag) << "listening for cursors on UDP " << _port;
}

void CursorLink::close(){
	if(_udp != nullptr){
		_udp->Close();
		delete _udp;
		_udp = nullptr;
	}
	_open = false;
}

const CursorLink::Hand * CursorLink::pointer() const {
	// Hidden along with every other cursor once the stream goes quiet, so
	// a caller cannot draw a dwell ring around a hand that left.
	if(secondsSinceLastFrame() > kCursorHoldSeconds){
		return nullptr;
	}
	for(const Hand & h : _hands){
		if(h.pointer){
			return &h;
		}
	}
	return nullptr;
}

float CursorLink::secondsSinceLastFrame() const {
	if(_lastFrameAtMs == 0){
		return 1.0e6f;   // never, not "just now"
	}
	return (float)(nowMs() - _lastFrameAtMs) / 1000.0f;
}

bool CursorLink::update(){
	if(!_open || _udp == nullptr){
		return false;
	}

	char buf[kRecvBuffer];
	bool accepted = false;
	int64_t bestSeq = -1;
	std::vector<Hand> bestHands;

	for(int i = 0; i < kMaxDrain; i++){
		int n = _udp->Receive(buf, kRecvBuffer);
		if(n <= 0){
			break;   // socket empty (non-blocking) — the ordinary exit
		}
		int64_t seq = -1;
		std::vector<Hand> hands;
		if(!parse(buf, n, seq, hands)){
			continue;   // a garbled datagram is dropped, never allowed to
						// take down the frame it arrived on
		}
		// Rule 2: across drains.
		if(seq <= _lastSeq){
			_droppedStale++;
			continue;
		}
		// Rule 1: within this drain. `>` not `>=`, and reading the highest
		// rather than the last — UDP may deliver 1, 5, 3 in that order, and
		// taking the last would put the cursor back where it was two
		// frames ago.
		if(seq > bestSeq){
			if(bestSeq >= 0){
				_droppedStale++;
			}
			bestSeq = seq;
			bestHands.swap(hands);
			accepted = true;
		}
		else {
			_droppedStale++;
		}
	}

	if(accepted){
		_lastSeq = bestSeq;
		_hands.swap(bestHands);
		_lastFrameAtMs = nowMs();
	}
	return accepted;
}

bool CursorLink::parse(const char * data, int len, int64_t & seqOut,
	std::vector<Hand> & handsOut) const {
	// std::string(data, len) rather than treating the buffer as a C string:
	// ofxUDPManager::Receive memsets the buffer before recvfrom so a short
	// datagram happens to be NUL-terminated, but relying on that would
	// break silently the day a datagram exactly fills the buffer.
	ofJson j = ofJson::parse(std::string(data, (size_t)len), nullptr,
		/*allow_exceptions*/ false);
	if(j.is_discarded() || !j.is_object()){
		return false;
	}
	if(!j.contains("seq") || !j["seq"].is_number_integer()){
		return false;
	}
	seqOut = j["seq"].get<int64_t>();

	handsOut.clear();
	if(!j.contains("hands") || !j["hands"].is_array()){
		return true;   // a frame with no hands array is an empty table, not junk
	}
	for(const auto & hj : j["hands"]){
		if(!hj.is_object()){
			continue;
		}
		// A hand missing a coordinate is DROPPED, never defaulted — (0,0)
		// is the top-left corner of the table and would park a phantom
		// cursor there. Same rule as cursorbus.Hand.from_json.
		if(!hj.contains("x") || !hj.contains("y")
			|| !hj["x"].is_number() || !hj["y"].is_number()){
			continue;
		}
		Hand h;
		h.x = hj["x"].get<float>();
		h.y = hj["y"].get<float>();
		h.id = hj.value("id", 0);
		h.conf = hj.value("conf", 0.0f);
		const std::string role = hj.value("role", "");
		if(role != "pointer" && role != "ambient"){
			continue;   // an unknown role is not a hand this build understands
		}
		h.pointer = (role == "pointer");
		handsOut.push_back(h);
	}
	return true;
}
