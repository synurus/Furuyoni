"""
NetMCBot: 결정화 몬테카를로 탐색 + 학습된 가치망 리프 평가.

B-4(1-ply valuenet_bot)의 실패 교훈을 반영한 설계:
- 실패 원인이었던 "가치망 단독으로 후보 수 변별"은 포기한다.
  대신 **탐색(MC 롤아웃)이 수 변별을 담당**하고, 가치망은 롤아웃이 절단된
  비종국 리프의 **국면 평가**만 맡는다 (MonteCarloBot이 evaluate()를 쓰던 자리).
- 즉 AlphaZero식 "search + learned leaf evaluator" 구조의 최소판.

MonteCarloBot을 상속해 대부분 재사용하고, 리프 평가만 갈아끼운다.
net_blend로 (가치망 : 손짠 evaluate) 리프 평가를 가중 혼합할 수 있어,
망이 아직 약한 초기 반복에서 손짠 평가를 안전망으로 섞을 수 있다.
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai.mcts import MonteCarloBot, _to_winrate       # noqa: E402
from ai.features import encode_state                  # noqa: E402
from ai.heuristic import evaluate                      # noqa: E402
from ai.train_value import ValueModel                  # noqa: E402


class NetMCBot(MonteCarloBot):
    """MC 탐색의 리프 평가를 학습된 가치망으로 대체한 봇."""

    def __init__(self, model=None, model_path="data/value_model_v2.npz",
                 net_blend=1.0, seed: int = 0, rollouts: int = 24,
                 horizon: int = 8, prune_top: int = 4, blend: float = 0.55,
                 **kw):
        super().__init__(seed=seed, rollouts=rollouts, horizon=horizon,
                         prune_top=prune_top, blend=blend, **kw)
        # model 객체를 직접 주면 재로드 없이 공유 (루프에서 유리)
        self._model = model if model is not None else ValueModel.load(model_path)
        self.net_blend = net_blend   # 1.0=순수 가치망, 0.0=순수 손짠평가

    def _leaf_eval(self, sim, me) -> float:
        """비종국 리프의 승률 추정. 가치망 + (옵션) 손짠평가 혼합."""
        x = np.array([encode_state(sim, me)], dtype=np.float32)
        net_wr = float(self._model.predict(x)[0])
        if self.net_blend >= 1.0:
            return net_wr
        hand_wr = _to_winrate(evaluate(sim, me))
        return self.net_blend * net_wr + (1.0 - self.net_blend) * hand_wr

    # MonteCarloBot이 롤아웃 절단 시 부르는 리프 평가 지점을 오버라이드
    def _outcome_or_eval(self, sim, me) -> float:
        if sim.is_over():
            return self._outcome(sim, me)
        return self._leaf_eval(sim, me)


if __name__ == "__main__":
    # 간단 동작 확인: 초기 국면 추천
    import time
    from setup import new_game
    from tokens import legal_basic_actions
    from cards import legal_card_uses

    s = new_game("yurina", "tokoyo", seed=3, first=0)
    bot = NetMCBot(seed=0, rollouts=16)
    legal = [("basic", a) for a in legal_basic_actions(s, 0)]
    legal += [("card", x) for x in legal_card_uses(s, 0, is_fullpower=False)]
    legal.append(("end", None))
    t0 = time.time()
    scored = sorted(bot.evaluate_moves(s, 0, legal), key=lambda x: -x[1])
    print(f"NetMCBot 초기국면 Top3 ({time.time()-t0:.1f}초):")
    for move, wr in scored[:3]:
        print(f"  {wr*100:5.1f}%  {move}")
