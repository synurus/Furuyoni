"""
UCT 파라미터 튜닝 스크립트 (로컬 실행용)

── 2026-07-06 세션 내 튜닝 실측 기록 ──
- UCT iters=160 vs 휴리스틱: ~50% (동급)
- UCT iters=400 vs 휴리스틱: 1승 7패 (소표본이나 악화 신호)
  → 반복 증량이 도움 안 됨 = 예산 문제가 아니라 구조 문제로 추정
    (노이즈 롤아웃 위의 미니맥스가 보수적 수를 과대평가하는 것으로 보임)
  → 개선 후보: 리프 평가를 롤아웃 대신 휴리스틱 평가로 대체, 트리 재사용,
    상대 노드에 정책 prior 도입
- 반면 MC 하이브리드 K=24→48 증량: 직접 대결 32판 69%±16%p로 확실한 개선
  → 챔피언은 MC 하이브리드 유지, recommend()는 K=48로 상향 반영됨

── 2026-07-08 UCT 개선 시도 최종 결론 ──
개선 후보들을 실측한 결과, UCT는 이 게임에서 휴리스틱을 넘지 못함이 확정됨.

시도 및 결과 (vs 휴리스틱, 20~24판 소표본):
- 기존 롤아웃 방식 iters160: ~29%
- 리프평가(leaf_eval=True, 롤아웃 제거) iters120: 29%
- 짧은 롤아웃 H=2: 29%
- 강prior(n0=8)+저탐험(c=0.3)+리프평가: 25%

기본 능력 확인:
- 리프UCT vs 랜덤: 100% (UCT 탐색 자체는 정상 작동)

결론(구조적 한계):
1. 평가함수(1수 앞 휴리스틱)가 이미 강력 → 트리 탐색의 한계이득이 작음
2. 불완전정보 determinize 노이즈 + 높은 분기 → 트리가 얕고 넓게 퍼짐
3. 리프평가로 노이즈를 없애면 평가함수의 근시안이 미니맥스로 증폭됨
→ UCT로는 휴리스틱 초과가 어려움. MC 하이브리드가 실질적 최적점.
  (MC는 다수 결정화의 앙상블 평균으로 노이즈를 상쇄, UCT는 단일 트리라 불리)

권장: 챔피언은 MonteCarloBot(K=48) 유지.
      UCTBot(leaf_eval=True)는 랜덤/약체 상대로 빠른 탐색이 필요할 때만 사용.
      추가 개선은 평가함수 자체 고도화(2수 앞 등)가 선행되어야 의미 있음.

조합당 games판씩 휴리스틱/MC 상대 승률을 측정한다.
한 조합에 수 분 걸리므로 밤에 돌려두는 용도.

실행: python3 ai/tune_uct.py --games 64
"""

import argparse
import math
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ai.league as L
from ai.uct import UCTBot
from ai.mcts import MonteCarloBot

# 실험할 조합 (필요에 따라 수정)
CONFIGS = [
    {"iters": 160, "c_uct": 0.9, "prior_visits": 3, "root_top": 5},
    {"iters": 400, "c_uct": 0.9, "prior_visits": 3, "root_top": 5},
    {"iters": 400, "c_uct": 0.6, "prior_visits": 5, "root_top": 4},
    {"iters": 800, "c_uct": 0.9, "prior_visits": 3, "root_top": 5},
    {"iters": 400, "c_uct": 1.2, "prior_visits": 2, "root_top": 6},
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=64)
    ap.add_argument("--opponent", default="heuristic",
                    choices=["heuristic", "mc"])
    args = ap.parse_args()
    L.BOTS["mc"] = lambda seed: MonteCarloBot(seed=seed)

    print(f"상대: {args.opponent}, 조합당 {args.games}판\n")
    for cfg in CONFIGS:
        L.BOTS["uct"] = lambda seed, c=cfg: UCTBot(seed=seed, **c)
        t0 = time.time()
        table, _ = L.league([("uct", args.opponent)], args.games,
                            verbose=False)
        w = table[("uct", args.opponent)]
        n = args.games
        p = w[0] / n
        ci = 1.96 * math.sqrt(p * (1 - p) / n)
        print(f"{cfg} → 승률 {p*100:.0f}% ± {ci*100:.0f}%p "
              f"({time.time()-t0:.0f}초)")


if __name__ == "__main__":
    main()
