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

## Local patch

`HX711-multi.h`: the `HX711MULTI::` qualifier removed from that declaration,
marked in place with a `LOCAL PATCH:` comment. No other changes.

`-fpermissive` would also suppress it, but that downgrades conformance errors
across the whole build to work around one line in one header.
