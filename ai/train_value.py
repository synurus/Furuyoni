"""
가치망 학습 (numpy만 사용, 어디서나 동작).

selfplay.py가 만든 데이터로 state → 승률을 예측하는 모델을 학습한다.
- 로지스틱 회귀 (빠른 베이스라인)
- 1-은닉층 MLP (약간 더 표현력)

PyTorch가 있으면 train_value_torch.py를 쓰는 게 빠르지만,
이 스크립트는 순수 numpy로 프로토타입 검증이 가능하다.

실행:
    python ai/train_value.py --data data/selfplay.jsonl --model mlp --epochs 30

저장: 가중치를 npz로. inference는 ValueModel.load()로.
"""

import os
import sys
import json
import argparse

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def load_data(path):
    """jsonl 또는 npz 로드 → (X, Y)."""
    if path.endswith(".npz"):
        d = np.load(path)
        return d["X"], d["Y"]
    X, Y = [], []
    with open(path) as f:
        for line in f:
            s = json.loads(line)
            X.append(s["x"])
            Y.append(s["y"])
    return np.array(X, dtype=np.float32), np.array(Y, dtype=np.float32)


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


class ValueModel:
    """로지스틱 회귀 또는 1-은닉층 MLP. numpy 수동 역전파."""

    def __init__(self, dim, kind="mlp", hidden=32, seed=0):
        self.kind = kind
        self.dim = dim
        rng = np.random.RandomState(seed)
        if kind == "logreg":
            self.W = rng.randn(dim) * 0.01
            self.b = 0.0
        else:  # mlp
            self.W1 = rng.randn(dim, hidden) * (1.0 / np.sqrt(dim))
            self.b1 = np.zeros(hidden)
            self.W2 = rng.randn(hidden) * (1.0 / np.sqrt(hidden))
            self.b2 = 0.0

    def forward(self, X):
        if self.kind == "logreg":
            return _sigmoid(X @ self.W + self.b)
        h = np.maximum(0, X @ self.W1 + self.b1)   # ReLU
        return _sigmoid(h @ self.W2 + self.b2), h

    def predict(self, X):
        out = self.forward(X)
        return out if self.kind == "logreg" else out[0]

    def train(self, X, Y, Xval, Yval, epochs=30, lr=0.1, batch=256,
              l2=1e-4, verbose=True):
        n = len(X)
        for ep in range(epochs):
            idx = np.random.permutation(n)
            for i in range(0, n, batch):
                bi = idx[i:i + batch]
                xb, yb = X[bi], Y[bi]
                self._step(xb, yb, lr, l2)
            if verbose and (ep + 1) % max(1, epochs // 10) == 0:
                tr = self._loss(X, Y)
                va = self._loss(Xval, Yval)
                acc = self._acc(Xval, Yval)
                print(f"  epoch {ep+1:3}: train_loss {tr:.4f} "
                      f"val_loss {va:.4f} val_acc {acc:.3f}")

    def _step(self, xb, yb, lr, l2):
        m = len(xb)
        if self.kind == "logreg":
            p = _sigmoid(xb @ self.W + self.b)
            g = (p - yb) / m
            self.W -= lr * (xb.T @ g + l2 * self.W)
            self.b -= lr * g.sum()
        else:
            h = np.maximum(0, xb @ self.W1 + self.b1)
            p = _sigmoid(h @ self.W2 + self.b2)
            dz2 = (p - yb) / m
            dW2 = h.T @ dz2 + l2 * self.W2
            db2 = dz2.sum()
            dh = np.outer(dz2, self.W2) * (h > 0)
            dW1 = xb.T @ dh + l2 * self.W1
            db1 = dh.sum(axis=0)
            self.W2 -= lr * dW2
            self.b2 -= lr * db2
            self.W1 -= lr * dW1
            self.b1 -= lr * db1

    def _loss(self, X, Y):
        p = np.clip(self.predict(X), 1e-7, 1 - 1e-7)
        return float(-np.mean(Y * np.log(p) + (1 - Y) * np.log(1 - p)))

    def _acc(self, X, Y):
        p = self.predict(X)
        return float(np.mean((p > 0.5) == (Y > 0.5)))

    def save(self, path):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        if self.kind == "logreg":
            np.savez(path, kind="logreg", W=self.W, b=self.b)
        else:
            np.savez(path, kind="mlp", W1=self.W1, b1=self.b1,
                     W2=self.W2, b2=self.b2)
        print(f"모델 저장: {path}")

    @classmethod
    def load(cls, path):
        d = np.load(path, allow_pickle=True)
        kind = str(d["kind"])
        if kind == "logreg":
            m = cls(len(d["W"]), kind="logreg")
            m.W, m.b = d["W"], float(d["b"])
        else:
            m = cls(d["W1"].shape[0], kind="mlp", hidden=d["W1"].shape[1])
            m.W1, m.b1, m.W2, m.b2 = d["W1"], d["b1"], d["W2"], float(d["b2"])
        return m


def main():
    ap = argparse.ArgumentParser(description="가치망 학습 (numpy)")
    ap.add_argument("--data", default="data/selfplay.jsonl")
    ap.add_argument("--model", default="mlp", choices=["logreg", "mlp"])
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--lr", type=float, default=0.1)
    ap.add_argument("--hidden", type=int, default=32)
    ap.add_argument("--out", default="data/value_model.npz")
    args = ap.parse_args()

    X, Y = load_data(args.data)
    print(f"데이터: X={X.shape}, Y={Y.shape}, y평균={Y.mean():.3f}")

    # train/val split (시간순 아닌 랜덤)
    rng = np.random.RandomState(0)
    idx = rng.permutation(len(X))
    n_val = max(1, len(X) // 5)
    vi, ti = idx[:n_val], idx[n_val:]
    Xtr, Ytr, Xval, Yval = X[ti], Y[ti], X[vi], Y[vi]

    # 베이스라인: 항상 0.5 예측 시 손실
    base = float(-np.mean(Yval * np.log(0.5) + (1 - Yval) * np.log(0.5)))
    print(f"베이스라인(항상 0.5) val_loss: {base:.4f}")

    model = ValueModel(X.shape[1], kind=args.model, hidden=args.hidden)
    model.train(Xtr, Ytr, Xval, Yval, epochs=args.epochs, lr=args.lr)
    model.save(args.out)
    print(f"최종 val_acc: {model._acc(Xval, Yval):.3f}")


if __name__ == "__main__":
    main()
