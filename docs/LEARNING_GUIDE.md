# 학습 데이터 파이프라인 (B-1)

알파고 방향의 첫걸음: 손으로 짠 평가함수 대신 **데이터로 승률을 학습**하는 가치망.

## 구성

```
상태 → [features.py] → 67차원 특징벡터
      ↓
[selfplay.py] 자기대국 → (특징, 최종승패) 샘플 수집 → jsonl/npz
      ↓
[train_value.py] 가치망 학습 (numpy) → state → 승률 예측 모델
```

## 1. 특징 인코딩 (features.py)

게임 상태를 **67차원 고정 벡터**로 변환. 여신 조합·손패 내용과 무관하게
같은 차원 (집계 특징 사용). me 관점, 값은 [0, 1.2] 정규화.

```python
from ai.features import encode_state, feature_dim
vec = encode_state(state, me=0)   # list[float], 길이 67
```

특징 구성: 전역(간격/더스트/턴), 내 자원(라이프/오라/플레어/손패 타입 구성/
여신 자원), 상대 자원(대칭).

## 2. 자기대국 데이터 생성 (selfplay.py)

```bash
# 200판 2여신 자기대국 → jsonl
python ai/selfplay.py --games 200 --bot heuristic --two --out data/selfplay.jsonl

# npz로 (numpy 있으면 학습 로드 빠름)
python ai/selfplay.py --games 1000 --bot heuristic --two --out data/selfplay.npz
```

각 샘플: `{"x": [특징 67개], "y": 1.0(승)/0.0(패)/0.5(무)}`
- 판당 약 70~90 샘플 (각 메인 행동 시점)
- heuristic 봇: 200판 ~6초. mc 계열은 훨씬 느림.

**대량 생성은 로컬에서** (수천~수만 판). 데이터가 많을수록 가치망이 좋아짐.

## 3. 가치망 학습 (train_value.py, numpy)

```bash
python ai/train_value.py --data data/selfplay.jsonl --model mlp --epochs 40
```

- `--model logreg`: 로지스틱 회귀 (빠른 베이스라인)
- `--model mlp`: 1-은닉층 MLP (기본, hidden=32)
- 순수 numpy 수동 역전파 → 어디서나 동작 (PyTorch 불필요)

**실측 성과** (200판=17022샘플, MLP):
- 베이스라인(항상 0.5): val_loss 0.693, 정확도 50%
- 학습 후: val_loss 0.51, **정확도 73%**
- 예측 검증: 내 라이프10 vs 상대1 → 98% 승률 / 반대 → 4.5%

특징 중요도(로지스틱): my_life +2.45, opp_life -2.62가 최상위 (게임 지식과 일치).
흥미롭게도 my_flare가 음(-)의 가중치 — "플레어만 쌓고 라이프 못 지킨" 패턴을
데이터가 포착 (수제 평가가 놓친 미묘함).

## 4. 학습된 모델 사용

```python
import numpy as np
from ai.train_value import ValueModel
from ai.features import encode_state

model = ValueModel.load("data/value_model.npz")
winrate = model.predict(np.array([encode_state(state, 0)]))[0]
```

## 다음 단계 (B-2~B-4)

- **B-2**: 로컬에서 대량 자기대국 (수만 판) → 더 강한 데이터셋
- **B-3**: PyTorch로 더 큰 신경망 (train_value_torch.py 참고, 로컬 GPU)
- **B-4**: 학습된 가치망을 evaluate()에 이식 → MC봇과 A/B 대결로 검증
  (기존 손짠 평가를 넘는지 실측)

## 한계와 주의

- 현재 데이터는 heuristic 자기대국 → 그 봇 수준의 판단이 상한.
  더 강한 봇(mc48)이나 사람 기보로 학습하면 더 좋아짐.
- 세션 환경은 일시적이라 대량 데이터·모델은 로컬 보관 권장.
- 이 파이프라인은 "학습 가능성 증명" 단계. 실전 강화는 B-2 이후.
