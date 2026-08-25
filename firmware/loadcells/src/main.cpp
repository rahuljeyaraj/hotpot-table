// Load cell aggregator: 8x HX711 on a shared clock, raw counts to USB serial.
//
// Output, one line per conversion cycle:
//   raw <c0> <c1> <c2> <c3> <c4> <c5> <c6> <c7>
//
// Diagnostics are prefixed with '#' and are never confusable with data:
// core/scale.py's parse_line() accepts a line only if it is exactly the
// literal `raw` plus eight integers, and drops everything else silently.
//
// No tare, no scaling, no settled detection, no stored state. The link is
// one-directional; nothing is read from Serial.
//
// Why there is a heartbeat LED
// ----------------------------
// The XIAO's USB is the ESP32-S3's USB-Serial-JTAG peripheral, which lives in
// ROM and enumerates on power alone. A COM port therefore appears whether the
// sketch is running, hung, or crashed, and "the board is plugged in" says
// nothing about whether it is sending. The LED is the out-of-band answer:
//
//   solid on        still in setup() -- or setup() never finished
//   slow blink      ~1Hz, one toggle per 5 lines: cells read, data going out
//   triple flash    repeating: alive, but a cell is not reporting; see the
//                   '# fault:' lines for which one

#include <Arduino.h>
#include <HX711-multi.h>

static const int CELL_COUNT = 8;

static const byte SCK_PIN = D0;  // GPIO1, shared clock to all 8 HX711s

// One DT line per cell, index == bin number, matching BINS[] in
// src/TableGeometry.h: 0-3 far row left to right, 4-7 near row left to right.
// Indices are row-major across the whole table, so each island holds one pair
// from each row rather than a contiguous run:
//
//        left island        right island
//   far     0   1              2   3
//   near    4   5              6   7
//
// D3-D6 serve the left island, D7-D10 the right. The right island runs in
// descending pin order because D10..D7 sit top to bottom on that header, so
// the harness stays in physical order. D1 and D2 are unused; D2/GPIO3 is a
// strapping pin, sampled at reset.
static byte DT_PINS[CELL_COUNT] = {
    D3,   // bin 0, far left      GPIO4
    D4,   // bin 1, far c-left    GPIO5
    D10,  // bin 2, far c-right   GPIO9
    D9,   // bin 3, far right     GPIO8
    D5,   // bin 4, near left     GPIO6
    D6,   // bin 5, near c-left   GPIO43
    D8,   // bin 6, near c-right  GPIO7
    D7,   // bin 7, near right    GPIO44
};

// Printed alongside a fault so the message names the wire to go and check,
// not just the bin index. Same order as DT_PINS.
static const char *DT_LABELS[CELL_COUNT] = {
    "D3/GPIO4",  "D4/GPIO5",  "D10/GPIO9", "D9/GPIO8",
    "D5/GPIO6",  "D6/GPIO43", "D8/GPIO7",  "D7/GPIO44",
};

// The XIAO ESP32-S3's user LED sits between 3V3 and GPIO21, so the pin sinks
// it: LOW is lit.
#ifndef LED_BUILTIN
#define LED_BUILTIN 21
#endif
static const byte LED_PIN = LED_BUILTIN;
static const uint8_t LED_ON = LOW;
static const uint8_t LED_OFF = HIGH;

// An HX711 at its default 10 SPS converts every ~100ms. 400ms is four
// conversions of margin -- long enough never to trip on a healthy cell, short
// enough that a dead one is reported two and a half times a second rather than
// once. The slower first conversion after power-up is absorbed by the startup
// probe below, which has already waited before the first read is attempted.
static const uint32_t READY_TIMEOUT_MS = 400;

// One LED toggle per five lines. At the measured 10.7Hz that is a ~1.07Hz
// blink -- slow enough to read as a heartbeat rather than a flicker.
static const uint8_t HEARTBEAT_SAMPLES = 5;

// A fault repeats for as long as it lasts, but not at loop rate.
static const uint32_t FAULT_REPORT_MS = 2000;

// Heap-allocated so construction happens after Serial.begin(). The constructor
// performs a priming read, and until that read was given a timeout it could
// block forever, presenting as a dead device. See lib/HX711-multi/README.md.
static HX711MULTI *cells = NULL;

static bool ledLit = false;

static void ledSet(bool on) {
    ledLit = on;
    digitalWrite(LED_PIN, on ? LED_ON : LED_OFF);
}

static void ledFlashes(uint8_t times, uint16_t on_ms, uint16_t off_ms) {
    for (uint8_t i = 0; i < times; ++i) {
        ledSet(true);
        delay(on_ms);
        ledSet(false);
        delay(off_ms);
    }
}

// Why a silent line is silent. The HX711 drives DOUT push-pull, so it beats
// either internal pull resistor; if the line instead follows whichever pull is
// applied, nothing is driving it at all. That distinction is the difference
// between a wiring job and a board swap, so it is worth the four pinMode calls.
static const char *lineCharacter(byte pin) {
    pinMode(pin, INPUT_PULLUP);
    delayMicroseconds(50);
    int up = digitalRead(pin);
    pinMode(pin, INPUT_PULLDOWN);
    delayMicroseconds(50);
    int down = digitalRead(pin);
    pinMode(pin, INPUT);

    if (up == HIGH && down == LOW) {
        // Follows the pull both ways: open DT wire, or an HX711 with no power.
        return "floating";
    }
    if (up == HIGH && down == HIGH) {
        // Something is holding it high. The HX711 does that when it has no
        // conversion ready -- powered but not converting, or held in power-down
        // by a stuck-high PD_SCK on that board.
        return "driven-high";
    }
    if (up == LOW && down == LOW) {
        return "driven-low";
    }
    return "unstable";
}

// Prints "bin 1 D4/GPIO5 floating" for each set bit of `mask`, one per line.
static void printCellDiagnosis(uint32_t mask) {
    for (int i = 0; i < CELL_COUNT; ++i) {
        if (mask & (((uint32_t)1) << i)) {
            Serial.print("#   bin ");
            Serial.print(i);
            Serial.print(' ');
            Serial.print(DT_LABELS[i]);
            Serial.print(' ');
            Serial.println(lineCharacter(DT_PINS[i]));
        }
    }
}

// Prints "bins 2 5 (D10/GPIO9 D6/GPIO43)" for the set bits of `mask`.
static void printCellSet(uint32_t mask) {
    Serial.print("bins");
    for (int i = 0; i < CELL_COUNT; ++i) {
        if (mask & (((uint32_t)1) << i)) {
            Serial.print(' ');
            Serial.print(i);
        }
    }
    Serial.print(" (");
    bool first = true;
    for (int i = 0; i < CELL_COUNT; ++i) {
        if (mask & (((uint32_t)1) << i)) {
            if (!first) {
                Serial.print(' ');
            }
            Serial.print(DT_LABELS[i]);
            first = false;
        }
    }
    Serial.print(')');
}

// Watches every DT line for `window_ms` and returns a mask of the ones never
// seen low. A healthy HX711 pulls DOUT low at the end of each conversion and
// holds it there until clocked, and nothing clocks during the probe, so one
// conversion period is enough for a good cell to answer.
//
// The lines are pulled up for the duration: an unconnected pin floats and can
// read low by chance, which would report a missing cell as healthy. A real
// HX711 drives DOUT push-pull and walks over the internal pull-up easily, so
// this costs a good cell nothing and makes a missing one unambiguous.
static uint32_t probeDataLines(uint32_t window_ms) {
    uint32_t neverLow = 0;
    for (int i = 0; i < CELL_COUNT; ++i) {
        pinMode(DT_PINS[i], INPUT_PULLUP);
        neverLow |= ((uint32_t)1) << i;
    }
    delay(2);  // let the pull-ups settle before the first sample

    uint32_t start = millis();
    while (neverLow && (uint32_t)(millis() - start) < window_ms) {
        for (int i = 0; i < CELL_COUNT; ++i) {
            uint32_t bit = ((uint32_t)1) << i;
            if ((neverLow & bit) && digitalRead(DT_PINS[i]) == LOW) {
                neverLow &= ~bit;
            }
        }
        delay(1);
    }

    // Hand the pins back in the state HX711MULTI's constructor expects.
    for (int i = 0; i < CELL_COUNT; ++i) {
        pinMode(DT_PINS[i], INPUT);
    }
    return neverLow;
}

void setup() {
    pinMode(LED_PIN, OUTPUT);
    ledSet(true);  // solid until the first line goes out

    Serial.begin(115200);

    // Without this, a write with no host attached blocks on the CDC FIFO for
    // the default timeout on every line. The rig boots with nothing listening
    // more often than not; dropping those bytes is the correct trade.
    Serial.setTxTimeoutMs(0);

    // Give a host that is already open a moment to attach, so the banner and
    // the startup probe are not lost. Bounded, because usually nothing is
    // listening and the rig must come up regardless.
    uint32_t start = millis();
    while (!Serial && (uint32_t)(millis() - start) < 1500) {
        delay(10);
    }

    Serial.println();
    Serial.println("# hx711 loadcell aggregator, 8 cells, raw counts");
    Serial.print("# built ");
    Serial.print(__DATE__);
    Serial.print(' ');
    Serial.println(__TIME__);

    // Probe before constructing, so a stuck line is named rather than merely
    // timing out later. 500ms is five conversion periods.
    uint32_t silent = probeDataLines(500);
    if (silent == 0) {
        Serial.println("# probe: all 8 cells reporting");
    } else {
        Serial.print("# probe: no data line activity from ");
        printCellSet(silent);
        Serial.println();
        printCellDiagnosis(silent);
        Serial.println("# probe: floating = open DT wire or unpowered board;");
        Serial.println("# probe: driven-high = board powered but not converting");
    }

    cells = new HX711MULTI(CELL_COUNT, DT_PINS, SCK_PIN);
    cells->setReadyTimeout(READY_TIMEOUT_MS);

    Serial.println("# ready");
}

void loop() {
    static uint8_t beat = 0;
    static bool faulted = false;
    static uint32_t lastFaultReport = 0;

    long counts[CELL_COUNT];

    // readRaw() bypasses the library's tare offsets, which are never set here.
    // It returns false rather than hanging when a cell stops reporting, and
    // clocks nothing in that case -- so there is no half-read frame to print.
    if (!cells->readRaw(counts)) {
        uint32_t mask = cells->notReadyMask();

        if (!faulted || (uint32_t)(millis() - lastFaultReport) >= FAULT_REPORT_MS) {
            lastFaultReport = millis();
            Serial.print("# fault: not ready within ");
            Serial.print(READY_TIMEOUT_MS);
            Serial.print("ms, ");
            printCellSet(mask);
            Serial.println(" -- no data sent this cycle");
            // Repeated rather than printed once per episode: the fault usually
            // predates whoever opened the port, and it is what you watch while
            // reseating a connector.
            printCellDiagnosis(mask);
        }
        faulted = true;

        // Deliberately not the heartbeat pattern: the board is alive, so the
        // LED must not be dark, but nothing is being sent, so it must not look
        // like the ~1Hz blink either.
        ledFlashes(3, 40, 80);
        return;
    }

    if (faulted) {
        Serial.println("# ok: all 8 cells reporting again");
        faulted = false;
        beat = 0;
    }

    Serial.print("raw");
    for (int i = 0; i < CELL_COUNT; ++i) {
        Serial.print(' ');
        Serial.print(counts[i]);
    }
    Serial.println();

    // The heartbeat is driven by lines actually sent, not by a timer, so it
    // stops the moment the data does.
    if (++beat >= HEARTBEAT_SAMPLES) {
        beat = 0;
        ledSet(!ledLit);
    }
}
