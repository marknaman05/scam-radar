"""Scam SMS Radar -- paste a message, get a verdict.

Routes:
  GET  /                 the page
  POST /check            {text, sender?} -> verdict + stored report
  GET  /reports/{id}     one report
  POST /reports/{id}/vote
  GET  /review           the human-review queue (uncertain verdicts)
  POST /reports/{id}/rule  {is_scam, kind}  a human's ruling
  GET  /leaderboard      most creative confirmed scams
  GET  /stats

Reviewing and ruling are protected by a shared token (RADAR_REVIEW_TOKEN);
everything else is open, because the point is that anyone can paste.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from fastapi import Body, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import classify as clf
from .store import Store

load_dotenv()
log = logging.getLogger("radar")

STATIC = Path(__file__).parent / "static"
store = Store(Path(os.environ.get("RADAR_DB") or "data/radar.db"))
app = FastAPI(title="Scam SMS Radar")

MAX_CHARS = 2000


class Check(BaseModel):
    text: str = Field(min_length=8, max_length=MAX_CHARS)
    sender: str | None = Field(default=None, max_length=40)


class Ruling(BaseModel):
    is_scam: bool
    kind: str
    claim_kind: str | None = None


def reviewer(authorization: Annotated[str | None, Header()] = None) -> None:
    token = os.environ.get("RADAR_REVIEW_TOKEN")
    if not token:
        return  # no token configured: single-person local use, review is open
    if authorization != f"Bearer {token}":
        raise HTTPException(401, "review token required")


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return (STATIC / "index.html").read_text()


@app.get("/share", response_class=HTMLResponse)
async def share() -> str:
    """Where Android lands when someone shares an SMS or a WhatsApp forward
    into the installed app (see share_target in the manifest).  The page
    reads ?text= and ?title= itself."""
    return (STATIC / "index.html").read_text()


@app.get("/sw.js")
async def service_worker() -> FileResponse:
    # Served from the root so its scope covers the whole site.
    return FileResponse(STATIC / "sw.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@app.get("/manifest.json")
async def manifest() -> FileResponse:
    return FileResponse(STATIC / "manifest.json", media_type="application/manifest+json")


@app.get("/robots.txt", response_class=Response)
async def robots() -> Response:
    return Response("User-agent: *\nAllow: /\nDisallow: /review\nDisallow: /reports/\n", media_type="text/plain")


@app.post("/check")
async def check(body: Check) -> dict:
    try:
        verdict = clf.classify(body.text, body.sender)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    except Exception as exc:  # the API is down, or refused us
        log.exception("classification failed")
        raise HTTPException(502, f"could not reach the model: {exc}")
    report = store.add(body.text, body.sender, verdict.as_dict())
    log.info("#%d %s | %s %s p=%.2f | claim %s p=%.2f harm=%d%s", report["id"], verdict.content,
             "SCAM" if verdict.is_scam else "ok", verdict.kind, verdict.p_scam,
             verdict.claim_kind, verdict.p_misleading, verdict.harm, " [review]" if verdict.needs_review else "")
    return report


@app.get("/reports/{report_id}")
async def report(report_id: int) -> dict:
    r = store.get(report_id)
    if not r:
        raise HTTPException(404, "no such report")
    return r


@app.post("/reports/{report_id}/vote")
async def vote(report_id: int) -> dict:
    r = store.vote(report_id)
    if not r:
        raise HTTPException(404, "no such report")
    return r


@app.get("/review")
async def review(authorization: Annotated[str | None, Header()] = None) -> list[dict]:
    reviewer(authorization)
    return store.review_queue()


@app.post("/reports/{report_id}/rule")
async def rule(report_id: int, ruling: Ruling, authorization: Annotated[str | None, Header()] = None) -> dict:
    reviewer(authorization)
    if ruling.kind not in clf.KINDS:
        raise HTTPException(400, f"kind must be one of {', '.join(clf.KINDS)}")
    if ruling.claim_kind is not None and ruling.claim_kind not in clf.CLAIMS:
        raise HTTPException(400, f"claim_kind must be one of {', '.join(clf.CLAIMS)}")
    r = store.rule(report_id, ruling.is_scam, ruling.kind, ruling.claim_kind)
    if not r:
        raise HTTPException(404, "no such report")
    return r


@app.get("/leaderboard")
async def leaderboard() -> list[dict]:
    return store.leaderboard()


@app.get("/recent")
async def recent() -> list[dict]:
    return store.recent()


@app.get("/stats")
async def stats() -> dict:
    return {**store.stats(), "kinds": list(clf.KINDS), "claim_kinds": list(clf.CLAIMS),
            "review_protected": bool(os.environ.get("RADAR_REVIEW_TOKEN"))}
