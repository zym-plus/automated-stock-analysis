"""
SQLite 数据访问层（DAO）
所有历史会话均持久化于 data/copilot.db，供后续查询/恢复使用。
"""
import sqlite3
import json
from pathlib import Path
from datetime import datetime

DB_PATH = Path(__file__).parent.parent.parent / "data" / "copilot.db"

# 模块级单例连接：避免每次查询重建连接的开销
# check_same_thread=False 对本地单用户应用安全
_conn: sqlite3.Connection | None = None


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    return _conn


def init_db() -> None:
    """建表（幂等）。"""
    with _get_conn() as conn:
        # 旧表：保留向后兼容
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                module        TEXT    NOT NULL,
                created_at    TEXT    NOT NULL,
                input_data    TEXT    NOT NULL,
                generated_content TEXT,
                exported_at   TEXT,
                export_path   TEXT
            )
        """)
        # 新表：任务生命周期
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                module     TEXT    NOT NULL,
                date       TEXT    NOT NULL,
                status     TEXT    NOT NULL DEFAULT 'in_progress',
                step       INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT    NOT NULL
            )
        """)
        # 新表：任务状态快照（JSON blob）
        conn.execute("""
            CREATE TABLE IF NOT EXISTS task_state (
                task_id    INTEGER PRIMARY KEY,
                state_json TEXT    NOT NULL,
                FOREIGN KEY (task_id) REFERENCES tasks(id)
            )
        """)
        # 新表：导出记录
        conn.execute("""
            CREATE TABLE IF NOT EXISTS exports (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id    INTEGER NOT NULL,
                path       TEXT    NOT NULL,
                created_at TEXT    NOT NULL,
                FOREIGN KEY (task_id) REFERENCES tasks(id)
            )
        """)
        conn.commit()


# ──────────────────────────────────────────────────────────
# 旧版 sessions DAO（向后兼容）
# ──────────────────────────────────────────────────────────

def save_session(module: str, input_data: dict, generated_content: str = "") -> int:
    """保存一条会话，返回自增 id。"""
    with _get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO sessions
               (module, created_at, input_data, generated_content)
               VALUES (?, ?, ?, ?)""",
            (
                module,
                datetime.now().isoformat(timespec="seconds"),
                json.dumps(input_data, ensure_ascii=False),
                generated_content,
            ),
        )
        conn.commit()
        return cur.lastrowid


def update_generated(session_id: int, generated_content: str) -> None:
    with _get_conn() as conn:
        conn.execute(
            "UPDATE sessions SET generated_content=? WHERE id=?",
            (generated_content, session_id),
        )
        conn.commit()


def update_export(session_id: int, export_path: str) -> None:
    with _get_conn() as conn:
        conn.execute(
            "UPDATE sessions SET exported_at=?, export_path=? WHERE id=?",
            (datetime.now().isoformat(timespec="seconds"), export_path, session_id),
        )
        conn.commit()


def list_sessions(module: str | None = None, limit: int = 20) -> list[dict]:
    """列出历史会话（最新优先）。"""
    with _get_conn() as conn:
        conn.row_factory = sqlite3.Row
        if module:
            rows = conn.execute(
                "SELECT * FROM sessions WHERE module=? ORDER BY id DESC LIMIT ?",
                (module, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM sessions ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


# ──────────────────────────────────────────────────────────
# 新版 tasks DAO
# ──────────────────────────────────────────────────────────

def create_task(module: str) -> int:
    """新建任务记录，返回 id。"""
    now = datetime.now().isoformat(timespec="seconds")
    with _get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO tasks (module, date, status, step, updated_at) VALUES (?, ?, 'in_progress', 1, ?)",
            (module, now[:10], now),
        )
        conn.commit()
        return cur.lastrowid


def update_task_step(task_id: int, step: int) -> None:
    """更新任务当前步骤及更新时间。"""
    with _get_conn() as conn:
        conn.execute(
            "UPDATE tasks SET step=?, updated_at=? WHERE id=?",
            (step, datetime.now().isoformat(timespec="seconds"), task_id),
        )
        conn.commit()


def set_task_status(task_id: int, status: str) -> None:
    """status: 'in_progress' | 'completed' | 'abandoned'"""
    with _get_conn() as conn:
        conn.execute(
            "UPDATE tasks SET status=?, updated_at=? WHERE id=?",
            (status, datetime.now().isoformat(timespec="seconds"), task_id),
        )
        conn.commit()


def abandon_task_if_incomplete(task_id: int) -> None:
    """仅当任务仍为 in_progress 时才标记为 abandoned，避免覆盖已完成记录。"""
    with _get_conn() as conn:
        conn.execute(
            "UPDATE tasks SET status='abandoned', updated_at=? WHERE id=? AND status='in_progress'",
            (datetime.now().isoformat(timespec="seconds"), task_id),
        )
        conn.commit()


def upsert_task_state(task_id: int, state: dict) -> None:
    """插入或更新任务状态快照（JSON）。"""
    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO task_state (task_id, state_json) VALUES (?, ?)
               ON CONFLICT(task_id) DO UPDATE SET state_json = excluded.state_json""",
            (task_id, json.dumps(state, ensure_ascii=False)),
        )
        conn.commit()


def load_task_state(task_id: int) -> dict | None:
    """读取任务状态快照，无记录返回 None。"""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT state_json FROM task_state WHERE task_id=?",
            (task_id,),
        ).fetchone()
        return json.loads(row[0]) if row else None


def find_latest_unfinished(module: str) -> dict | None:
    """返回该模块最近一条 in_progress 任务，无则返回 None。"""
    with _get_conn() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """SELECT * FROM tasks
               WHERE module = ? AND status = 'in_progress'
               ORDER BY updated_at DESC LIMIT 1""",
            (module,),
        ).fetchone()
        return dict(row) if row else None


def add_export(task_id: int, path: str) -> None:
    """记录导出文件路径。"""
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO exports (task_id, path, created_at) VALUES (?, ?, ?)",
            (task_id, path, datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
