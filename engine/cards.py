"""
카드 사용 (룰북 9-2) — 커밋 b: 전체 카드 타입 지원

- 공격 (9-2-1): 적정거리 확인 → 비용 → 공격 해결(버프 파이프라인) → 【공격후】
- 행동 (9-2-2): 비용 → 본문(main) 효과 해결
- 부여 (9-2-3): 비용 → 봉납(더스트/오라에서) → 【전개시】 → 부여패로

전력(fullpower) 카드는 전력행동에서만, 종단 사용 후엔 잠금.
사용 제한 custom: 저력(결사 아니면 불가), 종극(대응 전용 → 메인 사용 불가)
"""

import random
from state import GameState, Enhancement
from combat import perform_card_attack, parse_range, parse_damage, \
    effective_distance
from effects import resolve_trigger, check_condition
from setup import CARD_DB


def card_info(card_id: str):
    return CARD_DB[card_id]


def card_cost(card_id: str) -> int:
    v = CARD_DB[card_id].get("cost")
    if v is None or v == "":
        return 0
    return int(v)


def card_capacity(card_id: str) -> int:
    v = CARD_DB[card_id].get("capacity")
    if v is None or v == "":
        return 0
    return int(v)


def effective_cost(state: GameState, pidx: int, card_id: str) -> int:
    """실효 소모값. custom: 항명공진은 상대 오라만큼 감소."""
    cost = card_cost(card_id)
    card = CARD_DB[card_id]
    for fx in card.get("effects") or []:
        for op in fx["ops"]:
            if op["op"] == "custom" and op["id"] == "cost_reduce_by_opp_aura":
                cost = max(0, cost - state.players[1 - pidx].aura)
    return cost


def _has_custom(card: dict, custom_id: str) -> bool:
    for fx in card.get("effects") or []:
        for op in fx["ops"]:
            if op["op"] == "custom" and op["id"] == custom_id:
                return True
    return False


def _constants_of(card: dict) -> list:
    return [fx for fx in (card.get("effects") or []) if fx["trigger"] == "constant"]


# ═══════════════════════════════════════════
# 합법 수 생성
# ═══════════════════════════════════════════
def legal_card_uses(state: GameState, pidx: int, is_fullpower: bool):
    """
    사용 가능한 카드 목록 [(src, card_id)].
    src: 'hand' | 'special'
    - 표준행동: 《전력》 제외
    - 전력행동: 모든 카드 가능 (전력 포함)
    - 공격 카드: 적정거리 밖이면 부정 사용이므로 미리 제외
    - 비장패: 실효 소모값 지불 가능해야
    - 저력: 결사 아니면 불가 / 종극: 메인 사용 불가 (대응 전용)
    """
    p = state.players[pidx]
    result = []
    candidates = [("hand", cid) for cid in p.hand] + \
                 [("special", cid) for cid in p.specials]

    for src, cid in candidates:
        c = CARD_DB[cid]
        subtype = c.get("subtype")

        if subtype == "fullpower" and not is_fullpower:
            continue
        # 대응 전용 카드 (종극)
        if _has_custom(c, "reaction_to_special_only"):
            continue
        # 결사 전용 (저력)
        if _has_custom(c, "unusable_unless_kessa"):
            if not check_condition(state, pidx, "kessa"):
                continue
        # 원심 (6-2): 페이즈에 아직 공격 안 함 & 간격이 턴시작보다 2 이상 멀어짐
        if "centrifugal" in (c.get("card_flags") or []):
            if not _centrifugal_ok(state, pidx):
                continue
        # 연소 카드: 머신에 조화결정 없으면 사용 불가 (9-7)
        if "burn" in (c.get("card_flags") or []):
            if state.players[pidx].machine_harmony < 1:
                continue
        # 마비독: 이번 턴 기본동작을 수행했다면 사용 불가
        if _has_custom(c, "mabi_unusable_if_basic"):
            if state.basic_actions_this_turn[pidx] >= 1:
                continue
        # 이완독 전개중: 자신은 공격 카드 사용 불가
        if c["type"] == "attack":
            from combat import _has_deployed_custom
            if _has_deployed_custom(state, pidx, "iwan_no_attack"):
                continue

        # 비용
        if src == "special":
            if p.flare < effective_cost(state, pidx, cid):
                continue

        # 공격: 적정거리 확인 (버프 미적용 기준. 몸놀림 거리확대가 대기 중이면
        #       실제로는 더 넓지만, v1 단순화: 기본 적정거리로 판정)
        if c["type"] == "attack":
            rng_set = parse_range(card_range_str(state, pidx, c))
            # 대기 버프의 거리확대 반영
            for buff in state.pending_buffs[pidx]:
                if "far_extend_1" in buff.get("gains", []) and rng_set:
                    rng_set = set(rng_set) | {max(rng_set) + 1}
            if effective_distance(state) not in rng_set:
                continue

        result.append((src, cid))
    return result


# ═══════════════════════════════════════════
# 카드 사용
# ═══════════════════════════════════════════
def pay_cost(state: GameState, pidx: int, cost: int) -> bool:
    if not cost:
        return True
    p = state.players[pidx]
    if p.flare < cost:
        return False
    p.flare -= cost
    state.dust += cost
    state.check_conservation()
    return True


def use_card(state: GameState, pidx: int, src: str, card_id: str,
             agent, rng: random.Random, as_fullpower: bool = False) -> bool:
    """카드 1장 사용 (9-2). 성공 여부 반환."""
    p = state.players[pidx]
    c = CARD_DB[card_id]
    ctx = {"source_card": card_id, "source_megami": c["megami"],
           "as_fullpower": as_fullpower}

    # 라이라 대전 (10-4): 이미 대전 상태인 카드가 다시 사용중으로 이동하면
    #   그 카드 자신을 해제하고 게이지 +1 (상황 유발). 이후 재사용으로 다시 대전화.
    from setup import megamis_of
    _is_raira = "raira" in megamis_of(p)
    if _is_raira and card_id in p.taisen_cards:
        release_taisen(state, pidx, card_id, gain_gauge=True, agent=agent)
    # 연소 키워드: 추가 비용으로 조화결정 1개 연소 (9-7)
    if "burn" in (c.get("card_flags") or []):
        from effects import _burn_harmony
        _burn_harmony(state, pidx, 1)

    ctype = c["type"]
    if "centrifugal" in (c.get("card_flags") or []):
        state._hagane_used_centrifugal[pidx] = True

    # ── 공격 (9-2-1) ──
    if ctype == "attack":
        rng_set = parse_range(card_range_str(state, pidx, c))
        # 대기 버프 거리확대 반영해 적정거리 확인
        eff_range = set(rng_set)
        for buff in state.pending_buffs[pidx]:
            if "far_extend_1" in buff.get("gains", []) and eff_range:
                eff_range.add(max(eff_range) + 1)
        if effective_distance(state) not in eff_range:
            return False
        cost = effective_cost(state, pidx, card_id)
        if src == "special" and not pay_cost(state, pidx, cost):
            return False

        _remove_from(p, src, card_id)
        state.cards_used_this_turn[pidx] += 1

        a_dmg, l_dmg = parse_damage(card_damage_str(state, pidx, c))
        flags = set(c.get("card_flags") or [])
        atk_flags = {f for f in flags if f == "no_reactions"}
        success = perform_card_attack(
            state, pidx, rng_set, a_dmg, l_dmg, agent, rng,
            source_card=card_id, flags=atk_flags,
            is_special=(src == "special"),
            card_constants=_constants_of(c),
        )
        if state.is_over():
            _finish_card(state, p, src, card_id, ctx)
            return True

        # 사풍진 상시: 간격이 턴 시작보다 2 이상 변했으면 상대 손패 1장 버림
        if success and _has_custom(c, "sapungjin_discard"):
            if abs(state.distance - state.turn_start_distance) >= 2:
                opp = state.players[1 - pidx]
                if opp.hand:
                    idx = agent.choose_card_to_cover(state, 1 - pidx, opp.hand) \
                        if hasattr(agent, "choose_card_to_cover") else 0
                    opp.discard.append(opp.hand.pop(idx))

        # 【공격후】 — 공격이 성공했을 때만
        if success:
            resolve_trigger(state, pidx, c, "attack_after", agent, rng, ctx)
        _finish_card(state, p, src, card_id, ctx)

    # ── 행동 (9-2-2) ──
    elif ctype == "action":
        cost = effective_cost(state, pidx, card_id)
        if src == "special" and not pay_cost(state, pidx, cost):
            return False
        _remove_from(p, src, card_id)
        state.cards_used_this_turn[pidx] += 1
        resolve_trigger(state, pidx, c, "main", agent, rng, ctx)
        _finish_card(state, p, src, card_id, ctx)

    # ── 부여 (9-2-3) ──
    elif ctype == "enhance":
        cost = effective_cost(state, pidx, card_id)
        if src == "special" and not pay_cost(state, pidx, cost):
            return False
        _remove_from(p, src, card_id)
        state.cards_used_this_turn[pidx] += 1

        # iii: 봉납 — 메구미면 씨앗 규칙(17-7-1), 아니면 통상(더스트/오라)
        cap = card_capacity(card_id)
        enh = Enhancement(card_id=card_id, tokens=0)
        p.enhancements.append(enh)
        ctx["enhancement"] = enh

        if c["megami"] == "megumi":
            _megumi_enhance_dedication(state, pidx, c, enh, cap, agent)
        else:
            need = cap
            # 기본 정책/에이전트: 더스트 우선, 부족분 오라
            from_dust = min(need, state.dust)
            if hasattr(agent, "choose_dedication"):
                from_dust = agent.choose_dedication(state, pidx, need)
                from_dust = min(from_dust, state.dust, need)
            state.dust -= from_dust
            enh.tokens += from_dust
            need -= from_dust
            from_aura = min(need, p.aura)
            p.aura -= from_aura
            enh.tokens += from_aura
            need -= from_aura
            # 껍질치기: 메구미 플레이어가 다른 여신 부여를 쓰면 생육2 (씨앗 2개 얹기)
            if state._ggeopjil_growth.get(pidx):
                state._ggeopjil_growth[pidx] = False
                add = min(2, p.soil_sprouted)
                p.soil_sprouted -= add
                enh.seeds += add
        # 부족분은 있는 만큼만 (5-3 가능한 만큼)
        state.check_conservation()

        # iv: 【전개시】
        resolve_trigger(state, pidx, c, "deploy_start", agent, rng, ctx)

        # v: 부여패로 (이미 iii에서 등록됨)
        if not state.is_over():
            # 봉납 0(벚꽃결정 0)이고 씨앗도 0이면 즉시 파기
            if enh.tokens == 0 and enh.seeds == 0:
                destroy_enhancement(state, pidx, enh, agent, rng)

    else:
        return False

    # 모듀르(전개중): 행동 카드 사용 후 기본동작 1회
    if ctype == "action" and not state.is_over():
        for e in state.players[pidx].enhancements:
            ec = CARD_DB[e.card_id]
            if any(o.get("id") == "modur_action_basic"
                   for fx in (ec.get("effects") or [])
                   if fx["trigger"] == "deploy_during" for o in fx["ops"]):
                from tokens import legal_basic_actions, perform_basic_action
                acts = legal_basic_actions(state, pidx)
                if acts and hasattr(agent, "choose_free_basic_action"):
                    ch = agent.choose_free_basic_action(state, pidx, acts)
                    if ch:
                        perform_basic_action(state, pidx, ch)
                break

    # 종단 (9-2): 사용한 본인의 이 턴 카드 사용/기본동작 잠금 (FAQ)
    if "terminal" in (c.get("card_flags") or []):
        state.terminal_lock[pidx] = True

    # 라이라 대전 부여: 해결을 마치고 버림패/부여패/비장패로 간 비라이라 카드
    if _is_raira and c["megami"] != "raira":
        _mark_taisen_if_applicable(state, pidx, card_id)

    state.check_conservation()
    return True


def _remove_from(p, src, card_id):
    if src == "hand":
        p.hand.remove(card_id)
    else:
        p.specials.remove(card_id)


def _mark_taisen_if_applicable(state, pidx, card_id):
    """라이라 플레이어가 라이라 아닌 여신 카드를 사용하면 대전 상태 부여 (10-3-1-1)."""
    from setup import megamis_of
    p = state.players[pidx]
    if "raira" not in megamis_of(p):
        return
    if CARD_DB[card_id]["megami"] == "raira":
        return
    # 이 카드가 대전 상태가 됨 (사용중→버림패/부여패/비장패에서 유효)
    if card_id not in p.taisen_cards:
        p.taisen_cards.append(card_id)


def release_taisen(state, pidx, card_id, gain_gauge=True, agent=None):
    """
    대전 상태 해제 (10-4). gain_gauge면 풍신/뇌신 게이지 1 상승.
    반환: 해제 성공 여부.
    """
    p = state.players[pidx]
    if card_id not in p.taisen_cards:
        return False
    p.taisen_cards.remove(card_id)
    if gain_gauge:
        which = "fuujin"
        if agent is not None and hasattr(agent, "choose_gauge"):
            which = agent.choose_gauge(state, pidx)
        setattr(p, which, min(20, getattr(p, which) + 1))
    return True


def _finish_card(state, p, src, card_id, ctx):
    """카드 해결 종료 후 카드를 놓일 곳으로 (9-2-1 v 등)."""
    if ctx.get("remove_from_game"):    # 대파종 메갈로벨 등
        p.out_of_game.append(card_id)
        return
    if ctx.get("card_to_hand"):        # 숨긴 불꽃: 손패로 복귀
        p.hand.append(card_id)
        return
    if ctx.get("return_to_pouch"):     # 독 카드: 상대(치카게) 독주머니로
        owner = 1 - state.players.index(p)
        state.players[owner].poison_pouch.append(card_id)
        return
    if ctx.get("card_to_deck_top"):   # 빗어내리기
        p.deck.append(card_id)         # index -1 = 맨 위
        return
    if ctx.get("card_to_specials"):    # Julia's BlackBox: 미사용 비장 복귀
        p.specials.append(card_id)
        return
    if src == "hand":
        p.discard.append(card_id)
    else:
        p.used_specials.append(card_id)


# ═══════════════════════════════════════════
# 부여패 파기 (9-5)
# ═══════════════════════════════════════════
def destroy_enhancement(state: GameState, pidx: int, enh: Enhancement,
                        agent, rng) -> None:
    """부여패 파기: 【파기시】 해결 후 카드 이동."""
    p = state.players[pidx]
    c = CARD_DB[enh.card_id]
    if enh in p.enhancements:
        p.enhancements.remove(enh)

    ctx = {"source_card": enh.card_id, "source_megami": c["megami"],
           "enhancement": enh}
    resolve_trigger(state, pidx, c, "destroy", agent, rng, ctx)

    # 부여패 위 씨앗은 토양으로 (발아 안 한 상태로) 복귀 (17-2)
    if enh.seeds > 0:
        state.players[pidx].soil_unsprouted += enh.seeds
        enh.seeds = 0

    # 삼라판증 전개중: 다른 부여패가 파기되면 상대 라이프에 1 데미지
    for other in state.players[pidx].enhancements:
        if other is enh:
            continue
        oc = CARD_DB[other.card_id]
        if any(op2.get("id") == "samra_on_other_destroy"
               for fx2 in (oc.get("effects") or [])
               if fx2["trigger"] == "deploy_during" for op2 in fx2["ops"]):
            from combat import resolve_single_life_damage
            if not state.is_over():
                resolve_single_life_damage(state, 1 - pidx, 1)

    # ii: 통상패 → 버림패, 비장패 → 사용한 비장패. 독(이완독) → 상대 독주머니
    if ctx.get("return_to_pouch"):
        state.players[1 - pidx].poison_pouch.append(enh.card_id)
    elif ctx.get("card_to_specials"):   # 야미쿠라: 미사용 복귀 (파기시 실패 경로 아님)
        p.specials.append(enh.card_id)
    elif c.get("base_type") == "special":
        p.used_specials.append(enh.card_id)
    else:
        p.discard.append(enh.card_id)
    state.check_conservation()


# ═══════════════════════════════════════════
# 대응 (9-4 i, 커밋 c)
# ═══════════════════════════════════════════
def legal_reactions(state: GameState, pidx: int, atk):
    """
    atk(상대의 공격)에 대응 가능한 카드 목록 [(src, card_id)].
    - 서브타입 《대응》 카드 (손패 + 미사용 비장패)
    - 간파: 팔상이면 대응처럼 사용 가능 (usable_as_reaction)
    - 종극: 비장패 공격에만 대응 가능 (reaction_to_special_only)
    - 공격 타입 대응 카드는 현재 간격이 적정거리에 있어야 (9-2-1 i)
    - 비장패는 실효 소모값 지불 가능해야
    """
    p = state.players[pidx]
    if state.terminal_lock[pidx]:
        return []  # 종단 사용자는 이 턴 추가 대응 불가
    result = []
    candidates = [("hand", cid) for cid in p.hand] + \
                 [("special", cid) for cid in p.specials]

    for src, cid in candidates:
        c = CARD_DB[cid]
        is_reaction_sub = (c.get("subtype") == "reaction")
        is_kanpa = _has_custom(c, "usable_as_reaction") and \
            check_condition(state, pidx, "hassou")
        if not (is_reaction_sub or is_kanpa):
            continue
        # 종극: 비장패 대응 전용
        if _has_custom(c, "reaction_to_special_only") and not atk.is_special:
            continue
        # 공격 타입: 적정거리 확인
        if c["type"] == "attack":
            if effective_distance(state) not in \
                    parse_range(card_range_str(state, pidx, c)):
                continue
        # 비용
        if src == "special" and p.flare < effective_cost(state, pidx, cid):
            continue
        result.append((src, cid))
    return result


def use_card_as_reaction(state: GameState, pidx: int, src: str, card_id: str,
                         agent, rng, reacted_atk) -> bool:
    """
    대응으로 카드 사용 (끼어들기 해결, 9-8 단순화).
    ctx에 reacted_attack을 실어 카드 효과가 원 공격을 수정/무효화할 수 있게 한다.
    대응 카드의 공격은 다시 대응 창을 열지 않는다 (9-4 i).
    """
    p = state.players[pidx]
    c = CARD_DB[card_id]
    ctx = {"source_card": card_id, "source_megami": c["megami"],
           "reacted_attack": reacted_atk}

    cost = effective_cost(state, pidx, card_id)
    if src == "special" and not pay_cost(state, pidx, cost):
        return False

    _remove_from(p, src, card_id)
    state.cards_used_this_turn[pidx] += 1
    ctype = c["type"]

    if ctype == "attack":
        rng_set = parse_range(card_range_str(state, pidx, c))
        a_dmg, l_dmg = parse_damage(card_damage_str(state, pidx, c))
        flags = {f for f in (c.get("card_flags") or []) if f == "no_reactions"}
        success = perform_card_attack(
            state, pidx, rng_set, a_dmg, l_dmg, agent, rng,
            source_card=card_id, flags=flags,
            is_special=(src == "special"),
            card_constants=_constants_of(c),
            is_reaction=True,
        )
        if not state.is_over() and success:
            # 【공격후】 — 해안(-2/0), 음무쇄빙(-1/-1), 우아한 타격/영원한 꽃(무효화)
            resolve_trigger(state, pidx, c, "attack_after", agent, rng, ctx)
        _finish_card(state, p, src, card_id, ctx)

    elif ctype == "action":
        # 시의 춤, (팔상)간파: 본문 효과 해결 — 간격을 바꿔 회피 가능
        resolve_trigger(state, pidx, c, "main", agent, rng, ctx)
        _finish_card(state, p, src, card_id, ctx)

    elif ctype == "enhance":
        # 충음정: 봉납 후 【전개시】(대응 공격 -1/0)
        cap = card_capacity(card_id)
        enh = Enhancement(card_id=card_id, tokens=0)
        p.enhancements.append(enh)
        need = cap
        from_dust = min(need, state.dust)
        if hasattr(agent, "choose_dedication"):
            from_dust = min(agent.choose_dedication(state, pidx, need),
                            state.dust, need)
        state.dust -= from_dust
        enh.tokens += from_dust
        need -= from_dust
        from_aura = min(need, p.aura)
        p.aura -= from_aura
        enh.tokens += from_aura
        state.check_conservation()
        ctx["enhancement"] = enh
        resolve_trigger(state, pidx, c, "deploy_start", agent, rng, ctx)
        if not state.is_over() and enh.tokens == 0:
            destroy_enhancement(state, pidx, enh, agent, rng)

    # 종단: 대응으로 사용해도 본인에게 적용 (FAQ) — 이 턴의 추가 대응도 불가
    if "terminal" in (c.get("card_flags") or []):
        state.terminal_lock[pidx] = True

    state.check_conservation()
    return True


# ═══════════════════════════════════════════
# 재기 / 즉재기 (10-7, 10-8)
# ═══════════════════════════════════════════
def check_saiki_end_phase(state: GameState, pidx: int) -> None:
    """
    재기 (10-7): 소유자의 종료 페이즈에 조건 판정, 충족 시 미사용으로.
    대상: 음무쇄빙(팔상), 버밀리온 필드(hand==0), 무궁한 바람(경지)
    """
    p = state.players[pidx]
    returned = []
    for cid in list(p.used_specials):
        saiki = CARD_DB[cid].get("saiki")
        if not saiki or saiki.get("immediate"):
            continue
        if check_condition(state, pidx, saiki["condition"]):
            p.used_specials.remove(cid)
            p.specials.append(cid)
            returned.append(cid)
    return returned


def _yamikura_check(state: GameState, pidx: int) -> None:
    """야미쿠라 전개중: 라이프 데미지를 받으면 결정 전부 더스트, 카드 미사용 복귀."""
    p = state.players[pidx]
    for enh in list(p.enhancements):
        c = CARD_DB[enh.card_id]
        if any(op.get("id") == "yamikura_guard"
               for fx in (c.get("effects") or [])
               if fx["trigger"] == "deploy_during"
               for op in fx["ops"]):
            state.dust += enh.tokens
            enh.tokens = 0
            p.enhancements.remove(enh)
            p.specials.append(enh.card_id)   # 미사용으로 (파기시 효과 실패)
            state.check_conservation()


def on_life_reduced(state: GameState, pidx: int, life_before: int) -> None:
    """
    라이프 감소 후 호출되는 훅.
    즉재기 (10-8): '결사가 된다' = 라이프가 3 초과 → 3 이하로 전이하는 순간.
    (쪽배 FAQ: 4→3은 재기, 3→2는 이미 결사였으므로 재기 안 됨)
    야미쿠라 (치카게): 전개중 라이프 데미지 시 미사용 복귀.
    """
    _yamikura_check(state, pidx)
    p = state.players[pidx]
    if life_before > 3 and p.life <= 3:
        for cid in list(p.used_specials):
            saiki = CARD_DB[cid].get("saiki")
            if saiki and saiki.get("immediate") \
                    and saiki["condition"] == "kessa_becomes":
                p.used_specials.remove(cid)
                p.specials.append(cid)


def _centrifugal_ok(state, pidx) -> bool:
    """원심 사용 조건 (6-2)."""
    no_attack_yet = state.attacks_this_phase[pidx] == 0
    far_enough = (state.distance - state.turn_start_distance) >= 2 \
        or (state.turn_start_distance - state.distance) >= 2
    return no_attack_yet and far_enough


def use_card_from_discard(state, pidx, card_id, agent, rng, as_fullpower=False):
    """버림패에서 카드 사용 (대산맥 리스펙트 두번째 선택지, 인용 등)."""
    p = state.players[pidx]
    if card_id not in p.discard:
        return False
    p.discard.remove(card_id)
    c = CARD_DB[card_id]
    ctx = {"source_card": card_id, "source_megami": c["megami"],
           "as_fullpower": as_fullpower}
    # 연소 키워드: 추가 비용으로 조화결정 1개 연소 (9-7)
    if "burn" in (c.get("card_flags") or []):
        from effects import _burn_harmony
        _burn_harmony(state, pidx, 1)
    ctype = c["type"]
    if ctype == "attack":
        rng_set = parse_range(card_range_str(state, pidx, c))
        if effective_distance(state) in rng_set:
            a, l = parse_damage(card_damage_str(state, pidx, c))
            perform_card_attack(state, pidx, rng_set, a, l, agent, rng,
                                source_card=card_id, card_constants=_constants_of(c))
            if not state.is_over():
                resolve_trigger(state, pidx, c, "attack_after", agent, rng, ctx)
    elif ctype == "action":
        resolve_trigger(state, pidx, c, "main", agent, rng, ctx)
    # 부여가 아닌 카드가 버림패로 갈 상황이면 대신 덮음패로 (대산맥 규칙)
    if ctype != "enhance":
        p.covered.append(card_id)
    else:
        # 부여는 정상 전개 (단순화: 봉납 0으로 전개 후 파기 흐름은 생략)
        p.discard.append(card_id)
    state.check_conservation()
    return True


# ═══════════════════════════════════════════
# 오보로: 설치 / 덮음패에서 사용
# ═══════════════════════════════════════════
def card_range_str(state, pidx, c) -> str:
    """우산 카드: 보유자의 우산 상태에 따른 적정거리 (4-2)."""
    if c.get("range_opened") is not None and state.players[pidx].umbrella_open:
        return c["range_opened"]
    return c["range"]


def card_damage_str(state, pidx, c) -> str:
    """우산 카드: 보유자의 우산 상태에 따른 데미지 (4-2)."""
    if c.get("damage_opened") is not None and state.players[pidx].umbrella_open:
        return c["damage_opened"]
    return c["damage"]


# ═══════════════════════════════════════════
# 쿠루루: 기교(톱니바퀴칸)
# ═══════════════════════════════════════════
# 아이콘 → (분류키, 값)
_GIGYO_ICON = {
    "공": ("type", "attack"), "행": ("type", "action"),
    "부": ("type", "enhance"), "전": ("subtype", "fullpower"),
    "대": ("subtype", "reaction"),
}


def _gigyo_zone_cards(state, pidx):
    """기교 판정 영역: 버림패 + 부여패 + 사용된 비장패 (8-3)."""
    p = state.players[pidx]
    cards = list(p.discard)
    cards += [e.card_id for e in p.enhancements]
    cards += list(p.used_specials)
    return cards


def gigyo_complete(state, pidx, icons: str) -> bool:
    """
    기교 아이콘 문자열(예: '공공', '행행부')이 완성됐는가.
    각 타입/서브타입별 필요 개수 이상이 판정 영역에 존재해야 함.
    """
    from collections import Counter
    need = Counter()
    for ch in icons:
        if ch in _GIGYO_ICON:
            need[_GIGYO_ICON[ch]] += 1
    zone = _gigyo_zone_cards(state, pidx)
    have = Counter()
    for cid in zone:
        c = CARD_DB[cid]
        have[("type", c["type"])] += 1
        if c.get("subtype"):
            have[("subtype", c["subtype"])] += 1
    return all(have[k] >= n for k, n in need.items())


def has_burn(card_id: str) -> bool:
    """연소 키워드 보유 (9-7): 사용 시 조화결정 1개 연소 (추가 비용)."""
    return "burn" in (CARD_DB[card_id].get("card_flags") or [])


def has_setup(card_id: str) -> bool:
    """설치 키워드 보유 여부 (card_flags 또는 텍스트)."""
    c = CARD_DB[card_id]
    return "setup" in (c.get("card_flags") or [])


def legal_covered_setups(state, pidx):
    """재구성 직전, 덮음패에서 사용 가능한 설치 카드 목록."""
    p = state.players[pidx]
    return [cid for cid in p.covered if has_setup(cid)]


def _growth_of(c) -> int:
    """카드의 생육 X 값 (텍스트 '생육N' 파싱)."""
    import re
    text = (c.get("text_ko") or "")
    total = 0
    for m in re.finditer(r"생육\s*(\d+)", text):
        total += int(m.group(1))
    return total


def _megumi_enhance_dedication(state, pidx, c, enh, cap, agent):
    """
    메구미 부여 봉납 (17-7-1):
      1) 여신 카드면 발아 안 한 씨앗 1개 발아
      2) 봉납: 더스트 → 발아씨앗 → 오라 (검수 확정 우선순위)
      3) 생육 X: 발아 씨앗을 X개까지 부여패에 얹음
    씨앗을 부여패에 얹으면 enh.seeds 증가 (벚꽃결정 간주, 별개 계정).
    """
    p = state.players[pidx]
    # 1) 발아 (메구미 오리진은 전부 여신 카드)
    if p.soil_unsprouted > 0:
        p.soil_unsprouted -= 1
        p.soil_sprouted += 1

    # 2) 봉납: 더스트 우선 → 발아 씨앗 → 오라
    need = cap
    take_dust = min(need, state.dust)
    state.dust -= take_dust
    enh.tokens += take_dust
    need -= take_dust
    take_seed = min(need, p.soil_sprouted)
    p.soil_sprouted -= take_seed
    enh.seeds += take_seed          # 씨앗이 부여패로 (벚꽃결정 간주)
    need -= take_seed
    take_aura = min(need, p.aura)
    p.aura -= take_aura
    enh.tokens += take_aura
    need -= take_aura

    # 3) 생육 X: 발아 씨앗을 X개까지 추가로 부여패에 (봉납과 무관한 보너스)
    growth = _growth_of(c)
    if growth > 0 and p.soil_sprouted > 0:
        # 기본정책: 가능한 만큼 전부 얹음 (부여 강화 이득)
        add = min(growth, p.soil_sprouted)
        if hasattr(agent, "choose_count"):
            add = min(growth, agent.choose_count(state, pidx, p.soil_sprouted,
                                                 "growth"))
        p.soil_sprouted -= add
        enh.seeds += add


def use_card_from_covered(state, pidx, card_id, agent, rng, from_setup=True):
    """
    덮음패에서 카드 사용. 설치 보너스(from_covered) 적용.
    공격이면 적정거리 확인, 행동/부여도 지원.
    """
    p = state.players[pidx]
    if card_id not in p.covered:
        return False
    c = CARD_DB[card_id]
    ctx = {"source_card": card_id, "source_megami": c["megami"],
           "from_covered": True}
    p.covered.remove(card_id)
    state.cards_used_this_turn[pidx] += 1
    ctype = c["type"]

    if ctype == "attack":
        rng_set = parse_range(card_range_str(state, pidx, c))
        if effective_distance(state) not in rng_set:
            p.covered.append(card_id)  # 부정 → 되돌림
            return False
        a, l = parse_damage(card_damage_str(state, pidx, c))
        success = perform_card_attack(state, pidx, rng_set, a, l, agent, rng,
                                      source_card=card_id,
                                      card_constants=_constants_of(c),
                                      from_covered=True)
        if success and not state.is_over():
            resolve_trigger(state, pidx, c, "attack_after", agent, rng, ctx)
        p.discard.append(card_id)
    elif ctype == "action":
        resolve_trigger(state, pidx, c, "main", agent, rng, ctx)
        p.discard.append(card_id)
    state.check_conservation()
    return True
