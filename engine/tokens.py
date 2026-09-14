"""
벚꽃결정 이동 기본 연산 + 기본동작 5종 (룰북 9-6)

모든 토큰 이동은 여기의 move_tokens를 거치도록 해서,
이동 후 보존식 검사를 한 곳에서 강제한다.

기본동작 (룰북 9-6):
  전진(advance): 간격 → 자기 오라   [조건: 현재간격 > 달인의간격]
  후퇴(retreat): 자기 오라 → 간격
  휘감기(gather): 더스트 → 자기 오라
  품기(hold):    자기 오라 → 자기 플레어
  이탈(detach):  더스트 → 간격       [조건: 현재간격 > 달인의간격]
"""

from state import GameState, PlayerState
from constants import AURA_MAX


# ─── 토큰 영역 접근자 ───
# 영역 이름 → (getter, setter). 플레이어 영역은 player 객체에 대해 동작.
def _get_zone(state: GameState, zone: str, pidx: int) -> int:
    if zone == "distance":
        return state.distance
    if zone == "dust":
        return state.dust
    p = state.players[pidx]
    return getattr(p, zone)


def _set_zone(state: GameState, zone: str, pidx: int, value: int) -> None:
    if zone == "distance":
        state.distance = value
    elif zone == "dust":
        state.dust = value
    else:
        setattr(state.players[pidx], zone, value)


def move_tokens(state: GameState, frm: tuple, to: tuple, n: int,
                verify: bool = True) -> int:
    """
    벚꽃결정 n개를 frm 영역에서 to 영역으로 이동.
    frm, to = (zone_name, player_index). 공용 영역(distance/dust)은 pidx 무시.

    실제 이동 가능한 만큼만 옮긴다 (룰북 5-3: 감소량이 부족하면 가능한 만큼).
    반환값: 실제로 이동한 개수.
    """
    frm_zone, frm_p = frm
    to_zone, to_p = to

    available = _get_zone(state, frm_zone, frm_p)
    moved = min(n, available)
    # 오라 상한 5 (5-1-1): 결정+동결 합이 상한. 빈 자리만큼만
    if to_zone == "aura":
        occupied = state.players[to_p].aura + state.players[to_p].frozen
        room = AURA_MAX - occupied
        moved = min(moved, max(0, room))
    if moved <= 0:
        return 0

    _set_zone(state, frm_zone, frm_p, available - moved)
    _set_zone(state, to_zone, to_p, _get_zone(state, to_zone, to_p) + moved)

    if verify:
        state.check_conservation()
    return moved


# ─── 기본동작 5종 ───
# 각 함수는 (state, pidx) 를 받아 수행하고, 실제 수행 여부(bool)를 반환.
# pidx는 동작 주체 플레이어.

def master_range(state: GameState, pidx: int) -> int:
    """달인의 간격 (5-2-2 기본 2). 권역 전개중이면 +1."""
    base = state.players[pidx].master_range_bonus
    from setup import CARD_DB
    for enh in state.players[pidx].enhancements:
        card = CARD_DB[enh.card_id]
        for fx in card.get("effects") or []:
            if fx["trigger"] != "deploy_during":
                continue
            for op in fx["ops"]:
                if op["op"] == "custom" and op["id"] == "kwonyeok_master_range":
                    base += 1
                if op["op"] == "custom" and op["id"] == "galdae_range_x":
                    base += enh.seeds   # 갈대: 씨앗 수만큼 달인 간격 증가
    return base


def can_advance(state: GameState, pidx: int) -> bool:
    """전진 가능? 현재 간격이 달인의 간격보다 커야 함."""
    return state.distance > master_range(state, pidx)


def advance(state: GameState, pidx: int) -> bool:
    """전진: 간격 → 자기 오라 (현재간격 > 달인의간격일 때만)."""
    if not can_advance(state, pidx):
        return False
    return move_tokens(state, ("distance", pidx), ("aura", pidx), 1) > 0


def retreat(state: GameState, pidx: int) -> bool:
    """후퇴: 자기 오라 → 간격."""
    return move_tokens(state, ("aura", pidx), ("distance", pidx), 1) > 0


def gather(state: GameState, pidx: int) -> bool:
    """휘감기: 더스트 → 자기 오라."""
    return move_tokens(state, ("dust", pidx), ("aura", pidx), 1) > 0


def hold(state: GameState, pidx: int) -> bool:
    """
    품기 (9-6-4). 동결된 플레이어는 해동으로 변경 (13-6):
    동결 토큰 1개를 게임 바깥으로 (결정 이동 없이도 선택 가능).
    동결이 없으면 통상 품기: 자기 오라 → 자기 플레어.
    """
    p = state.players[pidx]
    if p.frozen > 0:
        p.frozen -= 1   # 게임 바깥으로 (무한 풀)
        return True
    return move_tokens(state, ("aura", pidx), ("flare", pidx), 1) > 0


def can_detach(state: GameState, pidx: int) -> bool:
    """
    이탈 가능? 룰북 9-6-5: 현재 간격이 달인의 간격보다 '크면' 불가.
    즉 현재 간격 <= 달인의 간격 일 때만 가능.
    """
    return state.distance <= master_range(state, pidx)


def detach(state: GameState, pidx: int) -> bool:
    """이탈: 더스트 → 간격 (현재간격 <= 달인의간격일 때만)."""
    if not can_detach(state, pidx):
        return False
    return move_tokens(state, ("dust", pidx), ("distance", pidx), 1) > 0


# 기본동작 레지스트리: 이름 → (실행함수, 가능여부함수 or None)
BASIC_ACTIONS = {
    "advance": (advance, can_advance),   # 전진
    "retreat": (retreat, None),          # 후퇴
    "gather":  (gather,  None),          # 휘감기
    "hold":    (hold,    None),          # 품기
    "detach":  (detach,  can_detach),    # 이탈
}

BASIC_ACTION_KR = {
    "advance": "전진", "retreat": "후퇴", "gather": "휘감기",
    "hold": "품기", "detach": "이탈",
}


def legal_basic_actions(state: GameState, pidx: int) -> list:
    """
    현재 상태에서 pidx 플레이어가 실제로 '무언가를 이동시킬 수 있는'
    기본동작 목록 (룰북 9-6 서두: 어떤 객체를 이동시킬 수 있는 것을 선택).
    """
    result = []
    p = state.players[pidx]
    # 전진: 간격에 결정 있고 & 현재간격 > 달인간격 & 오라에 빈칸
    #       (둔술: 이 턴 전진 금지)
    if state.distance > 0 and can_advance(state, pidx) and not p.aura_full() \
            and not state._no_advance_this_turn.get(pidx):
        result.append("advance")
    # 후퇴: 오라에 결정 있음 (진흙탕: 상대가 전개중이면 불가)
    if p.aura > 0 and not _opp_blocks_action(state, pidx, "jinheuk_no_retreat"):
        result.append("retreat")
    # 휘감기: 더스트에 결정 있음 & 오라에 빈칸
    if state.dust > 0 and not p.aura_full():
        result.append("gather")
    # 품기: 동결 시 해동(결정 불필요), 아니면 오라 결정 필요 (13-6)
    # 단, 상대가 동상을 전개중이면 품기 불가
    if (p.frozen > 0 or p.aura > 0) and not _opp_blocks_hold(state, pidx):
        result.append("hold")
    # 이탈: 더스트에 결정 있고 & 현재간격 > 달인간격 (진흙탕: 불가)
    if state.dust > 0 and can_detach(state, pidx) \
            and not _opp_blocks_action(state, pidx, "jinheuk_no_retreat"):
        result.append("detach")
    return result


def perform_basic_action(state: GameState, pidx: int, action: str) -> bool:
    """이름으로 기본동작 실행."""
    state.basic_actions_this_turn[pidx] += 1  # 마비독 판정용
    fn, _ = BASIC_ACTIONS[action]
    return fn(state, pidx)


def _opp_blocks_action(state, pidx, custom_id) -> bool:
    """상대의 전개중 부여가 특정 기본동작을 막는가 (진흙탕 등)."""
    from setup import CARD_DB
    for enh in state.players[1 - pidx].enhancements:
        for fx in CARD_DB[enh.card_id].get("effects") or []:
            if fx["trigger"] == "deploy_during":
                for op in fx["ops"]:
                    if op["op"] == "custom" and op["id"] == custom_id:
                        return True
    return False


def _opp_blocks_hold(state, pidx) -> bool:
    """상대의 동상(deploy_during dongsang_no_hold)이 이 플레이어의 품기를 막는가."""
    from setup import CARD_DB
    for enh in state.players[1 - pidx].enhancements:
        for fx in CARD_DB[enh.card_id].get("effects") or []:
            if fx["trigger"] == "deploy_during":
                for op in fx["ops"]:
                    if op["op"] == "custom" and op["id"] == "dongsang_no_hold":
                        return True
    return False
