#!/usr/bin/env python3
"""Sync the latest Instagram posts from @qhgflies into gallery/.

Fetches recent media via the Instagram API (Instagram Login), downloads any
new images, and rewrites gallery/manifest.json with the 12 newest posts.

Fail-safe by design: nothing in gallery/ is touched until every API call and
every image download has succeeded. On any failure the script exits with an
error and the existing gallery (and therefore the live site) stays as-is.

Requires: INSTAGRAM_ACCESS_TOKEN in the environment. Stdlib only.
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
CONFIG = os.path.join(ROOT, "config.json")

API_BASE = "https://graph.instagram.com/v23.0"
MAX_POSTS = 12
FIELDS = (
    "id,caption,media_type,media_url,thumbnail_url,permalink,timestamp,"
    "children{media_type,media_url,thumbnail_url}"
)
TIMEOUT = 30


def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "qhgflies-site-sync/1.0"})
    return urllib.request.urlopen(req, timeout=TIMEOUT)


def api_get(path, params):
    qs = urllib.parse.urlencode(params)
    with http_get(f"{API_BASE}/{path}?{qs}") as resp:
        return json.load(resp)


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


def about_photo_file():
    """File the About section points at, so pruning never removes it."""
    try:
        with open(CONFIG) as f:
            pick = (json.load(f).get("aboutPhoto") or "").strip()
    except (OSError, ValueError):
        return None
    if not pick:
        return None
    name = os.path.basename(pick)
    return name if "." in name else f"{name}.jpg"


def main():
    token = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "").strip()
    if not token:
        print("::warning::INSTAGRAM_ACCESS_TOKEN is not set - skipping sync, gallery unchanged.")
        return 0

    data = api_get("me/media", {"fields": FIELDS, "limit": 25, "access_token": token})
    candidates = []
    for post in data.get("data", []):
        url = image_url(post)
        if url:
            candidates.append((post, url))
        if len(candidates) == MAX_POSTS:
            break

    if not candidates:
        print("::warning::API returned no displayable posts - gallery unchanged.")
        return 0

    # Download everything to a temp dir first; only touch gallery/ if all succeed.
    tmpdir = tempfile.mkdtemp(prefix="ig-sync-")
    downloads = {}  # filename -> temp path
    posts_out = []
    try:
        for post, url in candidates:
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
            })

        for filename, tmp_path in downloads.items():
            shutil.move(tmp_path, os.path.join(GALLERY, filename))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    manifest = {
        "source": "instagram",
        "updated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "posts": posts_out,
    }
    with open(MANIFEST, "w") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
        f.write("\n")

    # Prune images that dropped out of the manifest. Keep the About photo and
    # the About placeholder no matter what.
    keep = {os.path.basename(p["file"]) for p in posts_out}
    keep.add("manifest.json")
    keep.add("about-placeholder.svg")
    about = about_photo_file()
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
