/* Damped wheel scrolling for mouse/trackpad + eased in-page anchor jumps.
   Touch, keyboard and scrollbar dragging stay native. */
window.createSmoothScroll = function ({ onFrame = () => {} } = {}) {
  const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const DAMP = 7.5;

  let maxScroll = 0;
  const measure = () => { maxScroll = document.documentElement.scrollHeight - innerHeight; };
  measure();
  addEventListener('resize', measure);
  addEventListener('load', measure);
  new ResizeObserver(measure).observe(document.body);
  const clamp = v => Math.max(0, Math.min(v, maxScroll));

  /* ---- anchor tween ---- */
  let tween = null;
  const cancelTween = () => { if (tween) { cancelAnimationFrame(tween); tween = null; } };
  ['touchstart', 'keydown', 'mousedown'].forEach(ev => addEventListener(ev, cancelTween, { passive: true }));

  /* ---- wheel inertia ---- */
  let target = scrollY, current = scrollY, expected = scrollY, running = false, last = 0;

  function stop() { running = false; target = current = expected = scrollY; }

  function nestedCanScroll(el, dy) {
    for (; el && el !== document.body && el !== document.documentElement; el = el.parentElement) {
      const oy = getComputedStyle(el).overflowY;
      if ((oy === 'auto' || oy === 'scroll') && el.scrollHeight > el.clientHeight + 1) {
        if (dy < 0 ? el.scrollTop > 0 : el.scrollTop + el.clientHeight < el.scrollHeight - 1) return true;
      }
    }
    return false;
  }

  function frame(now) {
    if (!running) return;
    // scrollbar drag / find-in-page moved the page under us: hand control back
    if (Math.abs(scrollY - expected) > 3) { stop(); return; }
    const dt = Math.min((now - last) / 1000, 1 / 30);
    last = now;
    current = target + (current - target) * Math.exp(-DAMP * dt);
    if (Math.abs(target - current) < 1.5) current = target;
    scrollTo(0, current);
    expected = scrollY;
    onFrame();
    if (current !== target) requestAnimationFrame(frame); else running = false;
  }

  if (!reduceMotion && matchMedia('(pointer: fine)').matches) {
    addEventListener('wheel', e => {
      if (e.ctrlKey || e.defaultPrevented) return;
      let dy = e.deltaY;
      if (!dy || Math.abs(e.deltaX) > Math.abs(dy)) return;
      if (e.deltaMode === 1) dy *= 16; else if (e.deltaMode === 2) dy *= innerHeight;
      if (nestedCanScroll(e.target, dy)) return;
      e.preventDefault();
      cancelTween();
      if (!running) { current = expected = target = scrollY; }
      target = clamp(target + dy);
      if (!running && target !== current) { running = true; last = performance.now(); requestAnimationFrame(frame); }
    }, { passive: false });
    addEventListener('keydown', stop, { passive: true });
  }

  function scrollToEl(el) {
    const offset = parseFloat(getComputedStyle(el).scrollMarginTop) || 0;
    const to = clamp(el.getBoundingClientRect().top + scrollY - offset);
    const start = scrollY, dist = to - start;
    cancelTween();
    stop();
    if (reduceMotion || Math.abs(dist) < 2) { scrollTo(0, to); return; }
    const dur = Math.min(850, 420 + Math.abs(dist) * 0.1);
    const t0 = performance.now();
    const ease = t => t < .5 ? 16 * t ** 5 : 1 - (-2 * t + 2) ** 5 / 2;
    (function step(now) {
      const p = Math.min((now - t0) / dur, 1);
      scrollTo(0, start + dist * ease(p));
      onFrame();
      tween = p < 1 ? requestAnimationFrame(step) : null;
    })(t0);
  }

  document.addEventListener('click', e => {
    const a = e.target.closest('a[href^="#"]');
    if (!a) return;
    const el = document.querySelector(a.getAttribute('href'));
    if (!el) return;
    e.preventDefault();
    scrollToEl(el);
    history.replaceState(null, '', a.getAttribute('href'));
  });

  return { scrollToEl, stop, get maxScroll() { return maxScroll; } };
};
