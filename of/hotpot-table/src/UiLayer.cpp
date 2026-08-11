#include "UiLayer.h"
#include "TableGeometry.h"

#include <cctype>
#include <cmath>
#include <cstdio>
#include <sstream>

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
	const float kLabelClearanceMM = 10.0f;
	const float kLabelLineGapMM = 4.0f;

	// The plate ring: the band of colour that frames a bin's cutout and is
	// the ONLY thing on the table carrying doc §4.3's `hl` state (I8 —
	// "distinguish states by hue"). It replaces a 3mm outline stroked on
	// the bin edge, which could never be seen: the light pass (§13.2)
	// stamps opaque white over the cutout — the bin grown by
	// CUTOUT_MARGIN_MM — last, and that patch reached 10mm past the bin
	// edge while the stroke reached 1.5mm, so every frame drew the
	// highlight and then buried it. The ring now sits OUTSIDE the cutout,
	// which is also the treatment doc §14.4 already specifies for the one
	// other per-bin decoration it describes ("an annulus outside the
	// cutout, never over it (I9)").
	//
	// 6mm rather than the old 3mm because the ring's job changed. A stroke
	// on a bin edge was an outline; this is the state channel, and it has
	// to read at the distance its own label reads at while sitting on a
	// near-white field (§13.2's floor lift takes even a full-chroma hue to
	// a mid tone). 6mm is roughly the stem weight of the 36px bold bin
	// name above it, which is §13.4's own answer to the same problem
	// ("contrast has to come from stroke width"). One constant to change.
	const float kRingMM = 6.0f;

	// doc §13.4: "Dark ink on a light field, and set bold" — the field is
	// near-white by construction (I9's white floor), so text has to win on
	// stroke weight, not brightness. Near-black rather than pure 0,0,0: full
	// black on this bold a face at these sizes reads harsher than the doc's
	// own comparison point (dark plates on the pre-rewrite app).
	const ofColor kInkColor(20, 20, 20);

	// M2 doc §21 acceptance test's "fault overlay" (`state.overlay.kind ==
	// "error"`, set by core/main.py's _overlay_msg when a bin that was
	// billing off real weight goes dark — doc §9.5's "no billing occurs
	// from the frozen reading"). Reuses the staff view's own fault palette
	// (web/static/index.html's --red #e05d5d, and the dark-red-on-red ink
	// its red pip already uses, #2a0000) so the same failure reads the same
	// way on both surfaces instead of inventing a second "red" for this
	// table.
	const ofColor kErrorBannerFill(224, 93, 93);   // #e05d5d
	const ofColor kErrorBannerInk(42, 0, 0);       // #2a0000

	// M2.6: setting mode's banner (doc §14.5, "a persistent banner strip
	// along the top edge"). Amber for the same reason the error banner is
	// red — it is the staff view's own --amber (#e8b33d) and the ink its
	// amber pip already uses (#2a1f00), so the header chip on the tablet
	// and the strip on the table are visibly the same statement. I8: modes
	// are distinguished by HUE, never by brightness, and this hue is
	// luminance-matched to the red one rather than being brighter or
	// dimmer than it.
	//
	// The rest of doc §14.5 — fluid off, amber chrome throughout, the
	// 100mm grid — stays M8 build item 6. Most of it is a no-op today
	// since no fluid exists. The banner alone is what makes this
	// milestone's acceptance test visible from three metres.
	const ofColor kSettingBannerFill(232, 179, 61);   // #e8b33d
	const ofColor kSettingBannerInk(42, 31, 0);       // #2a1f00

	// The banner panel. Height is in px, not mm, because it is sized to
	// the two font sizes it holds rather than to anything physical.
	// The inset is the breathing room from the pot-gap edges — see
	// drawBanner for why the panel lives in that gap at all.
	const float kBannerHeightPx = 104.0f;
	const float kBannerInsetMM = 10.0f;

	void drawCentered(const ofTrueTypeFont & font, const std::string & text,
		float cx, float baselineY){
		if(text.empty() || !font.isLoaded()){
			return;
		}
		ofRectangle bb = font.getStringBoundingBox(text, 0, 0);
		font.drawString(text, cx - bb.width * 0.5f - bb.x, baselineY);
	}

	// Bin item names (e.g. "Curly Noodles") can render wider than the 250mm
	// gap between two bin centres in the same pair at doc §13.4's fixed 36px
	// — that overflowed into the neighbour's label before this existed.
	// Greedy word-wrap to at most 2 lines instead of shrinking the font,
	// which would abandon the doc's px value. A single word wider than
	// maxWidthPx on its own is still returned whole — this never breaks
	// mid-word, matching how nothing else in this file does character-level
	// layout.
	std::vector<std::string> wrapNameToTwoLines(const ofTrueTypeFont & font,
		const std::string & text, float maxWidthPx){
		if(!font.isLoaded() || font.getStringBoundingBox(text, 0, 0).width <= maxWidthPx){
			return {text};
		}
		std::vector<std::string> words;
		std::istringstream iss(text);
		std::string w;
		while(iss >> w){
			words.push_back(w);
		}
		if(words.empty()){
			return {text};
		}
		std::string line1;
		size_t i = 0;
		for(; i < words.size(); i++){
			std::string candidate = line1.empty() ? words[i] : line1 + " " + words[i];
			if(!line1.empty() && font.getStringBoundingBox(candidate, 0, 0).width > maxWidthPx){
				break;
			}
			line1 = candidate;
		}
		if(line1.empty()){
			line1 = words[i++];   // one overlong word — take it anyway, never emit an empty line
		}
		std::string line2;
		for(; i < words.size(); i++){
			line2 = line2.empty() ? words[i] : line2 + " " + words[i];
		}
		if(line2.empty()){
			return {line1};
		}
		return {line1, line2};
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

namespace {
	// The plain font.load(file, size) overload only requests
	// ofUnicode::Latin — ASCII 32-127 — so ₹ (U+20B9) silently has no glyph
	// and drawString skips it, even though core sends it correctly (doc
	// §17.2 currency text is "<symbol><amount>"). Latin1Supplement adds the
	// yen/yuan sign and other Latin-1 symbols for the same reason, cheaply,
	// ahead of any locale actually needing them.
	bool loadUiFont(ofTrueTypeFont & font, const std::string & file, int size){
		ofTrueTypeFontSettings settings(file, size);
		settings.ranges = {ofUnicode::Latin1Supplement, ofUnicode::CurrencySymbols};
		return font.load(settings);
	}
}

void UiLayer::setup(){
	bool ok = true;
	ok = loadUiFont(_nameFont, kFontFile, 36) && ok;
	ok = loadUiFont(_detailFont, kFontFile, 26) && ok;
	ok = loadUiFont(_totalNumFont, kFontFile, 80) && ok;
	ok = loadUiFont(_totalLabelFont, kFontFile, 28) && ok;
	ok = loadUiFont(_devFont, kFontFile, 16) && ok;
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

ofRectangle UiLayer::cutoutRectPx(int i){
	BinRect f = binFillRectMM(BINS[i]);
	float x = mmToPxX(f.xMM);
	float y = mmToPxY(f.yMM);
	return ofRectangle(x, y, mmToPxX(f.xMM + f.wMM) - x, mmToPxY(f.yMM + f.hMM) - y);
}

std::vector<ofRectangle> UiLayer::cutoutRectsPx() const {
	// Built from the same cutoutRectPx() drawBin() frames its ring against,
	// so the ring and the light pass cannot drift apart — see UiLayer.h.
	std::vector<ofRectangle> out;
	out.reserve(BIN_COUNT);
	for(int i = 0; i < BIN_COUNT; i++){
		out.push_back(cutoutRectPx(i));
	}
	return out;
}

void UiLayer::drawRing(const ofRectangle & cut, float widthX, float widthY,
	const ofColor & colour){
	// Four filled bars, not a stroked path. VERIFIED in the installed oF
	// rather than assumed, because assuming is what put the ring under the
	// light pass in the first place: an unfilled ofPath is drawn by
	// ofGLRenderer::draw(const ofPath&), which calls
	// setLineWidth(shape.getStrokeWidth()) -> glLineWidth(). So
	// ofPath::setStrokeWidth() IS ofSetLineWidth(), the exact call doc
	// §13.4 says never to use because Mesa on Intel caps it at 1px — and
	// on the programmable renderer (which M8's fluid will force this app
	// onto) that glLineWidth call is commented out entirely, so the width
	// is ignored outright. §13.4 has been corrected to say so.
	//
	// Top and bottom span the full outer width so the corners are covered
	// once each; left and right fill only the gap between them. Nothing
	// overlaps, so this stays correct if the colour ever carries alpha.
	ofSetColor(colour);
	ofDrawRectangle(cut.x - widthX, cut.y - widthY,
		cut.width + 2.0f * widthX, widthY);                       // top
	ofDrawRectangle(cut.x - widthX, cut.y + cut.height,
		cut.width + 2.0f * widthX, widthY);                       // bottom
	ofDrawRectangle(cut.x - widthX, cut.y, widthX, cut.height);   // left
	ofDrawRectangle(cut.x + cut.width, cut.y, widthX, cut.height);// right
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
		//
		// 1.6, not the 1.06 this was while it scaled a rectangle: the
		// value now drives ring THICKNESS (drawBin), and 6% of a 6mm band
		// is a third of a millimetre on the table — a pulse nobody could
		// see. 1.6 is ~3.5mm of extra ring for the ~150ms the spring takes
		// to relax, which reads as the plate acknowledging the pick.
		if(b.picked > tw.lastPicked + 0.5f){
			tw.scale.snapTo(1.6f);
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
	ofRectangle cut = cutoutRectPx(i);

	// The pop thickens the ring outward instead of scaling a rectangle.
	// Doc §13.3 asks for a tweened "plate scale", but the plate is now a
	// frame around a hole in the table, and a physical hole cannot grow —
	// scaling the frame would just slide it off the cutout it belongs to.
	// Growing outward from a fixed inner edge is the same gesture with the
	// one degree of freedom the geometry actually has.
	const float s = tw.scale.get();
	const float ringX = mmToPxX(kRingMM) * s;
	const float ringY = mmToPxY(kRingMM) * s;

	ofColor ring((int)roundf(tw.colR.get()), (int)roundf(tw.colG.get()),
		(int)roundf(tw.colB.get()));
	drawRing(cut, ringX, ringY, ring);

	// Name/detail text drawn below inherits the draw colour (drawCentered
	// sets none of its own) — must be the doc §13.4 ink colour, not the
	// ring's and not white, or it disappears into the white field.
	ofSetColor(kInkColor);

	if(!b.resolved){
		return;   // doc §9.3: unresolved bins render with no label
	}

	const float cx = box.getCenter().x;
	const float clearance = mmToPxY(kLabelClearanceMM);
	const float gap = mmToPxY(kLabelLineGapMM);

	// Labels clear the ring at its RESTING width, never the popped one —
	// anchoring them to the animation would twitch every label on every
	// pick. And they clear the ring rather than the bin box, which is the
	// bug this replaces: kLabelClearanceMM and CUTOUT_MARGIN_MM are both
	// 10mm, so measuring from box.y put the far row's baseline exactly on
	// the cutout's edge and the light pass ate every descender — the "g"
	// in both "45g" and "₹18.00/100g".
	const float ringRestY = mmToPxY(kRingMM);
	const float ringTop = cut.y - ringRestY;
	const float ringBottom = cut.y + cut.height + ringRestY;
	// Negative for descenders below the baseline, per ofTrueTypeFont.h's
	// own doc comment (it is FreeType's face->descender). fabsf rather
	// than negation so a font that reported it the other way still clears.
	const float descend = fabsf(_detailFont.getDescenderHeight());

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

	// Wrap to the bin's own footprint, not the gap to its neighbour — the
	// neighbour gap (250mm) is wider, but wrapping to the box the label
	// sits over keeps every name visually inside its own plate.
	std::vector<std::string> nameLines = wrapNameToTwoLines(_nameFont, b.label, mmToPxX(BIN_W_MM));
	const float nameLineGap = 2.0f;   // px between a name's own wrapped lines, tighter than kLabelLineGapMM's block-to-block gap

	if(i < 4){
		// far row: label strip is ABOVE the ring, into the 177mm far margin
		float detailBaseline = ringTop - clearance - descend;
		drawCentered(_detailFont, detail, cx, detailBaseline);
		// nameLines.back() sits closest to detail; earlier lines stack upward.
		float lastLineBaseline = detailBaseline - _detailFont.getLineHeight() - gap;
		for(int li = (int)nameLines.size() - 1; li >= 0; li--){
			float y = lastLineBaseline - (float)(nameLines.size() - 1 - li) * (_nameFont.getLineHeight() + nameLineGap);
			drawCentered(_nameFont, nameLines[li], cx, y);
		}
	}
	else {
		// near row: label strip is BELOW the ring, into the 177.4mm near
		// margin — the diner's own side of the table.
		float firstLineBaseline = ringBottom + clearance + _nameFont.getAscenderHeight();
		float lastLineBaseline = firstLineBaseline;
		for(size_t li = 0; li < nameLines.size(); li++){
			float y = firstLineBaseline + (float)li * (_nameFont.getLineHeight() + nameLineGap);
			drawCentered(_nameFont, nameLines[li], cx, y);
			lastLineBaseline = y;
		}
		drawCentered(_detailFont, detail, cx, lastLineBaseline + _detailFont.getLineHeight() + gap);
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
	ofSetColor(kInkColor);
	drawCentered(_totalNumFont, text, cx, baselineY);

	// doc §13.4's 28px "Total label" caption — core now resolves it
	// per-locale (data/locales/<locale>.json's "total" key) and puts it on
	// `total.label`; oF only draws whatever string arrives (I2: no lookup
	// here), so this reads correctly whichever locale core is set to,
	// currency included, with no oF-side change needed when zh.json lands.
	// Empty on an older core (StateLink defaults it to "") — draws nothing
	// rather than a placeholder, same rule drawCentered already applies
	// everywhere else.
	if(!total.label.empty()){
		float labelBaseline = baselineY - _totalNumFont.getAscenderHeight() - mmToPxY(kLabelLineGapMM);
		drawCentered(_totalLabelFont, total.label, cx, labelBaseline);
	}
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

void UiLayer::drawBanner(const ofColor & fill, const ofColor & ink,
	const std::string & headline, const std::string & subline) const {
	// Doc §14.5's pattern for naming a persistent, whole-table state
	// loudly without touching the light field. Built for `overlay.kind ==
	// "error"` at M2 and generalised at M2.6, when setting mode became the
	// second thing that needed exactly this — same panel, different hue
	// and words.
	//
	// **NOT a full-width strip along the top edge, which is what §14.5
	// literally said and what this drew until it was seen on the table.**
	// The far row's labels are drawn ABOVE their rings, upward into the
	// 177mm far margin: a two-line wrapped name (which several catalogue
	// names are, at 36px in a 200mm box) puts ink as high as ~50px, and a
	// 72px full-width strip covered it. Staff have to READ those names to
	// confirm which tray is which — during setting mode above all, which
	// is exactly when this banner is up. Covering them defeated the mode.
	//
	// So the panel is confined to the centre column: the span between
	// bin 1's right edge and bin 2's left edge, which TableGeometry.h
	// calls "a wide gap up the middle for the pot" and which is the one
	// horizontal span on the table with no bin and no label in it, by
	// construction. Derived from BINS rather than hardcoded, so moving a
	// bin moves the panel with it.
	//
	// Being narrower, it is taller and two-line instead — a ~440x88mm
	// amber block is still unmistakable from three metres, which was the
	// actual goal, and the strip shape was only ever one way to get there.
	//
	// Stage's light pass runs after UiLayer and re-stamps every cutout
	// white regardless of what this draws (doc §13.2's "any overlay added
	// later" safety property), so this can never darken a bin patch.
	//
	// Bins and the total keep drawing underneath (draw() calls this after
	// them, not instead of them) — doc §13.3's rule for a dead core link
	// applies just as well to a dead scale link: "It does not black out —
	// a frozen table is far better... than a dead one."
	const float gapLeftMM = BINS[1].xMM + BINS[1].wMM;
	const float gapRightMM = BINS[2].xMM;
	const float insetPx = mmToPxX(kBannerInsetMM);
	const float x = mmToPxX(gapLeftMM) + insetPx;
	const float w = mmToPxX(gapRightMM - gapLeftMM) - 2.0f * insetPx;
	const float h = kBannerHeightPx;
	const float cx = x + w * 0.5f;

	ofSetColor(fill);
	ofDrawRectangle(x, 0.0f, w, h);

	ofSetColor(ink);
	// Two lines, centred as a block: the headline is what a diner reads
	// from across the room and says nothing about modes or billing; the
	// subline is the operator's word for which state it is. Both audiences
	// are looking at the same table (doc §12.1's no-jargon rule applies
	// here more than anywhere — this surface has no operator filter on it).
	const float lineGap = 8.0f;
	const float blockH = _nameFont.getAscenderHeight() + lineGap
		+ _detailFont.getAscenderHeight();
	const float top = (h - blockH) * 0.5f;
	drawCentered(_nameFont, headline, cx, top + _nameFont.getAscenderHeight());
	drawCentered(_detailFont, subline, cx, h - top);
	ofSetColor(255);
}

void UiLayer::drawTopBanner(const StateLink::State & state) const {
	// **Precedence: SETTING wins over error.** Both claim the same strip
	// and both can be true at once — in setting mode, someone knocks the
	// XIAO cable out. Nothing bills in setting mode (core's
	// _apply_scale_to_cart returns immediately there), so
	// "SCALES OFFLINE — NOT BILLING" would be warning about a risk that
	// cannot occur, while displacing the one message that is true. The
	// person doing setting-mode work is holding the tablet, and the staff
	// view's Bins tab already shows "Load cells: no connection"; this
	// strip is for everyone *not* holding the tablet.
	//
	// This is the general rule for the strip, established here because
	// `calibrating` (M4) and `recap`/`qr` (M6) will each ask the same
	// question: the state that changes what the table is DOING outranks
	// the state that reports a fault in a subsystem that state has
	// already disabled.
	//
	// English only, matching UiLayer's current scope. Doc §14.5 pairs the
	// banner with a Chinese string; zh locale data does not exist yet (M1
	// is English-only end to end) and §17.3 is explicit that Chinese
	// judges will read this, so the zh text must be confirmed by a native
	// speaker before it ships rather than guessed at here.
	// Both banners lead with the SAME headline, deliberately. "NOT
	// SERVING" is the only part a diner needs, and it is equally true of
	// both states; which one it is only matters to the operator, who gets
	// it from the subline and from the hue (I8). The old wording said
	// "NOT BILLING" — an internal word for an external surface, and the
	// system's own second word for the same idea. There is one word now,
	// "serving", and it is the one a diner already understands.
	if(state.mode == "setting"){
		drawBanner(kSettingBannerFill, kSettingBannerInk,
			"NOT SERVING", "setting the table");
		return;
	}
	if(state.overlayKind == "error"){
		drawBanner(kErrorBannerFill, kErrorBannerInk,
			"NOT SERVING", "scales offline");
	}
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
	bool connected, float staleSeconds, float fps, bool showDevOverlay) const {
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
		drawTopBanner(state);
	}

	drawConnectionIndicator(connected, staleSeconds);
	if(showDevOverlay){
		drawDevOverlay(hasState, state, connected, fps);
	}
}
