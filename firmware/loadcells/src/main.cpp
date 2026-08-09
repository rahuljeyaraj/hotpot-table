// Load cell aggregator: 8x HX711 on a shared clock, raw counts to USB serial.
//
// Output, one line per conversion cycle:
//   raw <c0> <c1> <c2> <c3> <c4> <c5> <c6> <c7>
//
// No tare, no scaling, no settled detection, no stored state. The link is
// one-directional; nothing is read from Serial.

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

// Heap-allocated so construction happens after Serial.begin(). The constructor
// performs a priming read that blocks until every DT line is low, so a global
// would stall ahead of USB enumeration and present as a dead device.
static HX711MULTI *cells = NULL;

void setup() {
    Serial.begin(115200);
    cells = new HX711MULTI(CELL_COUNT, DT_PINS, SCK_PIN);
}

void loop() {
    long counts[CELL_COUNT];

    // readRaw() bypasses the library's tare offsets, which are never set here.
    cells->readRaw(counts);

    Serial.print("raw");
    for (int i = 0; i < CELL_COUNT; ++i) {
        Serial.print(' ');
        Serial.print(counts[i]);
    }
    Serial.println();
}
