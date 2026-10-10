

import pytest as _pytest


@_pytest.fixture(autouse=True)
def _clear_kb_token_cache():
    """KB 토큰은 프로세스 안에서 공유된다. 테스트끼리 토큰이 새지 않게 비운다."""
    from alpha_server.brokers import kb_broker

    kb_broker._TOKENS.clear()
    yield
    kb_broker._TOKENS.clear()


def pytest_sessionfinish(session, exitstatus):
    """Qt 위젯·작업 스레드를 파이썬 종료 전에 정리한다.

    CI(리눅스, offscreen)에서 테스트는 모두 통과한 뒤 인터프리터 종료 중 세그폴트(139)가 났다(10/9) —
    QApplication 보다 늦게 남은 위젯이 정리되며 생기는 PySide6 종료 순서 문제. 정리 후에도 CI 에선
    결과 코드를 그대로 들고 즉시 종료해 종료 단계 크래시가 결과를 덮지 않게 한다.
    """
    import gc
    import os
    import sys

    qtw = sys.modules.get("PySide6.QtWidgets")
    if qtw is not None:
        from PySide6.QtCore import QCoreApplication, QEvent, QThread

        app = qtw.QApplication.instance()
        if app is not None:
            for w in qtw.QApplication.topLevelWidgets():
                w.close()
                w.deleteLater()
            for obj in gc.get_objects():
                if isinstance(obj, QThread):
                    try:
                        obj.wait(3000)
                    except RuntimeError:
                        pass
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()
    gc.collect()
    session.config._alpha_exitstatus = int(exitstatus)


def pytest_unconfigure(config):
    """결과 요약이 찍힌 뒤. CI 에선 여기서 즉시 종료해 Qt 종료 단계 크래시가 결과를 덮지 않게."""
    import os
    import sys

    if os.getenv("CI") and hasattr(config, "_alpha_exitstatus"):
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(config._alpha_exitstatus)
