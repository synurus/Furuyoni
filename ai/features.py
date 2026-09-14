"""
상태 → 고정 길이 특징 벡터 (학습용).

가치망 학습의 입력을 만든다. 여신 조합·손패 내용과 무관하게 항상 같은 차원의
벡터를 반환하도록 설계 (집계 특징 사용). me 관점으로 인코딩하며,
대칭 항목은 (내 값, 상대 값) 쌍으로 넣는다.

핵심 설계:
- 개별 카드 id는 넣지 않음 (여신마다 달라 차원 폭발) → 타입별 개수로 집계.
- 여신별 신규 자원은 "그 여신을 가졌을 때만 유효"하지만, 차원 고정을 위해
  항상 슬롯을 두고 없으면 0.
- 값은 대략 [0,1] 스케일로 정규화 (학습 안정).
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))

from setup import CARD_DB, megamis_of        # noqa: E402
from combat import effective_distance         # noqa: E402
from constants import (INITIAL_LIFE, INITIAL_AURA,  # noqa: E402
                       AURA_MAX, TOTAL_TOKENS)


# 특징 이름 (디버깅/해석용). FEATURE_DIM은 자동 계산.
FEATURE_NAMES = []


def _zone_type_counts(state, pidx):
    """플레이어의 손패+비장의 타입별 개수 (공격/행동/부여/대응/전력)."""
    p = state.players[pidx]
    counts = {"attack": 0, "action": 0, "enhance": 0,
              "reaction": 0, "fullpower": 0}
    for cid in p.hand + p.specials:
        c = CARD_DB[cid]
        counts[c["type"]] = counts.get(c["type"], 0) + 1
        if c.get("subtype") in ("reaction", "fullpower"):
            counts[c["subtype"]] += 1
    return counts


def _player_features(state, pidx, hide_hand=False):
    """플레이어 한 명의 특징 (정규화된 float 리스트)와 이름.

    hide_hand=True면 손패/비장의 타입 구성을 0으로 채운다 (상대용).
    """
    p = state.players[pidx]
    feats = []
    names = []

    def add(name, val):
        # 학습 안정을 위해 [0, 1.2]로 클립 (약간의 여유 허용)
        v = float(val)
        if v < 0.0:
            v = 0.0
        elif v > 1.2:
            v = 1.2
        feats.append(v)
        names.append(name)

    # 기본 자원 (정규화)
    add("life", p.life / INITIAL_LIFE)
    add("aura", p.aura / AURA_MAX)
    add("flare", p.flare / 10.0)
    add("vigor", p.vigor / 2.0)
    add("withered", 1.0 if p.withered else 0.0)

    # 카드 영역 크기
    add("hand", len(p.hand) / 7.0)
    add("deck", len(p.deck) / 7.0)
    add("discard", len(p.discard) / 10.0)
    add("covered", len(p.covered) / 5.0)
    add("specials", len(p.specials) / 4.0)
    add("used_specials", len(p.used_specials) / 4.0)
    add("enhancements", len(p.enhancements) / 3.0)
    add("enh_tokens", sum(e.tokens for e in p.enhancements) / 10.0)

    # 손패+비장 타입 구성.
    # 상대의 것은 실전에서 볼 수 없다. 예전에는 실제 값을 넣었는데, 학습은
    # 진짜 손패로 하고 추론은 mcts.determinize()가 섞어놓은 가짜 손패로 하게 되어
    # 이 다섯 칸이 추론 시점에 잡음이었다. 차원 유지를 위해 슬롯은 남기고 0으로 둔다.
    tc = ({"attack": 0, "action": 0, "enhance": 0, "reaction": 0, "fullpower": 0}
          if hide_hand else _zone_type_counts(state, pidx))
    add("n_attack", tc["attack"] / 7.0)
    add("n_action", tc["action"] / 7.0)
    add("n_enhance", tc["enhance"] / 7.0)
    add("n_reaction", tc["reaction"] / 4.0)
    add("n_fullpower", tc["fullpower"] / 4.0)

    # 여신별 신규 자원 (없으면 0 — 차원 고정)
    megs = megamis_of(p)
    add("fuujin", p.fuujin / 20.0 if "raira" in megs else 0.0)
    add("raijin", p.raijin / 20.0 if "raira" in megs else 0.0)
    add("taisen", len(p.taisen_cards) / 5.0 if "raira" in megs else 0.0)
    add("machine_harmony", p.machine_harmony / 5.0
        if "thallya" in megs else 0.0)
    add("burned_harmony", p.burned_harmony / 5.0
        if "thallya" in megs else 0.0)
    add("gap_harmony", (p.gap_minus_harmony + p.gap_plus_harmony) / 5.0
        if "thallya" in megs else 0.0)
    add("soil_sprouted", p.soil_sprouted / 5.0 if "megumi" in megs else 0.0)
    add("soil_unsprouted", p.soil_unsprouted / 5.0
        if "megumi" in megs else 0.0)
    add("frozen", p.frozen / 5.0 if "korunu" in megs else 0.0)
    add("poison", len(p.poison_pouch) / 5.0 if "chikage" in megs else 0.0)
    add("scheme_kimou", 1.0 if ("shinra" in megs and p.scheme == "kimou")
        else 0.0)
    add("sealed", len(p.sealed_cards) / 4.0 if "shinra" in megs else 0.0)
    add("umbrella", 1.0 if ("yukihi" in megs and p.umbrella_open) else 0.0)

    return feats, names


def encode_state(state, me: int):
    """
    me 관점의 고정 길이 특징 벡터. (list[float] 반환)
    구성: 공유 특징 + 내 특징 + 상대 특징.
    """
    feats = []
    names = []

    # ── 공유(전역) 특징 ──
    feats.append(effective_distance(state) / 10.0)
    names.append("eff_distance")
    feats.append(state.distance / 10.0)
    names.append("raw_distance")
    feats.append(state.dust / TOTAL_TOKENS)
    names.append("dust")
    feats.append(1.0 if state.active == me else 0.0)
    names.append("my_turn")
    feats.append(min(state.turn_count, 30) / 30.0)
    names.append("turn_count")

    # ── 내 특징 → 상대 특징 ──
    mf, mn = _player_features(state, me)
    of, on = _player_features(state, 1 - me, hide_hand=True)
    feats += mf
    names += ["my_" + n for n in mn]
    feats += of
    names += ["opp_" + n for n in on]

    # 전역 FEATURE_NAMES 캐시 (첫 호출 시)
    if not FEATURE_NAMES:
        FEATURE_NAMES.extend(names)
    return feats


def feature_dim():
    """특징 벡터 차원 (한 번 인코딩해서 확정)."""
    from setup import new_game
    s = new_game("yurina", "saine", seed=0)
    return len(encode_state(s, 0))
