export const PROJECT_LINKS = {
  'uptime-kuma': { source: 'https://github.com/louislam/uptime-kuma', reference: 'http://127.0.0.1:8770', candidate: 'http://127.0.0.1:8771' },
  'paperless-ngx': { source: 'https://github.com/paperless-ngx/paperless-ngx', reference: 'http://127.0.0.1:8772', candidate: 'http://127.0.0.1:8773' },
} as const;
export type ProjectId = keyof typeof PROJECT_LINKS;
export type Status = 'pending' | 'running' | 'passed' | 'failed' | 'blocked' | 'completed' | 'skipped';
export type Evidence = { id: string; label: string; status: Status; detail: string };
export type Project = {
  id: ProjectId; title: string; source_url: string; source_commit: string;
  phase: string; status: Status; feature: string; updated_at: string;
  reference_url: string; candidate_url: string; stages: Evidence[]; tests: Evidence[]; notes: string[];
  model: { requested_model: string; actual_model: string | null; completed_calls: number };
  readiness: { reference: boolean; candidate: boolean };
  embedding: { reference: boolean; candidate: boolean };
};
export type WebLive = { projects: Project[]; updated_at: string };
export const STATUS_LABEL: Record<Status, string> = {
  pending: '未开始', running: '进行中', passed: '通过', failed: '失败', blocked: '阻塞', completed: '已完成', skipped: '未执行',
};
const statusSet = new Set(Object.keys(STATUS_LABEL));
const object = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);
const text = (value: unknown, fallback = ''): string => typeof value === 'string' ? value : fallback;
function evidence(value: unknown): Evidence[] {
  if (!Array.isArray(value)) throw new Error('执行证据结构不正确');
  return value.map(item => {
    if (!object(item) || typeof item.status !== 'string' || !statusSet.has(item.status) || typeof item.id !== 'string' || typeof item.label !== 'string') throw new Error('执行证据结构不正确');
    return { id: item.id, label: item.label, status: item.status as Status, detail: text(item.detail) };
  });
}
export function parseWebLive(value: unknown): WebLive {
  if (!object(value) || !Array.isArray(value.projects)) throw new Error('服务返回的数据格式不正确');
  const seen = new Set<string>();
  const projects = value.projects.map(item => {
    if (!object(item) || typeof item.id !== 'string' || !Object.hasOwn(PROJECT_LINKS, item.id) || seen.has(item.id)) throw new Error('项目标识不正确');
    const id = item.id as ProjectId;
    seen.add(id);
    const links = PROJECT_LINKS[id];
    if (item.reference_url !== links.reference || item.candidate_url !== links.candidate || item.source_url !== links.source) throw new Error('项目地址不在本地实验范围内');
    if (typeof item.status !== 'string' || !statusSet.has(item.status) || !object(item.readiness) || typeof item.readiness.reference !== 'boolean' || typeof item.readiness.candidate !== 'boolean') throw new Error('项目状态不正确');
    if (!object(item.embedding) || typeof item.embedding.reference !== 'boolean' || typeof item.embedding.candidate !== 'boolean') throw new Error('应用嵌入策略不正确');
    const model = object(item.model) ? item.model : {};
    if (!Number.isSafeInteger(model.completed_calls) || (model.completed_calls as number) < 0) throw new Error('模型调用证据不正确');
    return { id, title: text(item.title, id), source_url: links.source, source_commit: text(item.source_commit), phase: text(item.phase), status: item.status as Status,
      feature: text(item.feature), updated_at: text(item.updated_at), reference_url: links.reference, candidate_url: links.candidate,
      stages: evidence(item.stages), tests: evidence(item.tests), notes: Array.isArray(item.notes) ? item.notes.filter((note): note is string => typeof note === 'string') : [],
      model: { requested_model: text(model.requested_model), actual_model: text(model.actual_model) || null, completed_calls: model.completed_calls as number },
      readiness: { reference: item.readiness.reference, candidate: item.readiness.candidate },
      embedding: { reference: item.embedding.reference, candidate: item.embedding.candidate } };
  });
  return { projects, updated_at: text(value.updated_at) };
}
export function previewTarget(project: Project, kind: 'reference' | 'candidate'): string | null {
  return project.readiness[kind] ? PROJECT_LINKS[project.id][kind] : null;
}
export function embeddedPreviewTarget(project: Project, kind: 'reference' | 'candidate'): string | null {
  return project.embedding[kind] ? previewTarget(project, kind) : null;
}
export function testCounts(tests: Evidence[]) {
  return { total: tests.length, passed: tests.filter(item => item.status === 'passed').length,
    failed: tests.filter(item => item.status === 'failed').length,
    unfinished: tests.filter(item => item.status !== 'passed' && item.status !== 'failed').length };
}
export function modelEvidence(model: Project['model']) {
  return {
    calls: model.completed_calls > 0 ? `已完成 ${model.completed_calls} 次调用` : '尚无已完成调用',
    identity: model.actual_model || '服务端未报告型号',
  };
}
