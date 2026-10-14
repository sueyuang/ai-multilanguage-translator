# AI 多语言翻译

基于 Flask 和 OpenAI 兼容 Chat Completions API（可选 bilibili Index-Translate 免费公共 API）的翻译工具，前端使用原生 HTML、CSS 和 JavaScript。

当前正式版本：**v1.1.0**（2026-10-14）。正式部署步骤见 [部署说明](部署说明.md)，功能与验证范围见 [发布说明](发布说明.md)，页面操作见 [使用说明](使用说明.md)。

## 功能

- 基础七种语言批量翻译，德/俄/阿/韩自定义选择，环境变量扩展语言。
- AI 源语言检测；检测不确定时仍翻译，避免把误判原文当成译文。
- 分组翻译、少量并发和单语言重试，保留各语言成功/失败结果。
- 复制按钮、双击复制、右键复制，Ctrl/Cmd + Enter 快捷键。
- API 设置支持检测当前地址、Key 和模型，无须先保存。
- 可切换到 bilibili Index-Translate 免费公共 API（无需 Key）。
- 输入修改或清空后取消页面旧请求，避免过期响应覆盖结果。

## 本地运行

需要 Python 3.10 或更新版本，推荐 Python 3.12。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

编辑 `.env` 配置 API。已有 `.env` 时不要覆盖。

开发运行：

```powershell
.\.venv\Scripts\python.exe app.py
```

生产运行（Windows/Linux 均支持）：

```powershell
.\.venv\Scripts\waitress-serve.exe --listen=0.0.0.0:5005 --threads=8 app:app
```

Linux/macOS 使用 `.venv/bin/python` 和 `.venv/bin/waitress-serve` 对应执行。浏览器访问 `http://localhost:5005`。

## API 配置与连接检测

```dotenv
OPENAI_API_URL=https://api.openai.com/v1
OPENAI_API_KEY=your_api_key
OPENAI_MODEL=gpt-3.5-turbo
CUSTOM_LANGUAGES=nl:荷兰语,sv:瑞典语
FLASK_DEBUG=false
```

Base URL 应为 API 根路径，例如 `https://provider.example/v1`；程序会追加 `/chat/completions`。模型名称需由提供方实际支持。

页面空白字段回退服务器配置。若页面填写与服务器不同的地址，必须同时填写该地址的 Key，服务器 Key 不会发送到其他地址；只填地址而未填 Key 时，该地址连同页面模型名一并视为无效配置被整体忽略，请求改用服务器的地址、Key 和模型。页面自定义地址仅允许公开 HTTP/HTTPS 地址；私网 LocalAI 等服务可由管理员通过服务器环境变量配置。

“检测连接”会发送一次简短模型请求，同时验证地址、认证和模型可用性，可能消耗少量模型额度。结果显示成功和耗时，或认证失败、限流、超时等错误。检测使用当前表单，不会自动保存设置。

地址和模型保存在浏览器本地；Key 只保存在当前标签页会话，关闭标签页后需要重新填写。旧版本永久存储的 Key 会迁移到会话并从永久存储移除。

## Index-Translate 免费公共 API

已接入 bilibili [Index-Translate](https://github.com/bilibili/Index-Translate) 的免费公共 API（`https://index-translate.bilibili.com/v1`，无需 Key）。在页面“API 设置 → 翻译服务”选择“Index-Translate 免费公共 API”，或在服务器设置默认服务：

```dotenv
TRANSLATION_PROVIDER=index            # openai（默认）或 index
INDEX_TRANSLATE_API_URL=https://index-translate.bilibili.com/v1
INDEX_TRANSLATE_MODEL=Index-Translate-35B-A3B
```

使用 Index-Translate 时：

- 按官方 `call_api.py` 的方式请求：只发送用户消息，使用官方提示词“请将以下文本翻译为{语言}，直接输出翻译结果，不要进行任何解释。”，`temperature=0`，关闭思考模式（`chat_template_kwargs.enable_thinking=false`），并去除残留的 `<think>` 块。
- 公共接口要求客户端 `User-Agent`，程序会自动携带 `Index-Translate-Client/1.0`。
- 每种目标语言单独请求，不使用分组 JSON；该模型不做 AI 语言检测，仅按文字脚本粗略识别，因此源语言通常也会被“翻译”一次。
- 页面的 Base URL 和 Key 不会使用；页面模型名仅在以 `Index-Translate-` 开头时生效，否则使用服务器配置的模型。

## HTTP 接口

所有 POST 接口接受 JSON 对象，参数错误返回 JSON 和 HTTP 400；请求体上限 64 KiB，超出返回 HTTP 413。

| 接口 | 用途 |
| --- | --- |
| `GET /` | 翻译页面 |
| `GET /languages` | 语言映射、显示顺序、自定义选项、输入长度上限和服务器默认翻译服务 `default_provider` |
| `POST /translate` | 单语言翻译 |
| `POST /translate_all` | 批量翻译，保留各语言成功或失败结果 |
| `POST /test_connection` | 检测当前 API 设置与模型 |

批量请求：

```json
{
  "text": "Hola mundo",
  "languages": ["en", "custom"],
  "custom_language": "de",
  "api_settings": {
    "base_url": "https://provider.example/v1",
    "api_key": "your_api_key",
    "model": "your_model",
    "provider": "openai"
  }
}
```

`api_settings` 可省略以使用服务器配置。`provider` 可为 `openai`、`index` 或留空（使用服务器 `TRANSLATION_PROVIDER`）；为 `index` 时忽略 `base_url` 和 `api_key`。`languages` 省略时使用全部支持语言；显式空列表、未知语言代码或错误类型返回 400。文本需非空且不超过 5000 字符。

批量响应 `results` 保持请求顺序，每项包含 `language`、`language_name`、`ok` 和 `is_original`。成功项包含 `translated_text`，失败项包含 `error`。顶层包含 `source_language`、`source_confirmed`（是否经 AI 确认）、`success_count` 和 `failed_count`；检测失败时可能返回字符特征推断的语言，无法推断时为 `unknown`，但只有 AI 确认同源才复用原文。部分成功返回 200，全部失败返回 502，仍包含逐项结果。模型截断不会被当作完整译文。

批量翻译默认每组最多 3 种语言，每个服务进程最多同时执行 2 个 API 请求，包含检测与翻译。单容器使用一个 Waitress 进程，多个用户与批次共享该上限；部署多个容器时并发上限会叠加。每组请求要求返回按语言代码组织的 JSON，结果顺序由服务器恢复。源语言经 AI 确认后直接保留原文，不加入翻译组；单语言组使用普通翻译请求。

组内缺失、空白或格式错误的语言会单独补译一次，已经成功的语言不重复调用。整组响应无效或截断时，该组语言分别降级为单语言请求，每个语言只补一次，补译失败不再追加重试。原始单语言组及 `/translate` 的临时失败最多重试一次；认证、权限、模型不存在或内容过滤等错误直接显示。限流只在 `Retry-After` 不超过 2 秒时做有限重试，较长等待直接报错。长文会根据输出预算缩小分组，单次输出预算最高为 12000 token；实际支持上限仍取决于模型。

例如 7 种非原文目标语言，正常路径从 7 次翻译请求减少为 3 次；源语言检测另计一次。分组减少重复提示词和原文开销，实际耗时仍取决于提供方生成速度、文本长度和重试情况。

连接检测请求使用 `{"api_settings":{"base_url":"...","api_key":"...","model":"..."}}`。成功返回 `ok`、`message`、`model` 和 `latency_ms`；失败返回 `ok: false` 和 `error`。两种情况都包含 `effective`，说明本次实际使用的配置：`provider`、`base_url`、`model`、`base_url_source`、`key_source`（`server`、`page` 或 Index-Translate 的 `none`）、`model_source`、`api_key_hint`（仅前几位和长度）和 `fell_back_to_server`。

## 正式 Docker 部署

镜像使用 Python 3.12 和 Waitress，运行用户为非 root，仅复制服务所需代码。`.env`、测试和验证产物不打入镜像。部署包提供源码和 `linux/amd64` 预构建镜像两种方式；首次使用先复制 `.env.example` 为 `.env`，填写有效的地址、Key 和模型。

导入发布镜像后启动（在源码包解压目录执行）：

```bash
docker load -i ../ai-multilanguage-translator-1.1.0-docker.tar.gz
docker compose -p ai-translator -f docker-compose.release.yml up -d
docker compose -p ai-translator -f docker-compose.release.yml ps
```

源码构建使用 `docker compose -p ai-translator -f docker-compose.yml up -d --build`。默认访问 `http://localhost:5005`；可用 `.env` 中的 `TRANSLATOR_PORT` 更改宿主端口。Compose 带健康检查和日志轮转；健康状态只验证 Web 服务，不验证上游模型。修改 `.env` 后需执行对应 Compose 文件的 `up -d --force-recreate`，普通 `restart` 不会重新注入环境变量。完整安装、校验、更新与回滚命令见 [部署说明](部署说明.md)。

## 回归验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --check static/js/script.js
```

后端测试使用模拟 API/DNS，不读取实际 `.env`，不会产生真实模型调用。

## 修改入口

| 文件 | 职责 |
| --- | --- |
| `app.py` | 设置验证、公共 API 客户端、检测和翻译路由 |
| `templates/index.html` | 页面结构与设置弹窗 |
| `static/js/script.js` | 请求状态、语言选择、检测连接、渲染与复制 |
| `static/css/style.css` | 主题、卡片与移动端布局 |
| `tests/test_app.py` | 后端回归测试 |
| `修改清单.md` | 本轮修改与验证清单 |
| `部署说明.md` | 正式包校验、镜像导入、启动、更新与回滚 |
| `发布说明.md` | 各版本功能与验证范围 |
