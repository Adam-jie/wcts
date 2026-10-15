// View-only controls. These never submit a model job or execute a probe.
const setup = document.getElementById('experiment-setup');
const toggle = document.getElementById('toggle-setup');
const close = document.getElementById('close-setup');
const backdrop = document.getElementById('setup-backdrop');
let returnFocus = null;
function setSetup(open) {
  if (open) returnFocus = document.activeElement;
  document.body.classList.toggle('setup-open', open);
  toggle.setAttribute('aria-expanded', String(open));
  backdrop.hidden = !open;
  if (open) {
    setup.setAttribute('role', 'dialog');
    setup.setAttribute('aria-modal', 'true');
    close.focus();
  } else {
    setup.removeAttribute('role');
    setup.removeAttribute('aria-modal');
    if (returnFocus?.isConnected) returnFocus.focus();
  }
}
toggle.addEventListener('click', () => setSetup(!document.body.classList.contains('setup-open')));
close.addEventListener('click', () => setSetup(false));
backdrop.addEventListener('click', () => setSetup(false));
window.addEventListener('lab:setup-requested', () => {
  setSetup(true);
  document.getElementById('requirement').focus();
});
window.addEventListener('lab:run-started', () => {
  setSetup(false);
  document.getElementById('collaboration').scrollIntoView({behavior: 'smooth', block: 'start'});
});
document.addEventListener('keydown', event => {
  if (!document.body.classList.contains('setup-open')) return;
  if (event.key === 'Escape') {
    event.preventDefault();
    setSetup(false);
  } else if (event.key === 'Tab') {
    const controls = [...setup.querySelectorAll('button, select, input, textarea, summary, a[href]')]
      .filter(element => !element.disabled && element.getClientRects().length);
    const first = controls[0], last = controls.at(-1);
    if (event.shiftKey && (document.activeElement === first || !setup.contains(document.activeElement))) {
      event.preventDefault(); last?.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault(); first?.focus();
    }
  }
});
const scene = document.getElementById('collaboration');
const fullscreen = document.getElementById('scene-fullscreen');
const hint = document.getElementById('interaction-hint');
fullscreen.addEventListener('click', async () => {
  try {
    if (document.fullscreenElement === scene) await document.exitFullscreen();
    else if (scene.requestFullscreen) await scene.requestFullscreen();
    else hint.textContent = '当前浏览器不支持全屏；仍可拖动旋转和滚轮缩放。';
  } catch {
    hint.textContent = '全屏暂不可用；拖动旋转、滚轮缩放仍可使用。';
  }
});
document.addEventListener('fullscreenchange', () => {
  const open = document.fullscreenElement === scene;
  fullscreen.textContent = open ? '⛶ 退出全屏' : '⛶ 全屏';
  fullscreen.setAttribute('aria-label', open ? '退出协作空间全屏' : '全屏查看协作空间');
});
