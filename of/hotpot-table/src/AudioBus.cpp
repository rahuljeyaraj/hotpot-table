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

	// The roaming fireball's burning loop, deliberately quieter than the
	// bin's (`kFireBurningGain` below) to match the visual size difference.
	// Its clip is a separate recording — see AudioBus.h's note on it.
	const float kHandFireGain = 0.45f;
	const float kFireBurningGain = 1.0f;

	// A loop cut with a bare `stop()` the instant its driving bool goes
	// false is audible as an abrupt chop — worst on the roaming fireball's
	// crackle, which ends the moment a hand leaves the camera's view. Half a
	// second reads as a fade rather than a stop, and is short enough that a
	// hand gone for good leaves no audible tail.
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
	// `id` doubles as the file's relative path without the extension (see
	// this class's top comment). Most clips are `.wav`; `.mp3` is tried
	// second rather than converting those files, because this app's Windows
	// sound backend is Media Foundation — `OF_NO_FMOD` is defined, which
	// routes `ofSoundPlayer` to `ofMediaFoundationSoundPlayer` — and Media
	// Foundation decodes MP3 natively.
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
	// Live loops react immediately rather than waiting for their next start
	// or fade edge. One-shots are left alone — a `play()` already in flight
	// keeps the gain it was triggered at, like any other sound mid-playback
	// — since only `isLoop` voices sustain long enough to hear the change.
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
