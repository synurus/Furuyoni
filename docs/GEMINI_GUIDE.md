# Gemini LLM 에이전트 사용법

실제 Gemini(Google의 LLM)가 후루요니를 플레이하는 에이전트입니다.

## 준비

### 1. 패키지 설치
```bash
pip install google-generativeai
```

### 2. API 키 발급
- https://aistudio.google.com/apikey 에서 무료 API 키 발급
- 환경변수로 설정 (권장):
  ```bash
  # Windows (PowerShell)
  $env:GOOGLE_API_KEY="your-key-here"
  # Windows (CMD)
  set GOOGLE_API_KEY=your-key-here
  # Mac/Linux
  export GOOGLE_API_KEY="your-key-here"
  ```
- 또는 코드에서 직접 전달: `GeminiBot(api_key="your-key")`

## 사용

### 기본 사용
```python
import sys
sys.path.insert(0, "engine")
from setup import new_game_2v2
from turn import play_turn
from ai.gemini_agent import GeminiBot
import random

# Gemini vs 자기 자신 한 판
rng = random.Random(0)
s = new_game_2v2(("yurina", "saine"), ("himika", "tokoyo"), seed=0)
bot = GeminiBot(model="gemini-2.0-flash", verbose=True)

while not s.is_over():
    play_turn(s, rng, bot)

print(f"승자: P{s.winner}")
print(f"Gemini 호출 통계: {bot.stats()}")
```

### Gemini vs 휴리스틱/MCTS 대결
```python
from ai.heuristic import HeuristicBot
from ai.gemini_agent import GeminiBot

gemini = GeminiBot(verbose=True)
opponent = HeuristicBot(seed=1)

while not s.is_over():
    bot = gemini if s.active == 0 else opponent
    play_turn(s, rng, bot)
```

### 데모 스크립트 실행
```bash
python ai/demo_gemini.py
```

## 동작 방식

1. **주요 행동 + 전략적 세부 선택을 LLM에게**:
   - `choose_main_action`(카드 사용/기본동작/종료)
   - `choose_damage_type`(오라로 막기 vs 라이프로 받기 — 방어의 핵심)
   - `choose_reaction`(대응 카드 사용 여부 — 게임을 바꾸는 선택)
   나머지 사소한 선택은 내장 휴리스틱을 사용해 API 호출을 절약합니다.

2. **상태 요약 + 전술 힌트**: 게임 상태를 한국어로 요약하고,
   LLM 판단을 돕는 전술 힌트(지금 명중하는 내 공격, 상대 압박 상태,
   이번 턴 처치 기회, 내 위험 경고)를 함께 제공합니다.
   불완전정보를 존중해 '나' 관점에서 보이는 정보만 노출.

3. **폴백**: 다음 경우 자동으로 휴리스틱 봇으로 대체 (게임이 멈추지 않음):
   - API 키 미설정 / 패키지 미설치
   - API 호출 실패 (네트워크, rate limit 등)
   - 이상 응답 (숫자 파싱 실패, 범위 밖 번호)

   `bot.stats()`로 호출 수 / 폴백 수를 확인할 수 있습니다.

## 주의사항

- **무료 등급 rate limit**: 분당 요청 수 제한이 있어 대량 self-play에는
  부적합합니다. 관전/해설/소수 대국용으로 설계되었습니다.
- **모델 선택**: `model="gemini-2.0-flash"`(빠름/저렴) 기본. 더 강한 플레이를
  원하면 `gemini-2.0-pro` 등으로 교체 가능 (비용/속도 트레이드오프).
- **temperature**: 기본 0.7. 낮추면(0.2) 더 일관적, 높이면 더 창의적/불안정.

## 해설 기능 (선택)

MCTS나 휴리스틱이 고른 수의 이유를 Gemini에게 한국어로 설명받을 수 있습니다:
```python
from ai.gemini_agent import GeminiExplainer
explainer = GeminiExplainer()
reason = explainer.explain(state, pidx, "카드 사용: 거합 (공격 거리2-4 4/3)")
print(reason)
```
