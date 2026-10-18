# Plan: Troubadour landing page at https://troubadour.5eapps.com

The app was renamed from Bard to Troubadour. The site moved from bard.5eapps.com (it redirects) and the Vercel project is now `troubadour-site`.

A one-page static site that explains Troubadour and links to the code and to Troubadour Cloud. **Troubadour itself is not
hosted**: it records the room's microphone and runs Whisper and the LLM on the user's Mac (repo rule: local only).

Executor: follow the steps in order. Every step ends with a check; don't move on until it passes.

## Hard rules (from AGENTS.md; break none)

1. Never read, copy or screenshot anything in `backend/data/` (real players, recordings, campaign notes).
2. Never play audio, never start a mic session, never restart the server on :8765.
3. No real people's names anywhere. Example text uses made-up names (Mira, Tobin, Elara, the GM).
4. No JavaScript, no external fonts, CDNs, analytics, trackers or embeds. The page makes zero third-party
   requests (Troubadour's whole pitch is privacy).
5. No Wizards of the Coast logos, art, fonts or trade dress. Say "D&D" only to describe what Troubadour is for
   ("your D&D table"), never as part of a name or heading. Keep the "unofficial" notice in the footer.
6. Claim only what `README.md` says Troubadour does. No Windows/Linux support, no pricing beyond "free".
7. `yarn check` must still pass before committing (the site isn't part of it; this proves nothing else broke).

## Files to create

| Path | What |
|---|---|
| `site/index.html` | the page, semantic HTML5 |
| `site/styles.css` | all styles; light and dark (`prefers-color-scheme`) |
| `site/favicon.svg` | copy of `web/public/favicon.svg` |
| `site/vercel.json` | security headers (below) |
| `.gitignore` | add the line `site/.vercel` |

Keep `site/PLAN.md` (this file) as the site's design note.

## Step 1: write the page

Structure of `index.html`, top to bottom. Use this copy as written; small wording fixes are fine, new claims are not.

1. `<head>`: `lang="en"`, charset, viewport, `<title>Troubadour: live transcripts and a campaign wiki for your table</title>`,
   `<meta name="description" content="Troubadour transcribes your in-person tabletop game from one microphone, shows who said what, and keeps a campaign wiki. Everything runs on your Mac.">`,
   `<link rel="icon" href="/favicon.svg" type="image/svg+xml">`, `<link rel="stylesheet" href="/styles.css">`.
2. **Hero**
   - `<h1>Troubadour</h1>`
   - Lead: "Live transcripts and a growing campaign wiki for your in-person D&D table, from one shared microphone."
   - Sub: "Everything runs on your Mac: speech-to-text, speaker recognition and the AI. No audio or text leaves the machine."
   - Buttons: **Get Troubadour on GitHub** → `https://github.com/yipe/troubadour` ; **Troubadour Cloud backup** → `https://cloud.5eapps.com`
3. **Example transcript** (pure HTML/CSS, made-up; an illustration, not a screenshot). A card with 4 lines,
   each with a speaker chip and text:
   - GM: "The door is carved with a drowned city. Ül Dar, if you can read the old script."
   - Mira: "I can. I want to check it for traps first."
   - Tobin: "Elara owes someone money in Ül Dar, right?"
   - Voice 3: "That's me, I'm Elara." (render "Voice 3" as an unlabeled voice: dashed chip)
   - Caption under the card: "Unknown voices show up as Voice 1, Voice 2…; tap a name once and every line from that voice is relabeled."
4. **What it does** (`<h2>`), a grid of 6 items, each a short `<h3>` + one sentence:
   - Live transcript: "Who is speaking and what they said, as the game happens."
   - No setup recording: "Name a voice once, mid-game; an optional 15–30 second sample helps quiet players."
   - Character voices: "A GM's distinct NPC voice can be its own character."
   - Names spelled right: "Troubadour learns your campaign's names and fixes misheard ones, keeping what was heard."
   - Campaign wiki: "Session write-ups, facts and new names, as plain Markdown you can edit in Obsidian."
   - Ask about the campaign: "Ask a question; Troubadour searches every transcript and note and shows its sources."
5. **Private by design** (`<h2>`): 3 bullets:
   - "Runs on one Apple Silicon Mac. No account, no cloud, no subscription."
   - "Recordings and voiceprints stay in a folder on your Mac. Ask your table before you record."
   - "Troubadour Cloud backup is optional and off until you connect it."
6. **What you need** (`<h2>`), list:
   - "A Mac with Apple Silicon"
   - "[LM Studio](https://lmstudio.ai/) for spelling cleanup and write-ups (transcription works without it)"
   - "A free [Hugging Face](https://huggingface.co/) account (speaker-recognition model)"
   - "A microphone in the middle of the table (an omnidirectional or boundary mic works best)"
7. **Install** (`<h2>`): one line "Troubadour is open source and installs from the command line:", then a `<pre><code>`
   block copied from README "Quick start" (clone line first: `git clone https://github.com/yipe/troubadour && cd troubadour`),
   then "Full instructions: [README on GitHub](https://github.com/yipe/troubadour#readme)".
8. **Troubadour Cloud** (`<h2>`): "Optional backup for your campaign notes, with history, a read-only web view and share
   links for your players. Free with a DPRcalc account." Button → `https://cloud.5eapps.com`. Small links:
   [Terms](https://cloud.5eapps.com/terms) · [Privacy](https://cloud.5eapps.com/privacy).
9. **Footer**: "Troubadour is open source ([MIT](https://github.com/yipe/troubadour/blob/main/LICENSE)). Made by
   [DPRcalc](https://dprcalc.com)." Second line, smaller: "Unofficial; not affiliated with or endorsed by Wizards
   of the Coast. Troubadour's built-in rules vocabulary comes from the System Reference Document 5.1/5.2
   ([CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/legalcode))."

Style (`styles.css`): system font stack, max width ~960px centered, generous spacing, one accent colour, cards
with a subtle border, buttons as links styled as buttons, responsive grid (3 columns ≥ 900px, 2 ≥ 600px, else
1), visible focus outlines, text contrast ≥ 4.5:1 in both themes. No CSS frameworks.

**Check:** run each of these from the repo root; each must print nothing:

```sh
grep -nE "<script|@import|fonts.googleapis|cdn\.|analytics|gtag" site/index.html site/styles.css
grep -noE "(src|href)=\"https?://[^\"]+\.(css|js|woff2?|png|jpg|svg)\"" site/index.html
grep -rn "backend/data" site/
```

## Step 2: headers (`site/vercel.json`)

```json
{
  "headers": [
    {
      "source": "/(.*)",
      "headers": [
        { "key": "Content-Security-Policy", "value": "default-src 'none'; style-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'" },
        { "key": "X-Content-Type-Options", "value": "nosniff" },
        { "key": "Referrer-Policy", "value": "strict-origin-when-cross-origin" },
        { "key": "Permissions-Policy", "value": "camera=(), microphone=(), geolocation=()" }
      ]
    }
  ]
}
```

No inline `style=""` attributes in the HTML (the CSP blocks them).

**Check:** `node -e 'JSON.parse(require("fs").readFileSync("site/vercel.json","utf8"))'` exits 0.

## Step 3: look at it (headless, muted)

From `site/`, run a local static server, e.g. `python3 -m http.server 8790` (never port 8765), then screenshot
with headless Chrome, muted:

```sh
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --mute-audio --hide-scrollbars \
  --user-data-dir=/tmp/troubadour-site-shot --window-size=1280,2400 --screenshot=/tmp/troubadour-site-desktop.png http://127.0.0.1:8790/
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --mute-audio --hide-scrollbars \
  --user-data-dir=/tmp/troubadour-site-shot --window-size=390,3600 --screenshot=/tmp/troubadour-site-mobile.png http://127.0.0.1:8790/
```

**Check:** open both PNGs and look: nothing overflows sideways on mobile, the transcript card reads clearly, the
footer notice is present. Repeat once with dark mode (`--force-dark-mode`). Stop the static server afterwards.

## Step 4: deploy to Vercel (team `dprcalc`, new project `troubadour-site`)

5eapps.com's DNS is already in the `dprcalc` Vercel team.

```sh
cd site
vercel link --yes --scope dprcalc --project troubadour-site   # creates the project; .vercel/ is git-ignored
vercel deploy --prod --yes
vercel domains add troubadour.5eapps.com troubadour-site
```

This file isn't deployed (`site/.vercelignore`).

Since 2026-10-18 the project is connected to GitHub (`yipe/troubadour`, Root Directory `site`, production branch
`main`): a push to `main` deploys it, and an ignored-build command skips pushes that don't touch `site/`. The
commands above are only for first setup or an emergency deploy.

If `vercel link` asks for settings: framework "Other", no build command, output directory `.` (this folder).

**Check** (all must hold):

```sh
curl -s -o /dev/null -w "%{http_code}\n" https://troubadour.5eapps.com/            # 200
curl -sI https://troubadour.5eapps.com/ | grep -i "content-security-policy"          # present
for u in https://troubadour.5eapps.com/styles.css https://troubadour.5eapps.com/favicon.svg https://github.com/yipe/troubadour https://cloud.5eapps.com https://cloud.5eapps.com/terms https://cloud.5eapps.com/privacy https://dprcalc.com; do
  printf "%s " "$u"; curl -sL -o /dev/null -w "%{http_code}\n" "$u"; done        # every one 200
```

## Step 5: commit

1. `yarn check` passes.
2. `git add site .gitignore` (not `site/.vercel`), commit: `Landing page for Troubadour (static, no scripts, no third-party requests)`.
3. Push the way the repo does (`AGENTS.md`: push to `origin main`).

Report back: the live URL, the two screenshots' paths, and the output of the Step 4 checks.

## Later (not part of this task)

- Real screenshots of Troubadour, made from a scratch data folder (`TROUBADOUR_DATA=/tmp/troubadour-demo`, made-up players), never
  from `backend/data/`.
- Connect the `troubadour-site` project to the GitHub repo (Root Directory `site`) so pushes deploy by themselves.
