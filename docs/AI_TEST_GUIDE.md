# AI 테스트 실행 가이드

봇끼리 대결시키고 승률을 측정하는 방법. (Windows 기준, `python3` 대신 `python` 사용 가능)

## 1. 빠른 두 봇 대결

```bash
# 휴리스틱 vs 랜덤, 50판 (단일 여신)
python ai/league.py --bot0 heuristic --bot1 random --games 50

# 2여신 덱 모드
python ai/league.py --bot0 heuristic --bot1 aggressive --games 50 --two
```

출력 예:
```
[2여신] heuristic vs aggressive: 42승 8패 0무 / 50판 → heuristic 승률 84.0%
  종료 사유: {'life_zero': 50}
  소요: 0.5초
```

## 2. 사용 가능한 봇

| 이름 | 설명 | 강도 |
|---|---|---|
| `random` | 무작위 | 최약 |
| `aggressive` | 공격 선호 무작위 | 약 |
| `heuristic` | 1수 앞 그리디 (평가함수) | 중 |
| `mc24` | 몬테카를로 K=24 | 강 |
| `mc48` | 몬테카를로 K=48 (챔피언) | 최강 |
| `uct` | UCT 트리탐색 | 중 (실험) |
| `uct_leaf` | UCT 리프평가 | 실험 |
| `lookahead` | 2수 앞 | 실험 (약함) |
| `gemini` | Gemini LLM | API 키 필요 |

**봇 서열 (실측)**: mc48 > mc24 > heuristic ≈ uct > aggressive > random

주의: mc24/mc48은 한 수마다 다수 시뮬레이션을 돌려 **느립니다**
(50판에 수 분). 빠른 확인은 heuristic 이하 봇으로.

## 3. 리그 (여러 봇 순위 한 번에)

```bash
python ai/league.py --league --games 30 --two
```
기본 매치업(heuristic vs random/aggressive, mc24/mc48 vs heuristic 등)을
순서대로 실행합니다.

## 4. 옵션

| 옵션 | 설명 |
|---|---|
| `--bot0`, `--bot1` | 대결할 두 봇 |
| `--games N` | 판 수 (기본 50) |
| `--two` | 2여신 덱 모드 (없으면 단일 여신) |
| `--seed N` | 시드 베이스 (재현용) |
| `--league` | 기본 리그 실행 |
| `--out path.jsonl` | 기보 저장 (run_game record 경로) |

## 5. Gemini LLM으로 대결

Gemini가 실제로 플레이하려면 API 키가 필요합니다.

```bash
# 1) 패키지 설치
pip install google-generativeai

# 2) API 키 설정 (https://aistudio.google.com/apikey)
set GOOGLE_API_KEY=your-key-here      # Windows CMD
$env:GOOGLE_API_KEY="your-key-here"   # Windows PowerShell
export GOOGLE_API_KEY="your-key-here" # Mac/Linux

# 3) Gemini vs 휴리스틱 (판 수는 적게! rate limit 주의)
python ai/league.py --bot0 gemini --bot1 heuristic --games 5 --two
```

**주의**:
- API 키가 없으면 Gemini 봇은 자동으로 휴리스틱으로 폴백합니다(50% 근처).
- 무료 등급은 분당 요청 제한이 있어 **판 수를 적게**(5~10판) 하세요.
- Gemini 봇의 상세 로그를 보려면 `demo_gemini.py`를 쓰세요:
  ```bash
  python ai/demo_gemini.py
  ```
  → 프롬프트 예시 + 한 판 진행 + 호출 통계 출력.

자세한 Gemini 설정은 `docs/GEMINI_GUIDE.md` 참조.

## 6. 파이썬에서 직접 호출

```python
import sys
sys.path.insert(0, "engine")
sys.path.insert(0, ".")
from ai.league import duel

# 두 봇 대결, 결과 dict 반환
result = duel("mc48", "heuristic", n=20, two_megami=True)
print(result)   # {'bot0':'mc48', 'bot1':'heuristic', 'wins':[..], 'winrate':..}
```

## 7. 스트레스 테스트 (버그 확인)

봇 대결이 아니라 엔진 안정성 확인용:
```bash
python tests/run_tests.py          # FAQ 테스트 110건
```

91페어링 등 대량 스트레스는 세션 문서(docs/) 참고.
