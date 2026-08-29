# ofxFlowTools patch

`ofxFlowTools.patch` is every local change this project needs in the
fluid addon. It applies to upstream
[moostrik/ofxFlowTools](https://github.com/moostrik/ofxFlowTools) at
commit `17cabe2aed9d47b982449c53ec7ada8a23289d3e`.

Apply it against the addon, not against this repo:

```
cd openFrameworks/addons/ofxFlowTools
git apply ../../apps/myApps/hotpot-table/of/patches/ofxFlowTools.patch
```

It carries two unrelated groups of change.

## The build fixes, without which the addon does not compile

`src/extensions/average/ftAverageFlow.cpp` and
`src/extensions/average/ftPixelFlow.h`.

- `std::bind2nd` was removed in C++17. Replaced with a lambda.
- Unqualified `min` resolves against the wrong candidate under MSVC.
  Qualified to `std::min`.

## The buoyancy and diffusion density lookups

`src/core/fluid/shaders/ftBuoyancyShader.h` and
`src/core/fluid/shaders/ftJacobiDiffusionShader.h`.

Both shaders render at SIM resolution and sample `tex_density`, which
is at DENSITY resolution. Upstream reads it at the raw fragment
coordinate, so the density term at a given pixel comes from half that
pixel's coordinates. The fix adds the `densityScale` uniform that
`ftAdvectShader` already applies to its own cross-resolution reads.

Both the GLSL 1.20 and the GLSL 4.10 variants of each shader are
patched. On this rig the 1.20 path is the one that runs.

Keep this patch in step with the addon. If you change the working copy
in `addons/ofxFlowTools`, regenerate it:

```
cd openFrameworks/addons/ofxFlowTools
git diff > ../../apps/myApps/hotpot-table/of/patches/ofxFlowTools.patch
```
