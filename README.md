# Business Scrying Portal, Conjured by: theL1zardWizard

Shows what every Torn company type needs for 1 to 10 stars (weekly or daily), and rates **your own company**
against that sample: where it sits now, what it takes to keep its star, and what it takes to reach 10.
**It advises; you run the company.**

Like it? Follow theL1zardWizard on Twitch: https://www.twitch.tv/thel1zardwizard_ttv

## Start it

Double-click **`run_portal.bat`**. A small server starts on your own PC and opens
`http://127.0.0.1:8780` in your browser. Close the black window to stop it.
`run_portal_demo.bat` shows everything with a fake company and no network access.
(`python3 business_scrying_portal.py [--demo]` or `./run_portal.sh` does the same on any OS. Python 3.9+, nothing to install.)

**First run:** open the **Data** tab, paste **your own Limited Access key** (Torn: Preferences > API) and press
**Save and verify**. The key is checked with Torn before it is saved. Public, Minimal and Full keys are refused on
purpose, so the app can never do more than read. The key must belong to the player who directs the company. After that,
**My company** fetches your company automatically on launch, and **Refresh now** (Data tab) re-collects the sample data.

## What you get

- **My company**: a verdict (secure, thin margin, at risk), where you sit against companies at your star, which star
  your income and customers actually look like, what you need per day to hold or climb, and a plain list of what to do.
- **Weekly / Daily** switch in the header, applied to the ladder, ranges, watchlist, explorer and all-types views.
- **Company type** selector across all 39 types, **Drivers** (which measurable metrics track stars), **All types** heat table.
- **Refresh on demand** (Data tab): re-collects every rated company of the selected type, or of all 39 types, from Torn's
  official company search at about one request a second (a full refresh is roughly 8 to 12 minutes; leave 5 minutes between
  full refreshes and 1 minute between single-type ones). It runs in the background and can be cancelled.
- **History log**: every refresh is archived under `userdata/history` (compressed snapshots, the earn line of every star per
  run, and which companies went up or down between snapshots). Your own company pulls are kept in `userdata/my_history.json`.
- **What your own logs show** (My company): real promotions and demotions seen between your snapshots, with the weekly income
  of companies that fell. This replaces guesswork once you have logged a few Sundays. Daily pulls also let the projection use
  the real days dropping out of your week instead of the average.

## Privacy and the rules

- The only site it contacts is **api.torn.com**: your own company (`profile` and `detailed`, at most once every 30 seconds)
  and the public company search/type endpoints during a refresh (paced under Torn's 100 requests a minute). Requests carry
  a comment naming this app. It never loads or scrapes a torn.com page.
- Your key is stored only in `config.json` next to the program and is **never sent to the browser**. The server listens on
  127.0.0.1 only, uses a per-run token and Host/Origin checks. Employee names and ids are dropped before the page sees anything.
- **Sharing this folder:** delete `config.json` and the `userdata` folder first. Every friend uses their own key.
- It reads data only. It cannot trade, send, hire, fire or change anything.

## Honest caveats

- The bundled ladder is a **snapshot** (Nov 6, 2026) collected through the official API. Press Refresh for current numbers.
  Your first refresh is a baseline: real promotions and demotions only appear after two snapshots that straddle a Sunday.
  Until then, demotion lines are inferred from where current holders sit.
- Torn bases stars on gross profit and re-rates companies every Sunday. It also caps the top star by lifetime gross
  revenue, which is not public and not modelled here. No one outside Torn knows the exact formula.
- Popularity, efficiency, environment, trains and ad budget are only visible for your own company. They are shown as
  context in the advice, not used to compute the star.
- Weekly "what it takes" projections assume the day that drops out of your week equals your weekly average, unless you have logged seven daily pulls that add up to your weekly figure. It is an estimate.

## Troubleshooting

- *Python was not found*: install Python 3.9+ from python.org and tick "Add python.exe to PATH".
- *Port busy*: it tries 8780 to 8789 automatically. The address is printed in the black window.
- *Torn error messages* (wrong key, access too low, key paused) are shown in plain words under the key box.
- *Refresh stopped part-way*: types finished before the stop are kept. Start it again later; Torn's own limits or a paused key are the usual cause.
