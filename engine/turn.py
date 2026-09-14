"""
턴 진행 상태머신 (룰북 8장)

턴 = 개시 페이즈 → 메인 페이즈 → 종료 페이즈, 그 후 활성/비활성 교대.

이 커밋의 범위:
- 개시/종료 페이즈의 '기정 처리'를 룰대로 구현
- 메인 페이즈는 '행동을 받아 처리하는 골격'만. 실제 카드 사용/데미지는 다음 커밋.
- 승패 판정(라이프0 / 덱아웃)을 각 처리 후 검사.

【상시】/【전개중】 등 효과 해결 훅은 자리만 만들어두고 다음 커밋에서 채운다.
"""

import random
from state import GameState
from constants import VIGOR_MAX, HAND_LIMIT, EndReason
from deck import draw_card, reconstruct_deck, can_reconstruct


# 각 플레이어가 이미 '첫 턴'을 치렀는지 (개시 기정 처리 스킵 판단용)
# GameState에 실으면 clone도 자동 처리되므로 그쪽에 두는 게 맞지만,
# v1에서는 turn_count 기반으로 판단 (아래 참고).

def _is_first_turn_of(state: GameState, pidx: int) -> bool:
    """
    해당 플레이어의 첫 턴인가?
    선공(active 초기값) 플레이어의 첫 턴 = turn_count 1,
    후공 플레이어의 첫 턴 = turn_count 2.
    각 플레이어가 자신의 첫 턴에만 개시 기정 처리를 스킵한다(8-1-3).
    """
    return state.players[pidx]._had_first_turn is False


def gain_vigor(state: GameState, pidx: int, n: int = 1) -> None:
    """
    집중력 획득 (룰북 5-1-2, 5-1-4).
    위축 상태면 집중력 대신 위축 해제. 상한 2.
    """
    p = state.players[pidx]
    for _ in range(n):
        if p.withered:
            p.withered = False
        else:
            p.vigor = min(p.vigor + 1, VIGOR_MAX)


# ─────────────────────────────────────────────
# 개시 페이즈 (8-1)
# ─────────────────────────────────────────────
def start_phase(state: GameState, rng: random.Random,
                agent=None) -> bool:
    """
    개시 페이즈 수행. 덱아웃 등으로 게임이 끝나면 False 반환.
    agent: 선택이 필요한 부분(재구성 여부)을 결정할 주체. None이면 기본정책.
    """
    state.phase = "start"
    pidx = state.active
    p = state.players[pidx]

    # 8-1-1 턴 시작 시 간격 기록 (탈리야 간격±1 토큰 반영한 유효 간격)
    from combat import effective_distance
    state.turn_start_distance = effective_distance(state)
    state.attacks_this_phase = [0, 0]   # 원심: 개시 페이즈 공격 카운트 리셋

    # 탈리야: 본인 개시 페이즈에 간격±1 조화결정을 연소됨으로 회수 (9-2-1-1)
    #   (턴 시작 간격 기록 후 회수 — 원심 FAQ)
    if p.gap_minus_harmony or p.gap_plus_harmony:
        p.burned_harmony += p.gap_minus_harmony + p.gap_plus_harmony
        p.gap_minus_harmony = 0
        p.gap_plus_harmony = 0

    # 8-1-2 개시 시작 효과 해결 (다음 커밋: 【상시】/【전개중】 훅)
    # _resolve_start_effects(state, pidx)

    # 8-1-3 기정 처리 — 각 플레이어 첫 턴은 스킵
    if not p._had_first_turn:
        p._had_first_turn = True
        return True  # 첫 턴은 기정 처리 없이 메인으로

    # i: 집중력 1 획득
    gain_vigor(state, pidx, 1)

    # ii: 부여패에 연결된 결정 1개씩 더스트로 (파기 유발 가능)
    _tick_enhancements(state, pidx, agent, rng)
    if state.is_over():
        return False  # 파기시 효과(공격 등)로 게임이 끝났을 수 있음

    # iii: 재구성 1회 수행 가능 (선택)
    if can_reconstruct(state, pidx):
        do_recon = _decide_reconstruct(state, pidx, agent)
        if do_recon:
            reconstruct_deck(state, pidx, by_rule=True, rng=rng, agent=agent)
            if _check_end_after_life_change(state):
                return False

    # iv: 카드 2장 뽑기 (패산 부족분은 초조로 해결 — 1장씩 따로)
    for _ in range(2):
        draw_card(state, pidx, rng=rng, agent=agent)
        if state.is_over():
            return False

    return True


def _tick_enhancements(state: GameState, pidx: int, agent, rng) -> None:
    """
    개시 기정 ii: 부여패마다 결정 1개씩 더스트로.
    결정이 0이 된 부여패는 파기(9-5) — v1에서는 파기시 효과 처리는
    다음 커밋으로 미루고, 카드만 버림패로 이동.
    """
    from cards import destroy_enhancement, _has_custom
    from setup import CARD_DB
    p = state.players[pidx]
    to_destroy = []
    for enh in list(p.enhancements):
        card = CARD_DB[enh.card_id]
        # 서리 가시덤불: 상대 동결 시 이 카드 결정을 이동 안 해도 됨 (틱 스킵)
        if _has_custom(card, "seori_no_tick_if_frozen") \
                and state.players[1 - pidx].frozen >= 1:
            continue
        if enh.tokens > 0:
            enh.tokens -= 1
            if _has_custom(card, "kwonyeok_redirect"):
                state.distance += 1
            else:
                state.dust += 1
        elif enh.seeds > 0:
            # 벚꽃결정이 없고 씨앗만 남음: 씨앗은 벚꽃결정 간주지만
            # 토양으로 돌아감 (17-2, 부여패에서 벗어나면 발아 안 한 상태로)
            enh.seeds -= 1
            p.soil_unsprouted += 1
        # 벚꽃결정도 씨앗도 0이 된 부여패만 파기
        if enh.tokens == 0 and enh.seeds == 0:
            to_destroy.append(enh)
    state.check_conservation()
    # 결정 0이 된 부여패 파기 (9-5: 파기시 효과 해결 포함)
    for enh in to_destroy:
        destroy_enhancement(state, pidx, enh, agent, rng)
        if state.is_over():
            return


def _decide_reconstruct(state, pidx, agent) -> bool:
    """개시 재구성 여부 결정. 기본정책: 패산이 비었을 때만 재구성."""
    if agent is not None and hasattr(agent, "decide_reconstruct"):
        return agent.decide_reconstruct(state, pidx)
    return len(state.players[pidx].deck) == 0


# ─────────────────────────────────────────────
# 메인 페이즈 (8-2) — 골격만
# ─────────────────────────────────────────────
def main_phase(state: GameState, rng: random.Random, agent) -> bool:
    """
    메인 페이즈 (8-2). 커밋 b: 표준행동/전력행동 + 기본동작 비용.

    표준행동: {카드 사용, 기본동작(비용: 집중1 또는 손패1 덮기), 종료} 반복
    전력행동: 카드 1장 사용 후 즉시 종료 (《전력》 카드는 이 방식만)
    종단 잠금: 종단 카드 사용 후 카드/기본동작 불가
    """
    from tokens import legal_basic_actions, perform_basic_action
    from cards import legal_card_uses, use_card

    state.phase = "main"
    pidx = state.active
    p = state.players[pidx]
    state.attacks_this_phase = [0, 0]   # 원심: 메인 페이즈 공격 카운트 리셋

    # 8-2-1: 표준/전력 선택
    fp_cards = legal_card_uses(state, pidx, is_fullpower=True)
    from setup import CARD_DB as _DBF
    has_fullpower_option = any(
        _is_fullpower(cid)
        or "fullpower_option" in (_DBF[cid].get("card_flags") or [])
        for _, cid in fp_cards)
    mode = "standard"
    if has_fullpower_option and hasattr(agent, "choose_action_mode"):
        mode = agent.choose_action_mode(state, pidx)  # 'standard' | 'fullpower'

    if mode == "fullpower":
        # 전력행동: 카드 1장 사용 후 종료
        legal = [("card", x) for x in legal_card_uses(state, pidx, True)]
        legal.append(("end", None))
        kind, arg = agent.choose_main_action(state, pidx, legal, rng)
        if kind == "card":
            use_card(state, pidx, arg[0], arg[1], agent, rng,
                     as_fullpower=True)
        return True

    # 표준행동 루프
    max_actions = 300
    for _ in range(max_actions):
        if state.is_over():
            return True
        if state.terminal_lock[pidx]:
            break  # 종단: 사용한 본인은 카드/기본동작 불가

        legal = []
        # 기본동작: 비용(집중1 or 손패1 덮기) 지불 가능해야
        from setup import CARD_DB as _DB2
        can_pay_basic = p.vigor >= 1 or \
            any(_DB2[c].get("base_type") != "poison" for c in p.hand)
        if can_pay_basic:
            for a in legal_basic_actions(state, pidx):
                legal.append(("basic", a))
        for x in legal_card_uses(state, pidx, is_fullpower=False):
            legal.append(("card", x))
        # 라이라 대전 해제 (10-4): 대전 카드가 있으면 표준행동 선택지로
        from setup import megamis_of
        if "raira" in megamis_of(p) and p.taisen_cards:
            for tc in p.taisen_cards:
                legal.append(("release_taisen", tc))
        legal.append(("end", None))

        kind, arg = agent.choose_main_action(state, pidx, legal, rng)
        if kind == "end":
            break
        elif kind == "release_taisen":
            from cards import release_taisen
            release_taisen(state, pidx, arg, gain_gauge=True, agent=agent)
            state.check_conservation()
        elif kind == "basic":
            _pay_basic_action_cost(state, pidx, agent)
            perform_basic_action(state, pidx, arg)
            state.check_conservation()
        elif kind == "card":
            use_card(state, pidx, arg[0], arg[1], agent, rng)
            state.check_conservation()
            if state.is_over():
                return True
    return True


def _is_fullpower(card_id: str) -> bool:
    from setup import CARD_DB
    return CARD_DB[card_id].get("subtype") == "fullpower"


def _pay_basic_action_cost(state: GameState, pidx: int, agent) -> None:
    """기본동작 비용 (9-6 ii): 집중력 1 또는 손패 1장 덮기."""
    p = state.players[pidx]
    use_vigor = p.vigor >= 1
    if p.vigor >= 1 and len(p.hand) >= 1 and hasattr(agent, "choose_basic_cost"):
        use_vigor = agent.choose_basic_cost(state, pidx) == "vigor"
    if use_vigor and p.vigor >= 1:
        p.vigor -= 1
    else:
        from setup import CARD_DB as _DB
        coverable = [c for c in p.hand if _DB[c].get("base_type") != "poison"]
        idx_in_cov = agent.choose_card_to_cover(state, pidx, coverable) \
            if hasattr(agent, "choose_card_to_cover") else 0
        p.covered.append(p.hand.pop(p.hand.index(coverable[idx_in_cov])))


# ─────────────────────────────────────────────
# 종료 페이즈 (8-3)
# ─────────────────────────────────────────────
def end_phase(state: GameState, agent, rng: random.Random = None) -> bool:
    """
    종료 페이즈. 손패 상한(2) 초과분을 덮음패로 정리 (8-3-2).
    신라: 계략이 미준비면 다음 계략 준비 (5-3).
    """
    state.phase = "end"
    pidx = state.active
    p = state.players[pidx]

    # 8-3-1 종료 효과: 리로딩 (전개중: 손패 0장이면 1장 뽑아도 된다)
    from combat import _has_deployed_custom
    if _has_deployed_custom(state, pidx, "reloading_draw") and len(p.hand) == 0:
        want = agent.choose_yes_no(state, pidx, "reloading_draw") \
            if hasattr(agent, "choose_yes_no") else True
        if want:
            from deck import draw_card
            draw_card(state, pidx, rng=rng, agent=agent)

    # 8-3-1b 재기 판정 (10-7): 사용한 비장패의 재기 조건 확인
    from cards import check_saiki_end_phase
    check_saiki_end_phase(state, pidx)

    # 8-3-2 손패 상한 정리
    from setup import CARD_DB as _DB
    while len(p.hand) > HAND_LIMIT:
        coverable = [c for c in p.hand if _DB[c].get("base_type") != "poison"]
        if not coverable:
            break   # 독은 덮을 수 없음 → 상한 초과여도 유지 (나무위키 FAQ)
        idx_in_cov = agent.choose_card_to_cover(state, pidx, coverable) \
            if hasattr(agent, "choose_card_to_cover") else 0
        idx = p.hand.index(coverable[idx_in_cov])
        card = p.hand.pop(idx)
        p.covered.append(card)

    return True


# ─────────────────────────────────────────────
# 턴 1회 수행
# ─────────────────────────────────────────────
def play_turn(state: GameState, rng: random.Random, agent) -> bool:
    """한 턴 전체 수행. 게임이 끝나면 False."""
    # 턴 단위 추적 리셋
    state.cards_used_this_turn = [0, 0]
    state.attacks_this_turn = [0, 0]
    state.terminal_lock = [False, False]
    state.pending_buffs = [[], []]  # this_turn 스코프 버프는 턴 종료 시 소멸
    state._hagane_used_centrifugal = {}
    state._oboro_opp_took_aura_dmg = {}
    state.basic_actions_this_turn = [0, 0]
    state._no_advance_this_turn = {}
    state._ggeopjil_growth = {}

    if not start_phase(state, rng, agent):
        return False
    if state.is_over():
        return False
    if not main_phase(state, rng, agent):
        return False
    if state.is_over():
        return False
    if not end_phase(state, agent, rng):
        return False
    if state.is_over():
        return False

    # 턴 종료: 활성/비활성 교대
    state.active = 1 - state.active
    state.turn_count += 1
    return True


# ─────────────────────────────────────────────
# 승패 판정 유틸
# ─────────────────────────────────────────────
def _check_end_after_life_change(state: GameState) -> bool:
    """라이프 0 검사 (룰북 4-2). 이미 끝난 게임은 재판정하지 않는다."""
    if state.is_over():
        return True
    p0, p1 = state.players
    z0, z1 = p0.life <= 0, p1.life <= 0
    if z0 and z1:
        _end_game(state, winner=-1, reason=EndReason.DRAW)
        return True
    if z0:
        _end_game(state, winner=1, reason=EndReason.LIFE_ZERO)
        return True
    if z1:
        _end_game(state, winner=0, reason=EndReason.LIFE_ZERO)
        return True
    return False


def _end_game(state: GameState, winner: int, reason: str) -> None:
    if state.is_over():
        return  # 결과 덮어쓰기 방지
    state.phase = "over"
    state.winner = winner
    state.end_reason = reason
