# 无遁 TestFix 桌面工程版

直接双击 `dist/desktop/0.1.6/Wudun-TestFix-win32-x64/Wudun-TestFix.exe`。软件打开独立窗口，自动启动自己的本地引擎，随后载入三维架构模型。首次安装与启动命令见 [复现说明](reproduce.md)。

这是使用当前工程、虚拟环境、模型配置和实验数据的 Windows 工程版。请保留原工程目录及完整的软件文件夹，不能只复制 exe。移动工程后，在软件右上角菜单中选择包含 `testfix`、`web`、`.venv` 的文件夹。此版本尚不包含面向其他电脑的完整依赖安装器。

## 使用

- 顶部切换架构模型、工程协作、硬件测试、网页验证和全部功能。
- 0.1.4 统一为深蓝暗色界面，窗口标题栏、菜单、工程流程画布、设备设置、JSON 编辑器及旧演示页面均使用暗色；公共配色定义在 `web/theme.css`。被测网站本身的页面和原始运行截图保留其实际样式。
- 右上角刷新按钮重新载入当前界面；菜单可重新连接引擎、切换工程、打开日志。
- 引擎崩溃后自动尝试恢复两次，持续失败会显示错误及恢复入口，不停在空白界面。
- 关闭窗口时，没有运行任务则关闭本软件启动的引擎。有本软件正在执行的任务时，软件保留在托盘继续运行；双击托盘图标返回，任务结束后退出。
- 三维模型与本地报告可本地使用。真实大模型和华为云调用仍需要相应网络、配置与权限。

## 实现与边界

Electron 45 独立桌面窗口包含受限的本地界面和单独的引擎进程。服务由操作系统分配空闲端口，使用会话随机标识和认证握手，不占用或接管旧网页服务的 8765 端口。正常退出由引擎在同一把锁内核验进行中的请求及所属任务并关闭新任务入口；忙碌时拒绝停止。父进程异常退出或输入管道断开时先关闭新任务入口，等待已有任务完成再退出，不按遗留 PID 文件或端口查杀进程。

主窗口工具栏和业务界面相互隔离，业务界面没有 Node.js 或桌面 IPC 权限。启用渲染器沙箱、context isolation 和 webSecurity，限制导航和新窗口。外部资料需经原生提示后在系统浏览器中打开。文件下载使用保存对话框。

软件本身不加入密钥、不复制 `.env`，仍由工程既有配置加载模型服务。接口兼容既有项目，桌面版本不会把动画当作任务执行结果。未配置的云服务和硬件执行器仍按工程报告如实显示。

## 构建与验证

```powershell
cd desktop
pnpm install --frozen-lockfile
cd ..
.\scripts\prepare-desktop.ps1
cd desktop
pnpm test
pnpm package
```

准备脚本从 Electron 官方 GitHub Release 下载固定版本运行时，并与已锁定 npm 包中的 SHA-256 校验值核对。图标复用项目已有图标。打包使用 `@electron/packager`，不修改系统浏览器设置。

Python 检查：`uv run pytest tests/test_desktop_server.py tests/test_frontend.py -q --basetemp artifacts/desktop/pytest-check`。临时目录应使用本工程下新的目录，避免 Windows 用户临时目录的权限冲突。

打包程序的 `--verify-desktop` 启动实际窗口与引擎，等待三维引擎初始化后结束自己的引擎输入管道，并再次在页面载入期间结束新引擎，验证两次自动恢复为新会话及窗口真实可见，再保存启动记录和应用自己导出的界面截图，最后正常退出。此模式采用独立的软件配置目录，不替换用户的正常配置；记录位于工程的 `artifacts/desktop/`。它验证启动、恢复与渲染，不等于重新执行一次大模型补全实验。

参考：[Electron 安全指南](https://www.electronjs.org/docs/latest/tutorial/security)、[WebContentsView](https://www.electronjs.org/docs/latest/api/web-contents-view)、[Electron Packager](https://github.com/electron/packager)。
