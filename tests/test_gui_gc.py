"""GUI 순환 참조 수거가 메인 스레드에서만 일어나는지(10/10 버튼 이중 삭제 크래시)."""
import gc

import pytest

pytest.importorskip("PySide6")


def test_gc_runs_on_main_thread_timer():
    from PySide6.QtWidgets import QApplication

    from alpha.gui import _collect_garbage_on_main_thread

    app = QApplication.instance() or QApplication([])
    try:
        timer = _collect_garbage_on_main_thread(app)
        assert not gc.isenabled()          # 작업 스레드에서 자동으로 돌지 않는다
        assert timer.isActive() and timer.parent() is app
        timer.stop()
    finally:
        gc.enable()
