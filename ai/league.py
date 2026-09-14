"""
자동 리그전 (Phase 3-2)

봇 종류 × 여신 조합 라운드로빈으로 대전시켜 승률 표를 만들고,
기보(게임 요약 + 수 기록)를 JSONL로 저장한다.

실행: python3 ai/league.py [--games N] [--out records.jsonl]
"""

import argparse
import json
import random
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from setup import new_game, CORE4, CARD_DB
from turn import play_turn
from ai.agents import RandomBot, AggressiveBot
from ai.heuristic import HeuristicBot
from ai.mcts import MonteCarloBot
from ai.uct import UCTBot


class Recorder:
    """에이전트를 감싸 주요 선택(메인 행동/대응)을 기록."""

    def __init__(self, agents):
        self.agents = agents
        self.log = []

    def __getattr__(self, name):
        def dispatch(state, pidx, *args, **kw):
            fn = getattr(self.agents[pidx], name, None)
            if fn is None:
                # 봇에 없는 훅은 RandomBot 기본으로 폴백 (없으면 그냥 통과)
                fn = getattr(RandomBot(seed=0), name, None)
                if fn is None:
                    raise AttributeError(name)
            result = fn(state, pidx, *args, **kw)
            try:
                if name == "choose_main_action" and result and \
                        result[0] not in ("end", None):
                    kind, arg = result
                    if kind == "basic":
                        desc = str(arg)
                    elif kind == "card":
                        desc = f"{CARD_DB[arg[1]]['name_ko']}({arg[0]})"
                    elif kind == "release_taisen":
                        desc = f"대전해제:{CARD_DB[arg]['name_ko']}"
                    else:
                        desc = str(arg)
                    self.log.append({"t": state.turn_count, "p": pidx,
                                     "a": kind, "v": desc})
                elif name == "choose_reaction" and result is not None:
                    self.log.append({"t": state.turn_count, "p": pidx,
                                     "a": "react",
                                     "v": CARD_DB[result[1]]["name_ko"]})
            except Exception:
                pass   # 기록 실패는 게임 진행에 영향 없음
            return result
        return dispatch


BOTS = {
    "random": lambda seed: RandomBot(end_prob=0.3, seed=seed),
    "aggressive": lambda seed: AggressiveBot(end_prob=0.3, seed=seed),
    "heuristic": lambda seed: HeuristicBot(seed=seed),
    "mc": lambda seed: MonteCarloBot(seed=seed),
    "mc24": lambda seed: MonteCarloBot(seed=seed, rollouts=24, prune_top=5),
    "mc48": lambda seed: MonteCarloBot(seed=seed, rollouts=48, prune_top=5),
    "uct": lambda seed: UCTBot(seed=seed, iters=160),
    "uct_leaf": lambda seed: UCTBot(seed=seed, iters=160, leaf_eval=True),
    "lookahead": lambda seed: _make_lookahead(seed),
    "gemini": lambda seed: _make_gemini(seed),
    "netmc": lambda seed: _make_netmc(seed),
}


def _make_netmc(seed):
    # 가치망 리프 평가 MC 봇 (반복 자기대국 루프 산출물).
    # 기본 모델은 부트스트랩 반복 net_iter1. 없으면 v2 폴백.
    from ai.net_mc_bot import NetMCBot
    for path in ("data/loop/net_iter1.npz", "data/value_model_v2.npz"):
        if os.path.exists(path):
            return NetMCBot(model_path=path, seed=seed, rollouts=24, prune_top=4)
    return NetMCBot(seed=seed, rollouts=24, prune_top=4)


def _make_lookahead(seed):
    from ai.heuristic import LookaheadBot
    return LookaheadBot(seed=seed)


def _make_gemini(seed):
    from ai.gemini_agent import GeminiBot
    return GeminiBot(seed=seed, verbose=False)


class _DirectDriver:
    """기록 없이 활성 플레이어의 봇 훅으로 그대로 위임 (모든 시그니처 투명)."""

    def __init__(self, agents):
        self.agents = agents

    def __getattr__(self, name):
        def dispatch(state, pidx, *args, **kw):
            return getattr(self.agents[pidx], name)(state, pidx, *args, **kw)
        return dispatch


def run_game(bot0_name, bot1_name, m0, m1, seed, record=False, max_turns=200,
             two_megami=False):
    """한 판 실행. two_megami=True면 m0,m1은 (여신A,여신B) 튜플."""
    rng = random.Random(seed)
    if two_megami:
        from setup import new_game_2v2
        state = new_game_2v2(m0, m1, seed=seed, first=seed % 2)
    else:
        state = new_game(m0, m1, seed=seed, first=seed % 2)
    agents = [BOTS[bot0_name](seed), BOTS[bot1_name](seed + 1)]

    if record:
        rec = Recorder(agents)
        driver = rec
    else:
        # 기록 없이 대결: 활성 플레이어의 봇을 직접 사용 (래퍼 오버헤드 X)
        driver = _DirectDriver(agents)

    for _ in range(max_turns):
        play_turn(state, rng, driver)
        if state.is_over():
            break
    result = {
        "bots": [bot0_name, bot1_name],
        "megami": [m0, m1],
        "seed": seed,
        "first": seed % 2,
        "winner": state.winner,
        "reason": state.end_reason,
        "turns": state.turn_count,
        "final": {
            "life": [state.players[0].life, state.players[1].life],
            "distance": state.distance,
        },
    }
    if record:
        result["moves"] = rec.log
    return result


def league(matchups, n_games, out_path=None, verbose=True):
    """matchups: [(bot0, bot1)] — 각 조합을 여신 페어링 순환 + 선공 교대로 n판."""
    records = []
    table = {}
    t0 = time.time()
    for bot0, bot1 in matchups:
        w = [0, 0, 0]  # bot0승, bot1승, 무승부
        for i in range(n_games):
            m0 = CORE4[i % 4]
            m1 = CORE4[(i // 4) % 4]
            r = run_game(bot0, bot1, m0, m1, seed=i, record=(out_path is not None))
            records.append(r)
            if r["winner"] == 0:
                w[0] += 1
            elif r["winner"] == 1:
                w[1] += 1
            else:
                w[2] += 1
        table[(bot0, bot1)] = w
        if verbose:
            print(f"{bot0:>10} vs {bot1:<10}: "
                  f"{w[0]:>3}승 {w[1]:>3}패 {w[2]}무 "
                  f"(승률 {100*w[0]/n_games:.0f}%)")
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        if verbose:
            print(f"기보 {len(records)}건 저장: {out_path}")
    if verbose:
        print(f"총 소요: {time.time()-t0:.1f}초")
    return table, records


def duel(bot0, bot1, n=50, two_megami=False, seed_base=0, verbose=True):
    """
    두 봇을 n판 대결시키고 승률 요약. 여신은 랜덤 배정(선공 교대).
    반환: dict(bot0승, bot1승, 무, 승률).
    """
    import random as _r
    from setup import CORE4
    w = [0, 0, 0]
    reasons = {}
    t0 = time.time()
    for i in range(n):
        rng = _r.Random(seed_base + i)
        if two_megami:
            m0 = tuple(rng.sample(CORE4, 2))
            m1 = tuple(_r.Random(seed_base + i * 7 + 1).sample(CORE4, 2))
        else:
            m0 = CORE4[i % len(CORE4)]
            m1 = CORE4[(i // len(CORE4) + 1) % len(CORE4)]
        r = run_game(bot0, bot1, m0, m1, seed=seed_base + i,
                     two_megami=two_megami)
        if r["winner"] == 0:
            w[0] += 1
        elif r["winner"] == 1:
            w[1] += 1
        else:
            w[2] += 1
        reasons[r["reason"] or "미결"] = reasons.get(r["reason"] or "미결", 0) + 1
    games = w[0] + w[1] + w[2]
    if verbose:
        mode = "2여신" if two_megami else "단일"
        print(f"[{mode}] {bot0} vs {bot1}: "
              f"{w[0]}승 {w[1]}패 {w[2]}무 / {games}판 "
              f"→ {bot0} 승률 {100*w[0]/max(games,1):.1f}%")
        print(f"  종료 사유: {reasons}")
        print(f"  소요: {time.time()-t0:.1f}초")
    return {"bot0": bot0, "bot1": bot1, "wins": w,
            "winrate": w[0] / max(games, 1)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="후루요니 AI 봇 대결 도구")
    ap.add_argument("--bot0", default="heuristic",
                    help=f"봇 이름 {list(BOTS.keys())}")
    ap.add_argument("--bot1", default="random")
    ap.add_argument("--games", type=int, default=50)
    ap.add_argument("--two", action="store_true", help="2여신 덱 모드")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default=None, help="기보 저장 경로(jsonl)")
    ap.add_argument("--league", action="store_true",
                    help="기본 리그(여러 봇 순위) 실행")
    args = ap.parse_args()

    if args.league:
        matchups = [
            ("heuristic", "random"),
            ("heuristic", "aggressive"),
            ("mc24", "heuristic"),
            ("mc48", "heuristic"),
            ("mc48", "mc24"),
        ]
        for b0, b1 in matchups:
            duel(b0, b1, args.games, two_megami=args.two, seed_base=args.seed)
    else:
        duel(args.bot0, args.bot1, args.games,
             two_megami=args.two, seed_base=args.seed)
