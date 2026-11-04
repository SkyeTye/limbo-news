# Video Fact-Check: Setup and Use

Lives at `/check` on the Limbo site. Private: nothing works until `FACTCHECK_PASSWORD` is set.

## 1. Railway variables (one time)

In Railway → the Limbo service → **Variables**, add:

| Variable | Value | Needed? |
|---|---|---|
| `FACTCHECK_PASSWORD` | a long password you choose | **Yes.** The section stays off without it |
| `SCRAPECREATORS_API_KEY` | same key as Content Files | **Yes** for Instagram. Without it, yt-dlp is tried (works for some TikTok/YouTube links) |
| `GROQ_API_KEY` | same key as Content Files | **Yes** for transcripts when the platform gives none |
| `DATA_DIR` | the path of the Railway volume (it may already be set) | **Yes**, or the source library is wiped on every deploy |
| `BRAVE_SEARCH_API_KEY` | Brave Search key | Recommended. DuckDuckGo is the free fallback and gets rate-limited |
| `GOOGLE_FACTCHECK_API_KEY` | Google Fact Check Tools API key | Optional. Finds other fact-checks faster (their verdicts stay hidden from the tracer) |
| `ANTHROPIC_API_KEY` | already set | Yes |

Spending limits (defaults shown): `FACTCHECK_MONTHLY_BUDGET_USD=15`, `FACTCHECK_DAILY_LIMIT=15`, `FACTCHECK_MONTHLY_LOOKUP_LIMIT=300`.

Model settings (defaults shown): `FACTCHECK_TRACE_MODEL=claude-sonnet-5-5`, `FACTCHECK_RETRY_MODEL=claude-opus-5-5`, `FACTCHECK_OPUS_RETRY=1` (set to 0 to turn the retry off).

Budgets per claim: `FACTCHECK_CENTRAL_HOPS=6`, `FACTCHECK_CENTRAL_READS=12`, `FACTCHECK_OTHER_HOPS=4`, `FACTCHECK_OTHER_READS=8`. The pilot evaluation's "more budget" run is 12 / 30.

Changing a variable redeploys the service.

## 2. iPhone

1. Open `https://web-production-110a6.up.railway.app/check` in Safari and sign in.
2. Share → **Add to Home Screen**. It opens full screen like an app.
3. Open **Settings** in the app and follow "Share from your iPhone" to build the Shortcut (about 2 minutes). The page has the address and the private Authorization value to copy.
4. Results from the Shortcut open in Safari, so stay signed in there too.

Then in TikTok or Instagram: **Share → Fact-check this**.

## 3. What happens after you share (v0.6)

1. **Seconds:** the post's creator, date and transcript appear, line by line.
2. **About 30 to 60 s:** **the claim**, the one central, checkable claim the video rests on, with its exact words and timestamp. Its line in the transcript is highlighted.
3. **A few minutes, live:** the timeline grows one step at a time, from the video back toward where the claim started. The step being built ripples and shows what the tracer is reading. Each step shows the source's name, its exact words, one line on how it compares to the step above, and **Read the full article**, which opens the original page scrolled to the quote and highlighted.
4. **Last:** where it started, then the verdict (label, confidence, one sentence) and anything still unknown.

On the home list, swipe a check left (phone) or right-click it (computer) to archive it (with Undo) or delete it. Deleting keeps its sources in the library. **Research log** at the bottom of each check shows every search and page read.

## 4. Where things are in the code

| Stage (research-flow.md) | File |
|---|---|
| 1 · Intake, security, API | `backend/factcheck/routes.py` |
| 2 · Capture | `backend/factcheck/capture.py` |
| 3 · The one central claim | `backend/factcheck/extract.py` |
| 5 · Provenance trace, 6 · Compare, receipt check, Opus retry | `backend/factcheck/tracer.py` |
| 7 · Context + second opinions (off since v0.6) | `backend/factcheck/context.py` |
| Orchestration, queue, archiving | `backend/factcheck/pipeline.py` |
| Source library, chains, rabbit-hole events, research log | `backend/factcheck/db.py` (SQLite at `DATA_DIR/factcheck/library.db`) |
| Reading pages, archive.org, fact-check links | `backend/factcheck/web.py` |
| Spending guard | `backend/factcheck/budget.py` |
| Settings and limits | `backend/factcheck/config.py` |
| Phone app | `frontend/check.html`, `frontend/js/check.js`, `frontend/css/check.css`, animations: `frontend/js/motion.min.js` (Motion 12.23.12, MIT) |

## 5. Tests

```
python3 tests/test_factcheck_offline.py    # 69 checks: links, frames, receipt check, library, tracer tools, triage, routes, security
python3 tests/test_factcheck_pipeline.py   # 18 checks: the whole pipeline with a scripted stand-in for Claude and a fake web
python3 tests/test_factcheck_pipeline.py --serve   # same, then serves the app on :5077 with the test data (password: test-password-123)
python3 tests/test_factcheck_pipeline.py --slow    # serves the app while a slowed-down check runs, to watch the live view
```

Neither test needs an API key or the internet. They do not test real search results, real pages or real Claude judgment. That is what the pilot evaluation's gold set is for.
