"""
자기대국 학습 데이터 생성.

봇끼리 대국시키며 각 턴의 (상태 특징 벡터, 그 게임의 최종 결과)를 기록한다.
가치망 학습용 (state → 승률) 데이터셋을 만든다.

출력: jsonl (한 줄 = {"x": [특징...], "y": 승패(1/0), "meta": {...}})
      또는 npz (대량용, numpy 있을 때).

실행:
    python ai/selfplay.py --games 200 --bot mc24 --two --out data/selfplay.jsonl

주의: mc 계열은 느림. 대량 생성은 heuristic 권장 (약하지만 빠름).
      제 실행 환경은 세션 한정 → 대량은 로컬에서.
"""

import os
import sys
import json
import random
import argparse
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from setup import new_game, new_game_2v2, CORE4    # noqa: E402
from turn import play_turn                          # noqa: E402
from ai.features import encode_state, feature_dim   # noqa: E402
from ai.league import BOTS                          # noqa: E402


class _ExploreWrapper:
    """
    임의 봇을 감싸, 메인 행동 선택 시 확률 eps로 무작위 합법수를 두게 한다.
    데이터 다양성 확보용 (같은 정책만 반복해 편향되는 것을 완화).
    다른 결정(대응/데미지 등)은 원봇에 그대로 위임.
    """

    def __init__(self, agent, eps, rng):
        self._agent = agent
        self._eps = eps
        self._rng = rng

    def __getattr__(self, name):
        def dispatch(state, pidx, *args, **kw):
            if name == "choose_main_action" and self._eps > 0:
                legal = args[0] if args else kw.get("legal")
                if legal and self._rng.random() < self._eps:
                    return self._rng.choice(legal)
            return getattr(self._agent, name)(state, pidx, *args, **kw)
        return dispatch


def _make_agent(side, seed, bot_name, agent_factory, bot_pool, explore_eps):
    """
    한 진영의 에이전트를 만든다. 우선순위:
      agent_factory(콜러블) > bot_pool(콜러블 리스트에서 무작위) > bot_name(BOTS 키)
    explore_eps>0이면 탐색 래퍼로 감싼다.
    """
    if agent_factory is not None:
        base = agent_factory(seed)
    elif bot_pool:
        pick = random.Random(seed * 131 + side).choice(bot_pool)
        base = pick(seed) if callable(pick) else BOTS[pick](seed)
    else:
        base = BOTS[bot_name](seed)
    if explore_eps > 0:
        return _ExploreWrapper(base, explore_eps, random.Random(seed * 977 + side))
    return base


class _SampleRecorder:
    """
    에이전트를 감싸, 각 결정 시점의 (특징벡터, 활성 플레이어)를 수집.
    게임 종료 후 최종 승패로 라벨링한다.
    """

    def __init__(self, agents, sink):
        self.agents = agents
        self.sink = sink   # list: (pidx, feature_vec) 누적

    def __getattr__(self, name):
        def dispatch(state, pidx, *args, **kw):
            # 주요 결정 시점(메인 행동)에서만 스냅샷
            if name == "choose_main_action":
                self.sink.append((pidx, encode_state(state, pidx)))
            return getattr(self.agents[pidx], name)(state, pidx, *args, **kw)
        return dispatch


def generate(n_games, bot_name="heuristic", two_megami=False,
             seed_base=0, max_turns=250, verbose=True,
             agent_factory=None, bot_pool=None, explore_eps=0.0):
    """
    n_games판 자기대국 → 샘플 리스트 반환.
    각 샘플: {"x": [...], "y": 1.0(그 플레이어 승) / 0.0(패) / 0.5(무)}

    다양성 옵션 (편향 완화 — B-4 실패 원인 #2 대응):
      agent_factory: seed->agent 콜러블. 주면 양쪽 다 이걸로 생성 (루프의
                     "학습된 봇으로 재생성" 단계에서 사용).
      bot_pool:      콜러블/BOTS키의 리스트. 진영마다 무작위 선택 → 정책 혼합.
      explore_eps:   메인 행동을 확률 eps로 무작위화 (탐색 노이즈).
    """
    samples = []
    t0 = time.time()
    wins = [0, 0, 0]
    for g in range(n_games):
        gid = seed_base + g          # 전역 게임 id — 청크 분할과 무관하게 고유
        rng = random.Random(gid)
        if two_megami:
            m0 = tuple(rng.sample(CORE4, 2))
            m1 = tuple(random.Random(gid * 7 + 1).sample(CORE4, 2))
            state = new_game_2v2(m0, m1, seed=gid, first=gid % 2)
        else:
            m0 = CORE4[gid % len(CORE4)]
            m1 = CORE4[(gid // len(CORE4) + 1) % len(CORE4)]
            state = new_game(m0, m1, seed=gid, first=gid % 2)
        agents = [_make_agent(0, gid, bot_name,
                              agent_factory, bot_pool, explore_eps),
                  _make_agent(1, gid + 1, bot_name,
                              agent_factory, bot_pool, explore_eps)]
        sink = []
        rec = _SampleRecorder(agents, sink)
        for _ in range(max_turns):
            play_turn(state, rng, rec)
            if state.is_over():
                break

        # 라벨링: 각 스냅샷의 플레이어 관점 승패
        winner = state.winner
        if winner == 0:
            wins[0] += 1
        elif winner == 1:
            wins[1] += 1
        else:
            wins[2] += 1
        for pidx, x in sink:
            if winner == -1 or winner is None:
                y = 0.5
            else:
                y = 1.0 if winner == pidx else 0.0
            samples.append({"x": x, "y": y})

        if verbose and (g + 1) % max(1, n_games // 10) == 0:
            print(f"  {g+1}/{n_games}판, 샘플 {len(samples)}개, "
                  f"{time.time()-t0:.1f}초")
    if verbose:
        print(f"완료: {n_games}판 → 샘플 {len(samples)}개 "
              f"(P0 {wins[0]}승 / P1 {wins[1]}승 / {wins[2]}무), "
              f"{time.time()-t0:.1f}초")
    return samples


def save_jsonl(samples, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        for s in samples:
            f.write(json.dumps(s) + "\n")
    print(f"저장: {path} ({len(samples)} 샘플)")


def save_npz(samples, path):
    """numpy 있으면 압축 저장 (학습 시 로드 빠름)."""
    try:
        import numpy as np
    except ImportError:
        print("numpy 없음 → jsonl로 대체 저장")
        save_jsonl(samples, path.replace(".npz", ".jsonl"))
        return
    X = np.array([s["x"] for s in samples], dtype=np.float32)
    Y = np.array([s["y"] for s in samples], dtype=np.float32)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    np.savez_compressed(path, X=X, Y=Y)
    print(f"저장: {path} (X={X.shape}, Y={Y.shape})")


# ─────────────────────────────────────────────────────────────
# 병렬 생성 (multiprocessing)
#
# 워커에 에이전트 객체/람다를 넘기지 않는다 (pickle 문제 회피).
# 대신 "명세(spec)"만 넘겨 워커 안에서 에이전트를 재구성한다.
#   spec = {"kind": "pool",  "pool": [...], }                       # 빠른 부트스트랩
#   spec = {"kind": "netmc", "model_path": "...", "rollouts": ...,  # on-policy
#           "prune_top": ..., "horizon": ...}
# on-policy 모델은 경로로 넘겨 각 워커가 1회 로드해 공유(재로드 방지).
#
# 결정성: 각 게임은 seed_base+g로 완전히 결정되므로, 워커 수와 무관하게
#         같은 데이터가 나온다 (병렬화가 데이터를 바꾸지 않음).
# ─────────────────────────────────────────────────────────────

def _build_from_spec(spec):
    """spec → generate()에 넘길 kwargs(agent_factory 또는 bot_pool)."""
    kind = spec["kind"]
    if kind == "pool":
        return {"bot_pool": spec["pool"]}
    if kind == "netmc":
        from ai.net_mc_bot import NetMCBot
        from ai.train_value import ValueModel
        model = ValueModel.load(spec["model_path"])   # 워커당 1회 로드

        def factory(seed):
            return NetMCBot(model=model, seed=seed,
                            rollouts=spec.get("rollouts", 12),
                            prune_top=spec.get("prune_top", 4),
                            horizon=spec.get("horizon", 8))
        return {"agent_factory": factory}
    raise ValueError(f"알 수 없는 spec.kind: {kind}")


def _gen_worker(payload):
    """한 워커가 맡은 게임 청크를 생성. (top-level 함수여야 pickle 가능)"""
    n, seed_base, spec, two_megami, max_turns, explore_eps = payload
    kwargs = _build_from_spec(spec)
    return generate(n, two_megami=two_megami, seed_base=seed_base,
                    max_turns=max_turns, explore_eps=explore_eps,
                    verbose=False, **kwargs)


def generate_parallel(n_games, spec, workers=1, two_megami=False,
                      seed_base=0, max_turns=250, explore_eps=0.0,
                      verbose=True):
    """
    spec 정책으로 n_games판을 workers개 프로세스로 나눠 생성 → 샘플 리스트.
    workers<=1이면 단일 프로세스(오버헤드 없음).
    """
    t0 = time.time()
    if workers <= 1:
        samples = _gen_worker(
            (n_games, seed_base, spec, two_megami, max_turns, explore_eps))
        if verbose:
            print(f"  생성 {n_games}판(직렬) → {len(samples)}샘플, "
                  f"{time.time()-t0:.1f}초")
        return samples

    import multiprocessing as mp
    # 청크 분할: 각 워커가 겹치지 않는 seed 구간을 맡음
    chunk = (n_games + workers - 1) // workers
    payloads = []
    g0 = 0
    while g0 < n_games:
        n = min(chunk, n_games - g0)
        payloads.append((n, seed_base + g0, spec, two_megami,
                         max_turns, explore_eps))
        g0 += n
    ctx = mp.get_context("fork")   # Linux 기본; numpy와 안전
    with ctx.Pool(processes=min(workers, len(payloads))) as pool:
        chunks = pool.map(_gen_worker, payloads)
    samples = [s for c in chunks for s in c]
    if verbose:
        print(f"  생성 {n_games}판({len(payloads)}워커 병렬) → "
              f"{len(samples)}샘플, {time.time()-t0:.1f}초")
    return samples


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="자기대국 학습 데이터 생성")
    ap.add_argument("--games", type=int, default=100)
    ap.add_argument("--bot", default="heuristic",
                    help=f"자기대국 봇 {list(BOTS.keys())}")
    ap.add_argument("--two", action="store_true", help="2여신 모드")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="data/selfplay.jsonl")
    args = ap.parse_args()

    print(f"특징 차원: {feature_dim()}")
    samples = generate(args.games, bot_name=args.bot,
                       two_megami=args.two, seed_base=args.seed)
    if args.out.endswith(".npz"):
        save_npz(samples, args.out)
    else:
        save_jsonl(samples, args.out)
