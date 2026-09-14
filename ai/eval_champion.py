"""
학습된 가치망 봇을 기존 봇과 제대로 붙여 승률과 신뢰구간을 낸다.

반복마다 15판씩 재서 20~47%를 오가는 무의미한 숫자를 보는 대신,
학습이 끝난 뒤 한 번 충분한 판수로 판정하기 위한 도구.

    python -m ai.eval_champion --model data/loop_ci/net_iter5.npz --games 300
    python -m ai.eval_champion --model data/loop_ci/net_iter5.npz --two --workers 8

선후공과 여신 배정을 번갈아 주어 자리 이점을 상쇄한다.
"""

import os
import sys
import math
import time
import random
import argparse
import multiprocessing as mp

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from setup import new_game, new_game_2v2, CORE4  # noqa: E402
from turn import play_turn                                     # noqa: E402
from ai.league import BOTS, _DirectDriver                      # noqa: E402


def wilson(wins, n, z=1.96):
    """이항 비율의 Wilson 신뢰구간 (n이 작을 때도 안전)."""
    if n == 0:
        return 0.0, 0.0
    p = wins / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    s = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - s) / d, (c + s) / d


def _play_chunk(payload):
    """워커 한 명이 맡은 게임 묶음. (top-level 함수여야 pickle 가능)"""
    n, seed_base, cfg = payload
    from ai.net_mc_bot import NetMCBot
    from ai.train_value import ValueModel

    model = ValueModel.load(cfg["model"])          # 워커당 1회 로드
    pool = CORE4                                   # 구현된 여신 전체
    wins = losses = draws = 0

    for i in range(n):
        gid = seed_base + i
        net_side = gid % 2                          # 자리를 번갈아
        rng = random.Random(gid)

        if cfg["two"]:
            mr = random.Random(gid ^ 0x5EED)        # 여신 배정도 gid로 결정
            m0 = tuple(mr.sample(pool, 2))
            m1 = tuple(mr.sample(pool, 2))
            state = new_game_2v2(m0, m1, seed=gid, first=gid % 2)
        else:
            m0 = pool[gid % len(pool)]
            m1 = pool[(gid // len(pool) + 1) % len(pool)]
            state = new_game(m0, m1, seed=gid, first=gid % 2)

        net = NetMCBot(model=model, seed=gid, rollouts=cfg["rollouts"],
                       prune_top=4, horizon=cfg["horizon"])
        opp = BOTS[cfg["opponent"]](gid + 1)
        agents = [net, opp] if net_side == 0 else [opp, net]
        driver = _DirectDriver(agents)

        for _ in range(cfg["max_turns"]):
            play_turn(state, rng, driver)
            if state.is_over():
                break
        if state.winner is None:
            draws += 1
        elif state.winner == net_side:
            wins += 1
        else:
            losses += 1
    return wins, losses, draws


def main():
    ap = argparse.ArgumentParser(description="가치망 봇 챔피언 판정")
    ap.add_argument("--model", required=True, help="학습된 .npz 경로")
    ap.add_argument("--opponent", default="mc48",
                    help=f"상대 봇 {list(BOTS.keys())}")
    ap.add_argument("--games", type=int, default=300)
    ap.add_argument("--rollouts", type=int, default=12)
    ap.add_argument("--horizon", type=int, default=8)
    ap.add_argument("--max-turns", type=int, default=200)
    ap.add_argument("--two", action="store_true", help="2여신 안전구축 게임")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--seed", type=int, default=10000)
    args = ap.parse_args()

    cfg = {"model": args.model, "opponent": args.opponent,
           "rollouts": args.rollouts, "horizon": args.horizon,
           "max_turns": args.max_turns, "two": args.two}

    print(f"{os.path.basename(args.model)} vs {args.opponent} | "
          f"{args.games}판 | {'2여신' if args.two else '단일여신'} | "
          f"워커 {args.workers}")
    t0 = time.time()

    if args.workers <= 1:
        chunks = [_play_chunk((args.games, args.seed, cfg))]
    else:
        size = (args.games + args.workers - 1) // args.workers
        payloads, g = [], 0
        while g < args.games:
            k = min(size, args.games - g)
            payloads.append((k, args.seed + g, cfg))
            g += k
        method = "fork" if "fork" in mp.get_all_start_methods() else "spawn"
        with mp.get_context(method).Pool(len(payloads)) as pool:
            chunks = pool.map(_play_chunk, payloads)

    wins = sum(c[0] for c in chunks)
    losses = sum(c[1] for c in chunks)
    draws = sum(c[2] for c in chunks)
    n = wins + losses
    lo, hi = wilson(wins, n)

    print(f"\n{wins}승 {losses}패 (무 {draws}) | {time.time()-t0:.0f}초")
    print(f"승률 {wins/max(n,1)*100:.1f}%  95% 신뢰구간 "
          f"[{lo*100:.1f}%, {hi*100:.1f}%]")
    if lo > 0.5:
        print("→ 상대보다 강하다고 볼 수 있다 (구간이 50% 위)")
    elif hi < 0.5:
        print("→ 상대보다 약하다 (구간이 50% 아래)")
    else:
        print("→ 판정 불가. 구간이 50%를 걸친다 — 판수를 늘려야 한다")


if __name__ == "__main__":
    main()
