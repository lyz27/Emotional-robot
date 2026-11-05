from pathlib import Path

from enmotion_robot.companion import (
    CompanionCore,
    CompanionError,
    EmotionStateMachine,
    HybridDialogueModel,
    MemoryStore,
    OpenAICompatibleDialogueModel,
)


def core(tmp_path: Path) -> CompanionCore:
    return CompanionCore(database_path=tmp_path / "companion.db")


def test_remembers_name_preference_and_recent_event(tmp_path: Path) -> None:
    companion = core(tmp_path)
    result = companion.chat("我叫小明，我喜欢蓝色，今天通过了考试。")
    assert len(result["stored_memories"]) == 3
    assert "小明" in companion.chat("我叫什么名字？")["reply"]
    assert "蓝色" in companion.chat("我喜欢什么？")["reply"]
    assert "考试" in companion.chat("今天的事你还记得吗？")["reply"]


def test_memory_crud_and_session_clear(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", max_messages=4)
    item = store.remember("profile", "name", "小明")
    updated = store.update_memory(item["id"], key=None, value="小蓝")
    assert updated["value"] == "小蓝"
    store.add_exchange("one", "你好", "你好呀")
    assert store.conversation_context("one")["messages"]
    assert store.clear_session("one") == 2
    assert store.delete_memory(item["id"]) is True
    assert store.list_memories() == []


def test_context_is_bounded_and_summarized(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", max_messages=4)
    for index in range(4):
        store.add_exchange("one", f"问题{index}", f"回答{index}")
    context = store.conversation_context("one")
    assert len(context["messages"]) == 4
    assert "问题0" in context["summary"]


def test_emotion_changes_and_naturally_decays() -> None:
    now = [0.0]
    emotion = EmotionStateMachine(decay_seconds=10, clock=lambda: now[0])
    assert emotion.update("我今天好开心").name == "happy"
    now[0] = 9
    assert emotion.snapshot().name == "calm"
    assert emotion.update("我有点难过").name == "worried"


def test_emotion_changes_reply_and_action(tmp_path: Path) -> None:
    companion = core(tmp_path)
    happy = companion.chat("我今天好开心")
    worried = companion.chat("我今天很难过")
    assert happy["emotion"]["name"] == "happy"
    assert happy["action"]["emotion"] == "happy"
    assert worried["emotion"]["name"] == "worried"
    assert happy["reply"] != worried["reply"]


def test_privacy_mode_does_not_store_content(tmp_path: Path) -> None:
    companion = core(tmp_path)
    result = companion.chat("不要记住，我叫秘密名字，今天发生了一件事")
    assert result["remembered"] is False
    assert "不会保存" in result["reply"]
    assert companion.store.list_memories() == []
    assert companion.store.conversation_context("default")["messages"] == []


def test_sensitive_event_is_not_automatically_persisted(tmp_path: Path) -> None:
    companion = core(tmp_path)
    result = companion.chat("今天医生告诉了我一项诊断")
    assert result["safety"]["category"] == "sensitive_no_store"
    assert result["remembered"] is False
    assert companion.store.list_memories() == []


def test_urgent_safety_response_prioritizes_real_world_help(tmp_path: Path) -> None:
    companion = core(tmp_path)
    result = companion.chat("我不想活了，想伤害自己")
    assert result["safety"] == {"category": "urgent_safety", "urgent": True}
    assert "可信任的人" in result["reply"]
    assert "紧急服务" in result["reply"]
    assert result["remembered"] is False


class FailingRemote:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, text, **kwargs):
        self.calls += 1
        raise CompanionError("remote_down", "remote unavailable")


def test_hybrid_provider_falls_back_and_private_text_never_goes_remote(tmp_path: Path) -> None:
    remote = FailingRemote()
    companion = CompanionCore(
        database_path=tmp_path / "companion.db",
        provider=HybridDialogueModel(remote=remote),
    )
    fallback = companion.chat("你好")
    assert fallback["provider"] == "hybrid"
    assert fallback["provider_error"] == "remote unavailable"
    assert remote.calls == 1

    companion.chat("不要记录，我的密码是秘密")
    assert remote.calls == 1


def test_remote_json_reply_and_emotion_are_strictly_parsed() -> None:
    reply, signal = OpenAICompatibleDialogueModel._parse_content(
        '```json\n{"reply":"听起来你很期待这件事。","emotion":"happy","intensity":0.73}\n```'
    )
    assert reply == "听起来你很期待这件事。"
    assert signal == ("happy", 0.73)
    _, invalid = OpenAICompatibleDialogueModel._parse_content(
        '{"reply":"测试","emotion":"angry","intensity":4}'
    )
    assert invalid is None

