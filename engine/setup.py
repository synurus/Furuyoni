"""
게임 셋업 (룰북 4-1 벚꽃결투의 준비)

v1 단순화:
- 쌍장요란/안전구축 생략. 단일 여신 프리셋 덱 사용.
- 통상패 7장 전부 + 비장패 4장 중 앞 3장 = 룰의 7/3 구성 충족.
- 첫 손패 조정(4-1-1 과정 5, 무다시)은 생략(선택 과정이라 봇은 skip).
"""

import json
import random
import os

from state import GameState, PlayerState
from constants import (
    INITIAL_AURA, INITIAL_LIFE, INITIAL_DISTANCE,
    NORMAL_DECK_SIZE, SPECIAL_DECK_SIZE, INITIAL_DRAW,
    FIRST_PLAYER_VIGOR, SECOND_PLAYER_VIGOR,
)

# 카드 DB 로드
_DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "cards_core4.json")
with open(_DATA_PATH, encoding="utf-8") as f:
    CARD_DB = json.load(f)


def cards_of(megami: str):
    """해당 여신의 (통상 id들, 비장 id들) 반환."""
    normals, specials = [], []
    for cid, c in CARD_DB.items():
        if c["megami"] != megami:
            continue
        if c.get("extra"):      # 추가패(풍마선풍 등)는 기본 덱 제외
            continue
        (specials if "-s-" in cid else normals).append(cid)
    return sorted(normals), sorted(specials)


def build_single_megami_deck(megami: str):
    """
    단일 여신 프리셋 덱.
    통상 7장 전부, 비장 4장 중 앞 3장.
    반환: (normal_deck[7], special_deck[3])
    """
    normals, specials = cards_of(megami)
    assert len(normals) == NORMAL_DECK_SIZE, f"{megami} 통상패 {len(normals)}장 (기대 7)"
    assert len(specials) >= SPECIAL_DECK_SIZE, f"{megami} 비장패 부족"
    return list(normals), list(specials[:SPECIAL_DECK_SIZE])


def new_game(megami0: str, megami1: str, seed: int = None,
             first: int = None) -> GameState:
    """
    두 여신으로 새 게임 생성 (룰북 4-1-1).

    megami0, megami1: 각 플레이어 여신 이름
    seed: 난수 시드 (재현용)
    first: 선공 플레이어 지정(0/1). None이면 무작위.
    """
    rng = random.Random(seed)

    p0 = PlayerState(name=megami0)
    p1 = PlayerState(name=megami1)
    p0.megamis = (megami0,)
    p1.megamis = (megami1,)

    for p, meg in ((p0, megami0), (p1, megami1)):
        normal, special = build_single_megami_deck(meg)
        _init_megami_state(p, [meg])
        p.deck = list(normal)      # 패산 (통상 7)
        p.specials = list(special) # 비장 3 (미사용)
        p.aura = INITIAL_AURA
        p.life = INITIAL_LIFE
        p.flare = 0

    state = GameState(players=[p0, p1], distance=INITIAL_DISTANCE, dust=0)

    # 과정 3: 선공 무작위 결정
    state.active = first if first is not None else rng.randrange(2)

    # 과정 4: 패산 섞고 3장 뽑기
    for i, p in enumerate(state.players):
        rng.shuffle(p.deck)
        for _ in range(INITIAL_DRAW):
            if p.deck:
                p.hand.append(p.deck.pop())

    # 과정 6: 집중력 설정
    state.players[state.active].vigor = FIRST_PLAYER_VIGOR
    state.players[1 - state.active].vigor = SECOND_PLAYER_VIGOR

    # 과정 7: 첫 턴 시작 (상세 페이즈 처리는 다음 커밋)
    state.phase = "main"
    state.turn_count = 1

    state.check_conservation()  # 초기 배치 = 36개 검증
    return state


# 사용 가능한 여신 (v1)
CORE4 = ["yurina", "saine", "himika", "tokoyo", "hagane", "korunu", "oboro", "raira", "chikage", "yukihi", "megumi", "shinra", "kururu", "thallya"]


def _init_megami_state(p, megamis):
    """여신별 시작 상태 초기화 (씨앗/계략/조화결정/독주머니). 2여신이면 둘 다."""
    for meg in megamis:
        if meg == "megumi":
            p.soil_unsprouted = 5   # 토양 씨앗 5개 (17-1)
        if meg == "shinra":
            p.scheme = "shinsan"    # 계략 시작: 신산 (5-1)
        if meg == "thallya":
            p.machine_harmony = 5   # 머신 조화결정 5개 (9-1)
        if meg == "chikage":
            p_ids = sorted(cid for cid, c in CARD_DB.items()
                           if c.get("megami") == "chikage"
                           and c.get("base_type") == "poison")
            p.poison_pouch = [p_ids[0], p_ids[1], p_ids[2], p_ids[3], p_ids[3]]


def apply_mulligan(state, agent0=None, agent1=None):
    """
    무다시 (4-1-1 과정 5): 활성 플레이어부터, 손패 일부를 패산 밑에 넣고
    같은 수를 다시 뽑는다. 에이전트의 choose_mulligan(state, pidx, hand) 훅 사용.
    반환 없음 (state 직접 수정).
    """
    order = [state.active, 1 - state.active]
    agents = {0: agent0, 1: agent1}
    for pidx in order:
        ag = agents.get(pidx)
        if ag is None or not hasattr(ag, "choose_mulligan"):
            continue
        p = state.players[pidx]
        to_swap = ag.choose_mulligan(state, pidx, list(p.hand))
        if not to_swap:
            continue
        n = 0
        for cid in to_swap:
            if cid in p.hand:
                p.hand.remove(cid)
                p.deck.insert(0, cid)   # 패산 밑 (index 0)
                n += 1
        for _ in range(n):
            if p.deck:
                p.hand.append(p.deck.pop())
    state.check_conservation()


def megamis_of(p):
    """플레이어가 깃들인 여신 목록 (단일 게임이면 name 폴백)."""
    return list(p.megamis) if p.megamis else [p.name]


def megami_card_pool(megami: str):
    """안전구축용 카드 풀: (통상 리스트, 비장 리스트). 추가패/독 제외."""
    return cards_of(megami)


def build_two_megami_deck(meg_a: str, meg_b: str, normal_picks=None,
                          special_picks=None, rng=None):
    """
    쌍장요란+안전구축: 두 여신의 카드 풀(각 통상7+비장3~4)에서
    통상 7장, 비장 3장을 선택.
    normal_picks/special_picks가 주어지면 그것을 사용, 아니면 기본 정책
    (통상은 균형있게 나눠서, 비장은 앞에서 3장).
    반환: (normal_deck[7], special_deck[3], out_of_game[나머지])
    """
    na, sa = cards_of(meg_a)
    nb, sb = cards_of(meg_b)
    all_normals = list(na) + list(nb)      # 14장 풀
    all_specials = list(sa) + list(sb)     # 6~8장 풀

    if normal_picks is None or special_picks is None:
        # 기본 정책: 스마트 덱 선택 (카드 가치 평가 + 여신 시너지)
        try:
            from ai.deckbuild import pick_deck
            sn, ss = pick_deck(meg_a, meg_b, rng)
            if normal_picks is None:
                normal_picks = sn
            if special_picks is None:
                special_picks = ss
        except Exception:
            # 폴백: 기계적 분배 (ai 모듈 미가용 시)
            if normal_picks is None:
                normal_picks = list(na[:4]) + list(nb[:3])
            if special_picks is None:
                special_picks = list(sa[:2]) + list(sb[:1])

    assert len(normal_picks) == NORMAL_DECK_SIZE, \
        f"통상패 {len(normal_picks)}장 (기대 7)"
    assert len(special_picks) == SPECIAL_DECK_SIZE, \
        f"비장패 {len(special_picks)}장 (기대 3)"

    chosen = set(normal_picks) | set(special_picks)
    out = [c for c in (all_normals + all_specials) if c not in chosen]
    return list(normal_picks), list(special_picks), out


def new_game_2v2(megamis0, megamis1, seed=None, first=None,
                 picks0=None, picks1=None):
    """
    2여신 벚꽃결투 게임 생성 (쌍장요란 → 안전구축 → 벚꽃결투 준비).

    megamis0, megamis1: 각 (여신A, 여신B) 튜플
    picks0, picks1: (normal_picks, special_picks) 안전구축 선택. None이면 기본 정책.
    """
    rng = random.Random(seed)
    m0a, m0b = megamis0
    m1a, m1b = megamis1
    assert m0a != m0b and m1a != m1b, "같은 여신 2명 선택 불가 (2-1)"

    p0 = PlayerState(name=f"{m0a}+{m0b}")
    p1 = PlayerState(name=f"{m1a}+{m1b}")
    p0.megamis = (m0a, m0b)
    p1.megamis = (m1a, m1b)

    for p, (ma, mb), picks in ((p0, (m0a, m0b), picks0),
                               (p1, (m1a, m1b), picks1)):
        np_, sp_ = (picks if picks else (None, None))
        normal, special, out = build_two_megami_deck(ma, mb, np_, sp_, rng)
        _init_megami_state(p, [ma, mb])
        p.deck = list(normal)
        p.specials = list(special)
        p.out_of_game = list(out)   # 안전구축 미선택 카드
        p.aura = INITIAL_AURA
        p.life = INITIAL_LIFE
        p.flare = 0

    state = GameState(players=[p0, p1], distance=INITIAL_DISTANCE, dust=0)
    state.active = first if first is not None else rng.randrange(2)
    for i, p in enumerate(state.players):
        rng.shuffle(p.deck)
        for _ in range(INITIAL_DRAW):
            if p.deck:
                p.hand.append(p.deck.pop())
    state.players[state.active].vigor = FIRST_PLAYER_VIGOR
    state.players[1 - state.active].vigor = SECOND_PLAYER_VIGOR
    state.phase = "main"
    state.turn_count = 1
    state.check_conservation()
    return state
