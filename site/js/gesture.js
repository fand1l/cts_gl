/* The gesture, shown rather than described.
 *
 * A port of src/circle_to_search/gesture.py, which draws the movement the
 * *current settings* are asking for rather than a fixed picture of "the"
 * gesture.  A web page has no settings to read, so the numbers here are the
 * defaults the installer writes into kwinrc and DetectionSettings carries
 * (config.py:39-56): two reversals, 150 px a swing, 700 px a second, 30 degrees
 * of tolerance off the diagonal.
 *
 * The path and the timing are arithmetic, exactly as they are there: every
 * stroke is `amplitude` long and within the tolerance of 45 degrees, because
 * those are the two things the detector actually measures, and the dot travels
 * at the *minimum speed the detector accepts* — easing it would make it lie
 * about how fast you have to shake.
 */
(function (global) {
  'use strict';

  var CTS = (global.CTS = global.CTS || {});

  /* config.py:43-50 — the defaults, which are also what install.sh seeds. */
  var REVERSALS = 2;
  var AMPLITUDE_PX = 150;
  var SPEED_PX_PER_SEC = 700;
  var ANGLE_TOLERANCE_DEG = 30;

  /* gesture.py:46-47 — how far each stroke leans off the diagonal.  The
   * detector needs no lean at all, but a real hand never lands twice on the
   * same pixel, and with no separation the animation is a dot sliding along a
   * single line. */
  var LEAN_OF_TOLERANCE = 0.45;
  var MAX_LEAN_DEG = 16;

  /* gesture.py:50-51 */
  var REST_MS = 900;

  function buildGesture() {
    var swings = REVERSALS + 1;
    var swingMs = (AMPLITUDE_PX / SPEED_PX_PER_SEC) * 1000;
    var lean = (Math.min(ANGLE_TOLERANCE_DEG, MAX_LEAN_DEG) * LEAN_OF_TOLERANCE * Math.PI) / 180;
    var forward = Math.PI / 4 + lean;
    var backward = Math.PI / 4 - lean;

    var points = [{ x: 0, y: 0 }];
    for (var i = 0; i < swings; i++) {
      var angle = i % 2 === 0 ? forward : backward;
      var direction = i % 2 === 0 ? 1 : -1;
      var last = points[points.length - 1];
      points.push({
        x: last.x + Math.cos(angle) * AMPLITUDE_PX * direction,
        y: last.y + Math.sin(angle) * AMPLITUDE_PX * direction,
      });
    }
    var left = Math.min.apply(null, points.map(function (p) { return p.x; }));
    var top = Math.min.apply(null, points.map(function (p) { return p.y; }));
    var shifted = points.map(function (p) {
      return { x: p.x - left, y: p.y - top };
    });
    return {
      points: shifted,
      swings: swings,
      swingMs: swingMs,
      totalMs: swingMs * swings,
      width: Math.max.apply(null, shifted.map(function (p) { return p.x; })),
      height: Math.max.apply(null, shifted.map(function (p) { return p.y; })),
    };
  }

  /* Where the dot is, `elapsed` into the gesture — gesture.py:76-88. */
  function at(gesture, elapsed) {
    var clamped = Math.max(0, Math.min(elapsed, gesture.totalMs));
    var index = Math.min(Math.floor(clamped / gesture.swingMs), gesture.swings - 1);
    var along = (clamped - index * gesture.swingMs) / gesture.swingMs;
    var start = gesture.points[index];
    var end = gesture.points[index + 1];
    return {
      x: start.x + (end.x - start.x) * along,
      y: start.y + (end.y - start.y) * along,
    };
  }

  function GesturePreview(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.gesture = buildGesture();
    this.elapsed = 0;
    this.resting = 0;
    this.last = 0;
    this.raf = 0;
    this.visible = false;
    this.reduced = !!(
      global.matchMedia && global.matchMedia('(prefers-reduced-motion: reduce)').matches
    );
    this.ratio = 1;
    this.width = 0;
    this.height = 0;
    this.resize();
    this.watch();
  }

  GesturePreview.prototype.resize = function () {
    var rect = this.canvas.getBoundingClientRect();
    var ratio = Math.min(3, Math.max(1, global.devicePixelRatio || 1));
    this.width = Math.max(1, Math.round(rect.width));
    this.height = Math.max(1, Math.round(rect.height));
    this.ratio = ratio;
    this.canvas.width = Math.round(this.width * ratio);
    this.canvas.height = Math.round(this.height * ratio);
    this.paint();
  };

  /* Fit the path into the panel — gesture.py:197-215. */
  GesturePreview.prototype.placement = function () {
    var margin = 18;
    var usableW = Math.max(1, this.width - 2 * margin);
    var usableH = Math.max(1, this.height - 2 * margin);
    var scale = Math.min(usableW / this.gesture.width, usableH / this.gesture.height);
    var drawnW = this.gesture.width * scale;
    var drawnH = this.gesture.height * scale;
    return {
      scale: scale,
      dx: (this.width - drawnW) / 2,
      dy: margin + (usableH - drawnH) / 2,
    };
  };

  GesturePreview.prototype.role = function (name) {
    return getComputedStyle(document.documentElement).getPropertyValue('--' + name).trim();
  };

  GesturePreview.prototype.paint = function () {
    var ctx = this.ctx;
    var place = this.placement();
    var gesture = this.gesture;
    var accent = this.role('primary') || '#005cba';
    var ink = this.role('on-surface-variant') || '#444653';

    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    ctx.scale(this.ratio, this.ratio);

    var placed = gesture.points.map(function (point) {
      return { x: point.x * place.scale + place.dx, y: point.y * place.scale + place.dy };
    });

    /* The whole path, faint: the shape is as much of the answer as the
     * movement is, and a dot on its own never shows a shape. */
    ctx.save();
    ctx.globalAlpha = 120 / 255;
    ctx.strokeStyle = ink;
    ctx.lineWidth = 2;
    ctx.setLineDash([6, 6]);
    ctx.beginPath();
    ctx.moveTo(placed[0].x, placed[0].y);
    for (var i = 1; i < placed.length; i++) ctx.lineTo(placed[i].x, placed[i].y);
    ctx.stroke();
    ctx.restore();

    /* How far the dot has come, solid. */
    var head = at(gesture, this.elapsed);
    var headAt = { x: head.x * place.scale + place.dx, y: head.y * place.scale + place.dy };
    var done = Math.min(Math.floor(this.elapsed / gesture.swingMs), gesture.swings);
    var travelled = placed.slice(0, done + 1).concat([headAt]);
    if (travelled.length >= 2) {
      ctx.save();
      ctx.strokeStyle = accent;
      ctx.lineWidth = 3;
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      ctx.beginPath();
      ctx.moveTo(travelled[0].x, travelled[0].y);
      for (var j = 1; j < travelled.length; j++) ctx.lineTo(travelled[j].x, travelled[j].y);
      ctx.stroke();
      ctx.restore();
    }

    ctx.fillStyle = accent;
    ctx.beginPath();
    ctx.arc(headAt.x, headAt.y, 7, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = this.role('on-primary') || '#ffffff';
    ctx.beginPath();
    ctx.arc(headAt.x, headAt.y, 3, 0, Math.PI * 2);
    ctx.fill();
  };

  GesturePreview.prototype.step = function (now) {
    var delta = this.last ? Math.min(64, now - this.last) : 16;
    this.last = now;
    if (this.resting > 0) {
      this.resting -= delta;
      if (this.resting <= 0) this.elapsed = 0;
    } else {
      this.elapsed += delta;
      if (this.elapsed >= this.gesture.totalMs) {
        this.elapsed = this.gesture.totalMs;
        this.resting = REST_MS;
      }
    }
    this.paint();
    var self = this;
    this.raf = requestAnimationFrame(function (stamp) {
      self.step(stamp);
    });
  };

  GesturePreview.prototype.start = function () {
    if (this.raf || this.reduced) return;
    this.last = 0;
    var self = this;
    this.raf = requestAnimationFrame(function (stamp) {
      self.step(stamp);
    });
  };

  GesturePreview.prototype.stop = function () {
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = 0;
  };

  /* The timer runs only while the panel is on screen — a settings window left
   * open in the background must not repaint anything thirty times a second, and
   * neither must a section you have already scrolled past. */
  GesturePreview.prototype.watch = function () {
    var self = this;
    if (this.reduced) {
      /* No movement at all: the finished path, with the dot at the end of it. */
      this.elapsed = this.gesture.totalMs;
      this.paint();
    } else if (global.IntersectionObserver) {
      new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          self.visible = entry.isIntersecting;
          if (entry.isIntersecting) self.start();
          else self.stop();
        });
      }, { threshold: 0.15 }).observe(this.canvas);
    } else {
      this.start();
    }

    document.addEventListener('visibilitychange', function () {
      if (document.hidden) self.stop();
      else if (self.visible) self.start();
    });

    if (global.ResizeObserver) {
      new ResizeObserver(function () {
        self.resize();
      }).observe(this.canvas);
    } else {
      global.addEventListener('resize', function () {
        self.resize();
      });
    }
  };

  CTS.GesturePreview = GesturePreview;
  CTS.buildGesture = buildGesture;
})(window);
