from astrbot_plugin_prompt_audit.routing_identity import build_routing_metadata


class FakeEvent:
    def __init__(
        self,
        platform,
        group_id,
        sender_id,
        sender_name="",
        group_name="",
    ):
        self.platform = platform
        self.group_id = group_id
        self.sender_id = sender_id
        self.sender_name = sender_name
        self.message_obj = type(
            "MessageObject",
            (),
            {
                "group": type(
                    "Group",
                    (),
                    {"group_name": group_name},
                )()
                if group_name
                else None,
            },
        )()

    def get_platform_name(self):
        return self.platform

    def get_group_id(self):
        return self.group_id

    def get_sender_id(self):
        return self.sender_id

    def get_sender_name(self):
        return self.sender_name


def test_group_metadata_is_stable_and_does_not_contain_raw_ids():
    event = FakeEvent(
        "aiocqhttp",
        "group-123",
        "user-456",
        sender_name="糯糯大肥鱼",
        group_name="测试群",
    )
    first = build_routing_metadata(event)
    second = build_routing_metadata(event)

    assert first == second
    assert first["scope_type"] == "group"
    assert first["actor_type"] == "group_member"
    assert first["group_hash"]
    assert first["user_hash"]
    assert first["actor_hash"]
    assert first["group_name"] == "测试群"
    assert first["user_name"] == "糯糯大肥鱼"
    assert first["group_name_source"] == "message_group"
    assert first["user_name_source"] == "event"
    assert "group-123" not in str(first)
    assert "user-456" not in str(first)


def test_private_metadata_uses_user_dimension():
    metadata = build_routing_metadata(FakeEvent("aiocqhttp", None, "user-456"))

    assert metadata["scope_type"] == "private"
    assert metadata["actor_type"] == "private_user"
    assert metadata["scope_hash"]
    assert metadata["actor_hash"] == metadata["user_hash"]
    assert not metadata["group_hash"]
    assert metadata["group_name"] == ""
    assert metadata["user_name"] == ""


def test_unknown_metadata_does_not_invent_identity():
    metadata = build_routing_metadata(FakeEvent("aiocqhttp", None, None))

    assert metadata["scope_type"] == "unknown"
    assert metadata["actor_type"] == "unknown"
    assert not metadata["scope_hash"]
    assert not metadata["actor_hash"]


def test_names_fall_back_to_message_object_and_raw_event():
    event = FakeEvent("aiocqhttp", "group-123", "user-456")
    event.get_sender_name = lambda: ""
    event.message_obj.group.group_name = ""
    event.message_obj.sender = type(
        "Sender",
        (),
        {"nickname": "消息对象昵称"},
    )()
    event.message_obj.raw_message = {
        "group_name": "原始群名",
        "sender": {"nickname": "原始昵称"},
    }

    metadata = build_routing_metadata(event)

    assert metadata["group_name"] == "原始群名"
    assert metadata["user_name"] == "消息对象昵称"
    assert metadata["group_name_source"] == "raw_event"
    assert metadata["user_name_source"] == "message_object"
