# Ingredient substitutes — training-photo prop to display ingredient

Reference table only, not a build doc — no code reads this file. Saved
2026-08-13 at the developer's request, "for future reference."

This is doc §8.1's HIDDEN/SHOWN split (see `CLAUDE.md`'s "HIDDEN LABELS"
section and `core/pricing.py`'s `Item` docstring) made concrete: for most
of these 12 items, what got photographed for training data is a
**different physical object** than what the table tells a diner they're
looking at. The **Original Substitute Item** is the physical prop that
was actually in front of the camera; the **Hotpot Display Ingredient** is
the name and Chinese/pinyin shown on the projected plate.

| Item # | Original Substitute Item | Hotpot Display Ingredient | Ingredient Type | Chinese Name | Pinyin |
|---|---|---|---|---|---|
| 1 | Instant Noodle Block | Instant Noodles | Noodle | 方便面 | Fāngbiànmiàn |
| 2 | Loose Straight Noodles | Hand-Pulled Wheat Noodles | Noodle | 拉面 | Lāmiàn |
| 3 | White Rusk | Fried Tofu Roll | Tofu / Veg | 响铃卷 | Xiǎnglíngjuǎn |
| 4 | Soya Chunks | Fish Balls | Non-Veg (Seafood) | 鱼丸 | Yúwán |
| 5 | Dried Small Shrimps | Dried Shrimp | Non-Veg (Seafood) | 虾米 | Xiāmǐ |
| 6 | Small Round Rusk | Beef Balls | Non-Veg (Meat) | 牛肉丸 | Niúròuwán |
| 7 | Chicken Eggs | Egg | Egg | 鸡蛋 | Jīdàn |
| 8 | Button Mushrooms | Button Mushrooms | Mushroom / Veg | 口蘑 | Kǒumó |
| 9 | Dried Mango Strips | Dried Eel Strips | Non-Veg (Seafood) | 鳝鱼条 | Shànyú Tiáo |
| 10 | Flat Round Cookies | Shrimp Cake | Non-Veg (Seafood) | 虾饼 | Xiābǐng |
| 11 | Yellow Rusk | Potato Slices | Veg | 土豆片 | Tǔdòu Piàn |
| 12 | Lotus Root Slices | Lotus Root Slices | Veg | 藕片 | Ǒupiàn |

Items 1, 8, and 12 photograph the real ingredient — substitute and
display coincide, same as `egg` always has. The other nine (2–7, 9–11)
photograph a stand-in prop chosen for a similar shape/texture, not the
real ingredient.

**Not yet reflected in `data/catalogue.json`.** That file's `id`/
`class_name` for every item is currently a slug of the *display* name
(e.g. `fish_balls`, `beef_balls`) — which was the right call when it was
written on the assumption every photo was of the real ingredient, but is
backwards for the nine substitution rows now that this table exists:
doc §8.1 says `class_name` should name the thing that was actually
*photographed*, so the model can emit a label for what it was trained on
— a folder called `fish_balls` is a folder the model can never produce a
label for if what's inside it is photographs of soya chunks. Flagged
here rather than changed silently; see the conversation this table came
from for the open question of whether to rename the catalogue's
`class_name`s to match the substitute items.
