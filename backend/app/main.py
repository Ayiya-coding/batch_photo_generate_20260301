"""
FastAPI 应用主入口
"""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import settings as app_settings
from app.core.logger import logger

# 导入路由
from app.api import (
    backup,
    upload,
    preprocess,
    prompt,
    generate,
    review,
    template,
    wideface,
    compress,
    export,
    settings
)
from app.core.database import init_db, seed_default_settings

# 初始化 FastAPI 应用
app = FastAPI(
    title="AI图片批量生成系统",
    description="一套高效的AI图片批量生成和管理系统",
    version="1.0.0"
)

# CORS 配置 - 允许本地前端 + Docker 内部通信
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 临时允许所有来源，用于调试
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 全局异常处理
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception(f"未处理异常 | {request.method} {request.url.path} | {exc}")
    return JSONResponse(
        status_code=500,
        content={
            "code": 500,
            "message": "服务器内部错误",
            "detail": str(exc) if app_settings.DEBUG else None,
        },
    )


@app.on_event("startup")
def on_startup():
    """应用启动时初始化数据库和默认配置"""
    logger.info("AI图片批量生成系统启动中...")
    init_db()
    seed_default_settings()
    logger.info(f"系统就绪 | DEBUG={app_settings.DEBUG}")


# 注册路由
app.include_router(upload.router, prefix="/api/upload", tags=["素材上传"])
app.include_router(preprocess.router, prefix="/api/preprocess", tags=["图片预处理"])
app.include_router(prompt.router, prefix="/api/prompt", tags=["提示词生成"])
app.include_router(generate.router, prefix="/api/generate", tags=["批量生图"])
app.include_router(review.router, prefix="/api/review", tags=["审核分类"])
app.include_router(template.router, prefix="/api/template", tags=["模板管理"])
app.include_router(wideface.router, prefix="/api/wideface", tags=["宽脸图生成"])
app.include_router(compress.router, prefix="/api/compress", tags=["画质压缩"])
app.include_router(export.router, prefix="/api/export", tags=["批量导出"])
app.include_router(backup.router, prefix="/api/backup", tags=["备份恢复"])
app.include_router(settings.router, prefix="/api/settings", tags=["系统设置"])

# 静态文件服务 — 让前端通过 /api/files/uploads/xxx.jpg 访问上传的图片
app.mount("/api/files", StaticFiles(directory=str(app_settings.DATA_DIR)), name="static_files")

# 健康检查
@app.get("/health")
async def health_check():
    """系统健康状态检查"""
    return {"status": "ok", "message": "系统运行正常"}


@app.get("/api/health")
async def api_health_check():
    """兼容前端/代理统一以 /api 前缀访问健康检查。"""
    return {"status": "ok", "message": "系统运行正常"}

@app.get("/")
async def root():
    """API 根路由"""
    return {
        "name": "AI图片批量生成系统",
        "version": "1.0.0",
        "status": "running"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )
