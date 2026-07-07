# One-time setup

Three things to do by hand, in order. Everything else is automated. Budget
~20 minutes.

1. Turn on GitHub Pages (2 min)
2. Create a Meta app and get an Instagram access token (10–15 min)
3. Add two GitHub secrets (5 min)

---

## 1. Turn on GitHub Pages

The deploy workflow enables Pages by itself on its first successful run, so
usually there's nothing to do — check the **Actions** tab for a green
**Deploy to GitHub Pages** run and the site is live at
**https://thekeeks.github.io/qhgflies/**.

If that run is red with a "Get Pages site failed" error, enable it by hand
once: repo **Settings** → **Pages** → under **Build and deployment →
Source** choose **GitHub Actions**, then re-run the workflow from the
Actions tab.

That's it — every future push (and every Instagram sync) redeploys
automatically. A truly custom domain (like `commissions.qhgflies.com`) would
need you to own a domain and set DNS records, so it's skipped; the
`thekeeks.github.io/qhgflies` URL costs nothing and never expires.

## 2. Meta developer app + Instagram access token

You'll create a (free) Meta developer app whose only job is to let a script
read your own Instagram feed. Nothing gets posted, and because you're only
accessing your own account the app never needs Meta's review/approval — it
can stay in "development mode" forever.

Your Instagram must be a **Professional account** (Creator counts — you're
set).

### 2a. Register as a Meta developer

1. Go to **https://developers.facebook.com** and log in. Use the Facebook
   account you'd normally use — or create one just for this. (Instagram
   logins work on this site too via "Log in with Instagram" if offered.)
2. Click **Get Started** (top right) and accept the developer terms. It may
   ask to verify your email or phone. Once done you land on the developer
   dashboard.

### 2b. Create the app

1. Click **My Apps** (top right) → **Create App**.
2. If asked about a **business portfolio**, choose **"I don't want to connect
   a business portfolio yet"** — you don't need one.
3. For the **use case**, pick the Instagram one — it's labeled something like
   **"Manage messaging and content on Instagram"** or just **Instagram**
   ("Build with the Instagram API"). Meta renames these periodically; you
   want the option that mentions the Instagram API.
4. App name: anything, e.g. `qhgflies site sync`. Contact email: yours.
   Click **Create App** (it may ask for your Facebook password to confirm).

### 2c. Connect @qhgflies and generate the token

1. In the app dashboard's left sidebar, click **Instagram** → **API setup
   with Instagram login**. (Not "API setup with Facebook login".)
2. Under **Step 1: Generate access tokens**, click **Add account** and log in
   as **@qhgflies**. Approve the permissions it asks for (it only needs the
   basic "access profile and media" one, `instagram_business_basic`).
3. Your account now appears in the list with a **Generate token** button.
   Click it, log in again if prompted, and **copy the token** — a very long
   string starting with `IG`. This is already a **long-lived token, valid 60
   days** (and the weekly workflow will keep renewing it before it expires —
   see step 3).

Keep that token somewhere safe for the next two minutes. Treat it like a
password — anyone who has it can read your Instagram media.

> If a screen mentions app review or "advanced access": ignore it. Reading
> your own account's media works in development mode with standard access.

## 3. GitHub secrets

The token is stored as an encrypted GitHub Actions secret — it never appears
in the code or the published site.

### 3a. `INSTAGRAM_ACCESS_TOKEN`

1. Repo → **Settings** → **Secrets and variables** → **Actions** →
   **New repository secret**.
2. Name: `INSTAGRAM_ACCESS_TOKEN`. Value: paste the token from step 2c.

### 3b. `SECRETS_ADMIN_PAT` (lets the token refresh itself)

Instagram tokens die after 60 days. A weekly workflow calls Instagram's
refresh endpoint and saves the renewed token back into the secret above —
but GitHub's built-in workflow token isn't allowed to edit secrets, so it
needs a small personal access token from you:

1. Go to **https://github.com/settings/personal-access-tokens** →
   **Generate new token** (fine-grained).
2. Name: `qhgflies token refresh`. Expiration: pick the longest offered
   (1 year — put a calendar reminder to re-issue it).
3. **Repository access**: "Only select repositories" → `thekeeks/qhgflies`.
4. **Repository permissions**: set **Secrets** to **Read and write**. Leave
   everything else at "No access".
5. Generate, copy, and save it as a second repo secret named
   `SECRETS_ADMIN_PAT` (same as step 3a).

## 4. Test it

1. **Actions** tab → **Sync Instagram gallery** → **Run workflow**. When it
   goes green, the placeholders are replaced by your 12 latest posts and the
   site redeploys itself.
2. **Actions** tab → **Refresh Instagram token** → **Run workflow** — should
   also go green (it's fine to run any time; the token must be at least 24
   hours old to refresh, so if it fails on day one just ignore it — the
   weekly schedule will succeed).

## Ongoing behavior

- **Every 6 hours** the sync runs; when you've posted something new it
  commits the image + caption and redeploys. No new posts → no commit, no
  deploy.
- **Every Monday** the token is refreshed (each refresh grants a fresh 60
  days, so it never expires).
- **If Instagram's API errors**, the run shows red in the Actions tab and
  you'll get an email from GitHub, but the site keeps serving the existing
  gallery — it can't break from a failed sync.
- **Picking your About photo**: after the first sync, open `gallery/` in the
  repo, pick the image you like, and put its filename (e.g.
  `17912345678901234.jpg`) in the `aboutPhoto` field of `config.json`.
- **Note**: GitHub pauses cron schedules if a repo has no activity for 60
  days. The sync's own commits normally keep it alive, but if you ever stop
  posting for a couple of months, GitHub will email you a "workflows
  disabled" notice — one click re-enables them.
