"""
후루요니 게임 상수 (룰북 4-1, 5-1 기준)
재연/스팀판 기본 4여신 v1 스코프
"""

# ─── 초기 배치 (룰북 4-1-1) ───
INITIAL_DISTANCE = 10      # 〈간격〉 초기 벚꽃결정
INITIAL_AURA = 3           # 각 플레이어 〈오라〉 초기
INITIAL_LIFE = 10          # 각 플레이어 〈라이프〉 초기
INITIAL_DUST = 0           # 〈더스트〉 초기 (나머지 결정은 여기 있지 않음 — 아래 보존식 참고)

# 벚꽃결정 총량:
#   간격 10 + (오라3 + 라이프10) x 2인 = 10 + 26 = 36
# 이 36개는 게임 내내 보존된다. (부여패 봉납/사용중 포함)
TOTAL_TOKENS = INITIAL_DISTANCE + (INITIAL_AURA + INITIAL_LIFE) * 2  # = 36

# ─── 플레이어 수치 상한 (룰북 5-1) ───
VIGOR_MAX = 2              # 집중력 상한 (5-1-2)
AURA_MAX = 5               # 오라 상한 (5-1-1, FAQ: 오라 5면 전진 불가)
HAND_LIMIT = 2             # 손패 상한 (5-1-3)

# ─── 덱 구성 (룰북 3-2) ───
NORMAL_DECK_SIZE = 7       # 통상패
SPECIAL_DECK_SIZE = 3      # 비장패
INITIAL_DRAW = 3           # 첫 손패 (4-1-1 과정 4)

# ─── 달인의 간격 기본값 (5-2-2) ───
# 룰북 5-2-2: 달인의 간격은 2. 권역 등 효과로 늘어난다.
#   전진(9-6-1): 현재간격 <= 달인의간격 이면 불가 → 간격 > 2 일 때만 전진
#   이탈(9-6-5): 현재간격 >  달인의간격 이면 불가 → 간격 <= 2 일 때만 이탈
BASE_MASTER_RANGE = 2

# ─── 초기 집중력 (4-1-1 과정 6) ───
FIRST_PLAYER_VIGOR = 0     # 활성 플레이어
SECOND_PLAYER_VIGOR = 1    # 비활성 플레이어

# ─── 벚꽃결정이 존재할 수 있는 영역 (보존식 검증용) ───
# 플레이어별 영역
PLAYER_TOKEN_ZONES = [
    "aura",       # 오라
    "life",       # 라이프
    "flare",      # 플레어
    "sealed",     # 봉인 (v1 미사용 예정이나 자리 확보)
]
# 부여패에 봉납된 결정 / 사용중인 결정도 플레이어에 귀속
# → GameState에서 enhancements 위의 토큰으로 별도 계산

# 공용 영역
SHARED_TOKEN_ZONES = [
    "distance",   # 간격
    "dust",       # 더스트
]

# ─── 승리/패배 사유 (v1) ───
class EndReason:
    LIFE_ZERO = "life_zero"                 # 라이프 0
    DECK_OUT = "deck_out"                   # 재구성 불가 (덱+버림+덮음 모두 0)
    DRAW = "draw"                           # 무승부 (양쪽 라이프 0 동시)
