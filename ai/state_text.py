"""
게임 상태 → LLM용 자연어 요약.

Gemini 등 LLM 에이전트가 상황을 이해하고 수를 선택할 수 있도록
게임 상태를 한국어 텍스트로 렌더링한다. 불완전정보를 존중해
'나(me)' 관점에서 보이는 정보만 노출한다.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))

from setup import CARD_DB, megamis_of   # noqa: E402
from combat import effective_distance   # noqa: E402
from cards import card_range_str, card_damage_str  # noqa: E402


def _card_line(state, pidx, cid):
    """카드 1장을 '이름 (타입 거리X 데미지Y 소모Z)' 형식으로."""
    c = CARD_DB[cid]
    parts = [c["name_ko"]]
    meta = []
    if c["type"] == "attack":
        rng = card_range_str(state, pidx, c)
        dmg = card_damage_str(state, pidx, c)
        meta.append(f"공격 거리{rng} {dmg}")
    elif c["type"] == "action":
        meta.append("행동")
    elif c["type"] == "enhance":
        meta.append(f"부여 납{c.get('capacity', '-')}")
    if c.get("subtype") == "fullpower":
        meta.append("전력")
    if c.get("subtype") == "reaction":
        meta.append("대응")
    if c.get("base_type") == "special":
        meta.append(f"비장 소모{c.get('cost', '-')}")
    if meta:
        parts.append("(" + ", ".join(meta) + ")")
    return " ".join(parts)


def _player_block(state, pidx, is_me):
    """플레이어 한 명의 상태 블록."""
    p = state.players[pidx]
    who = "나" if is_me else "상대"
    megs = "+".join(megamis_of(p))
    lines = [f"[{who}] 여신: {megs}"]
    lines.append(f"  라이프 {p.life} / 오라 {p.aura} / 플레어 {p.flare} "
                 f"/ 집중력 {p.vigor}")
    # 손패: 나는 전부 보이고, 상대는 장수만
    if is_me:
        if p.hand:
            hand = "; ".join(_card_line(state, pidx, c) for c in p.hand)
            lines.append(f"  손패({len(p.hand)}): {hand}")
        else:
            lines.append("  손패(0): 없음")
    else:
        lines.append(f"  손패: {len(p.hand)}장 (내용 비공개)")
    # 패산/버림패 장수
    lines.append(f"  패산 {len(p.deck)}장 / 버림패 {len(p.discard)}장 "
                 f"/ 덮음패 {len(p.covered)}장")
    # 비장패 (공개 정보)
    if p.specials:
        sp = "; ".join(_card_line(state, pidx, c) for c in p.specials)
        lines.append(f"  비장(미사용): {sp}")
    if p.used_specials:
        us = ", ".join(CARD_DB[c]["name_ko"] for c in p.used_specials)
        lines.append(f"  비장(사용완료): {us}")
    # 부여패
    if p.enhancements:
        en = "; ".join(f"{CARD_DB[e.card_id]['name_ko']}(결정{e.tokens}"
                       + (f",씨앗{e.seeds}" if e.seeds else "") + ")"
                       for e in p.enhancements)
        lines.append(f"  부여패: {en}")
    # 여신별 신규 자원
    res = _resource_line(state, pidx)
    if res:
        lines.append(f"  자원: {res}")
    return "\n".join(lines)


def _resource_line(state, pidx):
    """여신별 신규 자원 요약."""
    p = state.players[pidx]
    megs = megamis_of(p)
    bits = []
    if "raira" in megs:
        bits.append(f"풍신{p.fuujin}/뇌신{p.raijin}")
        if p.taisen_cards:
            bits.append(f"대전카드{len(p.taisen_cards)}")
    if "thallya" in megs:
        bits.append(f"머신조화{p.machine_harmony}/연소{p.burned_harmony}")
    if "megumi" in megs:
        bits.append(f"발아씨앗{p.soil_sprouted}/토양{p.soil_unsprouted}")
    if "korunu" in megs:
        bits.append(f"동결{p.frozen}")
    if "chikage" in megs:
        bits.append(f"독주머니{len(p.poison_pouch)}")
    if "shinra" in megs:
        bits.append(f"계략:{p.scheme or '미준비'}")
        if p.sealed_cards:
            bits.append(f"봉인{len(p.sealed_cards)}")
    if "yukihi" in megs:
        bits.append("우산:" + ("폄" if p.umbrella_open else "접힘"))
    return " / ".join(bits)


def render_state(state, me: int) -> str:
    """me 관점의 전체 상태 요약."""
    lines = []
    lines.append(f"=== 후루요니 대국 (턴 {state.turn_count}) ===")
    lines.append(f"간격: {effective_distance(state)} "
                 f"(기본 {state.distance}) / 더스트: {state.dust}")
    turn_who = "나" if state.active == me else "상대"
    lines.append(f"현재 수번: {turn_who}")
    lines.append("")
    lines.append(_player_block(state, me, True))
    lines.append("")
    lines.append(_player_block(state, 1 - me, False))
    hint = _tactical_hints(state, me)
    if hint:
        lines.append("")
        lines.append(hint)
    return "\n".join(lines)


def _tactical_hints(state, me: int) -> str:
    """LLM 판단을 돕는 전술 힌트 (현 상황의 기회/위협)."""
    from cards import card_range_str, card_damage_str, parse_range
    from combat import parse_damage
    p = state.players[me]
    o = state.players[1 - me]
    d = effective_distance(state)
    bits = []

    # 지금 간격에서 명중하는 내 공격
    hits = []
    for src, pool in (("손패", p.hand), ("비장", p.specials)):
        for cid in pool:
            c = CARD_DB[cid]
            if c["type"] != "attack":
                continue
            if src == "비장":
                cost = c.get("cost")
                cost = cost if isinstance(cost, int) else 0
                if p.flare < cost:
                    continue
            if d in parse_range(card_range_str(state, me, c)):
                dmg = card_damage_str(state, me, c)
                hits.append(f"{c['name_ko']}({dmg})")
    if hits:
        bits.append(f"● 지금 간격({d})에서 명중하는 내 공격: {', '.join(hits)}")
    else:
        bits.append(f"● 지금 간격({d})에서 명중하는 내 공격 없음 "
                    f"(간격을 조정해야 함)")

    # 상대 오라/라이프 압박
    bits.append(f"● 상대 오라 {o.aura} (오라뎀은 여기서 막힘), "
                f"상대 라이프 {o.life}")

    # 이번 턴 처치 가능성 (아주 러프한 추정: 명중 공격들의 라이프뎀 합)
    total_life_dmg = 0
    for cid in p.hand + p.specials:
        c = CARD_DB[cid]
        if c["type"] != "attack":
            continue
        if d in parse_range(card_range_str(state, me, c)):
            try:
                _, l = parse_damage(card_damage_str(state, me, c))
                total_life_dmg += l if isinstance(l, int) else 0
            except Exception:
                pass
    if o.aura == 0 and total_life_dmg >= o.life and o.life > 0:
        bits.append("● 주의: 상대 오라가 0이고 명중 공격의 라이프뎀 합이 "
                    "상대 라이프 이상 → 이번 턴 처치 기회일 수 있음")

    # 내가 위험한지
    if p.life <= 3:
        bits.append(f"● 경고: 내 라이프 {p.life} (위험, 방어 우선 고려)")

    return "전술 힌트:\n  " + "\n  ".join(bits) if bits else ""


def render_legal_actions(state, me: int, legal) -> str:
    """legal 액션 목록을 번호 붙인 선택지로."""
    lines = ["가능한 행동:"]
    for i, (kind, arg) in enumerate(legal):
        lines.append(f"  {i}: {_describe_action(state, me, kind, arg)}")
    return "\n".join(lines)


def _describe_action(state, me, kind, arg):
    """액션 하나를 사람이 읽을 설명으로."""
    if kind == "end":
        return "턴 종료"
    if kind == "basic":
        names = {"advance": "전진(간격-1, 오라1 지불)",
                 "retreat": "후퇴(간격+1, 오라1 지불)",
                 "focus": "집중(오라→플레어, 집중력 소비)",
                 "hold": "숙려(집중력+1, 손패1 덮기)"}
        return f"기본동작: {names.get(arg, arg)}"
    if kind == "card":
        src, cid = arg
        loc = "손패" if src == "hand" else "비장"
        return f"카드 사용({loc}): {_card_line(state, me, cid)}"
    if kind == "release_taisen":
        return f"대전 해제: {CARD_DB[arg]['name_ko']} (게이지+1)"
    return f"{kind} {arg}"
