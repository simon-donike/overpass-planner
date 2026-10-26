type TimeBasis = 'utc' | 'local';

function formatTime(date: Date, basis: TimeBasis): string {
  if (basis === 'utc') return date.toISOString().slice(0, 16);
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${String(date.getFullYear()).padStart(4, '0')}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function parseTime(value: string, basis: TimeBasis): Date {
  const date = new Date(value + (basis === 'utc' ? 'Z' : ''));
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value) || !Number.isFinite(date.getTime())) {
    throw new Error(`Choose a valid ${basis === 'utc' ? 'UTC' : 'local'} start time.`);
  }
  // Do not silently normalize impossible dates or spring-forward gaps.
  if (formatTime(date, basis) !== value) {
    throw new Error(basis === 'local'
      ? 'This local time does not exist, possibly because the clocks move forward. Choose another time or enter it in UTC.'
      : 'Choose a valid UTC start time.');
  }
  return date;
}

function offsetLabel(date: Date): string {
  const minutes = -date.getTimezoneOffset();
  return `UTC${minutes < 0 ? '−' : '+'}${String(Math.floor(Math.abs(minutes) / 60)).padStart(2, '0')}:${String(Math.abs(minutes) % 60).padStart(2, '0')}`;
}

function isRepeatedTime(date: Date): boolean {
  const offset = date.getTimezoneOffset();
  return [-36, 36].some(hours => {
    const otherOffset = new Date(date.getTime() + hours * 3600000).getTimezoneOffset();
    const candidate = new Date(date.getTime() + (otherOffset - offset) * 60000);
    return otherOffset !== offset && formatTime(candidate, 'local') === formatTime(date, 'local');
  });
}

export function startTimeControl(input: HTMLInputElement, buttons: NodeListOf<HTMLButtonElement>, hint: HTMLElement) {
  let basis: TimeBasis = 'utc';
  let instant: Date | undefined = new Date();
  instant.setSeconds(0, 0);
  input.value = formatTime(instant, basis);
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'your device timezone';

  function read(): Date {
    // Preserve the instant across display toggles, even in a repeated autumn
    // hour. Editing the field starts a new wall time (first occurrence).
    if (instant && formatTime(instant, basis) === input.value) return instant;
    return parseTime(input.value, basis);
  }
  function updateHint() {
    if (basis === 'utc') {
      hint.textContent = `UTC. Local time uses your device timezone (${zone}).`;
      return;
    }
    try {
      const date = read();
      hint.textContent = `${zone} · ${offsetLabel(date)}.${isRepeatedTime(date) ? ' This local time occurs twice. Using the displayed offset; switch to UTC to choose the other occurrence.' : ''}`;
    } catch {
      hint.textContent = `Enter a local time in ${zone}.`;
    }
  }
  input.addEventListener('input', () => { instant = undefined; updateHint(); });
  buttons.forEach(button => button.addEventListener('click', () => {
    const next = button.dataset.timeBasis as TimeBasis;
    if (next === basis) return;
    // An invalid entry has no instant to preserve. Allow choosing UTC so a
    // skipped local hour can be entered there instead.
    try { instant = read(); } catch { instant = undefined; }
    basis = next;
    if (instant) input.value = formatTime(instant, basis);
    buttons.forEach(item => item.setAttribute('aria-pressed', String(item === button)));
    updateHint();
  }));
  updateHint();
  return {read};
}
