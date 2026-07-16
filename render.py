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
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlencode

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


def merge_week_ingredients(recipes: list[dict]) -> list[str]:
    """Combine a week's ingredients into one shopping list: entries with the same
    name and unit are merged, summing numeric amounts (e.g. 150 g + 150 g -> 300 g,
    0.33 + 0.33 + 0.34 Bund -> 1 Bund). Order follows first appearance. Ingredients
    with a non-numeric amount (e.g. "etwas") can't be summed and are kept separate."""
    groups: dict = {}
    order: list = []
    uniq = 0
    for r in recipes:
        for ing in r.get("ingredients", []):
            name = (ing.get("name") or "").strip()
            unit = (ing.get("unit") or "").strip()
            amount = ing.get("amount")
            numeric = isinstance(amount, (int, float)) and not isinstance(amount, bool)
            if numeric or amount in (None, ""):
                key = (name.lower(), unit.lower())
            else:  # unmergeable amount -> keep as its own standalone entry
                key, uniq = ("\0uniq", uniq), uniq + 1
            g = groups.get(key)
            if g is None:
                groups[key] = {"name": name, "unit": unit,
                               "sum": float(amount) if numeric else None,
                               "raw": amount}
                order.append(key)
            elif numeric:
                g["sum"] = (g["sum"] or 0.0) + float(amount)
    out = []
    for key in order:
        g = groups[key]
        amount = g["sum"] if g["sum"] is not None else g["raw"]
        out.append(ingredient_str({"amount": amount, "unit": g["unit"], "name": g["name"]}))
    return out


def deeplink_for(url: str, servings: int) -> str:
    return (
        "https://api.getbring.com/rest/bringrecipes/deeplink"
        f"?url={quote(url, safe='')}&source=web"
        f"&baseQuantity={servings}&requestedQuantity={servings}"
    )


DINNER_TIME = time(19, 30)  # target time the meal should be ready to eat
CAL_TS = "%Y%m%dT%H%M%S"    # floating local time (no timezone suffix)


def cook_window(recipe: dict, dinner: time = DINNER_TIME) -> tuple[datetime, datetime]:
    """(start, end) of the cooking event: it ends at dinner time (19:30) and
    starts prep+cook minutes earlier, so the food is ready to eat on time.
    Returned as floating datetimes (interpreted in the viewer's local zone)."""
    total = int(recipe.get("prep_min", 0)) + int(recipe.get("cook_min", 0))
    end = datetime.combine(recipe["date"], dinner)
    return end - timedelta(minutes=total), end


def event_summary(recipe: dict) -> str:
    return f"Kochen: {recipe['name']}"


def event_description(url: str, recipe: dict) -> str:
    """Event body: a link back to the recipe plus a short, numbered step-by-step."""
    steps = recipe.get("steps") or []
    body = f"Rezept: {url}"
    if steps:
        body += "\n\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))
    return body


def gcal_link_for(url: str, recipe: dict, dinner: time = DINNER_TIME) -> str:
    """A Google Calendar "add event" link for cooking this recipe."""
    start, end = cook_window(recipe, dinner)
    params = {
        "action": "TEMPLATE",
        "text": event_summary(recipe),
        "dates": f"{start.strftime(CAL_TS)}/{end.strftime(CAL_TS)}",
        "details": event_description(url, recipe),
    }
    return "https://calendar.google.com/calendar/render?" + urlencode(params)


# --------------------------------------------------------------------------- #
# iCalendar feed (subscribable) — one VEVENT per recipe across all weeks.
# Subscribing once (webcal://…/meals.ics) surfaces every current and future
# cooking event and lets the client refresh them automatically.
# --------------------------------------------------------------------------- #
def _ics_escape(text: str) -> str:
    """Escape a TEXT value per RFC 5545 (backslash, semicolon, comma, newline)."""
    return (text.replace("\\", "\\\\").replace(";", "\\;")
                .replace(",", "\\,").replace("\r\n", "\n").replace("\n", "\\n"))


def _ics_fold(line: str) -> str:
    """Fold a content line to <=75 octets, continuations prefixed with a space,
    without splitting a multi-byte UTF-8 character (RFC 5545 sec. 3.1)."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    chunks, first = [], True
    while len(raw) > 75:
        cut = 75 if first else 74  # continuation lines lose one octet to the space
        while cut > 0 and (raw[cut] & 0xC0) == 0x80:  # don't split inside a codepoint
            cut -= 1
        chunks.append((b"" if first else b" ") + raw[:cut])
        raw, first = raw[cut:], False
    chunks.append(b" " + raw)
    return "\r\n".join(c.decode("utf-8") for c in chunks)


def build_ics(events: list[dict], *, cal_name: str, dtstamp: str) -> str:
    """Serialise cooking events into a VCALENDAR string (CRLF line endings)."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//meal-plans//recipe-feed//DE",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_ics_escape(cal_name)}",
        f"NAME:{_ics_escape(cal_name)}",
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
        "X-PUBLISHED-TTL:PT12H",
    ]
    for e in events:
        lines += [
            "BEGIN:VEVENT",
            f"UID:{e['uid']}",
            f"DTSTAMP:{dtstamp}",
            f"DTSTART:{e['start'].strftime(CAL_TS)}",
            f"DTEND:{e['end'].strftime(CAL_TS)}",
            f"SUMMARY:{_ics_escape(e['summary'])}",
            f"DESCRIPTION:{_ics_escape(e['description'])}",
            f"URL:{e['url']}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_ics_fold(ln) for ln in lines) + "\r\n"


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
        "ingredients": ings,
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


def _iso_week(d: date) -> tuple[int, str]:
    c = d.isocalendar()
    return (c[0], f"{c[1]:02d}")


def select_weeks(week_keys, recipes) -> tuple[set, bool]:
    """Decide which weeks to (re)render, returning (weeks, build_all).

    Default (BUILD_SCOPE=recent): the current + previous ISO week, plus any week
    whose plan file changed in this push (CHANGED_PLANS, a whitespace-separated
    list of repo-relative paths). Every other week keeps its already-generated
    files, so older weeks stay in place.

    A FULL build (every week) is forced when BUILD_SCOPE is not "recent", or when
    there is no existing site output to preserve (first build / cache miss) — that
    way older weeks can never be lost, only regenerated from their plan JSON."""
    scope = os.environ.get("BUILD_SCOPE", "all").strip().lower()
    site_has_output = (SITE / "index.html").exists()
    if scope != "recent" or not site_has_output:
        return set(week_keys), True

    recent = {_iso_week(date.today()), _iso_week(date.today() - timedelta(days=7))}
    selected = {wk for wk in week_keys if (wk[0], wk[2]) in recent}

    changed = {c.strip() for c in os.environ.get("CHANGED_PLANS", "").split() if c.strip()}
    if changed:
        selected |= {r["wk_key"] for r in recipes if r["src"] in changed}
    return selected, False


WEEKDAYS_DE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def _span_label(a: date, b: date) -> str:
    """Compact German date range, e.g. "13.–15.07." or "29.06.–02.07."."""
    if a == b:
        return f"{a.day}.{a.month:02d}."
    if a.month == b.month:
        return f"{a.day}.–{b.day}.{b.month:02d}."
    return f"{a.day}.{a.month:02d}.–{b.day}.{b.month:02d}."


_WEEKDAY_NUM = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
                "friday": 4, "saturday": 5, "sunday": 6}


def week_start_weekday() -> int:
    """The weekday a display week starts on (Mon=0 … Sun=6). Configurable via the
    WEEK_START env var (a day name or 0–6); defaults to Thursday."""
    v = os.environ.get("WEEK_START", "thursday").strip().lower()
    return int(v) % 7 if v.isdigit() else _WEEKDAY_NUM.get(v, 3)


def day_grid(items: list[dict], entries: list[dict]) -> list[dict]:
    """A rolling 7-day week starting on the configured start day (default
    Thursday): from the start day on/before the week's earliest recipe through
    the following six days. Days without a recipe are flagged so the template
    can grey them out."""
    by_date: dict = {}
    for r, e in zip(items, entries):
        by_date.setdefault(r["date"], []).append(e)
    anchor = min(r["date"] for r in items)
    start = week_start_weekday()
    grid_start = anchor - timedelta(days=(anchor.weekday() - start) % 7)
    today = date.today()
    grid = []
    for i in range(7):
        d = grid_start + timedelta(days=i)
        grid.append({
            "wd": WEEKDAYS_DE[d.weekday()],
            "label": f"{d.day}.{d.month}.",
            "recipes": by_date.get(d, []),
            "active": d in by_date,
            "is_today": d == today,
        })
    return grid


def weeks_calendar(week_summaries: list[dict]) -> tuple[list[dict], int]:
    """Rolling week list for the overview: one entry per ISO week from two weeks
    before to two weeks after the span of (today + all planned weeks). Each week
    is placed at the Monday of its label's ISO week, so two plans that share a
    calendar week (by anchor date) still get distinct rows. Returns
    (weeks, index_of_todays_week)."""
    today = date.today()
    ty, tw, _ = today.isocalendar()
    today_mon = date.fromisocalendar(ty, tw, 1)
    planned = {date.fromisocalendar(int(w["year"]), int(w["week"]), 1): w
               for w in week_summaries}
    mons = sorted(planned) or [today_mon]
    start = min([today_mon] + mons) - timedelta(weeks=2)
    end = max([today_mon] + mons) + timedelta(weeks=2)

    weeks, current_index, mon, i = [], 0, start, 0
    while mon <= end:
        iso = mon.isocalendar()
        w = planned.get(mon)
        is_current = mon == today_mon
        if is_current:
            current_index = i
        weeks.append({
            "key": f"{iso[0]}-W{iso[1]:02d}",
            "iso_week": f"{iso[1]:02d}",
            "range": _span_label(w["first"], w["last"]) if w else _span_label(mon, mon + timedelta(days=6)),
            "active": w is not None,
            "path": w["path"] if w else "",
            "count": w["count"] if w else 0,
            "is_current": is_current,
        })
        mon += timedelta(weeks=1)
        i += 1
    return weeks, current_index


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def main() -> None:
    base = pages_base_url()
    now = datetime.now(timezone.utc)
    built = now.strftime("%Y-%m-%d %H:%M UTC")
    dtstamp = now.strftime("%Y%m%dT%H%M%SZ")
    host = base.split("://", 1)[-1]           # e.g. owner.github.io/repo
    feed_url = f"{base}/meals.ics"            # https, for "add by URL"
    feed_webcal = f"webcal://{host}/meals.ics"  # one-tap subscribe
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

    import shutil
    to_build, build_all = select_weeks(weeks.keys(), recipes)
    if build_all and SITE.exists():
        shutil.rmtree(SITE)
    SITE.mkdir(parents=True, exist_ok=True)

    events = []  # one cooking event per recipe -> global .ics feed (all weeks)
    week_summaries = []
    rendered = 0
    for (year, mm, ww), items in sorted(weeks.items()):
        items.sort(key=lambda x: x["date"])
        web_dir = f"{base}/recipes/{year}/{mm}/W{ww}"
        wdir = SITE / "recipes" / str(year) / mm / f"W{ww}"
        write_week = (year, mm, ww) in to_build
        if write_week:
            rendered += 1
            if wdir.exists():  # rebuild from scratch so removed recipes don't linger
                shutil.rmtree(wdir)
            wdir.mkdir(parents=True, exist_ok=True)

        # The per-recipe loop always runs: the global index + .ics feed must cover
        # every week, even ones whose HTML we leave in place. Only the file writes
        # are gated on write_week.
        entries = []
        for r in items:
            fname = f"{r['date_str']}-{r['slug']}.html"
            page_url = f"{web_dir}/{fname}"
            deeplink = deeplink_for(page_url, r["servings"])
            gcal_link = gcal_link_for(page_url, r)
            entries.append({"name": r["name"], "icon": r["icon"], "date": r["date_str"],
                            "total": r["prep_min"] + r["cook_min"], "filename": fname,
                            "deeplink": deeplink, "gcal_link": gcal_link})

            start, end = cook_window(r)
            events.append({
                "uid": f"{r['date_str']}-{r['slug']}@{host}",
                "start": start, "end": end,
                "summary": event_summary(r),
                "description": event_description(page_url, r),
                "url": page_url,
            })

            if not write_week:
                continue
            jsonld = recipe_jsonld(
                name=r["name"], author=r["author"], servings=r["servings"],
                ingredient_strings=r["ingredient_strings"], steps=r["steps"],
                prep_min=r["prep_min"], cook_min=r["cook_min"],
                category=r["category"], image=r["image"], description=r["tagline"])
            (wdir / fname).write_text(
                tpl_recipe.render(r=r, servings=r["servings"], icon=r["icon"],
                                  ingredient_strings=r["ingredient_strings"],
                                  deeplink=deeplink, gcal_link=gcal_link, jsonld=jsonld,
                                  built=built, source_path=r["src"]),
                encoding="utf-8")

        week_summaries.append({"year": year, "week": ww, "count": len(items),
                               "path": f"recipes/{year}/{mm}/W{ww}/",
                               "first": items[0]["date"], "last": items[-1]["date"]})
        if not write_week:
            continue

        # Combined weekly shopping list (schema.org) -> single-tap "whole week".
        # Ingredients shared across recipes are merged, summing amounts.
        merged = merge_week_ingredients(items)
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
            tpl_week.render(year=year, week=ww, days=day_grid(items, entries),
                            root_rel="../../../../", week_deeplink=week_deeplink, built=built),
            encoding="utf-8")

    events.sort(key=lambda e: e["start"])
    (SITE / "meals.ics").write_text(
        build_ics(events, cal_name="Wochenpläne – Kochtermine", dtstamp=dtstamp),
        encoding="utf-8")

    weeks_cal, current_index = weeks_calendar(week_summaries)
    (SITE / "index.html").write_text(
        tpl_root.render(weeks_cal=weeks_cal, current_index=current_index, built=built,
                        feed_webcal=feed_webcal, feed_url=feed_url),
        encoding="utf-8")

    scope = "all weeks" if build_all else f"{rendered} of {len(weeks)} week(s) (current+previous+changed)"
    print(f"Rendered {scope} into {SITE}/ (base: {base}); {len(recipes)} recipe(s) total")
    print(f"Wrote {len(events)}-event feed -> {SITE / 'meals.ics'}")


if __name__ == "__main__":
    main()
