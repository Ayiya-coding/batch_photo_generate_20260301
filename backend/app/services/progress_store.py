"""
进度持久化存储 — 替代各模块的内存字典
所有后台任务的进度统一通过此模块读写数据库
"""
import json
import logging
from typing import Optional
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.database import TaskProgress

logger = logging.getLogger(__name__)

# 内存缓存：减少高频轮询时的 DB 读取（写入时同步更新）
_cache: dict[str, dict] = {}

MAX_LOGS = 20


def _cache_key(task_type: str, task_key: str) -> str:
    return f"{task_type}:{task_key}"


def _row_to_dict(row: TaskProgress) -> dict:
    """将 DB 行转为前端可用的 dict"""
    data = {
        "status": row.status,
        "progress": row.progress,
        "total": row.total,
        "completed": row.completed,
        "failed": row.failed,
        "logs": json.loads(row.logs) if row.logs else [],
    }
    extra = json.loads(row.extra) if row.extra else {}
    if extra:
        data.update(extra)
    return data


def _default_progress() -> dict:
    return {
        "status": "not_started",
        "progress": 0,
        "total": 0,
        "completed": 0,
        "failed": 0,
        "cancel_requested": False,
        "logs": [],
    }


def get(task_type: str, task_key: str) -> dict:
    """读取进度（优先内存缓存，fallback 到 DB）"""
    ck = _cache_key(task_type, task_key)
    if ck in _cache:
        return _cache[ck]

    db = SessionLocal()
    try:
        row = db.query(TaskProgress).filter(
            TaskProgress.task_type == task_type,
            TaskProgress.task_key == task_key,
        ).first()
        if row:
            data = _row_to_dict(row)
            _cache[ck] = data
            return data
    except Exception as e:
        logger.warning(f"读取进度失败 {task_type}/{task_key}: {e}")
    finally:
        db.close()

    return _default_progress()


def is_running(task_type: str, task_key: str) -> bool:
    """检查任务是否正在运行"""
    return get(task_type, task_key).get("status") == "running"


def set(task_type: str, task_key: str, data: dict, db: Optional[Session] = None):
    """写入进度（同时更新缓存和 DB）"""
    ck = _cache_key(task_type, task_key)

    # 截断日志
    logs = data.get("logs", [])
    if len(logs) > MAX_LOGS:
        data["logs"] = logs[-MAX_LOGS:]

    _cache[ck] = data

    # 分离 extra 字段（logs 和基础字段之外的都放 extra）
    base_keys = {"status", "progress", "total", "completed", "failed", "logs"}
    extra = {k: v for k, v in data.items() if k not in base_keys}

    own_session = db is None
    if own_session:
        db = SessionLocal()

    try:
        row = db.query(TaskProgress).filter(
            TaskProgress.task_type == task_type,
            TaskProgress.task_key == task_key,
        ).first()

        if row:
            row.status = data.get("status", row.status)
            row.progress = data.get("progress", row.progress)
            row.total = data.get("total", row.total)
            row.completed = data.get("completed", row.completed)
            row.failed = data.get("failed", row.failed)
            row.logs = json.dumps(data.get("logs", []), ensure_ascii=False)
            row.extra = json.dumps(extra, ensure_ascii=False) if extra else "{}"
        else:
            row = TaskProgress(
                task_type=task_type,
                task_key=task_key,
                status=data.get("status", "not_started"),
                progress=data.get("progress", 0),
                total=data.get("total", 0),
                completed=data.get("completed", 0),
                failed=data.get("failed", 0),
                logs=json.dumps(data.get("logs", []), ensure_ascii=False),
                extra=json.dumps(extra, ensure_ascii=False) if extra else "{}",
            )
            db.add(row)

        db.commit()
    except Exception as e:
        logger.warning(f"写入进度失败 {task_type}/{task_key}: {e}")
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        if own_session:
            db.close()


def update(task_type: str, task_key: str, **kwargs):
    """增量更新进度字段"""
    current = get(task_type, task_key)
    current.update(kwargs)
    set(task_type, task_key, current)


def append_log(task_type: str, task_key: str, msg: str):
    """追加一条日志"""
    current = get(task_type, task_key)
    logs = current.get("logs", [])
    logs.append(msg)
    if len(logs) > MAX_LOGS:
        logs = logs[-MAX_LOGS:]
    current["logs"] = logs
    set(task_type, task_key, current)


def init(task_type: str, task_key: str, total: int, first_log: str, **extra) -> dict:
    """初始化一个新任务的进度"""
    data = {
        "status": "running",
        "progress": 0,
        "total": total,
        "completed": 0,
        "failed": 0,
        "cancel_requested": False,
        "logs": [first_log],
        **extra,
    }
    set(task_type, task_key, data)
    return data


def finish(task_type: str, task_key: str, completed: int, failed: int, final_log: str, **extra):
    """标记任务完成"""
    current = get(task_type, task_key)
    current.update({
        "status": "completed",
        "progress": 100,
        "completed": completed,
        "failed": failed,
        "cancel_requested": False,
        **extra,
    })
    logs = current.get("logs", [])
    logs.append(final_log)
    current["logs"] = logs
    set(task_type, task_key, current)


def cancel(task_type: str, task_key: str, completed: int, failed: int, final_log: str, **extra):
    """标记任务已中断"""
    current = get(task_type, task_key)
    total = int(current.get("total") or 0)
    done = int(completed or 0) + int(failed or 0)
    progress = int(done / total * 100) if total > 0 else int(current.get("progress") or 0)
    current.update({
        "status": "cancelled",
        "progress": progress,
        "completed": completed,
        "failed": failed,
        "cancel_requested": False,
        **extra,
    })
    logs = current.get("logs", [])
    logs.append(final_log)
    current["logs"] = logs
    set(task_type, task_key, current)


def request_cancel(task_type: str, task_key: str, reason: str = "收到中断请求") -> bool:
    """请求中断运行中的任务"""
    current = get(task_type, task_key)
    status = current.get("status")
    if status not in ("running", "cancelling"):
        return False

    if current.get("cancel_requested") and status == "cancelling":
        return True

    current["cancel_requested"] = True
    current["status"] = "cancelling"
    logs = current.get("logs", [])
    logs.append(reason)
    current["logs"] = logs
    set(task_type, task_key, current)
    return True


def is_cancel_requested(task_type: str, task_key: str) -> bool:
    """是否已收到中断请求"""
    return bool(get(task_type, task_key).get("cancel_requested"))


def clear_cancel(task_type: str, task_key: str):
    """清理中断标记（启动新任务前调用）"""
    current = get(task_type, task_key)
    if not current.get("cancel_requested") and current.get("status") != "cancelling":
        return
    current["cancel_requested"] = False
    if current.get("status") == "cancelling":
        current["status"] = "running"
    set(task_type, task_key, current)


def fail(task_type: str, task_key: str, error_msg: str):
    """标记任务失败"""
    data = {
        "status": "error",
        "progress": 0,
        "total": 0,
        "completed": 0,
        "failed": 0,
        "cancel_requested": False,
        "logs": [error_msg],
    }
    set(task_type, task_key, data)
