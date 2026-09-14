"""
안전구축 덱 선택 정책 (2여신)

두 여신의 카드 풀(통상 14장 + 비장 8장)에서 통상 7 / 비장 3을 선택.
기계적 앞자르기 대신, 카드 가치를 점수화해서 상위 조합을 뽑되
여신 메커니즘이 작동할 최소 카드를 확보한다.

핵심:
- score_card(): 카드 1장의 범용 가치 (데미지/거리/효과/여신 시너지)
- pick_deck(): 통상 7 + 비장 3 선택. 거리 커버리지 + 메커니즘 보장 휴리스틱.

rng를 주면 점수에 비례한 확률 표집으로 고른다 (rng=None이면 종전대로 점수
상위를 확정적으로 자른다). 같은 여신 조합이 늘 같은 10장이 되던 문제를 없애
자기대국 데이터에 덱 다양성을 만들고, "상대가 고를 법한 덱"의 분포를 제공한다.
표집도 rng가 결정하므로 seed를 고정하면 그대로 재현된다.
"""

import math
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))

from setup import CARD_DB, cards_of  # noqa: E402
from combat import parse_range, parse_damage  # noqa: E402


# 표집 온도. 작을수록 점수 상위에 쏠리고, 클수록 고르게 섞인다.
# 카드 점수는 대략 0~12 범위라 1.2면 상위권 안에서만 흔들린다.
SAMPLE_TEMP = 1.2


# ─── 여신별 "메커니즘 핵심" 카드 태그 ───
# 이 카드들은 여신의 신규 시스템을 돌리는 데 중요 → 우선 확보
_MECH_KEYWORDS = {
    "korunu": ["freeze", "konru_freeze_if_aura", "jeoldae_gather"],  # 동결
    "megumi": ["growth", "ingwa_sprout"],   # 씨앗 (생육/발아)
    "chikage": ["plant_poison", "plant_myeoldeung"],  # 독 심기
    "raira": ["gain_gauge", "poongma_summon", "poongnoe_x"],  # 게이지
    "thallya": ["kidou", "burn", "julia_transform"],  # 조화결정
    "shinra": ["execute_scheme", "seal_from_discard"],  # 계략/봉인
    "kururu": ["gigyo", "industria"],       # 기교
    "yukihi": ["umbrella_toggle"],          # 우산
    "oboro": ["setup"],                     # 설치
    "hagane": ["centrifugal"],              # 원심
}


def _damage_value(c) -> float:
    """공격 카드의 데미지 잠재력 (오라+라이프 가중)."""
    if c["type"] != "attack" or not c.get("damage"):
        return 0.0
    try:
        a, l = parse_damage(c["damage"])
    except Exception:
        return 0.0
    a = a if isinstance(a, int) else 2   # X 데미지는 중간값 가정
    l = l if isinstance(l, int) else 2
    return a * 1.0 + l * 2.0             # 라이프가 더 귀함


def _range_span(c):
    """공격 카드의 적정거리 집합 (없으면 빈)."""
    if c["type"] != "attack" or not c.get("range"):
        return set()
    try:
        return parse_range(c["range"])
    except Exception:
        return set()


def _mech_bonus(cid, c, megamis) -> float:
    """여신 메커니즘 기여 보너스."""
    meg = c["megami"]
    if meg not in megamis:
        return 0.0
    keys = _MECH_KEYWORDS.get(meg, [])
    if not keys:
        return 0.0
    # 카드 효과/플래그/커스텀 id에 키워드가 있으면 보너스
    blob = str(c.get("effects") or "") + str(c.get("card_flags") or "")
    hit = sum(1 for k in keys if k in blob)
    return 2.0 * hit


def score_card(cid, megamis) -> float:
    """카드 1장의 범용 가치 점수."""
    c = CARD_DB[cid]
    s = 0.0
    # 데미지 잠재력
    s += _damage_value(c)
    # 거리 폭 (넓을수록 유연) — 단 과대평가 방지 위해 로그성 가중
    span = len(_range_span(c))
    s += min(span, 5) * 0.4
    # 효과 보유 (범용 유틸)
    if c.get("effects"):
        s += 1.0
    # 비장패는 강력하나 비용 존재 → 약간 가산
    if c.get("base_type") == "special":
        s += 1.5
    # 대응 카드 (방어 옵션)
    if c.get("subtype") == "reaction":
        s += 1.2
    # 여신 메커니즘 기여
    s += _mech_bonus(cid, c, megamis)
    return s


def pick_deck(meg_a, meg_b, rng=None):
    """
    통상 7 / 비장 3 선택.
    1) 각 카드 점수화
    2) 통상: 7장 (단, 각 여신 최소 2장 확보로 시너지 유지)
    3) 비장: 3장 (각 여신 최소 1장)

    rng=None이면 점수 상위를 확정적으로 자른다 (종전 동작).
    rng를 주면 점수에 비례해 확률적으로 뽑아 덱 다양성을 만든다.
    반환: (normal_picks[7], special_picks[3])
    """
    megamis = [meg_a, meg_b]
    na, sa = cards_of(meg_a)
    nb, sb = cards_of(meg_b)

    def _score(cid):
        return score_card(cid, megamis)

    # ── 통상 7장 ──
    normals = _balanced_pick(list(na), list(nb), 7, _score, min_each=2, rng=rng)
    # ── 비장 3장 ──
    specials = _balanced_pick(list(sa), list(sb), 3, _score, min_each=1, rng=rng)
    return normals, specials


def _take(pool, k, score_fn, rng):
    """pool에서 k장 꺼낸다. rng=None이면 점수 상위, 아니면 점수 비례 표집."""
    if rng is None:
        return sorted(pool, key=score_fn, reverse=True)[:k]
    picked = []
    rest = list(pool)
    while rest and len(picked) < k:
        scores = [score_fn(c) for c in rest]
        top = max(scores)
        weights = [math.exp((s - top) / SAMPLE_TEMP) for s in scores]
        r = rng.random() * sum(weights)
        acc = 0.0
        idx = len(rest) - 1
        for i, w in enumerate(weights):
            acc += w
            if acc >= r:
                idx = i
                break
        picked.append(rest.pop(idx))
    return picked


def _balanced_pick(pool_a, pool_b, k, score_fn, min_each, rng=None):
    """
    두 여신 풀에서 k장 선택. 각 여신 최소 min_each장 보장 후 나머지를 채운다.
    """
    picked = []
    picked += _take(pool_a, min_each, score_fn, rng)
    picked += _take(pool_b, min_each, score_fn, rng)
    rest = [c for c in list(pool_a) + list(pool_b) if c not in picked]
    picked += _take(rest, k - len(picked), score_fn, rng)
    return picked[:k]


# ─── 에이전트 훅용: 안전구축 선택 ───
def choose_safe_build(meg_a, meg_b, rng=None):
    """new_game_2v2의 picks 인자로 넘길 (normal_picks, special_picks)."""
    return pick_deck(meg_a, meg_b, rng)
