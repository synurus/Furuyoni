"""
공격 및 데미지 해결 (룰북 5-8, 9-4)

이 커밋(a)의 범위:
- 적정거리 확인 (9-4-1)
- 단일/복수 데미지 해결 (5-8-3): 오라/라이프 선택, 오라 부족 시 라이프 강제
- 데미지 이동: 오라→더스트, 플레어→더스트, 라이프→플레어
- 승패(라이프0) 판정 연동

아직 다루지 않음(다음 커밋):
- 대응(reaction) 창
- +X/+Y 등 데미지 수정, 무효화, 대응불가 상세
- 【공격후】 등 효과 트리거
"""

from state import GameState
from constants import EndReason


def parse_range(range_str: str):
    """'3-4' → {3,4}, '4' → {4}, '3,5' → {3,5}, '0-10' → {0..10}."""
    if not range_str or range_str == "-":
        return set()
    result = set()
    for part in str(range_str).split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-")
            result.update(range(int(lo), int(hi) + 1))
        else:
            result.add(int(part))
    return result


def parse_damage(dmg_str: str):
    """
    '3/1' → (3, 1), '-/1' → (None, 1), '3/-' → (3, None), '2/2' → (2,2).
    'X/Y' 등 변수 데미지 → (0, 0) (custom 효과가 데미지 해결 시 채운다).
    반환: (aura_dmg, life_dmg), 각각 int 또는 None.
    """
    if not dmg_str or dmg_str == "-":
        return (None, None)
    left, right = dmg_str.split("/")
    def _p(x):
        x = x.strip()
        if x == "-":
            return None
        if not x.lstrip("-").isdigit():
            return 0   # X, Y 등 변수 → 0에서 시작
        return int(x)
    return (_p(left), _p(right))


def in_range(state: GameState, range_set) -> bool:
    """현재 간격이 적정거리에 포함되는가 (9-4-1). 까치발 걸음 -2 반영."""
    return effective_distance(state) in range_set


# ─── 데미지 해결 (5-8-3) ───
def _muonheki_enh(state, pidx):
    """pidx의 전개중 무음벽 부여패 반환 (없으면 None)."""
    from setup import CARD_DB
    for enh in state.players[pidx].enhancements:
        card = CARD_DB[enh.card_id]
        for fx in card.get("effects") or []:
            if fx["trigger"] == "deploy_during":
                for op in fx["ops"]:
                    if op["op"] == "custom" and op["id"] == "muonheki_aura":
                        return enh
    return None


def effective_distance(state) -> int:
    """
    유효 간격: 까치발(전개중 -2) + 탈리야 조화결정 간격±1 토큰 반영.
    간격+1 토큰은 벚꽃결정으로 간주(간격 증가), 간격-1 토큰은 연결된 벚꽃결정을
    간격 계산에서 제외(간격 감소).
    """
    d = state.distance
    for pl in state.players:
        d += pl.gap_plus_harmony     # 간격+1 토큰: 간격에 포함
        d -= pl.gap_minus_harmony    # 간격-1 토큰: 연결된 벚꽃결정 제외
    from setup import CARD_DB
    for pl in state.players:
        for enh in pl.enhancements:
            for fx in CARD_DB[enh.card_id].get("effects") or []:
                if fx["trigger"] == "deploy_during":
                    for op in fx["ops"]:
                        if op["op"] == "custom" \
                                and op["id"] == "kachibal_dist_minus2":
                            d -= 2
                        if op["op"] == "custom" \
                                and op["id"] == "galdae_range_x":
                            d += enh.seeds   # 갈대: 씨앗 수만큼 간격 증가
    return max(0, d)


def effective_aura(state, pidx) -> int:
    """데미지 해결 시의 실효 오라 (무음벽 위 결정 포함)."""
    p = state.players[pidx]
    enh = _muonheki_enh(state, pidx)
    return p.aura + (enh.tokens if enh else 0)


def resolve_single_aura_damage(state: GameState, target_idx: int, amount: int) -> None:
    """
    오라에 단일 데미지: 오라→더스트.
    참격난무: 오라 데미지를 받았음을 기록 (턴 단위).
    무음벽 전개중이면 카드 위 결정도 오라처럼 소모 가능 (기본 정책: 실제 오라 우선).
    카드 위 결정이 0이 되면 파기 (상황 유발).
    """
    p = state.players[target_idx]
    if amount > 0 and min(amount, p.aura + (0)) > 0:
        state._oboro_opp_took_aura_dmg[target_idx] = True
    # 1) 실제 오라에서
    moved = min(amount, p.aura)
    p.aura -= moved
    state.dust += moved
    remain = amount - moved
    # 2) 무음벽 위 결정에서
    if remain > 0:
        enh = _muonheki_enh(state, target_idx)
        if enh:
            take = min(remain, enh.tokens)
            enh.tokens -= take
            state.dust += take
            if enh.tokens == 0:
                from cards import destroy_enhancement
                import random as _rmod
                destroy_enhancement(state, target_idx, enh,
                                    _NULL_AGENT, _rmod.Random(0))
    state.check_conservation()


class _NullAgent:
    """파기시 효과 등에서 선택이 필요할 때의 기본 정책 에이전트."""
    def choose_option(self, state, pidx, labels, tag):
        return 0
_NULL_AGENT = _NullAgent()


def resolve_single_life_damage(state: GameState, target_idx: int, amount: int) -> None:
    """라이프에 단일 데미지: 라이프→플레어 (5-8-3-1). 즉재기 훅 포함."""
    p = state.players[target_idx]
    before = p.life
    moved = min(amount, p.life)
    p.life -= moved
    p.flare += moved
    state.check_conservation()
    from cards import on_life_reduced
    on_life_reduced(state, target_idx, before)


def choose_and_apply_damage(state: GameState, attacker_idx: int, target_idx: int,
                            aura_dmg, life_dmg, agent,
                            both: bool = False, to_distance: bool = False) -> None:
    """
    복수 수치 데미지 해결 (5-8-3-2).
    both=True면 풀버스트/크림슨제로처럼 양쪽 모두 적용.
    한쪽이 '-'이면 자동으로 다른 쪽. 오라 결정이 부족하면 오라 선택 불가.
    """
    p = state.players[target_idx]

    # 양쪽 '-' → 아무것도
    if aura_dmg is None and life_dmg is None:
        return

    # both 플래그(풀버스트 등): 양쪽 다
    if both and aura_dmg is not None and life_dmg is not None:
        resolve_single_aura_damage(state, target_idx, aura_dmg)
        _check_life(state)
        if not state.is_over():
            resolve_single_life_damage(state, target_idx, life_dmg)
            _check_life(state)
        return

    # 한쪽만 존재
    if aura_dmg is None:
        resolve_single_life_damage(state, target_idx, life_dmg)
        _check_life(state)
        return
    if life_dmg is None:
        resolve_single_aura_damage(state, target_idx, aura_dmg)
        _check_life(state)
        return

    # 양쪽 정수 → 받는 쪽이 선택. 오라 부족(실효오라 < 오라뎀)이면 오라 선택 불가.
    # 실효 오라 = 오라 + 무음벽 위 결정 (5-8-3-2 + 무음벽 전개중)
    can_choose_aura = effective_aura(state, target_idx) >= aura_dmg
    if not can_choose_aura:
        choice = "life"
    else:
        choice = agent.choose_damage_type(state, target_idx, aura_dmg, life_dmg) \
            if hasattr(agent, "choose_damage_type") else "life"

    state._last_damage_choice = choice   # 콘루 루얀페 등이 참조
    if choice == "aura":
        resolve_single_aura_damage(state, target_idx, aura_dmg)
    else:
        resolve_single_life_damage(state, target_idx, life_dmg)
    _check_life(state)


def _check_life(state: GameState) -> None:
    """라이프 0 판정 (4-2). 이미 끝난 게임은 재판정하지 않는다."""
    if state.is_over():
        return
    p0, p1 = state.players
    z0, z1 = p0.life <= 0, p1.life <= 0
    if z0 and z1:
        _end(state, -1, EndReason.DRAW)
    elif z0:
        _end(state, 1, EndReason.LIFE_ZERO)
    elif z1:
        _end(state, 0, EndReason.LIFE_ZERO)


def _end(state, winner, reason):
    if state.is_over():
        return  # 결과 덮어쓰기 방지
    state.phase = "over"
    state.winner = winner
    state.end_reason = reason


# ─── 공격 인스턴스 (커밋 b) ───
class AttackInstance:
    """
    생성된 《공격》 하나. 버프/수정을 받아 최종 수치로 해결된다.
    """
    def __init__(self, range_set, aura_dmg, life_dmg, attacker_idx,
                 source_card=None, flags=None, is_special=False):
        self.range_set = set(range_set)
        self.aura = aura_dmg      # int 또는 None('-')
        self.life = life_dmg
        self.attacker = attacker_idx
        self.source_card = source_card
        self.flags = set(flags or [])   # 'no_reactions', 'both' 등
        self.is_special = is_special
        self.cancelled = False
        self.aura_cap = 5               # 오라 데미지 상한 (6-4-1-4). 초극이면 None
        self.dmg_to_distance = False    # 장대 찌르기: 데미지 결정을 간격으로

    def apply_buff(self, buff: dict) -> None:
        """+X/+Y 및 gains 적용. '-'(None)인 쪽은 수정되지 않는다 (룰북 5-6)."""
        # 조건부 버프 (백드래프트: 오라뎀 3 이하만)
        limit = buff.get("only_if_aura_dmg_le")
        if limit is not None and (self.aura is None or self.aura > limit):
            return
        if self.aura is not None:
            self.aura = max(0, self.aura + buff.get("aura", 0))
        if self.life is not None:
            self.life = max(0, self.life + buff.get("life", 0))
        for g in buff.get("gains", []):
            if g == "far_extend_1":
                # 거리확대(원1): 최댓값+1 을 적정거리에 추가 (10-35-1 단순화)
                if self.range_set:
                    self.range_set.add(max(self.range_set) + 1)
            elif g == "no_reactions":
                self.flags.add("no_reactions")
            elif g == "chokyoku":
                self.aura_cap = None        # 초극: 오라 데미지 상한 해제 (10-21)


def perform_card_attack(state: GameState, attacker_idx: int,
                        range_set, aura_dmg, life_dmg, agent, rng,
                        source_card=None, flags=None,
                        is_special=False, is_sub_attack=False,
                        card_constants=None, is_reaction=False,
                        atk_out=None, source_megami=None,
                        from_covered=False, as_fullpower=False) -> bool:
    """
    공격 해결 (9-4). 커밋 b: 버프 파이프라인 포함, 대응 창은 커밋 c.

    처리 순서:
      1. AttackInstance 생성
      2. 카드 자신의 【상시】 수정 적용 (조건 검사)
      3. 대기 중인 next_attack 버프 소비·적용 (서브공격 제외)
      4. 적정거리 확인
      5. 압도 등 상대 전개중 효과에 의한 데미지 경감
      6. 데미지 해결
    """
    from effects import check_condition

    atk = AttackInstance(range_set, aura_dmg, life_dmg, attacker_idx,
                         source_card=source_card, flags=flags,
                         is_special=is_special)
    if atk_out is not None:
        atk_out.append(atk)

    # 2. 상시 효과 1차 평가 — 성질(대응불가/both)만 먼저 적용.
    #    수치 수정(+X/+Y)은 데미지 해결 시점(6단계 직전)에 재평가 (일섬 FAQ:
    #    대응으로 라이프가 3이 되면 결사가 적용되어야 함)
    if card_constants:
        for fx in card_constants:
            cond = fx.get("condition")
            # ctx성 조건 처리 (1차 성질 평가)
            if cond == "from_covered":
                continue
            if cond == "as_fullpower":
                if not as_fullpower:
                    continue
            elif not check_condition(state, attacker_idx, cond):
                continue
            for op in fx["ops"]:
                if op["op"] == "buff" and op.get("target") == "this_attack":
                    for g in op.get("gains", []):
                        # 크림슨 제로 등: 대응불가 부여는 생성 시점 판정
                        # (세션4 판정 2번: 대응으로 간격이 변하는 극단 케이스는 v1 허용)
                        if g == "no_reactions":
                            atk.flags.add("no_reactions")
                elif op["op"] == "custom" and op["id"] == "full_burst_both":
                    atk.flags.add("both")

    # 3. 대기 버프 소비 — 카드 공격과 효과 생성 공격(서브공격) 모두
    #    "당신이 수행하는 다음번 공격"에 해당 (세션4 판정 1번)
    remaining = []
    from setup import CARD_DB
    my_megami = source_megami or \
        (CARD_DB[source_card]["megami"] if source_card else None)
    for buff in state.pending_buffs[attacker_idx]:
        if buff.get("other_megami_only"):
            # '다른 여신의 공격'에만 적용 (v1 단일 여신 덱에서는 사실상 미발동)
            if my_megami is None or buff.get("source_megami") == my_megami:
                remaining.append(buff)
                continue
        atk.apply_buff(buff)
    state.pending_buffs[attacker_idx] = remaining

    # "공격을 수행했다" 카운트 (9-4 서두: 적정하게 생성된 시점).
    # 압도의 '각 턴 첫 번째 공격' 판정은 무효화/빗나감과 무관하게 수행 순서 기준.
    is_first_attack = (state.attacks_this_turn[attacker_idx] == 0)

    # 3-a2. 효과 생성 공격의 적정거리 선확인 (9-4-2):
    #        부정이면 소거 — 대응 창조차 열리지 않는다 (율동호극 FAQ)
    if is_sub_attack and "unavoidable" not in atk.flags \
            and not in_range(state, atk.range_set):
        return False
    state.attacks_this_turn[attacker_idx] += 1

    # 리플렉터(전개중): 매 턴 상대(공격자)의 2번째 공격 무효화
    if _has_deployed_custom(state, 1 - attacker_idx, "reflector_block2nd"):
        if state.attacks_this_turn[attacker_idx] == 2:
            return False   # 2번째 공격 무효 (해결 없이 종료)

    # 3-b. 대응 창 (9-4 i): 이 공격이 대응이 아니고, 대응불가가 아니면
    #      상대가 《대응》 카드로 끼어들 수 있다. (효과 생성 공격도 대응 가능)
    if not is_reaction and "no_reactions" not in atk.flags:
        from cards import legal_reactions, use_card_as_reaction
        target_idx0 = 1 - attacker_idx
        options = legal_reactions(state, target_idx0, atk)
        if options:
            choice = agent.choose_reaction(state, target_idx0, options, atk) \
                if hasattr(agent, "choose_reaction") else None
            if choice is not None:
                src_r, cid_r = choice
                use_card_as_reaction(state, target_idx0, src_r, cid_r,
                                     agent, rng, atk)
                if state.is_over():
                    return False

    # 3-c. 무효화 확인 (9-4 ii)
    if atk.cancelled:
        return False

    # 4. 적정거리 확인 (9-4 iii) — 대응으로 간격이 바뀌었을 수 있으므로 여기서 판정
    if "unavoidable" not in atk.flags and not in_range(state, atk.range_set):
        return False

    # 5-a. 상시 수치 수정 재평가 (데미지 해결 시점 — 5-5-4, 일섬 FAQ)
    if card_constants:
        for fx in card_constants:
            cond = fx.get("condition")
            if cond == "from_covered":
                if not from_covered:
                    continue
            elif cond == "as_fullpower":
                if not as_fullpower:
                    continue
            elif not check_condition(state, attacker_idx, cond):
                continue
            for op in fx["ops"]:
                if op["op"] == "buff" and op.get("target") == "this_attack":
                    if op.get("aura") or op.get("life"):
                        atk.apply_buff({"aura": op.get("aura", 0),
                                        "life": op.get("life", 0),
                                        "gains": []})
                elif op["op"] == "custom" and op["id"] == "daecheongong_xy":
                    # X = |현재간격 - 턴시작간격|, Y = ceil(X/2), 초극
                    x = abs(state.distance - state.turn_start_distance)
                    atk.aura = x
                    atk.life = (x + 1) // 2
                    atk.aura_cap = None   # 초극
                elif op["op"] == "custom" and op["id"] == "poongnoe_x":
                    # 풍뢰격: X = min(풍신, 뇌신) → 오라 데미지
                    pp = state.players[attacker_idx]
                    atk.aura = min(pp.fuujin, pp.raijin)
                elif op["op"] == "custom" and op["id"] == "ipron_deck_cover":
                    # 입론: 상대 패산 2장 이상이면 데미지 대신 패산 위 2장 덮음
                    opp2 = state.players[1 - attacker_idx]
                    if len(opp2.deck) >= 2:
                        atk.aura = None
                        atk.life = None
                        for _ in range(2):
                            if opp2.deck:
                                opp2.covered.append(opp2.deck.pop())

    # 천지반박 (뒤바꿈, 5-6): 자기 공격의 오라뎀↔라이프뎀 교환 (증감보다 먼저)
    if _has_deployed_custom(state, attacker_idx, "cheonji_swap"):
        atk.aura, atk.life = atk.life, atk.aura

    # 반기의 얽힌독 (대입, 5-6): 라이프뎀이 '-'가 아닌 서로의 공격은
    # 오라뎀 = 라이프뎀. 공격 자체의 대입(풍뢰격 X 등)이 먼저 적용된 뒤.
    for _pl in (0, 1):
        if _has_deployed_custom(state, _pl, "bangi_assign"):
            if atk.life is not None:
                atk.aura = atk.life
            break

    # 타키가와 손바닥(전개중): 각 턴 첫 오라뎀3이하 공격 +1/+1
    if is_first_attack and _has_deployed_custom(state, attacker_idx,
                                                "sonbadak_first_attack"):
        if atk.aura is not None and atk.aura <= 3:
            atk.apply_buff({"aura": 1, "life": 1, "gains": []})

    # 질척이는 속내(전개중): 당신의 턴 첫 공격에 우산 상태별 버프
    if is_first_attack and attacker_idx == state.active \
            and _has_deployed_custom(state, attacker_idx, "jilcheok_first_attack"):
        pp = state.players[attacker_idx]
        if pp.umbrella_open:
            atk.apply_buff({"aura": 1, "life": 0, "gains": ["far_extend_1"]})
        else:
            atk.apply_buff({"aura": 0, "life": 1, "gains": ["near_extend_1"]})

    # 서리 가시덤불(전개중): 자기 턴 첫 비장 아닌 공격 +1/+1
    if is_first_attack and not atk.is_special and not is_sub_attack:
        if _has_deployed_custom(state, attacker_idx, "seori_first_attack_buff"):
            atk.apply_buff({"aura": 1, "life": 1, "gains": []})

    # 5. 상대의 압도(전개중): 각 턴 첫 번째로 수행된 공격의 오라 데미지 1 경감
    target_idx = 1 - attacker_idx
    if is_first_attack:
        if _has_deployed_custom(state, target_idx, "appdo_reduce"):
            if atk.aura is not None:
                atk.aura = max(0, atk.aura - 1)

    # 6. 데미지 해결 — 오라 데미지 상한 적용 (6-4-1-4, 초극이면 상한 없음)
    final_aura = atk.aura
    if final_aura is not None and atk.aura_cap is not None:
        final_aura = min(final_aura, atk.aura_cap)
    choose_and_apply_damage(state, attacker_idx, target_idx,
                            final_aura, atk.life, agent,
                            both=("both" in atk.flags))
    return True


def _has_deployed_custom(state, pidx, custom_id) -> bool:
    """pidx의 전개중 부여패에 특정 custom 전개중 효과가 있는가."""
    from setup import CARD_DB
    for enh in state.players[pidx].enhancements:
        card = CARD_DB[enh.card_id]
        for fx in card.get("effects") or []:
            if fx["trigger"] != "deploy_during":
                continue
            for op in fx["ops"]:
                if op["op"] == "custom" and op["id"] == custom_id:
                    return True
    return False


# 하위 호환 (demo_phase1 등)
def perform_attack(state, attacker_idx, range_set, aura_dmg, life_dmg, agent,
                   both=False, unavoidable=False):
    target_idx = 1 - attacker_idx
    if not unavoidable and not in_range(state, range_set):
        return False
    choose_and_apply_damage(state, attacker_idx, target_idx,
                            aura_dmg, life_dmg, agent, both=both)
    return True
