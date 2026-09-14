"""
후루요니 게임 상태 (GameState)

핵심 설계 원칙:
- 벚꽃결정 36개는 게임 내내 총량이 보존된다. 매 상태 변화 후 이 보존식을
  검사하는 것이 버그 조기 발견의 핵심 방파제다.
- 벚꽃결정 영역: 간격/더스트(공용), 각 플레이어의 오라/라이프/플레어/봉인,
  그리고 각 플레이어 부여패 위(봉납된) 결정.
- 카드 영역: 패산/손패/버림패/덮음패/비장패/게임바깥/전개중(부여패).
"""

from dataclasses import dataclass, field
from typing import Optional
import copy

from constants import (
    INITIAL_DISTANCE, INITIAL_AURA, INITIAL_LIFE, INITIAL_DUST,
    TOTAL_TOKENS, VIGOR_MAX, HAND_LIMIT, BASE_MASTER_RANGE,
)


class TokenConservationError(Exception):
    """벚꽃결정 총량 보존식이 깨졌을 때 발생. 엔진 버그의 신호."""
    pass


@dataclass
class Enhancement:
    """전개중인 부여패 1장과 그 위에 봉납된 벚꽃결정."""
    card_id: str
    tokens: int = 0          # 이 부여패 위에 놓인(봉납된) 벚꽃결정 수
    seeds: int = 0           # 이 부여패 위 씨앗 결정 (메구미; 벚꽃결정 간주, 별개 계정)

    def clone(self) -> "Enhancement":
        return Enhancement(self.card_id, self.tokens, self.seeds)


@dataclass
class PlayerState:
    """플레이어 한 명의 상태."""
    name: str
    megamis: tuple = ()      # 깃들인 여신들 (1명=단일, 2명=쌍장요란). 빈 값이면 name 사용

    # ── 벚꽃결정 영역 ──
    aura: int = INITIAL_AURA
    life: int = INITIAL_LIFE
    flare: int = 0
    sealed: int = 0          # 봉인 (v1 미사용)
    frozen: int = 0          # 동결 토큰 (코르누). 오라 칸을 점유하나 벚꽃결정 아님
    fuujin: int = 0          # 풍신 게이지 (라이라, 상한 20)
    raijin: int = 0          # 뇌신 게이지 (라이라, 상한 20)
    extra_specials: list = field(default_factory=list)  # 추가패 (풍마초래공 획득)
    poison_pouch: list = field(default_factory=list)     # 독주머니 (치카게 전용)
    umbrella_open: bool = False   # 우산 상태 (유키히, 시작: 접힘)
    soil_sprouted: int = 0        # 토양의 발아한 씨앗 (메구미)
    soil_unsprouted: int = 0      # 토양의 발아 안 한 씨앗
    _industria_sealed: str = None # 인더스트리아 봉인 카드 (쿠루루)
    machine_harmony: int = 0      # 머신의 조화결정 (탈리야)
    burned_harmony: int = 0       # 연소됨의 조화결정
    gap_minus_harmony: int = 0    # 간격-1 토큰 (벚꽃결정에 연결, 간격 계산서 제외)
    gap_plus_harmony: int = 0     # 간격+1 토큰 (벚꽃결정 간주, 간격에 포함)
    transforms: list = field(default_factory=list)  # 머신의 TransForm 카드
    _draw_limit_next: int = None  # YAKSHA: 다음 개시 뽑기 제한
    taisen_cards: list = field(default_factory=list)  # 대전 상태 카드 id (라이라)
    scheme: str = "shinsan"       # 계략 (신라): 'shinsan'(신산)|'kimou'(귀모)|None(미준비)
    sealed_cards: list = field(default_factory=list)  # 봉인 영역 (카드 격리)

    # ── 플레이어 수치 ──
    vigor: int = 0           # 집중력
    withered: bool = False   # 위축 상태
    master_range_bonus: int = BASE_MASTER_RANGE  # 달인의 간격 (기본 2)
    _had_first_turn: bool = False  # 자신의 첫 턴을 치렀는가 (개시 기정 스킵용)

    # ── 카드 영역 (card_id 리스트) ──
    deck: list = field(default_factory=list)        # 패산 (index -1 = 맨 위)
    hand: list = field(default_factory=list)        # 손패
    discard: list = field(default_factory=list)     # 버림패
    covered: list = field(default_factory=list)     # 덮음패
    specials: list = field(default_factory=list)    # 비장패(미사용)
    used_specials: list = field(default_factory=list)  # 사용한 비장패
    out_of_game: list = field(default_factory=list) # 게임 바깥
    enhancements: list = field(default_factory=list)  # 전개중 부여패 [Enhancement]

    def token_total(self) -> int:
        """이 플레이어에 귀속된 벚꽃결정 총합 (보존식 검증용).
        부여패 위 씨앗(e.seeds)은 벚꽃결정으로 간주되나 별개 계정이므로 제외."""
        enh_tokens = sum(e.tokens for e in self.enhancements)
        return self.aura + self.life + self.flare + self.sealed + enh_tokens

    def harmony_total(self) -> int:
        """조화결정 총합 (탈리야 준비 시 5개, 별개 보존)."""
        return (self.machine_harmony + self.burned_harmony
                + self.gap_minus_harmony + self.gap_plus_harmony)

    def seed_total(self) -> int:
        """씨앗 결정 총합 (메구미 준비 시 5개, 별개 보존)."""
        return self.soil_sprouted + self.soil_unsprouted \
            + sum(e.seeds for e in self.enhancements)

    def all_sprouted(self) -> bool:
        """토양에 발아 안 한 씨앗이 없는가 (17-3)."""
        return self.soil_unsprouted == 0

    def master_range(self) -> int:
        return self.master_range_bonus

    def aura_occupied(self) -> int:
        """오라 칸 점유량 = 벚꽃결정 + 동결 토큰 (오라 상한 5와 비교)."""
        return self.aura + self.frozen

    def aura_full(self) -> bool:
        """오라에 빈칸이 없다 (결정+동결 >= 5)."""
        from constants import AURA_MAX
        return self.aura_occupied() >= AURA_MAX

    def clone(self) -> "PlayerState":
        p = PlayerState(self.name)
        p.megamis = self.megamis
        p.aura, p.life, p.flare, p.sealed = self.aura, self.life, self.flare, self.sealed
        p.frozen = self.frozen
        p.fuujin = self.fuujin
        p.raijin = self.raijin
        p.extra_specials = list(self.extra_specials)
        p.poison_pouch = list(self.poison_pouch)
        p.umbrella_open = self.umbrella_open
        p.soil_sprouted = self.soil_sprouted
        p.soil_unsprouted = self.soil_unsprouted
        p.scheme = self.scheme
        p._industria_sealed = self._industria_sealed
        p.machine_harmony = self.machine_harmony
        p.burned_harmony = self.burned_harmony
        p.gap_minus_harmony = self.gap_minus_harmony
        p.gap_plus_harmony = self.gap_plus_harmony
        p.transforms = list(self.transforms)
        p._draw_limit_next = self._draw_limit_next
        p.taisen_cards = list(self.taisen_cards)
        p.sealed_cards = list(self.sealed_cards)
        p.vigor, p.withered = self.vigor, self.withered
        p.master_range_bonus = self.master_range_bonus
        p._had_first_turn = self._had_first_turn
        p.deck = list(self.deck)
        p.hand = list(self.hand)
        p.discard = list(self.discard)
        p.covered = list(self.covered)
        p.specials = list(self.specials)
        p.used_specials = list(self.used_specials)
        p.out_of_game = list(self.out_of_game)
        p.enhancements = [e.clone() for e in self.enhancements]
        return p


@dataclass
class GameState:
    """전체 게임 상태."""
    players: list                      # [PlayerState, PlayerState]
    distance: int = INITIAL_DISTANCE   # 간격
    dust: int = INITIAL_DUST           # 더스트

    active: int = 0                    # 활성 플레이어 인덱스 (0 or 1)
    turn_count: int = 0                # 진행된 턴 수
    turn_start_distance: int = INITIAL_DISTANCE  # 턴 시작 시 간격 (8-1-1)
    phase: str = "setup"               # setup / start / main / end / over

    # ── 턴 단위 추적 (매 턴 시작 시 리셋) ──
    cards_used_this_turn: list = field(default_factory=lambda: [0, 0])  # 연화 판정
    attacks_this_turn: list = field(default_factory=lambda: [0, 0])     # 압도(첫 공격) 판정
    attacks_this_phase: list = field(default_factory=lambda: [0, 0])    # 원심 판정 (페이즈 단위)
    terminal_lock: list = field(default_factory=lambda: [False, False])
    _hagane_used_centrifugal: dict = field(default_factory=dict)  # 대중력 재기용
    _last_damage_choice: str = None   # 콘루 루얀페: 직전 데미지 선택(aura/life)
    _oboro_opp_took_aura_dmg: dict = field(default_factory=dict)  # 참격난무용
    basic_actions_this_turn: list = field(default_factory=lambda: [0, 0])  # 마비독 판정
    _no_advance_this_turn: dict = field(default_factory=dict)  # 둔술: 전진 금지
    _next_basic_no_effect: dict = field(default_factory=dict)  # 장대 찌르기
    _no_hand_limit: dict = field(default_factory=dict)  # GARUDA: 손패 상한 없음
    _ggeopjil_growth: dict = field(default_factory=dict)  # 껍질치기: 다음 다른여신 부여 생육2
    _transform_count: dict = field(default_factory=dict)  # 탈리야 변신 횟수
    # 종단: 사용한 본인에게만 적용 (FAQ) — 카드 사용/기본동작/대응 불가
    pending_buffs: list = field(default_factory=lambda: [[], []])       # 다음 공격 버프 대기열
    winner: Optional[int] = None       # 승자 인덱스 (무승부는 -1)
    end_reason: Optional[str] = None

    # ── 접근 헬퍼 ──
    @property
    def cur(self) -> PlayerState:
        """현재 활성 플레이어."""
        return self.players[self.active]

    @property
    def opp(self) -> PlayerState:
        """현재 비활성 플레이어(상대)."""
        return self.players[1 - self.active]

    def player(self, idx: int) -> PlayerState:
        return self.players[idx]

    def is_over(self) -> bool:
        return self.phase == "over"

    # ── 벚꽃결정 보존식 ──
    def token_total(self) -> int:
        return self.distance + self.dust + sum(p.token_total() for p in self.players)

    def check_conservation(self) -> None:
        """
        벚꽃결정 총량이 36으로 유지되는지 검사.
        깨졌다면 어딘가의 이동 로직에 버그가 있다는 뜻이므로 즉시 예외.
        """
        total = self.token_total()
        if total != TOTAL_TOKENS:
            breakdown = (
                f"간격={self.distance}, 더스트={self.dust}, "
                f"P0={self.players[0].token_total()}"
                f"(오라{self.players[0].aura}/라이프{self.players[0].life}/"
                f"플레어{self.players[0].flare}/봉인{self.players[0].sealed}), "
                f"P1={self.players[1].token_total()}"
                f"(오라{self.players[1].aura}/라이프{self.players[1].life}/"
                f"플레어{self.players[1].flare}/봉인{self.players[1].sealed})"
            )
            raise TokenConservationError(
                f"벚꽃결정 보존 위반: 총 {total}개 (기대값 {TOTAL_TOKENS}). {breakdown}"
            )

    def clone(self) -> "GameState":
        """
        상태 복제 (MCTS/시뮬레이션에서 필수).
        deepcopy보다 빠르도록 필드별 얕은 복사 조합.
        """
        g = GameState(
            players=[self.players[0].clone(), self.players[1].clone()],
            distance=self.distance,
            dust=self.dust,
            active=self.active,
            turn_count=self.turn_count,
            turn_start_distance=self.turn_start_distance,
            phase=self.phase,
            cards_used_this_turn=list(self.cards_used_this_turn),
            attacks_this_turn=list(self.attacks_this_turn),
            attacks_this_phase=list(self.attacks_this_phase),
            terminal_lock=list(self.terminal_lock),
            _hagane_used_centrifugal=dict(self._hagane_used_centrifugal),
            _last_damage_choice=self._last_damage_choice,
            _oboro_opp_took_aura_dmg=dict(self._oboro_opp_took_aura_dmg),
            basic_actions_this_turn=list(self.basic_actions_this_turn),
            _no_advance_this_turn=dict(self._no_advance_this_turn),
            _next_basic_no_effect=dict(self._next_basic_no_effect),
            _no_hand_limit=dict(self._no_hand_limit),
            _ggeopjil_growth=dict(self._ggeopjil_growth),
            _transform_count=dict(self._transform_count),
            pending_buffs=[
                [dict(b) for b in self.pending_buffs[0]],
                [dict(b) for b in self.pending_buffs[1]],
            ],
            winner=self.winner,
            end_reason=self.end_reason,
        )
        return g

    # ── 디버그 출력 ──
    def summary(self) -> str:
        lines = [
            f"[턴 {self.turn_count} | phase={self.phase} | 활성=P{self.active}]",
            f"  간격={self.distance}  더스트={self.dust}",
        ]
        for i, p in enumerate(self.players):
            mark = "▶" if i == self.active else " "
            enh = ", ".join(f"{e.card_id}(결정{e.tokens})" for e in p.enhancements) or "-"
            lines.append(
                f" {mark}P{i} {p.name}: 라이프{p.life} 오라{p.aura} 플레어{p.flare} "
                f"집중{p.vigor}{'(위축)' if p.withered else ''}"
            )
            lines.append(
                f"      패산{len(p.deck)} 손패{len(p.hand)} 버림{len(p.discard)} "
                f"덮음{len(p.covered)} 비장{len(p.specials)}/사용{len(p.used_specials)} "
                f"부여[{enh}]"
            )
        if self.is_over():
            w = "무승부" if self.winner == -1 else f"P{self.winner} 승리"
            lines.append(f"  === 게임 종료: {w} ({self.end_reason}) ===")
        return "\n".join(lines)
