import { Component, lazy, Suspense } from 'react';
import type { JsonEditorProps } from './JsonEditorImpl';
import type { ButtonHTMLAttributes, ReactNode } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import * as Tooltip from '@radix-ui/react-tooltip';
import { X } from 'lucide-react';

export function Button({ className = '', variant = 'secondary', children, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'ghost'; children: ReactNode }) {
  return <button className={`button button-${variant} ${className}`} type="button" {...props}>{children}</button>;
}

export function IconButton({ label, children, className = '', ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { label: string; children: ReactNode }) {
  return <Tooltip.Root><Tooltip.Trigger asChild><button type="button" aria-label={label} className={`icon-button ${className}`} {...props}>{children}</button></Tooltip.Trigger><Tooltip.Portal><Tooltip.Content className="tooltip-content" sideOffset={7}>{label}<Tooltip.Arrow /></Tooltip.Content></Tooltip.Portal></Tooltip.Root>;
}

export function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'success' | 'warning' | 'brand' | 'danger' }) {
  return <span className={`badge badge-${tone}`}><span aria-hidden="true" />{children}</span>;
}

export function Sheet({ open, onOpenChange, title, description, children, className = '' }: { open: boolean; onOpenChange(open: boolean): void; title: string; description: string; children: ReactNode; className?: string }) {
  return <Dialog.Root open={open} onOpenChange={onOpenChange}><Dialog.Portal><Dialog.Overlay className="dialog-overlay" /><Dialog.Content className={`dialog-content ${className}`}><div className="dialog-heading"><div><Dialog.Title>{title}</Dialog.Title><Dialog.Description>{description}</Dialog.Description></div><Dialog.Close asChild><button className="icon-button" aria-label="关闭面板"><X size={19} /></button></Dialog.Close></div>{children}</Dialog.Content></Dialog.Portal></Dialog.Root>;
}

export function JsonView({ value }: { value: unknown }) {
  return <pre className="json-view" tabIndex={0}>{JSON.stringify(redact(value), null, 2)}</pre>;
}

function redact(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(redact);
  if (!value || typeof value !== 'object') return value;
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, /authorization|cookie|password|token|secret|api.?key|private_key|known_hosts/i.test(key) ? '[已隐藏]' : redact(item)]));
}

// CodeMirror is fetched only when the advanced panel actually mounts.
// Button/Badge/Sheet imports no longer pull it into the initial application.
const EnhancedJsonEditor = lazy(() => import('./JsonEditorImpl'));

function PlainJsonEditor({ value, onChange, readOnly = false, label, testId = 'json-editor', loading = false }: JsonEditorProps & { loading?: boolean }) {
  return <div className="json-editor-fallback"><p role="status">{loading ? '正在载入增强编辑器，纯文本编辑已可用。' : '增强编辑器暂不可用，仍可使用纯文本编辑。'}</p><textarea className="json-editor plain-json-editor" aria-label={label} data-testid={testId} aria-multiline="true" aria-readonly={readOnly} readOnly={readOnly} value={value} rows={20} cols={30} spellCheck={false} onChange={event => onChange?.(event.target.value)} /></div>;
}

class EditorBoundary extends Component<{ children: ReactNode; fallback: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? this.props.fallback : this.props.children; }
}

export function JsonEditor(props: JsonEditorProps) {
  return <EditorBoundary fallback={<PlainJsonEditor {...props} />}><Suspense fallback={<PlainJsonEditor {...props} loading />}><EnhancedJsonEditor {...props} /></Suspense></EditorBoundary>;
}
