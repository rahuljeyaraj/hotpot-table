#include "SkeletonLink.h"

#include "ofxUDPManager.h"
#include "ofLog.h"

#include <chrono>

namespace {
	const char * kTag = "SkeletonLink";

	// A skeleton datagram carries up to 21 points per hand rather than
	// cursorbus's one — bigger than CursorLink's buffer, still far below
	// the ~64KB a single UDP datagram could carry.
	const int kRecvBuffer = 16384;

	// Matches skeletonbus.MAX_DRAIN's own reasoning.
	const int kMaxDrain = 512;

	uint64_t nowMs(){
		using namespace std::chrono;
		return duration_cast<milliseconds>(
			steady_clock::now().time_since_epoch()).count();
	}
}

void SkeletonLink::setup(int port){
	_port = port;
	_udp = new ofxUDPManager();
	if(!_udp->Create()){
		ofLogError(kTag) << "could not create the UDP socket";
		return;
	}
	_udp->SetReuseAddress(true);
	if(!_udp->Bind((unsigned short)_port)){
		ofLogError(kTag) << "could not bind UDP port " << _port
			<< " — is another oF instance running? No raw skeleton will draw.";
		return;
	}
	_udp->SetNonBlocking(true);
	_open = true;
	ofLogNotice(kTag) << "listening for raw skeletons on UDP " << _port
		<< " (RIG_FEEDBACK item 11 diagnostic)";
}

void SkeletonLink::close(){
	if(_udp != nullptr){
		_udp->Close();
		delete _udp;
		_udp = nullptr;
	}
	_open = false;
}

float SkeletonLink::secondsSinceLastFrame() const {
	if(_lastFrameAtMs == 0){
		return 1.0e6f;
	}
	return (float)(nowMs() - _lastFrameAtMs) / 1000.0f;
}

bool SkeletonLink::update(){
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
			break;
		}
		int64_t seq = -1;
		std::vector<Hand> hands;
		if(!parse(buf, n, seq, hands)){
			continue;
		}
		if(seq <= _lastSeq){
			continue;
		}
		if(seq > bestSeq){
			bestSeq = seq;
			bestHands.swap(hands);
			accepted = true;
		}
	}

	if(accepted){
		_lastSeq = bestSeq;
		_hands.swap(bestHands);
		_lastFrameAtMs = nowMs();
	}
	return accepted;
}

bool SkeletonLink::parse(const char * data, int len, int64_t & seqOut,
	std::vector<Hand> & handsOut) const {
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
		return true;   // an empty-hands frame is a real "nobody visible"
	}
	for(const auto & hj : j["hands"]){
		if(!hj.is_object() || !hj.contains("points")
			|| !hj["points"].is_array()){
			continue;
		}
		Hand h;
		h.handedness = hj.value("handedness", "");
		h.conf = hj.value("conf", 0.0f);
		for(const auto & pj : hj["points"]){
			if(!pj.is_array() || pj.size() != 2
				|| !pj[0].is_number() || !pj[1].is_number()){
				continue;   // one bad point is dropped, not the whole hand
			}
			Point p;
			p.x = pj[0].get<float>();
			p.y = pj[1].get<float>();
			h.points.push_back(p);
		}
		if(!h.points.empty()){
			handsOut.push_back(h);
		}
	}
	return true;
}
