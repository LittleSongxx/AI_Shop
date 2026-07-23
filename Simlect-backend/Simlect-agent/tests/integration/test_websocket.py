from app.utils.ws_token import resolve_ws_token


def test_resolve_ws_token_from_query():
    assert resolve_ws_token("abc123") == "abc123"

def test_resolve_ws_token_from_cookie():
    assert resolve_ws_token(None, cookie_token="cookie_token") == "cookie_token"

def test_resolve_ws_token_query_over_cookie():
    assert resolve_ws_token("query_token", cookie_token="cookie_token") == "query_token"
