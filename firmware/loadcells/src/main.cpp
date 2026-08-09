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

// One DT line per cell, index == bin number.
// D2/GPIO3 is unused: it is a strapping pin, sampled at reset.
static byte DT_PINS[CELL_COUNT] = {
    D1,  // GPIO2
    D3,  // GPIO4
    D4,  // GPIO5
    D5,  // GPIO6
    D6,  // GPIO43
    D7,  // GPIO44
    D8,  // GPIO7
    D9,  // GPIO8
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
