#include "StateLink.h"

#include "ofxTCPClient.h"
#include "ofLog.h"
#include "ofUtils.h"

#include <algorithm>

#ifdef _WIN32
	#include <windows.h>
#else
	#include <unistd.h>
#endif

namespace {
	const char * kTag = "StateLink";

	// No ofGetProcessId() in this oF version (VERIFIED — ofUtils.h has none).
	// The pid in `hello` is display-only on core's side (health.py's Entry.pid
	// is read by the staff view, never by any liveness decision), so a
	// platform pid is worth having but nothing here depends on its accuracy.
	long currentProcessId(){
#ifdef _WIN32
		return (long)GetCurrentProcessId();
#else
		return (long)getpid();
#endif
	}

	// doc §20.2's ladder, and wire.py's BACKOFF_START/BACKOFF_MAX exactly.
	const float kBackoffStart = 1.0f;
	const float kBackoffMax = 10.0f;

	// doc §4.2.
	const float kHeartbeatInterval = 1.0f;

	// wire.py's WELCOME_TIMEOUT: "a client that connects and is never
	// welcomed is talking to something that is not core."
	const float kWelcomeTimeout = 5.0f;

	// Poll granularity for the link thread. Small enough that a heartbeat
	// or a queued `stat` line never waits more than this to go out.
	const float kTick = 0.05f;

	// wire.py's MAX_LINE_BYTES, so a stream we can no longer trust to be
	// framed gets the same treatment on both ends: drop the link.
	const size_t kMaxLineBytes = 1 << 20;

	uint64_t nowMs(){
		using namespace std::chrono;
		return duration_cast<milliseconds>(steady_clock::now().time_since_epoch()).count();
	}
}

void StateLink::setup(const std::string & host, int port, const std::string & who){
	_host = host;
	_port = port;
	_who = who;
	_stop = false;
	_thread = std::thread(&StateLink::threadLoop, this);
}

void StateLink::shutdown(){
	_stop = true;
	if(_thread.joinable()){
		_thread.join();
	}
}

bool StateLink::hasState() const {
	std::lock_guard<std::mutex> lock(_stateMx);
	return _hasState;
}

StateLink::State StateLink::getState() const {
	std::lock_guard<std::mutex> lock(_stateMx);
	return _latest;
}

float StateLink::secondsSinceLastState() const {
	std::lock_guard<std::mutex> lock(_stateMx);
	if(!_hasState){
		// Large rather than 0: "never connected" must not read as "just
		// updated", which is what the doc's 500ms freeze rule tests for.
		return 1.0e6f;
	}
	using namespace std::chrono;
	return duration<float>(steady_clock::now() - _lastStateAt).count();
}

bool StateLink::isConnected() const {
	return _connected.load();
}

void StateLink::sendStat(float fps, const std::string & keystoneFingerprint){
	// doc §4.5: telemetry only, fire-and-forget. sim_res/gpu_ms are
	// FluidLayer's numbers (M8) and honestly zero until that class exists.
	ofJson msg = {
		{"t", "stat"},
		{"fps", fps},
		{"sim_res", ofJson::array({0, 0})},
		{"gpu_ms", 0.0},
		{"dropped", 0},
		{"keystone_fingerprint", keystoneFingerprint},
	};
	std::lock_guard<std::mutex> lock(_outMx);
	_outQueue.push_back(std::move(msg));
}

void StateLink::sendLine(ofxTCPClient & tcp, const ofJson & obj){
	// dump()'s ensure_ascii defaults to false in this nlohmann version, the
	// same choice wire.encode() makes on purpose (doc: "\\u codes would
	// triple [Chinese labels'] size and make a tcpdump unreadable"). Not
	// passed explicitly here so a link-time version mismatch would show up
	// as a build error against the documented default, not a silent switch
	// back to escaping.
	tcp.sendRaw(obj.dump() + "\n");
}

void StateLink::pollIncoming(ofxTCPClient & tcp, std::string & recvBuf){
	// receiveRawBytes bypasses ofxTCPClient's own "[/TCP]" framing entirely
	// (see the class comment) — this is nothing but a non-blocking recv().
	char chunk[8192];
	for(;;){
		int n = tcp.receiveRawBytes(chunk, sizeof(chunk));
		if(n <= 0){
			break;   // no data right now; isConnected() is how a real close is noticed
		}
		recvBuf.append(chunk, (size_t)n);

		size_t nl;
		while((nl = recvBuf.find('\n')) != std::string::npos){
			std::string line = recvBuf.substr(0, nl);
			recvBuf.erase(0, nl + 1);
			if(line.empty()){
				continue;   // blank keepalive lines are not messages (wire.py parity)
			}

			ofJson j = ofJson::parse(line, nullptr, /*allow_exceptions*/ false);
			if(j.is_discarded() || !j.is_object()){
				ofLogWarning(kTag) << "dropped an unparseable line (" << line.size() << " bytes)";
				continue;
			}

			const std::string t = j.value("t", "");
			if(t == "welcome"){
				// Handshake completion is handled inline in connectOnce();
				// a welcome should not normally arrive again mid-session,
				// but if it does there's nothing to act on beyond noting it.
				continue;
			}
			if(t == "state"){
				State parsed;
				if(parseState(j, parsed)){
					std::lock_guard<std::mutex> lock(_stateMx);
					_latest = parsed;
					_hasState = true;
					_lastStateAt = std::chrono::steady_clock::now();
				}
				continue;
			}
			// `evt` (doc §4.4) is AudioBus/FluidLayer's cue — neither exists
			// yet (M1.4 build item scope). Fire-and-forget by design, so
			// dropping it here on the floor is exactly the documented
			// behaviour for a receiver that isn't ready for it.
		}

		if(recvBuf.size() > kMaxLineBytes){
			ofLogError(kTag) << "line exceeded " << kMaxLineBytes << " bytes — resetting link";
			tcp.close();
			recvBuf.clear();
			return;
		}
	}
}

bool StateLink::connectOnce(){
	ofxTCPClient tcp;
	if(!tcp.setup(_host, _port, false)){
		return false;
	}

	std::string recvBuf;

	sendLine(tcp, {{"t", "hello"}, {"who", _who}, {"pid", (int)currentProcessId()}, {"ver", 3}});

	// Wait for welcome. wire.py's own client blocks its dedicated thread the
	// same way while waiting — this is that thread, not the render one.
	uint64_t deadline = nowMs() + (uint64_t)(kWelcomeTimeout * 1000.0f);
	bool welcomed = false;
	while(nowMs() < deadline){
		if(_stop.load() || !tcp.isConnected()){
			return false;
		}
		char chunk[8192];
		int n = tcp.receiveRawBytes(chunk, sizeof(chunk));
		if(n > 0){
			recvBuf.append(chunk, (size_t)n);
			size_t nl;
			while((nl = recvBuf.find('\n')) != std::string::npos){
				std::string line = recvBuf.substr(0, nl);
				recvBuf.erase(0, nl + 1);
				ofJson j = ofJson::parse(line, nullptr, false);
				if(!j.is_discarded() && j.is_object() && j.value("t", "") == "welcome"){
					welcomed = true;
				}
			}
			if(welcomed){
				break;
			}
		}
		if(interruptibleSleep(0.01f)){
			return false;
		}
	}

	if(!welcomed){
		ofLogWarning(kTag) << "connected to " << _host << ":" << _port
			<< " but no welcome in " << kWelcomeTimeout << "s — retrying";
		tcp.close();
		return false;
	}

	ofLogNotice(kTag) << "control link up to " << _host << ":" << _port;
	_connected = true;

	uint64_t nextBeat = nowMs();
	while(!_stop.load()){
		if(!tcp.isConnected()){
			break;
		}

		pollIncoming(tcp, recvBuf);

		uint64_t now = nowMs();
		if(now >= nextBeat){
			// ofGetUnixTimeMillis(), not ofGetSystemTimeMillis() — the latter
			// is system UPTIME (VERIFIED in ofUtils.h's own doc comment),
			// not wall clock, and would make every skew reading nonsense.
			sendLine(tcp, {{"t", "hb"}, {"ts", ofGetUnixTimeMillis() / 1000.0}});
			nextBeat = now + (uint64_t)(kHeartbeatInterval * 1000.0f);
		}

		{
			std::vector<ofJson> pending;
			{
				std::lock_guard<std::mutex> lock(_outMx);
				pending.swap(_outQueue);
			}
			for(const auto & m : pending){
				sendLine(tcp, m);
			}
		}

		std::this_thread::sleep_for(std::chrono::milliseconds((int)(kTick * 1000.0f)));
	}

	_connected = false;
	tcp.close();
	ofLogNotice(kTag) << "control link to " << _host << ":" << _port << " closed";
	return true;   // reached a working link at all this attempt, even if it later dropped
}

void StateLink::threadLoop(){
	float backoff = kBackoffStart;
	while(!_stop.load()){
		bool everConnected = connectOnce();
		if(_stop.load()){
			return;
		}
		if(everConnected){
			backoff = kBackoffStart;   // was a working link, not a failing one — no penalty
			continue;
		}
		interruptibleSleep(backoff);
		backoff = std::min(backoff * 2.0f, kBackoffMax);
	}
}

bool StateLink::interruptibleSleep(float seconds){
	uint64_t deadline = nowMs() + (uint64_t)(seconds * 1000.0f);
	while(nowMs() < deadline){
		if(_stop.load()){
			return true;
		}
		std::this_thread::sleep_for(std::chrono::milliseconds(20));
	}
	return _stop.load();
}

bool StateLink::parseState(const ofJson & j, State & out){
	if(j.value("t", "") != "state"){
		return false;
	}
	out.seq = j.value("seq", (int64_t)-1);
	out.ts = j.value("ts", 0.0);
	out.mode = j.value("mode", "diner");
	out.locale = j.value("locale", "en");

	if(j.contains("fluid") && j["fluid"].is_object()){
		const ofJson & f = j["fluid"];
		out.fluid.style = f.value("style", "mala");
		out.fluid.enabled = f.value("enabled", false);
		out.fluid.intensity = f.value("intensity", 0.0f);
	}

	if(j.contains("total") && j["total"].is_object()){
		out.total.amount = j["total"].value("amount", 0.0);
		out.total.text = j["total"].value("text", "");
	}

	if(j.contains("overlay") && j["overlay"].is_object()){
		out.overlayKind = j["overlay"].value("kind", "none");
	}

	// doc §4.3: "bins always has exactly 8 entries." Trusted where it can
	// be — resized to 8 rather than left at whatever a malformed line sent,
	// so UiLayer's fixed 8-plate layout never has to bounds-check.
	out.bins.assign(8, Bin{});
	if(j.contains("bins") && j["bins"].is_array()){
		const auto & arr = j["bins"];
		for(size_t k = 0; k < arr.size() && k < 8; k++){
			const ofJson & bj = arr[k];
			if(!bj.is_object()){
				continue;
			}
			Bin b;
			b.i = bj.value("i", (int)k);
			b.label = bj.value("label", "");
			b.sub = bj.value("sub", "");
			b.grams = bj.value("grams", 0.0f);
			b.picked = bj.value("picked", 0.0f);
			b.price = bj.value("price", 0.0);
			b.hl = bj.value("hl", "none");
			b.stock = bj.value("stock", "ok");
			b.resolved = bj.value("resolved", false);
			int slot = (b.i >= 0 && b.i < 8) ? b.i : (int)k;
			out.bins[slot] = b;
		}
	}

	return true;
}
