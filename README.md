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
- A Marine earns one service stud for every complete pair of thresholds: `400 AAR points` **and** `4 complete weeks`. Completed studs are calculated as `min(floor(aarPoints / 400), floor(serverWeeks / 4))`, capped at 16.
- Each completed service stud represents 25 Long Vigil years. Long Vigil service is therefore `serviceStuds * 25`, capped at 400 years.
- The dossier uses a configurable late-M42 anchor (`CURRENT_IMPERIAL_YEAR = 41999`) and estimates Watch entry as the anchor year minus completed Long Vigil years. The setting is configurable because 40k does not provide one universally fixed current calendar date.
- Examples:
  - `400 AAR points + 4 weeks` earns `1 service stud` and `25 Long Vigil years`.
  - `5000 AAR points + 4 weeks` still earns only `1 service stud` because time is the limiting factor.
  - `5000 AAR points + 64 weeks` earns `12 service studs` and `300 Long Vigil years`.
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