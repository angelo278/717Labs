# 717Labs

The 717labs.co.uk website and the robot that sends the free AI visibility report.

## How it works

1. Someone fills in the free report form on the website (Netlify Forms).
2. `netlify/functions/submission-created.mjs` runs automatically. It applies two limits
   (one report per email address every 30 days, and a daily cap) and asks GitHub to start the robot.
3. `.github/workflows/report.yml` runs `report/pipeline.py`, which:
   - reads the customer's website and works out the business, town and trade;
   - writes 4 customer-style questions plus 1 question about the business by name;
   - asks ChatGPT, Perplexity, Gemini, Claude and Google AI Overviews (25 answers);
   - scores every answer, finds competitors and writes 3 opportunities;
   - builds the PDF (`report/build_report.py`) and emails it with Resend,
     with a silent copy to info@717labs.co.uk.
4. If anything fails twice, the customer is told the report will follow within 24 hours
   and info@717labs.co.uk gets an alert. To re-run by hand: GitHub → Actions →
   "Free AI report" → "Run workflow".

## Secrets

GitHub → Settings → Secrets and variables → Actions → New repository secret:
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `PERPLEXITY_API_KEY`,
`SERPAPI_API_KEY`, `RESEND_API_KEY`.

Optional variables (same page, Variables tab) if a model name ever needs changing:
`OPENAI_MODEL`, `JUDGE_MODEL`, `GEMINI_MODEL`, `ANTHROPIC_MODEL`.

Netlify → Project configuration → Environment variables:
`GH_REPO` (`angelo278/717labs`), `GH_DISPATCH_TOKEN` (a fine-grained GitHub token for this
repository only, with Contents: Read and write), optional `REPORT_DAILY_CAP` (default 40).

## Testing without keys

```
pip install -r report/requirements.txt && python -m playwright install chromium
MOCK=1 python report/pipeline.py some-request.json
```

## Consultation bookings

`netlify/lib/booking.mjs` handles the "consultation" form: it reserves the slot (so it shows as Booked on the site via `/api/slots`), emails the visitor a confirmation with a calendar invite, and sends a copy to info@. Netlify environment variables: `RESEND_API_KEY` (required), `MEETING_LINK` (optional: your Google Meet or Zoom personal link), `BOOKING_FROM` (optional). Each booking email to info@ includes a private link to free the slot if the call is cancelled (`/api/cancel`).
