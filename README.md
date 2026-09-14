# Furuyoni AI

후루요니(Sakura Arms) 시뮬레이터와 self-play 학습 실험.

## 설정

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows PowerShell
pip install -r requirements.txt
copy .env.example .env        # 그리고 .env 에 실제 값 입력
```

## 실험 로그

`notion_log.py` 가 iteration 결과를 Notion 데이터베이스에 한 줄씩 기록한다.
연결 확인:

```bash
python notion_log.py
```
