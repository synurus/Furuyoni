"""
UCT 트리 탐색 봇 (Phase 4-2, ISMCTS-lite)

- 단일 공유 트리: 노드 키 = 루트부터의 행동 경로
- 매 반복 결정화된 평행세계를 새로 샘플링, 그 세계에서 합법인 행동만 선택/확장
- 상대의 메인 결정도 트리 노드 (UCB가 상대 관점에선 내 승률 최소화 방향)
- 리프에서 절단 롤아웃 (mcts.py 재사용)

트리 내 단순화:
- 전력행동은 트리 밖 (HeuristicBot의 모드 판단 상속)
- 대응/데미지 등 미세 결정은 정책(휴리스틱 규칙)으로 처리
- 개시 페이즈 재구성 결정도 정책 처리
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
from turn import main_phase, end_phase, start_phase, play_turn
from ai.agents import AggressiveBot
from ai.heuristic import HeuristicBot, evaluate
from ai.mcts import determinize, _to_winrate


def _akey(move):
    kind, arg = move
    if kind == "card":
        return ("card", arg[0], arg[1])
    if kind == "basic":
        return ("basic", arg)
    return ("end",)


def _from_akey(key):
    if key[0] == "card":
        return ("card", (key[1], key[2]))
    if key[0] == "basic":
        return ("basic", key[1])
    return ("end", None)


def _legal_now(state, pidx):
    """main_phase와 동일한 기준의 합법 수 (트리용, 전력 제외)."""
    if state.terminal_lock[pidx]:
        return [("end", None)]
    p = state.players[pidx]
    legal = []
    from setup import CARD_DB as _DB
    if p.vigor >= 1 or any(_DB[c].get("base_type") != "poison" for c in p.hand):
        for a in legal_basic_actions(state, pidx):
            legal.append(("basic", a))
    for x in legal_card_uses(state, pidx, is_fullpower=False):
        legal.append(("card", x))
    legal.append(("end", None))
    return legal


def _apply(state, pidx, move, policy, rng):
    """
    move 적용 후 다음 결정 지점까지 전진.
    반환: (next_pidx or None(종결), next_legal)
    """
    kind, arg = move
    if kind == "basic":
        p = state.players[pidx]
        if p.vigor >= 1:
            p.vigor -= 1
        elif p.hand:
            p.covered.append(p.hand.pop(0))
        else:
            return None, []
        perform_basic_action(state, pidx, arg)
    elif kind == "card":
        use_card(state, pidx, arg[0], arg[1], policy, rng)
    if state.is_over():
        return None, []

    if kind != "end":
        return pidx, _legal_now(state, pidx)

    # end: 종료 페이즈 → 턴 교대 → 상대 개시 페이즈까지 진행
    end_phase(state, policy, rng)
    if state.is_over():
        return None, []
    state.active = 1 - state.active
    state.turn_count += 1
    # play_turn의 턴 리셋 재현
    state.cards_used_this_turn = [0, 0]
    state.attacks_this_turn = [0, 0]
    state.terminal_lock = [False, False]
    state.pending_buffs = [[], []]
    ok = start_phase(state, rng, policy)
    if not ok or state.is_over():
        return None, []
    npidx = state.active
    return npidx, _legal_now(state, npidx)


class UCTBot(HeuristicBot):
    def __init__(self, seed: int = 0, iters: int = 160, horizon: int = 8,
                 c_uct: float = 0.9, prior_visits: int = 3,
                 root_top: int = 5, leaf_eval: bool = False, **kw):
        super().__init__(seed=seed, **kw)
        self.iters = iters
        self.H = horizon
        self.c = c_uct
        self.n0 = prior_visits   # 사전확률 가상 방문 수 (휴리스틱 1수 평가 주입)
        self.root_top = root_top # 루트 후보 가지치기
        self.leaf_eval = leaf_eval  # True면 롤아웃 없이 리프에서 휴리스틱 평가만

    def choose_main_action(self, state, pidx, legal, rng):
        if len(legal) <= 1:
            return legal[0]
        edge_stats = self._search(state, pidx)
        # 방문 수 최대 행동 (루트 합법수 한정)
        legal_keys = {_akey(m) for m in legal}
        best_key, best_n = None, -1
        for key, (n, w) in edge_stats.items():
            if key in legal_keys and n > best_n:
                best_key, best_n = key, n
        return _from_akey(best_key) if best_key else ("end", None)

    def _search(self, state, me):
        # tree[path] = {"player": pidx, "edges": {akey: [n, w]}}
        tree = {(): {"player": me, "edges": {}}}

        # 루트 가지치기: 휴리스틱 1수 평가 상위 root_top개만 루트 후보로
        root_legal = _legal_now(state, me)
        scored = []
        for mv in root_legal:
            s1 = evaluate(state, me) if mv[0] == "end" \
                else self._simulate(state, me, mv)
            scored.append((mv, s1))
        scored.sort(key=lambda x: -x[1])
        self._root_allowed = {_akey(m) for m, _ in scored[:self.root_top]}
        self._prior = {_akey(m): _to_winrate(s) for m, s in scored}
        for it in range(self.iters):
            rng = random.Random(self.rng.randrange(10**9))
            world = determinize(state, me, rng)
            policy = AggressiveBot(end_prob=0.35, seed=rng.randrange(10**9))

            path, node_key = [], ()
            cur_pidx = me
            cur_legal = _legal_now(world, me)
            value = None

            # 선택 + 확장
            for depth in range(24):
                node = tree[node_key]
                legal_keys = [_akey(m) for m in cur_legal]
                if node_key == ():   # 루트: 가지치기 적용
                    legal_keys = [k for k in legal_keys
                                  if k in self._root_allowed]
                untried = [k for k in legal_keys if k not in node["edges"]]
                if untried:
                    key = rng.choice(untried)          # 확장
                    if node_key == ():
                        # prior는 정보가 있는 루트에만 (내부 0.5 주입은 신호 희석 — 실측)
                        pw = self._prior.get(key, 0.5)
                        node["edges"][key] = [self.n0, self.n0 * pw]
                    else:
                        node["edges"][key] = [0, 0.0]
                    path.append((node_key, key))
                    cur_pidx, cur_legal = _apply(
                        world, cur_pidx, _from_akey(key), policy, rng)
                    child_key = node_key + (key,)
                    if cur_pidx is not None:
                        tree[child_key] = {"player": cur_pidx, "edges": {}}
                        value = self._rollout(world, me, cur_pidx, policy, rng)
                    else:
                        value = self._terminal_value(world, me)
                    break
                # UCB 선택
                total_n = sum(n for n, _ in
                              (node["edges"][k] for k in legal_keys)) or 1
                is_me = (node["player"] == me)
                best_k, best_u = None, -1e18
                for k in legal_keys:
                    n, w = node["edges"][k]
                    q = (w / n) if n else 0.5
                    if not is_me:
                        q = 1.0 - q
                    u = q + self.c * math.sqrt(math.log(total_n + 1) / (n + 1e-9))
                    if u > best_u:
                        best_k, best_u = k, u
                path.append((node_key, best_k))
                cur_pidx, cur_legal = _apply(
                    world, cur_pidx, _from_akey(best_k), policy, rng)
                node_key = node_key + (best_k,)
                if cur_pidx is None:
                    value = self._terminal_value(world, me)
                    break
                if node_key not in tree:
                    tree[node_key] = {"player": cur_pidx, "edges": {}}
                    value = self._rollout(world, me, cur_pidx, policy, rng)
                    break
            if value is None:
                value = self._rollout(world, me, cur_pidx, policy, rng)

            # 역전파
            for nk, key in path:
                st = tree[nk]["edges"][key]
                st[0] += 1
                st[1] += value

        return tree[()]["edges"]

    def _terminal_value(self, world, me):
        if world.winner == me:
            return 1.0
        if world.winner == 1 - me:
            return 0.0
        return 0.5

    def _rollout(self, world, me, cur_pidx, policy, rng):
        """결정 지점(cur_pidx의 메인)에서 절단 롤아웃.
        leaf_eval=True면 무작위 플레이 없이 현 상태를 휴리스틱 평가만 (노이즈 제거).
        """
        if self.leaf_eval:
            # 리프에서 현재 국면을 바로 평가 (미니맥스가 결정론적 값 위에서 동작)
            if world.is_over():
                return self._terminal_value(world, me)
            return _to_winrate(evaluate(world, me))
        try:
            main_phase(world, rng, policy)
            if not world.is_over():
                end_phase(world, policy, rng)
            if not world.is_over():
                world.active = 1 - world.active
                world.turn_count += 1
            for _ in range(self.H):
                if world.is_over():
                    break
                play_turn(world, rng, policy)
        except Exception:
            pass
        if world.is_over():
            return self._terminal_value(world, me)
        return _to_winrate(evaluate(world, me))


if __name__ == "__main__":
    from setup import new_game
    import time
    s = new_game("yurina", "tokoyo", seed=3, first=0)
    s.dust += s.distance - 3
    s.distance = 3
    s.players[0].hand = ["01-yurina-o-n-1", "01-yurina-o-n-2"]
    s.players[0].vigor = 2
    s.check_conservation()
    bot = UCTBot(seed=1, iters=200)
    t0 = time.time()
    stats = bot._search(s, 0)
    print(f"탐색 {time.time()-t0:.2f}초, 루트 엣지:")
    for k, (n, w) in sorted(stats.items(), key=lambda x: -x[1][0]):
        print(f"  방문{n:4d} 승률{w/max(n,1)*100:5.1f}% | {k}")
