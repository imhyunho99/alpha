from alpha_server.global_model_handler import update_all_global_models

# Override to use a small subset of tickers to make the test fast
import alpha_server.global_model_handler
from alpha_server.asset_screener import get_all_tickers
alpha_server.global_model_handler.get_all_tickers = lambda: get_all_tickers()[:10]

update_all_global_models()
