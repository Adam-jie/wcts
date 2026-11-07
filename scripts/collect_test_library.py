"""Collect pinned projects and public benchmark inputs without running upstream code."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.collect_benchmarks import download, write_json

SOURCES = {
    'uptime-kuma': ('louislam/uptime-kuma', '124e79bce978ce29f0de681267b62b7d0d3d4429', 'MIT'),
    'rich-cli': ('Textualize/rich-cli', '46f4d2469097be395558768714a5f07ebccf1412', 'MIT'),
    'freertos': ('FreeRTOS/FreeRTOS-Kernel', 'dbf70559b27d39c1fdb68dfb9a32140b6a6777a0', 'MIT'),
    'libmodbus': ('stephane/libmodbus', '5190e5e141780ae481f24be16d7b39a5f3ad8f8f', 'LGPL-2.1-or-later'),
    'verilogeval': ('NVlabs/verilog-eval', 'c498220d0a52248f8e3fdffe279075215bde2da6', 'See upstream LICENSE'),
}


def collect_source(root, identifier):
    repo, commit, license_name = SOURCES[identifier]
    directory = root / 'evaluator-only' / identifier
    directory.mkdir(parents=True, exist_ok=True)
    archive, receipt = directory / 'source.zip', directory / 'source.json'
    url = f'https://codeload.github.com/{repo}/zip/{commit}'
    if archive.exists():
        manifest = json.loads(receipt.read_text(encoding='utf-8'))
        if hashlib.sha256(archive.read_bytes()).hexdigest() != manifest['archive_sha256']:
            raise ValueError(f'{identifier}: source archive checksum mismatch')
    else:
        raw, _ = download(url)
        archive.write_bytes(raw)
        manifest = {'repository': 'https://github.com/' + repo, 'commit': commit,
                    'license': license_name, 'archive_url': url,
                    'archive_sha256': hashlib.sha256(raw).hexdigest()}
        write_json(receipt, manifest)
    with zipfile.ZipFile(archive) as source:
        prefix = source.namelist()[0].split('/')[0] + '/'
        files = {entry.filename[len(prefix):]: source.read(entry) for entry in source.infolist()
                 if not entry.is_dir() and entry.filename.startswith(prefix)}
    print(f'{identifier}: {len(files)} files downloaded', flush=True)
    return manifest, files


def cases():
    # These are authored experiments on upstream snapshots, not upstream bug claims.
    values = [
        ('uptime-kuma', 'web', 'completion', '恢复 MQTT 监控模块', 'server/monitor-types/mqtt.js',
         '恢复缺失的 MQTT 监控模块，支持配置的Broker连接、订阅主题、消息匹配和监控状态反馈。',
         '正常消息为 UP；不匹配消息为 DOWN；覆盖连接失败、超时和恢复；原有监控回归通过。'),
        ('uptime-kuma', 'web', 'modification', '修改关键词监控行为', None,
         '为关键词监控增加可选的大小写不敏感模式，默认行为兼容当前版本，并保存配置。',
         '大小写开关分别测试；重启后配置保留；空关键词行为先询问确认；现有监控、通知、状态页回归通过。'),
        ('uptime-kuma', 'web', 'development', '按需求增加监控配置导出', None,
         '在现有工程新增监控配置 JSON 导出与导入预览，导入前展示差异，确认后才应用。',
         '导出再导入保持配置；拒绝无效 JSON；预览不写库；明确凭据是否导出；循环20次且原功能保持。'),
        ('rich-cli', 'software', 'completion', '恢复 CLI 主入口', 'src/rich_cli/__main__.py',
         '根据原项目 README 恢复缺失的 rich 命令入口，覆盖文件显示、CSV、JSON、Markdown 和帮助。',
         '命令可安装启动；--help/--version 正常；普通文件和标准输入均可显示；已有测试通过；重复20轮。'),
        ('rich-cli', 'software', 'modification', '修复 CSV 方括号显示', None,
         '修复上游 issue #147：CSV/TSV 表头与单元格中的 Rich 风格标签应按字面值显示，不能因 [/bold] 等内容崩溃。',
         '显式CSV、扩展名识别、stdin、head/tail、HTML/SVG导出均保留方括号；--print 的主动 markup 行为保持。'),
        ('rich-cli', 'software', 'development', '按需求增加批量文本统计', None,
         '新增 --stats 模式，输出文件或标准输入的行数、词数、字符数；原有渲染行为保持。',
         '空文件、中文、英文、混合换行和UTF-8测试；词定义先确认；20组数据重复20轮；JSON结果可解析。'),
        ('freertos', 'mcu', 'completion', '恢复内核链表模块', 'list.c',
         '恢复 FreeRTOS 内核缺失的 list.c，与现有 list.h、任务调度、队列接口兼容。',
         '链表初始化、插入、排序插入、删除和计数正确；宿主仿真运行；重复1000轮；原有内核回归通过。'),
        ('freertos', 'mcu', 'modification', '修改任务统计输出', None,
         '在已启用运行时间统计的内核配置中增加结构化任务统计导出，不改变调度行为。',
         '明确统计使能宏、时钟频率和溢出语义；统计数据与原接口一致；中断和调度回归；先宿主仿真后板级核验。'),
        ('freertos', 'mcu', 'development', '按需求实现传感器采集任务', None,
         '基于内核新增示例工程：每100ms采样，通过队列交给处理任务；阈值超限发送报警；丢样计数可读取。',
         '先询问MCU、采样接口、阈值和报警接口；宿主模拟1000次；队列满和恢复；板级以串口日志和示波器时序核验。'),
        ('libmodbus', 'mpu', 'completion', '恢复 Modbus 数据转换', 'src/modbus-data.c',
         '恢复缺失的 Modbus 数据转换模块，保持现有公开接口、字节序和寄存器映射行为。',
         '位转换及ABCD/BADC/CDAB/DCBA浮点转换边界；原有unit-test-client/server；100组数据循环20轮。'),
        ('libmodbus', 'mpu', 'modification', '修改网关超时恢复', None,
         '增加可选的有界重试策略：通信超时才重试，最多3次，非法协议响应保持原错误；默认不重试。',
         '明确RTU/TCP范围；正常、超时、断连、重连、非法帧；检测重复写风险并询问策略；原协议回归通过。'),
        ('libmodbus', 'mpu', 'development', '按需求开发寄存器采集网关', None,
         '新增Linux网关示例：轮询指定寄存器，带时间戳写入CSV，支持停止、重连、配置端口和轮询周期。',
         '询问设备地址、功能码、字节序和周期；本机TCP回环；1000轮无丢行；串口RTU实机再核验，不混为仿真结果。'),
    ]
    return values


def collect(root):
    with ThreadPoolExecutor(max_workers=4) as pool:
        sources = dict(zip(SOURCES, pool.map(lambda key: collect_source(root, key), SOURCES)))
    entries = []
    for source_id, domain, operation, title, missing, description, acceptance in cases():
        manifest, original = sources[source_id]
        identifier = source_id + '-' + operation
        if missing and missing not in original:
            raise ValueError(f'Missing expected upstream path: {missing}')
        project = {name: raw for name, raw in original.items() if name != missing}
        bundle = root / 'public/bundles' / (identifier + '.zip')
        bundle.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(bundle, 'w', compression=zipfile.ZIP_DEFLATED) as output:
            for name, raw in project.items():
                output.writestr(name, raw)
        environment = {'web': 'Node/npm；浏览器；数据库；遵循固定版本依赖锁。',
                       'software': 'Python；项目依赖；pytest；CLI构建与运行。',
                       'mcu': 'C工具链；FreeRTOS宿主端口或MCU工具链；真实板卡及串口/仪器按需配置。',
                       'mpu': 'Linux或交叉编译环境；C工具链、Autotools；TCP回环或串口RTU设备。'}[domain]
        entry = {'id': identifier, 'dataset': 'project-cases', 'domain': domain, 'operation': operation,
                 'title': title, 'description': description, 'acceptance': acceptance, 'environment': environment,
                 'source': {key: manifest[key] for key in ('repository', 'commit', 'license')},
                 'origin': 'controlled_missing_file' if missing else 'upstream_issue' if identifier == 'rich-cli-modification' else 'authored_requirement',
                 'status': 'source_ready', 'file_count': len(project), 'bundle': 'public/bundles/' + bundle.name,
                 'bundle_sha256': hashlib.sha256(bundle.read_bytes()).hexdigest(),
                 'execution': '需要匹配工程执行适配器与依赖；下载完成不代表修复或测试通过。'}
        if identifier == 'rich-cli-modification': entry['issue_url'] = 'https://github.com/Textualize/rich-cli/issues/147'
        entries.append(entry)
    manifest, files = sources['verilogeval']
    descriptions = sorted(name for name in files if name.startswith(('dataset_spec-to-rtl/', 'dataset_code-complete-iccad2023/')) and name.endswith('_prompt.txt'))
    if not descriptions: raise ValueError('VerilogEval prompt files not found')
    for name in descriptions:
        stem = name.removesuffix('_prompt.txt')
        operation = 'completion' if 'code-complete' in name else 'development'
        entries.append({'id': 'verilogeval-' + operation + '-' + Path(stem).name, 'dataset': 'verilogeval', 'domain': 'rtl',
                        'operation': operation, 'title': Path(stem).name,
                        'description': files[name].decode('utf-8'),
                        'acceptance': '使用对应官方测试平台进行Icarus Verilog仿真；保持官方判定口径。仿真通过不等于板级通过。',
                        'environment': 'Linux；Icarus Verilog 12；官方VerilogEval评测环境。', 'source': manifest,
                        'origin': 'official_benchmark', 'status': 'input_ready',
                        'execution': '公开需求已下载，官方参考RTL与测试平台保留在评测者归档；尚未接入本软件自动执行器。'})
    for domain, title, description, acceptance in [
        ('software', '从需求开发设备台账系统', '开发设备台账：新增、修改、删除、搜索、分页、CSV导出、SQLite保存；界面操作清晰。',
         '先询问用户权限及字段；中文英文、空数据、重复编号；重启持久化；单功能、组合、20轮循环和100组数据。'),
        ('mcu', '从需求开发串口控制固件', '开发串口命令控制LED并读取模拟传感器的固件；命令和错误都有明确回包。',
         '先询问MCU、引脚、波特率、协议和传感器；命令单测、组合、多数据、1000次循环；仿真及实机分别记录。'),
    ]:
        entries.append({'id': domain + '-idea', 'dataset': 'requirements', 'domain': domain, 'operation': 'development',
                        'title': title, 'description': description, 'acceptance': acceptance,
                        'environment': '由用户确认平台与依赖后配置', 'origin': 'authored_requirement',
                        'status': 'requirements_ready', 'source': {}, 'execution': '需求模板，可修改后生成任务；尚未生成工程。'})
    write_json(root / 'index.json', {'schema': 'testfix.test-library.v1', 'entries': entries})
    print(json.dumps({'entries': len(entries), 'project_cases': 12, 'verilogeval': len(descriptions), 'requirements': 2}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'data/test-library')
    collect(parser.parse_args().output)
