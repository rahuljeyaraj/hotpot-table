<!--
HACKSTER TRANSCRIPTION KEY — not part of the article, do not paste.

Nothing in this file is Hackster syntax. Hackster has no markdown at
all. The markup below is only a NOTE TO YOU saying which toolbar
BUTTON to press after pasting each piece of text in.

  WHAT YOU SEE HERE          WHICH BUTTON TO PRESS THERE
  a line starting with #  ->  the H button (only one heading level)
  **bold**                ->  the B button
  *italic*                ->  the i button
  `text in backticks`     ->  the # button   (inline code)
  a ``` fenced block      ->  the </> button (block code)
  a line starting with >  ->  the quote button
  a line starting with -  ->  the bullet button (one level, never nested)
  [text](url)             ->  the link button
  [IMAGE: ...]            ->  the image embed
  [VIDEO: ...]            ->  the video embed

Strip the markers as you go — paste the plain words, select them, press
the button. Sub-structure below a heading is faked with bold lead-ins,
because Hackster has no second heading level. Never nest bullets.
-->

# The Fire Pot: reimagining hotpot

*A dining table that weighs your food, prices it as you pick, and sets itself on fire. Responsibly.*

[VIDEO: YouTube demo — https://youtube.com/... ]

# A hawker centre in Singapore, and a bowl I couldn't read

While doing my master's at NTU Singapore, I found a hot pot stall in a hawker centre. There's no menu. You take a bowl and a pair of tongs, load up whatever you want from the stand, and hand it over. The staff weigh each ingredient, ask which broth and how spicy you want it, bill you, cook it, and call your token number when it's ready.

Brilliant system. I loved it. I also had absolutely no idea what I was doing.

- Half the ingredients were a mystery. Is that a fish ball, or a very confident potato?
- I couldn't see what I was spending until the bowl was already full and it was far too late to be sensible about it.
- The broths were all labelled in Chinese, which I do not read.
- I was a student. On a budget. Guessing.

So I'd stand there, tongs hovering, doing arithmetic I had no numbers for.

# So I made the table do the work

The Fire Pot is a hot pot ingredient table that explains itself.

There's a projector above it, a camera up there too, and eight load cells hidden underneath. The tabletop is not a screen — it's plywood. Everything you see is light thrown down onto it, and the table watches your hands to know what you're reaching for.

Nothing to download. No touchscreen to smear with tongs. You just use the table.

> Every ingredient is weighed live. Every price updates as you pick. Every label is in a language you can choose. And if you change your mind, you put it back and the number goes down.

# What it's like to use

**It wakes up when you wave.** With nobody around, the table idles quietly — glowing rings around each bin, a flame drifting about on its own. Wave a hand over it and all eight ingredients light up with their name and price per 100 g.

**Hover a bin and it catches fire.** A ring of flame wraps the bin you're reaching for, and the centre of the table tells you what's in it: veg or non-veg, calories per 100 g, and a short description of what it actually tastes like. This is the thing I wanted in Singapore and never got.

**The cart fills as you scoop.** Every item you've picked appears at the near edge of the table with its weight and its price, and the running total sits underneath.

**Changed your mind? Put it back.** The item's weight drops, the total drops with it, and nobody has to be called over. Tip the whole lot back and the cart empties itself.

**Read it in Chinese.** One button. Names, descriptions, prices, the lot — all of it switches, and switches back.

**Then check out on the table itself.** Pick a broth, pick a spice level, see the total, scan the QR code with your phone to pay. The table gives you a token number and asks you to hand your bowl to the staff. They cook it. Your number gets called. Same as it always was — you just knew what you were doing this time.

# The short version

- Eight ingredient bins, each on its own scale, weighed continuously
- Live price per item and a running total, projected onto the table
- Hand tracking — no buttons, no touchscreen, no app
- Ingredient info on demand: veg / non-veg, calories, tasting note
- Full English and Chinese
- Broth and spice selection, then QR checkout and a token number
- A staff dashboard for setup, calibration, and keeping an eye on the bins
- Built on a Seeed ODYSSEY X86, with a Seeed XIAO ESP32S3 reading the load cells

**One honest note:** there's a camera-based ingredient classifier in there that can label a bin by looking at it. It works, but its accuracy isn't good enough to trust in front of a paying customer yet, so it ships switched off and the staff pick ingredients from a dropdown instead. More on that further down — I'd rather tell you than have you find out.

# Gallery

[IMAGE: hero — the whole table lit, all 8 bins named and priced]
*It's a sheet of plywood. I promise. Turn the lights off and it stops looking like one.*

[IMAGE: a bin on fire under a hovering hand]
*Reach for the beef balls and the beef balls reach back.*

[IMAGE: the info box showing veg/non-veg, kcal, description]
*Veg or not, how many calories, and what it actually tastes like. The information I stood there wishing for.*

[IMAGE: the cart with several items and a running total]
*The running total. Statistically the most-requested feature by anyone who has ever eaten on a budget.*

[IMAGE: the table in Chinese]
*Same table, one button later.*

[IMAGE: broth selection screen]
*Three broths, and this time you can tell which is which.*

[IMAGE: spice chilli strip]
*Mild, medium, hot. The chillies fill in. No translation required.*

[IMAGE: QR / token number screen]
*Scan, pay, take a number, hand over the bowl.*

[IMAGE: under the table — the 8 load cells and wiring]
*The unglamorous half. Eight load cells, one microcontroller, a great deal of double-sided tape.*

[IMAGE: staff dashboard in a browser]
*Where the staff live. Calibration, live bin weights, and a camera feed of the whole table.*

[IMAGE: idle attract mode, flame wandering with no hand present]
*Nobody's here, so it plays with fire by itself.*
