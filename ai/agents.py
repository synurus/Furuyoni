"""
에이전트(봇) 인터페이스.

엔진이 선택이 필요할 때 agent에게 물어본다:
  choose_main_action(state, pidx, legal, rng) -> (kind, arg)
  choose_action_mode(state, pidx) -> 'standard' | 'fullpower'
  choose_basic_cost(state, pidx) -> 'vigor' | 'cover'
  choose_damage_type(state, target_idx, aura_dmg, life_dmg) -> 'aura' | 'life'
  choose_card_to_cover(state, pidx, hand) -> index
  choose_option(state, pidx, labels, tag) -> index      (효과 내 선택지)
  choose_free_basic_action(state, pidx, options) -> 액션명 or None
  choose_yes_no(state, pidx, tag) -> bool
  choose_dedication(state, pidx, need) -> 더스트에서 가져올 개수
  decide_reconstruct(state, pidx) -> bool
"""

import random


class RandomBot:
    """가능한 행동 중 무작위. 페이즈 무한 반복 방지용 종료 확률 보유.
    재현성을 위해 자체 시드 rng 사용 (전역 random 금지 — MCTS 필수 조건)."""

    def __init__(self, end_prob: float = 0.3, seed: int = 0):
        self.end_prob = end_prob
        self.rng = random.Random(seed)

    def choose_main_action(self, state, pidx, legal, rng: random.Random):
        non_end = [m for m in legal if m[0] != "end"]
        if not non_end or rng.random() < self.end_prob:
            return ("end", None)
        return rng.choice(non_end)

    def choose_action_mode(self, state, pidx):
        return "standard" if self.rng.random() < 0.8 else "fullpower"

    def choose_basic_cost(self, state, pidx):
        return "vigor" if state.players[pidx].vigor >= 1 else "cover"

    def choose_damage_type(self, state, target_idx, aura_dmg, life_dmg):
        from combat import effective_aura
        return "aura" if effective_aura(state, target_idx) >= aura_dmg else "life"

    def choose_card_to_cover(self, state, pidx, hand):
        return 0

    def choose_option(self, state, pidx, labels, tag):
        return self.rng.randrange(len(labels))

    def choose_free_basic_action(self, state, pidx, options):
        return self.rng.choice(options) if options else None

    def choose_yes_no(self, state, pidx, tag):
        return True

    def choose_dedication(self, state, pidx, need):
        # 봉납: 더스트 우선
        return min(need, state.dust)

    def decide_reconstruct(self, state, pidx):
        return len(state.players[pidx].deck) == 0

    def choose_scheme(self, state, pidx):
        # 신라 계략: 기본은 무작위 (신산/귀모)
        return self.rng.choice(["shinsan", "kimou"])

    def choose_gauge(self, state, pidx):
        # 라이라 대전 해제 시 올릴 게이지: 낮은 쪽을 올려 균형 (기본정책)
        p = state.players[pidx]
        return "fuujin" if p.fuujin <= p.raijin else "raijin"

    def choose_count(self, state, pidx, max_n, tag=None):
        # 개수 선택(메구미 생육 등): 기본은 가능한 최대 (자원 최대 활용)
        return max_n

    def choose_setup_use(self, state, pidx, setups):
        # 오보로 설치: 재구성 직전 덮음패 설치 카드 사용. 기본은 무작위(절반 확률).
        if setups and self.rng.random() < 0.5:
            return self.rng.choice(setups)
        return None


class AggressiveBot(RandomBot):
    """카드 사용을 우선, 없으면 전진 성향. 승부가 나는 게임을 만들기 위한 봇."""

    def choose_main_action(self, state, pidx, legal, rng):
        cards = [m for m in legal if m[0] == "card"]
        if cards:
            return rng.choice(cards)
        advances = [m for m in legal if m == ("basic", "advance")]
        if advances and rng.random() < 0.7:
            return advances[0]
        non_end = [m for m in legal if m[0] != "end"]
        if not non_end or rng.random() < 0.25:
            return ("end", None)
        return rng.choice(non_end)

    def choose_action_mode(self, state, pidx):
        return "fullpower" if self.rng.random() < 0.15 else "standard"


# RandomBot에 대응 선택 추가 (클래스 밖 몽키패치 대신 서브클래스 확장이 정석이지만,
# 기존 봇들이 모두 상속받도록 RandomBot에 직접 추가)
def _choose_reaction(self, state, pidx, options, atk):
    """대응 창: 절반 확률로 대응 (가능한 것 중 무작위)."""
    if not options:
        return None
    if self.rng.random() < 0.5:
        return self.rng.choice(options)
    return None

RandomBot.choose_reaction = _choose_reaction
