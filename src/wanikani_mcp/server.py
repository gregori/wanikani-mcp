"""Servidor MCP read-only para o progresso no WaniKani.

Variáveis de ambiente:
    WANIKANI_API_TOKEN   token read-only do WaniKani (obrigatório)
    MCP_PATH_SECRET      segredo no caminho: o endpoint fica em /mcp/<segredo>
                         (obrigatório, 32+ caracteres)
    MCP_ALLOWED_HOSTS    hosts aceitos no header Host, separados por vírgula
                         (ex.: wk.gregori.eti.br). Vazio = sem checagem.
    PORT                 porta HTTP (padrão 8000)
"""
from __future__ import annotations

import asyncio
import os
import random
import time
from collections import Counter
from typing import Any

import httpx
import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import PlainTextResponse

API = "https://api.wanikani.com/v2"
SRS_GROUPS = {
    1: "apprentice", 2: "apprentice", 3: "apprentice", 4: "apprentice",
    5: "guru", 6: "guru", 7: "master", 8: "enlightened", 9: "burned",
}
SUBJECTS_TTL = 24 * 3600  # o catálogo de subjects muda raramente
USER_DATA_TTL = 10 * 60   # assignments/estatísticas mudam a cada revisão


class WaniKani:
    """Cliente da API com cache em memória."""

    def __init__(self, token: str, transport: httpx.AsyncBaseTransport | None = None):
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {token}", "Wanikani-Revision": "20170710"},
            timeout=30,
            transport=transport,
        )
        self._lock = asyncio.Lock()
        self._subjects: dict[int, dict[str, Any]] = {}
        self._by_chars: dict[str, list[int]] = {}
        self._subjects_at = 0.0
        self._user_cache: dict[str, tuple[float, Any]] = {}

    async def _get(self, url: str) -> dict[str, Any]:
        for attempt in range(5):
            resp = await self._client.get(url)
            if resp.status_code == 429:
                await asyncio.sleep(10 * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError("Rate limit do WaniKani persistente; tente mais tarde.")

    async def _get_all(self, path: str) -> list[dict[str, Any]]:
        url: str | None = f"{API}{path}"
        items: list[dict[str, Any]] = []
        while url:
            page = await self._get(url)
            items.extend(page["data"])
            url = page["pages"]["next_url"]
        return items

    async def subjects(self) -> dict[int, dict[str, Any]]:
        async with self._lock:
            if time.monotonic() - self._subjects_at > SUBJECTS_TTL or not self._subjects:
                raw = await self._get_all("/subjects?types=kanji,vocabulary,kana_vocabulary")
                self._subjects = {s["id"]: _slim(s) for s in raw}
                self._by_chars = {}
                for sid, s in self._subjects.items():
                    if s["item"]:
                        self._by_chars.setdefault(s["item"], []).append(sid)
                self._subjects_at = time.monotonic()
        return self._subjects

    async def _cached(self, key: str, path: str) -> Any:
        hit = self._user_cache.get(key)
        if hit and time.monotonic() - hit[0] < USER_DATA_TTL:
            return hit[1]
        data = await (self._get(f"{API}{path}") if path in ("/user", "/summary")
                      else self._get_all(path))
        self._user_cache[key] = (time.monotonic(), data)
        return data

    async def user(self) -> dict[str, Any]:
        return (await self._cached("user", "/user"))["data"]

    async def summary(self) -> dict[str, Any]:
        return (await self._cached("summary", "/summary"))["data"]

    async def assignments(self) -> dict[int, dict[str, Any]]:
        raw = await self._cached("assignments", "/assignments?started=true&hidden=false")
        return {a["data"]["subject_id"]: a["data"] for a in raw}

    async def review_stats(self) -> list[dict[str, Any]]:
        raw = await self._cached("stats", "/review_statistics?hidden=false")
        return [r["data"] for r in raw]

    def ids_for(self, text: str) -> list[int]:
        return self._by_chars.get(text, [])


def _slim(subject: dict[str, Any]) -> dict[str, Any]:
    d = subject["data"]
    return {
        "id": subject["id"],
        "type": subject["object"],
        "item": d.get("characters"),
        "wk_level": d.get("level"),
        "readings": [r["reading"] for r in d.get("readings", []) if r.get("accepted_answer")],
        "meanings": [m["meaning"] for m in d.get("meanings", []) if m.get("accepted_answer")],
    }


def _leech_score(stat: dict[str, Any]) -> float:
    scores = []
    for kind in ("meaning", "reading"):
        wrong = stat.get(f"{kind}_incorrect", 0)
        streak = max(stat.get(f"{kind}_current_streak", 1), 1)
        if wrong:
            scores.append(wrong / streak**1.5)
    return max(scores, default=0.0)


def _srs(assignment: dict[str, Any] | None) -> str:
    if not assignment:
        return "not_started"
    return SRS_GROUPS.get(assignment["srs_stage"], "unknown")


def build_server(wk: WaniKani) -> MCPServer:
    mcp = MCPServer(
        name="wanikani",
        instructions=(
            "Progresso do usuário no WaniKani (somente leitura). Use para calibrar "
            "exercícios de kanji e vocabulário: o que ele já estudou, em que estágio "
            "SRS está e quais itens ele mais erra."
        ),
    )

    @mcp.tool()
    async def get_progress() -> dict[str, Any]:
        """Nível atual, contagem de kanji/vocabulário por estágio SRS e revisões pendentes."""
        user, assignments, summary = await asyncio.gather(
            wk.user(), wk.assignments(), wk.summary())
        counts: dict[str, Counter[str]] = {}
        for a in assignments.values():
            if a["subject_type"] == "radical":
                continue
            counts.setdefault(a["subject_type"], Counter())[_srs(a)] += 1
        reviews_now = len(summary["reviews"][0]["subject_ids"]) if summary["reviews"] else 0
        lessons = sum(len(lesson["subject_ids"]) for lesson in summary.get("lessons", []))
        return {
            "level": user["level"],
            "progress": {t: dict(c) for t, c in counts.items()},
            "reviews_available_now": reviews_now,
            "lessons_available": lessons,
        }

    @mcp.tool()
    async def get_leeches(top: int = 30, include_burned: bool = False) -> list[dict[str, Any]]:
        """Itens (kanji/vocabulário) que o usuário mais erra, do pior para o melhor.

        top: quantos itens retornar (máx. 100).
        include_burned: incluir itens já burned (normalmente têm erros antigos).
        """
        subjects, assignments, stats = await asyncio.gather(
            wk.subjects(), wk.assignments(), wk.review_stats())
        rows = []
        for st in stats:
            if st["subject_type"] == "radical":
                continue
            srs = _srs(assignments.get(st["subject_id"]))
            if srs == "burned" and not include_burned:
                continue
            score = _leech_score(st)
            if score <= 0 or st["subject_id"] not in subjects:
                continue
            rows.append({**subjects[st["subject_id"]], "srs": srs,
                         "pct_correct": st["percentage_correct"],
                         "leech_score": round(score, 2)})
        rows.sort(key=lambda r: r["leech_score"], reverse=True)
        return rows[: max(1, min(top, 100))]

    @mcp.tool()
    async def sample_burned(
        n: int = 10,
        subject_type: str = "vocabulary",
        min_level: int = 1,
        max_level: int = 60,
    ) -> list[dict[str, Any]]:
        """Sorteia itens já burned (que o WaniKani não revisa mais) para testar retenção.

        subject_type: "kanji", "vocabulary" ou "any".
        min_level/max_level: faixa de níveis do WaniKani.
        """
        subjects, assignments = await asyncio.gather(wk.subjects(), wk.assignments())
        pool = [
            subjects[sid] for sid, a in assignments.items()
            if a["srs_stage"] == 9 and sid in subjects
            and (subject_type == "any" or subjects[sid]["type"].endswith(subject_type))
            and min_level <= (subjects[sid]["wk_level"] or 0) <= max_level
        ]
        return random.sample(pool, min(max(n, 1), 50, len(pool)))

    @mcp.tool()
    async def lookup(text: str) -> dict[str, Any]:
        """Status no WaniKani de uma palavra ou kanji (ex.: "勤務" ou "勤").

        Retorna a correspondência exata (se houver), o status de cada kanji da
        palavra e, para um kanji isolado, vocabulários já estudados que o usam.
        """
        subjects, assignments = await asyncio.gather(wk.subjects(), wk.assignments())

        def entry(sid: int) -> dict[str, Any]:
            return {**subjects[sid], "srs": _srs(assignments.get(sid))}

        text = text.strip()
        exact = [entry(sid) for sid in wk.ids_for(text)]
        kanji = []
        for ch in dict.fromkeys(text):
            ids = [sid for sid in wk.ids_for(ch) if subjects[sid]["type"] == "kanji"]
            kanji.extend(entry(sid) for sid in ids)
        result: dict[str, Any] = {"query": text, "exact": exact, "kanji": kanji}
        if len(text) == 1:
            result["studied_vocabulary"] = [
                entry(sid) for sid, s in subjects.items()
                if s["type"] != "kanji" and s["item"] and text in s["item"]
                and sid in assignments
            ][:15]
        return result

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(_: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    return mcp


def create_app(wk: WaniKani | None = None):
    token = os.environ.get("WANIKANI_API_TOKEN", "")
    secret = os.environ.get("MCP_PATH_SECRET", "")
    if len(secret) < 32:
        raise SystemExit("MCP_PATH_SECRET precisa ter 32+ caracteres.")
    if wk is None:
        if not token:
            raise SystemExit("Defina WANIKANI_API_TOKEN.")
        wk = WaniKani(token)
    hosts = [h.strip() for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=bool(hosts),
        allowed_hosts=hosts,
        allowed_origins=[f"https://{h}" for h in hosts] + ["https://claude.ai"],
    )
    return build_server(wk).streamable_http_app(
        streamable_http_path=f"/mcp/{secret}",
        stateless_http=True,
        json_response=True,
        transport_security=security,
        host="0.0.0.0",
    )


def main() -> None:
    uvicorn.run(create_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8000")),
                proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
