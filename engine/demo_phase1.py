"""
Phase 1 첫 커밋 데모:
두 봇이 기본동작만 반복하며 벚꽃결정 보존식이 유지되는지 확인한다.

아직 카드 사용/데미지/승패는 구현 전이므로,
'간격을 사이에 두고 전진·후퇴·휘감기 등이 규칙대로 결정을 옮기는가'를 눈으로 확인하는 게 목적.
"""

import random
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from setup import new_game, CORE4
from tokens import legal_basic_actions, perform_basic_action, BASIC_ACTION_KR


def random_basic_bot(state, pidx, rng):
    """가능한 기본동작 중 하나를 무작위로 고르는 봇."""
    options = legal_basic_actions(state, pidx)
    if not options:
        return None
    return rng.choice(options)


def run_demo(megami0="yurina", megami1="tokoyo", seed=42,
             steps=40, verbose=True):
    rng = random.Random(seed)
    state = new_game(megami0, megami1, seed=seed)

    if verbose:
        print("=" * 60)
        print("초기 상태")
        print(state.summary())
        print("=" * 60)

    for step in range(steps):
        pidx = state.active
        action = random_basic_bot(state, pidx, rng)
        if action is None:
            if verbose:
                print(f"[step {step}] P{pidx} 가능한 기본동작 없음")
        else:
            before = state.token_total()
            perform_basic_action(state, pidx, action)
            # 매 동작 후 보존식 검사 (move_tokens 내부에서도 하지만 이중 확인)
            state.check_conservation()
            after = state.token_total()
            if verbose:
                print(f"[step {step}] P{pidx}({state.players[pidx].name}) "
                      f"{BASIC_ACTION_KR[action]}  "
                      f"→ 간격{state.distance} 더스트{state.dust} "
                      f"P{pidx}오라{state.players[pidx].aura} "
                      f"플레어{state.players[pidx].flare} "
                      f"[결정합 {after}]")
            assert before == after == 36, f"보존 위반! {before}→{after}"

        # 턴 넘기기 (임시: 매 동작마다 활성 전환)
        state.active = 1 - state.active
        state.turn_count += 1

    if verbose:
        print("=" * 60)
        print("최종 상태")
        print(state.summary())
        print("=" * 60)
        print(f"✅ {steps}스텝 동안 벚꽃결정 보존식 유지 (항상 36개)")

    return state


def stress_test(n_games=2000, steps=100):
    """다양한 시드/여신 조합으로 보존식 위반이 없는지 대량 검사."""
    fails = 0
    for i in range(n_games):
        m0 = CORE4[i % 4]
        m1 = CORE4[(i // 4) % 4]
        try:
            run_demo(m0, m1, seed=i, steps=steps, verbose=False)
        except Exception as e:
            fails += 1
            if fails <= 5:
                print(f"  ❌ 게임 {i} ({m0} vs {m1}): {e}")
    print(f"\n스트레스 테스트: {n_games}게임 × {steps}스텝")
    print(f"  결과: {'✅ 전부 통과' if fails == 0 else f'❌ {fails}건 실패'}")
    return fails


if __name__ == "__main__":
    # 1) 한 판 자세히 보기
    run_demo(megami0="yurina", megami1="tokoyo", seed=42, steps=20, verbose=True)
    print()
    # 2) 대량 스트레스 테스트
    stress_test(n_games=2000, steps=100)
