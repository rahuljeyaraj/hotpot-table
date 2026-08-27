#include "AudioBus.h"

#include "ofLog.h"
#include "ofMath.h"

namespace {
	const char * kTag = "AudioBus";
	const char * kAudioDir = "audio/";

	// Doc §15.2's `attract`: "almost inaudible simmer bed." A fixed low
	// level rather than a config knob — nothing else in this table's
	// audio needs per-install tuning yet, and this is the one sound meant
	// to sit under everything else rather than call attention to itself.
	const float kAttractGain = 0.12f;

	// 2026-08-26: the roaming fireball's own burning loop, quieter than
	// the bin's (`kFireBurningGain` below) — "little smaller", per the
	// developer, matching the visual size difference. Its clip
	// (`fire_burning_ambient.mp3`) is a separate, developer-supplied
	// recording as of 2026-08-26 — see AudioBus.h's note on it.
	const float kHandFireGain = 0.45f;
	const float kFireBurningGain = 1.0f;

	// 2026-08-26, developer report: a loop cut with a bare `stop()` the
	// instant its driving bool went false was audible as an abrupt chop
	// (worst on the roaming fireball's crackle, gone the moment a hand
	// left the camera's view). Half a second is enough to read as a fade
	// rather than a stop, short enough that a hand gone for good doesn't
	// leave an audible tail hanging around.
	const float kLoopFadeOutS = 0.5f;
}

void AudioBus::setup(){
	// Nothing to preload. Voices load lazily (voiceFor), which also means
	// an id core sends before its WAV has been recorded costs nothing at
	// startup — only a one-time warning the first time it is actually
	// asked for.
}

AudioBus::Voice & AudioBus::voiceFor(const std::string & id){
	auto it = _voices.find(id);
	if(it != _voices.end()){
		return it->second;
	}
	Voice v;
	// `id` doubles as the file's relative path with no extension (this
	// class's own top comment). Every clip recorded for this app so far
	// is a `.wav`, but 2026-08-26's `single_tap`/`double_tap` cues arrived
	// as `.mp3` from the developer — tried second, not converted, since
	// this app's actual Windows sound backend is Media Foundation
	// (`OF_NO_FMOD` is defined — see ofConstants.h — which routes
	// `ofSoundPlayer` to `ofMediaFoundationSoundPlayer`), and MF decodes
	// MP3 natively with no extra work.
	static const char * kExtensions[] = {".wav", ".mp3"};
	for(const char * ext : kExtensions){
		if(v.player.load(std::string(kAudioDir) + id + ext)){
			v.loaded = true;
			break;
		}
	}
	if(v.loaded){
		// One-shot default: overlapping triggers (a fast run of the same
		// confirmation tap) stack rather than cutting each other off.
		// setAttractActive() below reconfigures both for the one id that
		// loops instead of one-shotting.
		v.player.setMultiPlay(true);
		v.player.setLoop(false);
	} else {
		ofLogWarning(kTag) << "no audio for id '" << id << "' (" << kAudioDir
			<< id << ".wav or .mp3 not found) - this event will play silence";
	}
	auto result = _voices.emplace(id, std::move(v));
	return result.first->second;
}

void AudioBus::play(const std::string & id, float gain, float speed){
	Voice & v = voiceFor(id);
	if(!v.loaded){
		return;   // logged once already, in voiceFor
	}
	v.player.setVolume(gain * _masterVolume);
	v.player.setSpeed(speed);
	v.player.play();
}

void AudioBus::update(float dt){
	for(auto & entry : _voices){
		Voice & v = entry.second;
		if(!v.fadingOut){
			continue;
		}
		v.fadeElapsed += dt;
		if(v.fadeElapsed >= kLoopFadeOutS){
			// Reached silence: an actual stop(), same end state the old
			// instant cut left things in, just arrived at gradually.
			v.player.stop();
			v.fadingOut = false;
			continue;
		}
		float t = v.fadeElapsed / kLoopFadeOutS;
		v.player.setVolume(v.activeGain * _masterVolume * (1.0f - t));
	}
}

void AudioBus::startLoop(Voice & v, float gain){
	v.activeGain = gain;
	v.isLoop = true;
	v.fadingOut = false;
	if(!v.player.isPlaying()){
		v.player.setLoop(true);
		v.player.setMultiPlay(false);
		v.player.play();
	}
	// Set every time, not only on a fresh play(): this is also the "cancel
	// a fade in progress" path (hand back in view before kLoopFadeOutS
	// ran out), and the loop must jump straight back to full volume, not
	// resume climbing from wherever the fade had gotten to.
	v.player.setVolume(gain * _masterVolume);
}

void AudioBus::fadeOutLoop(Voice & v){
	if(v.fadingOut || !v.player.isPlaying()){
		return;   // already easing down, or already silent
	}
	v.fadingOut = true;
	v.fadeElapsed = 0.0f;
}

void AudioBus::setFireBurningActive(bool active){
	if(active == _fireBurningActive){
		return;   // state.fireActive repeats every tick (doc §4.3); only edges act
	}
	_fireBurningActive = active;

	Voice & v = voiceFor("fire_burning");
	if(!v.loaded){
		return;
	}
	if(active){
		startLoop(v, kFireBurningGain);
	} else {
		fadeOutLoop(v);
	}
}

void AudioBus::setHandFireActive(bool active){
	if(active == _handFireActive){
		return;   // driven every frame off CursorLink::pointer(); only edges act
	}
	_handFireActive = active;

	Voice & v = voiceFor("fire_burning_ambient");
	if(!v.loaded){
		return;
	}
	if(active){
		startLoop(v, kHandFireGain);
	} else {
		fadeOutLoop(v);
	}
}

void AudioBus::setMasterVolume(float volume01){
	_masterVolume = ofClamp(volume01, 0.0f, 1.0f);
	// Live loops react immediately rather than waiting for their next
	// start/fade edge — one-shots are left alone (a `play()` already in
	// flight keeps the gain it was triggered at, same as any other sound
	// mid-playback) since only `isLoop` voices are the sustained ones a
	// developer would actually hear the slider move on.
	for(auto & entry : _voices){
		Voice & v = entry.second;
		if(!v.isLoop || !v.player.isPlaying() || v.fadingOut){
			continue;
		}
		v.player.setVolume(v.activeGain * _masterVolume);
	}
}

void AudioBus::setAttractActive(bool active){
	if(active == _attractActive){
		return;   // state.idleAttract repeats every tick (doc §4.3); only edges act
	}
	_attractActive = active;

	Voice & v = voiceFor("attract");
	if(!v.loaded){
		return;
	}
	if(active){
		startLoop(v, kAttractGain);
	} else {
		fadeOutLoop(v);
	}
}
