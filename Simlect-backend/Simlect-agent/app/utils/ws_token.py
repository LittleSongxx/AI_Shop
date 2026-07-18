def resolve_ws_token(
    query_token: str | None,
    cookie_token: str | None = None,
    header_token: str | None = None,
) -> str | None:
    if query_token and query_token.strip():
        return query_token.strip()
    if cookie_token and cookie_token.strip():
        return cookie_token.strip()
    if header_token and header_token.strip():
        return header_token.strip()
    return None
