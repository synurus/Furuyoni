"""
반복 자기대국 루프 (AlphaZero식 최소 구현).

B-4 세션이 실측으로 규명한 3대 실패 원인을 이 루프가 정면으로 다룬다:
  #1 데이터량 부족   → --gen-games / --window 로 축적·확대
  #2 데이터 다양성   → 봇 풀 + 탐색 노이즈(explore_eps) 로 편향 완화
  #3 반복 개선 없음  → 학습된 가치망을 NetMCBot의 리프 평가로 넣어
                       다음 자기대국을 그 봇으로 재생성 → 재학습 (진짜 루프)

한 반복(iteration):
  1) 자기대국으로 데이터 생성
       - iter 0: heuristic 풀 + 탐색 노이즈 (빠른 부트스트랩)
       - iter k: 직전 반복의 NetMCBot(가치망 탐색봇)으로 생성 (on-policy)
  2) 최근 window회분 데이터로 가치망 재학습 → net_k 저장
  3) NetMCBot(net_k) 를 heuristic·mc48과 대결시켜 승률 측정
  4) 승률 추이 테이블 갱신

주의(중요): mc/NetMC 자기대국은 느리다. 이 샌드박스는 세션 제한이 있어
  기본값은 "동작 증명"용 소규모다. 실제 성능 개선에 필요한 규모(수만~수십만 판,
  여러 반복)는 로컬에서 --gen-games / --iters 를 키워 돌려야 한다.
  이 스크립트는 그 로컬 실행을 그대로 지원한다.

실행 예:
  # 샌드박스 동작 증명 (작게)
  python -m ai.run_loop --iters 2 --gen-games 80 --eval-games 40 --gen-rollouts 8
  # 로컬 본격 실행 (예)
  python -m ai.run_loop --iters 6 --gen-games 4000 --eval-games 200 \
                        --gen-rollouts 24 --window 3 --onpolicy
"""

import os
import sys
import time
import json
import random
import argparse
import subprocess

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from setup import new_game, CORE4                       # noqa: E402
from turn import play_turn                               # noqa: E402
from ai.league import BOTS, _DirectDriver                # noqa: E402
from ai.selfplay import generate                         # noqa: E402
from ai.features import feature_dim                       # noqa: E402
from ai.train_value import ValueModel                      # noqa: E402
from ai.net_mc_bot import NetMCBot                          # noqa: E402


# ── 임의 에이전트 대전 (league.duel은 등록봇 이름만 받으므로 자체 구현) ──
def duel_agents(make0, make1, n, seed_base=0, max_turns=200):
    """make0/make1: seed->agent. n판 대결 → [p0승, p1승, 무]."""
    w = [0, 0, 0]
    for i in range(n):
        rng = random.Random(seed_base + i)
        m0 = CORE4[i % len(CORE4)]
        m1 = CORE4[(i // len(CORE4) + 1) % len(CORE4)]
        state = new_game(m0, m1, seed=seed_base + i, first=(seed_base + i) % 2)
        agents = [make0(seed_base + i), make1(seed_base + i + 1)]
        driver = _DirectDriver(agents)
        for _ in range(max_turns):
            play_turn(state, rng, driver)
            if state.is_over():
                break
        w[0 if state.winner == 0 else 1 if state.winner == 1 else 2] += 1
    return w


def train_net(samples, kind="mlp", hidden=32, epochs=30, lr=0.1, seed=0):
    """샘플 리스트 → 학습된 ValueModel + val_acc."""
    X = np.array([s["x"] for s in samples], dtype=np.float32)
    Y = np.array([s["y"] for s in samples], dtype=np.float32)
    rng = np.random.RandomState(seed)
    idx = rng.permutation(len(X))
    n_val = max(1, len(X) // 5)
    vi, ti = idx[:n_val], idx[n_val:]
    m = ValueModel(X.shape[1], kind=kind, hidden=hidden, seed=seed)
    m.train(X[ti], Y[ti], X[vi], Y[vi], epochs=epochs, lr=lr, verbose=False)
    return m, m._acc(X[vi], Y[vi])


def gen_iteration_data(it, prev_model_path, args):
    """반복 it의 자기대국 데이터 생성 (병렬)."""
    from ai.selfplay import generate_parallel
    if it == 0 or not args.onpolicy or prev_model_path is None:
        # 부트스트랩: 빠른 heuristic 풀 + 탐색 노이즈
        spec = {"kind": "pool", "pool": ["heuristic", "aggressive"]}
    else:
        # on-policy: 직전 반복 가치망 탐색봇으로 생성 (진짜 반복 루프).
        # 모델은 경로로 넘겨 각 워커가 로드 → 프로세스 간 pickle 회피.
        spec = {"kind": "netmc", "model_path": prev_model_path,
                "rollouts": args.gen_rollouts, "prune_top": 4,
                "horizon": args.horizon}
    return generate_parallel(args.gen_games, spec, workers=args.workers,
                             explore_eps=args.explore_eps,
                             seed_base=args.seed + it * 100000, verbose=True)


def evaluate_net(model, args):
    """NetMCBot(model)을 heuristic·mc48과 대결 → 승률 dict."""
    def net_factory(seed):
        return NetMCBot(model=model, seed=seed, rollouts=args.eval_rollouts,
                        prune_top=4, horizon=args.horizon)
    out = {}
    w = duel_agents(net_factory, lambda s: BOTS["heuristic"](s),
                    args.eval_games, seed_base=args.seed + 777)
    out["vs_heuristic"] = w[0] / max(sum(w), 1)
    if args.eval_vs_mc:
        w2 = duel_agents(net_factory, lambda s: BOTS["mc48"](s),
                         max(4, args.eval_games // 4), seed_base=args.seed + 999)
        out["vs_mc48"] = w2[0] / max(sum(w2), 1)
    return out


def git_commit():
    """현재 커밋 해시 (실패하면 None)."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.join(os.path.dirname(__file__), ".."),
            text=True).strip()
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description="반복 자기대국 루프")
    ap.add_argument("--iters", type=int, default=2)
    ap.add_argument("--gen-games", type=int, default=80)
    ap.add_argument("--eval-games", type=int, default=40)
    ap.add_argument("--gen-rollouts", type=int, default=8,
                    help="on-policy 데이터 생성 시 NetMCBot 롤아웃 수")
    ap.add_argument("--eval-rollouts", type=int, default=12)
    ap.add_argument("--horizon", type=int, default=8)
    ap.add_argument("--explore-eps", type=float, default=0.15)
    ap.add_argument("--window", type=int, default=3,
                    help="재학습에 쓸 최근 반복 데이터 창 크기")
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--hidden", type=int, default=32)
    ap.add_argument("--onpolicy", action="store_true",
                    help="iter>=1을 NetMCBot 자기대국으로 생성 (진짜 루프)")
    ap.add_argument("--eval-vs-mc", action="store_true",
                    help="매 반복 mc48과도 대결 (느림)")
    ap.add_argument("--workers", type=int, default=1,
                    help="데이터 생성 병렬 프로세스 수 (로컬 코어 수 권장)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--outdir", default="data/loop")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    print(f"특징 차원: {feature_dim()} | 모드: "
          f"{'on-policy 반복' if args.onpolicy else '다양성 부트스트랩 반복'}")
    print(f"반복 {args.iters}회 | 판/반복 {args.gen_games} | "
          f"평가 {args.eval_games}판 | 워커 {args.workers}\n")

    buffers = []          # 반복별 샘플 리스트 (window 슬라이딩)
    prev_model_path = None
    history = []          # (iter, n_samples, val_acc, vs_heuristic, vs_mc48)
    t_all = time.time()
    commit = git_commit()
    run_id = time.strftime("%Y%m%d-%H%M")   # 한 실행의 모든 반복에 같은 값

    for it in range(args.iters):
        t0 = time.time()
        # 1) 데이터 생성 (직전 반복 모델 경로를 워커에 전달)
        samples = gen_iteration_data(it, prev_model_path, args)
        buffers.append(samples)
        if len(buffers) > args.window:
            buffers.pop(0)
        train_set = [s for buf in buffers for s in buf]
        t_gen = time.time() - t0

        # 2) 재학습
        t1 = time.time()
        model, val_acc = train_net(train_set, hidden=args.hidden,
                                   epochs=args.epochs, seed=args.seed + it)
        model_path = os.path.join(args.outdir, f"net_iter{it}.npz")
        model.save(model_path)
        t_train = time.time() - t1

        # 3) 평가
        t2 = time.time()
        ev = evaluate_net(model, args)
        t_eval = time.time() - t2

        prev_model_path = model_path
        row = (it, len(train_set), val_acc,
               ev["vs_heuristic"], ev.get("vs_mc48"))
        history.append(row)
        vm = f"{ev['vs_mc48']*100:.1f}%" if ev.get("vs_mc48") is not None else "—"
        print(f"[iter {it}] 샘플 {len(train_set):6d} | val_acc {val_acc:.3f} | "
              f"vs heuristic {ev['vs_heuristic']*100:5.1f}% | vs mc48 {vm} | "
              f"gen {t_gen:.0f}s train {t_train:.0f}s eval {t_eval:.0f}s")

        # 4) Notion 실험 로그 (실패해도 루프는 계속 돈다)
        try:
            from notion_log import log_iteration
            log_iteration(
                iteration=it,
                run_id=run_id,
                vs_heuristic=ev["vs_heuristic"] * 100,
                vs_mc=ev["vs_mc48"] * 100 if ev.get("vs_mc48") is not None else None,
                gen_games=args.gen_games,
                window=args.window,
                samples=len(train_set),
                val_acc=val_acc,
                commit=commit,
                notes=f"{'on-policy' if args.onpolicy else 'bootstrap'} "
                      f"workers={args.workers} gen_rollouts={args.gen_rollouts}",
            )
        except Exception as e:
            print(f"  (Notion 기록 실패, 무시: {e})")

    # 요약
    print("\n=== 반복 요약 (NetMCBot 승률 추이) ===")
    print(f"{'iter':>4} {'samples':>8} {'val_acc':>8} {'vs_heur':>9} {'vs_mc48':>9}")
    for it, n, acc, vh, vm in history:
        vms = f"{vm*100:.1f}%" if vm is not None else "—"
        print(f"{it:>4} {n:>8} {acc:>8.3f} {vh*100:>8.1f}% {vms:>9}")
    with open(os.path.join(args.outdir, "history.json"), "w") as f:
        json.dump([{"iter": it, "samples": n, "val_acc": acc,
                    "vs_heuristic": vh, "vs_mc48": vm}
                   for it, n, acc, vh, vm in history], f,
                  ensure_ascii=False, indent=2)
    print(f"\n총 소요 {time.time()-t_all:.0f}s | 산출물: {args.outdir}/")
    print("참고: 여기 승률이 mc48 챔피언을 넘지 못하면, 규모(판 수)를 로컬에서 "
          "키우는 것이 다음 단계다. 루프 기계 자체는 정상 작동함을 이 표가 보여준다.")


if __name__ == "__main__":
    main()
