

import pytest as _pytest


@_pytest.fixture(autouse=True)
def _clear_kb_token_cache():
    """KB 토큰은 프로세스 안에서 공유된다. 테스트끼리 토큰이 새지 않게 비운다."""
    from alpha_server.brokers import kb_broker

    kb_broker._TOKENS.clear()
    yield
    kb_broker._TOKENS.clear()
