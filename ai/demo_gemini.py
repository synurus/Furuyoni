"""
Gemini 에이전트 데모.

실행: python ai/demo_gemini.py

GOOGLE_API_KEY 환경변수가 설정돼 있으면 실제 Gemini가 플레이하고,
없으면 폴백(휴리스틱) 모드로 동작하며 프롬프트 예시를 보여준다.
"""

import os
import sys
import random

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from setup import new_game_2v2               # noqa: E402
from turn import play_turn                    # noqa: E402
from cards import legal_card_uses             # noqa: E402
from ai.gemini_agent import GeminiBot         # noqa: E402
from ai.heuristic import HeuristicBot         # noqa: E402
from ai.state_text import (render_state,      # noqa: E402
                           render_legal_actions)


def show_prompt_example():
    """프롬프트가 어떻게 생성되는지 예시 출력."""
    s = new_game_2v2(("raira", "yukihi"), ("megumi", "shinra"), seed=3)
    s.players[0].fuujin = 5
    s.players[0].raijin = 4
    legal = [("card", x) for x in legal_card_uses(s, 0, False)]
    legal.append(("end", None))
    print("=" * 60)
    print("프롬프트 예시 (Gemini에게 전달되는 내용):")
    print("=" * 60)
    print(render_state(s, 0))
    print()
    print(render_legal_actions(s, 0, legal))
    print("=" * 60)


def play_one_game(verbose=True):
    """Gemini(P0) vs 휴리스틱(P1) 한 판."""
    rng = random.Random(0)
    s = new_game_2v2(("yurina", "saine"), ("himika", "tokoyo"),
                     seed=0, first=0)
    gemini = GeminiBot(model="gemini-2.0-flash", verbose=verbose)
    opp = HeuristicBot(seed=1)

    print(f"\nGemini 초기화: {gemini.stats()}")
    if gemini.stats()["init_error"]:
        print("→ API 미설정. 폴백(휴리스틱) 모드로 진행합니다.")
        print("  실제 Gemini로 플레이하려면 GOOGLE_API_KEY를 설정하세요.\n")

    turn = 0
    while not s.is_over() and turn < 300:
        bot = gemini if s.active == 0 else opp
        play_turn(s, rng, bot)
        turn += 1

    print(f"\n{'='*40}")
    if s.winner is not None:
        who = "Gemini(P0)" if s.winner == 0 else "휴리스틱(P1)"
        print(f"승자: {who} ({s.end_reason})")
    else:
        print("무승부/미결")
    print(f"Gemini 호출 통계: {gemini.stats()}")


if __name__ == "__main__":
    show_prompt_example()
    play_one_game(verbose=True)
