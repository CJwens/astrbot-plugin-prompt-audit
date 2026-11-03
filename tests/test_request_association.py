from astrbot_plugin_prompt_audit.request_association import RequestAssociationRegistry


def test_registry_returns_newest_context_for_a_key():
    registry = RequestAssociationRegistry(ttl_seconds=30)
    first = {"request_id": "first"}
    second = {"request_id": "second"}

    registry.register(first, ["session:one"], now=10)
    registry.register(second, ["session:one"], now=11)

    result = registry.lookup(["session:one"], now=12)
    assert result is not None
    assert result[0] is second
    assert result[1] == 1000


def test_registry_expires_old_contexts():
    registry = RequestAssociationRegistry(ttl_seconds=5)
    context = {"request_id": "old"}
    registry.register(context, ["session:one"], now=10)

    assert registry.lookup(["session:one"], now=16) is None


def test_registry_falls_back_to_any_matching_key():
    registry = RequestAssociationRegistry(ttl_seconds=30)
    context = {"request_id": "conversation"}
    registry.register(context, ["conversation:one"], now=10)

    result = registry.lookup(["session:missing", "conversation:one"], now=11)
    assert result is not None
    assert result[0] is context
