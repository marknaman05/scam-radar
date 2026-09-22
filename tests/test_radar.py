"""The gate and the app, with the model faked at the SDK seam."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from radar import app as radar_app
from radar import classify as clf
from radar.store import Store, fingerprint


def confidence(probs):
    """TypeSafe's statistic: (n * p_max - 1) / (n - 1); 1.0 when all on one option."""
    n = len(probs)
    return 1.0 if n < 2 else (n * max(probs.values()) - 1) / (n - 1)


def answers(p_scam, kinds, danger, creativity=1.0):
    """Shape the SDK returns: .noul / .probabilities / .score / .confidence."""
    return {
        "is_scam": SimpleNamespace(noul=p_scam),
        "kind": SimpleNamespace(choice=max(kinds, key=kinds.get), probabilities=kinds, confidence=confidence(kinds)),
        "danger": SimpleNamespace(score=float(max(danger, key=danger.get)), probabilities=danger, confidence=confidence(danger)),
        "creativity": SimpleNamespace(score=creativity, probabilities={}, confidence=1.0),
    }


class TestGate:
    def test_confident_scam_passes(self):
        v = clf.verdict_from(answers(0.97, {"electricity": 0.9, "kyc": 0.05, "not_scam": 0.02}, {4: 0.7, 3: 0.3}, 2.2))
        assert v.is_scam and v.kind == "electricity" and v.danger == 4 and v.creativity == 2
        assert not v.needs_review

    def test_even_probability_is_gated(self):
        v = clf.verdict_from(answers(0.55, {"bank": 0.8, "kyc": 0.1}, {2: 1.0}))
        assert v.needs_review and "close to even" in v.review_reason

    def test_low_family_confidence_is_gated(self):
        # three-way spread: confidence (3*0.42-1)/2 = 0.13, well under 0.5
        v = clf.verdict_from(answers(0.95, {"kyc": 0.42, "bank": 0.40, "courier": 0.18}, {3: 1.0}))
        assert v.needs_review and "family unclear" in v.review_reason and v.kind == "kyc"
        assert v.kind_confidence == pytest.approx(0.13, abs=0.01)

    def test_confident_family_is_not_gated(self):
        v = clf.verdict_from(answers(0.95, {"kyc": 0.8, "bank": 0.15, "courier": 0.05}, {3: 1.0}))
        assert not v.needs_review and v.kind_confidence == pytest.approx(0.7, abs=0.01)

    def test_disagreement_is_gated(self):
        v = clf.verdict_from(answers(0.1, {"lottery": 0.7, "not_scam": 0.3}, {1: 1.0}))
        assert not v.is_scam and v.needs_review and "reads as 'lottery'" in v.review_reason

    def test_confident_genuine_passes(self):
        v = clf.verdict_from(answers(0.05, {"not_scam": 0.95, "bank": 0.05}, {0: 0.9, 1: 0.1}))
        assert not v.is_scam and v.kind == "not_scam" and not v.needs_review


class TestFingerprint:
    def test_numbers_and_links_do_not_matter(self):
        a = "Your electricity will be cut tonight. Call 9876543210 or visit http://bit.ly/x1"
        b = "Your electricity will be cut tonight. Call 9123456789 or visit http://bit.ly/zz9"
        assert fingerprint(a) == fingerprint(b)
        assert fingerprint(a) != fingerprint("Your parcel is waiting")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(radar_app, "store", Store(tmp_path / "r.db"))
    monkeypatch.delenv("RADAR_REVIEW_TOKEN", raising=False)
    return TestClient(radar_app.app)


def fake_model(monkeypatch, p_scam, kinds, danger, creativity=1.0):
    monkeypatch.setattr(clf, "classify", lambda text, sender=None, client=None:
                        clf.verdict_from(answers(p_scam, kinds, danger, creativity), model="fake"))


class TestApp:
    def test_check_stores_and_returns_verdict(self, client, monkeypatch):
        fake_model(monkeypatch, 0.96, {"electricity": 0.85, "impersonation": 0.1}, {4: 0.8, 3: 0.2}, 3.0)
        r = client.post("/check", json={"text": "Dear customer your electricity will be disconnected tonight call 9876543210", "sender": "+919876543210"})
        assert r.status_code == 200
        body = r.json()
        assert body["is_scam"] and body["kind"] == "electricity" and body["danger"] == 4 and body["creativity"] == 3
        assert body["seen_before"] == 0 and body["verdict"]["p_scam"] == 0.96
        again = client.post("/check", json={"text": "Dear customer your electricity will be disconnected tonight call 9111111111"}).json()
        assert again["seen_before"] == 1

    def test_uncertain_goes_to_review_and_human_rules(self, client, monkeypatch):
        fake_model(monkeypatch, 0.52, {"kyc": 0.5, "bank": 0.45}, {3: 1.0})
        rep = client.post("/check", json={"text": "Your account KYC is pending, update within 24 hours"}).json()
        assert rep["needs_review"]
        queue = client.get("/review").json()
        assert [q["id"] for q in queue] == [rep["id"]]
        assert client.get("/leaderboard").json() == []          # unconfirmed: not on the board
        ruled = client.post(f"/reports/{rep['id']}/rule", json={"is_scam": True, "kind": "bank"}).json()
        assert ruled["final_kind"] == "bank" and ruled["human_is_scam"] is True and ruled["reviewed"]
        assert client.get("/review").json() == []
        assert [b["id"] for b in client.get("/leaderboard").json()] == [rep["id"]]
        s = client.get("/stats").json()
        assert s["reviewed"] == 1 and s["model_agreed_with_human"] == 0 and s["by_kind"] == {"bank": 1}

    def test_review_token_when_configured(self, client, monkeypatch):
        monkeypatch.setenv("RADAR_REVIEW_TOKEN", "s3cret")
        assert client.get("/review").status_code == 401
        assert client.get("/review", headers={"Authorization": "Bearer s3cret"}).status_code == 200

    def test_leaderboard_orders_by_creativity_then_votes(self, client, monkeypatch):
        fake_model(monkeypatch, 0.99, {"lottery": 0.9}, {2: 1.0}, 1.0)
        dull = client.post("/check", json={"text": "You have won a lottery of 25 lakh, call now"}).json()
        fake_model(monkeypatch, 0.99, {"impersonation": 0.9}, {4: 1.0}, 4.0)
        clever = client.post("/check", json={"text": "This is the Mumbai cyber cell, a parcel in your name held narcotics; join the video call to record your statement"}).json()
        client.post(f"/reports/{dull['id']}/vote")
        board = client.get("/leaderboard").json()
        assert [b["id"] for b in board] == [clever["id"], dull["id"]]

    def test_bad_input(self, client):
        assert client.post("/check", json={"text": "hi"}).status_code == 422
        assert client.post("/reports/999/rule", json={"is_scam": True, "kind": "nope"}).status_code == 400

    def test_model_unconfigured_is_503(self, client, monkeypatch):
        monkeypatch.delenv("TYPESAFE_API_KEY", raising=False); monkeypatch.delenv("OPENROUTER_API_KEY", raising=False); monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
        assert client.post("/check", json={"text": "your KYC is pending click here"}).status_code == 503
