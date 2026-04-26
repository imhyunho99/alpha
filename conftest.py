"""pytest 전역 설정.

프로젝트 루트(이 파일이 위치한 디렉터리)를 sys.path 맨 앞에 삽입해
`from alpha import ...`, `from alpha_server import ...` 를 어디서 실행해도 import 가능하게 한다.
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
