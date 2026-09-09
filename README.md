# Watch Fortress Jericho Strategium

A single-page org chart web app for the Watch Fortress Jericho roster. The current build is a static HTML prototype with embedded CSS and JavaScript, sample roster data, searchable member dossiers, company views, specialist formations, and an optional live roster endpoint.

## Getting Started

Open `jericho-strategium.html` directly in a browser.

No build step or package install is required yet.

## Live Roster Data

The app can use sample data or load live data from a roster endpoint configured from the in-app settings button.

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
- A Marine earns one plasteel stud for every complete pair of thresholds: `400 AAR` **and** `14 server days`. Progress toward the next stud is continuous and uses `min(aarCount / 400, serverDays / 14)`, so partial service is nonzero but cannot exceed the progress supported by either input.
- Each completed plasteel stud represents 25 Long Vigil years. Every four completed plasteel studs also earns one auramite stud. Long Vigil service is capped at 400 years.
- The dossier uses a configurable late-M42 anchor (`CURRENT_IMPERIAL_YEAR = 41999`) and estimates Watch entry as the anchor year minus extrapolated Long Vigil years. The setting is intentionally configurable because 40k does not provide one universally fixed current calendar date.
- Examples:
  - `1000 AAR + 10 days` earns `0 plasteel studs` and `17.9 Long Vigil years`; the Marine is partway to the first stud.
  - `3000 AAR + 365 days` earns `7 plasteel studs`, `1 auramite stud`, and `187.5 Long Vigil years`; the AAR threshold is the limiting factor.
  - `6400 AAR + 224 days` earns `16 plasteel studs`, `4 auramite studs`, and `400 Long Vigil years` after the global cap.
- Do not call a Discord bot token or other secret directly from the browser. Serve roster JSON from a backend endpoint instead.

## Repository

Remote repository: <https://github.com/wfj-dev/strategium>

## Roadmap Ideas

- Split the static prototype into separate HTML, CSS, and JavaScript files as it grows.
- Add a small backend or scheduled export for Discord role mapping.
- Add deployment through GitHub Pages or another static host.