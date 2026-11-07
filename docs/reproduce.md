# v0.2.1 安装、启动与复现

本版本以源码目录作为工作区。建议使用 Python 3.13、Node.js 24 和 pnpm 11，Python 支持范围为 3.11 及以上。Python 和前端、桌面端依赖分别由 `uv.lock` 和各自的 `pnpm-lock.yaml` 固定。Windows 提供 Electron 桌面端，Linux 运行同一后端和工作台。

## 安装与启动

在解压后的源码根目录运行。

```powershell
uv sync --extra dev --locked
uv run python scripts/verify-software-cases.py http://127.0.0.1:8765 --prepare-only
uv run python -m testfix.cli serve --port 8765
```

打开 `http://127.0.0.1:8765/implementation`。准备命令只登记三份独立软件样本，不调用模型。每份样本的原始源码、需求和允许修改范围都单独保存，后续运行创建独立候选和冻结测试。

WCTP 七个核心文件位于 `testfix/_vendor/wctp`，首次运行直接加载并核对 SHA-256。`TESTFIX_WCTP_ROOT` 可以指定另一份哈希匹配的源码。依赖无需放在源码目录外的固定中文路径。

## 模型配置

本机已有 Codex CLI 登录时，运行 `codex login status` 确认状态，在工作台选择 Codex。CLI 请求模型默认为 `gpt-5.6-luna`。

也可以将 `.env.example` 复制为 `.env`，配置 `TESTFIX_LLM_BASE_URL`、`TESTFIX_LLM_MODEL`、`TESTFIX_LLM_API_KEY`，在工作台选择已配置的兼容模型服务。云端使用部署环境中的模型配置，运行完整任务后核验实际服务兼容性。配置文件和凭据独立于源码包。

## Windows 桌面端

```powershell
pnpm --dir desktop install --frozen-lockfile
.\scripts\prepare-desktop.ps1
pnpm --dir desktop start
```

首次启动可选择包含 `testfix`、`web` 和 `.venv` 的源码目录。桌面端自动启动所属引擎。构建可分发桌面壳时执行 `pnpm --dir desktop package`，输出位于 `dist/desktop/0.1.6`。桌面壳与已安装依赖的工程目录配合使用。

## 构建前端

发布源码带有工作台的构建页面。修改 `frontend/src` 后重新构建。

```powershell
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend build
```

构建生成 `web/hardware-app` 和第三方许可证清单。生成目录中的文件通过构建命令更新。

## 自动化验证

```powershell
uv run python -X utf8 -m pytest -q --basetemp .tmp-release-check --junitxml artifacts/release-check/pytest.xml
pnpm --dir frontend test
pnpm --dir desktop test
```

Python 全量测试会执行真实 WCTP 核心、源代码版本校验、原有回归、独立验收、真实 HTTP 及 SQLite 重启。模型传输的单元测试使用明确标注的边界输入。实际模型补全需要单独发起。

需要串口或 SSH 硬件适配时，可执行 `uv sync --extra dev --extra hardware --locked` 安装可选依赖，并提供设备连接信息。FEA-Bench 官方源码和 Lite 任务 ID 清单以固定版本随包保留；其完整数据重建、Docker 裁判和模型实验仍按对应基准文档执行。

## 三种软件任务的真实模型实验

保持后端运行，在另一个终端运行以下命令。它会产生真实模型请求，并为每份通过验收的候选生成、执行两轮 WCTP 功能测试。

```powershell
uv run python scripts/verify-software-cases.py http://127.0.0.1:8765 repair completion greenfield
```

样本分别覆盖搜索、分页及状态保存的缺陷修复，完成状态写入的缺失功能补全，以及从启动骨架生成网页、HTTP 接口和 SQLite 存储。它们是受控的 TaskBoard 验证样本。其他已登记工程通过各自适配器运行，任意新语言、框架和真实硬件需具备对应构建、执行及验收条件。

## 显示软件真实操作画面

```powershell
pnpm --dir frontend install --frozen-lockfile
$env:TESTFIX_UI_VISIBLE = '1'
uv run python scripts/verify-software-cases.py http://127.0.0.1:8765 repair completion greenfield
```

Windows 使用已安装的 Microsoft Edge。Linux 首次使用时执行 `pnpm --dir frontend exec playwright install --with-deps chromium`。Linux 有桌面时可显示浏览器，无桌面服务器保存浏览器测试记录需使用相应显示环境。

浏览器实际新增任务、勾选和取消完成、刷新、搜索、翻页和删除，并保存每步截图、WebM 录像和版本绑定的 JSON 回执。任一阶段失败，整轮脚本以非零状态退出。各次运行的完整输出位于 `artifacts/software-check-*`，补全报告位于 `artifacts/<run-id>`。

## 源码包校验

```powershell
uv run python scripts/package_source.py
```

输出 `artifacts/release-v0.2.1/testfix-agent-v0.2.1-source.zip` 和校验回执。包内 `SOURCE-MANIFEST.json` 包含每个文件的 SHA-256。可按以下方式独立核对。

```python
import hashlib
import json
import zipfile

with zipfile.ZipFile("testfix-agent-v0.2.1-source.zip") as archive:
    manifest = json.loads(archive.read("SOURCE-MANIFEST.json"))
    assert all(hashlib.sha256(archive.read(name)).hexdigest() == digest
               for name, digest in manifest.items())
```

GitHub Release 提供固定标签和源码包。启动软件、测试、模型实验分别记录结果。CodeArts 开发记录和华为云实际部署证据在对应操作完成后单独提供。
