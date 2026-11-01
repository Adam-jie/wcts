# 现代硬件测试前端

`/hardware` 使用 `frontend/` 中的 React、TypeScript、Vite 应用。FastAPI 继续执行原有的 HTTP、SSH 和串口 API；`/fea` 的 CodeAgent 与 Three.js 工作台保留。

## 开发与构建

Node.js 需满足 Vite 的 engines 要求，锁定依赖见 `frontend/package.json` 和 `pnpm-lock.yaml`。本轮使用 Node 24、pnpm 11。

```powershell
cd frontend
pnpm install --frozen-lockfile
pnpm test
pnpm build
```

构建产物固定输出到仓库内的 `web/hardware-app/`。启动原有 Python 服务后访问 <http://127.0.0.1:8765/hardware>；若产物缺失或版本不匹配，服务明确返回构建提示，不会冒充新版已上线。开发时执行 `pnpm dev`，Vite 将 `/api` 代理给本机 8765 服务。

## 使用的开源技术

| 技术 | 在本工程中的用途 |
| --- | --- |
| React、TypeScript、Vite | 组件状态、类型约束和生产构建 |
| Radix UI | 对话框、页签、工具提示与键盘交互 |
| React Flow | 可拖动、缩放、点选的五环节流程画布，节点展示当前配置、计划与回执的真实摘要 |
| CodeMirror 6 | 高级 JSON 档案、协议和计划编辑 |
| Lucide | 一致的矢量图标 |

[shadcn/ui dashboard](https://ui.shadcn.com/examples/dashboard) 用作布局与组件交互参考；没有引入其商业模板或示例业务数据，也不将它说成已安装的框架。流程交互参考 [React Flow 官方示例](https://reactflow.dev/examples/overview)。

代码许可证以实际安装依赖为准：`scripts/frontend-notices.mjs` 从运行时依赖递归收集原始 LICENSE/NOTICE，构建时生成并分发 `THIRD_PARTY_NOTICES.txt`。Lucide 包含 ISC 和其衍生图标许可，不能笼统称全部依赖均为 MIT。

CodeMirror 通过动态模块加载，仅在高级编辑面板打开时请求；加载期间或模块加载失败时保留可操作的纯文本编辑框。前端不依赖公共 CDN。

## 数据与执行口径

默认状态没有硬件测试成绩。设备连接、生成规划和执行是独立操作；只有明确点击生成步骤或执行测试才调用对应 POST API。导入档案不连接设备。画布采用“设备、CodeAgent 规划、协议动作、行为断言、执行证据”五个概念节点，展示真实摘要；逐用例步骤、执行顺序和回包详见测试计划与执行结果。连线不表示后端逐用例 DAG 或实时并发，也不用于修改实际计划。

模型只能引用已声明动作，冻结断言和服务器核验逻辑沿用原后端。编辑配置后，上次结果须标为过期；中止浏览器等待不能声称后台设备动作已经停止。模拟回执始终与真实固件验收分开。

## CSP

`/hardware` 的 HTML 响应为样式生成一次性 nonce，并通过 meta 交给 CodeMirror 与 Radix 的滚动锁样式。脚本仍只允许本机静态资源；其他 API/页面保持原来的 CSP。不添加 `unsafe-inline`、`unsafe-eval` 或外部 CDN 例外。详见 [CodeMirror cspNonce](https://codemirror.net/docs/ref/#view.EditorView%5EcspNonce)。
