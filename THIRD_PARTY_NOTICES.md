# Third-party notices

Alpha 소스 코드는 MIT 라이선스(`LICENSE`)다. 다만 함께 쓰는 라이브러리는 각자의 라이선스를 따른다.

## PySide6 / Qt for Python — LGPL-3.0

GUI(`alpha/`)는 [PySide6](https://pypi.org/project/PySide6/)(Qt for Python)를 쓴다.
PySide6와 Qt는 LGPL-3.0(또는 GPL-2.0/3.0) 라이선스이며 Alpha의 MIT 라이선스가 적용되지 않는다.

- 소스: https://code.qt.io/cgit/pyside/pyside-setup.git/ · https://download.qt.io/official_releases/qt/
- 라이선스 전문: https://www.gnu.org/licenses/lgpl-3.0.html
- 배포판(.dmg/.exe)은 PySide6·Qt 라이브러리를 수정하지 않은 채 별도 동적 라이브러리로 포함한다.
  사용자는 같은 인터페이스의 다른 PySide6/Qt 빌드로 교체할 수 있다.

## 그 밖의 의존성

`requirements.txt`의 나머지 패키지(FastAPI, pandas, numpy, scikit-learn, LightGBM, PyTorch, yfinance 등)는
MIT·BSD·Apache-2.0 계열이다. 각 패키지의 라이선스는 설치된 패키지 메타데이터(`pip show <패키지>`)에서 확인할 수 있다.
