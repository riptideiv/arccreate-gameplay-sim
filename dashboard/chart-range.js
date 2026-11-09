// Two native, keyboard-accessible sliders share one track and numeric fields.
const MIN_WINDOW = .5;
const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
const milliseconds = value => Math.round(value * 1000) / 1000;
const clock = seconds => Math.floor(seconds / 60) + ':' + String(Math.floor(seconds % 60)).padStart(2, '0');

export class ChartRange {
  constructor(form, changed) {
    this.changed = changed;
    this.start = form.elements.namedItem('start_s');
    this.end = form.elements.namedItem('end_s');
    this.track = document.getElementById('chart-range');
    this.handles = ['range-start', 'range-end'].map(id => document.getElementById(id));
    this.duration = 1;
    for (const [index, handle] of this.handles.entries()) {
      handle.addEventListener('input', () => this.move(index, Number(handle.value)));
      handle.addEventListener('keydown', event => {
        const increment = event.shiftKey ? 1 : .1;
        const keys = {ArrowLeft:-increment, ArrowDown:-increment, ArrowRight:increment,
          ArrowUp:increment, PageDown:-5, PageUp:5};
        let value = Number(handle.value);
        if (event.key in keys) value += keys[event.key];
        else if (event.key === 'Home') value = index ? Number(this.handles[0].value) + MIN_WINDOW : 0;
        else if (event.key === 'End') value = index ? this.duration : Number(this.handles[1].value) - MIN_WINDOW;
        else return;
        event.preventDefault();
        this.move(index, value);
      });
    }
    for (const [index, field] of [this.start, this.end].entries()) {
      field.addEventListener('input', () => {
        if (field.value === '' || !Number.isFinite(Number(field.value))) return;
        // Numeric entry can push the other endpoint; dragging keeps it fixed.
        let a = Number(this.start.value), b = Number(this.end.value);
        if (index === 0) {
          a = clamp(a, 0, this.duration-MIN_WINDOW);
          b = clamp(Math.max(b, a+MIN_WINDOW), a+MIN_WINDOW, this.duration);
        } else {
          b = clamp(b, MIN_WINDOW, this.duration);
          a = clamp(Math.min(a, b-MIN_WINDOW), 0, b-MIN_WINDOW);
        }
        this.set(a,b);
      });
    }
    document.getElementById('full-chart').addEventListener('click', () => this.set(0,this.duration));
    this.track.addEventListener('pointerdown', event => {
      if (!event.isPrimary || event.button !== 0) return;
      event.preventDefault();
      const value = this.pointerValue(event);
      const middle = (Number(this.handles[0].value)+Number(this.handles[1].value))/2;
      this.dragging = value <= middle ? 0 : 1;
      this.handles[this.dragging].focus({preventScroll:true});
      this.track.setPointerCapture(event.pointerId);
      this.move(this.dragging,value);
    }, true);
    this.track.addEventListener('pointermove', event => {
      if (this.dragging === undefined || !this.track.hasPointerCapture(event.pointerId)) return;
      this.move(this.dragging,this.pointerValue(event));
    });
    for (const type of ['pointerup','pointercancel','lostpointercapture']) {
      this.track.addEventListener(type, () => { this.dragging = undefined; });
    }
  }

  pointerValue(event) {
    const box = this.track.getBoundingClientRect();
    const value = clamp((event.clientX-box.left-12)/(box.width-24),0,1)*this.duration;
    return value > this.duration-.025 ? this.duration : Math.round(value/.05)*.05;
  }

  move(index,value) {
    const a = Number(this.handles[0].value), b = Number(this.handles[1].value);
    this.set(index === 0 ? clamp(value,0,b-MIN_WINDOW) : a,
      index === 1 ? clamp(value,a+MIN_WINDOW,this.duration) : b);
  }

  set(start,end) {
    this.start.value = milliseconds(start);
    this.end.value = milliseconds(end);
    this.sync(this.duration);
    this.changed();
  }

  sync(duration) {
    this.duration = duration;
    this.start.max = milliseconds(duration-MIN_WINDOW);
    this.end.max = duration;
    const a = clamp(Number(this.start.value === '' ? this.handles[0].value : this.start.value)||0,0,duration-MIN_WINDOW);
    const b = clamp(Number(this.end.value === '' ? this.handles[1].value : this.end.value)||0,a+MIN_WINDOW,duration);
    for (const [index,handle] of this.handles.entries()) {
      handle.max = duration;
      handle.value = index ? b : a;
      handle.setAttribute('aria-valuemin', index ? milliseconds(a+MIN_WINDOW) : 0);
      handle.setAttribute('aria-valuemax', index ? duration : milliseconds(b-MIN_WINDOW));
      handle.setAttribute('aria-valuetext', (index ? b : a).toFixed(3).replace(/\.?0+$/,'') + ' seconds');
    }
    this.track.style.setProperty('--range-start', `${100*a/duration}%`);
    this.track.style.setProperty('--range-end', `${100*b/duration}%`);
    document.getElementById('range-duration').textContent = clock(duration);
    document.getElementById('range-selection').textContent = `${a.toFixed(2)}–${b.toFixed(2)} s · ${(b-a).toFixed(2)} s selected`;
  }
}
