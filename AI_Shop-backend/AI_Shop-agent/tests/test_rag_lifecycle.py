from app.rag.lifecycle import (
    RagPrincipal,
    elastic_filter,
    filter_documents,
    freshness_reason,
    is_authorized,
    parse_access_policy,
)


def _doc(**metadata):
    return {"id": metadata.get("id", "d1"), "content": "规则", "metadata": metadata}


def test_legacy_document_is_public_but_explicit_acl_is_not():
    assert is_authorized({}, None)
    assert not is_authorized({"accessPolicy": "USER:alice"}, None)
    assert is_authorized({"accessPolicy": "USER:alice"}, RagPrincipal("alice", "USER"))
    assert not is_authorized({"accessPolicy": "USER:alice"}, RagPrincipal("bob", "USER"))


def test_acl_parser_rejects_wildcards_and_accepts_bounded_roles():
    assert parse_access_policy("PUBLIC,ROLE:USER") == (frozenset({"PUBLIC", "ROLE:USER"}), True)
    assert parse_access_policy('["AUTHENTICATED", "USER:alice"]')[1]
    assert parse_access_policy("*") == (frozenset(), False)
    assert parse_access_policy([]) == (frozenset(), False)


def test_filter_fails_closed_for_status_acl_and_freshness():
    now = 1_700_000_000_000
    result = filter_documents(
        [
            _doc(id="public"),
            _doc(id="draft", status="DRAFT"),
            _doc(id="private", accessPolicy="USER:alice"),
            _doc(id="expired", effectiveEnd=now - 1),
            _doc(id="future", effectiveStart=now + 1),
            _doc(id="malformed", effectiveEnd="not-a-date"),
        ],
        RagPrincipal("bob", "USER"),
        now_ms=now,
    )
    assert [doc["id"] for doc in result.documents] == ["public"]
    assert result.rejected == {
        "status_not_published": 1,
        "acl_denied": 1,
        "freshness_expired": 1,
        "freshness_not_started": 1,
        "freshness_invalid": 1,
    }


def test_freshness_accepts_seconds_and_iso_boundaries():
    now = 1_700_000_000_000
    assert freshness_reason({"effectiveStart": 1_699_999_999}, now) is None
    assert freshness_reason({"effectiveEnd": "2023-11-14T22:13:20Z"}, now) == "freshness_expired"
    assert freshness_reason({"effectiveEnd": now + 1}, now) is None


def test_elastic_filter_never_grants_private_policy_to_anonymous():
    anonymous = str(elastic_filter())
    assert "PUBLIC" in anonymous
    assert "AUTHENTICATED" not in anonymous
    user = str(elastic_filter(RagPrincipal("alice", "USER")))
    assert "USER:alice" in user
    assert "ROLE:USER" in user
