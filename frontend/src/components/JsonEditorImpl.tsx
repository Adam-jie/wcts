import { useEffect, useRef } from 'react';
import { EditorState } from '@codemirror/state';
import { EditorView, keymap, lineNumbers, highlightActiveLine, drawSelection } from '@codemirror/view';
import { json } from '@codemirror/lang-json';
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands';

export interface JsonEditorProps {
  value: string; onChange?(value: string): void; readOnly?: boolean; label: string; testId?: string;
}

export default function JsonEditorImpl({ value, onChange, readOnly = false, label, testId = 'json-editor' }: JsonEditorProps) {
  const container = useRef<HTMLDivElement>(null);
  const editor = useRef<EditorView | null>(null);
  const callback = useRef(onChange);
  const synchronizing = useRef(false);
  callback.current = onChange;
  useEffect(() => {
    if (!container.current) return;
    const nonce = document.querySelector<HTMLMetaElement>('meta[name="style-nonce"]')?.content || '';
    const view = new EditorView({ parent: container.current, state: EditorState.create({ doc: value, extensions: [
      lineNumbers(), drawSelection(), highlightActiveLine(), history(), json(), keymap.of([...defaultKeymap, ...historyKeymap]),
      EditorView.lineWrapping, EditorView.cspNonce.of(nonce), EditorState.readOnly.of(readOnly),
      EditorView.contentAttributes.of({ 'aria-label': label, 'data-testid': testId, role: 'textbox', 'aria-multiline': 'true', 'aria-readonly': String(readOnly) }),
      EditorView.updateListener.of(update => { if (update.docChanged && !synchronizing.current) callback.current?.(update.state.doc.toString()); }),
      EditorView.theme({ '&': { fontSize: '13px', height: '100%', color: '#dce7f6', backgroundColor: '#0d1827' }, '.cm-scroller': { fontFamily: '"Cascadia Code", Consolas, monospace', lineHeight: '1.8' }, '.cm-content': { padding: '16px 0' }, '.cm-gutters': { backgroundColor: '#111d2c', color: '#7e99b6', borderRight: '1px solid #2a3e54' }, '.cm-activeLine': { backgroundColor: '#182c40' }, '.cm-activeLineGutter': { backgroundColor: '#1b3b48' }, '.cm-cursor': { borderLeftColor: '#91dcd8' }, '.cm-selectionBackground, &.cm-focused .cm-selectionBackground': { backgroundColor: '#2a5366' }, '&.cm-focused': { outline: 'none' } }, { dark: true }),
    ] }) });
    editor.current = view;
    return () => { view.destroy(); editor.current = null; };
    // The editor is mounted for the lifetime of this panel; text updates use transactions below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [readOnly, label, testId]);
  useEffect(() => {
    const view = editor.current;
    if (view && view.state.doc.toString() !== value) {
      synchronizing.current = true;
      view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: value } });
      synchronizing.current = false;
    }
  }, [value]);
  return <div ref={container} className="json-editor" />;
}
