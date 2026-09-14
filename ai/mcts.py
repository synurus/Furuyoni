"""
결정화 몬테카를로 탐색 봇 (Phase 4-1)

각 합법 수에 대해:
  1) 결정화: 관측자(me)가 볼 수 없는 정보 — 상대 손패/패산/덮음패와
     내 패산의 순서 — 를 무작위 재배치한 평행세계를 만든다
  2) 그 수를 적용하고, 남은 내 메인 페이즈와 이후 턴들을
     빠른 정책 봇으로 H턴까지 굴린다 (rollout)
  3) 종결 시 승패, 미종결 시 휴리스틱 평가를 승률로 환산
  4) K회 평균 승률이 가장 높은 수를 선택

recommend(state, pidx): 추천 수 Top-N + 추정 승률 (알파고식 조언 기능)

다음 단계(4-2): 이 골격 위에 트리(UCT) 탐색을 얹는다.
"""

import math
import random
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from setup import CARD_DB
from cards import use_card, legal_card_uses
from tokens import legal_basic_actions, perform_basic_action
from turn import play_turn, main_phase, end_phase
from ai.agents import RandomBot, AggressiveBot
from ai.heuristic import HeuristicBot, evaluate

EVAL_SCALE = 12.0   # 평가점수 → 승률 변환 스케일 (라이프 1.2개 차 ≈ 73%)


def _to_winrate(score: float) -> float:
    x = max(-60.0, min(60.0, score / EVAL_SCALE))  # 오버플로 방지 (승리수 ±10000 대응)
    return 1.0 / (1.0 + math.exp(-x))


def determinize(state, me: int, rng: random.Random):
    """
    me가 볼 수 없는 정보를 재배치한 클론 반환.
    - 상대의 손패+패산+덮음패: 하나로 모아 섞고 같은 장수로 재분배
    - 내 패산: 순서만 섞기 (내용은 앎)
    (미사용/사용 비장패, 버림패, 부여패는 공개 정보)
    """
    sim = state.clone()
    opp = sim.players[1 - me]
    pool = list(opp.hand) + list(opp.deck) + list(opp.covered)
    rng.shuffle(pool)
    nh, nd = len(opp.hand), len(opp.deck)
    opp.hand = pool[:nh]
    opp.deck = pool[nh:nh + nd]
    opp.covered = pool[nh + nd:]
    rng.shuffle(sim.players[me].deck)
    return sim


class MonteCarloBot(HeuristicBot):
    """
    결정화 + 몬테카를로 롤아웃으로 메인 행동을 고르는 봇.
    대응/데미지 선택 등 미세 결정은 HeuristicBot의 규칙을 상속.
    """

    def __init__(self, seed: int = 0, rollouts: int = 24, horizon: int = 8,
                 prune_top: int = 4, blend: float = 0.55, **kw):
        super().__init__(seed=seed, **kw)
        self.K = rollouts       # 후보당 롤아웃 횟수
        self.H = horizon        # 롤아웃 절단 턴 수
        self.prune_top = prune_top  # 1수 평가로 남길 후보 수
        self.blend = blend      # 최종값 = blend*MC승률 + (1-blend)*1수평가승률

    # ── 핵심: 메인 행동 선택 ──
    def choose_main_action(self, state, pidx, legal, rng):
        scores = self.evaluate_moves(state, pidx, legal)
        best_move, best = max(scores, key=lambda x: x[1])
        return best_move

    def evaluate_moves(self, state, pidx, legal):
        """[(move, 추정승률)] 반환. recommend()와 공유하는 본체.

        1) 휴리스틱 1수 평가로 전체 후보 점수화 → 상위 prune_top개만 남김
        2) 남은 후보에 K회 결정화 롤아웃
        3) 최종값 = blend*MC + (1-blend)*1수평가 (탐색+정밀평가 결합)
        """
        # 1) 1수 평가 (전지적 1-ply — HeuristicBot 상속분 활용)
        h1 = []
        for move in legal:
            if move[0] == "end":
                score = evaluate(state, pidx)
            else:
                score = self._simulate(state, pidx, move)
            h1.append((move, _to_winrate(score)))
        h1.sort(key=lambda x: -x[1])
        pruned = h1[:self.prune_top]

        # 2) + 3)
        results = []
        for move, h1_wr in pruned:
            total = 0.0
            for k in range(self.K):
                rng = random.Random(self.rng.randrange(10**9))
                total += self._rollout_value(state, pidx, move, rng)
            mc_wr = total / self.K
            results.append((move, self.blend * mc_wr + (1 - self.blend) * h1_wr))
        # 가지치기로 잘린 수들도 (낮은 점수로) 반환 목록엔 포함
        kept = {id(m) for m, _ in results}
        for move, h1_wr in h1[self.prune_top:]:
            results.append((move, h1_wr * 0.5))
        return results

    def _rollout_value(self, state, pidx, move, rng) -> float:
        sim = determinize(state, pidx, rng)
        policy = AggressiveBot(end_prob=0.35, seed=rng.randrange(10**9))

        # 1) 후보 수 적용
        try:
            if move[0] == "basic":
                p = sim.players[pidx]
                if p.vigor >= 1:
                    p.vigor -= 1
                elif p.hand:
                    p.covered.append(p.hand.pop(0))
                else:
                    return 0.0
                perform_basic_action(sim, pidx, move[1])
            elif move[0] == "card":
                src, cid = move[1]
                ok = use_card(sim, pidx, src, cid, policy, rng)
                if not ok:
                    return 0.0  # 결정화 세계에서 사용 불가였던 수
            # move[0] == "end": 아무것도 안 하고 페이즈 종료로
        except Exception:
            return 0.0

        if sim.is_over():
            return self._outcome(sim, pidx)

        # 2) 남은 내 메인 페이즈 + 종료 페이즈를 정책 봇으로 마저 진행
        try:
            if move[0] != "end":
                main_phase(sim, rng, policy)
            if not sim.is_over():
                end_phase(sim, policy, rng)
            if not sim.is_over():
                sim.active = 1 - sim.active
                sim.turn_count += 1
        except Exception:
            return self._outcome_or_eval(sim, pidx)

        # 3) 이후 H턴 롤아웃
        for _ in range(self.H):
            if sim.is_over():
                break
            try:
                play_turn(sim, rng, policy)
            except Exception:
                break
        return self._outcome_or_eval(sim, pidx)

    def _outcome(self, sim, me) -> float:
        if sim.winner == me:
            return 1.0
        if sim.winner == 1 - me:
            return 0.0
        return 0.5

    def _outcome_or_eval(self, sim, me) -> float:
        if sim.is_over():
            return self._outcome(sim, me)
        return _to_winrate(evaluate(sim, me))


# ─── 알파고식 추천 API (Phase 4-3) ───
def recommend(state, pidx, n_top=3, rollouts=48, horizon=10, seed=0, legal=None):
    """
    현재 상태에서 pidx의 추천 수 Top-N.
    legal을 주면 그 목록만 평가 (CLI 훈수 모드용).
    반환: [(move, 설명 문자열, 추정 승률)] 내림차순.
    """
    bot = MonteCarloBot(seed=seed, rollouts=rollouts, horizon=horizon,
                        prune_top=5)
    if legal is None:
        legal = [("basic", a) for a in legal_basic_actions(state, pidx)]
        legal += [("card", x) for x in legal_card_uses(state, pidx, is_fullpower=False)]
        legal.append(("end", None))
    scored = bot.evaluate_moves(state, pidx, legal)
    scored.sort(key=lambda x: -x[1])

    from tokens import BASIC_ACTION_KR
    out = []
    for move, wr in scored[:n_top]:
        kind, arg = move
        if kind == "basic":
            desc = f"기본동작: {BASIC_ACTION_KR[arg]}"
        elif kind == "card":
            src, cid = arg
            where = "손패" if src == "hand" else "비장패"
            desc = f"카드 사용 [{where}]: {CARD_DB[cid]['name_ko']}"
        else:
            desc = "메인 페이즈 종료"
        out.append((move, desc, wr))
    return out


if __name__ == "__main__":
    # 데모: 초기 국면과 중반 국면의 추천 수
    from setup import new_game
    import time

    s = new_game("yurina", "tokoyo", seed=3, first=0)
    print("── 초기 국면 (유리나 시점, 간격 10) 추천 수 ──")
    t0 = time.time()
    for _, desc, wr in recommend(s, 0, rollouts=24):
        print(f"  승률 {wr*100:5.1f}% | {desc}")
    print(f"  ({time.time()-t0:.1f}초)")

    # 중반 국면: 근접전 상황을 인위 구성
    s2 = new_game("yurina", "tokoyo", seed=3, first=0)
    s2.dust += s2.distance - 3
    s2.distance = 3
    s2.players[0].hand = ["01-yurina-o-n-1", "01-yurina-o-n-2"]  # 참, 일섬
    s2.players[0].vigor = 2
    s2.check_conservation()
    print("── 중반 국면 (간격 3, 손패: 참·일섬) 추천 수 ──")
    t0 = time.time()
    for _, desc, wr in recommend(s2, 0, rollouts=24):
        print(f"  승률 {wr*100:5.1f}% | {desc}")
    print(f"  ({time.time()-t0:.1f}초)")
