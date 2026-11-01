"""Install an explicitly named example into an isolated WCTP copy."""
import argparse
from pathlib import Path
import shutil

import yaml


STEPS = [
    ('Prepare', '准备独立测试数据库', 'prepare', {}, {'database_path': '', 'result': {}}, '建立独立数据库并加载应用'),
    ('CreateChinese', '新增中文任务并检查返回值', 'create_chinese', {'chinese_title': 'WCTP-中文测试-苹果'}, {'task_id': None, 'result': {}}, '返回 201、任务 ID 与中文标题'),
    ('CreateEnglish', '新增英文任务并检查返回值', 'create_english', {'english_title': 'WCTP-English-Banana'}, {'second_task_id': None, 'result': {}}, '返回 201、任务 ID 与英文标题'),
    ('Search', '搜索中文标题并核对结果', 'search_chinese', {'search_word': '中文测试-苹果'}, {'result': {}}, '只返回刚创建的中文任务'),
    ('SearchMissing', '搜索不存在的任务并检查空结果', 'search_missing', {'missing_word': 'WCTP-绝不存在-123'}, {'result': {}}, '返回空列表且总数为 0'),
    ('PageOne', '查询第一页并记录任务 ID', 'first_page', {'page_word': 'WCTP-'}, {'first_page_id': None, 'result': {}}, '每页一条，返回第一页与匹配总数 2'),
    ('PageTwo', '查询第二页并检查不重复', 'second_page', {'page_word': 'WCTP-'}, {'result': {}}, '第二页一条且 ID 与第一页不同'),
    ('Complete', '将中文任务标记为完成', 'complete_task', {}, {'result': {}}, '接口返回已完成状态'),
    ('Reopen', '重新打开数据库检查完成状态', 'verify_persistence', {}, {'result': {}}, '新建应用实例后状态仍为已完成'),
    ('Delete', '删除英文任务并检查返回值', 'delete_task', {}, {'result': {}}, '返回被删除的任务 ID'),
    ('VerifyDelete', '重新读取英文任务确认已删除', 'verify_delete', {}, {'result': {}}, '再次读取返回 404'),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--wctp-root', type=Path, required=True)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--update', action='store_true', help='重新生成已有的专用演示环境')
    args = parser.parse_args()
    root, project = args.wctp_root.resolve(), args.project.resolve()
    if not (root / 'src/gui/launcher.py').is_file() or not (project / 'taskboard/application.py').is_file():
        raise ValueError('需要 WCTP 源码根目录和 TaskBoard 工程目录')
    target = root / 'repository/taskboard_demo'
    target.mkdir(parents=True, exist_ok=args.update)
    (target / 'steps').mkdir(exist_ok=args.update)
    (target / 'cases').mkdir(exist_ok=args.update)
    (target / 'suites').mkdir(exist_ok=args.update)
    (target / '__init__.py').write_text('', encoding='utf-8')
    (target / 'steps/__init__.py').write_text('', encoding='utf-8')
    shutil.copyfile(Path(__file__).with_name('actions.py'), target / 'steps/actions.py')
    shutil.copytree(project / 'taskboard', target / 'taskboard', dirs_exist_ok=args.update,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    env = {'env_id': 'taskboard_demo', 'env_type': 'ENV_TASKBOARD_DEMO', 'env_name': 'TaskBoard 本地测试副本',
           'status': '空闲', 'location': 'local', 'instruments': [], 'duts': []}
    definitions = [{'step_id': 'step_' + name, 'step_name': title, 'step_type': 'NormalStep',
                    'action': 'taskboard_demo.steps.actions.' + function, 'parameters': params,
                    'output': outputs, 'expected_result': expected, 'is_critical': True}
                   for name, title, function, params, outputs, expected in STEPS]
    definitions += [{'step_id': 'step_Start', 'step_name': '开始功能测试', 'step_type': 'NormalStep'},
                    {'step_id': 'step_Repeat', 'step_name': '重复完整功能测试 2 轮', 'step_type': 'LoopStep', 'total': 2},
                    {'step_id': 'step_Finish', 'step_name': '测试完成', 'step_type': 'EndStep'}]
    ids = ['step_' + item[0] for item in STEPS]
    graph = ['graph TD', '    step_Start --> step_Repeat', '    step_Repeat -->|True| ' + ids[0], '    step_Repeat -->|False| step_Finish']
    graph += [f'    {left} --> {right}' for left, right in zip(ids, ids[1:])]
    graph += [f'    {ids[-1]} --> step_Repeat']
    case = {'case_id': 'case_TaskboardFlow', 'case_name': '任务管理系统逐步功能测试',
            'required_env_type': 'ENV_TASKBOARD_DEMO', 'priority': 'P1', 'status': '草稿', 'timeout': 300,
            'description': '在独立数据库中测试新增、搜索、分页、完成状态持久化和删除；重复两轮。',
            'graph': '\n'.join(graph)}
    suite = {'suite_id': 'suite_taskboard_demo', 'suite_name': '任务管理系统逐步测试',
             'case_list': ['case_TaskboardFlow'], 'parallel_mode': False}
    for name, data in [('env.yaml', env), ('steps/steps.yaml', definitions),
                       ('cases/case_TaskboardFlow.yaml', case), ('suites/suite_taskboard_demo.yaml', suite)]:
        (target / name).write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding='utf-8')
    print(target)


if __name__ == '__main__':
    main()
