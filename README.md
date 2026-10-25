# Watch Fortress Jericho Strategium

A single-page org chart web app for the Watch Fortress Jericho roster. The current build includes the embedded frontend and a small backend for signed bot snapshots, searchable member dossiers, company views, specialist formations, Discord OAuth, and user-owned backstory editing.

## Galactic War

The Galactic Map now uses the bot's replacement Galactic War service: shared company
commitments, KT system assignments, approval/cover controls and separate four-strike
Vanilla and Omega boards per playable planet (eight total). The format switch preserves
each pool independently; both currently use vanilla maps. Map signup and automatic roster locking use authenticated `/api/war`
requests. The existing signed AAR bridge also carries these requests, so no campaign
database or bot credentials are exposed to the browser.

Select a subsector to open its campaign panel alongside the desktop map, or use
the collapsible subsector navigator on phones. Authenticated campaign permissions
load before the first map render. Role actions and systems are visible immediately;
selecting a star or world keeps the same panel and selected-sector context rather
than opening a separate modal. System worlds use the existing map imagery, and a
selected world shows its description, Vanilla/Omega pools and strike roster. Company
command forms and moderation controls expand only when needed. Counts describe the selected
planet's current pool, while cards distinguish the game mission from its briefing
theme. Changing selections or refreshing campaign data keeps the map canvas mounted.
New Vanilla offers show their assigned Hard-Stratagem recipe: one positive, three
negatives, and additional mandatory Intelligence Lapse on Chaos-only maps. Adaptation
notes distinguish the actual vanilla map/task from the Atlas setting, including ice
and asteroid proxies and Vortex's unchanged Mutalith boss. Recipe reports require
evidence of the selected stratagems and operation result; independent staff compare
the saved recipe with the receipt before credit and AAR rewards are applied. Existing
offers without recipes and the Omega pool retain their original contracts.
Campaign polling continues while controls are focused; form values, text selection
and keyboard focus are preserved when the console updates.

Phones and small touch screens use the actual interactive chart, with one-finger
dragging, two-finger pinch zoom, and tap selection. A native sector picker and
collapsible directory provide alternatives to tapping small regions. Map/Details
controls jump between the chart and its inline sector/system/strike panel. Zoom
controls have 44px touch targets; the chart remains full-width with bounded height
in portrait and landscape. Touching the chart stops camera interpolation, cancelled
gestures do not select, and rotating the device preserves the mounted canvas and
selected mission pool. Desktop keeps its side-panel layout.

Strike participation requires the actual Watch Brother role, even for higher ranks
and specialists. The bot verifies the role ID at signup and rechecks the full roster
before locking; the interface hides signup controls when that permission is absent.

There are no Ready, Deploy or coordinator controls. Once all slots are filled, the
roster locks automatically; a recorded cancellation releases an uncompleted roster.
Normal AARs accept an optional locked strike ID and report completions only. Report Completion from
the map prefills the actual vanilla operation, difficulty and signed-up players. Renown
displays four rolling tiers at 4/8/28/56 credited completions per formation member;
Captain/LT scores average their company's KTs equally. Personal lifetime laurels are
separate. Legacy Ordo Xenos standing is no longer displayed.

Active Watch Masters and Forge Masters have a collapsed Invasion testing section.
Their owner-private sandbox uses separate scratch state: start a test invasion,
commit a company, assign a KT to a system, and simulate completions or advance test
time. Test view hides real signup/AAR actions and is labelled TEST / NO REAL REWARDS.
Return to live campaign leaves the sandbox resumable; Revert Test Invasion discards
test progress without changing live campaigns, orders or awards. Test notices are
opt-in, confirmed and unpinged; a sent test notice gets a matching reverted notice.
Permissions and session ownership are rechecked by the bot, not trusted from the UI.

Authored system labels use bare proper names, such as Solenne Crown, without a
trailing System suffix. Stable system/body IDs and campaign assignments are unchanged.

This is an implementation branch, not a live migration. The bot's `GALACTIC_WAR.md`
documents configuration, specialist requirement probabilities and remaining rollout gates.
Atlas objectives are thematic briefings for supported vanilla three-player operations
and existing five-player Omega, not custom in-game interactions. Each generated offer
independently has a 25% specialist requirement chance; only specialist tracks can gate
it. Equerry combinations qualify for intermediate titles. The war reference includes
artwork-derived neighbors and provisional gameplay terrain, visibly marked for review.
It does not require certified custom maps. Staff have independent evidence approval,
rejection and audited strike-credit reversal controls; on-time reports have a bounded
24-hour processing grace at campaign closure. Existing AAR records and cosmetic roles
are retained, and no live migration is performed by this branch.

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

Any user signed into Discord who is a current, non-bot member of the configured guild may submit an AAR; no Discord role is required. The tab, direct routes, submissions, QR creation, and handoff retrieval enforce login and guild membership server-side. The website displays the member's guild display name rather than their account username. If the private bot bridge cannot verify membership, AAR access fails closed.

The participant picker searches the configured guild's current non-bot member directory, including members omitted from the active roster snapshot, through the private bot bridge.

The screenshot picker accepts file selection, clipboard image paste, and drag-and-drop. PC/Steam players can paste a copied endgame capture or drop a saved image. Console players can save captures to their phone using the Xbox/PlayStation app, then use **Add From Phone** on the AAR draft: the QR code opens an upload-only page and incoming screenshots appear in the desktop previews. Users must review and submit from the authenticated draft; the phone link cannot submit an AAR or read roster/evidence data.

Pasting imports all images the browser exposes, including batches of copied screenshots, in both new reports and evidence replacement. Clipboard support varies by operating system, browser, and source application; some expose only one image even when several files were copied. In that case, select multiple files in the screenshot picker or drag them into the upload area. The explicit clipboard button imports one supported format per clipboard image, without duplicating alternative encodings.

Phone links expire after ten minutes, can be closed by the draft owner, and are revoked after successful submission. Evidence is staged in memory only (maximum 16 active handoffs / 64 MiB globally, 10 images / 32 MiB per handoff) and disappears on restart. Incoming phone evidence is still subject to the draft's combined screenshot limits. Use the publicly reachable HTTPS site origin for `STRATEGIUM_ALLOWED_ORIGIN`; a localhost QR URL is not reachable from another device. Keep the link private until closed. The phone page strips the token fragment from its displayed URL and sends no referrer.

Phone uploads verify image structure and MIME/format agreement, reject animated images and images above 40 megapixels, and recheck the owner's pilot permission. QR creation shares the global upload slots and has a 30-second per-owner cooldown. Body reads time out after 20 seconds so a stalled upload cannot hold a slot indefinitely. Production still requires HTTPS, the private bot bridge, and upstream request-rate controls.

The **Submit AARs** channel at `/submit-aar` provides authenticated guild members with a mode-aware AAR form. It requires 1–10 PNG, JPEG, or WebP screenshots. The per-image limit follows Discord's current guild upload limit (capped at 32 MiB by the application), and the combined limit is 32 MiB. The site accepts at most two concurrent uploads; the bot independently caps two in-flight requests and 48 MiB total buffered. The bot validates the structured fields, writes the canonical record and processes points/challenges/awards through its datastore, then posts a separate lore-styled Discord embed with the screenshot previews. The receipt is not parsed as an AAR; its real Discord URL is stored with the record for audit and challenge references.

At `/manage-aars`, current High Command and Watch Techmarines can search and edit Strategium-submitted AARs, including replacing evidence; unchanged screenshots are retained. If the Discord receipt updates but archive processing fails, the pending edit appears in the manager and can be resumed after a refresh. Every operation rechecks current guild roles in the private bot bridge. Editors can request deletion with a reason, and a Forgemaster must confirm the permanent removal of both the canonical record and Discord receipt. The bot rebuilds affected challenge progress and computed stats without replaying announcements, revokes only challenge roles with recorded bot-award provenance, and flags ambiguous/manual grants for Forgemaster review. Forgemasters can acknowledge completed award reviews to clear them from the queue. A minimal deletion audit entry and mutation recovery state are retained.

The Archive view presents reports as match-history entries with mission, mode,
difficulty, rank, date, participants, waves/KIA or PvP result, and challenge-tag
badges. Search uses the bridge's `q` query and matches normalized words across AAR
ID, receipt URL, mission/difficulty/timestamp, submitter and participant IDs/names,
challenge-tag keys and labels, and canonical structured report fields including mode,
rank, waves, KIA, PvP result, and report tags. Search normalizes underscores and
hyphens as spaces. Staff request queues use ticket-style entries with request type,
status, date, and the exact AAR review link; edit and deletion tickets are shown
together in oldest-request-first order. Unfinished archive and award work remains a
separate maintenance queue. Existing role checks, review identity, pagination, and
audited recovery paths are unchanged.

The Record of Blood includes Black Vipers in the sample Chapter catalogue and
pauldron index. Its concise lore summary identifies the Primaris-only Salamanders
successor, marks its homeworld and later history as unknown, and cites
*Codex: Space Marines* (9th ed.), pp. 63, 71. The live reference remains
bridge-provided; a site fallback summary is shown if the connected reference omits it.
The supplied artwork at `assets/Painted Pauldrons/Completed/Black Vipers.png` is
converted by `.venv/bin/python -m scripts.build_web_assets --pauldron "Black Vipers"`
to the site's WebP pauldron thumbnail. The builder also accepts artwork in the
Painted Pauldrons root for future additions.

Run `./setup-secrets.sh` to provision the shared `STRATEGIUM_BOT_AAR_SHARED_SECRET` in both local `.env` files. The site forwards uploads to `STRATEGIUM_BOT_AAR_INTAKE_URL` (default `http://127.0.0.1:8080/v1/aar/submissions`) with a timestamped HMAC; the browser never receives the secret. The sibling `/v1/aar/access` route verifies current guild membership and returns the guild display name. Remote HTTPS targets must be explicitly listed in `STRATEGIUM_BOT_AAR_ALLOWED_HOSTS` as comma-separated hostnames. Enable the bot route explicitly with `web_submission.enabled: true` in the bot configuration. Keep the bot bridge private and do not expose `/v1/aar/*` through a public reverse proxy. The `/submit_aar` slash command, including staff test mode, is retired; use the website's `/submit-aar` intake.

Forgemasters can delete from the record detail with a reason and final confirmation;
the interface records the request and confirms it through the same audited endpoints.
Interrupted deletions remain resumable in the review workspace, with the original
Discord receipt URL retained in the audit. Other editors have request-only deletion controls.

### Member AAR Edits

The Submit AAR page includes **Edit My AAR**. Enter a website AAR ID or its Discord
receipt link to open the existing structured report. Only its original submitter or
a recorded participant can propose changes. Submitted fields are staged for approval;
the canonical AAR, receipt and awards remain unchanged until staff approval. Existing
screenshots and evidence hashes are retained; replacement evidence remains a staff edit.

Techmarines and High Command review current/proposed fields under
**Manage AARs > Edits & deletions**, then approve or decline with a note. A compact request
list opens the complete report, participants and screenshot evidence on the website,
alongside the exact proposed edit or deletion reason and its decision controls.
Each preview has a same-site permalink, such as `/manage-aars?aar=123`, with exact
review identity in its query parameters. Browser reload and Back/Forward preserve
the selected review. Screenshot buttons open a full-size viewer without leaving
the website. The mobile workspace switches from the list to the selected preview.
Approval uses
the existing audited receipt/archive update and recovery flow. A proposal based on
an outdated record is rejected rather than silently overwriting newer changes.
Members cannot access the staff queue or directly apply their proposed changes.

Member reviews have Edits-and-deletions/Edit/Deletion filters applied before pagination,
with up to ten items per page and a byte-bounded response. Archive search/pagination
remains independent under the Archive view. Closed reviews have no active decision
controls; partially applied edits and deletions show their recovery state.

The normal review queue contains only member edits and deletion requests. Automatic
follow-up work from interrupted archive saves or award reconciliation appears
separately under **Unfinished updates**, only when such work exists. It is not a
member request. The signed-in participant in Submit AAR uses the same guild display
name as the login badge, even when the published roster has older rank information.

Submitters and participants may also request deletion of their website AAR with a
reason. Only a Forgemaster can confirm deletion. Neither editing nor deletion is
offered for member-posted Discord AARs. Existing strike-link, evidence and
credit-reversal protections remain.

Members may have at most ten pending deletion requests; the overall deletion queue
is capped at 1,000. Editors can decline a requested deletion with an audited reason,
retaining the AAR and unlocking editing. Decline is unavailable once deletion has
started. Decisions are bound to the specific request so stale screens cannot act
on a later request for the same AAR. A later request retains the earlier decline audit.

## Rank Guide

Visit `/rank-guide` for the 28 numbered Watch Fortress rank cards and their promotion requirements. Checked-in full-size WebPs in `assets/web/rank-cards/` are sufficient to regenerate gallery previews with `.venv/bin/python scripts/build_web_assets.py`. When source PNGs are available in the sibling bot repository at `../discord-bots/op-scribe-servitor/assets/ranks/`, the builder uses them to regenerate both sizes; set `STRATEGIUM_RANK_CARDS_SOURCE` to override that path.

The Promotion System sections are available in both the track gallery and individual track views. They cover the 2/3/4/5/6-point mission tiers (Crucible awards 6), Omega's bot-authoritative 20 operation points minus parsed KIA (KIA clamped to 0-4, yielding 16-20 points independently of squad size; two to five brothers and an explicit KIA line required), successful-report/team/approved-armor eligibility with Discord links, oaths beyond Watch Veteran, Watch Command proclamations and mandatory Techmarine compliance checks. Service studs require both time and points: Plasteel is 4 weeks plus 400 AAR points; Auramite is 16 weeks plus 1,600 AAR points.

## Record of Blood

The interface is framed as an Ordo Xenos analyst's Cogitator for investigating activity in the Jericho Reach. The main channels are **Personnel Docket**, **Sector Operations**, and **Chapter Origins**; the shared Return control restores the previous view and focus where possible.

Sector Operations reads the versioned hierarchy in `data/reach_geography.json` from `/api/geography` and renders a continuous HTML5 Canvas chart. Wheel zoom anchors beneath the cursor, drag pans, and selecting a sector focuses its boundary center with a consistent viewport fit. Sector hover details appear immediately. Selecting a resolved star or orbiting body opens its system dossier immediately. Dossiers and hover text show useful body/stellar classifications and routes, without internal source or provisional-name labels. The viewport matches the artwork's aspect ratio and the camera uses cover-fit and bounded panning, so there are no black margins behind the sector image. Close zoom retires the raster artwork in favour of the static starfield and orbital system rendering.

The chart contains **29 sectors**. The central circle, sector 1, is **Jericho Reach**. Every sector starts Secure; this is a strategic status, separate from whether locations are charted. Sectors 02-28 contain provisional generated systems and worlds with no assigned factions; Sector 29 has an authored roster described below. Secure highlight layers appear only for sector hover/selection; zooming outward clears sector selection rather than latching its highlight after returning to the overview. Critical and Lost layers persist on the strategic view when explicitly assigned. `critical` uses the supplied `CONTESTED` exports. Sector statuses are independent of directive statuses, and their future campaign authority is still undecided.

The three numbered source sets are in `assets/JERICHO MAP - SECURE/`, `assets/JERICHO MAP - CONTESTED/`, and `assets/JERICHO MAP - LOST/`. Each export contains the complete backdrop, so `scripts/build_web_assets.py` derives their common unhighlighted base, lossless transparent overlays, and numbered geometry under `assets/web/galactic-map/`. The sectors follow the artwork's enclosed green boundaries; painted-line junctions are not assigned to fictional extra sectors. Original exports remain untouched.

The Fortress Atlas uses sequenced pointer lookups, a small hit-mask neighborhood fallback, and DOM-aware idempotency so Black Vault hover cannot leave stale state with a cleared leader. At 760 px or narrower it becomes a tap-first fortress directory beneath a sticky atlas: selecting a section lights its layer and reveals its lore and an access control.

On screens at 600 px or narrower, the map is replaced by a tap-first Sector Navigator. It lists all 29 sector states and their charted systems. Selecting a location opens the same body/route dossier as the desktop chart.

Jericho Reach retains five systems: **Erioch, Watch Fortress Jericho, the Warp Gate, Castobel, and Skapula**. Each has five non-stellar worlds (25 total), preserving selected original locations and adding only enough provisional worlds to reach five. Other previously modeled central systems are omitted from active geography as non-operational or non-strategic targets. Every outer sector has three to five systems, each with three to five non-stellar bodies. The current chart contains **118 systems and 594 bodies**, including one modeled star per system. Generated outer-sector locations remain provisional display content, not canon or faction claims.

Sector 29 contains **Solenne Crown (5 worlds), Viridian Tides (4), Adamant Chain (4), Penumbral Choir (3), and Duskward Verge (3)**. Its 19 authored bodies carry explicit world types and biomes, including Calyx as a Research Station. System IDs, map positions, routes, and draft stars are retained. Each body's dossier has an expandable Objective Types list, grouped into general and specialist missions. Availability comes only from the Deathwatch War Atlas's authored mission-version settings for that exact world type and biome, not its Natural/Plausible/Stretch rating. Research worlds and stations share the atlas's research category. Missing metadata is distinguished from a setting with no authored mission versions. These are reference lists, not mission launches, rank gates, campaign assignments, or bot configuration changes.

The compact, versioned catalogue in `data/reach_objectives.json` contains 10 general and 19 specialist objective types. It is included in the validated `/api/geography` document. Normal builds need no access to the atlas export; to refresh the catalogue from an updated export, run `.venv/bin/python scripts/build_reach_geography.py --import-objectives "/path/to/index.html" --write`, then rebuild geography. The importer parses only the embedded `cards-data` JSON and unions each objective's authored settings; it does not execute the HTML or import generated rank gates, fictional mission details, or assets.

The bot's shared-content integration reads this same geography catalogue for campaign eligibility, retaining its own approved specialist-rank policy. Authored world types and biomes override older provisional bot terrain. The geography also publishes `worldClasses`: Alpha/agri, Gamma/civilised, Delta/dead, Delta Tau/death, Epsilon/cemetery, Eta/hive, Mu/feudal, Rho/research, Phi/forge, and Phi Lambda/feral, with Greek symbols shown in dossiers and hover labels. Imperial world classes are separate from operation names and Vanilla/Omega formats. Deploy matching geography and bot versions together. The bot's 75% ungated / 25% specialist roll can use compatible specialists at any rank. The updated atlas supports both categories for every world in **The Canticle Reach (Sector 29)**, including Isolen. Across all 80 settings it currently has 3-4 general types and at most two specialist types per setting; these counts are not hard-coded runtime limits. The `feral|starship` and `hive|jungle` settings have no specialist objectives yet and remain explicit content gaps, neither of which occurs in The Canticle Reach.

Planet portraits from `assets/galactic map planets/` are matched by world type and biome, not by planet name or sector. Source names are used only to look up each image's authoritative pairing during asset preparation. The 19 supplied portraits cover 16 pairings; repeated pairings retain multiple variants selected consistently by a world's stable ID. Research Station artwork remains distinct from Research World artwork. Transparent, aspect-preserving WebPs under `assets/web/planets/` replace procedural orbiting bodies and generic planet symbols in dossiers, system/world lists, hover details, and the selected-world overview. Stars, fortresses, unsupported pairings, and not-yet-loaded Canvas images retain existing visuals. Source PNGs are untouched.

After adding or repainting portraits, run `.venv/bin/python scripts/build_web_assets.py --planet-art-only`, then `.venv/bin/python scripts/build_reach_geography.py --write`. The generated manifest is included as validated `planetArt` in `/api/geography`; only approved pairing/variant WebP filenames are served. The full web-asset build also refreshes planet portraits.

The shared-system groupings and stars are provisional display modeling, not established canon. Stars have individual draft names such as **Vigil Lux**, **Vespera**, **Solis Votum** and **Ferrum Lux**, instead of `<Planet> Primary`. Each has an explicit **O/B/A/F/G/K/M spectral class**, with matching blue, blue-white, white, yellow-white, yellow, light-orange or orange-red coloring and the supplied icon sheet's corresponding symbol in tooltips/dossiers. Rare green/purple stars and exotic stellar anomalies are not assigned automatically. Star and system naming/classification remain subject to the lore team.

The source artwork inventory is explicit in `scripts/build_reach_geography.py`; the Castolel graph identifier remains stable while its display name is Castobel. Exul, the added Recidious/Kadaku/Avarax/Demerium locations, duplicate Hestus/Ravacene graph anchors and procedural periphery/outpost/expanse systems remain excluded. The five retained central system centers follow their source positions with deterministic orbital-clearance adjustments. Generated systems use stable IDs and deterministic placements inside their numbered sector masks. Orbit envelopes remain within their owning sector and are separated from neighbouring systems. Orbital motion is deliberately slow, with outer bodies moving more slowly; reduced motion freezes it. Body hit testing and hover callouts follow the same animated coordinates. Generated names and classifications remain subject to lore review.

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

The builder retains five approved central systems, generates provisional systems/worlds in the other sectors, validates references and orbital bounds, and writes atomically. Warp links inside a shared system are omitted from the inter-system graph; parallel authored links between retained groups collapse to their lowest original transit cost, while provisional outer-sector routes connect generated locations. It does not edit bot campaign records or reintroduce excluded locations from active directives. The server serves only explicitly allowlisted generated map layers and classification/spectral icons. Chart coordinates, orbital scales and approach paths are schematic layout hints, not lore distances or canon invasion coordinates.

The root route opens with a skippable, session-scoped Cogitator startup and simulated Inquisitorial credential check, then reveals the interactive Watch Fortress Jericho atlas. Its base and 15 section layers come from `assets/Jericho 2.0 Map/`. Pointer hit testing uses the green-outline pixel mask in `assets/atlas-hover-mask.png`, generated from the aligned section exports by running `python3 scripts/build_atlas_hover_mask.py`; keyboard navigation uses the fortress directory. Hovered sections swap to their corresponding full-canvas highlights and show a terminal-style lore callout. Specialist and company locations open their existing dossiers, while the flight deck and strike cruiser open Sector Operations. On narrow screens the atlas preserves readable detail through horizontal panning. The base atlas is served at `/assets/watch-fortress-jericho-map.png`, the hit mask at `/assets/atlas-hover-mask.png`, and section states through the fixed allowlist at `/assets/fortress-layers/<layer>.png`.

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