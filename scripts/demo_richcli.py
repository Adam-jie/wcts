"""Capture real original/installed-candidate CLI outputs for the visible demo."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import uuid

from testfix.package_tools import _run_bounded
from testfix.project_tools import files_under
from testfix.reports import now


ROOT = Path(__file__).resolve().parent.parent
RUN = '2e350d21b5f04011a90ce5fa2c056656'
BASE = ROOT / 'experiments/rich-cli/baseline'
REPORT = json.loads((ROOT / 'artifacts' / RUN / 'report.json').read_text(encoding='utf-8'))
SITE = ROOT / 'web/rich-cli-demo'
STAMP = uuid.uuid4().hex[:12]
ASSETS = SITE / 'assets' / STAMP
PYTHON = ROOT / '.venv-richcli/Scripts/python.exe'
INSTALLED = ROOT / '.venv-richcli-delivery-2e350d21'
FIXED = INSTALLED / 'Scripts/rich.exe'
BASE_SEAL = files_under(BASE)
CANDIDATE = ROOT / 'artifacts' / RUN / 'workspace/candidate'
CANDIDATE_SEAL = files_under(CANDIDATE)


CASES = [
    {'id':'csv','title':'CSV：方括号内容应逐字显示',
     'purpose':'同一份 CSV 输入，原版会把格式标记从数据中吞掉；修复版应保留所有字符。',
     'filename':'sample.csv','input':'name,value\n[red]urgent[/red],2\n[bold]待办[/bold],3\nlist[0],4\n',
     'args':['sample.csv','--csv'],'contains':['[red]urgent[/red]','[bold]待办[/bold]','list[0]'],'absent':[]},
    {'id':'closing','title':'CSV：遇到 [/bold] 也不能崩溃',
     'purpose':'[/bold] 是输入数据。修复版应退出码为 0，并显示原文。',
     'filename':'unmatched.csv','input':'name,value\n[/bold],2\n',
     'args':['unmatched.csv','--csv'],'contains':['[/bold]'],'absent':[]},
    {'id':'tsv','title':'TSV：自动识别文件与字面值',
     'purpose':'不传 --csv，靠 .tsv 扩展名自动识别；表头和单元格中的标记均是数据。',
     'filename':'sample.tsv','input':'[bold]name[/bold]\tvalue\n[blue]中文[/blue]\t2\n',
     'args':['sample.tsv'],'contains':['[bold]name[/bold]','[blue]中文[/blue]'],'absent':[]},
    {'id':'print','title':'原有功能：--print 仍渲染有意的格式',
     'purpose':'用户显式用 --print 表示要渲染格式。保留这个行为，显示绿色加粗文字。',
     'filename':None,'input':'[bold green]TestFix OK[/bold green]',
     'args':['--print','[bold green]TestFix OK[/bold green]'],
     'contains':['TestFix OK'],'absent':['[bold green]','[/bold green]']},
    {'id':'json','title':'原有功能：JSON 格式化',
     'purpose':'从标准输入读取 JSON，格式化显示键、字符串、数字和布尔值。',
     'filename':None,'input':'{"project":"TestFix","checks":21,"passed":true}',
     'stdin':True,'args':['--json','-'],'contains':['project','TestFix','21','true'],'absent':[]},
    {'id':'markdown','title':'原有功能：Markdown 标题与列表',
     'purpose':'读取 Markdown 文件，渲染标题、加粗与列表。可打开真实 SVG/HTML 导出。',
     'filename':'report.md','input':'# TestFix report\n\n**Result: passed**\n\n- CSV literal data preserved\n- CLI runs from installed package\n',
     'args':['report.md'],'contains':['TestFix report','Result: passed','CSV literal data preserved'],'absent':['**Result: passed**']},
]


def capture(case, version):
    directory = ASSETS / case['id'] / version
    directory.mkdir(parents=True)
    if case['filename']:
        (directory / case['filename']).write_text(case['input'],encoding='utf-8',newline='')
    binding = directory / 'source-binding.json'
    args = [*case['args'],'--width','80','--export-svg',str(directory/'output.svg'),
            '--export-html',str(directory/'output.html')]
    if version == 'before':
        code = ('import sys,runpy,json;from pathlib import Path;'
                f'sys.path.insert(0,{str(BASE / "src")!r});'
                'import rich_cli;'
                f'assert Path(rich_cli.__file__).resolve().is_relative_to(Path({str(BASE / "src")!r}).resolve());'
                f'Path({str(binding)!r}).write_text(json.dumps({{"package":rich_cli.__file__,"mode":"pinned_upstream_src"}}),encoding="utf-8");'
                'sys.argv=["rich",*sys.argv[1:]];runpy.run_module("rich_cli",run_name="__main__")')
        command = [str(PYTHON),'-I','-B','-X','utf8','-c',code,*args]
    else:
        command = [str(FIXED),*args]
    process = _run_bounded(command,cwd=directory,timeout_s=20,
                           stdin=case['input'] if case.get('stdin') else None,
                           environment={'COLUMNS':'100','LINES':'60'})
    for stream in ('stdout','stderr'):
        (directory/(stream+'.txt')).write_text(process[stream],encoding='utf-8')
    passed = (process['exit_code']==0 and not process['timeout'] and not process['output_limit_exceeded']
              and all(value in process['stdout'] for value in case['contains'])
              and all(value not in process['stdout'] for value in case['absent']))
    relative=lambda path:path.relative_to(SITE).as_posix()
    result={key:process[key] for key in ('exit_code','stdout','stderr','duration_s','timeout','output_limit_exceeded')}
    result.update(passed=passed,actual_command=command,
                  command_display='rich '+ ' '.join(case['args'])+' --width 80 --export-svg output.svg --export-html output.html',
                  stdout_url=relative(directory/'stdout.txt'),stderr_url=relative(directory/'stderr.txt'),
                  svg_url=relative(directory/'output.svg') if (directory/'output.svg').exists() else None,
                  html_url=relative(directory/'output.html') if (directory/'output.html').exists() else None)
    (directory/'receipt.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


def main():
    assert REPORT['status']=='completed' and FIXED.is_file()
    installed_main=INSTALLED/'Lib/site-packages/rich_cli/__main__.py'
    assert hashlib.sha256(installed_main.read_bytes()).hexdigest()==CANDIDATE_SEAL['src/rich_cli/__main__.py']
    results=[{**case,'before':capture(case,'before'),'after':capture(case,'after')} for case in CASES]
    assert all(case['after']['passed'] for case in results)
    assert files_under(BASE)==BASE_SEAL and files_under(CANDIDATE)==CANDIDATE_SEAL
    payload={'captured_at':now(),'model_run_id':RUN,'kind':'additional_actual_cli_demonstration',
             'model_calls':0,'cases':results,'candidate_source_matches_accepted':True,
             'note':'New real subprocess demonstrations of the accepted package; separate from original 15/21/16 test counts. Controls browse recorded outputs, not new model calls.',
             'source':{'repository':'https://github.com/Textualize/rich-cli','commit':REPORT['tasks'][0]['source']['commit']},
             'experiment':{'duration_s':REPORT['summary']['duration_s'],'model_calls':len(REPORT['model_calls']),
                           'model_requested':'gpt-5.6-sol','actual_model':None,
                           'scores':{key:{'passed':REPORT['tasks'][0][key]['passed'],'total':REPORT['tasks'][0][key]['total']}
                                     for key in ('generated_baseline','generated','baseline','acceptance','upstream_regression','behavior_regression','extended')}}}
    (SITE/'data.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    evidence=ROOT/'artifacts/rich-cli-visible-demo'
    evidence.mkdir(parents=True,exist_ok=True)
    (evidence/f'demo-{STAMP}.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'url':'http://127.0.0.1:8765/static/rich-cli-demo/index.html',
                      'actual_commands':len(results)*2,'candidate_passed':sum(case['after']['passed'] for case in results),
                      'total':len(results),'original_conforms':sum(case['before']['passed'] for case in results),
                      'source_unchanged':True,'model_calls':0},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
