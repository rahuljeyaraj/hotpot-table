# HX711-multi (vendored)

Source: https://github.com/compugician/HX711-multi
Commit: `573743aca227fa3fe1db8cc4e680427eead488ca`

Licence in `LICENSE`, unchanged. Sources, licence and `library.json` only;
examples and CI config were not copied.

## Why vendored rather than a `lib_deps` git URL

Upstream does not compile. `HX711-multi.h` declares a member with a qualified
name inside the class body:

```cpp
void HX711MULTI::readRaw(long *result = NULL);
```

GCC rejects this:

```
error: extra qualification 'HX711MULTI::' on member 'readRaw' [-fpermissive]
```

It breaks the library's own translation unit and every file including the header.

## Local patches

### 1. Compile fix

`HX711-multi.h`: the `HX711MULTI::` qualifier removed from that declaration,
marked in place with a `LOCAL PATCH:` comment. No other changes.

`-fpermissive` would also suppress it, but that downgrades conformance errors
across the whole build to work around one line in one header.

### 2. The ready-wait is bounded

`HX711-multi.cpp`: `readRaw()` opened with

```cpp
while (!is_ready());
```

`is_ready()` is true only when **every** channel's DOUT is low, so a single
dead cell -- an unpowered board, a broken DT wire, a failed HX711 -- stops that
loop returning at all. Nothing downstream distinguishes that from a dead XIAO:
the constructor itself calls it (via `set_gain()` -> `read()`), so `setup()`
never finishes and the board prints nothing for the life of the power cycle,
while still enumerating on USB -- the USB-Serial-JTAG peripheral is ROM, not
application, so the COM port appearing proves only that the chip has power.
That is exactly the fault it presented with on 2026-08-25.

It is also a tight spin with no yield, which starves the RTOS idle task.

Replaced with `waitReady(timeout_ms)`, which polls on a `delay(1)` and gives
up after `READY_TIMEOUT_MS` (default 1000ms, `setReadyTimeout()`). On timeout
`readRaw()` clocks **nothing** and returns false -- a partial frame read from a
silent channel would be plausible-looking garbage, and `core/scale.py` is built
to treat absent data as a fault (stale -> `None` -> no billing) but has no way
to catch a well-formed wrong number.

Consequently `read()` and `readRaw()` return `bool` rather than `void`, and
`tare()` fails rather than storing an offset for a channel that never reported.

`notReadyMask()` is new: bit *i* set means channel *i* is still high. It is what
`src/main.cpp` prints to name the faulty cell instead of just failing.

