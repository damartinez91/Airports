# Site feature map

Static site built by `python -m flight_analyst build-site`. Serve it with `python -m http.server 8765 --directory site` and open `http://127.0.0.1:8765/`. Refresh this map when a page or control is added. The click-test skill is `.cursor/skills/verify-flight-site/SKILL.md`.

The live ask process is not a page. It stays off unless `LIVE_ASK=1`, and this map does not include it. Saved-answer lookup and the off-topic guard are planned in the README live-tier section. They are not built and they are not pages.

## Pages

| Page | URL | How a person gets there | What to confirm |
| --- | --- | --- | --- |
| Overview | `/` | Open the site | Draft banner, window, three cards: Explore, Scoreboard, Compare |
| Explore | `/explore/` | Overview card "Explore", or the Explore link in the nav | Question list, filters, one Daniel-approved "most on time out of" answer |
| Scoreboard | `/scoreboard/` | Overview card "Scoreboard", or the Scoreboard link in the nav | Overall accuracy, difficulty cards, miss table |
| Compare | `/compare/` | Overview card "Compare", the Compare link in the nav, or "Compare saved models on this question" on an explore card | Price-vs-accuracy chart, saved-run table, question menu, model switch, cost receipt |

## Explore controls

| Control | How to reach it | What it does |
| --- | --- | --- |
| Difficulty buttons All / Easy / Medium / Hard | Sticky bar at the top of Explore | Shows only that difficulty. All is pressed on arrival |
| Category menu | Same bar | Filters by category |
| Find box | Same bar | Matches the question id, the wording, and the category |
| Question card | Scroll, or find `q005` | Status badge, reading, candidate table, alternative cuts, flight floor, follow-up, reference SQL |
| Compare link on a card | Under the follow-up | Opens `/compare/#q005` with that question selected |

## Compare controls

| Control | How to reach it | What it does |
| --- | --- | --- |
| Chart | Top of Compare | One dot per saved run. Horizontal axis is cost per 100 questions. Vertical axis is accuracy on every scored question. Pending providers are not dots |
| Saved-run table | Under the chart | Run label, provider, both accuracies, cost per 100, price source. Repeat runs of one model stay on separate rows. A provider with no file says pending |
| Question menu | Under "One question, each saved answer" | Picks the question. A hash such as `#q007` selects that question on load |
| Model buttons | Under the menu | Switches the saved answer. Stub, claude-haiku-4-5 r1, and claude-haiku-4-5 r2 are saved. Press each one and confirm the answer and the receipt change |
| Grade line | Under the buttons | Matched, or the miss label |
| Cost receipt | Under the grade | This answer's list-price cost, tokens, latency, and the dearest saved model on that question. A single saved run says a top-model comparison is pending |
| Answer | Under the receipt | The saved prose for that model and question |

## Screenshot proof

After a change, save one screenshot of each page: overview, explore (with a filter or a found question visible), scoreboard, and compare (chart and receipt visible). Name them `screenshot_overview.png`, `screenshot_explore.png`, `screenshot_scoreboard.png`, and `screenshot_compare.png`.
