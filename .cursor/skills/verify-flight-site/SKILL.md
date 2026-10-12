---
name: verify-flight-site
description: Click-test the flight-delay static site after a change. Use when site pages, the explore bank, the scoreboard, or the model comparison change. Read the feature map, exercise every page, and return a screenshot of each page.
---

# Verify the flight-delay site

Use this after any change to `site/`, `src/flight_analyst/sitebuild.py`, or the eval results the site renders. Do not deploy, enable Pages, or change repository visibility.

## Before clicking

1. Read `docs/FEATURE-MAP.md`. If the change added, removed, or renamed a page or control, update that map first so the click-test matches the site.
2. Rebuild if the source changed: `python -m flight_analyst build-site` (or `PYTHONPATH=src python3 -m flight_analyst build-site` when the package is not installed).
3. Serve the folder: `python3 -m http.server 8765 --directory site`.
4. Open `http://127.0.0.1:8765/`.

## Click through

Follow the map. Every row is in scope.

- Overview: confirm the draft banner and open each card from the page, not only by typing the URL.
- Explore: q005 is easy, so press Easy (Medium hides it), type `q005` in Find, and confirm the LAX card shows the literal winner and the major carriers, with SkyWest labeled "SkyWest (flies for several airlines)" when that carrier is in the rows. The table has an on-time rate and a flight count on every row. Open Reference SQL only if you need to check the query. Use the card link "Compare saved models on this question".
- Scoreboard: read the overall figure and open one miss row far enough to see both SQL blocks.
- Compare: confirm the chart has one dot per saved run and no dot for a provider marked pending. Change the question menu to `q007`. Read the grade and the cost receipt. If more than one model button exists, press each one and confirm the answer and the receipt change.

A pending model is an empty slot. Do not treat it as a score. The stub dot is a rules baseline, not a live-model result.

## Proof

Save a screenshot of each page after the interaction above:

- `screenshot_overview.png`
- `screenshot_explore.png`
- `screenshot_scoreboard.png`
- `screenshot_compare.png`

The compare shot has to show the chart and the receipt. The explore shot has to show the filter or the found question, not only the top of the page.

## Stop

If a control in the map is missing, or a page shows a stack trace, fix the site and repeat the failed page. Do not publish.
