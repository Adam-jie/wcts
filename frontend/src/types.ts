export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };
export type TransportKind = 'http' | 'ssh' | 'serial';

export interface HTTPDevice {
  kind: 'http'; name?: string; base_url: string; headers?: Record<string, string>; verify_tls?: boolean;
}
export interface SSHDevice {
  kind: 'ssh'; name?: string; host: string; port?: number; username: string;
  private_key_path: string; known_hosts_path: string;
}
export interface SerialDevice {
  kind: 'serial'; name?: string; port: string; baudrate?: number; dtr?: boolean; rts?: boolean;
}
export type Device = HTTPDevice | SSHDevice | SerialDevice;

export interface Requirement {
  id: string; description: string; expected: string; category?: 'functional' | 'communication';
}
export interface Assertion {
  kind: 'status_code' | 'exit_code' | 'text_contains' | 'text_equals' | 'hex_equals' | 'json_equals';
  expected: JsonValue; path?: (string | number)[];
}
interface ActionBase {
  id: string; function_id: string; description: string; timeout_s?: number; max_bytes?: number;
  assertions: Assertion[];
}
export interface HTTPAction extends ActionBase {
  kind: 'http'; method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE' | 'HEAD';
  path: string; json_body?: JsonValue; headers?: Record<string, string>;
}
export interface SSHAction extends ActionBase { kind: 'ssh'; command: string; }
export interface SerialAction extends ActionBase {
  kind: 'serial'; write_text?: string | null; write_hex?: string | null;
  read_until?: string | null; read_until_hex?: string | null;
}
export type AllowedAction = HTTPAction | SSHAction | SerialAction;
export type HardwareAction = AllowedAction;
export interface TestStep { id: string; function_id: string; action_id: string; assertions: Assertion[]; }
export interface TestPlan { steps: TestStep[]; }
export interface HardwareDraft {
  device: Device; requirements: Requirement[]; allowed_actions: AllowedAction[];
  plan: TestPlan | null; planning_id?: string; max_duration_s: number;
}

export interface HardwareCapabilities {
  transports: Record<TransportKind, { available: boolean; dependency?: string }>;
  serial_ports: { port: string; description?: string }[];
  loopback?: { port: string; is_simulation: boolean };
  limits?: { steps: number; action_timeout_s: number; run_timeout_s: number; response_bytes: number };
  notice?: string;
}
export interface PlanningResponse {
  status: string; plan?: TestPlan; planning_id?: string; model_used?: boolean;
  model_requested?: string; model_actual?: string | null; summary?: string;
  uncovered?: { requirement_id: string; reason: string }[];
  duration_s?: number; [key: string]: unknown;
}
export interface ValidationResponse {
  valid: boolean; steps: number; requirements: number; covered_function_ids?: string[];
  is_simulation?: boolean; model_used?: boolean;
}
export interface AssertionResult extends Assertion {
  scope?: 'communication' | 'functional'; actual: JsonValue; passed: boolean; error?: string;
}
export interface HardwareActual {
  communication_passed?: boolean; text?: string; hex?: string; json?: JsonValue; json_valid?: boolean;
  status_code?: number; exit_code?: number; bytes_read?: number; bytes_written?: number;
  error?: string; timeout?: boolean; duration_ms?: number; [key: string]: unknown;
}
export interface HardwareStepResult {
  id: string; function_id: string; action_id: string; status: string;
  expected?: Assertion[]; actual?: HardwareActual; assertions?: AssertionResult[];
  request?: Record<string, unknown>; communication_passed?: boolean; duration_ms?: number; error?: string;
}
export interface HardwareReport {
  schema?: string; run_id: string; created_at?: string; status: string; success: boolean;
  functional_verified: boolean; is_simulation: boolean; model_used: boolean;
  planning_source?: string; planning_id?: string; model_requested?: string; model_actual?: string | null;
  device?: Record<string, unknown>; requirements?: Requirement[];
  steps: HardwareStepResult[];
  coverage: { function_id: string; covered: boolean; passed: boolean; category: string; steps?: string[] }[];
  validation?: { passed: number; total: number; status: string };
  duration_s?: number; report_path?: string; notice?: string; error?: string;
}
export type HardwareOperation = 'planning' | 'validating' | 'running';
export interface PlanRecord {
  origin: 'manual' | 'generated' | 'imported' | 'edited';
  planning_id?: string; model_requested?: string; model_actual?: string | null;
  summary?: string; serverConfirmed: boolean; uncovered?: { requirement_id: string; reason: string }[];
}
export interface HistoryEntry {
  id: string; operation: HardwareOperation; at: string; status: 'completed' | 'failed' | 'cancelled' | 'stale';
  message?: string; report?: HardwareReport; planning?: PlanningResponse; validation?: ValidationResponse;
}
