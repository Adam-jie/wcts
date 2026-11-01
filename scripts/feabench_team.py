"""Connect the native FEA pilot to the existing WCTP employee controller."""

from testfix.project_eval import _mapping_sha256
from testfix.project_tools import confined, files_under


def pending_team_work(plan, revision, task_id):
    states = revision['task_graph']['tasks']
    return [item for item in plan['work_items'] if item['id'] != task_id
            and states[item['id']]['status'] != 'handed_off']


class PilotTeamWorkspace:
    def __init__(self, settings, directory, case, baseline, candidate, integrity, apply):
        self.settings, self.directory = settings, directory
        self.baseline, self.candidate = baseline, candidate
        self.allowed = set(case['allowed_paths'])
        self.project = {'adapter':'python_package', 'requirements':[case['requirement']]}
        self.source_seal = files_under(baseline)
        self.candidate_seal = files_under(candidate)
        self._integrity, self._apply = integrity, apply

    def assert_integrity(self):
        self._integrity()

    def candidate_hash(self):
        return _mapping_sha256(files_under(self.candidate))

    def snapshot(self):
        self.assert_integrity()
        sources = {name:confined(self.candidate,name,exists=True).read_text(encoding='utf-8')[:2000]
                   for name in sorted(self.allowed)}
        return {'source_revision':_mapping_sha256(self.source_seal), 'candidate_sha256':self.candidate_hash(),
                'sources':sources, 'source_context':{'mode':'indexed_excerpts',
                    'notice':'Initial excerpts only; use employee read_file or search for actual source.'},
                'scope_manifest':{'allowed_changes':sorted(self.allowed),
                                  'frozen':['original_tests','generated_tests','independent_acceptance','evaluation_code']}}

    def apply_patch(self, proposal):
        receipt = self._apply(proposal)
        self.candidate_seal = files_under(self.candidate)
        return receipt
