# AI 图片批量生成系统

## Docker 一键部署

推荐直接使用仓库根目录的脚本。

### macOS / Linux

```bash
chmod +x start_docker.sh stop_docker.sh
./start_docker.sh
```

如果需要同时启用本地 `IOPaint` 服务：

```bash
./start_docker.sh --with-iopaint
```

停止服务：

```bash
./stop_docker.sh
```

### Windows

双击：

- `start_docker.cmd`
- `stop_docker.cmd`

也可以命令行执行：

```bat
start_docker.cmd
```

## 手动 Docker Compose 启动

```bash
cp .env.example .env
docker compose up -d --build
```

启用 `IOPaint`：

```bash
docker compose --profile iopaint up -d --build
```

## 部署前准备

1. 复制 `.env.example` 为 `.env`
2. 填入至少以下密钥
   - `APIYI_API_KEY`
   - `BAILIAN_API_KEY`
3. 如需去水印，再补充：
   - `VOLC_ACCESS_KEY_ID`
   - `VOLC_SECRET_ACCESS_KEY`

首次执行 `start_docker.sh` / `start_docker.cmd` 时，如果没有 `.env`，脚本会自动从 `.env.example` 创建。

## 启动后访问

- 前端：`http://localhost:3000`
- 后端：`http://localhost:8000`
- IOPaint：`http://localhost:8090`（仅在 `--with-iopaint` 或 `profile iopaint` 下启用）

## 持久化目录

- `./data`：数据库、运行数据、导出内容
- `./models`：模型目录，只读挂载到容器

## 已验证

当前仓库已验证以下命令可通过：

```bash
docker compose config
docker compose build backend frontend
```

本地功能验证还通过了：

```bash
cd frontend && npm run check && npm run build
cd backend && ../.venv/bin/python -m pytest tests/test_prompt_library_api.py tests/test_backup_api.py
```
