"""
Phase 1 두 번째 커밋 데모: 턴 상태머신 검증.

두 랜덤 봇이 개시/메인/종료 페이즈를 정상적으로 돌리며,
- 집중력 획득, 카드 뽑기, 손패 상한 정리가 규칙대로 되는지
- 보존식이 유지되는지
- 덱아웃/무한루프 없이 게임이 진행되는지
확인한다. (아직 공격/데미지가 없어 라이프 0 승리는 안 나므로,
최대 턴 수 제한으로 종료시켜 상태를 관찰한다.)
"""

import random
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "engine"))

from engine.setup import new_game, CORE4
from engine.turn import play_turn
from ai.agents import RandomBot


def run_one_game(m0, m1, seed, max_turns=50, verbose=False):
    rng = random.Random(seed)
    state = new_game(m0, m1, seed=seed)
    bot = RandomBot(end_prob=0.4)

    if verbose:
        print(state.summary())
        print("-" * 60)

    for _ in range(max_turns):
        alive = play_turn(state, rng, bot)
        state.check_conservation()
        if verbose:
            p = state.players[1 - state.active]  # 방금 턴 둔 사람
            print(f"턴{state.turn_count-1} 종료 후: 간격{state.distance} 더스트{state.dust} "
                  f"| P0 라이프{state.players[0].life} 손패{len(state.players[0].hand)} "
                  f"패산{len(state.players[0].deck)} 집중{state.players[0].vigor} "
                  f"| P1 라이프{state.players[1].life} 손패{len(state.players[1].hand)} "
                  f"패산{len(state.players[1].deck)} 집중{state.players[1].vigor}")
        if not alive or state.is_over():
            break

    if verbose:
        print("-" * 60)
        print(state.summary())
    return state


def stress(n_games=3000, max_turns=60):
    fails, ends = 0, {}
    for i in range(n_games):
        m0, m1 = CORE4[i % 4], CORE4[(i // 4) % 4]
        try:
            s = run_one_game(m0, m1, seed=i, max_turns=max_turns)
            r = s.end_reason or "max_turns"
            ends[r] = ends.get(r, 0) + 1
        except Exception as e:
            fails += 1
            if fails <= 5:
                import traceback
                print(f"❌ 게임{i} {m0}vs{m1}: {e}")
                traceback.print_exc()
    print(f"\n턴 상태머신 스트레스: {n_games}게임 (각 최대 {max_turns}턴)")
    print(f"  종료 사유 분포: {ends}")
    print(f"  결과: {'✅ 전부 통과' if fails==0 else f'❌ {fails}건 실패'}")
    return fails


if __name__ == "__main__":
    print("=" * 60)
    print("한 게임 상세 (yurina vs tokoyo, seed=7)")
    print("=" * 60)
    run_one_game("yurina", "tokoyo", seed=7, max_turns=12, verbose=True)
    print()
    stress(n_games=3000, max_turns=60)
