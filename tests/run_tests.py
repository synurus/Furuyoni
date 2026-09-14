"""
pytest 미설치 환경용 미니 러너.
tests/ 안의 Test* 클래스의 test_* 메서드를 전부 실행한다.
로컬에 pytest가 있다면 `python3 -m pytest tests/ -v` 를 그대로 써도 된다.
"""

import sys
import os
import traceback
import importlib.util


def run_module(path):
    spec = importlib.util.spec_from_file_location("t", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    passed, failed, errors = [], [], []
    for cls_name in dir(mod):
        if not cls_name.startswith("Test"):
            continue
        cls = getattr(mod, cls_name)
        for m_name in dir(cls):
            if not m_name.startswith("test_"):
                continue
            full = f"{cls_name}.{m_name}"
            try:
                inst = cls()
                getattr(inst, m_name)()
                passed.append(full)
                print(f"  ✅ {full}")
            except AssertionError as e:
                failed.append((full, e))
                print(f"  ❌ {full}: {e}")
            except Exception as e:
                errors.append((full, e))
                print(f"  💥 {full}: {type(e).__name__}: {e}")
                traceback.print_exc()
    return passed, failed, errors


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else \
        os.path.join(os.path.dirname(__file__), "test_faq.py")
    p, f, e = run_module(target)
    print(f"\n결과: 통과 {len(p)} / 실패 {len(f)} / 에러 {len(e)}")
    sys.exit(0 if not f and not e else 1)
