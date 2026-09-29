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

## Live Roster Data

The live app loads roster data from its same-origin `/api/roster` endpoint. The Discord bot publishes signed snapshots to the backend's internal `/internal/roster/snapshot` endpoint; browser clients never receive bot credentials.

## Record of Blood

The interface is framed as an Ordo Xenos analyst's Cogitator for investigating activity in the Jericho Reach. The main channels are **Personnel Docket**, **Sector Operations**, and **Chapter Origins**; the shared Return control restores the previous view and focus where possible.

The root route opens with a skippable, session-scoped Cogitator startup and simulated Inquisitorial credential check, then reveals the interactive Watch Fortress Jericho atlas. Its SVG region paths follow the source map and its legend: specialist and company locations open their existing dossiers, while flight, communications, and defense locations open Sector Operations. On narrow screens the atlas preserves readable detail through horizontal panning. The production atlas image is served from the fixed `/assets/watch-fortress-jericho-map.png` route; `assets/jericho map with legend.png` remains a development reference.

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
sudo install -o root -g root -m 644 server.py jericho-strategium.html /opt/strategium/
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