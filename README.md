# Scam SMS Radar

Paste the "Dear customer, your electricity will be disconnected tonight" or
"KYC pending, click here" message. You get:

- **is it a scam** — as a calibrated probability, not just a label;
- **which family** — KYC, electricity, courier, lottery, fake job, bank/UPI,
  loan, impersonation, other, or genuine — with the probability of each;
- **danger** 0–4, how much harm following it would do;
- **creativity** 0–4, feeding a leaderboard of the most inventive cons.

And a **confidence gate**: when the scam probability is near even, or two
families are too close to call, the verdict is queued for a human. Rulings
are kept next to the model's answer, so the stats show how often it was
right and the leaderboard only ranks confirmed scams.

The model is [Jev](https://typesafe.ai) (TypeSafe's System One). It generates
no text — it answers typed questions (yes/no, choice, score) about the message
in one call, with probabilities. That is what makes the gate possible; a
chat model would just say "this is a scam" with equal confidence every time.

## Run

```
uv sync                       # Python 3.14
cp .env.example .env          # put TYPESAFE_API_KEY in it
uv run uvicorn radar.app:app --reload
```

Open http://localhost:8000. Tests: `uv run pytest` (the model is faked; no key needed).

## Shape

- `radar/classify.py` — the four questions, the one API call, the gate.
- `radar/store.py` — SQLite: reports, human rulings, votes, leaderboard, stats.
- `radar/app.py` — FastAPI routes; `radar/static/index.html` — the page.

Android tip: install the page as a web app and share a message to it — the
`?text=` query parameter prefills the box.
