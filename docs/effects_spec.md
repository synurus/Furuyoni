# 후루요니 효과 DSL 명세 v1 (기본 4여신)

엔진이 카드 효과를 해석·실행하기 위한 구조 정의. `cards_core4.json`의 `effects` 필드에 적용.

## 효과 항목 구조
```json
{ "trigger": "...", "condition": "...", "ops": [ ... ] }
```

## trigger (효과가 발동하는 시점)
| 값 | 의미 | 룰북 대응 |
|---|---|---|
| `main` | 카드 본문 즉발 효과 (행동카드 본문, 공격카드 부가문) | 즉시 해결 |
| `constant` | 【상시】 | 9-1-1-2 |
| `attack_after` | 【공격후】 | 공격 해결 후 |
| `deploy_start` | 【전개시】 | 부여패 전개 시 |
| `deploy_during` | 【전개중】 | 전개된 동안 지속 |
| `destroy` | 【파기시】 | 부여패 파기 시 |

## condition (조건, null이면 무조건)
| 값 | 의미 |
|---|---|
| `kessa` | 결사: 자신 라이프 ≤ 3 |
| `hassou` | 팔상: 자신 오라 ≤ 1 |
| `kyouchi` | 경지: 자신 집중력 = 2 |
| `renka` | 연화: 이 턴에 사용한 3장째 이후의 카드 |
| `{"expr": "..."}` | 개별 조건식 (예: `dust == 0`, `current_range <= 2`, `my_life >= 2`, `my_life < opp_life`, `hand_count == 0`, `current_range == 0`) |

## ops (실행 연산)
### 결정 이동 (게임의 근간)
`{"op": "move", "from": ZONE, "to": ZONE, "n": 정수, "optional": bool, "up_to": bool}`
- ZONE: `dust`, `distance`(간격), `my_aura`, `opp_aura`, `my_flare`, `opp_flare`, `my_life`, `opp_life`, `this_card`(이 부여패 위)
- `move_either`: 양방향 선택 (간파의 ⇔)

### 공격
`{"op": "attack", "range": "X-Y", "dmg": "a/l", "flags": [...]}` — 카드에 의하지 않는 공격
- flags: `no_reactions`(대응불가) 등

### 공격 수정 (버프/디버프)
`{"op": "buff", "target": T, "aura": ±n, "life": ±n, "gains": [...], "scope": S}`
- target: `this_attack`, `next_attack`, `next_attack_other_megami`, `reacted_attack`, `my_attacks_other_megami`(지속), `opp_first_attack_each_turn`
- gains: `far_extend_1`(거리확대 원1), `chokyoku`(초극), `no_reactions`
- scope: `this_turn`, `while_deployed`
- 제약: `{"only_if_aura_dmg_le": 3}` (백드래프트)

### 기타 연산
| op | 의미 | 파라미터 |
|---|---|---|
| `draw` | 카드 뽑기 | n, optional |
| `cover_from_hand` | 손패→덮음패 | n |
| `opp_discard_non_attack` | 상대 손패의 비공격 카드 버리기 (불가 시 공개) | n |
| `gain_vigor` | 집중력 획득 | n |
| `set_vigor` | 집중력 설정 | who(`me`/`opp`), n |
| `wither` | 위축시키기 | who |
| `basic_actions` | 기본동작/기본행동 수행 | n, up_to |
| `cancel_reacted_attack` | 대응한 공격 무효화 | non_special(비장패 제외 여부) |
| `return_this_to_deck_top` | 이 카드를 패산 위로 | |
| `reconstruct_deck` | 패산 재구성 | no_damage |
| `deck_bottom_from_discard_or_cover` | 버림패/덮음패에서 골라 패산 밑으로 | n, up_to |
| `choose` | 선택지 중 하나 실행 | options: [ops배열, ...] |
| `custom` | 엔진 직접 구현 특수효과 | id |

### custom ID 목록 (엔진 하드코딩 대상)
| id | 카드 | 내용 |
|---|---|---|
| `muonheki_aura` | 무음벽 | 이 카드 위 결정을 오라처럼 취급 |
| `kwonyeok_redirect` | 권역 | 이 카드에서 더스트로 갈 결정 → 간격으로 |
| `kwonyeok_master_range` | 권역 | 달인의 간격 +1 |
| `appdo_reduce` | 압도 | 각 턴 상대 첫 공격 오라뎀 1 경감 |
| `reloading_draw` | 리로딩 | 종료 페이즈 손패 0이면 1드로우 가능 |
| `full_burst_both` | 풀 버스트/크림슨 제로 | 데미지 양쪽 모두 적용 |
| `usable_as_reaction` | 간파 | 팔상 시 대응처럼 사용 가능 |
| `reaction_to_special_only` | 종극 | 비장패 대응으로만 사용 가능 |
| `unusable_unless_kessa` | 저력 | 결사 아니면 사용 불가 |
| `cost_reduce_by_opp_aura` | 항명공진 | 소모값 -상대오라 |

## 카드 레벨 flags (효과가 아닌 카드 속성)
`card_flags`: `terminal`(종단), `gap`(빈틈), `no_reactions`(대응불가)

## 재기 (비장패 재사용 조건)
`"saiki": {"condition": ..., "immediate": bool}` — 즉재기는 immediate: true


> 용어 정리(세션2 검수): 대응불가 = `no_reactions` (대응 창 차단). 무효화(cancel)와는 별개 개념.
