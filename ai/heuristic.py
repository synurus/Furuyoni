"""
휴리스틱 봇 (Phase 3-1)

- evaluate(): 상태 평가 함수
- HeuristicBot: 합법 수마다 복제 상태에서 1수 시뮬레이션 후 최고점 선택

v1 한계 (Phase 4에서 해소 예정):
- 전지적 관점 (상대 손패/패산을 봄) — determinization 전까지 공통
- 시뮬레이션에서 상대의 대응을 모델링하지 않음 (낙관적 1수 탐색)
"""

import random
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))

from setup import CARD_DB
from cards import legal_card_uses, use_card, card_cost
from combat import parse_range, effective_aura
from tokens import legal_basic_actions, perform_basic_action
from ai.agents import RandomBot


# ─── 평가 함수 ───
W_LIFE, W_AURA, W_FLARE = 10.0, 2.0, 1.2
W_HAND, W_VIGOR, W_ENH = 0.5, 0.3, 0.3
W_MY_RANGE, W_OPP_RANGE = 0.8, 0.8
WIN = 10_000.0


def _lethal_and_pressure(state, me: int) -> float:
    """
    고도화 항: 처치 임박, 덱아웃 압박, 라이프 위험도(비선형), 자원 임계.
    me 관점의 추가 점수 (기존 선형 항 위에 얹음).
    """
    from cards import card_range_str, card_damage_str
    from combat import parse_range, parse_damage, effective_distance
    p = state.players[me]
    o = state.players[1 - me]
    d = effective_distance(state)
    s = 0.0

    # ① 비선형 라이프 위험도: 라이프가 낮을수록 1점의 가치가 급증
    #    (라이프 10→9는 덜 아프지만 2→1은 치명적)
    def life_danger(life):
        # 라이프가 낮을수록 큰 음수 (남은 라이프의 볼록 함수)
        return -(max(0, 10 - life) ** 1.5) * 0.15
    s += life_danger(p.life)          # 내 위험 (음수)
    s -= life_danger(o.life)          # 상대 위험 (상대가 위험하면 나에게 +)

    # ② 이번 턴 처치 임박: 상대 오라 0이고 명중 라이프뎀 합이 상대 라이프 이상
    if o.aura == 0 and o.life > 0:
        life_dmg_sum = 0
        for cid in p.hand + p.specials:
            c = CARD_DB[cid]
            if c["type"] != "attack":
                continue
            if c.get("base_type") == "special":
                cost = c.get("cost")
                if p.flare < (cost if isinstance(cost, int) else 99):
                    continue
            if d in parse_range(card_range_str(state, me, c)):
                try:
                    _, l = parse_damage(card_damage_str(state, me, c))
                    life_dmg_sum += l if isinstance(l, int) else 0
                except Exception:
                    pass
        if life_dmg_sum >= o.life:
            s += 8.0    # 처치 사정권 = 큰 우위
        elif life_dmg_sum > 0:
            s += life_dmg_sum * 0.5

    # ③ 덱아웃 압박: 상대 패산이 적으면 재구성 데미지 임박
    #    (패산 0에서 뽑으면 초조, 재구성 시 라이프 1 손실)
    opp_deck = len(o.deck)
    if opp_deck <= 1:
        s += 1.5
    elif opp_deck <= 3:
        s += 0.5
    # 내 덱아웃 압박 (대칭)
    my_deck = len(p.deck)
    if my_deck <= 1:
        s -= 1.5
    elif my_deck <= 3:
        s -= 0.5

    # ④ 플레어 임계: 비장패 사용 가능선(가진 비장의 최소 소모)에 도달하면 가치
    my_specials = [CARD_DB[c] for c in p.specials]
    if my_specials:
        min_cost = min((c.get("cost") if isinstance(c.get("cost"), int) else 99)
                       for c in my_specials)
        if min_cost < 99:
            if p.flare >= min_cost:
                s += 1.0    # 비장 사용 준비 완료
            elif p.flare == min_cost - 1:
                s += 0.4    # 한 끗 앞

    return s


# 고도화 항 사용 여부 (A/B 결과: 중립. 기본 OFF로 계산비용 절약)
# 실측(2026-07-08): 처치임박/덱아웃/비선형라이프/간격제어/가중치조정 모두
#   vs 기존 45~51%로 중립~약간 열세. 기존 평가가 이미 견고해 단순 추가항으론
#   개선 안 됨. 항 자체는 보존(연구용), 기본은 끔.
USE_ADVANCED_EVAL = False


def _attacks_in_range(state, pidx) -> int:
    """현재 (유효) 간격에서 적정거리인 사용 가능 공격 카드 수. 우산 스펙 반영."""
    from combat import effective_distance
    from cards import card_range_str
    p = state.players[pidx]
    d = effective_distance(state)
    n = 0
    for src, pool in (("hand", p.hand), ("special", p.specials)):
        for cid in pool:
            c = CARD_DB[cid]
            if c["type"] != "attack":
                continue
            if src == "special" and p.flare < card_cost(cid):
                continue
            if d in parse_range(card_range_str(state, pidx, c)):
                n += 1
    return n


# 신규 자원 가중치 (v2). 게이지/조화결정/씨앗 등은 잠재적 우위.
W_GAUGE = 0.15      # 라이라 풍신+뇌신 게이지 (임계 4/12에서 가치 상승)
W_HARMONY = 0.25    # 탈리야 머신 조화결정 (연소/기동 자원)
W_SEED = 0.4        # 메구미 발아 씨앗 (봉납/생육 자원)
W_FROZEN = 1.5      # 코르누: 상대 동결은 오라 압박
W_TAISEN = 0.6      # 라이라 대전 카드 (해제 대기 = 게이지 예약)


def _resource_score(state, pidx) -> float:
    """v2 여신별 신규 자원의 잠재 가치 (me 관점, 상대 것은 호출측에서 차감)."""
    from setup import megamis_of
    p = state.players[pidx]
    megs = megamis_of(p)
    s = 0.0
    if "raira" in megs:
        # 게이지: 합에 비례하되 임계(4/12) 근처에서 추가 가치
        gsum = p.fuujin + p.raijin
        s += W_GAUGE * gsum
        if p.raijin >= 4:
            s += 1.0   # 뇌라풍신조 상시 활성
        s += W_TAISEN * len(p.taisen_cards)
    if "thallya" in megs:
        s += W_HARMONY * p.machine_harmony
    if "megumi" in megs:
        s += W_SEED * p.soil_sprouted
        s += 0.2 * sum(e.seeds for e in p.enhancements)
    if "korunu" in megs:
        # 상대 동결은 상대 평가에서 처리 (여기선 내가 건 동결 = 상대 오라 압박)
        s += W_FROZEN * state.players[1 - pidx].frozen
    return s


def evaluate(state, me: int) -> float:
    """me 관점의 상태 점수. 클수록 유리."""
    if state.is_over():
        if state.winner == me:
            return WIN
        if state.winner == 1 - me:
            return -WIN
        return 0.0  # 무승부
    p = state.players[me]
    o = state.players[1 - me]
    s = W_LIFE * (p.life - o.life)
    s += W_AURA * (p.aura - o.aura)
    s += W_FLARE * (p.flare - o.flare)
    s += W_HAND * (len(p.hand) - len(o.hand))
    s += W_VIGOR * (p.vigor - o.vigor)
    s += W_ENH * (sum(e.tokens for e in p.enhancements)
                  - sum(e.tokens for e in o.enhancements))
    s += W_MY_RANGE * _attacks_in_range(state, me)
    s -= W_OPP_RANGE * _attacks_in_range(state, 1 - me)
    # v2 신규 자원 (2여신 대응)
    s += _resource_score(state, me)
    s -= _resource_score(state, 1 - me)
    # 고도화 항 (처치 임박/덱아웃/비선형 라이프/플레어 임계)
    if USE_ADVANCED_EVAL:
        s += _lethal_and_pressure(state, me)
    return s


class _SimAgent(RandomBot):
    """시뮬레이션 내부용: 상대는 대응하지 않고, 모든 선택은 기본정책."""
    def choose_reaction(self, state, pidx, options, atk):
        return None


class HeuristicBot(RandomBot):
    """1수 앞 그리디 봇."""

    def __init__(self, seed: int = 0, **kw):
        super().__init__(seed=seed, **kw)
        self._sim = _SimAgent(seed=seed)

    # ── 메인: 수마다 시뮬레이션 후 최고점 ──
    def choose_main_action(self, state, pidx, legal, rng):
        best, best_score = ("end", None), evaluate(state, pidx)  # 기준: 지금 멈추기
        for move in legal:
            if move[0] == "end":
                continue
            score = self._simulate(state, pidx, move)
            if score > best_score + 1e-9:
                best, best_score = move, score
        return best

    def _simulate(self, state, pidx, move) -> float:
        sim = state.clone()
        sim_rng = random.Random(0)
        try:
            if move[0] == "basic":
                # 비용 지불 (집중 우선, 없으면 손패 덮기) 후 수행 — main_phase와 동일
                p = sim.players[pidx]
                if p.vigor >= 1:
                    p.vigor -= 1
                elif p.hand:
                    p.covered.append(p.hand.pop(0))
                else:
                    return -WIN  # 비용 불가 (실제로는 legal에서 걸러짐)
                perform_basic_action(sim, pidx, move[1])
            elif move[0] == "card":
                src, cid = move[1]
                use_card(sim, pidx, src, cid, self._sim, sim_rng)
            elif move[0] == "release_taisen":
                from cards import release_taisen
                release_taisen(sim, pidx, move[1], gain_gauge=True,
                               agent=self._sim)
        except Exception:
            return -WIN  # 시뮬레이션 오류 수는 회피
        return evaluate(sim, pidx)

    # ── 전력행동: 전력 카드 사용이 표준 최선수보다 나으면 선택 ──
    def choose_action_mode(self, state, pidx):
        fp_moves = [("card", x) for x in legal_card_uses(state, pidx, True)
                    if CARD_DB[x[1]].get("subtype") == "fullpower"]
        if not fp_moves:
            return "standard"
        std_moves = [("card", x) for x in legal_card_uses(state, pidx, False)]
        std_moves += [("basic", a) for a in legal_basic_actions(state, pidx)]
        base = evaluate(state, pidx)
        best_fp = max((self._simulate(state, pidx, m) for m in fp_moves),
                      default=-WIN)
        best_std = max((self._simulate(state, pidx, m) for m in std_moves),
                       default=base)
        return "fullpower" if best_fp > max(best_std, base) else "standard"

    # ── 대응: 규칙 기반 (시뮬레이션은 공격 재현이 필요해 v1 제외) ──
    def choose_reaction(self, state, pidx, options, atk):
        threat = (atk.life or 0) * 2 + (atk.aura or 0)
        if threat < 2:
            return None
        # 우선순위: 무효화 가능 카드 > 공격형 대응 > 기타
        def prio(opt):
            cid = opt[1]
            fx = CARD_DB[cid].get("effects") or []
            cancels = any(op["op"] == "cancel_reacted_attack"
                          for f in fx for op in f["ops"])
            is_attack = CARD_DB[cid]["type"] == "attack"
            return (2 if cancels else 0) + (1 if is_attack else 0)
        best = max(options, key=prio)
        return best

    # ── 데미지: 라이프 여유 기반 ──
    def choose_damage_type(self, state, target_idx, aura_dmg, life_dmg):
        p = state.players[target_idx]
        if effective_aura(state, target_idx) >= aura_dmg:
            # 오라로 막을 수 있으면 원칙적으로 오라. 단 라이프 넉넉하고
            # 오라뎀이 커서 오라가 전멸하면(다음 공격에 취약) 소액 라이프 흡수 고려
            if life_dmg <= 1 and p.life >= 8 and aura_dmg >= p.aura:
                return "life"
            return "aura"
        return "life"

    def decide_reconstruct(self, state, pidx):
        p = state.players[pidx]
        # 패산이 비었으면 재구성 (초조 2회보다 재구성 1뎀이 보통 이득)
        return len(p.deck) == 0 and (len(p.discard) + len(p.covered)) >= 2


class LookaheadBot(HeuristicBot):
    """
    2수 앞 미니맥스 봇 (실험체 — 챔피언 아님).

    ⚠️ 실측(2026-07-08): vs 휴리스틱 32%로 오히려 열세.
    원인: 후루요니의 턴은 가변 길이(기본동작+카드 여러 장)인데, 이 근사는
    "내 턴이 한 수로 끝난다"고 가정해 상대 반응을 너무 일찍 계산 → 왜곡.
    → 얕은 미니맥스는 이 게임 구조와 맞지 않음. 챔피언은 MonteCarloBot(K=48).
    연구/비교 목적으로만 보존.

    각 내 수에 대해, 상대가 최선(1수 그리디)으로 반응했을 때의 점수로 평가한다.
    """

    def __init__(self, seed: int = 0, opp_top: int = 4, **kw):
        super().__init__(seed=seed, **kw)
        self.opp_top = opp_top   # 상대 반응 후보 상위 몇 개까지 볼지 (속도 조절)

    def choose_main_action(self, state, pidx, legal, rng):
        base = evaluate(state, pidx)
        best, best_score = ("end", None), base
        for move in legal:
            if move[0] == "end":
                continue
            score = self._simulate_2ply(state, pidx, move)
            if score > best_score + 1e-9:
                best, best_score = move, score
        return best

    def _simulate_2ply(self, state, pidx, move) -> float:
        """내 수를 둔 뒤, 상대 최선 반응까지 고려한 점수 (내 관점)."""
        sim = state.clone()
        sim_rng = random.Random(0)
        try:
            self._apply_move(sim, pidx, move, sim_rng)
        except Exception:
            return -WIN
        if sim.is_over():
            return evaluate(sim, pidx)
        # 내 턴이 안 끝났으면(추가 행동 여지) 즉시 평가로 근사
        # 상대 턴으로 넘어간다고 가정하고, 상대의 1수 그리디 반응을 시뮬레이션
        opp = 1 - pidx
        # 상대 턴 진행: 상대가 둘 수 있는 최선의 한 수를 찾아 그 결과로 평가
        opp_legal = _legal_moves_for(sim, opp)
        if not opp_legal:
            return evaluate(sim, pidx)
        # 상대는 자기 관점 점수를 최대화 = 내 관점 점수를 최소화
        worst_for_me = evaluate(sim, pidx)  # 상대가 아무것도 안 할 때
        cnt = 0
        for omove in opp_legal:
            if omove[0] == "end":
                continue
            osim = sim.clone()
            try:
                self._apply_move(osim, opp, omove, random.Random(0))
            except Exception:
                continue
            v = evaluate(osim, pidx)   # 내 관점
            if v < worst_for_me:
                worst_for_me = v
            cnt += 1
            if cnt >= self.opp_top:
                break
        return worst_for_me

    def _apply_move(self, sim, pidx, move, sim_rng):
        """한 수를 시뮬레이션 상태에 적용 (기본/카드/대전해제)."""
        if move[0] == "basic":
            p = sim.players[pidx]
            if p.vigor >= 1:
                p.vigor -= 1
            elif p.hand:
                p.covered.append(p.hand.pop(0))
            else:
                raise ValueError("basic 비용 불가")
            perform_basic_action(sim, pidx, move[1])
        elif move[0] == "card":
            src, cid = move[1]
            use_card(sim, pidx, src, cid, self._sim, sim_rng)
        elif move[0] == "release_taisen":
            from cards import release_taisen
            release_taisen(sim, pidx, move[1], gain_gauge=True, agent=self._sim)


def _legal_moves_for(state, pidx):
    """pidx가 지금 둘 수 있는 주요 수 목록 (2수 앞 상대 반응용)."""
    from cards import legal_card_uses
    from tokens import legal_basic_actions
    moves = [("card", x) for x in legal_card_uses(state, pidx, False)]
    moves += [("basic", a) for a in legal_basic_actions(state, pidx)]
    return moves
