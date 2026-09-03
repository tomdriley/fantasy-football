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

The app opens on a **setup screen** asking two things:

1. **Where do you pick?** As of 15:30 the league had *not* drawn the order yet.
   If it still has not, choose **Not drawn yet** — the tool will not guess, and
   will tell you its advice is best-available-only until you set it. The moment
   Sleeper shows your position, set it from the `Seat` dropdown in the header.
2. **How will picks get in?** It defaults to **Manual**, which is the safe
   choice: you type each pick, nothing depends on the network, and nothing can
   silently go wrong. Switch to `assisted` or `live` only if the feed is proven
   to be working.

Keep the printed sheet next to you. It is the backup if the laptop dies.

---

## If something goes wrong

| Problem | Fix |
|---|---|
| The feed is wrong, or picks are appearing that did not happen | **Take over manually** (header button, or press `M`). Polling stops; your board is untouched |
| You typed the wrong player | **Fix** next to that pick — replace or remove it |
| You typed one pick too many | **Undo last**, or press `U` |
| The board is beyond saving | **Reset board** — clears every pick, keeps your seat and mode |
| The clock is nearly out and you do not trust anything | **PANIC**, or press `P` — instant answer, no simulation, no network |
| You need to change seat or mode | **Setup** in the header |

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
Nothing to do. It keeps working from the local board. Record each pick in the
**Record a pick** card — see below.

### Recording opponents' picks quickly
This is the time-critical part: nine opponents autopicking can fire off a dozen
picks in seconds, and they all have to be recorded.

- The **Most likely next** grid shows the 18 players most likely to go next.
  **One click records a pick.** Roughly 86% of picks are on this grid, so most
  need no typing at all.
- If the player is not on the grid, type part of the surname. Matches appear
  **as you type**, and you click the right one. Typing alone never records
  anything — the commit is always a click on a name you can see.
- Names are shown the way Sleeper shows them (`J. Gibbs`), so you are matching
  identical text rather than translating. Where an abbreviation would be
  ambiguous — Bijan vs Brian Robinson — the full name is shown instead.

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
