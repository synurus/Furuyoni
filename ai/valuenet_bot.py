"""
ValueNetBot: 학습된 가치망으로 수를 선택하는 봇 (B-4 프로토타입).

HeuristicBot과 동일한 1수 시뮬레이션 구조를 쓰되, evaluate() 대신
train_value.py로 학습한 신경망의 예측(state → 승률)을 점수로 사용한다.
"손으로 짠 평가함수 vs 데이터로 학습한 평가함수"를 실측 비교하기 위한 것.
"""

import os
import sys
import random

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai.agents import RandomBot                      # noqa: E402
from ai.features import encode_state                  # noqa: E402
from ai.train_value import ValueModel                  # noqa: E402
from ai.heuristic import _SimAgent                      # noqa: E402
from cards import use_card                              # noqa: E402
from tokens import perform_basic_action                  # noqa: E402


class ValueNetBot(RandomBot):
    """1수 시뮬레이션 + 학습된 가치망 평가."""

    def __init__(self, model_path="data/value_model_v2.npz",
                 seed: int = 0, **kw):
        super().__init__(seed=seed, **kw)
        self._sim = _SimAgent(seed=seed)
        self._model = ValueModel.load(model_path)

    def _value(self, state, pidx) -> float:
        """상태를 가치망으로 평가 (승률, 0~1). 종국이면 실제 승패."""
        if state.is_over():
            if state.winner == pidx:
                return 1.0
            if state.winner == 1 - pidx:
                return 0.0
            return 0.5
        x = np.array([encode_state(state, pidx)])
        return float(self._model.predict(x)[0])

    def choose_main_action(self, state, pidx, legal, rng):
        best, best_score = ("end", None), self._value(state, pidx)
        for move in legal:
            if move[0] == "end":
                continue
            score = self._simulate(state, pidx, move)
            if score > best_score + 1e-9:
                best, best_score = move, score
        return best

    def _simulate(self, state, pidx, move) -> float:
        sim = state.clone()
        sim_rng = random.Random(0)
        try:
            if move[0] == "basic":
                p = sim.players[pidx]
                if p.vigor >= 1:
                    p.vigor -= 1
                elif p.hand:
                    p.covered.append(p.hand.pop(0))
                else:
                    return -1.0
                perform_basic_action(sim, pidx, move[1])
            elif move[0] == "card":
                src, cid = move[1]
                use_card(sim, pidx, src, cid, self._sim, sim_rng)
            elif move[0] == "release_taisen":
                from cards import release_taisen
                release_taisen(sim, pidx, move[1], gain_gauge=True,
                               agent=self._sim)
        except Exception:
            return -1.0
        return self._value(sim, pidx)
