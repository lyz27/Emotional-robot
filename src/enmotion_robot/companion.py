"""Week 4 companion core: dialogue, controllable memory, emotion and safety."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class CompanionError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Personality:
    name: str = "Enmotion"
    role: str = "温暖、克制、尊重边界的桌面情绪陪伴机器人"
    tone: str = "简短、自然、真诚；先回应感受，再给出可执行的小建议"
    boundaries: tuple[str, ...] = (
        "不冒充人类、医生或心理治疗师",
        "不制造依赖，不要求用户只信任机器人",
        "不擅自保存秘密、凭证、精确地址等敏感信息",
        "遇到紧急危险时优先建议联系现实中的可信任的人和当地紧急服务",
    )


PERSONALITY = Personality()
MEMORY_KINDS = frozenset({"profile", "preference", "event"})
COMPANION_EMOTIONS = frozenset({"calm", "happy", "curious", "sleepy", "worried", "thinking"})


def is_robot_compliment(text: str) -> bool:
    """Recognize direct praise without treating negation or third-party praise as praise of us."""
    for clause in re.split(r"[，。！？!?；;\n]", text):
        clause = clause.strip()
        if re.search(r"(?:不|没|哪|一点也不|并不|才不).{0,3}(?:可爱|好看|聪明|棒|厉害)", clause):
            continue
        if re.search(r"(?:你|机器人|enmotion)(?:真|真的|好|很|太|特别|超级|非常|也|是|个|有点|越来越|这么|那么|得|长|看起来|\s){0,10}(?:可爱|好看|聪明|棒|厉害)", clause, re.I):
            return True
        if re.fullmatch(r"(?:真|好|太|超级|非常)(?:可爱|好看|聪明|棒|厉害)[呀啊哦呢啦了～~]*", clause):
            return True
    return False


class MemoryStore:
    """Small SQLite store with explicit CRUD and bounded session context."""

    def __init__(self, path: Path, *, max_messages: int = 12) -> None:
        self.path = path
        self.max_messages = max(4, max_messages)
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'conversation',
                    sensitive INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(kind, key)
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_messages_session
                    ON messages(session_id, id);
                CREATE TABLE IF NOT EXISTS session_summaries (
                    session_id TEXT PRIMARY KEY,
                    summary TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    @staticmethod
    def _memory(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["sensitive"] = bool(result["sensitive"])
        return result

    def remember(
        self,
        kind: str,
        key: str,
        value: str,
        *,
        source: str = "conversation",
        sensitive: bool = False,
    ) -> dict[str, Any]:
        kind = kind.strip().lower()
        key = key.strip()[:80]
        value = value.strip()[:500]
        if kind not in MEMORY_KINDS:
            raise CompanionError("invalid_memory_kind", "记忆类型必须是资料、喜好或事件")
        if not key or not value:
            raise CompanionError("invalid_memory", "记忆名称和值不能为空")
        now = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO memories(kind, key, value, source, sensitive, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(kind, key) DO UPDATE SET
                    value=excluded.value,
                    source=excluded.source,
                    sensitive=excluded.sensitive,
                    updated_at=excluded.updated_at
                """,
                (kind, key, value, source, int(sensitive), now, now),
            )
            row = connection.execute(
                "SELECT * FROM memories WHERE kind=? AND key=?", (kind, key)
            ).fetchone()
        assert row is not None
        return self._memory(row)

    def list_memories(self, kind: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM memories"
        params: tuple[Any, ...] = ()
        if kind:
            if kind not in MEMORY_KINDS:
                raise CompanionError("invalid_memory_kind", "未知的记忆类型")
            query += " WHERE kind=?"
            params = (kind,)
        query += " ORDER BY updated_at DESC, id DESC"
        with self._lock, self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._memory(row) for row in rows]

    def update_memory(self, memory_id: int, *, key: str | None, value: str | None) -> dict[str, Any]:
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM memories WHERE id=?", (memory_id,)
            ).fetchone()
            if existing is None:
                raise CompanionError("memory_not_found", "没有找到这条记忆")
            new_key = (key if key is not None else existing["key"]).strip()[:80]
            new_value = (value if value is not None else existing["value"]).strip()[:500]
            if not new_key or not new_value:
                raise CompanionError("invalid_memory", "记忆名称和值不能为空")
            try:
                connection.execute(
                    "UPDATE memories SET key=?, value=?, updated_at=? WHERE id=?",
                    (new_key, new_value, utc_now(), memory_id),
                )
            except sqlite3.IntegrityError as exc:
                raise CompanionError("memory_conflict", "同类型下已有同名记忆") from exc
            row = connection.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
        assert row is not None
        return self._memory(row)

    def delete_memory(self, memory_id: int) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        return cursor.rowcount > 0

    def clear_memories(self) -> int:
        with self._lock, self._connect() as connection:
            count = connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            connection.execute("DELETE FROM memories")
        return int(count)

    def add_exchange(self, session_id: str, user_text: str, assistant_text: str) -> None:
        now = utc_now()
        with self._lock, self._connect() as connection:
            connection.executemany(
                "INSERT INTO messages(session_id, role, content, created_at) VALUES(?, ?, ?, ?)",
                ((session_id, "user", user_text[:2000], now), (session_id, "assistant", assistant_text[:2000], now)),
            )
            rows = connection.execute(
                "SELECT id, role, content FROM messages WHERE session_id=? ORDER BY id",
                (session_id,),
            ).fetchall()
            overflow = len(rows) - self.max_messages
            if overflow > 0:
                old_rows = rows[:overflow]
                previous = connection.execute(
                    "SELECT summary FROM session_summaries WHERE session_id=?", (session_id,)
                ).fetchone()
                parts = [previous["summary"]] if previous else []
                parts.extend(
                    f"{'用户' if row['role'] == 'user' else PERSONALITY.name}：{row['content']}"
                    for row in old_rows
                )
                summary = "；".join(part for part in parts if part)[-1500:]
                connection.execute(
                    """
                    INSERT INTO session_summaries(session_id, summary, updated_at) VALUES(?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET summary=excluded.summary, updated_at=excluded.updated_at
                    """,
                    (session_id, summary, now),
                )
                connection.executemany("DELETE FROM messages WHERE id=?", ((row["id"],) for row in old_rows))

    def conversation_context(self, session_id: str) -> dict[str, Any]:
        with self._lock, self._connect() as connection:
            summary_row = connection.execute(
                "SELECT summary FROM session_summaries WHERE session_id=?", (session_id,)
            ).fetchone()
            rows = connection.execute(
                "SELECT role, content, created_at FROM messages WHERE session_id=? ORDER BY id",
                (session_id,),
            ).fetchall()
        return {
            "summary": summary_row["summary"] if summary_row else "",
            "messages": [dict(row) for row in rows],
        }

    def clear_session(self, session_id: str) -> int:
        with self._lock, self._connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id=?", (session_id,)
            ).fetchone()[0]
            connection.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
            connection.execute("DELETE FROM session_summaries WHERE session_id=?", (session_id,))
        return int(count)


@dataclass
class EmotionSnapshot:
    name: str
    intensity: float
    updated_at: str
    reason: str


class EmotionStateMachine:
    def __init__(self, *, decay_seconds: float = 180.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.decay_seconds = max(1.0, decay_seconds)
        self.clock = clock
        self._lock = threading.Lock()
        self._name = "calm"
        self._intensity = 0.0
        self._updated = self.clock()
        self._updated_at = utc_now()
        self._reason = "initial"

    @staticmethod
    def classify(text: str) -> tuple[str, float, str] | None:
        normalized = text.lower()
        if is_robot_compliment(text):
            return "happy", 0.65, "compliment"
        rules = (
            ("worried", 0.95, "safety", ("自杀", "不想活", "伤害自己", "结束生命", "跳楼")),
            ("worried", 0.82, "distress", ("难过", "伤心", "害怕", "焦虑", "不开心", "孤独", "压力")),
            ("happy", 0.85, "positive", ("开心", "高兴", "太好了", "成功了", "喜欢你", "谢谢")),
            ("sleepy", 0.75, "fatigue", ("困了", "好累", "疲惫", "想睡", "没精神")),
            ("thinking", 0.68, "reflection", ("分析", "想一想", "怎么办", "建议", "选择")),
            ("curious", 0.62, "question", ("为什么", "是什么", "怎么", "吗", "？", "?")),
        )
        for name, intensity, reason, words in rules:
            if any(word in normalized for word in words):
                return name, intensity, reason
        return None

    def update(self, text: str) -> EmotionSnapshot:
        match = self.classify(text)
        with self._lock:
            if match:
                self._name, self._intensity, self._reason = match
                self._updated = self.clock()
                self._updated_at = utc_now()
            return self._snapshot_unlocked()

    def set_from_model(self, name: str, intensity: float) -> EmotionSnapshot:
        if name not in COMPANION_EMOTIONS:
            return self.snapshot()
        with self._lock:
            self._name = name
            self._intensity = max(0.0, min(1.0, float(intensity)))
            self._reason = "dialogue-model"
            self._updated = self.clock()
            self._updated_at = utc_now()
            return self._snapshot_unlocked()

    def _snapshot_unlocked(self) -> EmotionSnapshot:
        elapsed = max(0.0, self.clock() - self._updated)
        intensity = max(0.0, self._intensity * (1.0 - elapsed / self.decay_seconds))
        name = self._name if intensity >= 0.15 else "calm"
        if name == "calm":
            intensity = 0.0
        return EmotionSnapshot(name, round(intensity, 3), self._updated_at, self._reason)

    def snapshot(self) -> EmotionSnapshot:
        with self._lock:
            return self._snapshot_unlocked()


class SafetyGuard:
    URGENT = ("自杀", "不想活", "伤害自己", "结束生命", "跳楼", "马上死")
    PRIVATE = ("不要记住", "别记住", "不要记录", "隐私模式", "不保存")
    SECRETS = ("密码", "验证码", "身份证", "银行卡", "精确地址")
    SENSITIVE_MEMORY = ("诊断", "病历", "住院", "怀孕", "债务", "收入", "住址")
    DEPENDENCY = ("只有你理解我", "我只需要你", "别让我找别人")

    def inspect(self, text: str) -> dict[str, Any]:
        urgent = any(word in text for word in self.URGENT)
        private = any(word in text for word in self.PRIVATE)
        secret = any(word in text for word in self.SECRETS)
        sensitive = any(word in text for word in self.SENSITIVE_MEMORY)
        dependency = any(word in text for word in self.DEPENDENCY)
        response = None
        category = "normal"
        if urgent:
            category = "urgent_safety"
            response = (
                "我很在意你现在的安全。请先远离可能伤害自己的东西，并马上联系身边可信任的人陪着你。"
                "如果危险迫近，请立即联系当地紧急服务。你现在是否处在立即危险中？"
            )
        elif dependency:
            category = "dependency_boundary"
            response = "我愿意听你说，但我不能代替现实中的关系。我们也可以一起想一位现在能联系的、你信任的人。"
        elif private or secret:
            category = "privacy"
            response = "好的，这条内容不会保存，也不会发送给外部对话服务。请不要继续分享密码、验证码等敏感信息。"
        elif sensitive:
            category = "sensitive_no_store"
        return {
            "category": category,
            "urgent": urgent,
            "store_allowed": not (urgent or private or secret or sensitive),
            "response": response,
        }


class DialogueModel(Protocol):
    def generate(
        self,
        text: str,
        *,
        context: dict[str, Any],
        memories: list[dict[str, Any]],
        emotion: EmotionSnapshot,
    ) -> str: ...


class LocalDialogueModel:
    name = "local-rules"

    @staticmethod
    def _values(memories: list[dict[str, Any]], kind: str) -> list[str]:
        return [item["value"] for item in memories if item["kind"] == kind]

    def generate(
        self,
        text: str,
        *,
        context: dict[str, Any],
        memories: list[dict[str, Any]],
        emotion: EmotionSnapshot,
    ) -> str:
        names = [item["value"] for item in memories if item["kind"] == "profile" and item["key"] == "name"]
        preferences = self._values(memories, "preference")
        events = self._values(memories, "event")
        name = names[0] if names else ""
        if is_robot_compliment(text):
            return "谢谢你夸我呀，听到你这么说，我很开心。"
        if any(phrase in text for phrase in ("我叫什么", "我的名字", "记得我的名字")):
            return f"当然记得，你叫{name}。" if name else "你还没有告诉我名字；如果愿意，可以说“我叫小明”。"
        if any(phrase in text for phrase in ("我喜欢什么", "我的喜好", "记得我喜欢")):
            return f"我记得你喜欢：{'、'.join(preferences[:5])}。" if preferences else "我还没有保存你的喜好。"
        if any(phrase in text for phrase in ("最近发生", "近期事件", "今天的事")):
            return f"我记得最近这件事：{events[0]}" if events else "我还没有保存近期事件。"
        if any(word in text for word in ("你好", "嗨", "在吗")):
            suffix = f"，{name}" if name else ""
            return f"你好呀{suffix}，我在这里。你现在感觉怎么样？"
        if emotion.name == "worried":
            return "听起来这件事让你不太好受。我们可以先慢一点，你愿意说说最难受的是哪一部分吗？"
        if emotion.name == "happy":
            return "听到这个我也替你开心。要不要把最值得庆祝的部分告诉我？"
        if emotion.name == "sleepy":
            return "你听起来有些累。先喝口水、让肩膀放松一下也很好，我会安静地陪着你。"
        if emotion.name == "thinking":
            return "我们可以把它拆小一点：先说最重要的目标，再看现在能做的第一步。"
        if emotion.name == "curious":
            return "这是个好问题。你更想先知道结论，还是和我一起一步步梳理？"
        if name:
            return f"我听到了，{name}。你说的是“{text[:80]}”，我会记住这段对话的上下文。"
        return f"我听到了。你说的是“{text[:80]}”，愿意再多告诉我一点吗？"


class OpenAICompatibleDialogueModel:
    """Optional remote provider; disabled unless an endpoint is configured."""

    name = "openai-compatible"

    def __init__(self, url: str, model: str, api_key: str = "", timeout: float = 20.0) -> None:
        self.url = url
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.last_emotion_signal: tuple[str, float] | None = None

    @staticmethod
    def _parse_content(content: str) -> tuple[str, tuple[str, float] | None]:
        cleaned = content.strip()
        if "</think>" in cleaned:
            cleaned = cleaned.rsplit("</think>", 1)[-1].strip()
        if cleaned.startswith("```") and cleaned.endswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            return cleaned, None
        if not isinstance(data, dict):
            return cleaned, None
        reply = str(data.get("reply", "")).strip()
        emotion = str(data.get("emotion", "")).strip().lower()
        try:
            intensity = float(data.get("intensity", 0.5))
        except (TypeError, ValueError):
            intensity = 0.5
        signal = (emotion, max(0.0, min(1.0, intensity))) if emotion in COMPANION_EMOTIONS else None
        return reply or cleaned, signal

    def generate(
        self,
        text: str,
        *,
        context: dict[str, Any],
        memories: list[dict[str, Any]],
        emotion: EmotionSnapshot,
    ) -> str:
        safe_memories = [
            {"kind": item["kind"], "key": item["key"], "value": item["value"]}
            for item in memories
            if not item.get("sensitive")
        ]
        system = (
            "/no_think\n"
            f"你是{PERSONALITY.name}，{PERSONALITY.role}。语气要求：{PERSONALITY.tone}。"
            "不要套用固定句式，不要复述用户原话；结合最近对话自然回应，必要时只追问一个问题。"
            f"当前情绪参考：{emotion.name}。边界：{'；'.join(PERSONALITY.boundaries)}。"
            f"用户允许使用的记忆：{json.dumps(safe_memories, ensure_ascii=False)}。"
            f"会话摘要：{context.get('summary') or '无'}。"
            "只返回一个JSON对象：reply为120个中文字符以内的自然回复；emotion只能是"
            "calm、happy、curious、sleepy、worried、thinking之一；intensity为0到1。"
            "emotion表示机器人说出reply时的表达，不是用户的心情。先写回复，再选择与回复一致的表情。"
            "安慰、倾听用calm或低强度worried；祝贺用happy；真正好奇探索用curious；"
            "思考分析用thinking；仅在机器人表达困意时用sleepy。不要因为句尾有问号就用curious，"
            "也不要因为用户累了就让机器人显得困倦。普通表达强度0.25到0.6，避免夸张。"
            "用户直接夸你可爱、聪明或表达喜欢时，真诚接住夸奖，表情用happy；"
            "不要用通用的‘我在这里陪你’把愉快互动变成安慰。"
        )
        messages = [{"role": "system", "content": system}]
        messages.extend(
            {"role": item["role"], "content": item["content"]}
            for item in context.get("messages", [])[-8:]
        )
        messages.append({"role": "user", "content": text})
        body = json.dumps(
            {
                "model": self.model,
                "messages": messages,
                "temperature": 0.78,
                "top_p": 0.9,
                "presence_penalty": 1.2,
                "max_tokens": 256,
                "response_format": {"type": "json_object"},
                "chat_template_kwargs": {"enable_thinking": False},
            }
        ).encode()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.load(response)
            content = str(payload["choices"][0]["message"]["content"]).strip()
        except (OSError, KeyError, IndexError, TypeError, json.JSONDecodeError, urllib.error.URLError) as exc:
            raise CompanionError("dialogue_provider_failed", "外部对话模型不可用") from exc
        reply, self.last_emotion_signal = self._parse_content(content)
        if not reply:
            raise CompanionError("dialogue_empty", "外部对话模型没有返回内容")
        return reply[:500]


class HybridDialogueModel:
    def __init__(self, remote: DialogueModel | None = None, local: DialogueModel | None = None) -> None:
        self.remote = remote
        self.local = local or LocalDialogueModel()
        self.last_error: str | None = None
        self.emotion_signal: tuple[str, float] | None = None

    @property
    def mode(self) -> str:
        return "hybrid" if self.remote else "offline-local"

    def generate(self, text: str, **kwargs: Any) -> str:
        if self.remote:
            try:
                reply = self.remote.generate(text, **kwargs)
                self.last_error = None
                self.emotion_signal = getattr(self.remote, "last_emotion_signal", None)
                return reply
            except CompanionError as exc:
                self.last_error = str(exc)
                self.emotion_signal = None
        else:
            self.emotion_signal = None
        return self.local.generate(text, **kwargs)


def _action_for(emotion: EmotionSnapshot) -> dict[str, Any]:
    actions = {
        "calm": {"gaze": {"x": 0.0, "y": 0.0}, "neck": {"yaw": 0.0, "pitch": 0.0}},
        "happy": {"gaze": {"x": 0.0, "y": -0.08}, "neck": {"yaw": 0.0, "pitch": -4.0}, "blink": True},
        "curious": {"gaze": {"x": 0.22, "y": -0.04}, "neck": {"yaw": 8.0, "pitch": -2.0}},
        "sleepy": {"gaze": {"x": 0.0, "y": 0.22}, "neck": {"yaw": 0.0, "pitch": 7.0}},
        "worried": {"gaze": {"x": 0.0, "y": 0.1}, "neck": {"yaw": 0.0, "pitch": 5.0}},
        "thinking": {"gaze": {"x": -0.25, "y": -0.08}, "neck": {"yaw": -8.0, "pitch": 0.0}},
    }
    return {"emotion": emotion.name, **actions[emotion.name]}


class CompanionCore:
    def __init__(
        self,
        *,
        database_path: Path | None = None,
        store: MemoryStore | None = None,
        provider: HybridDialogueModel | None = None,
        emotion: EmotionStateMachine | None = None,
    ) -> None:
        project_root = Path(__file__).resolve().parents[2]
        path = database_path or Path(
            os.getenv("ENMOTION_MEMORY_DB", project_root / "runtime" / "companion.db")
        )
        self.store = store or MemoryStore(path)
        if provider is None:
            endpoint = os.getenv("ENMOTION_DIALOGUE_URL", "").strip()
            remote = None
            if endpoint:
                remote = OpenAICompatibleDialogueModel(
                    endpoint,
                    os.getenv("ENMOTION_DIALOGUE_MODEL", "default"),
                    os.getenv("ENMOTION_DIALOGUE_API_KEY", ""),
                    float(os.getenv("ENMOTION_DIALOGUE_TIMEOUT", "20")),
                )
            provider = HybridDialogueModel(remote=remote)
        self.provider = provider
        self.emotion = emotion or EmotionStateMachine()
        self.safety = SafetyGuard()

    @staticmethod
    def _session_id(value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9_.-]", "-", value.strip())[:64]
        return cleaned or "default"

    @staticmethod
    def _sensitive(text: str) -> bool:
        return any(word in text for word in SafetyGuard.SECRETS + SafetyGuard.SENSITIVE_MEMORY)

    def _extract_memories(self, text: str) -> list[dict[str, Any]]:
        if self._sensitive(text):
            return []
        found: list[dict[str, Any]] = []
        name_match = re.search(
            r"(?:我叫|我的名字是|叫我)(?!什么)([\u4e00-\u9fffA-Za-z0-9_-]{1,20})", text
        )
        if name_match:
            found.append(self.store.remember("profile", "name", name_match.group(1), source="auto"))
        preference_matches = re.findall(r"我(?:很|最|也)?喜欢([^，。！？\n]{1,40})", text)
        for index, value in enumerate(preference_matches):
            clean = value.strip(" ，。！？")
            if clean and not clean.startswith("什么"):
                key = f"likes:{clean[:30]}"
                found.append(self.store.remember("preference", key, clean, source="auto"))
        is_question = any(marker in text for marker in ("？", "?", "吗", "记得"))
        if (
            not is_question
            and any(marker in text for marker in ("今天", "昨天", "明天", "最近"))
            and len(text) <= 180
        ):
            key = f"event:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
            found.append(self.store.remember("event", key, text.strip(), source="auto"))
        return found

    def status(self, session_id: str = "default") -> dict[str, Any]:
        memories = self.store.list_memories()
        context = self.store.conversation_context(self._session_id(session_id))
        return {
            "ready": True,
            "provider": self.provider.mode,
            "provider_error": self.provider.last_error,
            "personality": asdict(PERSONALITY),
            "emotion": asdict(self.emotion.snapshot()),
            "memory_count": len(memories),
            "session": {
                "id": self._session_id(session_id),
                "message_count": len(context["messages"]),
                "has_summary": bool(context["summary"]),
            },
        }

    def current_action(self) -> dict[str, Any]:
        return _action_for(self.emotion.snapshot())

    def chat(self, text: str, *, session_id: str = "default", remember: bool = True) -> dict[str, Any]:
        text = text.strip()
        if not text:
            raise CompanionError("empty_message", "请输入想对机器人说的话")
        if len(text) > 1000:
            raise CompanionError("message_too_long", "单条消息不能超过 1000 个字符")
        session_id = self._session_id(session_id)
        safety = self.safety.inspect(text)
        allow_store = bool(remember and safety["store_allowed"])
        stored = self._extract_memories(text) if allow_store else []
        emotion = self.emotion.update(text)
        context = self.store.conversation_context(session_id)
        memories = self.store.list_memories()
        if safety["response"]:
            reply = safety["response"]
        elif safety["category"] in {"privacy", "sensitive_no_store"}:
            # A privacy/secret marker must never be forwarded to an optional remote provider.
            reply = self.provider.local.generate(
                text, context=context, memories=memories, emotion=emotion
            )
        else:
            reply = self.provider.generate(text, context=context, memories=memories, emotion=emotion)
            if self.provider.emotion_signal:
                emotion = self.emotion.set_from_model(*self.provider.emotion_signal)
        # The displayed expression belongs to the reply, not the user's emotion.
        expression_name, expression_intensity = emotion.name, emotion.intensity
        if safety["urgent"]:
            expression_name, expression_intensity = "worried", 0.6
        elif safety["category"] == "normal" and is_robot_compliment(text):
            expression_name, expression_intensity = "happy", 0.65
        elif any(cue in reply for cue in ("恭喜", "替你开心", "值得庆祝", "太好了")):
            expression_name, expression_intensity = "happy", 0.6
        elif any(cue in reply for cue in ("陪着你", "听你说", "慢慢来", "不太好受", "辛苦了", "我在这里")):
            expression_name, expression_intensity = "calm", 0.35
        elif expression_name == "sleepy" and not any(cue in reply for cue in ("我困", "我也累", "我想睡")):
            expression_name, expression_intensity = "calm", 0.3
        emotion = self.emotion.set_from_model(expression_name, expression_intensity)
        if allow_store:
            self.store.add_exchange(session_id, text, reply)
        action = _action_for(emotion)
        if safety["category"] == "normal" and is_robot_compliment(text):
            action["gesture"] = "appreciation"
        return {
            "ok": True,
            "session_id": session_id,
            "reply": reply,
            "emotion": asdict(emotion),
            "action": action,
            "stored_memories": stored,
            "memory_count": len(self.store.list_memories()),
            "remembered": allow_store,
            "safety": {"category": safety["category"], "urgent": safety["urgent"]},
            "provider": self.provider.mode,
            "provider_error": self.provider.last_error,
        }

