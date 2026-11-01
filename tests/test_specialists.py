"""Specialist routing is a host decision, never a grant of extra tools."""
from copy import deepcopy
import json
import shutil

import pytest

from testfix.specialists import DEFAULT_LIBRARY, SpecialistCatalog, route_task


def task(*paths, skill='repair-code'):
    return {'id': 'implement', 'title': 'Implement scoped behavior', 'skill_id': skill,
            'requirement_ids': ['R1'], 'paths': list(paths), 'depends_on': []}


def ids(route, status=None):
    return {item['id'] for item in route['specialists'] if status is None or item['status'] == status}


def test_flask_sql_template_routes_only_needed_skills():
    sources = {'flaskr/auth.py': 'from flask import Blueprint\ndb.execute("UPDATE users SET name=?", (name,))',
               'flaskr/templates/auth.html': '<form method="post"></form>',
               'unused/scene.js': 'import * as THREE from "three";'}
    result = route_task(task('flaskr/auth.py', 'flaskr/templates/auth.html'), sources, 'flaskr_project')
    assert result['status'] == 'ready'
    assert ids(result) == {'software', 'backend', 'database', 'frontend'}
    assert {'javascript', 'threejs', 'mcu', 'mpu', 'protocol'} <= set(result['not_applicable'])
    assert 'unused/scene.js' not in json.dumps(result)


def test_three_without_runtime_is_blocked_not_silently_generalized():
    result = route_task(task('web/scene.js'), {'web/scene.js': 'import * as THREE from "three";'}, 'flaskr_project')
    assert result['status'] == 'blocked'
    assert {'threejs', 'javascript', 'frontend'} <= ids(result, 'blocked')
    assert result['missing_capabilities']


def test_dependency_installation_or_comment_does_not_activate_three():
    sources = {'src/calc.py': '# import THREE from "three"\nVALUE = 3',
               'package.json': '{"dependencies":{"three":"*"}}'}
    assert ids(route_task(task('src/calc.py'), sources, 'python_package')) == {'software'}


def test_html_script_requires_browser_checks_as_well_as_template_rendering():
    result = route_task(task('flaskr/templates/page.html'),
                        {'flaskr/templates/page.html': '<script>const value = 1;</script>'}, 'flaskr_project')
    assert result['status'] == 'blocked'
    assert 'browser_tests' in result['missing_capabilities']


def test_deleted_file_can_take_registered_explicit_specialist_requirement():
    result = route_task(task('flaskr/view.py'), {'flaskr/view.py': ''}, 'flaskr_project',
                        required_specialists=['backend', 'database'])
    assert {'backend', 'database'} <= ids(result)
    assert result['status'] == 'ready'


def test_mcu_requires_build_and_physical_evidence():
    result = route_task(task('firmware/main.c'), {'firmware/main.c': '#include "stm32f4xx_hal.h"'}, 'flaskr_project')
    assert 'mcu' in ids(result, 'blocked')
    assert result['status'] == 'blocked'
    assert any('firmware' in name for name in result['missing_capabilities'])


def test_sqlite_adapter_does_not_imply_postgres_or_redis():
    for module, capability in [('psycopg', 'postgres_runtime'), ('redis', 'redis_runtime')]:
        result = route_task(task('flaskr/db.py'), {'flaskr/db.py': 'import ' + module}, 'flaskr_project')
        assert result['status'] == 'blocked'
        assert capability in result['missing_capabilities']


def test_explorer_can_read_unsupported_domain_without_claiming_execution():
    result = route_task(task('web/scene.js', skill='explore-repository'),
                        {'web/scene.js': 'import * as THREE from "three";'}, 'flaskr_project')
    assert result['status'] == 'ready'
    assert 'threejs' in ids(result)
    assert result['execution_scope'] == 'read_only_discovery'
    assert result['missing_capabilities']


def test_unknown_language_needs_discovery_and_missing_source_is_rejected():
    assert route_task(task('app/Main.java'), {'app/Main.java': 'class Main {}'}, 'python_package')['status'] == 'needs_discovery'
    with pytest.raises(ValueError, match='source'):
        route_task(task('absent.py'), {}, 'python_package')


def test_new_file_is_allowed_only_by_registered_write_scope():
    result = route_task(task('flaskr/templates/detail.html'), {}, 'flaskr_project',
                        allowed_new_paths=['flaskr/templates/detail.html'])
    assert ids(result) == {'software', 'frontend'}
    assert result['specialists'][0]['evidence'][0]['basis'] == 'registered_new_file_path'


def test_catalog_tamper_is_rejected_before_prompt_rendering(tmp_path):
    library = tmp_path / 'library'
    shutil.copytree(DEFAULT_LIBRARY, library)
    catalog = SpecialistCatalog(library)
    snapshot = catalog.snapshot()
    assert len(snapshot['files']) == 11
    document = library / 'database/SKILL.md'
    document.write_text(document.read_text(encoding='utf-8') + '\nchanged', encoding='utf-8')
    with pytest.raises(ValueError, match='changed'):
        catalog.render(['database'])


def test_unknown_skill_profile_and_invalid_path_are_closed():
    for profile, paths, sources, required in [
        ('arbitrary', ['app.py'], {'app.py': ''}, []),
        ('python_package', ['../app.py'], {'../app.py': ''}, []),
        ('python_package', ['app.py'], {'app.py': ''}, ['invented'])]:
        with pytest.raises(ValueError):
            route_task(task(*paths), sources, profile, required_specialists=required)


def test_route_is_deterministic_and_does_not_mutate_inputs():
    original = task('lib/util.py')
    before = deepcopy(original)
    sources = {'lib/util.py': 'def add(a, b): return a+b'}
    assert route_task(original, sources, 'python_package') == route_task(original, sources, 'python_package')
    assert original == before
