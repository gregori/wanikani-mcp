"""Testes contra uma API do WaniKani simulada (sem rede)."""
import json

import httpx
import pytest
from starlette.testclient import TestClient

from wanikani_mcp.server import WaniKani, create_app

SECRET = "x" * 40
HOST = "wk.example.com"


def _page(data):
    return {"data": data, "pages": {"next_url": None}}


def _subject(sid, obj, chars, level, reading, meaning):
    return {"id": sid, "object": obj, "data": {
        "characters": chars, "level": level,
        "readings": [{"reading": reading, "accepted_answer": True}],
        "meanings": [{"meaning": meaning, "accepted_answer": True}]}}


SUBJECTS = [
    _subject(1, "kanji", "勤", 20, "きん", "Diligence"),
    _subject(2, "kanji", "務", 22, "む", "Task"),
    _subject(3, "vocabulary", "勤務", 22, "きんむ", "Duty"),
    _subject(4, "vocabulary", "過去", 15, "かこ", "The Past"),
]
ASSIGNMENTS = [
    {"data": {"subject_id": 1, "subject_type": "kanji", "srs_stage": 9}},
    {"data": {"subject_id": 2, "subject_type": "kanji", "srs_stage": 6}},
    {"data": {"subject_id": 3, "subject_type": "vocabulary", "srs_stage": 9}},
    {"data": {"subject_id": 4, "subject_type": "vocabulary", "srs_stage": 3}},
]
STATS = [
    {"data": {"subject_id": 4, "subject_type": "vocabulary", "percentage_correct": 60,
              "meaning_incorrect": 1, "meaning_current_streak": 2,
              "reading_incorrect": 5, "reading_current_streak": 1}},
    {"data": {"subject_id": 3, "subject_type": "vocabulary", "percentage_correct": 90,
              "meaning_incorrect": 2, "meaning_current_streak": 8,
              "reading_incorrect": 0, "reading_current_streak": 8}},
]


def fake_api(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/user"):
        return httpx.Response(200, json={"data": {"level": 43}})
    if path.endswith("/summary"):
        return httpx.Response(200, json={"data": {
            "lessons": [{"subject_ids": [7, 8]}],
            "reviews": [{"subject_ids": [1, 2, 3]}]}})
    if path.endswith("/subjects"):
        return httpx.Response(200, json=_page(SUBJECTS))
    if path.endswith("/assignments"):
        return httpx.Response(200, json=_page(ASSIGNMENTS))
    if path.endswith("/review_statistics"):
        return httpx.Response(200, json=_page(STATS))
    return httpx.Response(404)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("MCP_PATH_SECRET", SECRET)
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", HOST)
    app = create_app(WaniKani("t", transport=httpx.MockTransport(fake_api)))
    with TestClient(app, base_url=f"https://{HOST}") as c:
        yield c


def call(client, tool, args=None):
    resp = client.post(
        f"/mcp/{SECRET}",
        headers={"Accept": "application/json, text/event-stream",
                 "Content-Type": "application/json"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": tool, "arguments": args or {}}},
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    assert not result.get("isError"), result
    return json.loads(result["content"][0]["text"]) if len(result["content"]) == 1 \
        else [json.loads(c["text"]) for c in result["content"]]


def test_healthz(client):
    assert client.get("/healthz").text == "ok"


def test_wrong_path_is_404(client):
    assert client.post("/mcp/errado", json={}).status_code == 404


def test_tools_listed(client):
    resp = client.post(
        f"/mcp/{SECRET}",
        headers={"Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    names = {t["name"] for t in resp.json()["result"]["tools"]}
    assert names == {"get_progress", "get_leeches", "sample_burned", "lookup"}


def test_progress(client):
    p = call(client, "get_progress")
    assert p["level"] == 43
    assert p["progress"]["kanji"] == {"burned": 1, "guru": 1}
    assert p["reviews_available_now"] == 3 and p["lessons_available"] == 2


def test_leeches_skip_burned_by_default(client):
    rows = call(client, "get_leeches")
    rows = rows if isinstance(rows, list) else [rows]
    assert [r["item"] for r in rows] == ["過去"]
    rows = call(client, "get_leeches", {"include_burned": True})
    assert [r["item"] for r in rows] == ["過去", "勤務"]


def test_sample_burned(client):
    rows = call(client, "sample_burned", {"subject_type": "any"})
    rows = rows if isinstance(rows, list) else [rows]
    assert {r["item"] for r in rows} == {"勤", "勤務"}


def test_lookup(client):
    r = call(client, "lookup", {"text": "勤務"})
    assert r["exact"][0]["srs"] == "burned"
    assert [k["item"] for k in r["kanji"]] == ["勤", "務"]
    r = call(client, "lookup", {"text": "勤"})
    assert [v["item"] for v in r["studied_vocabulary"]] == ["勤務"]
