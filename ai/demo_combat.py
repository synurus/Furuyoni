"""
커밋 (a) 데모: 순수 공격 카드로 데미지가 들어가고 승부가 나는지 검증.

유리나(참·달그림자) vs 히미카(슛·레드불릿)는 순수 공격 카드를 가져
실제 라이프가 깎이고 게임이 라이프 0으로 끝날 수 있다.
"""

import random
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "engine"))

from engine.setup import new_game, CORE4
from engine.turn import play_turn
from ai.agents import RandomBot, AggressiveBot




def run_game(m0, m1, seed, max_turns=100, verbose=False):
    rng = random.Random(seed)
    state = new_game(m0, m1, seed=seed)
    bot = AggressiveBot(end_prob=0.3)
    for _ in range(max_turns):
        play_turn(state, rng, bot)
        state.check_conservation()
        if state.is_over():
            break
    return state


def demo_verbose():
    rng = random.Random(3)
    state = new_game("yurina", "himika", seed=3)
    bot = AggressiveBot()
    print(state.summary())
    print("=" * 60)
    for t in range(60):
        p_before = [state.players[0].life, state.players[1].life]
        play_turn(state, rng, bot)
        p_after = [state.players[0].life, state.players[1].life]
        if p_before != p_after:
            print(f"턴{state.turn_count-1}: 라이프 {p_before} → {p_after}  "
                  f"(간격{state.distance})")
        if state.is_over():
            break
    print("=" * 60)
    print(state.summary())


def stress(n=5000, max_turns=150):
    fails = 0
    ends = {}
    winners = {0: 0, 1: 0, -1: 0}
    life_end = 0
    for i in range(n):
        m0, m1 = CORE4[i % 4], CORE4[(i * 3) % 4]
        try:
            s = run_game(m0, m1, seed=i, max_turns=max_turns)
            r = s.end_reason or "max_turns"
            ends[r] = ends.get(r, 0) + 1
            if s.is_over() and s.winner is not None:
                winners[s.winner] = winners.get(s.winner, 0) + 1
            if r == "life_zero":
                life_end += 1
        except Exception as e:
            fails += 1
            if fails <= 5:
                import traceback; traceback.print_exc()
                print(f"❌ {m0}vs{m1} seed{i}: {e}")
    print(f"\n커밋(a) 스트레스: {n}게임")
    print(f"  종료 사유: {ends}")
    print(f"  라이프0로 끝난 게임: {life_end}")
    print(f"  결과: {'✅ 전부 통과' if fails==0 else f'❌ {fails}건 실패'}")


if __name__ == "__main__":
    demo_verbose()
    print()
    stress(n=5000, max_turns=150)
