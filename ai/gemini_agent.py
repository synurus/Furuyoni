"""
Gemini LLM 에이전트.

게임 상태를 자연어로 요약해 Gemini API에 보내고, 반환된 수를 파싱한다.
- API 키는 환경변수 GOOGLE_API_KEY 또는 생성자 인자로 제공.
- API 미설정/실패/이상응답 시 HeuristicBot으로 폴백 (게임이 멈추지 않음).
- google-generativeai 패키지가 없으면 폴백 모드로만 동작.

사용 예:
    from ai.gemini_agent import GeminiBot
    bot = GeminiBot(api_key="...", model="gemini-2.0-flash")
    # 이후 일반 봇처럼 play_turn에 전달

주의: 무료 등급은 분당 요청 제한이 있어 대량 self-play엔 부적합.
      대국 관전/해설/소수 게임 용도로 설계.
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))

from ai.heuristic import HeuristicBot           # noqa: E402
from ai.state_text import (render_state,         # noqa: E402
                           render_legal_actions,
                           _describe_action)


SYSTEM_RULES = """당신은 2인 카드 게임 '후루요니'(벚꽃 내리는 시대에 결투를)의 전문 플레이어입니다.

핵심 규칙:
- 목표: 상대의 라이프를 0으로 만들거나, 상대가 패산이 빈 상태에서 재구성하게 만들면 승리.
- 오라(방어막)와 라이프(체력), 플레어(비장 자원), 집중력을 가집니다.
- 공격은 '거리 X-Y'가 현재 간격에 맞아야 명중하며 '오라뎀/라이프뎀'을 줍니다.
  상대는 오라로 오라뎀을 막고, 오라가 부족하면 라이프로 받습니다.
- 기본동작: 전진(간격-1), 후퇴(간격+1), 집중(오라→플레어), 숙려(집중력+1, 손패 1장 덮기).

전략 원칙:
1. 간격 관리가 핵심입니다. 내 강한 공격이 명중하는 간격으로 맞추고,
   상대의 강한 공격 거리는 피하세요.
2. 상대 오라를 먼저 깎은 뒤 라이프를 노리세요. 오라가 있으면 라이프뎀도 오라로 막힙니다.
3. 상대 오라가 0일 때가 라이프를 깎을 기회입니다.
4. 플레어를 모으면 강력한 비장패를 쓸 수 있습니다 (집중 동작으로 오라→플레어).
5. 자원형 여신(라이라 게이지, 탈리야 조화결정, 메구미 씨앗 등)은
   그 자원을 모으고 활용하는 흐름을 만드세요.
6. 전술 힌트가 주어지면 참고하되, 장기적 관점도 고려하세요.

당신은 상황과 선택지를 받고, 가장 좋은 행동의 '번호 하나'만 답합니다."""

PROMPT_TEMPLATE = """{rules}

--- 현재 상황 ---
{state}

{actions}

가장 좋은 행동의 번호만 답하세요. 다른 설명 없이 숫자만 출력하세요.
답:"""


class GeminiBot(HeuristicBot):
    """
    Gemini API로 주요 행동(choose_main_action)을 결정하는 봇.
    세부 선택(데미지 타입, 대응 등)은 휴리스틱 정책을 그대로 사용해
    API 호출 횟수를 아낀다.
    """

    def __init__(self, api_key=None, model="gemini-2.0-flash",
                 seed=0, verbose=False, temperature=0.7, **kw):
        super().__init__(seed=seed, **kw)
        self.model_name = model
        self.verbose = verbose
        self.temperature = temperature
        self._api_key = api_key or os.environ.get("GOOGLE_API_KEY")
        self._model = None
        self._init_error = None
        self._call_count = 0
        self._fallback_count = 0
        self._setup_model()

    def _setup_model(self):
        """google-generativeai 초기화. 실패해도 폴백으로 동작."""
        if not self._api_key:
            self._init_error = "API 키 없음 (GOOGLE_API_KEY 미설정)"
            return
        try:
            import google.generativeai as genai
            genai.configure(api_key=self._api_key)
            self._model = genai.GenerativeModel(
                self.model_name,
                system_instruction=SYSTEM_RULES,
            )
        except ImportError:
            self._init_error = "google-generativeai 패키지 미설치"
        except Exception as e:
            self._init_error = f"모델 초기화 실패: {e}"

    # ─── 주 행동만 LLM에게, 나머지는 휴리스틱 ───
    def choose_main_action(self, state, pidx, legal, rng):
        if self._model is None or not legal:
            return self._fallback_main(state, pidx, legal, rng)
        # 선택지 1개면 API 낭비 안 함
        if len(legal) == 1:
            return legal[0]
        try:
            idx = self._ask_gemini(state, pidx, legal)
            if idx is not None and 0 <= idx < len(legal):
                self._call_count += 1
                if self.verbose:
                    kind, arg = legal[idx]
                    print(f"  [Gemini] 선택 {idx}: "
                          f"{_describe_action(state, pidx, kind, arg)}")
                return legal[idx]
        except Exception as e:
            if self.verbose:
                print(f"  [Gemini] 호출 실패 → 폴백: {e}")
        return self._fallback_main(state, pidx, legal, rng)

    def _fallback_main(self, state, pidx, legal, rng):
        self._fallback_count += 1
        return super().choose_main_action(state, pidx, legal, rng)

    # ─── 세부 선택도 Gemini에게 (전략적으로 중요한 것만) ───
    def choose_damage_type(self, state, target_idx, aura_dmg, life_dmg):
        """오라로 막을지 라이프로 받을지 — 방어의 핵심 선택."""
        if self._model is None:
            return super().choose_damage_type(state, target_idx,
                                               aura_dmg, life_dmg)
        me = target_idx   # 데미지를 받는 쪽이 결정
        question = (f"상대의 공격을 받습니다. 오라뎀 {aura_dmg}, "
                    f"라이프뎀 {life_dmg}.\n"
                    f"오라로 막으면 오라가 줄고, 라이프로 받으면 라이프가 줍니다.\n"
                    f"라이프는 승패에 직결되니 보통 오라로 막는 게 좋지만,\n"
                    f"오라를 아껴 다음 공격 방어나 집중에 쓸 수도 있습니다.")
        opts = ["오라로 막기 (aura)", "라이프로 받기 (life)"]
        idx = self._ask_choice(state, me, question, opts)
        if idx == 1:
            return "life"
        if idx == 0:
            return "aura"
        return super().choose_damage_type(state, target_idx,
                                          aura_dmg, life_dmg)

    def choose_reaction(self, state, pidx, options, atk):
        """상대 공격에 대응 카드를 쓸지 — 게임을 바꾸는 선택."""
        if self._model is None or not options:
            return super().choose_reaction(state, pidx, options, atk)
        from ai.state_text import _card_line
        a = atk.aura if atk.aura is not None else "-"
        l = atk.life if atk.life is not None else "-"
        question = (f"상대의 공격(오라뎀 {a}/라이프뎀 {l})을 받는 중입니다.\n"
                    f"대응 카드를 사용하면 공격을 무효화하거나 약화할 수 있습니다.\n"
                    f"대응 카드는 소모되니, 이 공격이 위협적일 때 쓰는 게 좋습니다.")
        opts = [f"대응: {_card_line(state, pidx, o[1])}" for o in options]
        opts.append("대응하지 않음")
        idx = self._ask_choice(state, pidx, question, opts)
        if idx is not None and idx < len(options):
            return options[idx]
        return None   # 대응 안 함

    def _ask_choice(self, state, pidx, question, options):
        """일반 선택지를 Gemini에게 묻고 인덱스 반환 (실패 시 None)."""
        if self._model is None:
            return None
        opt_text = "\n".join(f"  {i}: {o}" for i, o in enumerate(options))
        prompt = (f"--- 현재 상황 ---\n{render_state(state, pidx)}\n\n"
                  f"{question}\n\n선택지:\n{opt_text}\n\n"
                  f"가장 좋은 선택의 번호만 답하세요. 숫자만:\n답:")
        try:
            resp = self._model.generate_content(
                prompt,
                generation_config={"temperature": self.temperature,
                                   "max_output_tokens": 10})
            idx = self._parse_index((resp.text or "").strip(), len(options))
            if idx is not None:
                self._call_count += 1
                return idx
        except Exception as e:
            if self.verbose:
                print(f"  [Gemini] 세부선택 실패 → 폴백: {e}")
        self._fallback_count += 1
        return None

    def _ask_gemini(self, state, pidx, legal):
        """프롬프트 구성 → API 호출 → 번호 파싱."""
        prompt = PROMPT_TEMPLATE.format(
            rules="",  # system_instruction으로 이미 전달
            state=render_state(state, pidx),
            actions=render_legal_actions(state, pidx, legal),
        )
        resp = self._model.generate_content(
            prompt,
            generation_config={"temperature": self.temperature,
                               "max_output_tokens": 10},
        )
        text = (resp.text or "").strip()
        return self._parse_index(text, len(legal))

    @staticmethod
    def _parse_index(text, n):
        """응답 텍스트에서 첫 정수를 뽑아 유효 범위면 반환."""
        # 양의 정수만 (음수 부호 앞에 붙은 경우 제외)
        m = re.search(r"(?<![\d-])\d+", text)
        if not m:
            return None
        idx = int(m.group())
        return idx if 0 <= idx < n else None

    def stats(self):
        """API 호출/폴백 통계."""
        return {"gemini_calls": self._call_count,
                "fallbacks": self._fallback_count,
                "init_error": self._init_error}


class GeminiExplainer:
    """
    (선택) 수의 이유를 Gemini에게 설명받는 해설기.
    MCTS/휴리스틱이 고른 수 + 상태를 주고 한국어 해설을 생성.
    대량 호출이 아니라 관전용.
    """

    def __init__(self, api_key=None, model="gemini-2.0-flash"):
        self._api_key = api_key or os.environ.get("GOOGLE_API_KEY")
        self._model = None
        if self._api_key:
            try:
                import google.generativeai as genai
                genai.configure(api_key=self._api_key)
                self._model = genai.GenerativeModel(model)
            except Exception:
                self._model = None

    def explain(self, state, pidx, chosen_desc):
        if self._model is None:
            return "(해설 불가: Gemini 미설정)"
        prompt = (f"{SYSTEM_RULES}\n\n--- 상황 ---\n"
                  f"{render_state(state, pidx)}\n\n"
                  f"선택한 수: {chosen_desc}\n\n"
                  f"이 수를 선택한 이유를 2-3문장의 한국어로 설명하세요.")
        try:
            resp = self._model.generate_content(prompt)
            return (resp.text or "").strip()
        except Exception as e:
            return f"(해설 생성 실패: {e})"
