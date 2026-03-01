AI 图片批量生成系统（Windows / Mac 一键启动说明）

目标：把整个项目文件夹发给别人后，对方只要准备好环境，结合 `可行性分析/AccessKey.txt` 就能一键启动。

1) 文件夹要求
- 项目可放任意目录（桌面/D盘/下载目录均可）。
- 必须保持目录结构不变，尤其是：
  - `scripts/start_local.cmd` / `scripts/start_local.sh`
  - `可行性分析/AccessKey.txt`
  - `.env.example`

2) 运行前准备
- Windows：安装 Python 3.10+、Node.js 18+（LTS）。
- Mac：安装 Python 3.10+、Node.js 18+（LTS）。

3) 密钥自动注入（已内置）
- 启动脚本会自动执行：`scripts/apply_access_keys.py`
- 会从 `可行性分析/AccessKey.txt` 提取并写入 `.env`（若 `.env` 不存在会先从 `.env.example` 创建）
- 当前支持自动写入的键：
  - `APIYI_API_KEY`
  - `BAILIAN_API_KEY`
  - `VOLC_ACCESS_KEY_ID`
  - `VOLC_SECRET_ACCESS_KEY`

4) Windows 一键启动/停止
- 启动：双击 `scripts/start_local.cmd`
- 停止：双击 `scripts/stop_local.cmd`
- 首次启动会自动安装依赖，耗时较长属正常
- 启动后访问：
  - 前端: `http://localhost:3000`
  - 后端: `http://localhost:8000`

5) Mac 一键启动/停止
- 启动：`./scripts/start_local.sh`
- 停止：`./scripts/stop_local.sh`

6) 推荐给 AI 的一句话（给不会配环境的人）
- 可直接把下面这句发给 AI（如 ChatGPT/Codex）：
  - `请进入项目根目录，检查可行性分析/AccessKey.txt，自动写入 .env，然后执行一键启动脚本并检查 localhost:3000 和 localhost:8000 是否可用。`

7) 常见问题
- 3000/8000 打不开：先执行停止脚本，再重新启动。
- 提示缺少 Python/npm：先安装后重试。
- 生成失败但服务能打开：
  - 查看 `.runtime/logs/backend.log`
  - 重点关注：
    - `insufficient_user_quota`（上游额度不足）
    - `no_available_channel`（上游渠道不可用）
    - `strict_no_watermark_cleanup_failed`（严格无水印校验失败）
