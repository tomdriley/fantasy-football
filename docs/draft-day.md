# Draft Day — Quick Reference

Everything needed on 2026-09-03 at 16:00. No football knowledge required.

---

## Before the draft (do this at ~15:30)

```sh
cd ~/github/fantasy-football

python3 scripts/refresh_rules.py  # pull the league's current state first
python3 scripts/make_sheet.py     # then PRINT draft-sheet.txt
python3 scripts/serve.py          # opens http://127.0.0.1:8777
```

`refresh_rules.py` matters: managers join and the draft order gets assigned right
up to the start. It prints what changed, or says there was no drift.

If it says **RESUMED: N picks restored**, and the draft has not started, that is
leftover state — restart with `python3 scripts/serve.py --fresh`, or click
**Clear board** in the banner.

Two things to check on screen:

1. **Seat** — it fills itself in once the league assigns the draft order. If it
   still shows `?` at 16:00, pick your seat from the dropdown (the app shows it
   in the Sleeper draft room).
2. **Mode** — leave it on `live`.

Keep the printed sheet next to you. It is the backup if the laptop dies.

---

## During the draft

When it is your turn the top of the screen shows three options and a green
**TAKE** button on the first one.

**Take the one marked TAKE.** Then click that same player in the Sleeper app.

The tool never picks for you — it advises, you click.

### Reading the screen

| What you see | What it means |
|---|---|
| `TAKE` (green row) | The recommendation. This is the reliable one |
| `low value — much easier to replace later` | A trap. This player scores a lot but is easy to replace. **Do not take him** |
| `stakes LOW` | All options are near-equal. Decide in 5 seconds |
| `stakes HIGH` | This pick matters. Take the top one |
| `sanity ok` | The pick is close to what the wider market would do |
| `unusual` / `reach` | The pick deviates from market consensus, with the reason |
| `QB 0/1  RB 1/2 …` | Which lineup slots you still need |

### Keyboard

- `Enter` — record the player typed in the box
- `P` — panic (instant pick)
- `U` — undo the last entry

---

## When something goes wrong

### The clock is nearly out and you are unsure
Press **P** or click the big red **PANIC** button. It answers instantly and
gives several names. Take the first one still available.

### The tool says "offline"
Nothing to do. It keeps working from the local board. Type each pick as it
happens in the **Enter a pick** box (a surname is enough: `gibbs`).

### You typed the wrong player
Click **Fix** next to that pick in the Recent list. Replace him, or remove the
pick entirely if it never happened.

### You missed entering a pick and everything is now off by one
This matters — it makes the tool think the wrong players are yours. Click
**Fix** on the pick where the mistake starts, then use **Remove pick**, or
re-enter the missing one in the right position. The roster panel on the right
should always match what Sleeper shows as your team.

### The feed and your board disagree (assisted mode)
A blue box appears saying what the feed has that you do not. Click **Accept
feed** if the feed is right, **Keep mine** if it is not.

### The whole thing is broken
Use the printed sheet. Take the highest player in the highest tier that fills a
slot you still need.

### The laptop is gone entirely
Two rules capture most of the value:

- **Do not take a quarterback early.**
- **Take a kicker with your very last pick.**

Otherwise take the best running back or receiver available.

---

## Non-negotiables

1. **Never end the draft unable to fill a slot** — QB, 2 RB, 2 WR, TE, K, DEF.
   The tool warns you when picks are running short.
2. **Kicker last. Defense second-to-last.**
3. **One quarterback.** The best one is barely better than the tenth best.

---

## What to expect

Backtested across five seasons, this drafts about **9% better** than letting the
timer run out and about **10% better** than a sensible human rule of thumb. In
one of those five seasons the margin was only 2%, so it is an edge, not a
guarantee.

Your team is called **Git Blame Copilot**. If it goes badly, the name was
already a hedge.
