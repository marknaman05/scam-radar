"""The one call to Jev, and what we make of its answer.

Jev (TypeSafe's System One) writes nothing.  It answers typed questions
about a piece of state -- here, a pasted SMS -- with calibrated
probabilities.  Three questions carry the whole product:

  is_scam      Noul   yes/no, as a probability
  kind         Choice which scam family, with a probability per label
  danger       Score  0..4, how much harm following the message would do

plus one for the leaderboard:

  creativity   Score  0..4, how inventive the con is

The confidence gate is the point of using a model that reports its
uncertainty: a verdict is *certain* only when the scam probability is far
from 0.5 and the chosen family clearly beats the runner-up.  Everything else
is queued for a human, whose corrections become the ground truth the
leaderboard and the stats are built on.

Thresholds are ordinary numbers, not tuned constants: change them and the
gate moves.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from typesafe_sdk import Choice, Noul, Score, TypeSafeClient

KINDS: dict[str, str] = {
    "kyc": "KYC / account-suspension bait: 'update your KYC', 'PAN not linked', 'account will be blocked', with a link or number to call.",
    "electricity": "Utility disconnection threat: 'your electricity/gas/water will be cut tonight', 'bill unpaid, contact officer', usually with a personal mobile number.",
    "courier": "Parcel / customs hold: 'your package could not be delivered', 'customs duty pending', 'FedEx/India Post', pay a small fee via a link.",
    "lottery": "Prize, lottery, lucky draw, cashback or gift you never entered; claim by paying a fee or sharing details.",
    "job": "Fake job or task offer: part-time work, 'like YouTube videos and earn', 'earn ₹5000 a day', WhatsApp/Telegram contact, registration fee.",
    "bank": "Bank / card / UPI fraud: 'transaction blocked', 'refund pending', OTP requests, fake bank links, 'your card is debited'.",
    "loan": "Instant-loan or investment lure: pre-approved loan, guaranteed returns, crypto/stock tips, app download links.",
    "impersonation": "Someone pretending to be police, a court, customs, a boss, a relative in trouble, or a government scheme (PM Kisan, Aadhaar).",
    "other_scam": "A scam that fits none of the families above.",
    "not_scam": "A legitimate message: a real OTP, a genuine delivery update, a bank alert without a link, personal chat, marketing that asks for nothing.",
}

DANGER = [
    "harmless: nothing is asked of the reader",
    "nuisance: spam or marketing; ignoring it costs nothing",
    "risky: leads to a link, app or number that could phish details",
    "dangerous: asks for OTP, PIN, card or a payment; money loss likely if followed",
    "severe: impersonates authority with urgency and threat (arrest, disconnection, legal action) to extract money or access",
]

CREATIVITY = [
    "template: the standard wording everyone has seen",
    "minor twist on a known template",
    "some craft: a plausible detail, a new hook, decent grammar",
    "inventive: a fresh premise or a clever pressure tactic",
    "genuinely creative: a story or angle that would surprise a seasoned skeptic",
]

QUESTIONS = {
    "is_scam": Noul(
        instructions="Is this SMS a scam, fraud attempt or phishing message?",
        criteria={
            "true": "It tries to deceive the recipient into paying, sharing credentials/OTP, clicking a malicious link, or contacting a fraudster.",
            "false": "It is genuine, harmless, or ordinary marketing that asks for nothing sensitive.",
        },
    ),
    "kind": Choice(instructions="Which family does this message belong to?", criteria=KINDS),
    "danger": Score(instructions="How much harm would following this message do to a typical recipient?", criteria=DANGER),
    "creativity": Score(instructions="As a scam, how creative or novel is this message?", criteria=CREATIVITY),
}

#: A verdict is confident when the scam probability is this far from 0.5 ...
SCAM_MARGIN = 0.25
#: ... and the top family beats the runner-up by at least this much.
KIND_MARGIN = 0.25


@dataclass
class Verdict:
    is_scam: bool
    p_scam: float
    kind: str
    p_kind: float
    kind_runner_up: str
    p_runner_up: float
    danger: int
    danger_probabilities: dict[int, float]
    creativity: int
    needs_review: bool
    review_reason: str
    model: str = ""
    kinds: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "is_scam": self.is_scam, "p_scam": round(self.p_scam, 3),
            "kind": self.kind, "p_kind": round(self.p_kind, 3),
            "kind_runner_up": self.kind_runner_up, "p_runner_up": round(self.p_runner_up, 3),
            "danger": self.danger, "danger_probabilities": {k: round(v, 3) for k, v in self.danger_probabilities.items()},
            "creativity": self.creativity,
            "needs_review": self.needs_review, "review_reason": self.review_reason,
            "kinds": {k: round(v, 3) for k, v in self.kinds.items()}, "model": self.model,
        }


#: Jev is served by TypeSafe directly and, under the same wire format, by
#: OpenRouter (model ``typesafe/jev-1.13``, POST /api/v1/systemone).  An
#: OpenRouter key is enough; a TypeSafe key is used if that is what is set.
OPENROUTER_BASE = "https://openrouter.ai/api"
OPENROUTER_MODEL = "typesafe/jev-1.13"


def _client() -> TypeSafeClient:
    if key := os.environ.get("OPENROUTER_API_KEY"):
        return TypeSafeClient(api_key=key, base_url=OPENROUTER_BASE, model=os.environ.get("JEV_MODEL") or OPENROUTER_MODEL)
    if key := os.environ.get("TYPESAFE_API_KEY"):
        return TypeSafeClient(api_key=key)
    raise RuntimeError("set OPENROUTER_API_KEY (model typesafe/jev-1.13) or TYPESAFE_API_KEY")


def state_for(text: str, sender: str | None = None) -> dict:
    """What Jev is shown.  Structured, so the sender is a separate field the
    model can weigh (a 10-digit mobile number sending 'bank' notices is a tell)."""
    state: dict = {"channel": "SMS received on a phone in India", "message": text.strip()}
    if sender:
        state["sender"] = sender.strip()
    return state


def classify(text: str, sender: str | None = None, *, client: TypeSafeClient | None = None) -> Verdict:
    response = (client or _client()).system_one(state_for(text, sender), QUESTIONS)
    return verdict_from(response.answers, model=response.model)


def verdict_from(answers: dict, *, model: str = "") -> Verdict:
    """Apply the gate to raw answers.  Separated so it is testable without
    the API and so the thresholds live in one place."""
    p_scam = float(answers["is_scam"].noul)
    kinds = dict(answers["kind"].probabilities)
    ranked = sorted(kinds.items(), key=lambda kv: kv[1], reverse=True)
    (kind, p_kind), (runner, p_runner) = ranked[0], (ranked[1] if len(ranked) > 1 else (ranked[0][0], 0.0))
    danger_probs = {int(k): float(v) for k, v in answers["danger"].probabilities.items()}
    danger = max(danger_probs, key=danger_probs.get)
    creativity = int(round(float(answers["creativity"].score)))
    is_scam = p_scam >= 0.5

    reasons = []
    if abs(p_scam - 0.5) < SCAM_MARGIN:
        reasons.append(f"scam probability {p_scam:.0%} is too close to even")
    if p_kind - p_runner < KIND_MARGIN and is_scam:
        reasons.append(f"'{kind}' ({p_kind:.0%}) barely beats '{runner}' ({p_runner:.0%})")
    # A confident not-scam verdict should still get eyes when the family
    # question thinks otherwise -- the two questions disagreeing is a signal.
    if not is_scam and kind != "not_scam" and p_kind > 0.5:
        reasons.append(f"not flagged as scam, yet reads as '{kind}'")

    return Verdict(
        is_scam=is_scam, p_scam=p_scam, kind=kind,
        p_kind=p_kind, kind_runner_up=runner, p_runner_up=p_runner,
        danger=danger, danger_probabilities=danger_probs, creativity=creativity,
        needs_review=bool(reasons), review_reason="; ".join(reasons), model=model, kinds=kinds,
    )
