"""Furuyoni self-play iteration 결과를 Notion DB에 한 줄 기록한다.

사용 전 저장소 루트에 .env 파일을 두고 값 두 개를 채운다:
    NOTION_TOKEN        https://www.notion.so/developers/tokens 에서 발급 (ntn_ 로 시작)
    NOTION_DATABASE_ID  DB URL 의 32자리 해시

단독 실행하면 더미 값으로 한 줄 써본다:
    python notion_log.py
"""

import os
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()

API = "https://api.notion.com/v1"
_HEADERS = {
    "Authorization": f"Bearer {os.environ.get('NOTION_TOKEN', '')}",
    "Notion-Version": "2026-03-11",
    "Content-Type": "application/json",
}
_data_source_id = None


def _resolve_data_source() -> str:
    """DB 하나에 데이터소스가 여러 개일 수 있어 첫 번째를 쓴다. 한 번만 조회."""
    global _data_source_id
    if _data_source_id is None:
        db_id = os.environ["NOTION_DATABASE_ID"]
        r = requests.get(f"{API}/databases/{db_id}", headers=_HEADERS, timeout=30)
        r.raise_for_status()
        _data_source_id = r.json()["data_sources"][0]["id"]
    return _data_source_id


def log_iteration(
    iteration: int,
    vs_mc: float | None = None,
    vs_heuristic: float | None = None,
    gen_games: int | None = None,
    window: int | None = None,
    samples: int | None = None,
    val_acc: float | None = None,
    commit: str | None = None,
    notes: str = "",
) -> str:
    """한 iteration 결과를 기록하고 생성된 Notion 페이지 URL 을 돌려준다."""
    props = {
        "Name": {"title": [{"text": {"content": f"iter {iteration}"}}]},
        "RunAt": {"date": {"start": datetime.now().astimezone().isoformat()}},
        "Iteration": {"number": iteration},
    }
    for key, value in (
        ("VsMC", vs_mc),
        ("VsHeuristic", vs_heuristic),
        ("GenGames", gen_games),
        ("Window", window),
        ("Samples", samples),
        ("ValAcc", val_acc),
    ):
        if value is not None:
            props[key] = {"number": value}
    for key, value in (("Commit", commit), ("Notes", notes)):
        if value:
            props[key] = {"rich_text": [{"text": {"content": value}}]}

    r = requests.post(
        f"{API}/pages",
        headers=_HEADERS,
        json={
            "parent": {"type": "data_source_id", "data_source_id": _resolve_data_source()},
            "properties": props,
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["url"]


if __name__ == "__main__":
    print(
        log_iteration(
            iteration=0,
            vs_mc=52.5,
            vs_heuristic=55.0,
            gen_games=200,
            window=5,
            commit="smoke",
            notes="연결 확인용 더미 기록",
        )
    )
