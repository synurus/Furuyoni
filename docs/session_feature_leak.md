# 세션: 가치망 특징의 비공개 정보 누수 (2026-09-14)

## 요약

`ai/features.py`가 **상대 손패의 타입 구성**을 특징 벡터에 넣고 있었다.
학습 데이터는 진짜 손패로 만들고, 추론은 `mcts.determinize()`가 무작위로
섞어놓은 가짜 손패로 하기 때문에, 67차원 중 5칸이 **추론 시점에 잡음**이었다.
상대 쪽 5칸을 0으로 고정해 학습과 추론이 같은 정보만 보도록 고쳤다.

## 발견 경위

"이 파이프라인으로 후루요니판 알파고를 만들 수 있는가"를 검토하던 중,
후루요니가 불완전정보 게임이라는 점에서 특징 인코더가 볼 수 없는 정보를
쓰고 있지 않은지 확인하다 나왔다.

## 문제

`encode_state`는 내 특징과 상대 특징을 같은 함수로 만든다.

```python
mf, mn = _player_features(state, me)
of, on = _player_features(state, 1 - me)     # ← 상대에게도 그대로
```

그 안에 손패 내용을 읽는 부분이 있었다.

```python
def _zone_type_counts(state, pidx):
    for cid in p.hand + p.specials:          # ← 실제 카드 id를 순회
        counts[c["type"]] += 1
```

결과적으로 `opp_n_attack` `opp_n_action` `opp_n_enhance` `opp_n_reaction`
`opp_n_fullpower` 다섯 개가 상대 손패의 실제 구성이었다.

나머지 상대 특징은 문제없다. 라이프·오라·플레어·기세·간격은 공개 정보고,
손패/패산/덮음패는 **장수만** 쓴다.

### 왜 단순한 치팅보다 나쁜가

`mcts.determinize()`는 제대로 만들어져 있다. 상대의 손패·패산·덮음패를 한 풀에
섞어 다시 나눈다. 그래서 `NetMCBot._leaf_eval`이 받는 `sim`은 **가짜 손패**를 든다.

| | `opp_n_*` 값 |
|---|---|
| 학습 (`selfplay`가 실제 대국 상태를 인코딩) | 진짜 — 승패와 상관 있음 |
| 추론 (`_leaf_eval`이 결정화된 sim을 인코딩) | 샘플 — 상관 없음 |

가치망은 "상대 손에 공격이 많으면 위험"을 진짜 데이터로 배운 뒤, 쓸 때는
주사위 값을 받는다. **데이터를 늘릴수록 그 잡음 특징을 더 확신 있게 쓰게 되므로
판 수로는 해결되지 않는다.**

NetMCBot이 heuristic 상대 50~55%에서 멈춘 원인의 후보다. 확정은 아니다 —
규모 부족일 가능성도 여전히 남아 있다.

## 수정

`_player_features`에 `hide_hand` 플래그를 추가하고 상대에게만 켠다.
차원은 67로 유지해 기존 코드 경로를 건드리지 않는다.

```python
def _player_features(state, pidx, hide_hand=False):
    ...
    tc = ({"attack": 0, "action": 0, "enhance": 0, "reaction": 0, "fullpower": 0}
          if hide_hand else _zone_type_counts(state, pidx))
...
of, on = _player_features(state, 1 - me, hide_hand=True)
```

학습 데이터도 결정화된 상태에서 뽑는 방법이 있었지만, 라벨에 잡음을 더하는
쪽이라 택하지 않았다.

## 검증

- `feature_dim()` = 67 (변화 없음)
- `opp_n_*` 5개가 전부 0
- `my_n_*`와 상대 공개 특징은 그대로
- **상대 손패를 다른 카드로 통째로 바꿔도 벡터가 한 칸도 변하지 않음** — 누수 없음
- `tests/run_tests.py` 117/0/0

## 영향

이 수정 이전에 학습된 모델은 **특징의 의미가 달라졌으므로 재학습 대상**이다.

- `data/value_model.npz`, `data/value_model_v2.npz` — `NetMCBot`의 기본 `model_path`가
  `value_model_v2.npz`라, 모델을 명시하지 않고 쓰면 옛 정의로 학습된 망이 로드된다
- `data/loop/`, `data/loop_onpolicy/`, `data/loop_probe/` 의 `net_iter*.npz`

전부 소규모 실험 산출물이라 버리는 비용은 크지 않다.

## 다음 검증

같은 설정으로 다시 돌려 **heuristic 상대 승률이 50~55%를 넘는지** 본다.

- 넘으면 → 누수가 정체의 원인이었다
- 그대로면 → 원인은 다른 곳. 규모 부족이거나, 아래의 구조적 천장

## 남은 것 — PIMC의 구조적 천장

결정화 몬테카를로(PIMC)에는 알려진 두 병리가 있고, 이번 수정으로 사라지지 않는다.

- **strategy fusion** — 결정화마다 다른 수를 둘 수 있다고 암묵적으로 가정한다.
  실제로는 어느 세계인지 모르는 채 하나의 수를 둬야 한다
- **non-locality** — 정보를 숨기거나 흘리는 행동의 가치를 평가하지 못한다

실전에서는 블러프를 못 하고 상대 손패를 역추론하지 못하는 형태로 나타난다.
이 벽을 넘으려면 정보집합과 믿음을 다루는 계열(CFR / Deep CFR / ReBeL)로
가야 하며, 지금 파이프라인의 연장선이 아니다.

## 정직한 결론

- ✅ 볼 수 없는 정보를 보고 있던 것을 찾아 고쳤고, 누수가 없음을 실측으로 확인했다
- ⚠️ 이것이 성능 정체의 원인인지는 **아직 모른다**. 재학습 결과가 말해준다
- ➡️ 원인이 아니었다면 남은 후보는 규모, 그다음이 PIMC의 구조적 한계다
