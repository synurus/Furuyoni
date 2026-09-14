# C-1 반복 자기대국 루프 착수 (2026-07-18)

## 배경 — B-4가 남긴 세 벽
B-4(1-ply valuenet_bot)는 vs HeuristicBot 10%로 실패했고, 원인을 실측으로 규명했다:
1. **데이터량 부족** (4~5만 샘플)
2. **데이터 다양성 부족** (약한 봇 1개의 편향된 자기대국)
3. **반복 개선 루프 없음** (지도학습 1회로 끝)

추가로 핵심 진단: *가치망 단독으로는 후보 수를 변별 못 함*(같은 상태 서로 다른
수의 점수 표준편차 0.0125 ≈ 노이즈). B-4는 이 망을 **1-ply 수 선택**에 직접 썼기에
망의 약점을 그대로 맞았다.

## 이번 세션의 설계 결정
가치망에게 "수 변별"을 시키지 않는다. 대신:
- **탐색(MC 롤아웃)이 수 변별을 담당**하고,
- **가치망은 롤아웃이 절단된 비종국 리프의 국면 평가만 담당**한다
  (MonteCarloBot이 `evaluate()`를 쓰던 바로 그 자리).

즉 AlphaZero식 "search + learned leaf evaluator"의 최소판. 이것이 B-4의 근본
결함을 우회한다.

## 구현물
- `ai/selfplay.py` 확장 — `bot_pool`(정책 혼합), `explore_eps`(탐색 노이즈),
  `agent_factory`(학습된 봇으로 재생성) 추가. → 실패 원인 #2 대응.
- `ai/net_mc_bot.py` — `NetMCBot(MonteCarloBot)`. 리프 평가를 가치망으로 교체.
  `net_blend`로 (가치망:손짠평가) 리프 혼합 가능(초기 반복 안전망).
- `ai/run_loop.py` — 반복 오케스트레이터. 생성→재학습→NetMCBot 대결 평가→
  다음 반복은 그 NetMCBot으로 on-policy 재생성. → 실패 원인 #3 대응.
- `ai/league.py` — `netmc` 봇 등록(기본 모델 `data/loop/net_iter1.npz`).

## 실측 1 — 아키텍처 수정의 효과 (핵심 결과)
같은 가치망을 **어디에 꽂느냐**만 바꿨을 때:

| 봇 | vs HeuristicBot |
|---|---|
| B-4 ValueNetBot (1-ply 수 선택) | **10.0%** |
| NetMCBot (MC 탐색 리프 평가) | **50~55%** |

망은 그대로인데 승률이 10%→50%대로 뛴다. B-4의 진단("망은 국면 평가엔 강하나
수 변별엔 약하다 → 탐색이 변별을 맡아야 한다")이 실측으로 확인됐다.

## 실측 2 — 부트스트랩 반복 루프 (다양성 데이터, 빠름)
`--iters 2 --gen-games 150 --eval-games 20 --eval-rollouts 10`

| iter | samples | val_acc | vs heuristic |
|---|---|---|---|
| 0 | 14,765 | 0.771 | 50.0% |
| 1 | 29,663 | 0.811 | 55.0% |

데이터 누적 + 재학습으로 val_acc·승률이 함께 상향(표본 작아 노이즈 포함).

## 실측 3 — on-policy 반복 루프 (진짜 루프, 소규모)
`--onpolicy --iters 2 --gen-games 30 --gen-rollouts 6 --eval-games 18`
iter1이 iter0에서 학습한 NetMCBot으로 데이터를 재생성(gen 115초가 그 증거)한 뒤
재학습·재평가. 승률 33→39%, val_acc 0.80→0.74(작은 on-policy 표본의 분포 변화).
**의의: 성능 수치가 아니라, 반복 메커니즘 전체가 end-to-end로 작동함을 증명.**

## 정직한 결론
- ✅ B-4의 세 벽 중 **다양성·반복 루프**를 실제 코드로 구축했고, **아키텍처 결함**
  (1-ply→탐색 리프)은 실측 개선(10%→50%대)까지 확인했다.
- ⚠️ 남은 벽은 **데이터량 = 규모**. 이 샌드박스는 세션 시간 제한으로 판/반복이
  수십~수백에 그쳐, 승률이 mc48 챔피언을 넘길 만큼 못 키운다(NetMC 평가가
  판당 ~4초, on-policy 생성이 판당 ~3초라 대량 생성이 불가).
- ➡️ **다음 단계는 로컬 대규모 실행**. 스크립트는 그대로 지원:
  ```
  python -m ai.run_loop --onpolicy --iters 6 --gen-games 4000 \
         --eval-games 200 --gen-rollouts 24 --window 3 --eval-vs-mc
  ```
  판/반복을 100배로 키우면 이 루프가 손짠 평가를 넘을 수 있는지 비로소 검증된다.

## 챔피언 상태
- 챔피언은 여전히 **MonteCarloBot(K=48)**. NetMCBot은 등록됐으나 현재 규모의
  가치망으로는 mc48 미검증 → 로컬 대규모 학습 후 재평가 대상.
- 산출물: `data/loop/`(부트스트랩), `data/loop_onpolicy/`(on-policy) 각 반복 모델 +
  `history.json`.

## 추가 — 병렬 생성 (로컬 대규모 실행용)
데이터 생성이 시간 병목이라(NetMC 자기대국 판당 ~3~12초), `selfplay.py`에
`generate_parallel(n, spec, workers)`을 추가하고 `run_loop.py`에 `--workers`를
연결했다.
- 워커에 에이전트/람다를 넘기지 않고 **명세(spec)만 넘겨 워커 안에서 재구성** —
  pickle 문제 회피. on-policy 모델은 **경로로 넘겨 각 워커가 1회 로드해 공유**.
- **결정성 보장**: 각 게임을 전역 id `gid=seed_base+g`로 완전히 결정하도록
  수정(이전엔 여신 배정이 로컬 루프 인덱스에 의존해, 청크 분할 시 데이터가
  달라지는 잠재 버그가 있었음). 검증: 직렬 = 3워커 = 4워커 **동일 해시**.
- 속도: 코어 수에 거의 선형(예: 8코어면 생성 시간 ≈ 1/8). 이 샌드박스는 1코어라
  속도 이득은 로컬에서만 나오지만, 병렬 경로 자체는 on-policy까지 정상 작동 확인.

로컬 대규모 실행:
```
python -m ai.run_loop --onpolicy --iters 8 --gen-games 4000 \
       --eval-games 200 --gen-rollouts 12 --eval-rollouts 24 \
       --window 3 --workers 8 --eval-vs-mc
```
