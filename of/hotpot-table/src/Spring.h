#pragma once

#include <cmath>

// v3 doc §13.3: "tweened toward the target from `state` with a critically
// damped spring (no overshoot, no oscillation) at roughly 150ms to settle."
//
// This is the exact closed-form solution of the critically damped harmonic
// oscillator, not an approximation of one. For x'' = -omega^2(x - target)
// - 2*omega*x', the repeated root at -omega gives, relative to a fixed
// target over one substep:
//
//   x_rel(t) = (x0 + (v0 + omega*x0)*t) * exp(-omega*t)
//   v_rel(t) = (v0 - omega*(v0 + omega*x0)*t) * exp(-omega*t)
//
// (v_rel is exactly d/dt of x_rel — differentiate the product rule and the
// omega*x0 terms cancel). Zero velocity and any positive omega therefore
// cannot overshoot: x_rel is a sum of decaying-exponential terms with no
// sign change available to produce one. That is what "critically damped"
// buys over a plain lerp — a lerp is this same shape with v0 forced to 0
// every frame, which is why a lerping number reads as jumping rather than
// moving (doc §13.3's rationale for the odometer over linear interpolation
// applies to the raw value too, not only to its digits).
//
// The only approximation here is the mapping from "roughly 150ms to
// settle" to omega. Critically damped step response is
// (1+omega*t)*exp(-omega*t); at omega*t = 6 that has decayed to
// 7*exp(-6) ≈ 1.7% of the initial error, which reads as "settled" on a
// projected table. omega = 6/settleSeconds is that rule of thumb, not a
// derived constant — matching the doc's own "roughly".
class Spring {
public:
	explicit Spring(float settleSeconds = 0.15f)
		: omega(6.0f / settleSeconds)
	{}

	// First call snaps rather than springing from zero — a bin that starts
	// at 500g must not visibly grow in from nothing the instant state
	// first arrives.
	void snapTo(float v){
		value = v;
		target = v;
		velocity = 0.0f;
		initialised = true;
	}

	void setTarget(float v){
		target = v;
		if(!initialised){
			snapTo(v);
		}
	}

	void update(float dt){
		if(!initialised || dt <= 0.0f){
			return;
		}
		const float x0 = value - target;
		const float expTerm = expf(-omega * dt);
		const float temp = (velocity + omega * x0) * dt;
		velocity = (velocity - omega * temp) * expTerm;
		value = target + (x0 + temp) * expTerm;
	}

	float get() const { return value; }

	// doc §13.3: "Numbers use an odometer roll, not a linear lerp." What
	// M1.4 actually builds is the SPRING half of that — the continuous,
	// non-overshooting value a rolling-digit renderer would consume. The
	// glyph-strip rendering itself (two clipped glyphs per digit cell,
	// sliding on the fractional part of (value/placeValue) mod 10 — the
	// trick that makes carries roll cleanly with no special-casing) needs
	// per-digit clipping this app has no precedent for yet (no shader, no
	// scissor use anywhere else in it), and M1's acceptance test (doc §21)
	// checks the settled NUMBER by arithmetic, not the roll animation. So
	// it is deferred rather than built on unverified clipping: get() below
	// is drawn as plain text for now, still critically damped, never
	// linear. Revisit once AudioBus (a later build item) needs the digit
	// boundaries anyway for `total_tick` (§15.3).

private:
	float omega;
	float value = 0.0f;
	float target = 0.0f;
	float velocity = 0.0f;
	bool initialised = false;
};
