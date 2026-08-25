#include "AudioBus.h"

#include "ofLog.h"

namespace {
	const char * kTag = "AudioBus";
	const char * kAudioDir = "audio/";

	// Doc §15.2's `attract`: "almost inaudible simmer bed." A fixed low
	// level rather than a config knob — nothing else in this table's
	// audio needs per-install tuning yet, and this is the one sound meant
	// to sit under everything else rather than call attention to itself.
	const float kAttractGain = 0.12f;
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
	v.loaded = v.player.load(std::string(kAudioDir) + id + ".wav");
	if(v.loaded){
		// One-shot default: overlapping triggers (dwell_tick's ladder, a
		// fast run of pick_confirm) stack rather than cutting each other
		// off. setAttractActive() below reconfigures both for the one id
		// that loops instead of one-shotting.
		v.player.setMultiPlay(true);
		v.player.setLoop(false);
	} else {
		ofLogWarning(kTag) << "no audio for id '" << id << "' (" << kAudioDir
			<< id << ".wav not found) - this event will play silence";
	}
	auto result = _voices.emplace(id, std::move(v));
	return result.first->second;
}

void AudioBus::play(const std::string & id, float gain, float speed){
	Voice & v = voiceFor(id);
	if(!v.loaded){
		return;   // logged once already, in voiceFor
	}
	v.player.setVolume(gain);
	v.player.setSpeed(speed);
	v.player.play();
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
		v.player.setLoop(true);
		v.player.setMultiPlay(false);
		v.player.setVolume(kAttractGain);
		v.player.play();
	} else {
		v.player.stop();
	}
}
