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
      "backstory": "Optional dossier text"
    }
  ]
}
```

Important notes:

- `company` should be `1` through `5`, or `null`.
- `killTeam` should use the `"<company>-<team>"` format, such as `"3-2"`, or `null`.
- `formation` should be one of `armory`, `librarius`, `reclusiam`, `apothecarion`, `hall_of_blades`, `black_vault`, or `null`.
- Do not call a Discord bot token or other secret directly from the browser. Serve roster JSON from a backend endpoint instead.

## Repository

Remote repository: <https://github.com/wfj-dev/strategium>

## Roadmap Ideas

- Split the static prototype into separate HTML, CSS, and JavaScript files as it grows.
- Add a small backend or scheduled export for Discord role mapping.
- Add deployment through GitHub Pages or another static host.