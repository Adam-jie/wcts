# 测试集库

桌面软件进入“工程协作 → 测试集”，或在架构模型顶部点击“测试集”。页面路径 `/test-library`。可按领域、补全/修改/开发、数据来源筛选，并搜索功能和任务编号。

选中任务后，可以编辑功能需求和验收标准，再点击“导入为任务”。每次导入创建独立任务，工程附件复制到该任务目录，后续编辑不会修改测试集原包。SWE-bench 任务导入公开问题和固定提交的源码下载地址，须在任务中心点击“整理输入材料”下载工程。

## 已收录内容

| 集合 | 本地输入条目 | 内容与来源 | 执行条件 |
| --- | ---: | --- | --- |
| 完整工程样例 | 12 | Uptime Kuma、rich-cli、FreeRTOS-Kernel、libmodbus，每仓库各补全、修改、开发3项 | 源码包已下载；各任务仍需相应工程适配器、依赖、工具链或设备 |
| SWE-bench Lite | 300 | ICLR 2024 SWE-bench 官方 Lite test 的公开问题和基线提交 | Linux / Docker及官方评测器；目标仓库未全部下载 |
| HumanEval | 164 | 官方函数补全输入；不向Agent发送参考解或隐藏测试 | 函数级辅助评测，不能算作完整工程 |
| VerilogEval | 312 | 同156道题的代码补全与spec-to-RTL两种设置 | Icarus Verilog和官方评测环境；不等于312道不同问题 |
| 需求开发模板 | 2 | 设备台账软件、串口控制固件 | 平台、接口和歧义先确认，随后开发 |

页面合计790条输入，不是790项成功实验。功能通过、回归保持、循环稳定性和耗时须由后续真实运行报告统计。硬件宿主仿真、RTL仿真、MCU板级和MPU设备测试分开记录。

## 工程样例的构造

完整工程包保留上游源码、文档、许可证与已有测试。四项补全任务仅从各自固定快照移除一个生产文件：Uptime Kuma的MQTT监控模块、rich-cli命令入口、FreeRTOS的链表模块、libmodbus的数据转换模块。原始上游ZIP单独保存在`data/test-library/evaluator-only/`，不进入Agent附件。它们是人为构造的缺失实现实验，不是上游真实缺陷。

rich-cli修改任务来自上游issue #147，固定快照本身含有CSV标签误解释问题。其已注册执行器可以通过详情中的“使用原始基准任务运行”进入补全界面，使用既有冻结合同；这个按钮不采用用户在测试集详情中临时编辑的需求。其余修改和开发任务为自建需求，不宣称属于原论文。

VerilogEval仅将公开prompt收录到页面；参考RTL和测试平台留在上游评测归档。SWE-bench仅使用既有校验过的公开任务索引，参考补丁不传入任务资料。

## 采集与复现

```powershell
.venv/Scripts/python.exe -X utf8 scripts/collect_test_library.py
```

采集脚本固定上游commit，保存原始ZIP、来源、许可证和SHA-256，生成12个任务工程包与公开索引。重复采集先核对已下载归档哈希。脚本不安装依赖、不运行目标仓库、不调用模型。SWE-bench Lite与HumanEval沿用既有采集流程。

生成数据位于`data/test-library/`，由脚本重新生成，不能直接手改生成的`index.json`。默认源码发布包不包含本机采集的原始数据，克隆后运行采集脚本。

## 来源

- [SWE-bench，ICLR 2024](https://proceedings.iclr.cc/paper_files/paper/2024/hash/edac78c3e300629acfe6cbe9ca88fb84-Abstract-Conference.html)，[官方实现](https://github.com/SWE-bench/SWE-bench)
- [HumanEval](https://github.com/openai/human-eval)
- [VerilogEval](https://github.com/NVlabs/verilog-eval)，本次commit `c498220d0a52248f8e3fdffe279075215bde2da6`
- [Uptime Kuma](https://github.com/louislam/uptime-kuma)，`124e79bce978ce29f0de681267b62b7d0d3d4429`，MIT
- [rich-cli issue #147](https://github.com/Textualize/rich-cli/issues/147)，`46f4d2469097be395558768714a5f07ebccf1412`，MIT
- [FreeRTOS-Kernel](https://github.com/FreeRTOS/FreeRTOS-Kernel)，`dbf70559b27d39c1fdb68dfb9a32140b6a6777a0`，MIT
- [libmodbus](https://github.com/stephane/libmodbus)，`5190e5e141780ae481f24be16d7b39a5f3ad8f8f`，LGPL-2.1-or-later；保留原许可，分发修改版时遵循该许可
