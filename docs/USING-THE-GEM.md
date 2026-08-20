# Using the Gem (every week)

Once setup is done, the weekly loop is: **chat → copy JSON → commit → tap Bring**.

---

## Step 1 — Plan with the Gem

Open your Gem in Gemini and start a chat. A typical exchange:

> **You:** Plan Friday to Tuesday.
>
> **Gem:** *(Phase 1)* Confirms the dates — and, if you want more than dinner,
> which meals per day (breakfast/lunch/dinner/other), how many participants each
> serves (default 2), and the serving times — then proposes meals, one line each:
> ```
> Fri 2026-07-17 | Thai Rotes Curry mit Hähnchen | 🥩 | 20 + 30 = 50 min
>   Key: 🔄 Kokosmilch, 🔄 Karotten, Hähnchenbrust, Tamari, Reis
>   Cremiges rotes Curry, glutenfrei mit Tamari.
> ... (one line per day) ...
> Ingredient Reuse Summary: Kokosmilch (2×), Karotten (3×), Reis (2×) ...
> ```
>
> **You:** Approve
>
> **Gem:** *(Phase 3)* Outputs three things:
> 1. **A single ```json``` block** — the plan file.
> 2. A **Bring! shopping list** grouped by supermarket section + a flat copy-paste block.
> 3. **Calendar events** (if Calendar is connected), each ending at its meal's
>    serving time (breakfast 09:00, lunch 12:00, dinner 19:30, other 15:00 — or
>    your overrides) and starting prep+cook minutes earlier.

If you want changes, say so before approving ("swap Tuesday for fish", "avoid
peppers", "cheaper week"). Only approve when you're happy — Phase 2 holds export
until you do.

---

## Step 2 — Commit the plan file

1. Copy the entire ```json``` block the Gem produced.
2. In your GitHub repo: **Add file → Create new file**.
3. Name it `plans/<week_label>.json` — the Gem suggests the label, e.g.
   `plans/2026-W29.json`.
4. Paste, then **Commit new file** to `main`.

That single commit triggers the Action. In ~1 minute the pages are live.

> **Tip (optional, local check):** before committing you can validate the JSON:
> ```bash
> make dev-install       # once
> python validate.py plans/2026-W29.json
> ```

---

## Step 3 — Import to Bring

Open the week on your phone:
```
https://<owner>.github.io/<repo>/recipes/2026/07/W29/
```
- Tap a recipe's **🛒 Bring!** button to import that recipe's ingredients, or
- Tap **🛒 Ganze Woche zu Bring!** to import the whole week's ingredients at once.

Each recipe page also has its own Bring button and the full recipe (ingredients,
steps, GF tips).

---

## Editing or re-running

- **Fix a recipe:** edit the committed `plans/*.json` and commit — the whole site
  re-renders (it's rebuilt from scratch every run, so nothing goes stale).
- **Add another week:** commit a new `plans/*.json`. Past weeks remain; the root
  page lists them all.
- **Force a specific folder** (e.g. keep a plan that spans New Year in one place):
  add to the plan JSON:
  ```json
  "folder": { "year": 2026, "month": 12, "week": 53 }
  ```

---

## What the folder structure looks like

```
recipes/
  2026/
    07/
      W29/
        index.html                              ← the week
        week.html                               ← combined shopping list
        2026-07-17-thai-rotes-curry-....html    ← one recipe
        2026-07-18-....html
        2026-07-20-....html
```

The folder is the ISO week of the plan's **earliest** date (your Friday), so a
Fri→Tue plan stays together even though it crosses the Sun→Mon ISO boundary. Each
file keeps its own real date in the name.
