"""
Phase 2: FAQ 기반 룰 정확성 테스트

각 테스트는 faq_core4.md의 Q&A 하나에 대응한다.
docstring에 원 FAQ를 요약 인용해 추적 가능하게 유지.

실행: 프로젝트 루트에서  python3 -m pytest tests/test_faq.py -v
"""

import random
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from setup import new_game
from state import Enhancement
from cards import use_card, legal_card_uses, legal_reactions
from combat import AttackInstance, effective_aura
from tokens import legal_basic_actions
from turn import play_turn, end_phase
from deck import draw_card
from ai.agents import RandomBot


# ─── 헬퍼 ───
def fresh(m0="yurina", m1="tokoyo", dist=3, seed=1):
    """간격을 조정한 새 게임 (조정분은 더스트로 보정해 보존 유지)."""
    s = new_game(m0, m1, seed=seed, first=0)
    s.dust += s.distance - dist
    s.distance = dist
    s.check_conservation()
    return s


def give_aura(s, pidx, n):
    """오라를 n으로 설정 (차이는 더스트와 정산)."""
    diff = s.players[pidx].aura - n
    s.players[pidx].aura = n
    s.dust += diff
    s.check_conservation()


def give_flare(s, pidx, n):
    diff = s.players[pidx].flare - n
    s.players[pidx].flare = n
    s.dust += diff
    s.check_conservation()


def rebalance(s):
    """다른 존을 자유롭게 설정한 뒤, 더스트를 보존식에 맞게 재계산."""
    from constants import TOTAL_TOKENS
    others = s.distance + sum(p.token_total() for p in s.players)
    s.dust = TOTAL_TOKENS - others
    assert s.dust >= 0, f"셋업 오류: 결정 과다 배치 (더스트 {s.dust})"
    s.check_conservation()


def give_life(s, pidx, n):
    diff = s.players[pidx].life - n
    s.players[pidx].life = n
    s.dust += diff
    s.check_conservation()


class ScriptBot(RandomBot):
    """검증용 봇: 대응/선택을 스크립트로 제어."""
    def __init__(self, react_with=None, damage="auto", option=0, count=None, **kw):
        super().__init__(**kw)
        self.react_with = react_with
        self.damage = damage
        self.option = option
        self.count = count

    def choose_count(self, state, pidx, max_n, tag):
        return max_n if self.count is None else min(self.count, max_n)

    def choose_reaction(self, state, pidx, options, atk):
        if self.react_with is None:
            return None
        for o in options:
            if o[1] == self.react_with:
                return o
        return None

    def choose_damage_type(self, state, target_idx, aura_dmg, life_dmg):
        if self.damage == "auto":
            return super().choose_damage_type(state, target_idx, aura_dmg, life_dmg)
        return self.damage

    def choose_option(self, state, pidx, labels, tag):
        if isinstance(self.option, str):
            for i, l in enumerate(labels):
                if l == self.option:
                    return i
            return 0
        return self.option


RNG = random.Random(1)


# ═══════════════════════════════════════════
# 규칙 전반
# ═══════════════════════════════════════════
class TestGeneralRules:

    def test_aura_max_5_blocks_advance(self):
        """FAQ: 오라에 결정 5개면 전진할 수 없다."""
        s = fresh(dist=5)
        give_aura(s, 0, 5)
        assert "advance" not in legal_basic_actions(s, 0)
        assert "gather" not in legal_basic_actions(s, 0)  # 휘감기도 오라행

    def test_no_empty_basic_action(self):
        """FAQ: 결정이 움직이지 않는 기본동작은 선택 불가 (오라 0이면 품기 불가)."""
        s = fresh(dist=5)
        give_aura(s, 0, 0)
        acts = legal_basic_actions(s, 0)
        assert "hold" not in acts and "retreat" not in acts

    def test_used_special_not_discard(self):
        """FAQ: 사용한 비장패는 버림패가 되지 않는다."""
        s = fresh(m0="himika", dist=6)
        s.players[0].specials = ["03-himika-o-s-1"]  # 레드불릿 5-10, 소모0
        bot = ScriptBot(seed=1)
        use_card(s, 0, "special", "03-himika-o-s-1", bot, RNG)
        assert "03-himika-o-s-1" in s.players[0].used_specials
        assert "03-himika-o-s-1" not in s.players[0].discard

    def test_attack_out_of_range_unusable_action_usable(self):
        """FAQ: 간격 안 맞는 공격은 사용 불가, 행동은 효과 미해결이어도 사용 가능."""
        s = fresh(m0="yurina", dist=10)
        s.players[0].hand = ["01-yurina-o-n-1", "01-yurina-o-n-5"]  # 참(3-4), 몸놀림(행동)
        legal = [c for _, c in legal_card_uses(s, 0, is_fullpower=False)]
        assert "01-yurina-o-n-1" not in legal   # 공격: 거리 밖 → 불가
        assert "01-yurina-o-n-5" in legal        # 행동: 가능

    def test_reaction_card_usable_normally_in_main(self):
        """FAQ: 《대응》 카드는 메인 페이즈에 그냥 사용 가능."""
        s = fresh(m0="tokoyo", dist=3)
        s.players[0].hand = ["04-tokoyo-o-n-2"]  # 우아한 타격 (공격/대응 2-4)
        legal = [c for _, c in legal_card_uses(s, 0, is_fullpower=False)]
        assert "04-tokoyo-o-n-2" in legal

    def test_enhance_partial_dedication(self):
        """FAQ: 오라+더스트 합계가 봉납 미만이면 가능한 만큼만 놓는다."""
        s = fresh(m0="saine", dist=3)
        s.players[0].aura = 1
        s.players[0].flare = 7    # 36 - 간격3 - 더스트2 - P0(1+10) - P1(13)
        rebalance(s)
        assert s.dust == 2
        s.players[0].hand = ["02-saine-o-n-7"]  # 무음벽 납5
        bot = ScriptBot(seed=1)
        use_card(s, 0, "hand", "02-saine-o-n-7", bot, RNG)
        enh = s.players[0].enhancements[0]
        assert enh.tokens == 3  # 더스트2 + 오라1

    def test_enhance_zero_dedication_immediate_destroy(self):
        """FAQ: 결정이 하나도 없으면 전개시 해결 후 즉시 파기 (파기시 해결)."""
        s = fresh(m0="yurina", m1="tokoyo", dist=2)
        s.players[0].aura = 0
        s.players[1].aura = 3
        s.players[0].flare = 36 - 2 - 0 - 10 - 3 - 10 - s.players[1].flare
        rebalance(s)
        assert s.dust == 0
        s.players[0].hand = ["01-yurina-o-n-6"]  # 압도 납2, 파기시 1-3 3/- 공격
        bot = ScriptBot(seed=1)
        use_card(s, 0, "hand", "01-yurina-o-n-6", bot, RNG)
        # 즉시 파기 → 파기시 공격 3/- (간격2, 적정 1-3) → 상대 오라 3→0
        assert s.players[0].enhancements == []
        assert "01-yurina-o-n-6" in s.players[0].discard
        assert s.players[1].aura == 0

    def test_special_enhance_cost_recyclable_into_dedication(self):
        """FAQ: 봉납은 소모값 지불(플레어→더스트) '이후' 더스트에서 가져온다.
        core4에 비장 부여가 없어, 순서 자체를 직접 검증:
        더스트 0에서 pay_cost 후 봉납이 그 결정을 회수하는지."""
        from cards import pay_cost
        s = fresh(m0="saine", dist=3)
        s.players[0].flare = 7   # 36 - 간격3 - 더스트0 - P0(3+10) - P1(13)
        rebalance(s)
        assert s.dust == 0
        pay_cost(s, 0, 3)                      # 플레어 3 → 더스트 3
        assert s.dust == 3
        s.players[0].hand = ["02-saine-o-n-7"]  # 무음벽 납5
        bot = ScriptBot(seed=1)
        use_card(s, 0, "hand", "02-saine-o-n-7", bot, RNG)
        # 봉납: 더스트3 + 오라(3에서 2개) = 5
        assert s.players[0].enhancements[0].tokens == 5
        assert s.dust == 0

    def test_impatience_on_empty_deck_draw(self):
        """FAQ: 패산 0장일 때 뽑으면 초조 — 오라1/라이프1 데미지 선택, 1장씩 따로."""
        s = fresh(m0="yurina", dist=5)
        p = s.players[0]
        s.dust += len(p.deck) * 0  # 자리표시
        p.discard.extend(p.deck)
        p.deck = []
        give_aura(s, 0, 2)
        life0 = p.life
        bot = ScriptBot(damage="aura", seed=1)
        draw_card(s, 0, rng=RNG, agent=bot)   # 초조 → 오라 선택
        assert p.aura == 1 and p.life == life0
        bot2 = ScriptBot(damage="life", seed=1)
        draw_card(s, 0, rng=RNG, agent=bot2)  # 초조 → 라이프 선택
        assert p.life == life0 - 1
        assert len(p.hand) == 3  # 초기 3장 그대로, 뽑힌 카드 없음

    def test_no_reaction_to_reaction(self):
        """FAQ: 대응으로 사용한 공격에 다시 대응 불가."""
        s = fresh(m0="yurina", m1="tokoyo", dist=3)
        s.players[0].hand = ["01-yurina-o-n-1"]       # 참
        s.players[1].hand = ["04-tokoyo-o-n-2"]       # 우아한 타격 (대응)
        s.players[1].vigor = 0                          # 경지 아님 → 무효화 없음
        # P0에도 대응 카드를 쥐여주고, 우아한 타격의 공격에 대응이 열리는지 관찰
        s.players[0].specials = ["01-yurina-o-s-2"]   # 해안 (대응)
        give_flare(s, 0, 3)
        reacted = []
        class SpyBot(ScriptBot):
            def choose_reaction(self, state, pidx, options, atk):
                reacted.append((pidx, [c for _, c in options]))
                return super().choose_reaction(state, pidx, options, atk)
        bot = SpyBot(react_with="04-tokoyo-o-n-2", seed=1)
        use_card(s, 0, "hand", "01-yurina-o-n-1", bot, RNG)
        # 대응 창은 P1(참에 대해)에만 열리고, 우아한 타격의 공격에는 열리지 않아야
        assert all(p == 1 for p, _ in reacted), f"대응에 대응이 열림: {reacted}"


# ═══════════════════════════════════════════
# 유리나
# ═══════════════════════════════════════════
class TestYurina:

    def test_issen_kessa_checked_at_damage_time(self):
        """일섬 FAQ: 라이프 4로 사용 → 대응 데미지로 라이프 3 → +1/+0 적용."""
        s = fresh(m0="yurina", m1="tokoyo", dist=3)
        give_life(s, 0, 4)
        s.players[0].hand = ["01-yurina-o-n-2"]       # 일섬 3-4 2/2
        s.players[1].specials = ["04-tokoyo-o-s-1"]   # 영원한 꽃? (무효화라 부적합)
        # 대응으로 라이프를 깎을 카드: 토코요엔 없음 → 사이네 음무쇄빙(-1/-1은 디버프)
        # 유리나 미러로: 해안은 2/- (오라만). 라이프를 깎는 대응은 종극(5/5) 뿐.
        # 간단히: 사이네 상대, 대응 종극... 종극은 비장패 대응 전용 → 일섬은 통상.
        # 대안: 무게추(2-3 2/1 대응) — 간격 3, P1 라이프 데미지 선택으로 P0 라이프 4→3
        s = fresh(m0="yurina", m1="saine", dist=3)
        give_life(s, 0, 4)
        give_aura(s, 0, 0)   # 오라 0 → 무게추 2/1에서 라이프 강제
        s.players[0].hand = ["01-yurina-o-n-2"]
        s.players[1].hand = ["02-saine-o-n-3"]        # 무게추
        give_aura(s, 1, 5)   # 일섬 데미지를 오라로 받게
        bot = ScriptBot(react_with="02-saine-o-n-3", seed=1)
        use_card(s, 0, "hand", "01-yurina-o-n-2", bot, RNG)
        # 무게추 2/1: P0 오라0 → 라이프 강제 → 라이프 4→3 (결사 진입)
        assert s.players[0].life == 3
        # 일섬 해결: 결사 적용 → 3/2. P1 오라 5 → 오라 선택 → 5-3=2
        assert s.players[1].aura == 2, f"결사 미적용: P1 오라 {s.players[1].aura}"

    def test_appdo_zero_dedication_gets_destroy_effect(self):
        """압도 FAQ: 결정 없이 전개해도 파기시 효과 발동. (위 일반 테스트로 커버)"""
        pass  # TestGeneralRules.test_enhance_zero_dedication_immediate_destroy

    def test_shore_wave_zero_damage_still_attack_after(self):
        """해안 FAQ: 대응으로 오라뎀이 0이 되어도 공격 성공 → 공격후 해결됨."""
        s = fresh(m0="yurina", m1="yurina", dist=3)
        s.players[0].hand = ["01-yurina-o-n-3"]       # 자루치기 2 거리... 간격 확인
        # 자루치기는 거리 2. 대신 참(3/1)+해안(-2/0) → 1/1, 0이 아님.
        # 오라뎀 0 만들기: 참 3/1에 해안 두 번은 불가(대응 1회).
        # 무게추 2/1 + 해안 -2/0 → 0/1: 오라뎀 0이지만 라이프뎀 1 존재.
        # 팔방(공격후 있는 카드) 2/1 + 해안 → 0/1, 공격후 팔상 판정 카드로 검증.
        s = fresh(m0="saine", m1="yurina", dist=4)
        s.players[0].hand = ["02-saine-o-n-1"]        # 팔방 4-5 2/1, 공격후 팔상: 서브공격
        give_aura(s, 0, 0)                              # 팔상 충족
        s.players[1].specials = ["01-yurina-o-s-2"]   # 해안 0-10 2/-, 소모3
        give_flare(s, 1, 3)
        give_aura(s, 1, 5)
        bot = ScriptBot(react_with="01-yurina-o-s-2", seed=1)
        life1 = s.players[1].life
        use_card(s, 0, "hand", "02-saine-o-n-1", bot, RNG)
        # 해안 2/-: P0 오라 0 → 데미지 0 (오라에 가능한 만큼 = 0)... 해안 자체는 성공
        # 팔방 2/1 → 해안 디버프 0/1 → P1 라이프 선택 강제? 오라5≥0 → 오라 0뎀 선택가능
        # 봇 auto: 실효오라 5 >= 0 → 오라 선택 → 데미지 0
        # 핵심: 공격후(팔상 서브공격 4-5 2/1)가 해결되는가 → P1 오라 5-2=3
        assert s.players[1].aura == 3, f"공격후 미해결: 오라 {s.players[1].aura}"

    def test_boat_immediate_saiki_on_kessa_transition(self):
        """쪽배 FAQ: 라이프 4→3에 즉재기, 3→2에는 재기 안 됨."""
        s = fresh(m0="yurina", m1="saine", dist=3)
        p = s.players[0]
        p.used_specials = ["01-yurina-o-s-3"]  # 쪽배 (사용됨)
        p.specials = []
        give_life(s, 0, 4)
        give_aura(s, 0, 0)
        # 상대 공격으로 라이프 4→3
        s.players[1].hand = ["02-saine-o-n-3"]  # 무게추 2-3 2/1
        bot = ScriptBot(seed=1)
        use_card(s, 1, "hand", "02-saine-o-n-3", bot, RNG)
        assert p.life == 3
        assert "01-yurina-o-s-3" in p.specials, "즉재기 미발동"
        # 다시 사용됨으로 놓고 3→2: 재기 안 됨
        p.specials.remove("01-yurina-o-s-3")
        p.used_specials = ["01-yurina-o-s-3"]
        s.players[1].hand = ["02-saine-o-n-3"]
        s.players[1].discard.remove("02-saine-o-n-3")
        bot2 = ScriptBot(seed=1)
        use_card(s, 1, "hand", "02-saine-o-n-3", bot2, RNG)
        assert p.life == 2
        assert "01-yurina-o-s-3" not in p.specials, "3→2에 재기가 잘못 발동"


# ═══════════════════════════════════════════
# 사이네
# ═══════════════════════════════════════════
class TestSaine:

    def test_happo_hassou_checked_at_attack_after_time(self):
        """팔방 FAQ: 공격후 팔상 판정은 공격후 해결 시점.
        오라1로 사용 → 대응으로 오라2가 되면 서브공격 미발생."""
        s = fresh(m0="saine", m1="tokoyo", dist=4)
        give_aura(s, 0, 1)  # 팔상
        s.players[0].hand = ["02-saine-o-n-1"]   # 팔방 4-5 2/1
        # 대응으로 오라를 늘릴 카드: 시의 춤 (대응/행동, 집중+1 & 플레어→오라 1)
        s.players[1].hand = ["04-tokoyo-o-n-4"]
        give_flare(s, 1, 2)
        give_aura(s, 1, 5)
        # 시의 춤이 '자기(P1)' 오라를 늘리는 거라 P0 팔상엔 영향 없음...
        # P0 오라를 늘리는 대응은 core4에 없음 → 역방향 검증:
        # 오라 2로 사용 → 대응(무게추 등 P0 오라 깎기)으로 오라 1 → 서브공격 발생
        s = fresh(m0="saine", m1="tokoyo", dist=4)
        give_aura(s, 0, 2)  # 팔상 아님
        s.players[0].hand = ["02-saine-o-n-1"]
        s.players[1].hand = ["04-tokoyo-o-n-2"]  # 우아한 타격 2-4 2/1 (대응)
        s.players[1].vigor = 0  # 무효화 안 되게
        give_aura(s, 1, 5)
        bot = ScriptBot(react_with="04-tokoyo-o-n-2", damage="aura", seed=1)
        use_card(s, 0, "hand", "02-saine-o-n-1", bot, RNG)
        # 우아한 타격 2/1 → P0 오라 2→0 → 팔상 진입
        assert s.players[0].aura == 0
        # 팔방 본공격 2/1 → P1 오라 5→3, 공격후 팔상 충족 → 서브 2/1 → 오라 3→1
        assert s.players[1].aura == 1, f"공격후 팔상 서브공격 미발생: {s.players[1].aura}"

    def test_muonheki_full_absorb_and_destroy(self):
        """무음벽 FAQ: 오라1+카드2로 3/1을 오라로 받기 가능, 전부 더스트로, 무음벽 파기."""
        s = fresh(m0="yurina", m1="saine", dist=3)
        s.players[0].hand = ["01-yurina-o-n-1"]  # 참 3/1
        enh = Enhancement("02-saine-o-n-7", tokens=2)
        s.players[1].enhancements.append(enh)
        s.dust -= 2
        give_aura(s, 1, 1)
        s.check_conservation()
        dust_before = s.dust
        bot = ScriptBot(damage="aura", seed=1)
        use_card(s, 0, "hand", "01-yurina-o-n-1", bot, RNG)
        assert s.players[1].aura == 0
        assert s.players[1].enhancements == []          # 무음벽 파기
        assert "02-saine-o-n-7" in s.players[1].discard
        assert s.dust == dust_before + 3                 # 오라1+카드2 모두 더스트

    def test_hangmyeong_cost_ignores_muonheki(self):
        """항명공진 FAQ: 무음벽 위 결정은 오라가 아니므로 소모값 감소에 미포함."""
        from cards import effective_cost
        s = fresh(m0="saine", m1="saine", dist=3)
        give_aura(s, 1, 2)
        enh = Enhancement("02-saine-o-n-7", tokens=3)
        s.players[1].enhancements.append(enh)
        s.dust -= 3
        s.check_conservation()
        # 항명공진 소모 8 - 상대오라 2 = 6 (무음벽 3은 무시)
        assert effective_cost(s, 0, "02-saine-o-s-2") == 6

    def test_yuldong_out_of_range_sub_attacks_no_reaction_window(self):
        """율동호극 FAQ: 적정거리 밖 서브공격은 부정 소거 → 대응 시점 없음."""
        s = fresh(m0="saine", m1="tokoyo", dist=6)  # 3-4/4-5/3-5 전부 밖
        s.players[0].specials = ["02-saine-o-s-1"]
        give_flare(s, 0, 6)
        windows = []
        class SpyBot(ScriptBot):
            def choose_reaction(self, state, pidx, options, atk):
                windows.append(options)
                return None
        bot = SpyBot(seed=1)
        aura1 = s.players[1].aura
        s.players[1].hand = ["04-tokoyo-o-n-2"]  # 대응 후보 존재하게
        use_card(s, 0, "special", "02-saine-o-s-1", bot, RNG)
        assert windows == [], f"부정 공격에 대응 창 열림: {len(windows)}회"
        assert s.players[1].aura == aura1  # 데미지 없음

    def test_eummu_saiki_at_end_phase(self):
        """음무쇄빙 재기: 팔상이면 소유자 종료 페이즈에 미사용으로 복귀."""
        s = fresh(m0="saine", m1="yurina", dist=3)
        p = s.players[0]
        p.used_specials = ["02-saine-o-s-3"]
        p.specials = []
        give_aura(s, 0, 1)  # 팔상
        bot = ScriptBot(seed=1)
        end_phase(s, bot, RNG)
        assert "02-saine-o-s-3" in p.specials, "종료 페이즈 재기 미발동"


# ═══════════════════════════════════════════
# 히미카
# ═══════════════════════════════════════════
class TestHimika:

    def test_renka_counts_cards_used_not_covers(self):
        """연화 FAQ: 기본동작 비용으로 덮은 카드는 사용 장수에 미포함."""
        s = fresh(m0="himika", dist=7)
        s.players[0].hand = ["03-himika-o-n-1", "03-himika-o-n-5", "03-himika-o-n-2"]
        # 슛 사용(1장) → 백스탭 사용(2장) → 래피드(3장째, 연화 +1/+1)
        give_aura(s, 1, 5)
        bot = ScriptBot(damage="aura", seed=1)
        use_card(s, 0, "hand", "03-himika-o-n-1", bot, RNG)   # 슛 2/1 → 오라 5→3
        assert s.cards_used_this_turn[0] == 1
        use_card(s, 0, "hand", "03-himika-o-n-5", bot, RNG)   # 백스탭 (draw+간격)
        assert s.cards_used_this_turn[0] == 2
        # 간격 7→8 (백스탭 더스트→간격 1)... 래피드 6-8 적정 확인
        use_card(s, 0, "hand", "03-himika-o-n-2", bot, RNG)   # 래피드 2/1, 연화 +1/+1 → 3/2
        assert s.cards_used_this_turn[0] == 3
        # 연화 적용: 오라뎀 3 → 오라 3에서 3 소모 → 0
        assert s.players[1].aura == 0, f"연화 미적용: {s.players[1].aura}"

    def test_cancelled_attack_counts_as_used(self):
        """연화 FAQ: 공격이 무효화되어도 사용 장수에 포함."""
        s = fresh(m0="himika", m1="tokoyo", dist=7)
        s.players[0].hand = ["03-himika-o-n-1"]
        s.players[1].hand = ["04-tokoyo-o-n-2"]  # 우아한 타격... 2-4 거리 밖 (7)
        # 거리 7에서 우아한 타격 대응 불가 → 영원한 꽃(0-10)으로
        s.players[1].specials = ["04-tokoyo-o-s-1"]
        give_flare(s, 1, 5)
        bot = ScriptBot(react_with="04-tokoyo-o-s-1", seed=1)
        use_card(s, 0, "hand", "03-himika-o-n-1", bot, RNG)
        assert s.cards_used_this_turn[0] == 1  # 무효화됐어도 1장 사용

    def test_recoil_burn_win_before_attack_after(self):
        """리코일 번(구 매그넘) FAQ: 데미지로 상대 라이프 0 → 즉시 승리,
        공격후(자기 라이프→간격) 해결 전 → 내 라이프 안 깎임."""
        s = fresh(m0="himika", m1="yurina", dist=5)
        give_life(s, 0, 2)
        give_life(s, 1, 1)
        give_aura(s, 1, 0)
        s.players[0].hand = ["03-himika-o-n-3"]  # 리코일 번 2-8 2/2
        bot = ScriptBot(seed=1)
        use_card(s, 0, "hand", "03-himika-o-n-3", bot, RNG)
        assert s.is_over() and s.winner == 0
        assert s.players[0].life == 2, "공격후가 승리 판정 전에 해결됨"

    def test_full_burst_separate_damages(self):
        """풀 버스트 FAQ: 오라1 라이프4 상대 → 오라0, 라이프3 (각각 따로)."""
        s = fresh(m0="himika", m1="yurina", dist=7)
        give_aura(s, 1, 1)
        give_life(s, 1, 4)
        s.players[0].hand = ["03-himika-o-n-4"]  # 풀 버스트 (전력) 5-9 3/1
        bot = ScriptBot(seed=1)
        # 전력 카드: is_fullpower=True 경로로 직접 사용
        use_card(s, 0, "hand", "03-himika-o-n-4", bot, RNG)
        assert s.players[1].aura == 0
        assert s.players[1].life == 3


# ═══════════════════════════════════════════
# 토코요
# ═══════════════════════════════════════════
class TestTokoyo:

    def test_poets_dance_empty_option_allowed(self):
        """시의 춤 FAQ: 오라 0이어도 '오라→간격' 선택 가능 (아무 일도 안 일어남)."""
        s = fresh(m0="tokoyo", dist=3)
        give_aura(s, 0, 0)
        s.players[0].hand = ["04-tokoyo-o-n-4"]
        dist_before = s.distance
        bot = ScriptBot(option=1, seed=1)  # 옵션1: my_aura→distance
        use_card(s, 0, "hand", "04-tokoyo-o-n-4", bot, RNG)
        assert s.distance == dist_before  # 이동 없음, 에러도 없음
        assert s.players[0].vigor >= 1     # 집중력 획득은 정상 해결

    def test_wind_stage_partial_return(self):
        """바람의 무대 FAQ: 파기시 오라가 1뿐이면 1개만 간격으로."""
        s = fresh(m0="tokoyo", m1="yurina", dist=5)
        p = s.players[0]
        enh = Enhancement("04-tokoyo-o-n-6", tokens=1)  # 다음 틱에 파기
        p.enhancements.append(enh)
        s.dust -= 1
        give_aura(s, 0, 1)
        s.check_conservation()
        p._had_first_turn = True
        dist_before = s.distance
        class PassBot(ScriptBot):
            def choose_main_action(self, state, pidx, legal, rng):
                return ("end", None)
            def decide_reconstruct(self, state, pidx):
                return False
        play_turn(s, random.Random(5), PassBot(seed=1))
        # 틱: 결정 1→0 → 파기 → 파기시 오라→간격 2 중 1개만 (오라 1)
        # 간격 변화: 틱으로 +0(결정은 더스트로), 파기시 +1
        assert s.distance == dist_before + 1
        assert s.players[0].aura == 0

    def test_clear_stage_vigor2_keeps_withered(self):
        """맑음의 무대 FAQ: 위축 상태로 사용 → 집중력 2가 되고 위축은 유지."""
        s = fresh(m0="tokoyo", dist=3)
        p = s.players[0]
        p.withered = True
        p.vigor = 0
        p.hand = ["04-tokoyo-o-n-7"]  # 맑음의 무대 (부여, 종단)
        bot = ScriptBot(seed=1)
        use_card(s, 0, "hand", "04-tokoyo-o-n-7", bot, RNG)
        assert p.vigor == 2
        assert p.withered is True, "위축이 잘못 해제됨"

    def test_terminal_locks_rest_of_turn(self):
        """종단 (맑음의 무대): 사용 후 이 턴 카드/기본동작 불가."""
        s = fresh(m0="tokoyo", dist=3)
        assert s.terminal_lock == [False, False]
        s.players[0].hand = ["04-tokoyo-o-n-7"]
        bot = ScriptBot(seed=1)
        use_card(s, 0, "hand", "04-tokoyo-o-n-7", bot, RNG)
        assert s.terminal_lock[0] is True and s.terminal_lock[1] is False

    def test_mugung_saiki_kyouchi_end_phase(self):
        """무궁한 바람 재기: 경지면 종료 페이즈에 복귀."""
        s = fresh(m0="tokoyo", dist=3)
        p = s.players[0]
        p.used_specials = ["04-tokoyo-o-s-3"]
        p.specials = []
        p.vigor = 2  # 경지
        bot = ScriptBot(seed=1)
        end_phase(s, bot, RNG)
        assert "04-tokoyo-o-s-3" in p.specials


if __name__ == "__main__":
    # pytest가 있으면 pytest로, 없으면 미니 러너로
    try:
        import pytest
        sys.exit(pytest.main([__file__, "-v"]))
    except ImportError:
        from run_tests import run_module
        run_module(__file__)


# ═══════════════════════════════════════════
# 오라 데미지 선택 & 초극 (v2 하가네 검수 반영)
# ═══════════════════════════════════════════
class TestAuraDamageChoice:

    def _greedy_aura(self):
        class B(ScriptBot):
            def choose_damage_type(self, s, t, a, l): return "aura"
        return B(seed=1)

    def test_chokyoku_blocks_aura_choice(self):
        """초극 FAQ: 대천공 X6/Y3, 상대 오라5 → 6>5라 오라 선택 봉쇄, 라이프 Y=3 강제."""
        s = fresh("hagane", "yurina", 3)
        s.turn_start_distance = 9  # X=6
        s.players[0].specials = ["08-hagane-o-s-1"]
        s.players[0].flare = 4
        s.players[1].aura = 5
        rebalance(s)
        lb = s.players[1].life
        use_card(s, 0, "special", "08-hagane-o-s-1", self._greedy_aura(), RNG)
        assert s.players[1].aura == 5          # 오라 선택 봉쇄
        assert s.players[1].life == lb - 3     # 라이프 Y=3 강제

    def test_aura_cap_5_without_chokyoku(self):
        """오라뎀 상한 5 (6-4-1-4): 초극 없으면 6뎀도 5로 클램프 → 오라5로 받기 가능."""
        from combat import choose_and_apply_damage
        s = fresh("himika", "yurina", 3)
        s.players[1].aura = 5
        rebalance(s)
        # 이미 상한 적용된 5로 해결 (perform_card_attack이 클램프)
        choose_and_apply_damage(s, 0, 1, 5, 3, self._greedy_aura())
        assert s.players[1].aura == 0

    def test_life_dash_aura_insufficient(self):
        """라이프 '-' + 오라 부족: 오라로 가능한 만큼 받아 0이 된다."""
        from combat import choose_and_apply_damage
        s = fresh("yurina", "saine", 3)
        s.players[1].aura = 2
        rebalance(s)
        choose_and_apply_damage(s, 0, 1, 3, None, self._greedy_aura())
        assert s.players[1].aura == 0

    def test_both_int_aura_insufficient_forces_life(self):
        """양쪽 정수 + 오라 부족: 오라 선택 불가 → 라이프 강제."""
        from combat import choose_and_apply_damage
        s = fresh("yurina", "saine", 3)
        s.players[1].aura = 2
        rebalance(s)
        lb = s.players[1].life
        choose_and_apply_damage(s, 0, 1, 3, 1, self._greedy_aura())
        assert s.players[1].aura == 2 and s.players[1].life == lb - 1


# ═══════════════════════════════════════════
# 코르누 (동결, v2)
# ═══════════════════════════════════════════
class TestKorunu:

    def test_freeze_occupies_aura_slot(self):
        """동결 토큰이 오라 칸을 점유 (결정3+동결2=5) → 전진/휘감기 불가."""
        s = fresh("korunu", "yurina", 5)
        s.players[1].aura = 3
        s.players[1].frozen = 2
        rebalance(s)
        acts = legal_basic_actions(s, 1)
        assert "advance" not in acts and "gather" not in acts

    def test_hold_thaws_when_frozen(self):
        """동결된 플레이어의 품기 = 해동 (동결 1개를 게임 바깥으로)."""
        from tokens import hold
        s = fresh("korunu", "yurina", 3)
        s.players[1].frozen = 2
        s.players[1].aura = 0
        rebalance(s)
        hold(s, 1)
        assert s.players[1].frozen == 1

    def test_freeze_preserves_token_conservation(self):
        """동결 토큰은 벚꽃결정과 별개 → 보존식 36 불변."""
        s = fresh("korunu", "yurina", 4)
        s.players[0].hand = ["15-korunu-o-n-1"]  # 눈 칼날 (공격후 동결)
        use_card(s, 0, "hand", "15-korunu-o-n-1", ScriptBot(seed=1), RNG)
        assert s.players[1].frozen == 1
        s.check_conservation()  # 예외 안 나면 통과

    def test_dongsang_blocks_opponent_hold(self):
        """동상 전개중이면 상대는 품기 불가."""
        from state import Enhancement
        s = fresh("korunu", "yurina", 3)
        s.players[0].enhancements.append(Enhancement("15-korunu-o-n-6", tokens=2))
        s.players[1].aura = 3
        rebalance(s)
        assert "hold" not in legal_basic_actions(s, 1)

    def test_upas_saiki_on_aura_full(self):
        """우파스 툼 즉재기: 동결로 상대 오라가 꽉 차면 미사용으로 복귀."""
        import random as _r
        from effects import _resolve_op
        s = fresh("korunu", "yurina", 4)
        s.players[0].used_specials = ["15-korunu-o-s-3"]
        s.players[0].specials = []
        s.players[1].aura = 4
        rebalance(s)
        _resolve_op(s, 0, {"op": "freeze", "who": "opp", "n": 1},
                    ScriptBot(seed=1), _r.Random(1), {})
        assert "15-korunu-o-s-3" in s.players[0].specials


# ═══════════════════════════════════════════
# 오보로 (설치 / 덮음패 사용, v2)
# ═══════════════════════════════════════════
class TestOboro:

    def _covered_bot(self):
        class B(ScriptBot):
            def choose_setup_use(self, s, p, setups):
                return setups[0] if setups else None
        return B(seed=1)

    def test_setup_from_hand_is_plain(self):
        """설치 카드를 손패에서 내면 평범한 공격 (철사 2/1)."""
        s = fresh("oboro", "yurina", 4)
        s.players[1].aura = 5
        rebalance(s)
        s.players[0].hand = ["05-oboro-o-n-1"]  # 철사
        b = ScriptBot(seed=1); b.damage = "aura"
        use_card(s, 0, "hand", "05-oboro-o-n-1", b, RNG)
        assert s.players[1].aura == 3  # 오라뎀 2

    def test_setup_from_covered_bonus(self):
        """철사를 덮음패에서 사용하면 +0/+1 (2/2)."""
        from cards import use_card_from_covered
        s = fresh("oboro", "yurina", 4)
        s.players[1].aura = 5
        rebalance(s)
        s.players[0].covered = ["05-oboro-o-n-1"]
        b = ScriptBot(seed=1); b.damage = "life"
        use_card_from_covered(s, 0, "05-oboro-o-n-1", b, RNG)
        assert s.players[1].life == 8  # 라이프뎀 2 (기본1 +설치1)

    def test_kumasuke_repeats_by_covered_count(self):
        """쿠마스케: 덮음패 장수만큼 추가 공격."""
        s = fresh("oboro", "yurina", 3)
        s.players[1].aura = 5
        s.players[0].covered = ["05-oboro-o-n-3", "05-oboro-o-n-4", "05-oboro-o-n-6"]
        s.players[0].specials = ["05-oboro-o-s-1"]  # 쿠마스케
        s.players[0].flare = 4
        rebalance(s)
        a0, l0 = s.players[1].aura, s.players[1].life
        b = ScriptBot(seed=1); b.damage = "life"
        use_card(s, 0, "special", "05-oboro-o-s-1", b, RNG)
        dmg = (a0 - s.players[1].aura) + (l0 - s.players[1].life)
        assert dmg >= 6  # 본체 + 덮음3장 추가

    def test_setup_reconstruct_hook(self):
        """설치: 재구성 직전 덮음패의 설치 카드 사용 가능."""
        from deck import reconstruct_deck
        s = fresh("oboro", "yurina", 4)
        s.players[0].deck = []
        s.players[0].discard = ["05-oboro-o-n-3"]
        s.players[0].covered = ["05-oboro-o-n-1"]  # 철사(설치)
        s.players[1].aura = 5
        rebalance(s)
        reconstruct_deck(s, 0, by_rule=True, rng=RNG, agent=self._covered_bot())
        assert "05-oboro-o-n-1" not in s.players[0].covered  # 사용됨


# ═══════════════════════════════════════════
# 라이라 (풍신/뇌신 게이지, v2)
# ═══════════════════════════════════════════
class TestRaira:

    def _rid(self, name):
        from setup import CARD_DB
        for k, c in CARD_DB.items():
            if isinstance(c, dict) and c.get("megami") == "raira" \
                    and c["name_ko"] == name:
                return k

    def test_ulboo_choice_withers_and_gains(self):
        """울부짖기 선택0: 상대 위축 + 각 게이지 +1."""
        s = fresh("raira", "yurina", 3)
        rebalance(s)
        s.players[0].hand = [self._rid("울부짖기")]
        b = ScriptBot(seed=1); b.option = 0
        use_card(s, 0, "hand", self._rid("울부짖기"), b, RNG)
        assert s.players[1].withered
        assert s.players[0].fuujin == 1 and s.players[0].raijin == 1

    def test_ulboo_doubles_raijin(self):
        """울부짖기 선택1: 뇌신 게이지 2배."""
        s = fresh("raira", "yurina", 3)
        s.players[0].raijin = 2
        rebalance(s)
        s.players[0].hand = [self._rid("울부짖기")]
        b = ScriptBot(seed=1); b.option = 1
        use_card(s, 0, "hand", self._rid("울부짖기"), b, RNG)
        assert s.players[0].raijin == 4

    def test_poongnoe_x_is_min_gauge(self):
        """풍뢰격: X = min(풍신, 뇌신) 오라 데미지."""
        s = fresh("raira", "yurina", 2)
        s.players[0].fuujin = 3
        s.players[0].raijin = 5
        s.players[1].aura = 5
        rebalance(s)
        s.players[0].hand = [self._rid("풍뢰격")]
        b = ScriptBot(seed=1); b.damage = "aura"
        use_card(s, 0, "hand", self._rid("풍뢰격"), b, RNG)
        assert s.players[1].aura == 2  # X=3 오라뎀

    def test_poongma_summon_by_fuujin(self):
        """풍마초래공: 풍신 7 → 선풍+전회 획득."""
        s = fresh("raira", "yurina", 3)
        s.players[0].fuujin = 7
        s.players[0].specials = [self._rid("풍마초래공")]
        s.players[0].flare = 2
        rebalance(s)
        use_card(s, 0, "special", self._rid("풍마초래공"), ScriptBot(seed=1), RNG)
        assert self._rid("풍마선풍") in s.players[0].specials
        assert self._rid("풍마전회") in s.players[0].specials

    def test_cheonroe_summon_x_attacks(self):
        """천뢰소환진: 뇌신 6 → 3회(절반 올림) 1/1 공격."""
        s = fresh("raira", "yurina", 5)
        s.players[0].raijin = 6
        s.players[1].aura = 3
        s.players[0].specials = [self._rid("천뢰소환진")]
        s.players[0].flare = 6
        s.players[0].aura = 0
        rebalance(s)
        a0, l0 = s.players[1].aura, s.players[1].life
        b = ScriptBot(seed=1); b.damage = "aura"
        use_card(s, 0, "special", self._rid("천뢰소환진"), b, RNG)
        assert (a0 - s.players[1].aura) + (l0 - s.players[1].life) == 3


# ═══════════════════════════════════════════
# 치카게 (독주머니, v2)
# ═══════════════════════════════════════════
class TestChikage:

    def _cid(self, name):
        from setup import CARD_DB
        for k, c in CARD_DB.items():
            if isinstance(c, dict) and c.get("megami") == "chikage" \
                    and c["name_ko"] == name:
                return k

    def test_pouch_initial_five(self):
        """독주머니 준비: 마비/환각/이완 각1 + 멸등독 2 = 5장 (룰북 7-1)."""
        s = fresh("chikage", "yurina", 4)
        assert len(s.players[0].poison_pouch) == 5
        assert s.players[0].poison_pouch.count(self._cid("멸등독")) == 2

    def test_poison_returns_to_pouch(self):
        """마비독 사용 → 상대(치카게) 독주머니로 복귀 + 종단."""
        s = fresh("chikage", "yurina", 3)
        rebalance(s)
        s.players[1].hand = [self._cid("마비독")]
        n0 = len(s.players[0].poison_pouch)
        use_card(s, 1, "hand", self._cid("마비독"), ScriptBot(seed=1), RNG)
        assert len(s.players[0].poison_pouch) == n0 + 1
        assert s.terminal_lock[1]

    def test_myeoldeung_never_returns(self):
        """멸등독: 사용해도 독주머니로 복귀하지 않음 (버림패 잔류) — 나무위키 FAQ."""
        s = fresh("chikage", "yurina", 3)
        s.players[1].aura = 3
        rebalance(s)
        s.players[1].hand = [self._cid("멸등독")]
        n0 = len(s.players[0].poison_pouch)
        use_card(s, 1, "hand", self._cid("멸등독"), ScriptBot(seed=1), RNG)
        assert len(s.players[0].poison_pouch) == n0
        assert self._cid("멸등독") in s.players[1].discard

    def test_mabi_blocked_after_basic_action(self):
        """마비독: 이번 턴 기본동작을 수행했다면 사용 불가."""
        from cards import legal_card_uses
        s = fresh("chikage", "yurina", 3)
        rebalance(s)
        s.players[1].hand = [self._cid("마비독")]
        s.basic_actions_this_turn[1] = 1
        legal = [c for _, c in legal_card_uses(s, 1, False)]
        assert self._cid("마비독") not in legal

    def test_bangi_assignment(self):
        """반기의 얽힌독 대입: 1/2 공격 → 2/2 (오라뎀 = 라이프뎀)."""
        from state import Enhancement
        s = fresh("chikage", "yurina", 4)
        s.players[1].aura = 5
        s.players[0].enhancements.append(
            Enhancement(self._cid("반기의 얽힌독"), tokens=2))
        s.players[0].specials = [self._cid("윤회의 안개독")]
        s.players[0].flare = 1
        rebalance(s)
        b = ScriptBot(seed=1); b.damage = "aura"
        use_card(s, 0, "special", self._cid("윤회의 안개독"), b, RNG)
        assert s.players[1].aura == 3   # 1/2 → 2/2

    def test_kachibal_effective_distance(self):
        """까치발 걸음: 전개중 간격 2 감소 (적정거리 판정)."""
        from state import Enhancement
        from combat import effective_distance
        s = fresh("chikage", "yurina", 5)
        s.players[0].enhancements.append(
            Enhancement(self._cid("까치발 걸음"), tokens=2))
        rebalance(s)
        assert effective_distance(s) == 3

    def test_poison_cannot_be_covered_at_end(self):
        """독은 덮을 수 없음: 종료 페이즈 손패 상한 초과여도 독은 유지."""
        from turn import end_phase
        s = fresh("chikage", "yurina", 3)
        rebalance(s)
        s.active = 1
        s.players[1].hand = [self._cid("마비독"), self._cid("환각독"),
                             self._cid("이완독")]
        end_phase(s, ScriptBot(seed=1), RNG)
        assert len(s.players[1].hand) == 3   # 전부 독 → 못 덮고 유지


# ═══════════════════════════════════════════
# 유키히 (우산, v2)
# ═══════════════════════════════════════════
class TestYukihi:

    def _yid(self, part):
        from setup import CARD_DB
        for k, c in CARD_DB.items():
            if isinstance(c, dict) and c.get("megami") == "yukihi" \
                    and part in c["name_ko"]:
                return k

    def test_umbrella_switches_stats(self):
        """우산 카드: 접힘 4-6 3/1 ↔ 폄 0-2 1/2 (숨긴 바늘)."""
        cid = self._yid("숨긴 바늘")
        s = fresh("yukihi", "yurina", 5)
        s.players[1].aura = 5
        rebalance(s)
        s.players[0].hand = [cid]
        b = ScriptBot(seed=1); b.damage = "aura"
        use_card(s, 0, "hand", cid, b, RNG)
        assert s.players[1].aura == 2   # 접힘 3/1

        s = fresh("yukihi", "yurina", 1)
        s.players[0].umbrella_open = True
        s.players[1].aura = 5
        rebalance(s)
        s.players[0].hand = [cid]
        b = ScriptBot(seed=1); b.damage = "aura"
        use_card(s, 0, "hand", cid, b, RNG)
        assert s.players[1].aura == 4   # 폄 1/2

    def test_snowflake_saiki_every_toggle(self):
        """흩날리는 눈꽃 즉재기: 개폐 상태와 무관하게 개폐할 때마다."""
        import random as _r
        from effects import _resolve_op
        cid = self._yid("눈꽃")
        s = fresh("yukihi", "yurina", 3)
        rebalance(s)
        for start_open in (False, True):
            s.players[0].umbrella_open = start_open
            s.players[0].specials = []
            s.players[0].used_specials = [cid]
            _resolve_op(s, 0, {"op": "umbrella_toggle"},
                        ScriptBot(seed=1), _r.Random(1), {})
            assert cid in s.players[0].specials

    def test_inyeon_destroy_follows_current_state(self):
        """인연 맺기: 파기시 화살표는 파기 시점의 우산 상태 기준."""
        from state import Enhancement
        from cards import destroy_enhancement
        cid = self._yid("인연 맺기")
        s = fresh("yukihi", "yurina", 5)
        s.players[0].umbrella_open = True
        rebalance(s)
        enh = Enhancement(cid, tokens=0)
        s.players[0].enhancements.append(enh)
        d0 = s.distance
        destroy_enhancement(s, 0, enh, ScriptBot(seed=1), RNG)
        assert s.distance == d0 - 1   # 폄: 간격→더스트 (반전)

    def test_lantern_open_only(self):
        """일렁이는 등불: 접힘 4-6 0/0, 폄 간격0 4/5."""
        cid = self._yid("등불")
        s = fresh("yukihi", "yurina", 0)
        s.players[0].umbrella_open = True
        s.players[0].specials = [cid]
        s.players[0].flare = 5
        s.players[1].aura = 5
        rebalance(s)
        lb = s.players[1].life
        b = ScriptBot(seed=1); b.damage = "life"
        use_card(s, 0, "special", cid, b, RNG)
        assert s.players[1].life == lb - 5


# ═══════════════════════════════════════════
# 메구미 (씨앗 결정, v2)
# ═══════════════════════════════════════════
class TestMegumi:

    def _mid(self, part):
        from setup import CARD_DB
        for k, c in CARD_DB.items():
            if isinstance(c, dict) and c.get("megami") == "megumi" \
                    and part in c["name_ko"]:
                return k

    def test_soil_init_five_seeds(self):
        """준비: 토양에 씨앗 5개(발아 안 함), 벚꽃결정과 별개 보존."""
        s = fresh("megumi", "yurina", 4)
        assert s.players[0].soil_unsprouted == 5
        assert s.players[0].seed_total() == 5
        s.check_conservation()   # 벚꽃결정 36 별개

    def test_enhance_sprouts_seed_dedication_dust_first(self):
        """부여 사용: 씨앗 1개 발아 + 봉납은 더스트 우선(씨앗 미소비)."""
        s = fresh("megumi", "yurina", 3)
        rebalance(s)
        s.players[0].hand = [self._mid("갈대")]
        b = ScriptBot(seed=1); b.count = 0
        use_card(s, 0, "hand", self._mid("갈대"), b, RNG)
        assert s.players[0].soil_unsprouted == 4   # 발아 1개
        assert s.players[0].soil_sprouted == 1     # 봉납은 더스트로
        enh = s.players[0].enhancements[-1]
        assert enh.tokens == 1 and enh.seeds == 0

    def test_growth_places_seeds_on_enh(self):
        """생육 X: 발아 씨앗을 부여패에 얹음 (벚꽃결정 간주)."""
        s = fresh("megumi", "yurina", 3)
        s.players[0].soil_sprouted = 2
        s.players[0].soil_unsprouted = 3
        rebalance(s)
        s.players[0].hand = [self._mid("갈대")]  # 생육1
        b = ScriptBot(seed=1); b.count = 1
        use_card(s, 0, "hand", self._mid("갈대"), b, RNG)
        assert s.players[0].enhancements[-1].seeds == 1

    def test_galdae_extends_range_by_seeds(self):
        """갈대 전개중: 간격/달인간격이 부여패 위 씨앗 수만큼 증가."""
        from combat import effective_distance
        from tokens import master_range
        from state import Enhancement
        s = fresh("megumi", "yurina", 3)
        s.players[0].enhancements.append(
            Enhancement(self._mid("갈대"), tokens=1, seeds=2))
        rebalance(s)
        assert effective_distance(s) == 5
        assert master_range(s, 0) == 4

    def test_seeds_return_to_soil_on_destroy(self):
        """부여패 파기 시 씨앗은 토양(미발아)으로 복귀, 총 5개 보존."""
        from state import Enhancement
        from cards import destroy_enhancement
        s = fresh("megumi", "yurina", 3)
        e = Enhancement(self._mid("갈대"), tokens=0, seeds=2)
        s.players[0].enhancements.append(e)
        s.players[0].soil_unsprouted = 3
        rebalance(s)
        destroy_enhancement(s, 0, e, ScriptBot(seed=1), RNG)
        assert s.players[0].soil_unsprouted == 5
        assert s.players[0].seed_total() == 5

    def test_ingwa_sprouts_after_attack(self):
        """인과율의 뿌리: 공격후 씨앗 1개 발아."""
        s = fresh("megumi", "yurina", 5)
        s.players[1].aura = 5
        s.players[0].soil_unsprouted = 5
        s.players[0].soil_sprouted = 0
        s.players[0].specials = [self._mid("인과율")]
        s.players[0].flare = 1
        rebalance(s)
        b = ScriptBot(seed=1); b.damage = "aura"
        use_card(s, 0, "special", self._mid("인과율"), b, RNG)
        assert s.players[0].soil_sprouted == 1


# ═══════════════════════════════════════════
# 신라 (계략/봉인, v2)
# ═══════════════════════════════════════════
class TestShinra:

    def _nid(self, name):
        from setup import CARD_DB
        for k, c in CARD_DB.items():
            if isinstance(c, dict) and c.get("megami") == "shinra" \
                    and c["name_ko"] == name:
                return k

    def _schemebot(self, scheme="shinsan"):
        class B(ScriptBot):
            def choose_scheme(self, s, p): return scheme
        return B(seed=1)

    def test_scheme_starts_shinsan(self):
        """계략 시작 상태: 신산."""
        s = fresh("shinra", "yurina", 4)
        assert s.players[0].scheme == "shinsan"

    def test_ipron_covers_instead_of_damage(self):
        """입론: 상대 패산 2장 이상이면 데미지 대신 패산 위 2장 덮음."""
        s = fresh("shinra", "yurina", 4)
        s.players[1].aura = 5
        s.players[1].deck = ["01-yurina-o-n-1", "01-yurina-o-n-2",
                             "01-yurina-o-n-3"]
        s.players[0].hand = [self._nid("입론")]
        rebalance(s)
        a0 = s.players[1].aura
        cov0 = len(s.players[1].covered)
        use_card(s, 0, "hand", self._nid("입론"), ScriptBot(seed=1), RNG)
        assert s.players[1].aura == a0
        assert len(s.players[1].covered) == cov0 + 2

    def test_scheme_execute_and_prepare(self):
        """선동: 신산 효과(더스트→간격) 실행 + 다음 계략 준비."""
        s = fresh("shinra", "yurina", 3)
        s.players[0].scheme = "shinsan"
        s.players[0].hand = [self._nid("선동")]
        rebalance(s)
        d0 = s.distance
        use_card(s, 0, "hand", self._nid("선동"), self._schemebot("kimou"), RNG)
        assert s.distance == d0 + 1
        assert s.players[0].scheme == "kimou"

    def test_kanzen_seals_discard(self):
        """완전논파: 상대 버림패 1장을 봉인 (영구)."""
        s = fresh("shinra", "yurina", 3)
        s.players[1].discard = ["01-yurina-o-n-1"]
        s.players[0].specials = [self._nid("완전논파")]
        s.players[0].flare = 2
        rebalance(s)
        use_card(s, 0, "special", self._nid("완전논파"), ScriptBot(seed=1), RNG)
        assert "01-yurina-o-n-1" in s.players[0].sealed_cards
        assert "01-yurina-o-n-1" not in s.players[1].discard

    def test_cheonji_swaps_damage(self):
        """천지반박: 오라뎀↔라이프뎀 교환 (반론 1/- → -/1)."""
        from state import Enhancement
        s = fresh("shinra", "yurina", 3)
        s.players[1].aura = 5
        s.players[0].enhancements.append(
            Enhancement(self._nid("천지반박"), tokens=5))
        s.players[0].hand = [self._nid("반론")]
        rebalance(s)
        lb = s.players[1].life
        use_card(s, 0, "hand", self._nid("반론"), ScriptBot(seed=1), RNG)
        assert s.players[1].life == lb - 1

    def test_samra_destroy_loses(self):
        """삼라판증: 파기시 패배 (야미쿠라의 정반대)."""
        from state import Enhancement
        from cards import destroy_enhancement
        s = fresh("shinra", "yurina", 3)
        enh = Enhancement(self._nid("삼라판증"), tokens=0)
        s.players[0].enhancements.append(enh)
        rebalance(s)
        destroy_enhancement(s, 0, enh, ScriptBot(seed=1), RNG)
        assert s.winner == 1 and s.end_reason == "samra"


# ═══════════════════════════════════════════
# 쿠루루 (기교/톱니바퀴칸, v2)
# ═══════════════════════════════════════════
class TestKururu:

    def _kid(self, part):
        from setup import CARD_DB
        for k, c in CARD_DB.items():
            if isinstance(c, dict) and c.get("megami") == "kururu" \
                    and part in c["name_ko"]:
                return k

    def test_gigyo_counts_from_zones(self):
        """기교: 버림패+부여패+사용된 비장패의 타입 카운트로 완성."""
        from cards import gigyo_complete
        s = fresh("kururu", "yurina", 3)
        s.players[0].discard = ["01-yurina-o-n-1", "01-yurina-o-n-2"]  # 공격 2
        assert gigyo_complete(s, 0, "공공")
        assert not gigyo_complete(s, 0, "공공공")

    def test_tornado_gigyo_damage(self):
        """토네이도: {공공} 완성 시 오라 5 데미지."""
        s = fresh("kururu", "yurina", 3)
        s.players[1].aura = 5
        s.players[0].discard = ["01-yurina-o-n-1", "01-yurina-o-n-2"]
        s.players[0].hand = [self._kid("토네")]
        rebalance(s)
        b = ScriptBot(seed=1); b.damage = "aura"
        use_card(s, 0, "hand", self._kid("토네"), b, RNG, as_fullpower=True)
        assert s.players[1].aura == 0

    def test_reflector_blocks_second_attack(self):
        """리플렉터: 상대 2번째 공격 무효화."""
        from state import Enhancement
        from combat import perform_card_attack
        s = fresh("kururu", "yurina", 3)
        s.players[0].enhancements.append(
            Enhancement(self._kid("리플렉터"), tokens=4))
        s.players[0].aura = 5
        s.players[1].aura = 0
        rebalance(s)
        s.active = 1
        s.attacks_this_turn = [0, 0]
        b = ScriptBot(seed=1); b.damage = "aura"
        perform_card_attack(s, 1, {3}, 2, 0, b, RNG,
                            source_card="01-yurina-o-n-1")
        a1 = s.players[0].aura
        perform_card_attack(s, 1, {3}, 2, 0, b, RNG,
                            source_card="01-yurina-o-n-2")
        a2 = s.players[0].aura
        assert a1 == 3 and a2 == 3   # 2번째 무효

    def test_industria_seals_and_places_dup(self):
        """인더스트리아: 카드 봉인 + 듀플리기어 패산 밑."""
        s = fresh("kururu", "yurina", 3)
        s.players[0].specials = [self._kid("인더스트리아")]
        s.players[0].flare = 1
        s.players[0].hand = ["01-yurina-o-n-1"]
        rebalance(s)
        b = ScriptBot(seed=1)
        use_card(s, 0, "special", self._kid("인더스트리아"), b, RNG)
        assert s.players[0]._industria_sealed == "01-yurina-o-n-1"
        assert "10-kururu-o-s-3-ex1" in s.players[0].deck

    def test_duplfigear_excluded_from_deck(self):
        """듀플리기어는 추가패 → 기본 덱에 포함 안 됨 (통상7 비장4)."""
        from setup import cards_of
        n, sp = cards_of("kururu")
        assert len(n) == 7 and len(sp) == 4
        assert "10-kururu-o-s-3-ex1" not in n + sp


# ═══════════════════════════════════════════
# 탈리야 (조화결정/기동/변신, v2 마지막)
# ═══════════════════════════════════════════
class TestThallya:

    def _tid(self, name):
        from setup import CARD_DB
        for k, c in CARD_DB.items():
            if isinstance(c, dict) and c.get("megami") == "thallya" \
                    and c["name_ko"] == name:
                return k

    def test_harmony_init_five(self):
        """준비: 머신에 조화결정 5개 (벚꽃결정과 별개 보존)."""
        s = fresh("thallya", "yurina", 4)
        assert s.players[0].machine_harmony == 5
        assert s.players[0].harmony_total() == 5
        s.check_conservation()

    def test_burn_keyword_requires_harmony(self):
        """연소 카드: 머신에 조화결정 없으면 사용 불가."""
        from cards import legal_card_uses
        s = fresh("thallya", "yurina", 2)
        s.players[0].machine_harmony = 0
        s.players[0].burned_harmony = 5
        rebalance(s)
        s.players[0].hand = [self._tid("Waving Edge")]
        legal = [c for _, c in legal_card_uses(s, 0, False)]
        assert self._tid("Waving Edge") not in legal

    def test_kidou_changes_distance(self):
        """기동후퇴: 간격 +1 (조화결정 간격+1 토큰)."""
        from effects import _perform_kidou
        from combat import effective_distance
        s = fresh("thallya", "yurina", 3)
        rebalance(s)
        b = ScriptBot(seed=1); b.option = 1
        d0 = effective_distance(s)
        _perform_kidou(s, 0, b, {})
        assert effective_distance(s) == d0 + 1
        assert s.players[0].machine_harmony == 4

    def test_alpha_edge_saiki_on_kidou(self):
        """Alpha-Edge: 기동으로 간격 변화 시 즉재기."""
        from effects import _perform_kidou
        s = fresh("thallya", "yurina", 3)
        rebalance(s)
        s.players[0].used_specials = [self._tid("Alpha-Edge")]
        s.players[0].specials = []
        b = ScriptBot(seed=1); b.option = 1
        _perform_kidou(s, 0, b, {})
        assert self._tid("Alpha-Edge") in s.players[0].specials

    def test_start_phase_recovers_gap_harmony(self):
        """개시 페이즈: 본인 간격 조화결정을 연소됨으로 회수."""
        from turn import start_phase
        import random as _r
        s = fresh("thallya", "yurina", 3)
        s.players[0].machine_harmony = 3
        s.players[0].gap_plus_harmony = 1
        s.players[0].gap_minus_harmony = 1
        rebalance(s)
        s.active = 0
        s.players[0]._had_first_turn = True
        start_phase(s, _r.Random(1), ScriptBot(seed=1))
        assert s.players[0].gap_plus_harmony == 0
        assert s.players[0].gap_minus_harmony == 0
        assert s.players[0].burned_harmony == 2

    def test_julia_transform_when_empty(self):
        """Julia's BlackBox: 머신 0이면 TransForm + 회복2."""
        s = fresh("thallya", "yurina", 3)
        s.players[0].machine_harmony = 0
        s.players[0].burned_harmony = 5
        s.players[0].specials = [self._tid("Julia's BlackBox")]
        s.players[0].flare = 2
        rebalance(s)
        b = ScriptBot(seed=1); b.option = 0
        use_card(s, 0, "special", self._tid("Julia's BlackBox"), b, RNG,
                 as_fullpower=True)
        assert len(s.players[0].transforms) == 1
        assert s.players[0].machine_harmony == 2

    def test_masterpiece_reverses_burn(self):
        """Thallya's Masterpiece: 연소 방향 반전 (연소됨→머신)."""
        from state import Enhancement
        from effects import _burn_harmony
        s = fresh("thallya", "yurina", 3)
        s.players[0].enhancements.append(
            Enhancement(self._tid("Thallya's Masterpiece"), tokens=3))
        s.players[0].machine_harmony = 2
        s.players[0].burned_harmony = 2
        rebalance(s)
        _burn_harmony(s, 0, 1)
        assert s.players[0].machine_harmony == 3
        assert s.players[0].burned_harmony == 1



# ═══════════════════════════════════════════
# 2여신 덱 구축 (쌍장요란 + 안전구축)
# ═══════════════════════════════════════════
class TestTwoMegami:

    def test_deck_build_counts(self):
        """2여신 덱: 통상 7 + 비장 3, 미선택 12장은 게임 밖."""
        from setup import new_game_2v2
        s = new_game_2v2(("yurina", "saine"), ("himika", "tokoyo"), seed=1)
        p = s.players[0]
        assert len(p.deck) + len(p.hand) == 7
        assert len(p.specials) == 3
        assert len(p.out_of_game) == 12   # (14+8) - (7+3) = 12
        s.check_conservation()

    def test_two_megami_state_init(self):
        """2여신 상태 초기화: 두 여신의 신규 토큰 모두 세팅."""
        from setup import new_game_2v2
        s = new_game_2v2(("megumi", "thallya"), ("yurina", "saine"), seed=2)
        assert s.players[0].seed_total() == 5       # 메구미
        assert s.players[0].harmony_total() == 5    # 탈리야
        s.check_conservation()

    def test_same_megami_rejected(self):
        """같은 여신 2명 선택 불가 (2-1)."""
        from setup import new_game_2v2
        try:
            new_game_2v2(("yurina", "yurina"), ("saine", "himika"), seed=1)
            assert False, "같은 여신 허용됨"
        except AssertionError as e:
            assert "같은 여신" in str(e)

    def test_deck_has_both_megami_cards(self):
        """덱에 두 여신의 카드가 섞여 존재 가능."""
        from setup import new_game_2v2, CARD_DB, megamis_of
        s = new_game_2v2(("yurina", "saine"), ("himika", "tokoyo"),
                         seed=5,
                         picks0=(["01-yurina-o-n-1", "01-yurina-o-n-2",
                                  "01-yurina-o-n-3", "02-saine-o-n-1",
                                  "02-saine-o-n-2", "02-saine-o-n-3",
                                  "02-saine-o-n-4"],
                                 ["01-yurina-o-s-1", "02-saine-o-s-1",
                                  "02-saine-o-s-2"]))
        deck_cards = s.players[0].deck + s.players[0].hand
        megamis = set(CARD_DB[c]["megami"] for c in deck_cards)
        assert "yurina" in megamis and "saine" in megamis


# ═══════════════════════════════════════════
# 라이라 대전 (2여신 덱 전용, v2 후속)
# ═══════════════════════════════════════════
class TestRairaTaisen:

    def _deck(self, seed=1):
        from setup import new_game_2v2
        return new_game_2v2(
            ("raira", "yurina"), ("saine", "himika"), seed=seed,
            picks0=(["12-raira-o-n-1", "12-raira-o-n-2", "12-raira-o-n-3",
                     "01-yurina-o-n-1", "01-yurina-o-n-2", "01-yurina-o-n-3",
                     "01-yurina-o-n-4"],
                    ["12-raira-o-s-1", "01-yurina-o-s-1", "01-yurina-o-s-2"]))

    class _GBot(ScriptBot):
        def choose_gauge(self, state, pidx):
            return "fuujin"

    def test_other_megami_card_becomes_taisen(self):
        """라이라가 다른 여신 카드를 사용하면 대전 상태가 된다."""
        s = self._deck()
        p = s.players[0]
        s.distance = 3
        p.hand = ["01-yurina-o-n-1"]
        p.aura = 3
        s.players[1].aura = 5
        rebalance(s)
        f0 = p.fuujin
        use_card(s, 0, "hand", "01-yurina-o-n-1", self._GBot(seed=1), RNG)
        assert "01-yurina-o-n-1" in p.taisen_cards
        assert p.fuujin == f0   # 사용만으로는 게이지 안 오름

    def test_release_taisen_gains_gauge(self):
        """대전 해제 시 게이지 +1."""
        from cards import release_taisen
        s = self._deck()
        p = s.players[0]
        p.taisen_cards = ["01-yurina-o-n-1"]
        f0 = p.fuujin
        release_taisen(s, 0, "01-yurina-o-n-1", gain_gauge=True,
                       agent=self._GBot(seed=1))
        assert p.fuujin == f0 + 1
        assert "01-yurina-o-n-1" not in p.taisen_cards

    def test_raira_own_card_not_taisen(self):
        """라이라 자기 여신 카드는 대전 상태가 되지 않는다."""
        s = self._deck(seed=2)
        p = s.players[0]
        s.distance = 3
        p.hand = ["12-raira-o-n-1"]
        rebalance(s)
        n0 = len(p.taisen_cards)
        use_card(s, 0, "hand", "12-raira-o-n-1", self._GBot(seed=1), RNG)
        assert len(p.taisen_cards) == n0

    def test_non_raira_player_no_taisen(self):
        """라이라를 깃들이지 않은 플레이어는 대전 시스템 미적용."""
        from setup import new_game_2v2
        s = new_game_2v2(("yurina", "saine"), ("himika", "tokoyo"), seed=3)
        p = s.players[0]
        s.distance = 3
        p.hand = ["01-yurina-o-n-1"]
        p.aura = 3
        s.players[1].aura = 5
        rebalance(s)
        use_card(s, 0, "hand", "01-yurina-o-n-1", self._GBot(seed=1), RNG)
        assert len(p.taisen_cards) == 0


# ═══════════════════════════════════════════
# AI 2여신 대응 (평가함수 + 봇)
# ═══════════════════════════════════════════
class TestAI2v2:

    def test_evaluate_handles_2v2(self):
        """평가함수가 2여신 상태에서 오류 없이 점수 반환."""
        from setup import new_game_2v2
        from ai.heuristic import evaluate
        s = new_game_2v2(("raira", "yurina"), ("megumi", "thallya"), seed=1)
        score = evaluate(s, 0)
        assert isinstance(score, float)

    def test_resource_score_reflects_gauge(self):
        """평가함수가 라이라 게이지를 자원으로 반영."""
        from setup import new_game_2v2
        from ai.heuristic import _resource_score
        s = new_game_2v2(("raira", "yurina"), ("saine", "himika"), seed=1)
        base = _resource_score(s, 0)
        s.players[0].fuujin = 5
        s.players[0].raijin = 5
        raised = _resource_score(s, 0)
        assert raised > base

    def test_resource_score_reflects_harmony(self):
        """평가함수가 탈리야 조화결정을 자원으로 반영."""
        from setup import new_game_2v2
        from ai.heuristic import _resource_score
        s = new_game_2v2(("thallya", "yurina"), ("saine", "himika"), seed=1)
        base = _resource_score(s, 0)
        s.players[0].machine_harmony = 0
        low = _resource_score(s, 0)
        assert base > low   # 조화결정 5개 > 0개

    def test_heuristic_bot_runs_2v2(self):
        """HeuristicBot이 2여신 게임을 완주."""
        import random
        from setup import new_game_2v2
        from turn import play_turn
        from ai.heuristic import HeuristicBot
        rng = random.Random(1)
        s = new_game_2v2(("raira", "chikage"), ("shinra", "kururu"), seed=1)
        for _ in range(300):
            play_turn(s, rng, HeuristicBot(seed=1))
            if s.is_over():
                break
        assert s.is_over() or s.turn_count > 1


# ═══════════════════════════════════════════
# 안전구축 덱 선택 정책 (2여신)
# ═══════════════════════════════════════════
class TestDeckBuild:

    def test_pick_deck_counts_and_min(self):
        """덱 선택: 통상7/비장3, 각 여신 통상 최소 2장 확보."""
        from ai.deckbuild import pick_deck
        from setup import CARD_DB
        n, s = pick_deck("raira", "megumi")
        assert len(n) == 7 and len(s) == 3
        na = sum(1 for c in n if CARD_DB[c]["megami"] == "raira")
        nb = sum(1 for c in n if CARD_DB[c]["megami"] == "megumi")
        assert na >= 2 and nb >= 2

    def test_score_prefers_high_damage(self):
        """카드 점수: 고데미지 공격이 저데미지보다 높게 평가."""
        from ai.deckbuild import score_card
        # 거합 4/3 vs 자루치기 2/1 (둘 다 yurina)
        geohap = None
        jaru = None
        from setup import CARD_DB
        for k, c in CARD_DB.items():
            if c.get("megami") == "yurina" and c["name_ko"] == "거합":
                geohap = k
            if c.get("megami") == "yurina" and c["name_ko"] == "자루치기":
                jaru = k
        if geohap and jaru:
            assert score_card(geohap, ["yurina"]) > score_card(jaru, ["yurina"])

    def test_smart_deck_default_in_game(self):
        """new_game_2v2 기본값이 스마트 덱을 사용 (거합 포함 확인)."""
        from setup import new_game_2v2, CARD_DB
        s = new_game_2v2(("yurina", "saine"), ("himika", "tokoyo"), seed=1)
        deck_names = [CARD_DB[c]["name_ko"]
                      for c in s.players[0].deck + s.players[0].hand]
        # 거합(4/3 최고 데미지)은 스마트 선택에서 반드시 포함
        assert "거합" in deck_names


# ═══════════════════════════════════════════
# 다른 여신 카드 의존 효과 (2여신 정밀 검증)
# ═══════════════════════════════════════════
class TestCrossMegamiEffects:

    def _bot(self):
        class B(ScriptBot):
            def choose_count(self, s, p, mx, t): return 0
            def choose_dedication(self, s, p, need): return min(need, s.dust)
        return B(seed=1)

    def test_ggeopjil_growth_on_other_enhance(self):
        """껍질치기: 메구미가 다른 여신 부여를 쓰면 생육2 (씨앗 2개 얹힘)."""
        from setup import new_game_2v2, CARD_DB
        s = new_game_2v2(("megumi", "saine"), ("yurina", "himika"), seed=1)
        p0 = s.players[0]
        kid = None
        saine_enh = None
        for k, c in CARD_DB.items():
            if c.get("megami") == "megumi" and "껍질" in c["name_ko"]:
                kid = k
            if c.get("megami") == "saine" and c["type"] == "enhance" \
                    and not c.get("extra") and saine_enh is None:
                saine_enh = k
        s.distance = 4
        s.players[1].aura = 5
        p0.soil_sprouted = 3
        p0.soil_unsprouted = 2
        p0.hand = [kid, saine_enh]
        rebalance(s)
        use_card(s, 0, "hand", kid, self._bot(), RNG)
        assert s._ggeopjil_growth.get(0) is True
        use_card(s, 0, "hand", saine_enh, self._bot(), RNG)
        assert s.players[0].enhancements[-1].seeds == 2
        s.check_conservation()

    def test_dual_action_uses_discard_attack(self):
        """Dual Action: 버림패의 다른 여신 공격을 사용."""
        from setup import new_game_2v2
        s = new_game_2v2(("thallya", "yurina"), ("saine", "himika"), seed=1)
        p0 = s.players[0]
        s.distance = 4
        s.players[1].aura = 5
        p0.machine_harmony = 3
        p0.hand = ["11-thallya-o-n-5"]
        p0.discard = ["01-yurina-o-n-1"]   # 참 3-4
        rebalance(s)
        a0 = s.players[1].aura
        b = self._bot(); b.damage = "aura"
        use_card(s, 0, "hand", "11-thallya-o-n-5", b, RNG, as_fullpower=True)
        assert a0 - s.players[1].aura == 4   # Dual 1 + 참 3


# ═══════════════════════════════════════════
# 코드 감사: 재기 / 대응 무효화 (사각지대 검증)
# ═══════════════════════════════════════════
class TestSaikiAndReactions:

    def _react_bot(self, react=None):
        class B(ScriptBot):
            def __init__(s, **k):
                super().__init__(**k)
                s._rc = react
            def choose_reaction(s, state, pidx, options, atk):
                for o in options:
                    if o[1] == s._rc:
                        return o
                return None
        return B(seed=1)

    def _find(self, megami, part):
        from setup import CARD_DB
        for k, c in CARD_DB.items():
            if isinstance(c, dict) and c.get("megami") == megami \
                    and part in c["name_ko"]:
                return k

    def test_end_phase_saiki_gauge(self):
        """뇌라풍신조: 풍신 4+ 종료 재기, 3이면 안 됨."""
        from cards import check_saiki_end_phase
        cid = self._find("raira", "뇌라풍신조")
        for fuujin, expect in ((4, True), (3, False)):
            s = fresh("raira", "yurina", 3)
            s.players[0].used_specials = [cid]
            s.players[0].specials = []
            s.players[0].fuujin = fuujin
            check_saiki_end_phase(s, 0)
            assert (cid in s.players[0].specials) is expect

    def test_end_phase_saiki_seed(self):
        """인과율의 뿌리: 발아 씨앗 0이면 종료 재기."""
        from cards import check_saiki_end_phase
        cid = self._find("megumi", "인과율")
        s = fresh("megumi", "yurina", 3)
        s.players[0].used_specials = [cid]
        s.players[0].specials = []
        s.players[0].soil_sprouted = 0
        check_saiki_end_phase(s, 0)
        assert cid in s.players[0].specials

    def test_eternal_flower_cancels_special(self):
        """영원한 꽃: 비장 공격도 무효화 (non_special=False)."""
        from setup import new_game, CARD_DB
        from combat import perform_card_attack
        s = new_game("yurina", "tokoyo", seed=1, first=0)
        s.distance = 3
        rc = self._find("tokoyo", "영원한")
        s.players[1].hand = [rc]
        s.players[1].aura = 4
        s.players[1].life = 10
        rebalance(s)
        sid = self._find("yurina", "달그림자")
        a0 = s.players[1].aura
        perform_card_attack(s, 0, {3}, 3, 1, self._react_bot(rc),
                            RNG, source_card=sid, is_special=True)
        assert s.players[1].aura == a0   # 무효화

    def test_elegant_strike_only_non_special(self):
        """우아한 타격: 경지 시 통상공격만 무효, 비장은 못 막음."""
        from setup import new_game
        from combat import perform_card_attack
        rc = self._find("tokoyo", "우아한")
        sid = self._find("yurina", "달그림자")
        # 통상 공격 → 무효
        s = new_game("yurina", "tokoyo", seed=1, first=0)
        s.distance = 3
        s.players[1].hand = [rc]
        s.players[1].aura = 4
        s.players[1].vigor = 2   # 경지
        rebalance(s)
        a0 = s.players[1].aura
        perform_card_attack(s, 0, {3}, 3, 1, self._react_bot(rc),
                            RNG, source_card="01-yurina-o-n-1")
        assert s.players[1].aura == a0   # 통상 무효

        # 비장 공격 → 무효 안 됨
        s = new_game("yurina", "tokoyo", seed=1, first=0)
        s.distance = 3
        s.players[1].hand = [rc]
        s.players[1].aura = 4
        s.players[1].vigor = 2
        rebalance(s)
        a0 = s.players[1].aura
        perform_card_attack(s, 0, {3}, 3, 1, self._react_bot(rc),
                            RNG, source_card=sid, is_special=True)
        assert s.players[1].aura < a0   # 비장은 데미지 들어감


# ═══════════════════════════════════════════
# Gemini 에이전트 (LLM 플레이어)
# ═══════════════════════════════════════════
class TestGeminiAgent:

    class _MockModel:
        def __init__(self, answer):
            self.answer = answer

        def generate_content(self, prompt, generation_config=None):
            class R:
                pass
            r = R()
            r.text = self.answer
            return r

    def test_parse_index(self):
        """응답 텍스트에서 유효한 행동 번호만 파싱."""
        from ai.gemini_agent import GeminiBot
        assert GeminiBot._parse_index("2", 5) == 2
        assert GeminiBot._parse_index("답: 3", 5) == 3
        assert GeminiBot._parse_index("나는 4번", 5) == 4
        assert GeminiBot._parse_index("999", 5) is None   # 범위 밖
        assert GeminiBot._parse_index("-1", 5) is None    # 음수
        assert GeminiBot._parse_index("숫자없음", 5) is None

    def test_fallback_without_api(self):
        """API 키 없으면 폴백 모드로 게임 완주 (휴리스틱)."""
        import random
        from setup import new_game_2v2
        from turn import play_turn
        from ai.gemini_agent import GeminiBot
        bot = GeminiBot(seed=1)
        assert bot.stats()["init_error"] is not None   # API 미설정
        rng = random.Random(1)
        s = new_game_2v2(("yurina", "saine"), ("himika", "tokoyo"), seed=1)
        for _ in range(300):
            play_turn(s, rng, GeminiBot(seed=1))
            if s.is_over():
                break
        assert s.is_over() or s.turn_count > 1

    def test_mock_gemini_selects_action(self):
        """모의 Gemini 응답대로 legal[idx] 선택."""
        import random
        from setup import new_game_2v2
        from cards import legal_card_uses
        from ai.gemini_agent import GeminiBot
        bot = GeminiBot(seed=1)
        bot._model = self._MockModel("1")
        s = new_game_2v2(("yurina", "saine"), ("himika", "tokoyo"), seed=1)
        s.distance = 3
        legal = [("card", x) for x in legal_card_uses(s, 0, False)]
        legal.append(("end", None))
        if len(legal) >= 2:
            choice = bot.choose_main_action(s, 0, legal, random.Random(1))
            assert choice == legal[1]
            assert bot.stats()["gemini_calls"] == 1

    def test_mock_gemini_bad_response_fallbacks(self):
        """이상 응답이면 폴백."""
        import random
        from setup import new_game_2v2
        from cards import legal_card_uses
        from ai.gemini_agent import GeminiBot
        bot = GeminiBot(seed=1)
        bot._model = self._MockModel("설명만 있고 숫자 없음")
        s = new_game_2v2(("yurina", "saine"), ("himika", "tokoyo"), seed=1)
        s.distance = 3
        legal = [("card", x) for x in legal_card_uses(s, 0, False)]
        legal.append(("end", None))
        bot.choose_main_action(s, 0, legal, random.Random(1))
        assert bot.stats()["fallbacks"] >= 1


# ═══════════════════════════════════════════
# Gemini 세부 선택 위임 + 전술 힌트 (강화)
# ═══════════════════════════════════════════
class TestGeminiEnhanced:

    class _MockModel:
        def __init__(self, answer):
            self.answer = answer

        def generate_content(self, prompt, generation_config=None):
            class R:
                pass
            r = R()
            r.text = self.answer
            return r

    def test_gemini_damage_type_choice(self):
        """Gemini가 데미지 타입(오라/라이프)을 선택."""
        from setup import new_game_2v2
        from ai.gemini_agent import GeminiBot
        bot = GeminiBot(seed=1)
        s = new_game_2v2(("yurina", "saine"), ("himika", "tokoyo"), seed=1)
        bot._model = self._MockModel("1")
        assert bot.choose_damage_type(s, 0, 3, 1) == "life"
        bot._model = self._MockModel("0")
        assert bot.choose_damage_type(s, 0, 3, 1) == "aura"

    def test_gemini_reaction_choice(self):
        """Gemini가 대응 카드 사용 여부를 선택."""
        from setup import new_game_2v2, CARD_DB
        from ai.gemini_agent import GeminiBot
        bot = GeminiBot(seed=1)
        rc = None
        for k, c in CARD_DB.items():
            if isinstance(c, dict) and c.get("megami") == "tokoyo" \
                    and "영원한" in c["name_ko"]:
                rc = k
        s = new_game_2v2(("tokoyo", "yurina"), ("himika", "saine"), seed=1)
        s.players[0].hand = [rc]
        rebalance(s)

        class FakeAtk:
            aura = 3
            life = 1
            is_special = False
            flags = set()
            cancelled = False
        options = [("hand", rc)]
        bot._model = self._MockModel("0")
        assert bot.choose_reaction(s, 0, options, FakeAtk()) == options[0]
        bot._model = self._MockModel("1")   # 대응 안 함
        assert bot.choose_reaction(s, 0, options, FakeAtk()) is None

    def test_tactical_hints_present(self):
        """상태 요약에 전술 힌트(명중 공격/처치 기회)가 포함."""
        from setup import new_game_2v2
        from ai.state_text import render_state
        s = new_game_2v2(("yurina", "saine"), ("himika", "tokoyo"), seed=1)
        s.distance = 3
        txt = render_state(s, 0)
        assert "전술 힌트" in txt
        assert "명중하는 내 공격" in txt

    def test_tactical_hint_lethal(self):
        """상대 오라0+라이프 낮을 때 처치 기회 힌트."""
        from setup import new_game_2v2
        from ai.state_text import render_state
        s = new_game_2v2(("yurina", "saine"), ("himika", "tokoyo"), seed=1)
        s.distance = 3
        s.players[1].aura = 0
        s.players[1].life = 2
        rebalance(s)
        txt = render_state(s, 0)
        assert "처치 기회" in txt


# ═══════════════════════════════════════════
# 학습 데이터 파이프라인 (B-1)
# ═══════════════════════════════════════════
class TestLearningPipeline:

    def test_feature_dim_fixed(self):
        """특징 벡터는 여신 조합과 무관하게 고정 차원."""
        from ai.features import encode_state, feature_dim
        from setup import new_game, new_game_2v2
        dim = feature_dim()
        s1 = new_game("yurina", "saine", seed=1)
        s2 = new_game_2v2(("raira", "thallya"), ("megumi", "shinra"), seed=1)
        assert len(encode_state(s1, 0)) == dim
        assert len(encode_state(s2, 0)) == dim

    def test_feature_values_normalized(self):
        """특징 값은 [0, 1.2] 범위로 클립."""
        from ai.features import encode_state
        from setup import new_game_2v2
        s = new_game_2v2(("raira", "thallya"), ("yurina", "saine"), seed=1)
        s.players[0].fuujin = 20
        s.players[0].machine_harmony = 5
        v = encode_state(s, 0)
        assert all(0.0 <= x <= 1.2 for x in v)

    def test_feature_reflects_resources(self):
        """자원 특징이 여신 자원을 반영."""
        from ai.features import encode_state, FEATURE_NAMES, feature_dim
        from setup import new_game_2v2
        feature_dim()
        s = new_game_2v2(("raira", "saine"), ("yurina", "himika"), seed=1)
        s.players[0].fuujin = 10
        v = encode_state(s, 0)
        fi = FEATURE_NAMES.index("my_fuujin")
        assert abs(v[fi] - 0.5) < 0.01   # 10/20

    def test_selfplay_generates_samples(self):
        """자기대국이 (x, y) 샘플을 생성."""
        from ai.selfplay import generate
        samples = generate(4, bot_name="heuristic", two_megami=True,
                           verbose=False)
        assert len(samples) > 0
        s0 = samples[0]
        assert "x" in s0 and "y" in s0
        assert s0["y"] in (0.0, 0.5, 1.0)
        from ai.features import feature_dim
        assert len(s0["x"]) == feature_dim()

    def test_value_model_learns(self):
        """가치망이 유리한 상황을 불리한 상황보다 높게 예측."""
        import numpy as np
        from ai.train_value import ValueModel
        from ai.features import encode_state, feature_dim
        from setup import new_game_2v2
        from constants import TOTAL_TOKENS
        from ai.selfplay import generate
        samples = generate(40, bot_name="heuristic", two_megami=True,
                           verbose=False)
        X = np.array([s["x"] for s in samples], dtype=np.float32)
        Y = np.array([s["y"] for s in samples], dtype=np.float32)
        m = ValueModel(feature_dim(), kind="mlp", hidden=16)
        m.train(X, Y, X, Y, epochs=30, lr=0.1, verbose=False)

        def winrate(my_life, opp_life):
            s = new_game_2v2(("yurina", "saine"), ("himika", "tokoyo"),
                             seed=1)
            s.players[0].life = my_life
            s.players[1].life = opp_life
            s.dust = TOTAL_TOKENS - s.distance \
                - sum(p.token_total() for p in s.players)
            return m.predict(np.array([encode_state(s, 0)]))[0]

        # 상대적 비교: 유리한 쪽이 불리한 쪽보다 높은 승률 예측
        p_good = winrate(10, 1)
        p_bad = winrate(1, 10)
        assert p_good > p_bad   # 학습이 유불리 방향을 잡았는지


# ═══════════════════════════════════════════
# B-4: 가치망 봇 프로토타입 (실험적, 챔피언 아님)
# ═══════════════════════════════════════════
class TestValueNetBot:

    def test_valuenet_bot_completes_game(self):
        """ValueNetBot이 2여신 게임을 오류 없이 완주."""
        import random
        from setup import new_game_2v2
        from turn import play_turn
        from ai.valuenet_bot import ValueNetBot
        rng = random.Random(1)
        s = new_game_2v2(("yurina", "saine"), ("himika", "tokoyo"), seed=1)
        bot = ValueNetBot(seed=1)
        for _ in range(250):
            play_turn(s, rng, bot)
            if s.is_over():
                break
        assert s.is_over() or s.turn_count > 1

    def test_valuenet_predicts_extreme_life_gap(self):
        """가치망은 극단적 라이프 격차를 올바른 방향으로 예측."""
        import numpy as np
        from setup import new_game_2v2
        from ai.valuenet_bot import ValueNetBot
        from constants import TOTAL_TOKENS
        bot = ValueNetBot(seed=1)
        s = new_game_2v2(("yurina", "saine"), ("himika", "tokoyo"), seed=1)
        s.players[0].life = 10
        s.players[1].life = 1
        s.dust = TOTAL_TOKENS - s.distance \
            - sum(p.token_total() for p in s.players)
        s.check_conservation()
        v_good = bot._value(s, 0)
        s2 = new_game_2v2(("yurina", "saine"), ("himika", "tokoyo"), seed=1)
        s2.players[0].life = 1
        s2.players[1].life = 10
        s2.dust = TOTAL_TOKENS - s2.distance \
            - sum(p.token_total() for p in s2.players)
        s2.check_conservation()
        v_bad = bot._value(s2, 0)
        assert v_good > v_bad
