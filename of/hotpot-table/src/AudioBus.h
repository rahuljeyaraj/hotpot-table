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

	// One id at a time, the wire's own shape (doc §4.4's `evt`). `gain`
	// scales the file's own recorded level (§15.2's `hover` wants "-18
	// dB", carried as a gain in the 0..1 sense `ofSoundPlayer::setVolume`
	// already uses, not literal decibels). `speed` is doc §15.2's
	// "pick_confirm ... pitch shifted by grams" — the grams-to-speed
	// mapping is ofApp's call (it owns the wire's `grams` field), this
	// just plays at whatever speed it is handed.
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

private:
	struct Voice {
		ofSoundPlayer player;
		bool loaded = false;
	};

	// Loads on first use (logging a "no such file" warning exactly once
	// per id, since the Voice — and so the warning — is cached either
	// way). A Voice for an unloadable id just silently declines to play,
	// matching the class comment above.
	Voice & voiceFor(const std::string & id);

	std::unordered_map<std::string, Voice> _voices;
	bool _attractActive = false;
	bool _fireBurningActive = false;
};
