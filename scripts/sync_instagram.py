#!/usr/bin/env python3
"""Sync the best of @qhgflies's Instagram posts into gallery/.

Pipeline per run:
  1. Fetch the ~50 most recent posts (with like/comment counts) via the
     Instagram API (Instagram Login).
  2. Classify each not-yet-seen post as painting / not-painting using Claude
     (image + caption). Verdicts are cached in gallery/classify-cache.json and
     committed, so each post is classified exactly once.
  3. Select 12 posts: paintings only; the newest few paintings are always
     included; remaining slots go to the highest-engagement paintings
     (likes + 2 x comments). Displayed most-engaged first.
  4. Download any new images and rewrite gallery/manifest.json.

Fail-safe by design: nothing in gallery/ is touched until every API call and
every image download has succeeded. On any Instagram failure the script exits
with an error and the existing gallery (and the live site) stays as-is.
AI curation degrades gracefully: no ANTHROPIC_API_KEY, a missing anthropic
package, or a failed classification just means the affected posts are treated
as paintings (and re-classified next run) - the site never goes empty.

Env: INSTAGRAM_ACCESS_TOKEN (required), ANTHROPIC_API_KEY (optional).
"""

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

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "is_painting": {
            "type": "boolean",
            "description": "true if the post shows the artist's own painting/drawing "
                           "(finished or work-in-progress), false for anything else",
        },
    },
    "required": ["is_painting"],
    "additionalProperties": False,
}

CLASSIFY_PROMPT = (
    "You are curating a painter's commission portfolio from their Instagram feed. "
    "Decide whether this post shows the artist's own artwork - a painting or drawing, "
    "finished or in progress, including framed/installed shots where the artwork is "
    "the subject. Posts that are primarily anything else (selfies, people, places, "
    "events, announcements, memes, screenshots) are not portfolio pieces.\n\n"
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


def engagement(post):
    return (post.get("like_count") or 0) + 2 * (post.get("comments_count") or 0)


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
    todo = [(p, url) for p, url in candidates if p["id"] not in cache]
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
            response = client.messages.create(
                model=model,
                max_tokens=256,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "url", "url": url}},
                        {"type": "text", "text": CLASSIFY_PROMPT.format(
                            caption=(post.get("caption") or "(no caption)")[:1500])},
                    ],
                }],
                output_config={"format": {"type": "json_schema", "schema": CLASSIFY_SCHEMA}},
            )
            if response.stop_reason == "refusal":
                raise RuntimeError("classifier refused")
            text = next(b.text for b in response.content if b.type == "text")
            verdict = bool(json.loads(text)["is_painting"])
            cache[post["id"]] = {
                "painting": verdict,
                "model": model,
                "classified_at": now_iso(),
            }
            print(f"Classified {post['id']}: {'painting' if verdict else 'not a painting'}")
        except Exception as exc:  # noqa: BLE001 - soft-fail per post, retry next run
            print(f"::warning::Could not classify post {post['id']} ({exc}) - "
                  "including it for now; will retry next sync.")
    return cache


def select_posts(candidates, cache, config):
    """Paintings only; newest few guaranteed; rest by engagement; 12 total."""
    curation = config.get("curation") or {}
    newest_guaranteed = int(curation.get("newestGuaranteed", 3))

    # Unclassified posts count as paintings so the gallery never starves.
    paintings = [
        (p, url) for p, url in candidates
        if cache.get(p["id"], {}).get("painting") is not False
    ]
    guaranteed = paintings[:newest_guaranteed]  # API returns newest first
    rest = sorted(paintings[newest_guaranteed:], key=lambda c: engagement(c[0]), reverse=True)
    selected = (guaranteed + rest)[:MAX_POSTS]
    # Display order: crowd favorites first.
    selected.sort(key=lambda c: engagement(c[0]), reverse=True)
    return selected


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def main():
    token = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "").strip()
    if not token:
        print("::warning::INSTAGRAM_ACCESS_TOKEN is not set - skipping sync, gallery unchanged.")
        return 0

    config = load_json(CONFIG, {})
    data = api_get("me/media", {"fields": FIELDS, "limit": FETCH_LIMIT, "access_token": token})
    candidates = [(p, image_url(p)) for p in data.get("data", [])]
    candidates = [(p, url) for p, url in candidates if url]

    if not candidates:
        print("::warning::API returned no displayable posts - gallery unchanged.")
        return 0

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
            })

        for filename, tmp_path in downloads.items():
            shutil.move(tmp_path, os.path.join(GALLERY, filename))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

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
