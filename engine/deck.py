"""
패산의 재구성 (룰북 9-7)

어떤 플레이어가 재구성을 수행하는 경우:
  i:  규칙에 의해 생성된 재구성이면 라이프에 1 데미지
  ii: 버림패 및 덮음패의 통상패 전부를 패산으로 이동 후 섞는다

주의: 카드 효과(천년의 새 등)에 의한 재구성은 라이프 데미지 없음(by_rule=False).
"""

import random
from state import GameState


def reconstruct_deck(state: GameState, pidx: int, by_rule: bool,
                     rng: random.Random = None, agent=None) -> None:
    """
    패산 재구성.
    by_rule=True  : 규칙에 의한 재구성 → 라이프 -1
    by_rule=False : 효과에 의한 재구성 → 데미지 없음
    설치(오보로): 재구성 직전, 덮음패의 설치 카드 1장 사용 가능 (라이프 지불보다 먼저).
    """
    p = state.players[pidx]

    # 라이라 대전 해제 (10-4): 재구성 직전, 대전 카드를 해제하고 게이지 +1
    from setup import megamis_of
    if "raira" in megamis_of(p) and p.taisen_cards and agent is not None:
        from cards import release_taisen
        # 재구성 시 버림패의 대전 카드는 어차피 패산으로 → 해제 (게이지 이득)
        for tc in list(p.taisen_cards):
            if hasattr(agent, "choose_yes_no") and \
                    not agent.choose_yes_no(state, pidx, "release_taisen_recon"):
                continue
            release_taisen(state, pidx, tc, gain_gauge=True, agent=agent)

    # 오보로 설치: 재구성 직전 덮음패 설치 카드 1장 사용 (9-7 전, 라이프보다 먼저)
    if agent is not None:
        from cards import legal_covered_setups, use_card_from_covered
        setups = legal_covered_setups(state, pidx)
        if setups and hasattr(agent, "choose_setup_use"):
            pick = agent.choose_setup_use(state, pidx, setups)
            if pick is not None and pick in state.players[pidx].covered:
                use_card_from_covered(state, pidx, pick, agent, rng)

    if by_rule:
        # i: 라이프 1 데미지 (라이프→더스트... 가 아니라 룰북 9-7은 "라이프에 1 데미지"
        # → 5-8-3-1에 따라 라이프 데미지는 라이프→플레어로 이동해야 하는지 확인 필요.
        # 9-7 원문: "자신의 라이프에 1 데미지를 준다" → 데미지 해결 규칙(5-8-3-1) 적용
        # → 라이프의 결정 1개를 같은 플레이어의 플레어로 이동.
        before = p.life
        dmg = min(1, p.life)
        p.life -= dmg
        p.flare += dmg
        # 라이프 0 즉시 판정 + 즉재기 훅
        from combat import _check_life
        _check_life(state)
        from cards import on_life_reduced
        on_life_reduced(state, pidx, before)

    # ii: 버림패 + 덮음패의 통상패 전부를 패산으로
    #     (v1은 통상패만 다루므로 covered/discard 전부가 통상패)
    p.deck.extend(p.discard)
    p.deck.extend(p.covered)
    p.discard.clear()
    p.covered.clear()

    if rng is not None:
        rng.shuffle(p.deck)
    else:
        random.shuffle(p.deck)

    state.check_conservation()


def can_reconstruct(state: GameState, pidx: int) -> bool:
    """재구성으로 패산에 카드를 채울 수 있는가 (버림패+덮음패에 카드 존재)."""
    p = state.players[pidx]
    return len(p.discard) + len(p.covered) > 0


def draw_card(state: GameState, pidx: int, rng: random.Random = None,
              agent=None) -> bool:
    """
    카드 1장 뽑기 (5-5).
    패산이 비었으면 뽑는 대신 초조(10-10)를 해결한다:
    오라 1 / 라이프 1 데미지 중 받는 플레이어가 선택.
    (재구성은 개시 페이즈에 선택으로만 수행 — 자동 재구성 없음)
    """
    p = state.players[pidx]
    if not p.deck:
        _resolve_impatience(state, pidx, agent)
        return True  # 초조로 해결됨 (카드는 뽑지 못함)
    p.hand.append(p.deck.pop())
    return True


def _resolve_impatience(state: GameState, pidx: int, agent) -> None:
    """초조 (10-10): 오라 1 / 라이프 1 데미지, 받는 쪽이 선택."""
    from combat import choose_and_apply_damage
    choose_and_apply_damage(state, 1 - pidx, pidx, 1, 1, agent)
