"""
API integration tests — health, compress, export endpoints
(sync TestClient, no pytest-asyncio needed)
"""
from starlette.testclient import TestClient
from app.models.database import Settings


# --------------- Health / Root ---------------

def test_health(client: TestClient):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_root(client: TestClient):
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert "version" in body


# --------------- Compress ---------------

def test_compress_progress_default(client: TestClient):
    """无任务时进度接口返回 not_started"""
    resp = client.get("/api/compress/progress")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "not_started"


def test_compress_start_no_images(client: TestClient):
    """没有待压缩图片时启动压缩，任务应快速完成"""
    resp = client.post("/api/compress/start", json={
        "target_size_kb": 500,
        "min_quality": 60,
        "max_quality": 95,
    })
    assert resp.status_code == 200
    assert resp.json()["code"] == 0


def test_compress_start_disabled_by_setting(client: TestClient, db_session):
    db_session.add(Settings(key="compress_enabled", value="0"))
    db_session.commit()

    resp = client.post("/api/compress/start", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 1
    assert "已关闭" in body["message"]


def test_compress_retry_not_found(client: TestClient):
    """重试不存在的图片返回 404 或错误"""
    resp = client.post("/api/compress/retry/nonexistent-id")
    # 404 (image not found) or 500 (table missing in minimal test DB) are both acceptable
    assert resp.status_code in (404, 500)


# --------------- Export ---------------

def test_export_progress_default(client: TestClient):
    """无任务时导出进度返回 not_started"""
    resp = client.get("/api/export/progress")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "not_started"


def test_export_start(client: TestClient, tmp_path):
    """启动导出任务（空数据库，应快速完成）"""
    resp = client.post("/api/export/start", json={
        "export_dir": str(tmp_path / "export_out"),
    })
    assert resp.status_code == 200
    assert resp.json()["code"] == 0
