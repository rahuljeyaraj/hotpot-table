# Handover: write Tier 3 of the Hackster article

Paste this whole file as the opening message of a new session.

---

Write Tier 3 of the Hackster article for this project.

FILE: apps/myApps/hotpot-table/docs/HACKSTER.md
Tier 1 and Tier 2 are finished and approved. Append Tier 3; do not edit
Tier 1 or Tier 2.

THE PROJECT
"The Fire Pot: reimagining hotpot" — a hot pot ingredient table. Eight
bins on load cells, a projector overhead painting the entire UI onto the
tabletop, and a camera tracking the diner's hands. Repo:
github.com/rahuljeyaraj/hotpot-table (may be renamed to fire-pot).

Tier 1 is the story and the feature tour. Tier 2 is the build guide:
parts, wiring, a KiCad schematic, assembly, install, calibration, run.

WHAT TIER 3 IS
The architecture deep-dive. It is the section for the engineering that
Tier 2 deliberately left out, and the developer wants AS MANY DIAGRAMS
AS POSSIBLE: design is better shown than described. Assume the reader
has read Tier 2 and wants to know how the thing actually works.

MATERIAL WORTH TELLING (from the developer)
- The projected field is the room's only light source AND the UI. The
  table lights the food it is describing.
- The dot-projection calibration that was defeated by room lighting and
  replaced by manual corner-dragging. The wizard was removed outright.
- Two ofxFlowTools shader bugs. The GLSL 4.10 path was dead code on this
  rig, and the real bug sat in the 1.20 sibling nobody was reading.
- The classifier ships disabled because its accuracy is not good enough.
  This story belongs HERE, not in Tier 2.

This list is the developer's, not exhaustive. Read the code and find the
rest.

WHERE THE SHADER BUG LIVES
The addon lives outside this repo, in the openFrameworks tree, but the
change itself is saved here:

    of/patches/ofxFlowTools.patch     the diff
    of/patches/README.md              what each hunk does and why

The working copy it came from is at C:\openframeworks\addons\ofxFlowTools,
where the same edits sit uncommitted against upstream 17cabe2. Tier 2
already tells the reader to apply the patch; Tier 3 is where the story
of the bug goes.

`src/core/ftShader.h` holds the `GLSL120(...)` and `GLSL410(...)` macros
every shader header defines both variants with. The bug: `tex_density`
is at DENSITY resolution while those shaders render at SIM resolution,
so the lookup needed the `densityScale` that ftAdvectShader already
applied to its own cross-resolution reads. Read both diffs in full; the
patch comments explain the failure mode.

READ THESE BEFORE WRITING (do not write from memory)
Python:
- run.py                                    process supervision, tiers
- python/hotpot/core/main.py                the FSM, ports, wire messages
- python/hotpot/core/bin_grid.py            the two grids, and why two
- python/hotpot/core/geometry_store.py      homography, corner solve
- python/hotpot/core/scale.py               serial thread, filters
- python/hotpot/common/framebus.py          the shared-memory frame ring
- python/hotpot/tracker/                    MediaPipe, smoothing, cursor
- python/hotpot/camera/capture.py           capture, locked exposure/WB
- python/hotpot/classifier/                 backends, and why it is off

openFrameworks (of/hotpot-table/src/):
- ofApp.cpp, Stage.cpp/.h                   FBO stack, the light pass
- FluidLayer.cpp                            ofxFlowTools driving
- UiLayer.cpp                               layout, the centre column
- TableGeometry.h                           mm, the CAD layout
- StateLink.cpp/.h, CursorLink              the wire into oF

Web:
- python/hotpot/core/web/static/index.html  the dashboard, all tabs

VERIFY EVERYTHING AGAINST CODE, NOT DOCUMENTATION.
docs/HOTPOT_ARCHITECTURE_v3.md is the design spec and is authoritative
about INTENT, not about what shipped. It is stale in places (it still
names a Seeed ODYSSEY as the host, which was never obtained).
apps/myApps/hotpot-table/CLAUDE.md is a long build log and is STALE. It
has been wrong about shipped behaviour more than twice. Every claim goes
back to source.

DIAGRAMS
Hackster renders neither mermaid nor markdown, so every diagram must be
an image file committed to docs/img/ and referenced as
[IMAGE: docs/img/name.svg].

Two toolchains are already set up and working:

1. Hand-authored SVG, generated from a small Python script. See how
   docs/img/table-cutting-plan.svg was made. Light background, dark
   strokes, real numbers on the drawing. This is right for block
   diagrams, pipelines, coordinate spaces, state machines, timelines.

2. KiCad, for anything that is a real circuit. Installed at
   C:\Users\Rahul Jeyaraj\AppData\Local\Programs\KiCad\10.0\bin\kicad-cli.exe
   Export:  kicad-cli sch export svg --output docs\img --draw-hop-over <file>.kicad_sch
   The existing sheet is hardware/firepot-loadcells/firepot-loadcells.kicad_sch.

To rasterise an SVG to PNG (there is no rsvg/inkscape/magick on this
machine), wrap it in an HTML page with `img{width:100vw}` and screenshot
it with Edge headless:

    "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --headless
      --disable-gpu --screenshot=out.png --window-size=W,H
      --default-background-color=FFFFFFFF file:///path/wrap.html

Diagram style the developer has already accepted: portrait where the
content is tall, few columns, and text LARGE relative to the boxes.
Small labels get rejected.

FORMATTING (Hackster has NO markdown; these are toolbar buttons)
The comment at the top of HACKSTER.md is the key. Markdown in the file
is only notation for which button to press. Constraints:
- ONE heading level. Fake sub-structure with **bold lead-ins**.
- ONE bullet level. Never nest.
- Inline code and block code both exist (the # and </> buttons).

WRITING RULES (each of these came from a rejected draft)
- NO em dashes or en dashes anywhere. Use full stops, colons, commas.
- Never define something by what it lacks. "No buttons", "you don't
  press anything", "there's no X to handle" were all rejected. State
  what IS.
- No double negatives. Say the positive thing directly.
- No meta-commentary about design decisions as decisions. "I didn't
  build that as a feature" was rejected. Explaining how a mechanism
  works is fine and is the whole point of this tier.
- No generic rhetorical headings. "What if the table answered?" was
  rejected. Headings should be concrete.
- Cut any sentence that restates the clause before it.
- Do not paraphrase the demo video. Add detail it does not have.
- Do not restate Tier 1 or Tier 2. Tier 3 earns its place with the
  engineering underneath them.
- Specifics over generalities. Real file names, real numbers, real
  symptoms.
- It has to be enjoyable to read. Length is not a constraint.

FACTS ALREADY CORRECTED (do not reintroduce any of these)
- Host is an ASUS NUC 14 running Windows 11. NOT a Seeed ODYSSEY; that
  was never obtained. The Seeed part in the build is the XIAO ESP32S3.
- The projector is a WZATCO Yuva Go Plus, an ORDINARY projector,
  mounted on the wall itself, hard against the ceiling. Not
  short-throw. Keystone is done on the projector's own 4D keystone;
  bin/data/keystone.json is the identity rectangle and does nothing.
- The camera is a Lenovo 300 FHD webcam, zip tied to the end of a
  wooden batten fixed to the projector mount, mounted at 180 degrees.
- The tabletop is 6 mm plywood covered in projector screen fabric. It
  carries NO weight: it stands on twelve printed pillars at the height
  of the bin tops. The bins sit on printed cross bases on the table
  underneath, each on its own 1 kg CZL-611N load cell.
- The 440 mm centre column of the table is where the ingredient
  details, the cart and the checkout buttons are drawn. It is the
  554 px centre column in UiLayer.cpp. It is NOT a gap for a pot.
- There is NO voice feature. Never mention voice, even though a `voice`
  process exists in the stack and in run.py's process table.
- Never mention the XIAO's LED heartbeat. It never worked on the rig.
- Load cell sample rate is about 10.7 lines a second, not the 78 Hz the
  architecture doc claims.

WORKING STYLE
One step at a time, then stop and report. Commit to main when done, do
not push. Match the existing commit message style. The developer is
dyslexic: keep reports short, one thing at a time, and name the single
specific thing when asking for confirmation.

Expect several rounds of corrections. When the developer corrects a
fact, check it against source before applying it, and check whether the
same wrong assumption appears anywhere else in the tier.
