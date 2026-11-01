# MPU / MCU 功能测试

打开 <http://127.0.0.1:8765/hardware>。设备型号不参与连接判断：按实际通信协议接入 HTTP、SSH 或串口。用户提供功能要求、连接信息、协议动作和预期响应，CodeAgent 规划步骤，宿主逐步执行并保留回执。IP、串口号或芯片名称本身不包含固件命令；缺少协议时需要补充接口或帧格式。

工作台前端的首次构建与本地开发见 [现代硬件测试前端](frontend-workbench.md)。设备配置、流程和回执分别展示；高级 JSON 编辑器用于导入既有协议档案。

## 使用流程

1. 选择 HTTP、SSH 或串口，填写 IP/端口、串口号/波特率等连接信息。
2. 填写需求和允许动作。HTTP 使用相对接口路径及固定方法；SSH 使用用户提供的固定命令、本地私钥路径和已核实的 known_hosts；串口可使用文本或十六进制帧及结束符。
3. 为动作声明验收条件：HTTP 状态、SSH 退出码用于通信检查；文本匹配、十六进制匹配和 JSON 字段预期用于行为检查。功能需求必须有行为预期，单纯回显不能证明固件功能。
4. 点击“用模型生成步骤”才调用现有模型配置。硬件专用 Skill 只允许选择和安排动作，宿主复制原有断言，禁止模型更换命令、地址或降低预期。也可以自己编辑计划并先检查。
5. 执行计划，查看每步输入、预期、实际响应、断言和需求覆盖。每次生成与执行都有独立记录，失败不会被修改成成功。

默认复用现有 `codex_cli` 登录，请求模型 `gpt-5.6-sol`；也支持已有兼容 API、Ollama 配置。实际服务型号未返回时单独标为未验证。硬件规划不复用软件工程九项能力的提示词；当前 AgentArts 云运行时仍对应原软件流程。

## 安装与复现

```powershell
uv sync --extra dev --extra hardware
./scripts/start.ps1 -Background
```

HTTP 使用现有 httpx；串口使用 pyserial；SSH 使用 Paramiko。页面可枚举本机串口，不会自动扫描网络或向未知设备发命令。SSH 未知主机密钥会拒绝连接。设备凭据不进入模型上下文，报告不包含 HTTP 授权头或 SSH 私钥路径。

没有真机也可检查执行链路，但模拟结果不能当作真机成绩：

```powershell
.venv/Scripts/python.exe -X utf8 scripts/hardware_demo.py --port 9188
```

导入 `examples/hardware/http-demo.json`：`/status` 应通过两个状态断言；把动作路径改为 `/bad-status` 并重新生成或编辑计划，HTTP 仍返回 200，指示灯字段断言失败。该服务只模拟协议数据。

导入 `examples/hardware/serial-loop.json`：使用 pyserial 的 `loop://` 发送并回读文本，只验证串口传输链路。文本和二进制回环、SSH 密钥握手、HTTP 功能失败等在自动测试中覆盖。

## 结果口径

- `success`：本次声明的计划及断言是否全部通过。
- `functional_verified`：非模拟目标的功能合同是否通过；不是独立的外部物理测量证明。验证 LED 引脚、电机或传感器实际效果，需要协议返回相应可核查观测，或新增独立测量适配器。
- `is_simulation`：本机回环 IP 和 `loop://` 自动标记模拟；即使断言通过，`functional_verified` 仍为 false。
- `model_used`：执行配置和计划与服务端保存的真实模型规划指纹一致才标记 true；修改过的计划按人工编辑记录。

动作逐个在有截止时间的子进程执行，同一设备不会同时运行两套测试。一次最多 32 步、120 秒，每步最多 10 秒、64 KiB 响应。HTTP 不跟随重定向，串口与 SSH 结束后关闭连接。当前每个串口动作会独立打开、执行、关闭端口；需要持续会话或实时采样的设备应使用会话适配器扩展。

模型记录位于 `artifacts/hardware-plans/<id>/planning.json`，执行证据位于 `artifacts/hardware-runs/<id>/report.json`。这些本机产物不属于公开源码样例。

协议库文档：[pyserial](https://pyserial.readthedocs.io/en/latest/pyserial_api.html)、[Paramiko](https://docs.paramiko.org/en/stable/api/client.html)、[HTTPX 超时](https://www.python-httpx.org/advanced/timeouts/)。
