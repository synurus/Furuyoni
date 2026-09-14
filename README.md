# 후루요니 AI 시뮬레이터 (furuyoni-ai)

기본 4여신(유리나·사이네·히미카·토코요) 기반 후루요니 게임 엔진 + AI 대전 프로젝트.
재연 패치 기준. 외부 라이브러리 불필요 (순수 Python 3.10+).

## 폴더 구조

```
furuyoni-ai/
├── data/
│   └── cards_core4.json      # 카드 44장 (재연 반영, 세션1 검수 완료, effects 구조화)
├── engine/                   # 게임 엔진 (룰북 2025-08-15판 기준)
│   ├── constants.py          # 게임 상수 (결정 36개, 오라 상한 5, 달인의 간격 2 등)
│   ├── state.py              # GameState + 벚꽃결정 보존 검증기
│   ├── tokens.py             # 결정 이동 + 기본동작 5종
│   ├── deck.py               # 뽑기 / 재구성 / 초조
│   ├── combat.py             # 공격 해결 파이프라인 (대응 창, 버프, 데미지)
│   ├── cards.py              # 카드 사용 (공격/행동/부여), 대응, 재기
│   ├── effects.py            # effects DSL 인터프리터
│   ├── turn.py               # 턴 상태머신 (개시/메인/종료)
│   └── setup.py              # 덱 프리셋 + 게임 초기화
├── ai/
│   ├── agents.py             # 에이전트 인터페이스 + RandomBot / AggressiveBot
│   ├── demo_turn.py          # 턴 상태머신 데모
│   └── demo_combat.py        # 대전 데모 + 스트레스 테스트
├── cli/
│   └── play.py               # 사람 플레이용 CLI (세션 5 검증 도구)
├── tests/
│   ├── test_faq.py           # FAQ 기반 룰 정확성 테스트 28건
│   └── run_tests.py          # pytest 미설치 환경용 미니 러너
└── docs/
    ├── PLAN.md               # 전체 작업 계획서
    ├── effects_spec.md       # 카드 효과 DSL 명세
    ├── session1_card_audit.md      # 검수: 카드 데이터 (완료)
    ├── session2_effects_audit.md   # 검수: effects 구조화 (완료)
    ├── session3_rulebook_audit.md  # 검수: 룰북 게임 흐름 (완료)
    └── session4_simplifications.md # 검수: 단순화 판정 (완료)
```

## 실행 방법

```bash
# 1) 사람 vs 봇 대전 (CLI)
python3 cli/play.py                        # 유리나(나) vs 토코요(봇)
python3 cli/play.py saine himika           # 여신 지정
python3 cli/play.py yurina saine --hotseat # 사람 vs 사람 (스팀판 대조 검증에 추천)
python3 cli/play.py yurina tokoyo --seed 7 # 시드 고정

# 2) 룰 정확성 테스트
python3 tests/run_tests.py                 # 또는: python3 -m pytest tests/ -v

# 3) 봇 자동 대전 데모 + 스트레스
python3 ai/demo_combat.py
```

## 현재 상태 (2026-07-05)

- Phase 0 (데이터) ✅ / Phase 1 (엔진) ✅ / Phase 2 (FAQ 검증 28건) ✅
- 검수 세션 1~4 ✅ (오류 7건 수정) / 세션 5 (CLI 실전 검증) 도구 완비
- Phase 3 ✅ 휴리스틱 봇 (vs 랜덤 97%) + 자동 리그전/기보
- Phase 4-1 ✅ 결정화 몬테카를로 봇 (vs 휴리스틱 62%) + recommend() 추천 API
  - `python3 ai/mcts.py` — 상황별 추천 수 Top3 + 승률 데모
  - `python3 ai/league.py --games 200` — 봇 리그전
- Phase 4-2 △ UCT 트리 탐색 구현 (튜닝 실측: 휴리스틱 동급, 반복 증량 무효 — 구조 개선 과제)
  - 실측 기록과 개선 방향: ai/tune_uct.py 상단 주석 참조
- Phase 4 튜닝 ✅ MC 하이브리드 K=48이 K=24를 69%로 압도 → recommend()/훈수 기본값 상향
- CLI 훈수 모드 ✅ — 대전 중 `r` 입력 시 AI 추천 Top3 + 승률
- C-1 ✅ 반복 자기대국 루프 착수 (`ai/run_loop.py`) — 다양성 데이터 + NetMCBot
  (가치망을 MC 리프 평가로 사용) + on-policy 재생성 루프. 아키텍처 수정으로
  가치망 활용이 B-4의 10%→50%대(vs heuristic)로 개선. 상세: docs/session_c1_selfplay_loop.md
  - `python -m ai.run_loop --iters 2 --gen-games 150` (샌드박스 동작 증명)
  - 대규모는 로컬: `--onpolicy --gen-games 4000 --iters 6 --eval-vs-mc`
- 챔피언: MonteCarloBot(K=48) 유지. NetMCBot(`netmc`)은 등록됨, 대규모 학습 후 재평가 대상
- 다음: 루프 로컬 대규모 실행 / UCT 튜닝 / v2 여신 확장

## v1 스코프 메모

- 단일 여신 미러/교차 덱 (통상 7 + 비장 3), 안전구축·무다시는 v2
- 알려진 단순화는 docs/session4_simplifications.md 판정 기록 참조
- 룰북/FAQ 원문 md는 용량 관계로 미포함 (별도 보관)
