# UI screenshots (Stage 5)

Taken in Microsoft Edge with Playwright, against the running backend (Claude Sonnet 5.5, real answers),
on 2026-11-22. Answers vary slightly between runs; the numbers come from the scoring code.

| File | What it shows |
|---|---|
| `01-empty-light.png`, `01-empty-dark.png` | Empty screen: the client's 4 example questions as one-click chips; theme toggle in the header. |
| `02-loading-light.png`, `02-loading-dark.png` | Loading state: bouncing "thinking" dots, stage message and elapsed seconds. |
| `03-table-light.png`, `03-table-dark.png` | Midwest winter answer: hub scores table with level colours and the worst hazard outlined in its hazard colour. The answer quotes score gaps returned by `rank_hubs` ("2.7 points behind Chicago"); no number warning. |
| `04-follow-up-dark-full.png` | Full conversation: the Midwest question and the Kansas City follow-up in the same session. |
| `05-dallas-dark.png` | False premise: "Why is Dallas high?" — the answer says it is moderate, shows the breakdown, and cites tornado / hail / ice-storm context that is not scored. |
| `06-denver-details-light.png` | Denver snowfall %, with "Figures cited", "Assumptions" and "Data sources" expanded. |
| `07-warning-MOCKED-light.png` | **MOCKED RESPONSE — not a real answer.** The `/chat` response was replaced in the browser with a hand-written one containing an invented number (9.5), only to show how the "Check these numbers" warning looks. The backend was not involved. |
