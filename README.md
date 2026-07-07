# qhgflies — commission site

Single-page static site, served by GitHub Pages at
**https://thekeeks.github.io/qhgflies/**. The gallery syncs itself from
[@qhgflies](https://instagram.com/qhgflies) every 6 hours.

**New here? Follow [SETUP.md](SETUP.md)** — Pages, the Meta app, and the two
secrets. Everything after that is automatic.

## How it fits together

| Piece | What it does |
| --- | --- |
| `index.html`, `assets/` | The whole site — no build step. |
| `gallery/manifest.json` | The 12 posts the gallery shows (newest first). |
| `config.json` | Site options — set `aboutPhoto` to a gallery filename to pick the About photo. |
| `scripts/sync_instagram.py` | Downloads new posts + captions, rewrites the manifest. Touches nothing on failure. |
| `.github/workflows/sync-instagram.yml` | Runs the sync every 6 hours, commits, redeploys. |
| `.github/workflows/deploy.yml` | Publishes to GitHub Pages on every push. |
| `.github/workflows/refresh-token.yml` | Renews the Instagram token weekly so it never expires. |

## Everyday edits

- **Prices / text**: edit `index.html`, push — the site redeploys itself.
- **About photo**: put a filename from `gallery/` into `aboutPhoto` in
  `config.json`.
- **Gallery**: don't edit by hand — the sync will overwrite it. Post on
  Instagram instead; the site follows within 6 hours.
