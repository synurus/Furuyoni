"""
후루요니 CLI 대전 모드 (세션 5: 실전 플레이 검증용)

실행:
  python3 cli/play.py                          # 사람(유리나) vs 봇(토코요)
  python3 cli/play.py yurina saine             # 여신 지정
  python3 cli/play.py yurina saine --hotseat   # 사람 vs 사람
  python3 cli/play.py yurina saine --seed 7    # 시드 지정

사람 차례에는 번호를 입력해 선택한다. 언제든 q 입력 시 종료.
"""

import argparse
import random
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from setup import new_game, CORE4, CARD_DB
from turn import play_turn
from tokens import BASIC_ACTION_KR, master_range
from cards import card_cost, card_capacity
from ai.agents import RandomBot, AggressiveBot


class QuitGame(Exception):
    pass


def kname(cid):
    return CARD_DB[cid]["name_ko"]


# ═══════════════════════════════════════════
# 2여신 대화형 덱 구축 (쌍장요란 → 안전구축 → 무다시)
# ═══════════════════════════════════════════

def _ask_numbers(prompt, n_min, n_max, valid_range, allow_auto=False):
    """공백 구분 번호 입력 (1-기반). n_min~n_max개, 중복 불가.
    allow_auto면 'd' 입력 시 None 반환(자동 선택)."""
    while True:
        raw = input(prompt).strip()
        if allow_auto and raw.lower() == "d":
            return None
        if raw.lower() in ("q", "quit"):
            raise QuitGame()
        try:
            nums = [int(x) for x in raw.split()]
        except ValueError:
            print("  숫자를 공백으로 구분해 입력하세요.")
            continue
        if len(nums) < n_min or len(nums) > n_max:
            print(f"  {n_min}~{n_max}개를 선택하세요.")
            continue
        if len(set(nums)) != len(nums):
            print("  중복 없이 선택하세요.")
            continue
        if any(x < 1 or x > valid_range for x in nums):
            print(f"  1~{valid_range} 범위로 입력하세요.")
            continue
        return [x - 1 for x in nums]


def interactive_megami_pick():
    """쌍장요란: 내 여신 2명 선택."""
    print("\n=== 쌍장요란: 여신 2명을 선택하세요 ===")
    for i, m in enumerate(CORE4):
        print(f"  {i+1:2}: {m}")
    idx = _ask_numbers("여신 2명 (예: 1 9): ", 2, 2, len(CORE4))
    return (CORE4[idx[0]], CORE4[idx[1]])


def interactive_deck_build(meg_a, meg_b):
    """안전구축: 통상 7 / 비장 3 직접 선택. 'd'면 스마트 자동."""
    from setup import cards_of
    na, sa = cards_of(meg_a)
    nb, sb = cards_of(meg_b)
    normals = list(na) + list(nb)
    specials = list(sa) + list(sb)

    print(f"\n=== 안전구축: {meg_a}+{meg_b} ===")
    print(f"--- 통상패 풀 {len(normals)}장 → 7장 선택 "
          f"('d' 입력 시 자동 추천) ---")
    for i, cid in enumerate(normals):
        print(f"  {i+1:2}: {card_line(cid)}")
    idx = _ask_numbers("통상 7장 (예: 1 2 4 6 8 10 12, d=자동): ",
                       7, 7, len(normals), allow_auto=True)
    if idx is None:
        from ai.deckbuild import pick_deck
        n_pick, s_pick = pick_deck(meg_a, meg_b)
        print("  → 자동 추천 덱 사용:")
        for cid in n_pick:
            print(f"    · {kname(cid)}")
        for cid in s_pick:
            print(f"    · [비장] {kname(cid)}")
        return n_pick, s_pick
    n_pick = [normals[i] for i in idx]

    print(f"\n--- 비장패 풀 {len(specials)}장 → 3장 선택 ---")
    for i, cid in enumerate(specials):
        print(f"  {i+1:2}: {card_line(cid)}")
    idx = _ask_numbers("비장 3장 (예: 1 3 5): ", 3, 3, len(specials))
    s_pick = [specials[i] for i in idx]
    return n_pick, s_pick


def interactive_mulligan(state, pidx):
    """무다시: 첫 손패 일부를 패산 밑에 넣고 같은 수 뽑기."""
    p = state.players[pidx]
    if not p.hand:
        return
    print("\n=== 무다시 (첫 손패 교환) ===")
    for i, cid in enumerate(p.hand):
        print(f"  {i+1}: {card_line(cid)}")
    raw = input("교환할 카드 번호 (공백 구분, 없으면 엔터): ").strip()
    if not raw:
        return
    try:
        nums = sorted({int(x) - 1 for x in raw.split()}, reverse=True)
    except ValueError:
        print("  잘못된 입력 — 교환 생략")
        return
    n = 0
    for i in nums:
        if 0 <= i < len(p.hand):
            cid = p.hand.pop(i)
            p.deck.insert(0, cid)   # 패산 밑
            n += 1
    for _ in range(n):
        if p.deck:
            p.hand.append(p.deck.pop())
    print(f"  → {n}장 교환 완료. 새 손패: "
          + ", ".join(kname(c) for c in p.hand))
    state.check_conservation()


def card_line(cid):
    c = CARD_DB[cid]
    typ = {"attack": "공격", "action": "행동", "enhance": "부여"}[c["type"]]
    sub = {"fullpower": "/전력", "reaction": "/대응"}.get(c.get("subtype"), "")
    bits = [f"{typ}{sub}"]
    if c.get("range"):
        bits.append(f"거리{c['range']}")
    if c.get("damage"):
        bits.append(c["damage"])
    cost = card_cost(cid)
    if cost:
        bits.append(f"소모{cost}")
    cap = card_capacity(cid)
    if cap:
        bits.append(f"납{cap}")
    text = (c.get("text_ko") or "").replace("\n", " ")
    if text:
        bits.append(f"“{text[:60]}{'…' if len(text) > 60 else ''}”")
    return f"{kname(cid)} ({', '.join(bits)})"


def render(state, viewer):
    """viewer 시점의 보드 출력 (상대 손패는 장수만)."""
    from ai.state_text import _resource_line
    from combat import effective_distance
    L = []
    L.append("─" * 62)
    eff = effective_distance(state)
    dist_str = f"간격 {state.distance}"
    if eff != state.distance:
        dist_str += f" (유효 {eff})"
    L.append(f"턴 {state.turn_count} | 활성 P{state.active}"
             f" | {dist_str} | 더스트 {state.dust}")
    for i, p in enumerate(state.players):
        mark = "▶" if i == state.active else " "
        you = " (나)" if i == viewer else ""
        enh = ", ".join(
            f"{kname(e.card_id)}[{e.tokens}"
            + (f"+씨{e.seeds}" if e.seeds else "") + "]"
            for e in p.enhancements) or "없음"
        L.append(f"{mark}P{i} {p.name}{you}: 라이프{p.life} 오라{p.aura} "
                 f"플레어{p.flare} 집중{p.vigor}"
                 f"{'(위축)' if p.withered else ''} 달인간격{master_range(state, i)}")
        res = _resource_line(state, i)
        if res:
            L.append(f"   자원: {res}")
        L.append(f"   패산{len(p.deck)} 버림{len(p.discard)} 덮음{len(p.covered)} "
                 f"| 부여: {enh}")
        L.append(f"   비장(미사용): "
                 + (", ".join(kname(c) for c in p.specials) or "없음"))
        if i == viewer:
            L.append(f"   손패: "
                     + (", ".join(kname(c) for c in p.hand) or "없음"))
        else:
            L.append(f"   손패: {len(p.hand)}장")
    L.append("─" * 62)
    return "\n".join(L)


def ask(prompt, options, advisor=None):
    """번호 선택 입력. options = [(label, value)]. q → 종료, r → AI 훈수(가능 시)."""
    for i, (label, _) in enumerate(options):
        print(f"  {i}) {label}")
    hint = " (q=종료" + (", r=AI 추천" if advisor else "") + ")"
    while True:
        try:
            raw = input(f"{prompt}{hint} > ").strip()
        except EOFError:
            raise QuitGame()
        if raw.lower() == "q":
            raise QuitGame()
        if raw.lower() == "r" and advisor:
            advisor()
            continue
        if raw.isdigit() and int(raw) < len(options):
            return options[int(raw)][1]
        print("  잘못된 입력입니다. 번호를 입력하세요.")


class HumanAgent:
    """모든 엔진 선택 지점을 입력 프롬프트로 연결."""

    def choose_main_action(self, state, pidx, legal, rng):
        print(render(state, viewer=pidx))
        opts = []
        for kind, arg in legal:
            if kind == "basic":
                opts.append((f"기본동작: {BASIC_ACTION_KR[arg]} (비용: 집중1 또는 손패1 덮기)",
                             (kind, arg)))
            elif kind == "card":
                src, cid = arg
                where = "손패" if src == "hand" else "비장패"
                opts.append((f"카드 사용 [{where}]: {card_line(cid)}", (kind, arg)))
            else:
                opts.append(("메인 페이즈 종료", (kind, arg)))

        def advisor():
            from ai.mcts import recommend
            print("  🤖 AI 분석 중...")
            recs = recommend(state, pidx, n_top=3, legal=legal)
            idx_of = {id(m): i for i, (_, m) in enumerate(opts)}
            # move 동등 비교로 옵션 번호 매핑
            for move, desc, wr in recs:
                num = next((i for i, (_, v) in enumerate(opts) if v == move), "?")
                print(f"    승률 {wr*100:5.1f}% → {num}) {desc}")
        return ask("행동 선택", opts, advisor=advisor)

    def choose_action_mode(self, state, pidx):
        return ask("행동 방식", [("표준행동", "standard"),
                                  ("전력행동 (카드 1장 사용 후 종료)", "fullpower")])

    def choose_basic_cost(self, state, pidx):
        p = state.players[pidx]
        opts = []
        if p.vigor >= 1:
            opts.append((f"집중력 1 지불 (현재 {p.vigor})", "vigor"))
        if p.hand:
            opts.append(("손패 1장 덮기", "cover"))
        return ask("기본동작 비용", opts) if len(opts) > 1 else opts[0][1]

    def choose_card_to_cover(self, state, pidx, hand):
        return ask("덮을 카드", [(card_line(c), i) for i, c in enumerate(hand)])

    def choose_damage_type(self, state, target_idx, aura_dmg, life_dmg):
        print(f"  ※ P{target_idx}가 데미지 {aura_dmg}/{life_dmg}를 받습니다.")
        return ask("데미지 선택", [(f"오라로 받기 ({aura_dmg})", "aura"),
                                    (f"라이프로 받기 ({life_dmg})", "life")])

    def choose_reaction(self, state, pidx, options, atk):
        src_name = kname(atk.source_card) if atk.source_card else "효과 생성 공격"
        dmg = f"{atk.aura if atk.aura is not None else '-'}/" \
              f"{atk.life if atk.life is not None else '-'}"
        print(render(state, viewer=pidx))
        print(f"  ※ 상대의 공격: {src_name} ({dmg}) — 대응하시겠습니까?")
        opts = [("대응하지 않음", None)]
        for src, cid in options:
            where = "손패" if src == "hand" else "비장패"
            opts.append((f"대응 [{where}]: {card_line(cid)}", (src, cid)))
        return ask("대응 선택", opts)

    def choose_option(self, state, pidx, labels, tag):
        return ask(f"효과 선택 ({tag})", [(l, i) for i, l in enumerate(labels)])

    def choose_free_basic_action(self, state, pidx, options):
        opts = [("그만두기", None)] + \
               [(BASIC_ACTION_KR[a], a) for a in options]
        return ask("무료 기본동작", opts)

    def choose_yes_no(self, state, pidx, tag):
        return ask(f"선택 ({tag})", [("예", True), ("아니오", False)])

    def choose_dedication(self, state, pidx, need):
        p = state.players[pidx]
        print(f"  ※ 봉납 {need}개: 더스트({state.dust})와 오라({p.aura})에서 가져옵니다.")
        max_dust = min(need, state.dust)
        opts = [(f"더스트에서 {d}개 + 오라에서 {min(need - d, p.aura)}개", d)
                for d in range(max_dust, -1, -1)]
        return ask("봉납 배분", opts)

    def decide_reconstruct(self, state, pidx):
        p = state.players[pidx]
        print(render(state, viewer=pidx))
        print(f"  ※ 개시 페이즈: 재구성하시겠습니까? (라이프 1 데미지, "
              f"버림{len(p.discard)}+덮음{len(p.covered)}장이 패산으로)")
        return ask("재구성", [("예 (라이프 -1)", True), ("아니오", False)])


class Router:
    """플레이어별로 다른 에이전트에 위임. 모든 훅의 2번째 인자가 pidx."""

    def __init__(self, agents):
        self.agents = agents

    def __getattr__(self, name):
        def dispatch(state, pidx, *args, **kw):
            agent = self.agents[pidx]
            fn = getattr(agent, name, None)
            if fn is None:
                # 해당 에이전트에 없는 훅 → 기본 봇 정책으로
                fn = getattr(RandomBot(seed=0), name)
            return fn(state, pidx, *args, **kw)
        return dispatch


def _make_opponent(name, seed):
    """상대 봇 생성."""
    if name == "heuristic":
        from ai.heuristic import HeuristicBot
        return HeuristicBot(seed=seed)
    if name == "mc24":
        from ai.mcts import MonteCarloBot
        return MonteCarloBot(seed=seed, rollouts=24, prune_top=5)
    if name == "mc48":
        from ai.mcts import MonteCarloBot
        return MonteCarloBot(seed=seed, rollouts=48, prune_top=5)
    return AggressiveBot(end_prob=0.25, seed=seed)


def main():
    ap = argparse.ArgumentParser(
        description="후루요니 CLI 플레이 (r=훈수, q=종료)")
    ap.add_argument("megami0", nargs="?", default="yurina", choices=CORE4)
    ap.add_argument("megami1", nargs="?", default="tokoyo", choices=CORE4)
    ap.add_argument("--two", action="store_true",
                    help="2여신 덱 모드 (대화형 덱 구축)")
    ap.add_argument("--bot", default="aggressive",
                    choices=["aggressive", "heuristic", "mc24", "mc48"],
                    help="상대 봇 (기본 aggressive; mc48이 최강이나 느림)")
    ap.add_argument("--hotseat", action="store_true", help="사람 vs 사람")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--first", type=int, default=None, choices=[0, 1],
                    help="선공 지정 (기본: 무작위)")
    args = ap.parse_args()

    seed = args.seed if args.seed is not None else random.randrange(10**6)
    rng = random.Random(seed)

    if args.two:
        # ── 2여신 모드: 쌍장요란 → 안전구축 → 게임 생성 → 무다시 ──
        from setup import new_game_2v2
        my_megamis = interactive_megami_pick()
        my_picks = interactive_deck_build(*my_megamis)
        # 봇 여신: 무작위 2명 (내 여신과 무관하게)
        bot_rng = random.Random(seed + 777)
        bot_megamis = tuple(bot_rng.sample(CORE4, 2))
        print(f"\n상대(봇) 여신: {bot_megamis[0]}+{bot_megamis[1]} "
              f"(덱은 자동 구축)")
        state = new_game_2v2(my_megamis, bot_megamis, seed=seed,
                             first=args.first, picks0=my_picks, picks1=None)
        label0 = "+".join(my_megamis)
        label1 = "+".join(bot_megamis)
    else:
        state = new_game(args.megami0, args.megami1, seed=seed,
                         first=args.first)
        label0, label1 = args.megami0, args.megami1

    human = HumanAgent()
    if args.hotseat:
        agents = [human, human]
        print(f"사람 vs 사람 | P0={label0}, P1={label1} | seed={seed}")
    else:
        bot = _make_opponent(args.bot, seed)
        agents = [human, bot]
        print(f"사람(P0={label0}) vs {args.bot}봇(P1={label1}) | seed={seed}")
    router = Router(agents)

    print(f"선공: P{state.active}")

    if args.two:
        # 무다시: 사람 플레이어만 대화형 (봇은 생략)
        interactive_mulligan(state, 0)
        if args.hotseat:
            print("\n(P1 차례)")
            interactive_mulligan(state, 1)
    try:
        for _ in range(300):
            turn_owner = state.active
            snap = (state.players[0].life, state.players[0].aura,
                    state.players[1].life, state.players[1].aura,
                    state.distance)
            play_turn(state, rng, router)
            # 상대(봇) 턴이 끝났으면 변화 요약 출력
            if not args.hotseat and turn_owner == 1:
                now = (state.players[0].life, state.players[0].aura,
                       state.players[1].life, state.players[1].aura,
                       state.distance)
                if snap != now:
                    print(f"  ▷ 봇 턴 결과: 간격 {snap[4]}→{now[4]} | "
                          f"내 라이프 {snap[0]}→{now[0]} 오라 {snap[1]}→{now[1]} | "
                          f"봇 라이프 {snap[2]}→{now[2]} 오라 {snap[3]}→{now[3]}")
                else:
                    print("  ▷ 봇 턴: 수치 변화 없음")
            if state.is_over():
                break
        print(render(state, viewer=0))
        if state.winner == -1:
            print("=== 무승부 ===")
        else:
            print(f"=== P{state.winner} ({state.players[state.winner].name}) 승리! "
                  f"({state.end_reason}) ===")
    except QuitGame:
        print("\n게임을 종료합니다.")


if __name__ == "__main__":
    main()
