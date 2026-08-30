#pragma once

#include "ofSoundPlayer.h"

#include <string>
#include <unordered_map>

// doc §15: oF owns the audio device, sound is presentation, and core must
// never block on it. Core sends {"t":"evt","kind":"sound","id":"..."} and oF
// plays it. This is the player side of that.
//
// One ofSoundPlayer per id, loaded lazily from bin/data/audio/<id>.wav and
// kept for the app's lifetime — §15.2's set is a dozen short clips, so there
// is no memory case for unloading one. `id` doubles as the file's relative
// path without the extension, which is what §16.3's voice output relies on
// ("tts/zh/order_ready" is the same event type as any other sound): this
// class never special-cases a prefix, it resolves whatever id it is given.
//
// An id with no matching WAV on disk is NOT an error. Doc §4.4 calls `evt`
// fire-and-forget, and the same tolerance covers an id nobody has recorded
// yet or one absent from §15.2's table altogether — core sends `order_code`
// with no row in that table. The warning is logged once per id rather than
// once per play(), so a placeholder sound set does not spam the console.
class AudioBus {
public:
	void setup();

	// Advances any loop currently easing to silence (see the fade-out note
	// on `setHandFireActive` below). Call once per frame with the raw
	// `ofGetLastFrameTime()` ofApp::update() keeps for UI tweening, not
	// FluidLayer's smoothed dt: a fade is cosmetic rather than a simulation,
	// so a single long frame merely makes that one fade step bigger.
	void update(float dt);

	// One id at a time, the wire's own shape (doc §4.4's `evt`). `gain`
	// scales the file's recorded level — §15.2's `hover` wants "-18 dB",
	// carried as a gain in the 0..1 sense `ofSoundPlayer::setVolume` uses,
	// not as literal decibels. There is no dwell-progress sound, so every
	// caller leaves `speed` at the default and every clip plays at its own
	// recorded rate; the parameter stays so a future cue wanting a pitch
	// change has somewhere to plug in.
	void play(const std::string & id, float gain = 1.0f, float speed = 1.0f);

	// Doc §15.2's `attract`: an idle loop, an almost inaudible simmer bed.
	// Driven every frame from `state.idleAttract`, which StateLink::State
	// already carries, rather than from a discrete evt — §4.4's one-shot
	// events have no "stop" shape, so oF starts and stops the loop itself
	// off the same boolean it already reads to blank the idle UI.
	void setAttractActive(bool active);

	// The bin "burning" loop, one of the fire_start/fire_burning/fire_stop
	// set. `fire_burning` is the sustained crackle for as long as a hand
	// STAYS in a bin; `fire_start` and `fire_stop` are the catch and put-out
	// one-shots, which arrive as ordinary `evt`s through `play()` like any
	// other cue, sent by core on `self._hover_bin`'s edges. Driven every
	// frame off `state.fireActive` rather than a discrete evt, for the same
	// reason as setAttractActive: §4.4's one-shot events have no "stop"
	// shape, so oF starts and stops the loop itself off the boolean.
	void setFireBurningActive(bool active);

	// The roaming fireball's loop: the hand's OWN cursor flame, which is
	// drawn everywhere on the table (CursorLink's `pointer()`), not only
	// inside a bin, and is quieter than the bin fire via `kHandFireGain` to
	// match its smaller visual.
	//
	// A SEPARATE id and clip from the bin's `fire_burning` voice, so the two
	// loop concurrently rather than one replacing the other — they are
	// independent `ofSoundPlayer`s the same way the two flames are
	// independent draws, and the cursor's flame keeps following the hand even
	// while it sits inside a lit bin.
	//
	// `fire_burning_ambient.mp3` is NOT verified gapless: it has not been
	// checked for silence or padding at its boundaries, so `setLoop(true)`
	// may audibly click or gap at the seam. Trim it to a clean loop point if
	// that is heard on the rig.
	//
	// Going inactive eases out over `kLoopFadeOutS` (`fadeOutLoop`) rather
	// than calling `ofSoundPlayer::stop()`, which would cut the loop dead on
	// the current sample and chop audibly the instant a hand leaves the
	// camera's view. All three loops in this class fade out this way. Only
	// going quiet is smoothed: catching fire stays instant.
	void setHandFireActive(bool active);

	// Developer volume slider (UiLayer's dev overlay). `volume01` is
	// clamped to 0..1 and multiplies every gain this class plays at from
	// here on — one-shots (`play()`) and loops alike. Applied immediately
	// to any loop already sounding, not just the next start/fade edge, so
	// dragging the slider while `attract`/`fire_burning`/`fire_burning_
	// ambient` is playing is heard live rather than on the next edge.
	void setMasterVolume(float volume01);
	float getMasterVolume() const { return _masterVolume; }

private:
	struct Voice {
		ofSoundPlayer player;
		bool loaded = false;
		// Fade-out state, `startLoop`/`fadeOutLoop`/`update`'s own. Unused
		// by `play()`'s one-shots.
		float activeGain = 1.0f;   // volume once fully faded in, before master
		bool fadingOut = false;
		float fadeElapsed = 0.0f;
		bool isLoop = false;   // set by startLoop; distinguishes from a one-shot Voice
	};

	// Starts (or resumes) a looping voice at `gain`. Does not restart
	// playback if the voice is already sounding — including mid fade-out
	// — so a hand that leaves and comes straight back just cancels the
	// fade and glides the volume back up rather than clicking to a dead
	// stop and restarting from sample 0.
	void startLoop(Voice & v, float gain);

	// Begins easing `v` to silence over `kLoopFadeOutS`; `update()` does
	// the actual stepping and calls `player.stop()` once it reaches zero.
	// A no-op if `v` isn't sounding or is already fading — the fade is
	// idempotent the same way `setFireBurningActive` etc. already only
	// act on an edge.
	void fadeOutLoop(Voice & v);

	// Loads on first use (logging a "no such file" warning exactly once
	// per id, since the Voice — and so the warning — is cached either
	// way). A Voice for an unloadable id just silently declines to play,
	// matching the class comment above.
	Voice & voiceFor(const std::string & id);

	std::unordered_map<std::string, Voice> _voices;
	bool _attractActive = false;
	bool _fireBurningActive = false;
	bool _handFireActive = false;
	float _masterVolume = 1.0f;
};
