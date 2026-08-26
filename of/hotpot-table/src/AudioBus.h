#pragma once

#include "ofSoundPlayer.h"

#include <string>
#include <unordered_map>

// v3 doc §15: "oF owns the audio device. Sound is presentation, core must
// never block on it... Core sends {"t":"evt","kind":"sound","id":"..."}
// and oF plays it." This is the player side of that.
//
// One ofSoundPlayer per id, loaded lazily from bin/data/audio/<id>.wav and
// kept for the app's lifetime — §15.2's set is short (a dozen ids) and all
// short clips, so there is no memory case for unloading one. `id` doubles
// as the file's relative path with no extension, which is exactly what
// §16.3 relies on for voice output ("tts/zh/order_ready... the SAME event
// type as any other sound... oF needs no new code path") — this class
// never special-cases a prefix, it just resolves whatever id it is given.
//
// **An id with no matching WAV on disk is not an error.** Doc §4.4 already
// calls `evt` fire-and-forget ("if oF misses one because it just
// restarted, nothing breaks"), and the same tolerance covers an id nobody
// has recorded yet, or one that isn't in §15.2's table at all — core sends
// `order_code` today (main.py) with no row in that table. Logged once per
// id, not once per play(), so a screen full of `pick_confirm` on a table
// with a placeholder set does not spam the console.
class AudioBus {
public:
	void setup();

	// Advances any loop currently easing to silence (see the fade-out note
	// on `setHandFireActive` below). Call once per frame, real `dt` — the
	// same raw `ofGetLastFrameTime()` ofApp::update() already keeps for UI
	// tweening, not FluidLayer's smoothed one; a fade is cosmetic, not a
	// sim, so a single long frame just makes that one fade step bigger,
	// never a driver worth smoothing further.
	void update(float dt);

	// One id at a time, the wire's own shape (doc §4.4's `evt`). `gain`
	// scales the file's own recorded level (§15.2's `hover` wants "-18
	// dB", carried as a gain in the 0..1 sense `ofSoundPlayer::setVolume`
	// already uses, not literal decibels). `speed` used to carry doc
	// §15.2's `dwell_tick` rising-pitch ladder; 2026-08-26 that sound is
	// gone outright (developer request: no dwell-progress sound at all),
	// so every caller today leaves it at the default and every clip
	// plays at its own recorded speed. Left as a parameter rather than
	// deleted — a future cue that wants a pitch change has somewhere to
	// plug into with no new plumbing.
	void play(const std::string & id, float gain = 1.0f, float speed = 1.0f);

	// Doc §15.2's `attract`, "idle loop, every 30s, almost inaudible
	// simmer bed, loopable." Driven every frame from `state.idleAttract`
	// (StateLink::State already carries that flag — doc §21's phantom-hand
	// idle rule) rather than a discrete evt: there is no "stop" shape in
	// §4.4's one-shot events to invent, so oF starts/stops the loop itself
	// off the same boolean it already reads to blank the idle UI.
	void setAttractActive(bool active);

	// 2026-08-26. The bin "burning" loop — doc's fire_start/fire_burning/
	// fire_stop set, developer request. `fire_burning` is the sustained
	// crackle for as long as a hand STAYS in a bin; `fire_start`/
	// `fire_stop` are the catch/put-out one-shots and arrive as ordinary
	// `evt`s through `play()` like any other cue (main.py sends them on
	// `self._hover_bin`'s own edges, replacing the old `hover` tick
	// outright). Same start/stop-on-the-edge shape as setAttractActive,
	// driven every frame off `state.fireActive` (`self._hover_bin is not
	// None`) rather than a discrete evt, for the identical reason: there
	// is no "stop" shape in §4.4's one-shot events, so oF starts/stops
	// the loop itself off the boolean it already reads.
	void setFireBurningActive(bool active);

	// 2026-08-26, developer request: the roaming fireball — the hand's
	// OWN cursor flame, drawn everywhere on the table (CursorLink's
	// `pointer()`), not just inside a bin — gets its own burning sound,
	// "little smaller than the bin fire which is very big". A SEPARATE
	// id/clip from the bin's own `fire_burning` voice so it can loop
	// concurrently with, not instead of, it — the two are independent
	// `ofSoundPlayer`s the same way the two flames are independent draws
	// (the cursor's own flame keeps following the hand even while it
	// sits inside a lit bin). Quieter via `kHandFireGain`, matching the
	// visual being smaller.
	//
	// 2026-08-26, developer request: `fire_burning_ambient`'s clip
	// swapped to a developer-supplied recording (`fire_burning_ambient.mp3`
	// — the bin's own `fire_burning.wav` is untouched, still its
	// original render). Not verified gapless: unlike the file it
	// replaced (a purpose-rendered seamless loop), this one has not been
	// checked for silence/padding at its boundaries, so `setLoop(true)`
	// may audibly click or gap at the seam. Trim it to a clean loop
	// point if that's heard on the rig.
	//
	// 2026-08-26, developer report: going inactive used to call
	// `ofSoundPlayer::stop()` straight away, which cuts the loop dead on
	// the current sample — audible as an abrupt chop the instant a hand
	// leaves the camera's view, not a fade. All three loops in this class
	// now ease out over `kLoopFadeOutS` instead (`fadeOutLoop`); only the
	// entry stays instant ("catches fire" the moment a hand/bin lights up
	// is still meant to be immediate — only going quiet is smoothed).
	void setHandFireActive(bool active);

private:
	struct Voice {
		ofSoundPlayer player;
		bool loaded = false;
		// Fade-out state, `startLoop`/`fadeOutLoop`/`update`'s own. Unused
		// by `play()`'s one-shots.
		float activeGain = 1.0f;   // volume once fully faded in
		bool fadingOut = false;
		float fadeElapsed = 0.0f;
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
};
