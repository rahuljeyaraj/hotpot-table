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

# The stall wasn't broken. It was just silent.

There's a hot pot stall in a Singapore hawker centre that I ate at all through my master's at NTU. It works like this: take a bowl and a pair of tongs, load up whatever you want off the stand, hand it over. They weigh each ingredient, ask your broth and your spice level, bill you, cook it, and call your number.

It's a good system. Faster than a menu, cheaper than table service, and you get exactly the bowl you wanted. Nothing about it needs fixing.

It just never answers a single question while you're standing there deciding.

- **What is this?** Some of it I recognised. Some of it was a pale sphere. Is that a fish ball, or a very confident potato?
- **What does it taste like?** No idea, and the only way to find out costs money and one dinner.
- **What is it costing me?** The bill comes *after* the decisions. All of them.
- **Which broth?** Labelled in Chinese, which I do not read.

Here's the thing: every one of those answers already exists. The staff know them. The regulars know them. Somebody printed them somewhere once. The information isn't missing from the world — it's just nowhere near the moment you're standing there with tongs in your hand, guessing.

That's the actual gap. Not a broken system. An opaque one — and only for the people who don't already know.

# What if the table answered?

Not an app. Not a kiosk in the corner. Not a tablet bolted to the sneeze guard. You have a bowl in one hand and tongs in the other; you're not getting your phone out.

The answer had to be the table itself.

**So the table is the display.** The surface is plywood. There's a projector overhead throwing the entire interface down onto it — names, prices, your cart, the checkout. And because the room is dark, that projected light is doing two jobs at once: it's the user interface, and it's the only light in the room. The white patch under each bin isn't decoration, it's the lamp that lets you see the food. Getting one of those jobs right usually means getting the other wrong, which turned out to be most of the fun.

**And the table never asks what you took.** There are eight load cells under the surface, one per bin, weighing continuously. You don't press anything to add an item. You reach in, you take food, the weight goes down, and the table works out the rest. The interaction is *subtraction* — there's no "add to cart" button because there's no moment where you'd press it.

**Which is what makes changing your mind free.** Put the food back and the weight comes back, so the price comes back down. I didn't build that as a feature. It's just what happens when you measure what's actually there instead of counting events — there's no put-back to handle, because there was never an add to undo.

The rest of it — a ring of fire around whatever bin your hand is over, the ingredient's calories and tasting note in the middle of the table, the whole thing in Chinese at the press of a button, checkout and payment on the tabletop — all of that hangs off those three decisions.

# Watch it work

The video is the fastest way to see it. Full walkthrough of a diner's order, then a look behind the counter at the staff dashboard.

[VIDEO: YouTube demo — https://youtube.com/... ]

# The short version

- Eight ingredient bins, each on its own load cell, weighed continuously
- Live weight and price per item, plus a running total, projected onto the tabletop
- Hand tracking overhead — no buttons, no touchscreen, no app, no phone
- Ingredient info on demand: veg / non-veg, calories per 100 g, tasting note
- Full English and Chinese, switchable mid-order
- Broth and spice selection, QR payment, and a token number — all on the table
- A staff dashboard in the browser for calibration, live bin weights, and low-stock alerts
- A Seeed Studio XIAO ESP32S3 reads the eight load cells and streams them to the host
- Host is an ASUS NUC 14 running Python and openFrameworks

**One honest note before you go further:** there's a camera-based classifier in here that can identify what's in a bin by looking at it. It works. Its accuracy isn't good enough to put in front of a paying customer yet, so it ships switched off, and staff pick ingredients from a dropdown instead. There's a section further down on why, because the reason is more interesting than the feature.

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
