# HOTPOT TABLE

Interactive weigh-by-weight hot pot ingredient counter.
Seeed "Make a Sign" Interactive Signage Contest 2026.
Product name: Hot Pot (en) / 称重火锅 (zh).

## GROUND TRUTH
Read docs/HOTPOT_ARCHITECTURE_v3.md before doing anything.
It is authoritative. This file is only status + rules.

## STATUS
Architecture v3 adopted. Full rewrite in progress.
Stage 1-2 code is being replaced, not extended.
Current milestone: M0 (scaffold, launcher, transport).
Last completed step: M0.0.

## HOW TO WORK HERE
- One step at a time. Commit. Stop and report back.
- The developer is dyslexic. Reports are short.
  One thing at a time. Confirmation questions must name
  the single specific thing being confirmed.
- Never assume an external API exists. Verify against the
  installed version. Items marked VERIFY in the doc are
  where this has already gone wrong.
- Every check must be capable of failing. Items marked
  TRAP in the doc are checks that pass by construction.
- Say whether evidence is a framebuffer capture or
  physical observation of the projected surface.

## HARD INVARIANTS (full list in doc section 2)
- Core owns all state. oF is a dumb renderer.
- Core never touches a frame.
- Price = (startWeight - liveWeight) / 100 * pricePer100g.
  Never sum per-event deltas. No put-back branch.
- The 10g deadband is display-only and SNAPS to truth.
- Re-baseline, never re-tare.
- Food position is not fixed. Bin map is live data.

## TOP RISKS
- reComputer x86 model still unidentified. Blocks M8.
- Camera elevation angle never measured. Due in M3.

## BUILD
oF 0.12.1, Visual Studio 2026, toolset v145
(projectGenerator emits v143 - must be changed).
msbuild hotpot-table.sln /p:Configuration=Debug
        /p:Platform=x64 /m
Firmware: PlatformIO, firmware/loadcells/. Do not touch.
