# GF Meal Plan → Bring! recipe site

Website: https://newloki.github.io/meal-planning/

A small pipeline that turns the weekly output of a **Gemini meal-planner Gem** into
a static site of `schema.org/Recipe` pages that the **Bring!** shopping app can
import — one tap per recipe, plus a whole-week button. Hosted free on **GitHub
Pages**. Gluten-free / lactose-free, German ingredient names, 2-person household.

## How it works

```
Gemini Gem  ──emits──►  plans/2026-W29.json   (you commit this — the source of truth)
                              │
                    git push ─┤
                              ▼
             GitHub Action  (render.py + Jinja templates)
                              │  renders schema.org HTML into a year/month/week tree
                              ▼
                    GitHub Pages  ──fetched by──►  Bring! import
```

The Gem never writes HTML — it only emits validated recipe **data**. The markup is
built from a fixed template in CI, identically every run, so the machine-readable
format lives in version-controlled code instead of prompt output. That is the whole
design: it removes the one fragile link (an LLM regenerating exact microdata).

## Documentation

- **[docs/SETUP.md](docs/SETUP.md)** — one-time setup: GitHub repo, Pages, creating
  the Gem, connecting Calendar, and the first-import smoke test.
- **[docs/USING-THE-GEM.md](docs/USING-THE-GEM.md)** — the weekly workflow with a
  worked example conversation.

## Repository contents

```
.
├── README.md
├── LICENSE
├── GEMINI_GEM.md                 ← paste this into your Gemini Gem's Instructions
├── render.py                     ← reads plans/, renders schema.org HTML into site/
├── validate.py                   ← optional: validate a plan against the schema
├── requirements.txt              ← runtime dep (Jinja2)
├── requirements-dev.txt          ← dev dep (jsonschema, for validate.py)
├── Makefile                      ← install / build / serve / validate / clean
├── .gitignore
├── .github/workflows/build.yml   ← render + push site to gh-pages on push to plans/**
├── templates/
│   ├── recipe.html.j2            ← the schema.org Recipe page Bring parses
│   ├── week_index.html.j2        ← per-week listing with Bring buttons
│   └── index.html.j2             ← root listing of all weeks
├── schema/plan.schema.json       ← JSON Schema for a plan file
├── plans/
│   └── 2026-W29.json             ← example plan (GF, lactose-free) + smoke test
└── docs/
    ├── SETUP.md
    └── USING-THE-GEM.md
```

## Output layout

```
recipes/{YYYY}/{MM}/W{WW}/{YYYY-MM-DD}-{slug}.html   one recipe
recipes/{YYYY}/{MM}/W{WW}/week.html                  combined weekly shopping list
recipes/{YYYY}/{MM}/W{WW}/index.html                 the week
index.html                                           all weeks
```

`{YYYY}`/`{MM}`/`W{WW}` come from the plan's **anchor date** — the earliest recipe
date (your Friday) — so a Fri→Tue plan stays in **one** folder even though it
crosses the ISO Sun→Mon week boundary. Each recipe's real date stays in its
filename. Override with a `folder` block in the plan, or switch to per-recipe ISO
weeks via the one-line change noted in `render.py`.

## Quick start (local preview)

```bash
make install
make serve        # renders to ./site and serves http://localhost:8000
# or:
PAGES_BASE_URL="https://<owner>.github.io/<repo>" python render.py
```

## Notes

- **Action versions** are pinned to current majors (`checkout@v4`, `setup-python@v5`).
  Bump if GitHub ships newer majors.
- **Durable output:** the built site is stored on the `gh-pages` branch and served
  from it, so older weeks persist. By default only the current + previous week (plus
  any week whose plan changed) is rebuilt; template/`render.py`/requirements changes
  and manual runs rebuild every week. See `docs/SETUP.md`.
- **Deploy delay:** after the build pushes to `gh-pages`, Pages needs ~30–60s to
  deploy; don't tap a Bring button before it finishes or it will fetch a 404.
- **Unverified externally:** Bring's exact `.json` schema isn't public, so this uses
  the documented **HTML/schema.org** format. Run the smoke test in SETUP.md once
  before relying on it.
- `site/` is git-ignored — it's a build artifact, never committed.
