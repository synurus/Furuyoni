"""
effects DSL 인터프리터 (Phase 0의 effects_spec.md 실행기)

역할:
- 조건(condition) 판정: 결사/팔상/경지/연화/개별식
- 연산(ops) 실행: move, buff, draw, attack(서브공격), choose, custom 등

컨텍스트(ctx) 딕셔너리:
  source_card: 효과의 원본 카드 id
  attack: 현재 해결 중인 AttackInstance (있다면)
  enhancement: 이 효과가 붙은 부여패 Enhancement 객체 (있다면)

이번 커밋에서 구현하는 custom:
  full_burst_both, unusable_unless_kessa, cost_reduce_by_opp_aura,
  kwonyeok_master_range, reloading_draw, appdo_reduce (데미지 파이프라인 쪽)
대응 시스템이 필요한 custom (다음 커밋 c):
  usable_as_reaction, reaction_to_special_only(사용 제한만 이번에 처리),
  muonheki_aura, kwonyeok_redirect
"""

import random
from state import GameState


# ═══════════════════════════════════════════
# 조건 판정
# ═══════════════════════════════════════════
def check_condition(state: GameState, pidx: int, cond) -> bool:
    """효과 발동 조건 검사. pidx = 효과 소유자."""
    if cond is None:
        return True
    p = state.players[pidx]
    opp = state.players[1 - pidx]

    if cond == "kessa":      # 결사: 라이프 3 이하
        return p.life <= 3
    if cond == "hassou":     # 팔상: 오라 1 이하
        return p.aura <= 1
    if cond == "kyouchi":    # 경지: 집중력 2
        return p.vigor == 2
    if cond == "opp_aura_full":   # 상대 오라 꽉 참 (결정+동결)
        return opp.aura_full()
    if cond == "opp_frozen_ge3":  # 상대 3개 이상 동결
        return opp.frozen >= 3
    if cond == "opp_frozen":      # 상대 동결됨
        return opp.frozen >= 1
    if cond == "opp_aura_not_full":
        return not opp.aura_full()
    if cond == "umbrella_closed":
        return not p.umbrella_open
    if cond == "umbrella_open":
        return p.umbrella_open
    if cond == "umbrella_toggle":
        return False   # 즉재기 전용 (토글 훅에서 직접 처리)
    if cond == "all_seeds_sprouted":     # 공섬: 토양 씨앗 모두 발아
        return p.all_sprouted()
    if cond == "enh_has_seed":           # 타척: 부여패에 씨앗 있음
        return any(e.seeds > 0 for e in p.enhancements)
    if cond == "opp_deck_ge2":           # 입론: 상대 패산 2장 이상
        return len(opp.deck) >= 2
    if cond == "renka":      # 연화: 이 턴 3장째 이후 사용 카드
        return state.cards_used_this_turn[pidx] >= 3

    if cond == "other_specials_all_used":
        # 대파종 메갈로벨: 이 카드 외 다른 비장패를 모두 사용함
        p = state.players[pidx]
        this = None  # 현재 사용 중인 카드는 이미 손/비장에서 빠졌으므로 미사용에 없음
        return len(p.specials) == 0

    if cond == "used_centrifugal_not_this":
        # 대중력 어트랙트 재기: 이 턴 원심 카드를 썼고 이 카드는 안 씀
        return state._hagane_used_centrifugal.get(pidx, False)

    if isinstance(cond, dict) and "gigyo" in cond:
        from cards import gigyo_complete
        return gigyo_complete(state, pidx, cond["gigyo"])
    if isinstance(cond, dict) and "expr" in cond:
        expr = cond["expr"]
        env = {
            "dust": state.dust,
            "current_range": state.distance,
            "my_life": p.life, "opp_life": opp.life,
            "my_aura": p.aura, "opp_aura": opp.aura,
            "my_flare": p.flare, "opp_flare": opp.flare,
            "hand_count": len(p.hand),
            "fuujin": p.fuujin, "raijin": p.raijin,
            "opp_hand": len(opp.hand),
            "seeds_on_enh": sum(e.seeds for e in p.enhancements),
            "soil_sprouted": p.soil_sprouted,
            "gauge_sum": p.fuujin + p.raijin,
        }
        # 제한된 식 평가 (데이터는 우리가 만든 것이므로 안전)
        return bool(eval(expr, {"__builtins__": {}}, env))

    raise ValueError(f"알 수 없는 조건: {cond}")


# ═══════════════════════════════════════════
# 연산 실행
# ═══════════════════════════════════════════
def resolve_ops(state: GameState, pidx: int, ops: list, agent,
                rng: random.Random, ctx: dict) -> None:
    """효과의 ops 목록을 순서대로 실행. pidx = 효과 소유자."""
    for op in ops:
        if state.is_over():
            return
        _resolve_op(state, pidx, op, agent, rng, ctx)
        state.check_conservation()


def _zone_ref(zone: str, pidx: int, ctx: dict):
    """DSL 존 이름 → tokens.move_tokens용 (zone, pidx) 참조로 변환."""
    opp = 1 - pidx
    mapping = {
        "dust": ("dust", 0),
        "distance": ("distance", 0),
        "my_aura": ("aura", pidx), "opp_aura": ("aura", opp),
        "my_flare": ("flare", pidx), "opp_flare": ("flare", opp),
        "my_life": ("life", pidx), "opp_life": ("life", opp),
    }
    if zone in mapping:
        return mapping[zone]
    if zone == "this_card":
        return ("__this_card__", pidx)  # 특수 처리
    raise ValueError(f"알 수 없는 존: {zone}")


def _move(state, pidx, frm_zone, to_zone, n, ctx, up_to=False):
    """
    존 간 결정 이동. this_card(부여패 위)와 라이프 데미지 특례 처리.
    라이프에서 나가는 이동은 '데미지'가 아니라 '이동'이므로 그대로 이동
    (룰북: 리코일 번 등 라이프→간격은 데미지가 아님 → 플레어 경유 없음).
    """
    from tokens import move_tokens
    from combat import _check_life

    # 부여패 위 결정 처리
    if frm_zone == "this_card" or to_zone == "this_card":
        enh = ctx.get("enhancement")
        if enh is None:
            return 0
        if frm_zone == "this_card":
            moved = min(n, enh.tokens)
            enh.tokens -= moved
            tz, tp = _zone_ref(to_zone, pidx, ctx)
            if tz == "distance": state.distance += moved
            elif tz == "dust": state.dust += moved
            else: setattr(state.players[tp], tz, getattr(state.players[tp], tz) + moved)
        else:
            fz, fp = _zone_ref(frm_zone, pidx, ctx)
            if fz == "distance":
                moved = min(n, state.distance); state.distance -= moved
            elif fz == "dust":
                moved = min(n, state.dust); state.dust -= moved
            else:
                cur = getattr(state.players[fp], fz)
                moved = min(n, cur)
                setattr(state.players[fp], fz, cur - moved)
            enh.tokens += moved
        state.check_conservation()
        return moved

    frm = _zone_ref(frm_zone, pidx, ctx)
    to = _zone_ref(to_zone, pidx, ctx)
    life_before = None
    if frm[0] == "life":
        life_before = state.players[frm[1]].life
    moved = move_tokens(state, frm, to, n)
    # 라이프가 줄어드는 이동: 라이프 0 판정 + 즉재기 훅
    if frm[0] == "life":
        _check_life(state)
        from cards import on_life_reduced
        on_life_reduced(state, frm[1], life_before)
    return moved


def _resolve_op(state: GameState, pidx: int, op: dict, agent,
                rng: random.Random, ctx: dict) -> None:
    from setup import CARD_DB   # 함수 전역 (지역 재import 금지 — 스코프 오염 방지)
    kind = op["op"]
    p = state.players[pidx]
    opp_idx = 1 - pidx
    opp = state.players[opp_idx]

    # ── 결정 이동 ──
    if kind == "move":
        _move(state, pidx, op["from"], op["to"], op["n"], ctx,
              up_to=op.get("up_to", False))
        return

    if kind == "move_either":
        # 간파: a→b 또는 b→a 중 선택
        a, b, n = op["a"], op["b"], op["n"]
        direction = agent.choose_option(
            state, pidx, [f"{a}→{b}", f"{b}→{a}"], "move_either") \
            if hasattr(agent, "choose_option") else 0
        if direction == 0:
            _move(state, pidx, a, b, n, ctx)
        else:
            _move(state, pidx, b, a, n, ctx)
        return

    # ── 서브 공격 (카드에 의하지 않는 공격) ──
    if kind == "attack":
        from combat import perform_card_attack, parse_range, parse_damage
        rng_set = parse_range(op["range"])
        a_dmg, l_dmg = parse_damage(op["dmg"])
        flags = set(op.get("flags", []))
        perform_card_attack(state, pidx, rng_set, a_dmg, l_dmg, agent, rng,
                            source_card=None, flags=flags, is_sub_attack=True,
                            source_megami=ctx.get("source_megami"))
        return

    # ── 공격 수정 (버프) ──
    if kind == "buff":
        target = op["target"]
        buff = {
            "aura": op.get("aura", 0),
            "life": op.get("life", 0),
            "gains": op.get("gains", []),
            "only_if_aura_dmg_le": op.get("only_if_aura_dmg_le"),
        }
        if target == "this_attack":
            atk = ctx.get("attack")
            if atk is not None:
                atk.apply_buff(buff)
        elif target in ("next_attack", "next_attack_other_megami"):
            buff["other_megami_only"] = (target == "next_attack_other_megami")
            buff["source_megami"] = ctx.get("source_megami")
            state.pending_buffs[pidx].append(buff)
        elif target == "reacted_attack":
            atk = ctx.get("reacted_attack")   # 대응 시스템은 커밋 c
            if atk is not None:
                atk.apply_buff(buff)
        elif target == "my_attacks_other_megami":
            pass  # 기염만장: 전개중 지속 버프 → 공격 파이프라인에서 동적 조회
        return

    # ── 카드/손패 조작 ──
    if kind == "draw":
        from deck import draw_card
        n = op["n"]
        who = 1 - pidx if op.get("who") == "opp" else pidx
        if op.get("optional") and hasattr(agent, "choose_yes_no"):
            if not agent.choose_yes_no(state, pidx, f"draw{n}"):
                return
        for _ in range(n):
            draw_card(state, who, rng=rng, agent=agent)
            if state.is_over():
                return
        return

    if kind == "cover_from_hand":
        for _ in range(op["n"]):
            if not p.hand:
                break
            idx = agent.choose_card_to_cover(state, pidx, p.hand) \
                if hasattr(agent, "choose_card_to_cover") else 0
            p.covered.append(p.hand.pop(idx))
        return

    if kind == "opp_discard_non_attack":
        # 무궁한 바람: 상대는 손패에서 공격이 아닌 카드 1장을 버린다. 불가능하면 손패 공개.
        non_attacks = [c for c in opp.hand if CARD_DB[c]["type"] != "attack"]
        if non_attacks:
            # 상대가 선택 (v1: 첫 장)
            chosen = non_attacks[0]
            opp.hand.remove(chosen)
            opp.discard.append(chosen)
        # else: 손패 공개 (정보 공개는 v1 관찰 모델에서 생략)
        return

    if kind == "deck_bottom_from_discard_or_cover":
        # 부채 뒤집기: 버림패/덮음패에서 최대 n장을 골라 패산 밑으로
        n = op["n"]
        pool = [("discard", c) for c in p.discard] + [("covered", c) for c in p.covered]
        take = min(n, len(pool))
        for _ in range(take):
            if not pool:
                break
            src, cid = pool.pop(0)  # v1 기본정책: 앞에서부터
            getattr(p, src).remove(cid)
            p.deck.insert(0, cid)   # index 0 = 패산 맨 밑
        return

    if kind == "return_this_to_deck_top":
        # 빗어내리기: 이 카드를 패산 맨 위로 (버림패로 가는 대신)
        ctx["card_to_deck_top"] = True
        return

    if kind == "reconstruct_deck":
        from deck import reconstruct_deck
        reconstruct_deck(state, pidx, by_rule=False, rng=rng)  # 효과 재구성: 무데미지
        return

    # ── 플레이어 수치 ──
    if kind == "umbrella_toggle":
        # 우산의 개폐 (4-3) + 훅: 흩날리는 눈꽃 즉재기(개폐마다), 우산 돌리기 공개(1회)
        p.umbrella_open = not p.umbrella_open
        _DB = CARD_DB
        for cid2 in list(p.used_specials):
            sk = _DB[cid2].get("saiki")
            if sk and sk.get("condition") == "umbrella_toggle":
                p.used_specials.remove(cid2)
                p.specials.append(cid2)
        for cid2 in p.hand:
            fx_list = _DB[cid2].get("effects") or []
            if any(op2.get("id") == "umbrella_reveal"
                   for fx2 in fx_list for op2 in fx2["ops"]):
                want = agent.choose_yes_no(state, pidx, "umbrella_reveal") \
                    if hasattr(agent, "choose_yes_no") else True
                if want:
                    from tokens import move_tokens
                    move_tokens(state, ("dust", None), ("aura", pidx), 1)
                break
        return

    if kind == "return_this_to_hand":
        ctx["card_to_hand"] = True
        return

    if kind == "execute_scheme":
        # 계략을 실행하고 다음 계략을 준비 (5-2)
        p2 = state.players[pidx]
        scheme = p2.scheme
        if scheme == "shinsan":
            _resolve_scheme_effect(state, pidx, op["shinsan"], agent, rng, ctx)
        elif scheme == "kimou":
            _resolve_scheme_effect(state, pidx, op["kimou"], agent, rng, ctx)
        # 다음 계략 준비 (비밀 선택 — 봇 정책)
        if not state.is_over():
            if hasattr(agent, "choose_scheme"):
                p2.scheme = agent.choose_scheme(state, pidx)
            else:
                p2.scheme = "shinsan"
        return

    if kind == "seal_from_discard":
        # 완전논파/논파: 상대 버림패 1장을 봉인
        opp2 = state.players[1 - pidx]
        if opp2.discard:
            pick = agent.choose_option(
                state, pidx, [CARD_DB[c]["name_ko"] for c in opp2.discard],
                "seal") if hasattr(agent, "choose_option") else 0
            card = opp2.discard.pop(pick)
            state.players[pidx].sealed_cards.append(card)
            ctx["sealed_card"] = card
        return

    if kind == "basic_free":
        from tokens import legal_basic_actions, perform_basic_action
        acts = legal_basic_actions(state, pidx)
        if acts and hasattr(agent, "choose_free_basic_action"):
            ch = agent.choose_free_basic_action(state, pidx, acts)
            if ch:
                perform_basic_action(state, pidx, ch)
        return

    if kind == "burn_harmony":
        # 조화결정 연소: 머신→연소됨 (마스터피스 전개중이면 반대)
        n = op.get("n", 1)
        _burn_harmony(state, pidx, n)
        return

    if kind == "recover_harmony":
        # 연소된 조화결정 회복: 연소됨→머신
        n = op.get("n", 99 if op.get("all") else 1)
        moved = min(n, p.burned_harmony)
        p.burned_harmony -= moved
        p.machine_harmony += moved
        ctx["recovered_harmony"] = moved
        return

    if kind == "kidou":
        # 기동 (9-8): 기동전진(간격-1) or 기동후퇴(간격+1)
        _perform_kidou(state, pidx, agent, ctx)
        return

    if kind == "transform":
        # TransForm: 추가패의 변신 카드 1장을 머신으로 + 변형시 효과
        _perform_transform(state, pidx, agent, rng, ctx)
        return

    if kind == "gain_gauge":
        # 라이라: 풍신/뇌신 게이지 증가 (상한 20)
        which = op["which"]   # 'fuujin' | 'raijin' | 'both'
        n = op.get("n", 1)
        targets = ["fuujin", "raijin"] if which == "both" else [which]
        for g in targets:
            setattr(p, g, min(20, getattr(p, g) + n))
        return

    if kind == "double_raijin":
        # 울부짖기: 뇌신 게이지 2배 (상한 20)
        p.raijin = min(20, p.raijin * 2)
        return

    if kind == "gain_vigor":
        from turn import gain_vigor
        gain_vigor(state, pidx, op["n"])
        return

    if kind == "set_vigor":
        who = pidx if op["who"] == "me" else opp_idx
        state.players[who].vigor = max(0, min(2, op["n"]))
        return

    if kind == "wither":
        who = pidx if op["who"] == "me" else opp_idx
        state.players[who].withered = True
        return

    if kind == "freeze":
        # 동결 (13-5): 게임 바깥 → 대상 오라에 동결 토큰. 오라 상한(결정+동결) 초과 불가.
        who = opp_idx if op.get("who", "opp") == "opp" else pidx
        from constants import AURA_MAX
        target = state.players[who]
        n = op.get("n", 1)
        for _ in range(n):
            if target.aura + target.frozen >= AURA_MAX:
                break
            target.frozen += 1
        _check_aura_full_saiki(state, who)
        return

    if kind == "freeze_until_full":
        # 오라 빈칸이 없어질 때까지 동결 (콘루/절대영도)
        who = opp_idx if op.get("who", "opp") == "opp" else pidx
        from constants import AURA_MAX
        target = state.players[who]
        while target.aura + target.frozen < AURA_MAX:
            target.frozen += 1
        _check_aura_full_saiki(state, who)
        return

    if kind == "basic_actions":
        # 카드 효과에 의한 기본동작: 비용 없음 (9-6 ii 괄호)
        from tokens import legal_basic_actions, perform_basic_action
        n = op["n"]
        for _ in range(n):
            options = legal_basic_actions(state, pidx)
            if not options:
                break
            if op.get("up_to") and hasattr(agent, "choose_free_basic_action"):
                choice = agent.choose_free_basic_action(state, pidx, options)
            else:
                choice = agent.choose_free_basic_action(state, pidx, options) \
                    if hasattr(agent, "choose_free_basic_action") else options[0]
            if choice is None:  # up_to에서 그만두기 선택
                break
            perform_basic_action(state, pidx, choice)
        return

    # ── 선택 ──
    if kind == "choose":
        options = op["options"]
        idx = agent.choose_option(state, pidx,
                                  [str(o) for o in options], "effect_choice") \
            if hasattr(agent, "choose_option") else 0
        resolve_ops(state, pidx, options[idx], agent, rng, ctx)
        return

    # ── 대응 관련 (커밋 c에서 활성화) ──
    if kind == "cancel_reacted_attack":
        atk = ctx.get("reacted_attack")
        if atk is not None:
            if op.get("non_special") and atk.is_special:
                return
            atk.cancelled = True
        return

    # ── custom ──
    if kind == "custom":
        _resolve_custom(state, pidx, op["id"], agent, rng, ctx)
        return

    if kind == "remove_from_game":
        # 이 카드를 게임에서 제거 (대파종 메갈로벨)
        ctx["remove_from_game"] = True
        return

    raise ValueError(f"알 수 없는 op: {kind}")


# ═══════════════════════════════════════════
# custom 효과
# ═══════════════════════════════════════════
def _resolve_custom(state, pidx, cid, agent, rng, ctx):
    """
    즉발형 custom만 여기서 처리.
    판정형 custom(사용가능 여부, 데미지 수정 등)은 각 파이프라인에서 조회:
      unusable_unless_kessa   → cards.legal_moves
      cost_reduce_by_opp_aura → cards.effective_cost
      full_burst_both         → combat (공격 플래그)
      appdo_reduce            → combat (데미지 수정)
      kwonyeok_master_range   → state.master_range 계산
      reloading_draw          → turn.end_phase
      reaction_to_special_only→ cards.legal_moves (사용 제한)
      usable_as_reaction, muonheki_aura, kwonyeok_redirect → 커밋 c
    """
    from setup import CARD_DB   # 함수 전역에서 사용 (지역 재import 금지)
    # 콘루 루얀페: 상대가 오라 데미지를 선택했다면 오라 꽉 찰 때까지 동결
    if cid == "konru_freeze_if_aura":
        if getattr(state, "_last_damage_choice", None) == "aura":
            from constants import AURA_MAX
            t = state.players[1 - pidx]
            while t.aura + t.frozen < AURA_MAX:
                t.frozen += 1
        return

    # 대산맥 리스펙트: 2번까지 선택 (패산 전부 버림 / 버림패의 비전력 카드 1장 사용)
    if cid == "daesanmaek_choose":
        p = state.players[pidx]
        for _ in range(2):
            branches = ["패산 전부 버림패로", "버림패의 비전력 카드 1장 사용", "그만두기"]
            idx = agent.choose_option(state, pidx, branches, "daesanmaek") \
                if hasattr(agent, "choose_option") else 0
            if idx == 0:
                p.discard.extend(p.deck)
                p.deck.clear()
            elif idx == 1:
                usable = [c for c in p.discard
                          if CARD_DB[c].get("subtype") != "fullpower"]
                if usable:
                    pick = agent.choose_option(
                        state, pidx,
                        [CARD_DB[c]["name_ko"] for c in usable], "daesanmaek_use") \
                        if hasattr(agent, "choose_option") else 0
                    from cards import use_card_from_discard
                    use_card_from_discard(state, pidx, usable[pick], agent, rng)
            else:
                break
            if state.is_over():
                return
        return

    # 절대영도: 휘감기 1회 + 상대 3개 이상 동결이면 추가 1회
    if cid == "jeoldae_gather":
        from tokens import gather
        gather(state, pidx)
        if state.players[1 - pidx].frozen >= 3:
            gather(state, pidx)
        return

    # ── 오보로 ──
    # 참격난무: 이 턴 상대가 오라 데미지를 받았으면 +1/+1 (상시)
    if cid == "chamgyeok_aura_dmg_taken":
        atk = ctx.get("attack")
        if atk is not None and state._oboro_opp_took_aura_dmg.get(1 - pidx, False):
            atk.apply_buff({"aura": 1, "life": 1, "gains": []})
        return

    # 닌자걸음(덮음패): 덮음패에서 설치 카드 1장 추가 사용
    if cid == "ninja_extra_setup":
        from cards import legal_covered_setups, use_card_from_covered
        setups = legal_covered_setups(state, pidx)
        if setups:
            pick = agent.choose_option(
                state, pidx,
                [f"{s} 사용" for s in setups] + ["안 함"], "ninja_setup") \
                if hasattr(agent, "choose_option") else len(setups)
            if pick < len(setups):
                use_card_from_covered(state, pidx, setups[pick], agent, rng)
        return

    # 분신술: 손패 1장 덮고, 추가 공격 3-4 1/1 + 휘감기
    if cid == "bunsin_extra":
        p = state.players[pidx]
        if p.hand and (agent.choose_yes_no(state, pidx, "bunsin")
                       if hasattr(agent, "choose_yes_no") else True):
            idx = agent.choose_card_to_cover(state, pidx, p.hand) \
                if hasattr(agent, "choose_card_to_cover") else 0
            p.covered.append(p.hand.pop(idx))
            from combat import perform_card_attack
            perform_card_attack(state, pidx, {3, 4}, 1, 1, agent, rng,
                                is_sub_attack=True,
                                source_megami=ctx.get("source_megami"))
            if not state.is_over():
                from tokens import gather
                gather(state, pidx)
        return

    # 생체활성 파기시: 사용한 비장패 1장을 미사용으로
    if cid == "saengche_recover_special":
        p = state.players[pidx]
        if p.used_specials:
            pick = agent.choose_option(
                state, pidx,
                [CARD_DB[c]["name_ko"] for c in p.used_specials], "saengche") \
                if hasattr(agent, "choose_option") else 0
            cid2 = p.used_specials.pop(pick)
            p.specials.append(cid2)
        return

    # 쿠마스케: 덮음패 장수만큼 3-4 2/2 추가 공격
    if cid == "kumasuke_repeat":
        from combat import perform_card_attack
        x = len(state.players[pidx].covered)
        for _ in range(x):
            if state.is_over():
                break
            perform_card_attack(state, pidx, {3, 4}, 2, 2, agent, rng,
                                is_sub_attack=True,
                                source_megami=ctx.get("source_megami"))
        return

    # 토비카게(대응): 덮음패의 비전력 카드 1장 사용
    if cid == "tobikage_use_covered":
        from cards import use_card_from_covered
        p = state.players[pidx]
        pool = [c for c in p.covered if CARD_DB[c].get("subtype") != "fullpower"]
        if pool:
            pick = agent.choose_option(
                state, pidx,
                [CARD_DB[c]["name_ko"] for c in pool] + ["안 함"], "tobikage") \
                if hasattr(agent, "choose_option") else len(pool)
            if pick < len(pool):
                use_card_from_covered(state, pidx, pool[pick], agent, rng)
        return

    # 우로우오 전개시: 버림패를 원하는 만큼 덮음패로
    if cid == "urouo_discard_to_covered":
        p = state.players[pidx]
        # 기본정책: 전부 이동 (덮음패 활용 극대화)
        n = len(p.discard)
        if hasattr(agent, "choose_count"):
            n = agent.choose_count(state, pidx, len(p.discard), "urouo")
        for _ in range(n):
            if p.discard:
                p.covered.append(p.discard.pop())
        return

    # 우로우오 전개중(양방향 화살표): v1 단순화 — 덮음패 사용 시 move_either로 이미 유연
    if cid == "urouo_bidirectional":
        return  # 표식만 (덮음패 사용 시 방향 선택은 개별 카드 처리)

    # ── 라이라 ──
    # 풍뢰격: X = min(풍신, 뇌신)  (상시, 데미지 해결 시 오라뎀에 반영)
    if cid == "poongnoe_x":
        atk = ctx.get("attack")
        if atk is not None:
            p = state.players[pidx]
            atk.aura = min(p.fuujin, p.raijin)
        return

    # 윤회의 손톱: 버림패의 공격 카드 1장을 패산 맨 위로 (선택)
    if cid == "yunhoe_recover_attack":
        p = state.players[pidx]
        atks = [c for c in p.discard if CARD_DB[c]["type"] == "attack"]
        if atks and (agent.choose_yes_no(state, pidx, "yunhoe")
                     if hasattr(agent, "choose_yes_no") else True):
            pick = agent.choose_option(
                state, pidx, [CARD_DB[c]["name_ko"] for c in atks], "yunhoe") \
                if hasattr(agent, "choose_option") else 0
            cid2 = atks[pick]
            p.discard.remove(cid2)
            p.deck.append(cid2)  # 맨 위
        return

    # 풍뢰의 지혜: 게이지 합 조건부 효과
    if cid == "poongnoe_wisdom":
        p = state.players[pidx]
        gsum = p.fuujin + p.raijin
        if gsum >= 4:
            # 버림패의 '다른 여신' 카드 1장을 패산 위로 — v1 단일 여신이라 해당 없음
            pass
        if gsum >= 12:
            from deck import reconstruct_deck, draw_card
            reconstruct_deck(state, pidx, by_rule=False, rng=rng, agent=agent)
            draw_card(state, pidx, rng=rng, agent=agent)
            ctx["remove_from_game"] = True
        return

    # 울부짖기: 두 선택지 중 하나
    if cid == "ulboo_choose":
        p = state.players[pidx]
        opts = ["상대 위축 + 각 게이지 +1", "뇌신 게이지 2배"]
        idx = agent.choose_option(state, pidx, opts, "ulboo") \
            if hasattr(agent, "choose_option") else 0
        if idx == 0:
            state.players[1 - pidx].withered = True
            p.fuujin = min(20, p.fuujin + 1)
            p.raijin = min(20, p.raijin + 1)
        else:
            p.raijin = min(20, p.raijin * 2)
        # 그 후 게이지 합 12+ 면 기본동작 2회까지
        if p.fuujin + p.raijin >= 12:
            from tokens import legal_basic_actions, perform_basic_action
            for _ in range(2):
                acts = legal_basic_actions(state, pidx)
                if not acts: break
                ch = agent.choose_free_basic_action(state, pidx, acts) \
                    if hasattr(agent, "choose_free_basic_action") else None
                if ch is None: break
                perform_basic_action(state, pidx, ch)
        return

    # 풍마전회: 사용완료 비장패 1장을 미사용으로
    if cid == "poongma_jeonhoe_recover":
        p = state.players[pidx]
        if p.used_specials:
            pick = agent.choose_option(
                state, pidx, [CARD_DB[c]["name_ko"] for c in p.used_specials],
                "jeonhoe") if hasattr(agent, "choose_option") else 0
            cid2 = p.used_specials.pop(pick)
            p.specials.append(cid2)
        return

    # 풍마초래공: 풍신 게이지에 따라 추가패에서 비장패 획득
    if cid == "poongma_summon":
        p = state.players[pidx]
        gain = []
        if p.fuujin >= 3: gain.append("poongma_seonpung")
        if p.fuujin >= 7: gain.append("poongma_jeonhoe")
        if p.fuujin >= 12: gain.append("poongma_cheongudo")
        # 추가패 id 매핑 (실제 카드 id로)
        name_to_id = {c: k for k, cc in CARD_DB.items()
                      if isinstance(cc, dict)
                      for c in [cc.get("_summon_key")] if c}
        for key in gain:
            cid2 = name_to_id.get(key)
            if cid2 and cid2 not in p.specials:
                p.specials.append(cid2)
        ctx["remove_from_game"] = True
        return

    # 천뢰소환진: 뇌신 게이지 절반(올림)만큼 0-10 1/1 공격
    if cid == "cheonroe_summon":
        from combat import perform_card_attack
        x = (state.players[pidx].raijin + 1) // 2
        for _ in range(x):
            if state.is_over(): break
            perform_card_attack(state, pidx, set(range(0, 11)), 1, 1, agent, rng,
                                is_sub_attack=True,
                                source_megami=ctx.get("source_megami"))
        return

    # 원환륜회선 전개중: 상대 공격 해결 후 간격⇔더스트1 + 게이지1 (선택)
    if cid == "wonhwan_after_opp_attack":
        return  # deploy_during 표식 — 실제 처리는 combat 대응 훅에서 (v1 단순화)

    # ── 치카게 ──
    # 독 심기: 독주머니에서 마비/환각/이완 중 1장 → 상대 패산 맨 위
    if cid == "plant_poison_deck" or cid == "plant_poison_hand":
        p = state.players[pidx]
        basics = [c for c in p.poison_pouch
                  if CARD_DB[c]["name_ko"] in ("마비독", "환각독", "이완독")]
        if not basics:
            return
        names = [CARD_DB[c]["name_ko"] for c in basics]
        pick = agent.choose_option(state, pidx, names, "plant_poison") \
            if hasattr(agent, "choose_option") else 0
        chosen = basics[pick]
        p.poison_pouch.remove(chosen)
        opp = state.players[1 - pidx]
        if cid == "plant_poison_deck":
            opp.deck.append(chosen)     # 맨 위
        else:
            opp.hand.append(chosen)
        return

    # 멸등의 영혼독: 멸등독 1장 → 상대 패산 맨 위
    if cid == "plant_myeoldeung_deck":
        p = state.players[pidx]
        myeol = [c for c in p.poison_pouch
                 if CARD_DB[c]["name_ko"] == "멸등독"]
        if myeol:
            p.poison_pouch.remove(myeol[0])
            state.players[1 - pidx].deck.append(myeol[0])
        return

    # 독 카드 사용 후 복귀 (마비독/환각독): 상대 독주머니로
    if cid == "poison_return":
        ctx["return_to_pouch"] = True
        return

    # 이완독 파기시: 상대 독주머니로
    if cid == "poison_return_on_destroy":
        ctx["return_to_pouch"] = True
        return

    # 암기 전력화 공격후: 상대가 독주머니에서 1장 골라 자기 손패로
    if cid == "amki_opp_take_poison":
        p = state.players[pidx]
        opp_idx2 = 1 - pidx
        if p.poison_pouch:
            names = [CARD_DB[c]["name_ko"] for c in p.poison_pouch]
            pick = agent.choose_option(state, opp_idx2, names, "amki_take") \
                if hasattr(agent, "choose_option") else 0
            chosen = p.poison_pouch.pop(pick)
            state.players[opp_idx2].hand.append(chosen)
        return

    # 암기 공격후(공통): 상대 손패에 독이 있으면 휘감기 1회
    if cid == "amki_gather_if_poison":
        opp = state.players[1 - pidx]
        if any(CARD_DB[c].get("base_type") == "poison" for c in opp.hand):
            from tokens import gather
            gather(state, pidx)
        return

    # 둔술: 이 턴 상대 전진 금지
    if cid == "dunsul_no_advance":
        state._no_advance_this_turn[1 - pidx] = True
        return

    # 야미쿠라 파기시: 다른 비장패 모두 사용완료면 승리
    if cid == "yamikura_win":
        p = state.players[pidx]
        others_unused = [c for c in p.specials]
        deployed_specials = [e for e in p.enhancements
                             if CARD_DB[e.card_id].get("base_type") == "special"]
        if not others_unused and not deployed_specials:
            state.winner = pidx
            state.end_reason = "yamikura"
        return

    # ── 쿠루루 (기교) ──
    # 엘레키텔 기교: 라이프 1 데미지
    if cid == "kururu_life_dmg_1":
        from combat import resolve_single_life_damage
        resolve_single_life_damage(state, 1 - pidx, 1)
        return

    # 토네이도 기교별 데미지 (각 독립 완성)
    if cid == "tornado_aura5":
        from combat import resolve_single_aura_damage
        resolve_single_aura_damage(state, 1 - pidx, 5)
        return
    if cid == "tornado_flare1":
        opp2 = state.players[1 - pidx]
        moved = min(1, opp2.flare)
        opp2.flare -= moved
        state.dust += moved
        return
    if cid == "tornado_life1":
        from combat import resolve_single_life_damage
        resolve_single_life_damage(state, 1 - pidx, 1)
        return

    # 액셀러 기교: 손패 전력 카드 1장 사용
    if cid == "accel_use_fullpower":
        p2 = state.players[pidx]
        fps = [c for c in p2.hand if CARD_DB[c].get("subtype") == "fullpower"]
        if fps and (agent.choose_yes_no(state, pidx, "accel")
                    if hasattr(agent, "choose_yes_no") else False):
            pick = agent.choose_option(
                state, pidx, [CARD_DB[c]["name_ko"] for c in fps], "accel_use") \
                if hasattr(agent, "choose_option") else 0
            from cards import use_card as _use_card
            _use_card(state, pidx, "hand", fps[pick], agent, rng,
                      as_fullpower=True)
        return

    # 쿠루룽 대응: 2개까지 선택 (뽑기 / 덮음패 패산밑 / 상대 손패 버림)
    if cid == "kururung_choose2":
        p2 = state.players[pidx]
        opts = ["카드 1장 뽑기", "덮음패 1장 패산 밑", "상대 손패 1장 버림"]
        done = set()
        for _ in range(2):
            avail = [i for i in range(3) if i not in done]
            labels = [opts[i] for i in avail] + ["그만"]
            pick = agent.choose_option(state, pidx, labels, "kururung") \
                if hasattr(agent, "choose_option") else len(avail)
            if pick >= len(avail):
                break
            choice = avail[pick]
            done.add(choice)
            if choice == 0:
                from deck import draw_card
                draw_card(state, pidx, rng=rng, agent=agent)
            elif choice == 1:
                if p2.covered:
                    p2.deck.insert(0, p2.covered.pop(0))
            elif choice == 2:
                opp2 = state.players[1 - pidx]
                if opp2.hand:
                    opp2.discard.append(opp2.hand.pop(0))
        return

    # 리게이너 기교: 다른 여신 카드 1장 사용 (v1 단일 여신 → 대상 없음)
    if cid == "regainer_use_other":
        return

    # 모듀르: 행동 카드 사용 후 기본동작 1회 (전개중)
    if cid == "modur_action_basic":
        return  # 표식 (use_card 후 조회)

    # 리플렉터 기교: 이 카드 위 결정 4개 (더스트에서)
    if cid == "reflector_gain4":
        enh = ctx.get("enhancement")
        if enh is not None:
            add = min(4, state.dust)
            state.dust -= add
            enh.tokens += add
        return
    if cid == "reflector_block2nd":
        return  # 표식 (combat에서 상대 2번째 공격 무효화 조회)

    # 인더스트리아: 카드 봉인 + 듀플리기어를 패산 밑에
    if cid == "industria_seal_duplicate":
        p2 = state.players[pidx]
        # 봉인 (손패/버림패의 비부여 카드 1장)
        pool = [c for c in (p2.hand + p2.discard)
                if CARD_DB[c]["type"] != "enhance"]
        if pool and not getattr(p2, "_industria_sealed", None):
            if agent.choose_yes_no(state, pidx, "industria_seal") \
                    if hasattr(agent, "choose_yes_no") else True:
                pick = agent.choose_option(
                    state, pidx, [CARD_DB[c]["name_ko"] for c in pool],
                    "industria") if hasattr(agent, "choose_option") else 0
                card = pool[pick]
                if card in p2.hand:
                    p2.hand.remove(card)
                else:
                    p2.discard.remove(card)
                p2._industria_sealed = card
        # 듀플리기어를 패산 밑에 (추가패에서, 최대 3장)
        dup_id = "10-kururu-o-s-3-ex1"
        placed = p2.deck.count(dup_id) + p2.hand.count(dup_id) \
            + p2.discard.count(dup_id)
        if placed < 3:
            p2.deck.insert(0, dup_id)
        return

    # 듀플리기어: 인더스트리아 봉인 카드의 복제로 사용
    if cid == "duplicate_of_industria":
        p2 = state.players[pidx]
        sealed = getattr(p2, "_industria_sealed", None)
        if sealed is not None:
            # 봉인된 카드를 복제 사용 (효과만 해결)
            sc = CARD_DB[sealed]
            resolve_trigger(state, pidx, sc, "main", agent, rng, ctx)
        return

    # 신섭장치 기교: 상대 비장 1장 사용완료로
    if cid == "sinseop_see_specials":
        opp2 = state.players[1 - pidx]
        if opp2.specials:
            pick = agent.choose_option(
                state, pidx, [CARD_DB[c]["name_ko"] for c in opp2.specials],
                "sinseop") if hasattr(agent, "choose_option") else 0
            opp2.used_specials.append(opp2.specials.pop(pick))
        return

    # 신섭장치: 상대 사용완료 비장 1장 사용 후 제외
    if cid == "sinseop_use_opp_special":
        opp2 = state.players[1 - pidx]
        if opp2.used_specials and (agent.choose_yes_no(state, pidx, "sinseop2")
                                   if hasattr(agent, "choose_yes_no") else False):
            pick = agent.choose_option(
                state, pidx, [CARD_DB[c]["name_ko"] for c in opp2.used_specials],
                "sinseop_use") if hasattr(agent, "choose_option") else 0
            card = opp2.used_specials[pick]
            sc = CARD_DB[card]
            resolve_trigger(state, pidx, sc, "main", agent, rng, ctx)
        ctx["remove_from_game"] = True
        return

    # ── 탈리야 (조화결정) ──
    # Shield Charge 상시: 데미지 벚꽃결정을 간격으로
    if cid == "shield_dmg_to_distance":
        atk = ctx.get("attack")
        if atk is not None:
            atk.dmg_to_distance = True
        return

    # Dual Action: 버림패의 다른 여신 전력 아닌 공격 사용
    if cid == "dual_use_discard_attack":
        p2 = state.players[pidx]
        pool = [c for c in p2.discard
                if CARD_DB[c]["type"] == "attack"
                and CARD_DB[c].get("subtype") != "fullpower"
                and CARD_DB[c]["megami"] != "thallya"]
        if pool:
            pick = agent.choose_option(
                state, pidx, [CARD_DB[c]["name_ko"] for c in pool] + ["안 함"],
                "dual") if hasattr(agent, "choose_option") else len(pool)
            if pick < len(pool):
                from cards import use_card as _uc
                card = pool[pick]
                p2.discard.remove(card)
                p2.hand.append(card)
                _uc(state, pidx, "hand", card, agent, rng)
        return

    # Roaring: 두 코스트 선택형
    if cid == "roaring_choose":
        p2 = state.players[pidx]
        # 위: 조화결정 2개 연소 → 집중+1, 플레어(상대)→더스트1
        if p2.machine_harmony >= 2 and (
                agent.choose_yes_no(state, pidx, "roaring_top")
                if hasattr(agent, "choose_yes_no") else True):
            _burn_harmony(state, pidx, 2)
            p2.vigor = min(2, p2.vigor + 1)
            opp2 = state.players[1 - pidx]
            moved = min(1, opp2.flare)
            opp2.flare -= moved
            state.dust += moved
        # 아래: 집중 2 지불 → 연소된 조화결정 3개 회복
        if p2.vigor >= 2 and (
                agent.choose_yes_no(state, pidx, "roaring_bottom")
                if hasattr(agent, "choose_yes_no") else True):
            p2.vigor -= 2
            rec = min(3, p2.burned_harmony)
            p2.burned_harmony -= rec
            p2.machine_harmony += rec
        return

    # Omega-Burst: 회복한 조화결정 수 X 이하 오라뎀 공격 무효화
    if cid == "omega_cancel":
        ra = ctx.get("reacted_attack")
        x = ctx.get("recovered_harmony", 0)
        if ra is not None:
            if ra.aura is None or ra.aura <= x:
                ra.cancelled = True
        return

    # Julia's BlackBox: 머신에 조화결정 없으면 TransForm+회복2, 아니면 미사용
    if cid == "julia_transform":
        p2 = state.players[pidx]
        if p2.machine_harmony == 0:
            _perform_transform(state, pidx, agent, rng, ctx)
            rec = min(2, p2.burned_harmony)
            p2.burned_harmony -= rec
            p2.machine_harmony += rec
        else:
            ctx["card_to_specials"] = True   # 미사용으로 되돌림
        return

    # 마스터피스 전개중 표식 (연소 반전 — _burn_harmony에서 조회)
    if cid == "masterpiece_reverse":
        return

    # TransForm 변형시 효과
    if cid == "yaksha_transform":
        opp2 = state.players[1 - pidx]
        opp2.withered = True
        opp2._draw_limit_next = 1   # 다음 개시 1장만
        return
    if cid == "naga_transform":
        opp2 = state.players[1 - pidx]
        if opp2.flare >= 3:
            move = opp2.flare - 2
            opp2.flare -= move
            state.dust += move
        return
    if cid == "garuda_no_hand_limit":
        state._no_hand_limit[pidx] = True
        return

    # ── 신라 ──
    # 입론: 상대 패산 2장 이상이면 데미지 대신 패산 위 2장 덮음
    if cid == "ipron_deck_cover":
        atk = ctx.get("attack")
        if atk is not None:
            atk.aura = None   # 데미지 무효화 (덮음으로 대체)
            atk.life = None
        opp = state.players[1 - pidx]
        for _ in range(2):
            if opp.deck:
                opp.covered.append(opp.deck.pop())
        return

    # 반론: 대응한 비장 아니고 오라뎀 '-'가 아닌 공격 무효화
    if cid == "ballon_cancel":
        ra = ctx.get("reacted_attack")
        if ra is not None and not ra.is_special and ra.aura is not None:
            ra.cancelled = True
        return

    # 궤변 신산: 상대 패산 위 3장 덮음
    if cid == "gebyeon_shinsan":
        opp = state.players[1 - pidx]
        for _ in range(3):
            if opp.deck:
                opp.covered.append(opp.deck.pop())
        return

    # 궤변 귀모: 상대 버림패 1장 사용 가능
    if cid == "gebyeon_kimou":
        opp = state.players[1 - pidx]
        if opp.discard and (agent.choose_yes_no(state, pidx, "gebyeon")
                            if hasattr(agent, "choose_yes_no") else False):
            pick = agent.choose_option(
                state, pidx, [CARD_DB[c]["name_ko"] for c in opp.discard],
                "gebyeon_use") if hasattr(agent, "choose_option") else 0
            # 단순화: 상대 버림패 카드를 상대 손패로 (실제 사용 위임은 복잡)
            opp.hand.append(opp.discard.pop(pick))
        return

    # 인용: 상대 손패 공격 카드 1장 사용 or 덮음 (봇 전지적 → 단순화: 덮음)
    if cid == "inyong_use_opp_attack":
        opp = state.players[1 - pidx]
        attacks = [c for c in opp.hand if CARD_DB[c]["type"] == "attack"]
        if attacks:
            pick = agent.choose_option(
                state, pidx, [CARD_DB[c]["name_ko"] for c in attacks] + ["안 함"],
                "inyong") if hasattr(agent, "choose_option") else len(attacks)
            if pick < len(attacks):
                card = attacks[pick]
                opp.hand.remove(card)
                opp.covered.append(card)   # 단순화: 덮음패로
        return

    # 장담 신산: 집중 +1, 이 카드 패산 위로
    if cid == "jangdam_shinsan":
        p2 = state.players[pidx]
        p2.vigor = min(2, p2.vigor + 1)
        ctx["card_to_deck_top"] = True
        return

    # 장담 귀모: 상대 손패 1장 이하면 위축+3장 뽑고 2장 버림
    if cid == "jangdam_kimou":
        opp = state.players[1 - pidx]
        if len(opp.hand) <= 1:
            opp.withered = True
            from deck import draw_card
            for _ in range(3):
                draw_card(state, 1 - pidx, rng=rng, agent=agent)
            for _ in range(2):
                if opp.hand:
                    opp.discard.append(opp.hand.pop(0))
        return

    # 논파 전개시: 상대 패산 맨 위 1장 버림
    if cid == "nonpa_deck_discard":
        opp = state.players[1 - pidx]
        if opp.deck:
            opp.discard.append(opp.deck.pop())
        return

    # 논파 파기시: 봉인된 카드를 버림패로 복귀
    if cid == "nonpa_unseal":
        p2 = state.players[pidx]
        enh = ctx.get("enhancement")
        # 이 논파에 봉인된 카드 (단순화: 봉인 영역 전체를 상대 버림패로)
        opp = state.players[1 - pidx]
        while p2.sealed_cards:
            opp.discard.append(p2.sealed_cards.pop())
        return

    # 일절이해 신산: 버림패/사용 비장의 부여 1장 무료 사용 (단순화: 미사용 비장 복귀)
    if cid == "iljeol_shinsan":
        p2 = state.players[pidx]
        enh_cards = [c for c in p2.discard
                     if CARD_DB[c]["type"] == "enhance"]
        if enh_cards:
            card = enh_cards[0]
            p2.discard.remove(card)
            from state import Enhancement
            p2.enhancements.append(Enhancement(card, tokens=0, seeds=0))
        return

    # 일절이해 귀모: 상대 비장 아닌 부여패 1장의 결정 전부 더스트
    if cid == "iljeol_kimou":
        opp = state.players[1 - pidx]
        targets = [e for e in opp.enhancements
                   if CARD_DB[e.card_id].get("base_type") != "special"]
        if targets:
            pick = agent.choose_option(
                state, pidx, [CARD_DB[e.card_id]["name_ko"] for e in targets],
                "iljeol") if hasattr(agent, "choose_option") else 0
            e = targets[pick]
            state.dust += e.tokens
            e.tokens = 0
        return

    # 삼라판증 전개중: 다른 부여패 파기 시 상대 라이프 1
    if cid == "samra_on_other_destroy":
        return  # 표식 (destroy_enhancement에서 조회)

    # 삼라판증 파기시: 패배
    if cid == "samra_lose":
        state.winner = 1 - pidx
        state.end_reason = "samra"
        return

    # 판정형 표식
    if cid in ("cheonji_swap",):
        return

    # ── 메구미 ──
    # 껍질치기: 이 턴 다음 사용하는 다른 여신 부여에 생육2 (플래그)
    if cid == "ggeopjil_growth2":
        state._ggeopjil_growth[pidx] = True
        return

    # 인과율의 뿌리: 공격후 씨앗 1개 발아
    if cid == "ingwa_sprout":
        p2 = state.players[pidx]
        if p2.soil_unsprouted > 0:
            p2.soil_unsprouted -= 1
            p2.soil_sprouted += 1
        return

    # 장대 찌르기 상시: 이 공격 데미지의 벚꽃결정을 간격으로
    if cid == "jangdae_dmg_to_distance":
        atk = ctx.get("attack")
        if atk is not None:
            atk.dmg_to_distance = True
        return

    # 장대 찌르기 공격후: 부여패 있으면 상대 다음 기본동작 무효
    if cid == "jangdae_block_basic":
        if state.players[pidx].enhancements:
            state._next_basic_no_effect = state._next_basic_no_effect or {}
            state._next_basic_no_effect[1 - pidx] = True
        return

    # 찔레꽃 전개시: 기본동작 1회 무료
    if cid == "jjille_free_basic":
        from tokens import legal_basic_actions, perform_basic_action
        acts = legal_basic_actions(state, pidx)
        if acts and hasattr(agent, "choose_free_basic_action"):
            ch = agent.choose_free_basic_action(state, pidx, acts)
            if ch:
                perform_basic_action(state, pidx, ch)
        return

    # 판정형/표식 (파이프라인 조회): galdae_range_x, bongseonhwa_attacks,
    # jjille_marker, ganeungseong_marker, gyeolmal_check, sonbadak_first_attack
    if cid in ("galdae_range_x", "bongseonhwa_attacks", "jjille_marker",
               "ganeungseong_marker", "gyeolmal_check", "sonbadak_first_attack"):
        return

    # ── 유키히 ──
    # 인연 맺기: 전개시/파기시 화살표 (우산 펼침이면 반대)
    if cid == "inyeon_deploy":
        from tokens import move_tokens
        p2 = state.players[pidx]
        if p2.umbrella_open:
            move_tokens(state, ("dust", None), ("distance", None), 1)
        else:
            move_tokens(state, ("distance", None), ("dust", None), 1)
        return
    if cid == "inyeon_destroy":
        from tokens import move_tokens
        p2 = state.players[pidx]
        if p2.umbrella_open:
            move_tokens(state, ("distance", None), ("dust", None), 1)
        else:
            move_tokens(state, ("dust", None), ("distance", None), 1)
        return
    if cid == "umbrella_reveal":
        return  # 판정형 (토글 훅에서 조회)

    # 판정형 표식 (파이프라인 조회용): kachibal_dist_minus2, jinheuk_no_retreat,
    # bangi_assign, iwan_no_attack, yamikura_guard, mabi_unusable_if_basic
    if cid in ("kachibal_dist_minus2", "jinheuk_no_retreat", "bangi_assign",
               "iwan_no_attack", "yamikura_guard", "mabi_unusable_if_basic"):
        return

    # 크림슨 제로 등 나머지 판정형은 각 파이프라인에서
    pass


def _check_aura_full_saiki(state, who):
    """상대(who) 오라가 꽉 찼을 때 우파스 툼 즉재기 (10-8). who의 상대가 소유자."""
    if not state.players[who].aura_full():
        return
    owner = 1 - who
    p = state.players[owner]
    from setup import CARD_DB
    for cid in list(p.used_specials):
        sk = CARD_DB[cid].get("saiki")
        if sk and sk.get("immediate") and sk["condition"] == "opp_aura_becomes_full":
            p.used_specials.remove(cid)
            p.specials.append(cid)


# ═══════════════════════════════════════════
def _burn_harmony(state, pidx, n):
    """조화결정 연소. 마스터피스 전개중이면 연소됨→머신으로 반전 (9-2패치)."""
    from setup import CARD_DB
    p = state.players[pidx]
    reversed_burn = any(
        any(o.get("id") == "masterpiece_reverse"
            for fx in (CARD_DB[e.card_id].get("effects") or [])
            if fx["trigger"] == "deploy_during" for o in fx["ops"])
        for e in p.enhancements)
    if reversed_burn:
        # 연소됨→머신 (연소됨 부족하면 연소 불가)
        moved = min(n, p.burned_harmony)
        p.burned_harmony -= moved
        p.machine_harmony += moved
    else:
        moved = min(n, p.machine_harmony)
        p.machine_harmony -= moved
        p.burned_harmony += moved


def _perform_kidou(state, pidx, agent, ctx):
    """기동 (9-8). 머신 조화결정을 간격±1 토큰으로."""
    p = state.players[pidx]
    if p.machine_harmony <= 0:
        return
    # 간격 벚꽃결정(간격-1 안 붙은 것) 존재 여부 → 기동전진 가능
    free_gap = state.distance - p.gap_minus_harmony
    gap_total = state.distance + p.gap_plus_harmony
    options = []
    if free_gap > 0:
        options.append("advance")   # 기동전진: 간격 감소
    if gap_total < 10:
        options.append("retreat")   # 기동후퇴: 간격 증가
    if not options:
        return
    choice = options[0]
    if len(options) > 1 and hasattr(agent, "choose_option"):
        idx = agent.choose_option(state, pidx,
                                  ["기동전진(간격-1)", "기동후퇴(간격+1)"], "kidou")
        choice = "advance" if idx == 0 else "retreat"
    p.machine_harmony -= 1
    if choice == "advance":
        p.gap_minus_harmony += 1
    else:
        p.gap_plus_harmony += 1
    ctx["kidou_changed"] = True
    # Alpha-Edge 즉재기 (기동으로 간격 변화 시)
    _check_kidou_saiki(state, pidx)


def _check_kidou_saiki(state, pidx):
    from setup import CARD_DB
    p = state.players[pidx]
    for cid in list(p.used_specials):
        sk = CARD_DB[cid].get("saiki")
        if sk and sk.get("condition") == "kidou_changed" and sk.get("immediate"):
            p.used_specials.remove(cid)
            p.specials.append(cid)


def _perform_transform(state, pidx, agent, rng, ctx):
    """TransForm (9-3): 추가패 변신 카드 1장을 머신으로 + 변형시 효과."""
    from setup import CARD_DB
    p = state.players[pidx]
    avail = [cid for cid in CARD_DB
             if CARD_DB[cid].get("megami") == "thallya"
             and CARD_DB[cid].get("base_type") == "transform"
             and cid not in p.transforms]
    if not avail:
        return
    pick = avail[0]
    if hasattr(agent, "choose_option"):
        idx = agent.choose_option(
            state, pidx, [CARD_DB[c]["name_ko"] for c in avail], "transform")
        pick = avail[idx]
    p.transforms.append(pick)
    state._transform_count[pidx] = state._transform_count.get(pidx, 0) + 1
    # 변형시 효과
    tc = CARD_DB[pick]
    resolve_trigger(state, pidx, tc, "transform_on", agent, rng, ctx)


def _resolve_scheme_effect(state, pidx, ops, agent, rng, ctx):
    """계략(신산/귀모) 한쪽의 ops를 순서대로 실행."""
    for op in ops:
        _resolve_op(state, pidx, op, agent, rng, ctx)
        if state.is_over():
            return


def resolve_trigger(state: GameState, pidx: int, card: dict, trigger: str,
                    agent, rng, ctx: dict) -> None:
    """카드의 effects 중 해당 trigger인 것을 순서대로 (조건 검사 후) 실행."""
    for fx in card.get("effects") or []:
        if fx["trigger"] != trigger:
            continue
        cond = fx.get("condition")
        # ctx 기반 조건 (설치/전력화)
        if cond == "from_covered":
            if not ctx.get("from_covered"):
                continue
        elif cond == "as_fullpower":
            if not ctx.get("as_fullpower"):
                continue
        elif cond == "not_fullpower":
            if ctx.get("as_fullpower"):
                continue
        elif not check_condition(state, pidx, cond):
            continue
        resolve_ops(state, pidx, fx["ops"], agent, rng, ctx)
        if state.is_over():
            return
