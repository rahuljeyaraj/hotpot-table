#include "UiLayer.h"
#include "TableGeometry.h"

#include <cctype>
#include <cmath>
#include <cstdio>

namespace {
	const char * kTag = "UiLayer";

	// Doc §13.4: "Load each font at its final display size... projected
	// text at 3x scale is mud." Inter (the doc's specified `en` face) is not
	// present anywhere in this repo or the oF distribution — checked both
	// before reaching for a substitute, not assumed absent. DejaVuSans-Bold
	// already ships in bin/data/fonts/ for the legacy bin labels this
	// rewrite deletes, and it already satisfies the harder rule underneath
	// the font choice ("dark ink on light field, set bold" — doc §13.4).
	// Swap this one line once the real font file exists.
	const std::string kFontFile = "fonts/DejaVuSans-Bold.ttf";

	// doc's old kBinOutlineMM/kLabelClearanceMM/kLabelLineGapMM (ofApp.cpp,
	// now deleted). Redefined here rather than resurrected in
	// TableGeometry.h, which v3 §7.1 keeps for CAD geometry only.
	const float kOutlineMM = 3.0f;
	const float kLabelClearanceMM = 10.0f;
	const float kLabelLineGapMM = 4.0f;

	void drawCentered(const ofTrueTypeFont & font, const std::string & text,
		float cx, float baselineY){
		if(text.empty() || !font.isLoaded()){
			return;
		}
		ofRectangle bb = font.getStringBoundingBox(text, 0, 0);
		font.drawString(text, cx - bb.width * 0.5f - bb.x, baselineY);
	}

	// Pulls the currency symbol and decimal count out of core's already-
	// resolved `total.text` (e.g. "\xE2\x82\xB9""41.20") instead of oF
	// hardcoding either. Doc I2: "oF does no lookup" — this is not a
	// lookup, it is reusing the one locale-resolved string the wire
	// already gives oF, so the same prefix can dress up the per-bin price
	// line below, which core sends as a bare number with no text of its
	// own to borrow from.
	void splitCurrencyText(const std::string & text, std::string & prefix, int & decimals){
		size_t i = 0;
		while(i < text.size() && !(std::isdigit((unsigned char)text[i]) || text[i] == '-')){
			i++;
		}
		prefix = text.substr(0, i);
		size_t dot = text.find('.', i);
		decimals = (dot == std::string::npos) ? 0 : (int)(text.size() - dot - 1);
	}

	std::string formatCurrency(double amount, const std::string & prefix, int decimals){
		char buf[32];
		snprintf(buf, sizeof(buf), "%.*f", decimals, amount);
		return prefix + buf;
	}
}

void UiLayer::setup(){
	bool ok = true;
	ok = _nameFont.load(kFontFile, 36) && ok;
	ok = _detailFont.load(kFontFile, 26) && ok;
	ok = _totalNumFont.load(kFontFile, 80) && ok;
	ok = _totalLabelFont.load(kFontFile, 28) && ok;
	ok = _devFont.load(kFontFile, 16) && ok;
	_fontsLoaded = ok;
	if(!_fontsLoaded){
		ofLogError(kTag) << "could not load " << kFontFile << " at one or more sizes"
			<< " — labels will not draw";
	}
}

ofRectangle UiLayer::binRectPx(int i){
	const BinRect & b = BINS[i];
	float x = mmToPxX(b.xMM);
	float y = mmToPxY(b.yMM);
	return ofRectangle(x, y, mmToPxX(b.xMM + b.wMM) - x, mmToPxY(b.yMM + b.hMM) - y);
}

std::vector<ofRectangle> UiLayer::cutoutRectsPx() const {
	std::vector<ofRectangle> out;
	out.reserve(BIN_COUNT);
	for(int i = 0; i < BIN_COUNT; i++){
		BinRect f = binFillRectMM(BINS[i]);
		float x = mmToPxX(f.xMM);
		float y = mmToPxY(f.yMM);
		out.emplace_back(x, y, mmToPxX(f.xMM + f.wMM) - x, mmToPxY(f.yMM + f.hMM) - y);
	}
	return out;
}

ofColor UiLayer::highlightColour(const std::string & hl){
	// Only "none" and "picked" are reachable from core in M1 — its
	// _bin_msg (verified against the actual code, not the doc's example)
	// sets hl to "picked" if picked>0 else "none". hover/picking need the
	// tracker (M5); lowstock needs a threshold nothing sets yet (doc §22,
	// P3). Mapped anyway, onto the pre-rewrite outline's equiluminant-on-
	// white palette (doc I8: hue carries the state, never brightness), so
	// the wire contract holds even though nothing exercises most of it yet.
	if(hl == "picked")   return ofColor(0, 115, 0);
	if(hl == "hover")    return ofColor(200, 0, 0);
	if(hl == "picking")  return ofColor(200, 120, 0);
	if(hl == "lowstock") return ofColor(190, 140, 0);
	if(hl == "disabled") return ofColor(190, 190, 190);
	return ofColor(98, 98, 98);   // "none"
}

void UiLayer::update(float dt, bool hasState, const StateLink::State & state){
	if(!hasState){
		return;
	}
	for(int i = 0; i < 8 && i < (int)state.bins.size(); i++){
		const StateLink::Bin & b = state.bins[i];
		BinTween & tw = _bins[i];

		tw.picked.setTarget(b.picked);
		tw.price.setTarget((float)b.price);

		ofColor target = highlightColour(b.hl);
		tw.colR.setTarget((float)target.r);
		tw.colG.setTarget((float)target.g);
		tw.colB.setTarget((float)target.b);

		// A pick is a discrete event, not something to spring into — snap
		// the scale UP the instant picked grows, then let the spring
		// relax it back to rest. Doc §13.3 tweens "plate scale" but leaves
		// what drives it unspecified; a pick is the one thing M1 actually
		// has to react to (core sends nothing else that varies per-bin).
		if(b.picked > tw.lastPicked + 0.5f){
			tw.scale.snapTo(1.06f);
		}
		tw.lastPicked = b.picked;
		tw.scale.setTarget(1.0f);

		tw.picked.update(dt);
		tw.price.update(dt);
		tw.scale.update(dt);
		tw.colR.update(dt);
		tw.colG.update(dt);
		tw.colB.update(dt);
	}
	_totalAmount.setTarget((float)state.total.amount);
	_totalAmount.update(dt);
}

void UiLayer::drawBin(int i, const StateLink::Bin & b, const BinTween & tw) const {
	ofRectangle box = binRectPx(i);

	ofRectangle scaled = box;
	float s = tw.scale.get();
	if(fabsf(s - 1.0f) > 0.0001f){
		glm::vec3 c = box.getCenter();   // ofRectangle::getCenter() returns vec3
		scaled.width *= s;
		scaled.height *= s;
		scaled.x = c.x - scaled.width * 0.5f;
		scaled.y = c.y - scaled.height * 0.5f;
	}

	ofColor outline((int)roundf(tw.colR.get()), (int)roundf(tw.colG.get()), (int)roundf(tw.colB.get()));
	ofPath path;
	path.setFilled(false);
	// ofPath+setStrokeWidth, never ofSetLineWidth (doc §13.4 VERIFY: Mesa
	// on Intel — the ODYSSEY's driver family — caps ofSetLineWidth at 1px).
	path.setStrokeWidth(mmToPxX(kOutlineMM));
	path.setColor(outline);
	path.rectangle(scaled);
	path.draw();
	ofSetColor(255);

	if(!b.resolved){
		return;   // doc §9.3: unresolved bins render with no label
	}

	const float cx = box.getCenter().x;
	const float clearance = mmToPxY(kLabelClearanceMM);
	const float gap = mmToPxY(kLabelLineGapMM);

	std::string detail = b.sub;
	if(b.picked > 0.5f){
		// doc §13.4 names one 26px row, "Bin weight / unit price" — read
		// literally as one line whose content switches, not two lines: the
		// unit price matters before a pick, the picked amount and its
		// running price matter after one.
		char g[16];
		snprintf(g, sizeof(g), "%dg", (int)roundf(tw.picked.get()));
		detail = std::string(g) + "  " + _priceText(tw.price.get());
	}

	if(i < 4){
		// far row: label strip is ABOVE the box, into the 177mm far margin
		float detailBaseline = box.y - clearance;
		drawCentered(_detailFont, detail, cx, detailBaseline);
		drawCentered(_nameFont, b.label, cx, detailBaseline - _detailFont.getLineHeight() - gap);
	}
	else {
		// near row: label strip is BELOW the box, into the 177.4mm near
		// margin — the diner's own side of the table.
		float nameBaseline = box.y + box.height + clearance + _nameFont.getAscenderHeight();
		drawCentered(_nameFont, b.label, cx, nameBaseline);
		drawCentered(_detailFont, detail, cx, nameBaseline + _detailFont.getLineHeight() + gap);
	}
}

std::string UiLayer::_priceText(double amount) const {
	return formatCurrency(amount, _currencyPrefix, _currencyDecimals);
}

void UiLayer::drawTotal(const StateLink::Total & total) const {
	// Centred over the pot gap, near its diner-facing edge (v3 §7.1's
	// "wide gap up the middle for the pot") — the one open span on the
	// table with no bin in it. There is nowhere better until a widget
	// rect exists to anchor to (M1 sends widgets:[] — doc §21 item 5/6).
	float cx = mmToPxX(TABLE_W_MM * 0.5f);
	float baselineY = mmToPxY(TABLE_H_MM) - mmToPxY(40.0f);

	std::string text = formatCurrency(_totalAmount.get(), _currencyPrefix, _currencyDecimals);
	drawCentered(_totalNumFont, text, cx, baselineY);

	// doc §13.4 reserves a "Total label" size (28px) for a translated
	// caption (e.g. "Total"/"总计"). Not drawn: core's `state.total` carries
	// only {amount, text}, no resolved label string (verified against
	// core/main.py — doc §21 M1 item 3 didn't add one), and I2 forbids oF
	// from supplying the English word itself. _totalLabelFont is loaded and
	// ready for the day a `total.label` field exists on the wire.
	(void)_totalLabelFont;
}

void UiLayer::drawConnectionIndicator(bool connected, float staleSeconds) const {
	// Doc §13.3: frozen state, never a black table, plus "a small
	// connection-lost indicator in a corner." This is diner-facing (drawn
	// on the table itself, not gated behind any dev flag) because a stalled
	// table with no explanation reads as broken, not paused.
	if(connected && staleSeconds <= 0.5f){
		return;
	}
	const float w = 1920.0f, h = 1080.0f;
	const float pad = 24.0f;
	ofSetColor(200, 0, 0);
	ofDrawCircle(w - pad - 8, pad + 8, 8);
	ofSetColor(60, 60, 60);
	_devFont.drawString("NO SIGNAL", w - pad - 118, pad + 14);
	ofSetColor(255);
}

void UiLayer::drawDevOverlay(bool hasState, const StateLink::State & state,
	bool connected, float fps) const {
	char buf[128];
	snprintf(buf, sizeof(buf), "fps %.0f  link %s  seq %lld",
		fps, connected ? "up" : "down", hasState ? (long long)state.seq : -1LL);
	ofSetColor(140, 140, 140);
	_devFont.drawString(buf, 16, 1080.0f - 16.0f);
	ofSetColor(255);
}

void UiLayer::draw(bool hasState, const StateLink::State & state,
	bool connected, float staleSeconds, float fps) const {
	if(!_fontsLoaded){
		drawConnectionIndicator(connected, staleSeconds);
		return;
	}

	if(hasState){
		// Once per frame, ahead of the bins: drawBin's price line and
		// drawTotal's numeral both format off this same prefix/decimals
		// pair, pulled from the one locale-resolved string the wire gives
		// oF (state.total.text) — see splitCurrencyText's comment.
		splitCurrencyText(state.total.text, _currencyPrefix, _currencyDecimals);
		for(int i = 0; i < 8 && i < (int)state.bins.size(); i++){
			drawBin(i, state.bins[i], _bins[i]);
		}
		drawTotal(state.total);
	}

	drawConnectionIndicator(connected, staleSeconds);
	drawDevOverlay(hasState, state, connected, fps);
}
