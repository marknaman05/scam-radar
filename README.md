# Scam SMS Radar

Paste — or share straight from WhatsApp — the "Dear customer, your
electricity will be disconnected tonight" SMS, or the forward claiming that
lemon water cures cancer. You get:

- **is it a scam** — as a calibrated probability, not just a label;
- **which family** — KYC, electricity, courier, lottery, fake job, bank/UPI,
  loan, impersonation, other, or genuine — with the probability of each;
- **danger** 0–4, how much harm following it would do;
- **creativity** 0–4, feeding a leaderboard of the most inventive cons;
- and for a forward rather than a scam: whether it is **misleading**, which
  kind of claim (health myth, communal hoax, political, money rumour,
  recycled media, doctored media, chain forward, false alarm), how much
  **harm** believing it would do, and how hard it pushes you to forward it.

An SMS is usually a scam and a WhatsApp forward is usually a false claim, so
a router question decides which half the verdict is about — and the gate
follows it, rather than judging a health myth for being a poor scam.

And a **confidence gate**: when the scam probability is near even, or two
families are too close to call, the verdict is queued for a human. Rulings
are kept next to the model's answer, so the stats show how often it was
right and the leaderboard only ranks confirmed scams.

The model is [Jev](https://typesafe.ai) (TypeSafe's System One), reached
through OpenRouter as `typesafe/jev-1.13` (about $0.00002 per message). It generates
no text — it answers typed questions (yes/no, choice, score) about the message
in one call, with probabilities. That is what makes the gate possible; a
chat model would just say "this is a scam" with equal confidence every time.

## Run

```
uv sync                       # Python 3.14
cp .env.example .env          # put OPENROUTER_API_KEY in it
uv run uvicorn radar.app:app --reload
```

Open http://localhost:8000. Tests: `uv run pytest` (the model is faked; no key needed).

## Shape

- `radar/classify.py` — the four questions, the one API call, the gate.
- `radar/store.py` — SQLite: reports, human rulings, votes, leaderboard, stats.
- `radar/app.py` — FastAPI routes; `radar/static/index.html` — the page.

## On a phone

It is a PWA with a **share target**: install it (button on the Check tab, or
Share → Add to Home Screen on iPhone), then long-press any message in
WhatsApp or Messages → Share → Scam Radar. Android hands the text to
`/share?text=…`, the page fills the box and checks it immediately. The app
never reads your messages; you hand it one at a time.
