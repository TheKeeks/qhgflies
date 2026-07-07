#!/usr/bin/env python3
"""Sync the best of @qhgflies's Instagram posts into gallery/.

Pipeline per run:
  1. Fetch the ~50 most recent posts (with like/comment counts) via the
     Instagram API (Instagram Login).
  2. Classify each not-yet-seen post with Claude (image + caption): is it a
     painting, and is the shot gallery-ready (clean frontal presentation) or
     a context photo? Verdicts are cached in gallery/classify-cache.json and
     committed, so each post is classified exactly once per schema version.
  3. Select 12 posts: gallery-ready paintings preferred (context shots only
     fill leftover slots); videos and config-excluded posts are skipped; the
     newest few gallery-ready paintings are always included; the rest are
     ranked by recency-decayed engagement (weighted likes/comments/saves/
     shares/views, halving every halfLifeDays). Displayed best-ranked first.
  4. Download any new images (auto-cropping uniform borders such as white
     mats, once per post) and rewrite gallery/manifest.json.

Fail-safe by design: nothing in gallery/ is touched until every API call and
every image download has succeeded. On any Instagram failure the script exits
with an error and the existing gallery (and the live site) stays as-is.
AI curation degrades gracefully: no ANTHROPIC_API_KEY, a missing anthropic
package, or a failed classification just means the affected posts are treated
as paintings (and re-classified next run) - the site never goes empty.

Env: INSTAGRAM_ACCESS_TOKEN (required), ANTHROPIC_API_KEY (optional).
"""

import base64
import datetime
import json
import os
import shutil
import sys
import tempfile
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GALLERY = os.path.join(ROOT, "gallery")
MANIFEST = os.path.join(GALLERY, "manifest.json")
CLASSIFY_CACHE = os.path.join(GALLERY, "classify-cache.json")
CONFIG = os.path.join(ROOT, "config.json")

API_BASE = "https://graph.instagram.com/v23.0"
MAX_POSTS = 12
FETCH_LIMIT = 50
FIELDS = (
    "id,caption,media_type,media_url,thumbnail_url,permalink,timestamp,"
    "like_count,comments_count,children{media_type,media_url,thumbnail_url}"
)
TIMEOUT = 30

CACHE_VERSION = 2

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "is_painting": {
            "type": "boolean",
            "description": "true if the post shows the artist's own painting/drawing "
                           "(finished or work-in-progress), false for anything else",
        },
        "gallery_ready": {
            "type": "boolean",
            "description": "true only if the photo is a clean, frontal presentation of "
                           "the artwork itself filling most of the frame (a plain border "
                           "or mat is fine); false if the artwork sits inside a wider "
                           "scene - sketchbook on a car or in grass, held in a hand, on "
                           "an easel outdoors, or in a room with surroundings visible",
        },
    },
    "required": ["is_painting", "gallery_ready"],
    "additionalProperties": False,
}

CLASSIFY_PROMPT = (
    "You are curating a painter's commission portfolio from their Instagram feed. "
    "Make two judgments about this post:\n"
    "1. is_painting - does it show the artist's own artwork (painting or drawing, "
    "finished or in progress)? Selfies, people, places, events, announcements, memes "
    "and screenshots are not portfolio pieces.\n"
    "2. gallery_ready - is the photo a clean, frontal shot of the artwork itself, "
    "filling most of the frame (a plain border/mat around it is fine)? Shots where "
    "the artwork appears within a larger scene - a sketchbook on a car hood or lawn, "
    "held in a hand, propped on an easel with scenery around it, photographed in a "
    "room - are paintings but NOT gallery_ready.\n\n"
    "Caption of the post:\n{caption}"
)


def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "qhgflies-site-sync/1.0"})
    return urllib.request.urlopen(req, timeout=TIMEOUT)


def api_get(path, params):
    qs = urllib.parse.urlencode(params)
    with http_get(f"{API_BASE}/{path}?{qs}") as resp:
        return json.load(resp)


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


DEFAULT_WEIGHTS = {"likes": 1, "comments": 2, "saves": 3, "shares": 4, "views": 0.02}


def fetch_insights(candidates, token):
    """Attach saves/shares/views/reach insights to each post (best effort).

    Insights require the instagram_business_manage_insights permission; if the
    first call is denied, skip the rest and rank on likes/comments only.
    """
    for i, (post, _url) in enumerate(candidates):
        try:
            data = api_get(f"{post['id']}/insights", {
                "metric": "views,reach,saved,shares",
                "access_token": token,
            })
            metrics = {}
            for item in data.get("data", []):
                values = item.get("values") or [{}]
                value = values[0].get("value")
                if value is None:
                    value = (item.get("total_value") or {}).get("value")
                if isinstance(value, (int, float)):
                    metrics[item["name"]] = value
            post["_metrics"] = metrics
        except Exception as exc:  # noqa: BLE001 - insights are a bonus, never fatal
            post["_metrics"] = {}
            if i == 0:
                print(f"::warning::Post insights unavailable ({exc}) - ranking on "
                      "likes/comments only. Grant instagram_business_manage_insights "
                      "on the token to include saves/shares/views.")
                break


def engagement(post, weights=DEFAULT_WEIGHTS, half_life_days=60):
    m = post.get("_metrics") or {}
    score = (
        weights.get("likes", 1) * (post.get("like_count") or 0)
        + weights.get("comments", 2) * (post.get("comments_count") or 0)
        + weights.get("saves", 3) * (m.get("saved") or 0)
        + weights.get("shares", 4) * (m.get("shares") or 0)
        + weights.get("views", 0.02) * (m.get("views") or 0)
    )
    # Recency decay: a post's score halves every half_life_days.
    if half_life_days and post.get("timestamp"):
        try:
            posted = datetime.datetime.strptime(post["timestamp"], "%Y-%m-%dT%H:%M:%S%z")
            age_days = max(0.0, (datetime.datetime.now(datetime.timezone.utc) - posted)
                           .total_seconds() / 86400)
            score *= 0.5 ** (age_days / half_life_days)
        except ValueError:
            pass
    return score


def trim_border(path):
    """Crop a uniform border (white mat, wall, etc.) so the painting fills the
    image. Conservative: only crops when all four corners agree on a border
    color and a real border exists. Returns True if the file was rewritten."""
    try:
        from PIL import Image, ImageChops
    except ImportError:
        return False
    img = Image.open(path).convert("RGB")
    w, h = img.size
    corners = [img.getpixel(p) for p in [(2, 2), (w - 3, 2), (2, h - 3), (w - 3, h - 3)]]
    avg = tuple(sum(c[i] for c in corners) // 4 for i in range(3))
    if max(sum(abs(c[i] - avg[i]) for i in range(3)) for c in corners) > 60:
        return False  # corners differ: photo runs edge-to-edge, no border
    bg = Image.new("RGB", img.size, avg)
    diff = ImageChops.difference(img, bg).convert("L")
    bbox = diff.point(lambda p: 255 if p > 26 else 0).getbbox()
    if not bbox:
        return False
    bw, bh = bbox[2] - bbox[0], bbox[3] - bbox[1]
    if bw < 0.35 * w or bh < 0.35 * h:
        return False  # would cut away too much - probably not a border
    sides = (bbox[0], bbox[1], w - bbox[2], h - bbox[3])
    if max(sides[0], sides[2]) < 0.025 * w and max(sides[1], sides[3]) < 0.025 * h:
        return False  # border too thin to be worth cropping
    img.crop(bbox).save(path, "JPEG", quality=92)
    return True


def image_url(post):
    """Best displayable image URL for a post, or None to skip it."""
    mtype = post.get("media_type")
    if mtype == "IMAGE":
        return post.get("media_url")
    if mtype == "VIDEO":
        return post.get("thumbnail_url")
    if mtype == "CAROUSEL_ALBUM":
        children = (post.get("children") or {}).get("data") or []
        for child in children:
            if child.get("media_type") == "IMAGE" and child.get("media_url"):
                return child["media_url"]
        for child in children:
            if child.get("thumbnail_url"):
                return child["thumbnail_url"]
        return post.get("media_url") or post.get("thumbnail_url")
    return None


def about_photo_file(config):
    """File the About section points at, so pruning never removes it."""
    pick = (config.get("aboutPhoto") or "").strip()
    if not pick:
        return None
    name = os.path.basename(pick)
    return name if "." in name else f"{name}.jpg"


def classify_new_posts(candidates, cache, config):
    """Ask Claude "painting or not?" for posts missing from the cache.

    Mutates and returns the cache. Every failure mode is soft: the post is
    simply left unclassified (treated as a painting) and retried next run.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    # Re-classify anything cached under an older schema version.
    todo = [(p, url) for p, url in candidates
            if cache.get(p["id"], {}).get("v") != CACHE_VERSION]
    if not todo:
        return cache
    if not api_key:
        print(f"::warning::ANTHROPIC_API_KEY not set - {len(todo)} post(s) left "
              "unclassified and included as paintings.")
        return cache
    try:
        import anthropic
    except ImportError:
        print("::warning::anthropic package not installed - skipping AI curation.")
        return cache

    curation = config.get("curation") or {}
    model = curation.get("model") or "claude-opus-4-8"
    client = anthropic.Anthropic(api_key=api_key)

    for post, url in todo:
        try:
            # Fetch the image ourselves and send bytes: Anthropic's URL fetcher
            # is blocked by Instagram's CDN robots.txt.
            with http_get(url) as resp:
                image_b64 = base64.standard_b64encode(resp.read()).decode()
            response = client.messages.create(
                model=model,
                max_tokens=256,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {
                            "type": "base64", "media_type": "image/jpeg",
                            "data": image_b64}},
                        {"type": "text", "text": CLASSIFY_PROMPT.format(
                            caption=(post.get("caption") or "(no caption)")[:1500])},
                    ],
                }],
                output_config={"format": {"type": "json_schema", "schema": CLASSIFY_SCHEMA}},
            )
            if response.stop_reason == "refusal":
                raise RuntimeError("classifier refused")
            text = next(b.text for b in response.content if b.type == "text")
            result = json.loads(text)
            previous = cache.get(post["id"], {})
            cache[post["id"]] = {
                "v": CACHE_VERSION,
                "painting": bool(result["is_painting"]),
                "gallery_ready": bool(result["gallery_ready"]),
                "trimmed": previous.get("trimmed", False),
                "model": model,
                "classified_at": now_iso(),
            }
            label = ("painting, gallery-ready" if result["gallery_ready"]
                     else "painting, context shot") if result["is_painting"] else "not a painting"
            print(f"Classified {post['id']}: {label}")
        except Exception as exc:  # noqa: BLE001 - soft-fail per post, retry next run
            print(f"::warning::Could not classify post {post['id']} ({exc}) - "
                  "including it for now; will retry next sync.")
    return cache


def select_posts(candidates, cache, config):
    """Gallery-ready paintings first; newest few guaranteed; ranked by
    recency-decayed engagement; context-shot paintings only fill leftover
    slots; 12 total."""
    curation = config.get("curation") or {}
    newest_guaranteed = int(curation.get("newestGuaranteed", 3))
    half_life = curation.get("halfLifeDays", 60)
    weights = {**DEFAULT_WEIGHTS, **(curation.get("weights") or {})}

    def score(c):
        return engagement(c[0], weights, half_life)

    # Unclassified posts count as paintings so the gallery never starves.
    paintings = [
        (p, url) for p, url in candidates
        if cache.get(p["id"], {}).get("painting") is not False
    ]
    ready = [c for c in paintings
             if cache.get(c[0]["id"], {}).get("gallery_ready") is True]
    context = [c for c in paintings if c not in ready]

    guaranteed = ready[:newest_guaranteed]  # API returns newest first
    rest = sorted(ready[newest_guaranteed:], key=score, reverse=True)
    selected = (guaranteed + rest)[:MAX_POSTS]
    if len(selected) < MAX_POSTS:  # top up with context shots rather than run short
        selected += sorted(context, key=score, reverse=True)[:MAX_POSTS - len(selected)]
    selected.sort(key=score, reverse=True)
    return selected


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def main():
    token = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "").strip()
    if not token:
        print("::warning::INSTAGRAM_ACCESS_TOKEN is not set - skipping sync, gallery unchanged.")
        return 0

    config = load_json(CONFIG, {})
    curation = config.get("curation") or {}
    data = api_get("me/media", {"fields": FIELDS, "limit": FETCH_LIMIT, "access_token": token})
    candidates = [(p, image_url(p)) for p in data.get("data", [])]
    candidates = [(p, url) for p, url in candidates if url]
    if not curation.get("includeVideos", False):
        candidates = [(p, url) for p, url in candidates if p.get("media_type") != "VIDEO"]
    excluded = set(curation.get("exclude") or [])
    candidates = [(p, url) for p, url in candidates if p["id"] not in excluded]

    if not candidates:
        print("::warning::API returned no displayable posts - gallery unchanged.")
        return 0

    fetch_insights(candidates, token)
    cache = load_json(CLASSIFY_CACHE, {})
    cache = classify_new_posts(candidates, cache, config)
    selected = select_posts(candidates, cache, config)

    if not selected:
        print("::warning::No paintings among fetched posts - gallery unchanged.")
        return 0

    # Download everything to a temp dir first; only touch gallery/ if all succeed.
    tmpdir = tempfile.mkdtemp(prefix="ig-sync-")
    downloads = {}  # filename -> temp path
    posts_out = []
    try:
        for post, url in selected:
            filename = f"{post['id']}.jpg"
            if not os.path.exists(os.path.join(GALLERY, filename)):
                tmp_path = os.path.join(tmpdir, filename)
                with http_get(url) as resp, open(tmp_path, "wb") as out:
                    shutil.copyfileobj(resp, out)
                if os.path.getsize(tmp_path) == 0:
                    raise RuntimeError(f"Empty download for post {post['id']}")
                downloads[filename] = tmp_path
            posts_out.append({
                "id": post["id"],
                "file": f"gallery/{filename}",
                "caption": post.get("caption") or "",
                "permalink": post.get("permalink"),
                "timestamp": post.get("timestamp"),
                "likes": post.get("like_count") or 0,
                "comments": post.get("comments_count") or 0,
                "metrics": post.get("_metrics") or {},
            })

        for filename, tmp_path in downloads.items():
            shutil.move(tmp_path, os.path.join(GALLERY, filename))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    # One-time border crop per post (white mats etc.), tracked in the cache so
    # already-trimmed images are never re-processed.
    for post, _url in selected:
        entry = cache.setdefault(post["id"], {})
        if not entry.get("trimmed"):
            try:
                if trim_border(os.path.join(GALLERY, f"{post['id']}.jpg")):
                    print(f"Cropped border off {post['id']}.jpg")
            except Exception as exc:  # noqa: BLE001 - cosmetic step, never fatal
                print(f"::warning::Border trim failed for {post['id']}: {exc}")
            entry["trimmed"] = True

    # Record real image dimensions (post-crop) so the site can lay the grid
    # out at true aspect ratios without waiting for images to load.
    try:
        from PIL import Image
        for entry in posts_out:
            with Image.open(os.path.join(GALLERY, os.path.basename(entry["file"]))) as im:
                entry["width"], entry["height"] = im.size
    except ImportError:
        pass

    manifest = {
        "source": "instagram",
        "updated": now_iso(),
        "posts": posts_out,
    }
    with open(MANIFEST, "w") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
        f.write("\n")

    # Keep the classify cache to posts still in the fetched window.
    fetched_ids = {p["id"] for p, _ in candidates}
    cache = {pid: v for pid, v in cache.items() if pid in fetched_ids}
    with open(CLASSIFY_CACHE, "w") as f:
        json.dump(cache, f, indent=2, sort_keys=True)
        f.write("\n")

    # Prune images that dropped out of the manifest. Keep the About photo and
    # the About placeholder no matter what.
    keep = {os.path.basename(p["file"]) for p in posts_out}
    keep.update({"manifest.json", "classify-cache.json", "about-placeholder.svg"})
    about = about_photo_file(config)
    if about:
        keep.add(about)
    for name in os.listdir(GALLERY):
        if name not in keep:
            os.remove(os.path.join(GALLERY, name))

    print(f"Synced {len(posts_out)} posts ({len(downloads)} new image(s) downloaded).")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - fail loudly, but leave gallery intact
        print(f"::error::Instagram sync failed, gallery left unchanged: {exc}")
        sys.exit(1)
