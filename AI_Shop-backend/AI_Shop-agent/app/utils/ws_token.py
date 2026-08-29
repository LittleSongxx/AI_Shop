from app.auth.security import WsCredentials


def resolve_ws_credentials(
    query_token: str | None,
    cookie_token: str | None = None,
    header_token: str | None = None,
) -> WsCredentials | None:
    """Resolve a WebSocket credential while preserving its trust source.

    Query-token precedence is retained for compatibility with the legacy
    endpoint; the browser client now uses the HttpOnly cookie path.
    """

    for value, source in (
        (query_token, "query"),
        (cookie_token, "cookie"),
        (header_token, "header"),
    ):
        if value and value.strip():
            return WsCredentials(value.strip(), source)  # type: ignore[arg-type]
    return None


def resolve_ws_token(
    query_token: str | None,
    cookie_token: str | None = None,
    header_token: str | None = None,
) -> str | None:
    credentials = resolve_ws_credentials(query_token, cookie_token, header_token)
    return credentials.token if credentials else None
