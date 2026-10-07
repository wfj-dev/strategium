# Watch Fortress Jericho Strategium

A single-page org chart web app for the Watch Fortress Jericho roster. The current build includes the embedded frontend and a small backend for signed bot snapshots, searchable member dossiers, company views, specialist formations, Discord OAuth, and user-owned backstory editing.

## Getting Started

Open `jericho-strategium.html` directly in a browser.

For the live local app with the backend:

1. Run the one-time local secret setup script:
```bash
./setup-secrets.sh
```
*(Optionally pass your Discord token: `./setup-secrets.sh <DISCORD_TOKEN>`)*

2. Start the Strategium backend:
```bash
python3 server.py
```

Then open `http://127.0.0.1:8787/`. The backend automatically loads `.env`, serves the page, receives signed bot snapshots, serves merged roster data, and owns authenticated backstory edits.

To show the compact Discord join mark beside authentication, set a permanent invite in `.env`:

```bash
STRATEGIUM_DISCORD_INVITE_URL=https://discord.gg/your-invite
```

Only HTTPS `discord.gg/<code>` and `discord.com/invite/<code>` URLs are accepted. The control remains hidden when the setting is absent or invalid.

## Live Roster Data

The live app loads roster data from its same-origin `/api/roster` endpoint. The Discord bot publishes signed snapshots to the backend's internal `/internal/roster/snapshot` endpoint; browser clients never receive bot credentials.

## Submit AARs

Install the website runtime dependencies with `.venv/bin/python -m pip install -r requirements.txt` before starting the server with that interpreter. QR codes are generated locally; handoff tokens are never sent to an external QR provider.

The AAR pilot is restricted to members holding the **High Command** or **Watch Techmarine** Discord role. The bot configuration defaults to `web_submission.access_mode: "staff"`; change it to `"members"` later to allow all logged-in guild members. The tab is hidden for other accounts, and direct routes, submissions, QR creation, and handoff retrieval enforce authorization server-side. The website displays the member's guild display name rather than their account username. If the private bot bridge cannot verify roles, AAR access fails closed.

The screenshot picker accepts file selection, clipboard image paste, and drag-and-drop. PC/Steam players can paste a copied endgame capture or drop a saved image. Console players can save captures to their phone using the Xbox/PlayStation app, then use **Add From Phone** on the AAR draft: the QR code opens an upload-only page and incoming screenshots appear in the desktop previews. Users must review and submit from the authenticated draft; the phone link cannot submit an AAR or read roster/evidence data.

Phone links expire after ten minutes, can be closed by the draft owner, and are revoked after successful submission. Evidence is staged in memory only (maximum 16 active handoffs / 64 MiB globally, 10 images / 32 MiB per handoff) and disappears on restart. Incoming phone evidence is still subject to the draft's combined screenshot limits. Use the publicly reachable HTTPS site origin for `STRATEGIUM_ALLOWED_ORIGIN`; a localhost QR URL is not reachable from another device. Keep the link private until closed. The phone page strips the token fragment from its displayed URL and sends no referrer.

Phone uploads verify image structure and MIME/format agreement, reject animated images and images above 40 megapixels, and recheck the owner's pilot permission. QR creation shares the global upload slots and has a 30-second per-owner cooldown. Body reads time out after 20 seconds so a stalled upload cannot hold a slot indefinitely. Production still requires HTTPS, the private bot bridge, and upstream request-rate controls.

The **Submit AARs** channel at `/submit-aar` provides authenticated guild members with a mode-aware AAR form. It requires 1–10 PNG, JPEG, or WebP screenshots. The per-image limit follows Discord's current guild upload limit (capped at 32 MiB by the application), and the combined limit is 32 MiB. The site accepts at most two concurrent uploads; the bot independently caps two in-flight requests and 48 MiB total buffered. The bot validates the structured fields, writes the canonical record and processes points/challenges/awards through its datastore, then posts a separate lore-styled Discord embed with the screenshot previews. The receipt is not parsed as an AAR; its real Discord URL is stored with the record for audit and challenge references.

Run `./setup-secrets.sh` to provision the shared `STRATEGIUM_BOT_AAR_SHARED_SECRET` in both local `.env` files. The site forwards uploads to `STRATEGIUM_BOT_AAR_INTAKE_URL` (default `http://127.0.0.1:8080/v1/aar/submissions`) with a timestamped HMAC; the browser never receives the secret. The sibling `/v1/aar/access` route verifies roles and guild display names. Remote HTTPS targets must be explicitly listed in `STRATEGIUM_BOT_AAR_ALLOWED_HOSTS` as comma-separated hostnames. Enable the bot route explicitly with `web_submission.enabled: true` in the bot configuration. Keep the bot bridge private and do not expose `/v1/aar/*` through a public reverse proxy. The existing staff-only `/submit_aar` slash command and its test mode are unchanged.

## Rank Guide

Visit `/rank-guide` for the 28 numbered Watch Fortress rank cards and their promotion requirements. Checked-in full-size WebPs in `assets/web/rank-cards/` are sufficient to regenerate gallery previews with `.venv/bin/python scripts/build_web_assets.py`. When source PNGs are available in the sibling bot repository at `../discord-bots/op-scribe-servitor/assets/ranks/`, the builder uses them to regenerate both sizes; set `STRATEGIUM_RANK_CARDS_SOURCE` to override that path.

The Promotion System sections are available in both the track gallery and individual track views. They cover the 2/3/4/5/6-point mission tiers (Crucible awards 6), Omega's bot-authoritative 20 operation points minus parsed KIA (KIA clamped to 0-4, yielding 16-20 points independently of squad size; two to five brothers and an explicit KIA line required), successful-report/team/approved-armor eligibility with Discord links, oaths beyond Watch Veteran, Watch Command proclamations and mandatory Techmarine compliance checks. Service studs require both time and points: Plasteel is 4 weeks plus 400 AAR points; Auramite is 16 weeks plus 1,600 AAR points.

## Record of Blood

The interface is framed as an Ordo Xenos analyst's Cogitator for investigating activity in the Jericho Reach. The main channels are **Personnel Docket**, **Sector Operations**, and **Chapter Origins**; the shared Return control restores the previous view and focus where possible.

Sector Operations reads the versioned hierarchy in `data/reach_geography.json` from `/api/geography` and renders a continuous HTML5 Canvas chart. Wheel zoom anchors beneath the cursor, drag pans, and selecting a sector focuses it. Selecting a resolved star or orbiting body opens its system dossier immediately. Dossiers and hover text show useful body/stellar classifications and routes, without internal source or provisional-name labels. The viewport matches the artwork's aspect ratio and the camera uses cover-fit and bounded panning, so there are no black margins behind the sector image. Close zoom retires the raster artwork in favour of the static starfield and orbital system rendering.

The chart now contains **29 sectors**. The central circle, sector 1, is **Jericho Reach**. Sectors 02-29 remain unnamed and uncharted: Secure denotes their initial sector state, not an invented population or affiliation. Every sector starts Secure. Secure highlight layers appear only for sector hover/selection; zooming outward clears sector selection rather than latching its highlight after returning to the overview. Critical and Lost layers persist on the strategic view when explicitly assigned. `critical` uses the supplied `CONTESTED` exports. Sector statuses are independent of directive statuses, and their future campaign authority is still undecided.

The three numbered source sets are in `assets/JERICHO MAP - SECURE/`, `assets/JERICHO MAP - CONTESTED/`, and `assets/JERICHO MAP - LOST/`. Each export contains the complete backdrop, so `scripts/build_web_assets.py` derives their common unhighlighted base, lossless transparent overlays, and numbered geometry under `assets/web/galactic-map/`. The sectors follow the artwork's enclosed green boundaries; painted-line junctions are not assigned to fictional extra sectors. Original exports remain untouched.

The Fortress Atlas uses sequenced pointer lookups, a small hit-mask neighborhood fallback, and DOM-aware idempotency so Black Vault hover cannot leave stale state with a cleared leader. At 760 px or narrower it becomes a tap-first fortress directory beneath a sticky atlas: selecting a section lights its layer and reveals its lore and an access control.

On screens at 600 px or narrower, the map is replaced by a tap-first Sector Navigator. It lists all 29 sector states, the Reach's named locations, and explicit Uncharted states elsewhere. Selecting a location opens the same body/route dossier as the desktop chart.

The Reach retains **86 original-map locations plus Watch Fortress Jericho**, grouped into **22 systems with 109 bodies: 87 retained locations and 22 modeled stars**. Each system contains **three to five existing locations**, selected by deterministic nearest-location clustering in the chart's original relative layout. No companion planets, moons, belts or other new non-stellar locations are generated. Watch Fortress Jericho remains the sole homebrew non-stellar exception. Original-map bodies retain their names and stable IDs. The two fortress systems occupy opposite parts of the Reach, over 400 chart units apart.

The shared-system groupings and stars are provisional display modeling, not established canon. Stars have individual draft names such as **Vigil Lux**, **Vespera**, **Solis Votum** and **Ferrum Lux**, instead of `<Planet> Primary`. Each has an explicit **O/B/A/F/G/K/M spectral class**, with matching blue, blue-white, white, yellow-white, yellow, light-orange or orange-red coloring and the supplied icon sheet's corresponding symbol in tooltips/dossiers. Rare green/purple stars and exotic stellar anomalies are not assigned automatically. Star and system naming/classification remain subject to the lore team.

The original artwork inventory is explicit in `scripts/build_reach_geography.py`; the Castolel graph identifier remains stable while its display name is Castobel. Exul, the added Recidious/Kadaku/Avarax/Demerium locations, duplicate Hestus/Ravacene graph anchors and procedural periphery/outpost/expanse systems remain excluded. Group centers follow the nearby original location positions, with deterministic adjustments for full orbital clearance. Initial orbital angles retain the locations' relative directions around their shared center. All orbit envelopes remain inside sector 1 and are separated from neighbouring systems. Orbital motion is deliberately slow, with outer bodies moving more slowly; reduced motion freezes it. Body hit testing and hover callouts follow the same animated coordinates. Existing world classifications are retained, without invented worlds or faction-specific additions.

Eastern tendrils are an animated **unnamed approaching threat**, not a claim of occupation, a named fleet, or a new campaign history. The supplied `assets/Leviathan_Tendril.png` replaces the procedural branches, with its original colors and transparency preserved in a lossless browser asset. The artwork is displayed 35% smaller against the map's top-right corner. Internal detail flows slowly down-left toward the map's middle while the silhouette and size stay fixed. Two overlapping flow phases keep the motion continuous without a visible reset, growth, or waves. The overlay follows map pan/zoom and fades out at deeper zoom. Reduced motion or unavailable WebGL displays the static artwork. It does not alter the all-Secure initial state. Faction names, fleet identities and lore outside the original Reach map and Watch Fortress Jericho remain reserved for the lore team; no active GSC, Necron, or other hostile faction holdings are added.

Classification icons are cropped from `assets/40k map icons.webp` and used only in tooltips/dossiers, not as map marker replacements or inferred affiliation badges. The supplied sheet credits **Purple Wyrm / TheMightyGoatMan**, with selected icon inspirations credited to **s3xyrandal**, and states that the unofficial icons are free to use, distribute and share. Its Games Workshop ownership/disclaimer remains on the untouched source sheet. Crops are generated under `assets/web/map-icons/`.

The map camera cannot pan past the edges of the supplied sector artwork. The starfield, nebula, and tendril flow are composited in one visible WebGL background layer at the map's frame cadence, without offscreen GPU-to-Canvas copies, reduced-resolution tendrils, or low-refresh animation caches. A transparent foreground Canvas keeps sector highlights, stars, labels, hover feedback, and camera interactions aligned with the background. Reduced motion freezes both effects; unavailable or lost WebGL uses the original static artwork. The GPU context is released when leaving the map.

Browsers receive downscaled WebP images generated by `.venv/bin/python scripts/build_web_assets.py` (pauldrons, rank ribbons, symbols, atlas, and sparse fortress highlight layers). Re-run it after adding or repainting artwork. Media is cached for five minutes and then revalidated (`Last-Modified`), so repainted art reaches browsers promptly.

The interface uses restrained terminal ambience across every page: an infrequent 36-second raster sweep, persistent scanlines, occasional signal flicker, brief low-amplitude phosphor dips on terminal text, nebular drift, and gentle planetary signal breathing. These layers never accept pointer input. `prefers-reduced-motion: reduce` removes moving sweeps and text flicker, then freezes the remaining treatment to a faint static scanline.

Regenerate map assets first, then rebuild the vetted hierarchy from the bot's read-only legacy graph (OpenCV, NumPy and Pillow are available in the project `.venv`):

```bash
.venv/bin/python scripts/build_web_assets.py --galactic-map-only
.venv/bin/python scripts/build_reach_geography.py       # validate and preview counts
.venv/bin/python scripts/build_reach_geography.py --write
.venv/bin/python -m pytest test_geography.py test_security.py
```

The builder groups approved original-map locations and the fortress exception, adds only provisional stars, validates references, and writes atomically. Warp links inside a shared system are omitted from the inter-system graph; parallel authored links between groups collapse to their lowest original transit cost, rather than costs inferred from relocated pixels. It does not edit bot campaign records or reintroduce excluded locations from active directives. The server serves only explicitly allowlisted generated map layers and classification/spectral icons. Chart coordinates, orbital scales and approach paths are schematic layout hints, not lore distances or canon invasion coordinates.

The root route opens with a skippable, session-scoped Cogitator startup and simulated Inquisitorial credential check, then reveals the interactive Watch Fortress Jericho atlas. Pointer hit testing uses the green-outline pixel mask in `assets/atlas-hover-mask.png`, generated from the aligned section exports by running `python3 scripts/build_atlas_hover_mask.py`; keyboard navigation uses the SVG section paths. Hovered sections swap to their corresponding full-canvas highlights from `assets/Jericho Fortress Layers/` and show a terminal-style lore callout. Specialist and company locations open their existing dossiers, while flight locations open Sector Operations. On narrow screens the atlas preserves readable detail through horizontal panning. The base atlas is served at `/assets/watch-fortress-jericho-map.png`, the hit mask at `/assets/atlas-hover-mask.png`, and section states through the fixed allowlist at `/assets/fortress-layers/<layer>.png`; `assets/jericho map with legend.png` remains a development reference.

Formation chart tiles and dossiers use the supplied specialist insignia in place of generic cadre dots: `Armory.png`, `Apothecarion.png`, `Librarians.png`, `Reclusiam.png`, `Recon.png` for the Black Vault, and `Watch_Blades.png` for the Hall of Blades. Existing company sigils remain unchanged. The server serves the six formation files through `/assets/formation-symbols/<formation-key>.png`.

Company, formation, and Kill Team views display `RENOWN: <tier>` as the rank, with numeric REP and completed directives as separate values. Kill Team views also display the tier unlocks defined by the bot: Unproven/Initiated have no unlocks; Vigilant unlocks Cloaks; Sworn adds Iron Halos; Hallowed adds crested helms (except Victrix Guard); Eternal unlocks a feature in Jericho lore. The publisher only defines unlock descriptions for Kill Teams, so company and formation views do not invent unlocks. Formation honours use the existing challenge-ribbon display; when there are no awarded ribbons, no honours panel is rendered. Marine dossiers place recorded award ribbons between the marine's identity and service studs, aligned without a separator. The Personnel Docket's only blinking cursor sits at the end of its terminal footer.

Visit `/record-of-blood` for the Chapter-origin index. It shows one pauldron for each home Chapter represented by brothers currently serving at Watch Fortress Jericho, excluding Black Shield and unrecorded origins. Use the arrows to browse twelve Chapters per page at any viewport size. Each compact Chapter record expands on hover or keyboard focus to show up to five longest-serving members, including rank, name, and service years; select one to open its Chapter lore and complete roster in a compact dossier. Lore is read from the bot's `reference/chapters.json` when the repositories are side by side; set `STRATEGIUM_CHAPTERS_REFERENCE_PATH` when they are stored elsewhere. Without a live roster, the archive shows a relay/empty state rather than sample Chapters. A subtle blinking cursor and scanline treatment carry the Cogitator display language across the Strategium.

Pauldrons without supplied artwork are intentionally unmarked. The supplied PNGs are served in place from `assets/Painted Pauldrons/Completed/`; the Chapter-to-filename list is `PAULDRON_ART` in `jericho-strategium.html`. To add another, place a transparent PNG in that folder and add its Chapter name to the list (or an explicit filename override when spelling differs, as with Celestial Lions). The server serves only simple PNG basenames from this folder; it does not expose the PSD template or other files.

Optional ambience uses `assets/ambience/fortress-ambience.mp3`. No recording is currently included, so the play control identifies the missing file and stays disabled until it is supplied. Playback never starts automatically, and stops when the visitor leaves the archive or hides the tab. Use an original or licensed loop you have permission to distribute. Both asset folders are included when `assets/` is copied during deployment.

The shared header uses the Jericho Deathwatch emblem at `assets/jericho symbol.png`, served at the fixed `/assets/jericho-symbol.png` route. The startup terminal uses `assets/Inquisitorial_Rosette.png`, served at the fixed `/assets/inquisitorial-rosette.png` route. The Fortress org chart keeps its existing layout and connector network; the Librarius and Reclusiam command routes branch orthogonally from the Watch Master's node.

The endpoint should return JSON in this shape:

```json
{
  "members": [
    {
      "id": "m001",
      "name": "Example Name",
      "chapter": "Example Chapter",
      "rank": "watch_master",
      "company": null,
      "killTeam": null,
      "formation": null,
      "title": null,
      "vigil": "Example vigil note",
      "backstory": "Optional dossier text",
      "stats": { "strength": 8, "toughness": 7 },
      "serverJoinedAt": "2022-04-12T09:30:00Z",
      "aarCount": 12
    }
  ]
}
```

Important notes:

- `company` should be `1` through `5`, or `null`.
- `killTeam` should use the `"<company>-<team>"` format, such as `"3-2"`, or `null`.
- `formation` should be one of `armory`, `librarius`, `reclusiam`, `apothecarion`, `hall_of_blades`, `black_vault`, or `null`.
- `stats` is optional. It should be an object containing simple displayable values such as numbers, strings, or booleans; the member dossier renders the provided entries without requiring a fixed stat schema yet.
- `serverJoinedAt` is an optional ISO timestamp from Discord. The UI derives completed years of service from it.
- `serverDays` is an optional direct alternative to `serverJoinedAt` when the bot already calculates Discord tenure.
- `aarCount` is an optional number of recorded after-action reports.
- A Marine earns one service stud for every complete pair of thresholds: `400 AAR points` **and** `4 complete weeks`. Completed studs are calculated as `min(floor(aarPoints / 400), floor(serverDays / 28))`, capped at 16.
- Long Vigil years use continuous progress through those same paired thresholds: `min(aarPoints / 400, serverDays / 28) * 25`, capped at 400 years and displayed to one decimal place. Service studs remain discrete milestone badges, so partial progress changes years without awarding a stud.
- The dossier uses a configurable late-M42 anchor (`CURRENT_IMPERIAL_YEAR = 41999`) and estimates Watch entry as the anchor year minus completed Long Vigil years. The setting is configurable because 40k does not provide one universally fixed current calendar date.
- Examples:
  - `200 AAR points + 4 weeks` earns `0 service studs` and `12.5 Long Vigil years` because AAR progress is limiting.
  - `400 AAR points + 2 weeks` earns `0 service studs` and `12.5 Long Vigil years` because tenure is limiting.
  - `5000 AAR points + 4 weeks` earns `1 service stud` and `25 Long Vigil years`; additional AAR cannot outrun tenure.
- Do not call a Discord bot token or other secret directly from the browser. The bot publishes signed snapshots to `/internal/roster/snapshot`; this backend stores the snapshot and serves `/api/roster`.
- `backstory` is the only user-editable field. Discord roles and bot-managed data remain authoritative for all other fields.
- For production, put the backend behind HTTPS, set `STRATEGIUM_ALLOWED_ORIGIN` to the exact site origin, set `STRATEGIUM_SECURE_COOKIES=1`, and keep all secrets in the host secret manager.

## Production Hardening

Templates are in `deploy/`:

- `deploy/Caddyfile.example` proxies the public site over HTTPS and returns `404` for `/internal/*`. The bot should publish locally to `http://127.0.0.1:8787/internal/roster/snapshot`, so the signed ingestion route is not publicly reachable.
- `deploy/strategium.service.example` runs the backend as an unprivileged `strategium` user with `NoNewPrivileges`, private temporary storage, a read-only system filesystem, and write access only to the app's `data/` directory.

Example installation:

```bash
sudo useradd --system --home /opt/strategium --shell /usr/sbin/nologin strategium
sudo install -d -o strategium -g strategium /opt/strategium/data
sudo install -o root -g root -m 644 server.py geography.py jericho-strategium.html aar-evidence.html requirements.txt /opt/strategium/
sudo python3 -m venv /opt/strategium/.venv
sudo /opt/strategium/.venv/bin/python -m pip install -r /opt/strategium/requirements.txt
sudo cp -r assets /opt/strategium/  # ribbon images; awards are dropped (and logged) if missing
sudo install -o root -g root -m 644 deploy/strategium.service.example /etc/systemd/system/strategium.service
sudo install -o root -g root -m 644 deploy/Caddyfile.example /etc/caddy/Caddyfile
sudo systemctl daemon-reload
sudo systemctl enable --now strategium
sudo systemctl reload caddy
```

Set the production `.env` to the actual HTTPS origin before starting the service. Add rate limiting at the reverse proxy or upstream edge for `/api/auth/*`, `/api/me/backstory`, and public roster reads. Never expose `/internal/roster/snapshot` through the proxy.

## Repository

Remote repository: <https://github.com/wfj-dev/strategium>

## Roadmap Ideas

- Split the static prototype into separate HTML, CSS, and JavaScript files as it grows.
- Add a small backend or scheduled export for Discord role mapping.
- Add deployment through GitHub Pages or another static host.