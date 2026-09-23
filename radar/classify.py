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

#: What kind of trouble this message is, which decides which half of the
#: answer matters.  A WhatsApp forward is rarely a scam *and* rarely nothing:
#: usually it is a claim about the world that is wrong.
CONTENT = {
    "scam": "Someone is trying to take money, credentials or access from the reader: a fake bill, a phishing link, a prize, a job, an impersonated official.",
    "misinformation": "A forwarded claim about the world -- health, politics, religion, money, a video or photo -- presented as fact, that is false, misleading, unverifiable or recycled from another time or place.",
    "both": "It is a scam *and* it carries a false claim to make the bait work.",
    "harmless": "An ordinary message: a real OTP or bank alert, a delivery update, marketing, a personal or family message, a genuine news item, an opinion nobody is passing off as fact.",
}

CLAIMS = {
    "health": "Health or medical myth: a miracle cure, a home remedy 'doctors won't tell you', vaccine or drug fear, cancer/diabetes claims, a food that supposedly kills or cures.",
    "communal": "Communal or caste hatred dressed as news: an atrocity story, a 'they are doing this to us' claim, a fabricated quote or crime attributed to a community.",
    "political": "Political misinformation: a fake statement by a leader, a doctored election claim, an invented policy, a fake government order or scheme.",
    "money": "Financial misinformation: a guaranteed-returns scheme, a currency or note rumour, a bank closing, a stock or crypto tip presented as inside knowledge.",
    "recycled": "A real photo or video from another time, place or event, retold as something happening now or nearby.",
    "doctored": "Media that has been edited, staged or AI-generated and is presented as real.",
    "chain": "A chain message: forward to N people for luck, blessings, a free recharge, or 'WhatsApp will start charging'.",
    "alarm": "A false alarm or safety rumour: a kidnapping gang in the area, a virus in a product, a lockdown, a curfew, a disaster warning with no source.",
    "other_claim": "A false or unverifiable claim that fits none of the above.",
    "no_claim": "It makes no factual claim about the world, or the claims in it are true and unremarkable.",
}

HARM = [
    "none: believing it changes nothing",
    "wastes time: a silly rumour, a chain letter",
    "misleads: someone could make a poor decision -- money, a purchase, a vote",
    "harmful: could lead to a health risk, a financial loss, or a family rift",
    "dangerous: could lead to violence, a medical emergency, a mob, or serious loss",
]

VIRALITY = [
    "no push: nothing asks you to pass it on",
    "mild: ends with 'share if you agree' or similar",
    "pushy: 'forward to 10 people', urgency, 'before it is deleted'",
    "engineered: threats, blessings, curses or deadlines attached to forwarding",
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
    # The claim half.  Asked of every message: Jev answers all of these in
    # one parallel call, so asking speculatively costs a fraction of a paisa
    # and saves a second round trip when the message turns out to be a
    # forward rather than an SMS scam.
    "content": Choice(instructions="What kind of message is this?", criteria=CONTENT),
    "is_misleading": Noul(
        instructions="Does this message state something about the world that is false, misleading, unverifiable, or taken out of its real context?",
        criteria={
            "true": "It asserts facts a careful reader could not trust: invented, distorted, missing its source, or recycled from another event.",
            "false": "It makes no factual claim, or its claims are true and fairly presented.",
        },
    ),
    "claim_kind": Choice(instructions="If it carries a false or doubtful claim, what kind is it?", criteria=CLAIMS),
    "harm": Score(instructions="How much harm would believing and forwarding this claim do?", criteria=HARM),
    "virality": Score(instructions="How hard does the message push the reader to forward it?", criteria=VIRALITY),
}

#: A verdict is certain when the scam probability is this far from 0.5 ...
SCAM_MARGIN = 0.25
#: ... and the family answer's *confidence* -- TypeSafe's own statistic for
#: how concentrated the choice distribution is (1.0 = all on one label,
#: lower as it spreads) -- clears their "don't guess" line.  Their guidance:
#: below 0.5 the model is genuinely unsure; 0.5-0.9 proceed with care; above
#: 0.9 act automatically.  Choice and Score answers carry it; Noul does not,
#: which is why the scam question is gated on its probability instead.
KIND_CONFIDENCE = 0.5
#: Danger is used only for display, so a spread-out danger distribution is
#: reported rather than gated; below this the level is shown as a range.
DANGER_CONFIDENCE = 0.5
#: The claim half gets the same treatment as the scam half.
MISLEADING_MARGIN = 0.25
CLAIM_CONFIDENCE = 0.5
#: Below this the router itself is guessing, so both halves are judged.
CONTENT_CONFIDENCE = 0.5


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
    kind_confidence: float = 1.0
    danger_confidence: float = 1.0
    creativity_confidence: float = 1.0
    # The claim half: what a WhatsApp forward is usually about.
    content: str = "scam"
    content_confidence: float = 1.0
    is_misleading: bool = False
    p_misleading: float = 0.0
    claim_kind: str = "no_claim"
    p_claim: float = 0.0
    claim_confidence: float = 1.0
    claims: dict[str, float] = field(default_factory=dict)
    harm: int = 0
    virality: int = 0

    def as_dict(self) -> dict:
        return {
            "is_scam": self.is_scam, "p_scam": round(self.p_scam, 3),
            "kind": self.kind, "p_kind": round(self.p_kind, 3),
            "kind_runner_up": self.kind_runner_up, "p_runner_up": round(self.p_runner_up, 3),
            "danger": self.danger, "danger_probabilities": {k: round(v, 3) for k, v in self.danger_probabilities.items()},
            "creativity": self.creativity,
            "needs_review": self.needs_review, "review_reason": self.review_reason,
            "kinds": {k: round(v, 3) for k, v in self.kinds.items()}, "model": self.model,
            "kind_confidence": round(self.kind_confidence, 3), "danger_confidence": round(self.danger_confidence, 3),
            "creativity_confidence": round(self.creativity_confidence, 3),
            "content": self.content, "content_confidence": round(self.content_confidence, 3),
            "is_misleading": self.is_misleading, "p_misleading": round(self.p_misleading, 3),
            "claim_kind": self.claim_kind, "p_claim": round(self.p_claim, 3),
            "claim_confidence": round(self.claim_confidence, 3),
            "claims": {k: round(v, 3) for k, v in self.claims.items()},
            "harm": self.harm, "virality": self.virality,
        }

    @property
    def headline(self) -> str:
        """The one thing to tell the reader."""
        if self.content == "both":
            return "scam, and it lies to sell the bait"
        if self.content == "misinformation" or (self.is_misleading and not self.is_scam):
            return "false or misleading claim"
        if self.is_scam:
            return "scam"
        return "looks genuine"


#: Jev is served by three gateways.  The same questions go to each; the
#: first configured key wins:
#:   AI_GATEWAY_API_KEY  Vercel AI Gateway, POST /v1/evaluate (model typesafe-ai/jev;
#:                       same shape except the yes/no question is called "boolean")
#:   OPENROUTER_API_KEY  OpenRouter, POST /api/v1/systemone (model typesafe/jev-1.13)
#:   TYPESAFE_API_KEY    TypeSafe directly
VERCEL_URL = "https://ai-gateway.vercel.sh/v1/evaluate"
VERCEL_MODEL = "typesafe-ai/jev"
OPENROUTER_BASE = "https://openrouter.ai/api"
OPENROUTER_MODEL = "typesafe/jev-1.13"


class _Answer:
    """Duck-typed answer for the Vercel path, matching the SDK's attributes."""

    def __init__(self, raw: dict) -> None:
        self.__dict__.update(raw)


class _Response:
    def __init__(self, body: dict) -> None:
        self.model = body.get("model", VERCEL_MODEL)
        self.answers = {name: _Answer(a) for name, a in body["answers"].items()}


class VercelJev:
    """The Vercel AI Gateway route, without the SDK (it hard-codes TypeSafe's path)."""

    def __init__(self, api_key: str, model: str = VERCEL_MODEL) -> None:
        self.api_key, self.model = api_key, model

    def system_one(self, state, questions) -> _Response:
        import json
        import urllib.request

        qs = {}
        for name, q in questions.items():
            d = q.model_dump(exclude_none=True)
            if d["type"] == "noul":
                d["type"] = "boolean"
            qs[name] = d
        req = urllib.request.Request(
            VERCEL_URL, method="POST", data=json.dumps({"model": self.model, "state": state, "questions": qs}).encode(),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            body = json.load(r)
        for a in body.get("answers", {}).values():        # "boolean" answers back to the SDK's field name
            if a.get("type") == "boolean" and "noul" not in a:
                a["noul"] = a.get("boolean", a.get("probability", a.get("value")))
        return _Response(body)


def _client():
    if key := os.environ.get("AI_GATEWAY_API_KEY"):
        return VercelJev(key, os.environ.get("JEV_MODEL") or VERCEL_MODEL)
    if key := os.environ.get("OPENROUTER_API_KEY"):
        return TypeSafeClient(api_key=key, base_url=OPENROUTER_BASE, model=os.environ.get("JEV_MODEL") or OPENROUTER_MODEL)
    if key := os.environ.get("TYPESAFE_API_KEY"):
        return TypeSafeClient(api_key=key)
    raise RuntimeError("set AI_GATEWAY_API_KEY (Vercel), OPENROUTER_API_KEY, or TYPESAFE_API_KEY")


def state_for(text: str, sender: str | None = None) -> dict:
    """What Jev is shown.  Structured, so the sender is a separate field the
    model can weigh (a 10-digit mobile number sending 'bank' notices is a tell)."""
    state: dict = {"channel": "SMS received on a phone in India", "message": text.strip()}
    if sender:
        state["sender"] = sender.strip()
    return state


def classify(text: str, sender: str | None = None, *, client=None) -> Verdict:
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
    conf = lambda name: float(getattr(answers[name], "confidence", 1.0))
    kind_conf, danger_conf, cre_conf = conf("kind"), conf("danger"), conf("creativity")

    # The claim half.  Absent from an older stored answer, so every read is
    # defensive: this function is also used to re-render historical rows.
    def claim_answers() -> dict:
        if "content" not in answers:
            return {}
        claims = dict(answers["claim_kind"].probabilities)
        top = sorted(claims.items(), key=lambda kv: kv[1], reverse=True)
        p_mis = float(answers["is_misleading"].noul)
        harm_probs = {int(k): float(v) for k, v in answers["harm"].probabilities.items()}
        vir_probs = {int(k): float(v) for k, v in answers["virality"].probabilities.items()}
        return {
            "content": answers["content"].choice, "content_confidence": conf("content"),
            "is_misleading": p_mis >= 0.5, "p_misleading": p_mis,
            "claim_kind": top[0][0], "p_claim": top[0][1], "claim_confidence": conf("claim_kind"),
            "claims": claims,
            "harm": max(harm_probs, key=harm_probs.get), "virality": max(vir_probs, key=vir_probs.get),
        }

    claim = claim_answers()

    # Which half of the answer matters is itself a question, so the gate
    # follows it: a WhatsApp health myth should not be second-guessed for
    # being a poor scam, and an electricity SMS should not be quizzed on its
    # factual accuracy.  When the router itself is unsure, both halves are
    # judged -- that uncertainty is exactly when a human should look.
    content = claim.get("content", "scam")
    content_conf = claim.get("content_confidence", 1.0)
    router_unsure = bool(claim) and content_conf < CONTENT_CONFIDENCE
    judge_scam = not claim or router_unsure or content in ("scam", "both") or is_scam
    judge_claim = bool(claim) and (router_unsure or content in ("misinformation", "both") or claim["is_misleading"])

    reasons = []
    if router_unsure:
        reasons.append(f"unclear what kind of message this is: confidence {content_conf:.2f} (top '{content}')")
    if judge_scam:
        if abs(p_scam - 0.5) < SCAM_MARGIN:
            reasons.append(f"scam probability {p_scam:.0%} is too close to even")
        if is_scam and kind_conf < KIND_CONFIDENCE:
            reasons.append(f"family unclear: confidence {kind_conf:.2f} ('{kind}' {p_kind:.0%} vs '{runner}' {p_runner:.0%})")
        # A confident not-scam verdict still gets eyes when the family
        # question thinks otherwise -- the two questions disagreeing is a signal.
        if not is_scam and kind != "not_scam" and p_kind > 0.5:
            reasons.append(f"not flagged as scam, yet reads as '{kind}'")
    if judge_claim:
        if abs(claim["p_misleading"] - 0.5) < MISLEADING_MARGIN:
            reasons.append(f"misleading probability {claim['p_misleading']:.0%} is too close to even")
        if claim["is_misleading"] and claim["claim_kind"] != "no_claim" and claim["claim_confidence"] < CLAIM_CONFIDENCE:
            reasons.append(f"claim type unclear: confidence {claim['claim_confidence']:.2f} (top '{claim['claim_kind']}' {claim['p_claim']:.0%})")

    return Verdict(
        is_scam=is_scam, p_scam=p_scam, kind=kind,
        p_kind=p_kind, kind_runner_up=runner, p_runner_up=p_runner,
        danger=danger, danger_probabilities=danger_probs, creativity=creativity,
        needs_review=bool(reasons), review_reason="; ".join(reasons), model=model, kinds=kinds,
        kind_confidence=kind_conf, danger_confidence=danger_conf, creativity_confidence=cre_conf,
        **claim,
    )
