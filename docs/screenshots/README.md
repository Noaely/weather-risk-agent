# UI screenshots (Stage 5)

Taken in Microsoft Edge with Playwright, against the running backend (Claude Sonnet 5.5, real answers),
on 2026-11-22. Answers vary slightly between runs; the numbers come from the scoring code.

| File | What it shows |
|---|---|
| `01-empty-light.png`, `01-empty-dark.png` | Empty screen: the client's 4 example questions as one-click chips; theme toggle in the header. |
| `02-loading-light.png`, `02-loading-dark.png` | Loading state: bouncing "thinking" dots, stage message and elapsed seconds. |
| `03-table-light.png`, `03-table-dark.png` | Midwest winter ranking: a short answer (conclusion + what the table does not show, e.g. score gaps returned by `rank_hubs`) and the hub scores table, shown by default for 2+ hubs, with level colours and the worst hazard outlined in its hazard colour. |
| `04-follow-up-dark-full.png` | Full conversation: the Midwest question and the Kansas City follow-up in the same session, each with its table and a hide/show toggle. |
| `05-dallas-dark.png` | False premise, single hub: "Why is Dallas high?" — full answer (it is moderate, the breakdown, and not-scored tornado / hail / ice-storm context); table hidden by default. |
| `06-denver-details-light.png` | Single-metric answer (Denver snowfall %): full answer, table hidden behind "Show scores table (1 hub)", with "Figures cited", "Assumptions" and "Data sources" expanded. |
| `08-single-hub-table-toggled-light.png` | The Denver answer after clicking "Show scores table". |
| `07-warning-MOCKED-light.png` | **MOCKED RESPONSE — not a real answer.** The `/chat` response was replaced in the browser with a hand-written one containing an invented number (9.5), only to show how the "Check these numbers" warning looks. The backend was not involved. |
