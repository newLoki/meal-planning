#!/usr/bin/env python3
"""
Render weekly meal-plan JSON into a static, Bring!-parseable recipe site.

Source of truth = plans/*.json (committed).
Output          = ./site/  (deployed to GitHub Pages by the Action; not committed).

Layout produced:
    site/index.html                              -> lists all weeks
    site/recipes/{YYYY}/{MM}/W{WW}/index.html    -> one week
    site/recipes/{YYYY}/{MM}/W{WW}/week.html     -> combined weekly shopping list (schema.org)
    site/recipes/{YYYY}/{MM}/W{WW}/{date}-{slug}.html  -> one recipe (schema.org)

Path components are derived from each recipe's `date` (calendar year + month,
ISO-8601 week number), NOT from the plan filename.
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).parent
SITE = ROOT / "site"
TEMPLATES = ROOT / "templates"
PLANS_GLOB = str(ROOT / "plans" / "**" / "*.json")

# Bring! requires an `author` on the schema.org/Recipe or it rejects the page as
# "no valid recipe" (see its integration checker). Recipes/plans may override it.
DEFAULT_AUTHOR = "MealAI"

TYPE_ICON = {"meat": "\U0001F969", "vegetarian": "\U0001F33F", "fish": "\U0001F41F"}
UMLAUTS = {
    "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
    "Ä": "ae", "Ö": "oe", "Ü": "ue",
    "é": "e", "è": "e", "ê": "e", "à": "a", "á": "a", "ç": "c",
}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def die(msg: str) -> "None":
    print(f"::error::{msg}", file=sys.stderr)
    sys.exit(1)


def pages_base_url() -> str:
    """Resolve the public base URL the site will be served from."""
    env = os.environ.get("PAGES_BASE_URL", "").strip().rstrip("/")
    if env:
        return env
    cfg = ROOT / "config.json"
    if cfg.exists():
        val = json.loads(cfg.read_text()).get("pages_base_url", "").strip().rstrip("/")
        if val:
            return val
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()  # "owner/name"
    if repo and "/" in repo:
        owner, name = repo.split("/", 1)
        if name.lower() == f"{owner.lower()}.github.io":
            return f"https://{owner}.github.io"
        return f"https://{owner}.github.io/{name}"
    # Local fallback so `python render.py` works before you configure anything.
    return "https://EXAMPLE.github.io/REPO"


def slugify(name: str) -> str:
    s = "".join(UMLAUTS.get(ch, ch) for ch in name).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "rezept"


def fmt_amount(amount) -> str:
    if amount is None or amount == "":
        return ""
    if isinstance(amount, bool):  # guard against JSON true/false
        return ""
    if isinstance(amount, float):
        return str(int(amount)) if amount.is_integer() else (f"{amount:.2f}".rstrip("0").rstrip("."))
    return str(amount)


def ingredient_str(ing: dict) -> str:
    parts = [fmt_amount(ing.get("amount")), (ing.get("unit") or "").strip(), (ing.get("name") or "").strip()]
    return " ".join(p for p in parts if p)


def deeplink_for(url: str, servings: int) -> str:
    return (
        "https://api.getbring.com/rest/bringrecipes/deeplink"
        f"?url={quote(url, safe='')}&source=web"
        f"&baseQuantity={servings}&requestedQuantity={servings}"
    )


def recipe_jsonld(*, name, author, servings, ingredient_strings, steps,
                  prep_min=0, cook_min=0, category="", image="", description="") -> dict:
    """schema.org/Recipe as a JSON-LD dict. Bring's live parser reads JSON-LD
    reliably; the page also carries microdata, so both formats are present."""
    data = {
        "@context": "https://schema.org",
        "@type": "Recipe",
        "name": name,
        "author": {"@type": "Organization", "name": author},
        "recipeYield": str(servings),
        "recipeIngredient": list(ingredient_strings),
    }
    if steps:
        data["recipeInstructions"] = [{"@type": "HowToStep", "text": s} for s in steps]
    if prep_min:
        data["prepTime"] = f"PT{prep_min}M"
    if cook_min:
        data["cookTime"] = f"PT{cook_min}M"
    if prep_min or cook_min:
        data["totalTime"] = f"PT{prep_min + cook_min}M"
    if category:
        data["recipeCategory"] = category
    if image:
        data["image"] = image
    if description:
        data["description"] = description
    return data


# --------------------------------------------------------------------------- #
# Validation / normalisation
# --------------------------------------------------------------------------- #
def normalise_recipe(r: dict, plan_servings: int, src: str, plan_author: str) -> dict:
    def req(key):
        if key not in r or r[key] in (None, "", []):
            die(f"{src}: recipe missing required field '{key}': {json.dumps(r, ensure_ascii=False)[:120]}")
        return r[key]

    try:
        d = datetime.strptime(req("date"), "%Y-%m-%d").date()
    except ValueError:
        die(f"{src}: recipe 'date' must be YYYY-MM-DD, got {r.get('date')!r}")

    ings = req("ingredients")
    if not isinstance(ings, list):
        die(f"{src}: 'ingredients' must be a list")
    ing_strs = [ingredient_str(i) for i in ings]
    if any(not s for s in ing_strs):
        die(f"{src}: every ingredient needs at least a 'name'")

    rtype = (r.get("type") or "meat").lower()
    if rtype not in TYPE_ICON:
        die(f"{src}: 'type' must be one of {list(TYPE_ICON)}, got {rtype!r}")

    return {
        "date": d,
        "date_str": d.isoformat(),
        "name": req("name"),
        "author": (str(r.get("author") or "").strip() or plan_author),
        "type": rtype,
        "icon": TYPE_ICON[rtype],
        "prep_min": int(r.get("prep_min", 0)),
        "cook_min": int(r.get("cook_min", 0)),
        "servings": int(r.get("servings", plan_servings)),
        "image": r.get("image") or "",
        "category": r.get("category") or "",
        "tagline": r.get("tagline") or "",
        "ingredient_strings": ing_strs,
        "steps": req("steps"),
        "tips": r.get("tips") or [],
        "src": src,
        "slug": slugify(req("name")),
    }


WEEK_LABEL_RE = re.compile(r"^\s*(\d{4})-W(\d{1,2})\s*$", re.IGNORECASE)


def week_key_for(anchor: date, override: dict | None, week_label: str | None = None) -> tuple:
    """(year, 'MM', 'WW') a plan lives under. Priority:
      1. explicit {"year":..,"month":..,"week":..} folder override
      2. the plan's own `week_label` (e.g. "2026-W28") — the user's authoritative
         week identity; needed because two plans in the same ISO week (e.g. a
         Mon-Wed and a Fri-Tue plan) would otherwise collide in one folder
      3. ISO week of the anchor (earliest) date, so a Fri->Tue plan stays in ONE folder
    Month always comes from the anchor date."""
    if override:
        return (int(override["year"]), f"{int(override['month']):02d}", f"{int(override['week']):02d}")
    if week_label:
        m = WEEK_LABEL_RE.match(str(week_label))
        if m:
            return (int(m.group(1)), f"{anchor.month:02d}", f"{int(m.group(2)):02d}")
    return (anchor.year, f"{anchor.month:02d}", f"{anchor.isocalendar().week:02d}")


def load_recipes() -> list[dict]:
    files = sorted(glob.glob(PLANS_GLOB, recursive=True))
    if not files:
        die("No plan files found under plans/*.json")
    out = []
    for f in files:
        try:
            data = json.loads(Path(f).read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            die(f"{f}: invalid JSON ({e})")
        servings = int(data.get("servings", 2))
        author = str(data.get("author") or "").strip() or DEFAULT_AUTHOR
        recipes = data.get("recipes")
        if not isinstance(recipes, list) or not recipes:
            die(f"{f}: top-level 'recipes' must be a non-empty list")
        norm = [normalise_recipe(r, servings, os.path.relpath(f, ROOT), author) for r in recipes]
        anchor = min(r["date"] for r in norm)
        wk = week_key_for(anchor, data.get("folder"), data.get("week_label"))
        for r in norm:
            r["wk_key"] = wk
        out.extend(norm)
    return out


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def main() -> None:
    base = pages_base_url()
    built = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)),
                      autoescape=select_autoescape(["html", "j2"]),
                      trim_blocks=True, lstrip_blocks=True)
    tpl_recipe = env.get_template("recipe.html.j2")
    tpl_week = env.get_template("week_index.html.j2")
    tpl_root = env.get_template("index.html.j2")

    recipes = load_recipes()

    # Group by the plan's anchor week (year, month, isoweek)
    weeks: dict[tuple, list] = {}
    for r in recipes:
        weeks.setdefault(r["wk_key"], []).append(r)

    if SITE.exists():
        import shutil
        shutil.rmtree(SITE)
    SITE.mkdir(parents=True)

    week_summaries = []
    for (year, mm, ww), items in sorted(weeks.items()):
        items.sort(key=lambda x: x["date"])
        wdir = SITE / "recipes" / str(year) / mm / f"W{ww}"
        wdir.mkdir(parents=True, exist_ok=True)
        web_dir = f"{base}/recipes/{year}/{mm}/W{ww}"

        entries = []
        for r in items:
            fname = f"{r['date_str']}-{r['slug']}.html"
            page_url = f"{web_dir}/{fname}"
            deeplink = deeplink_for(page_url, r["servings"])
            jsonld = recipe_jsonld(
                name=r["name"], author=r["author"], servings=r["servings"],
                ingredient_strings=r["ingredient_strings"], steps=r["steps"],
                prep_min=r["prep_min"], cook_min=r["cook_min"],
                category=r["category"], image=r["image"], description=r["tagline"])
            (wdir / fname).write_text(
                tpl_recipe.render(r=r, servings=r["servings"], icon=r["icon"],
                                  ingredient_strings=r["ingredient_strings"],
                                  deeplink=deeplink, jsonld=jsonld, built=built, source_path=r["src"]),
                encoding="utf-8")
            entries.append({"name": r["name"], "icon": r["icon"], "date": r["date_str"],
                            "total": r["prep_min"] + r["cook_min"], "filename": fname,
                            "deeplink": deeplink})

        # Combined weekly shopping list (schema.org) -> single-tap "whole week"
        merged = []
        for r in items:
            merged.extend(r["ingredient_strings"])
        week_recipe = {"name": f"Wocheneinkauf KW {ww}/{year}", "author": items[0]["author"],
                       "type": "meat",
                       "prep_min": 0, "cook_min": 0, "image": "", "category": "",
                       "tagline": "Alle Zutaten der Woche als eine Liste.", "steps": [], "tips": []}
        week_page_url = f"{web_dir}/week.html"
        week_deeplink = deeplink_for(week_page_url, items[0]["servings"])
        week_jsonld = recipe_jsonld(
            name=week_recipe["name"], author=week_recipe["author"],
            servings=items[0]["servings"], ingredient_strings=merged, steps=[],
            description=week_recipe["tagline"])
        (wdir / "week.html").write_text(
            tpl_recipe.render(r=week_recipe, servings=items[0]["servings"], icon="\U0001F6D2",
                              ingredient_strings=merged, deeplink=week_deeplink,
                              jsonld=week_jsonld, built=built, source_path="(kombiniert)"),
            encoding="utf-8")

        (wdir / "index.html").write_text(
            tpl_week.render(year=year, week=ww, items=entries, root_rel="../../../../",
                            week_deeplink=week_deeplink, built=built),
            encoding="utf-8")

        week_summaries.append({"year": year, "week": ww, "count": len(items),
                               "path": f"recipes/{year}/{mm}/W{ww}/"})

    week_summaries.sort(key=lambda w: (w["year"], w["week"]), reverse=True)
    (SITE / "index.html").write_text(tpl_root.render(weeks=week_summaries, built=built), encoding="utf-8")

    print(f"Rendered {len(recipes)} recipe(s) across {len(weeks)} week(s) into {SITE}/ (base: {base})")


if __name__ == "__main__":
    main()
