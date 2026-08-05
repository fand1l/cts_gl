/* The lasso stroke, on a canvas.
 *
 * A port of what the program draws, by the rules in docs/STROKE.md and the
 * constants in src/circle_to_search/stroke.py — not something that also glows:
 *
 *   * the line is white, all of it, end to end, at full opacity, 12 px, with
 *     round caps and round joins, and it is open — the ends pass each other;
 *   * under it a translucent black stroke four pixels wider, because a white
 *     line on a white page is invisible and this page does not control what is
 *     underneath it either;
 *   * the colour is a *glow under the head*: a soft round blob, drawn BEFORE
 *     the white line so the cap sits on top of it;
 *   * the colour of a blob is read off where that blob is, top to bottom —
 *     blue, red at two fifths, yellow a little below, green at the bottom, with
 *     linear interpolation.  One pure function of one number (js/ramp.js);
 *   * the stretch is not velocity maths.  The last half-second of head
 *     positions are kept and a blob is drawn at every one of them, fading with
 *     age: moving fast they lie far apart and the glow *is* a smear; nearly
 *     still they stack and it is a bright round circle.
 *
 * The two performance lessons from the program are ported with it: the settled
 * part of the ribbon is baked into a layer every 48 points so the per-frame cost
 * stays flat however long the line gets, and each blob colour is rasterised once
 * and blitted after that.
 */
(function (global) {
  'use strict';

  var CTS = (global.CTS = global.CTS || {});

  /* stroke.py:38 — the line, and the shadow that makes it visible on white. */
  var LINE_WIDTH = 12;
  var SHADOW_EXTRA = 4;
  var SHADOW_ALPHA = 90 / 255; /* overlay.py:1758, QColor(0, 0, 0, 90) */

  /* stroke.py:44 — far bigger than a first guess suggests. */
  var GLOW_RADIUS = 140;

  /* stroke.py:49 — how long a remembered position keeps glowing, and how often
   * new ones are taken.  The cap matters: a mouse can report at 1000 Hz. */
  var TRAIL_LIFETIME_MS = 500;
  /* `1000 // 90` in Python is 11, not 11.11: integer division, and the tests
   * pin the 11 rather than the rate. */
  var TRAIL_MIN_GAP_MS = Math.floor(1000 / 90);

  /* stroke.py:54 — a full trail is blobs overlapping by nine tenths; drawing
   * every third one is indistinguishable and costs a third of the blending. */
  var MAX_DRAWN = 16;

  /* overlay.py:126 — how much of the ribbon is baked into the layer at a time. */
  var STROKE_CHUNK = 48;

  /* overlay.py:107 — a pointer move joins the polyline only once it is this far
   * from the last kept point, on either axis. */
  var MIN_STEP = 3;

  /* --------------------------------------------------------- the glow blobs */

  var blobs = Object.create(null);
  var BLOB_CACHE_LIMIT = 64;

  /* A soft round glow of that colour, rendered once and kept — keyed on the
   * colour rounded to five bits a channel, which is invisible on a blur and
   * keeps the cache to about two dozen entries for a whole ramp. */
  function glowBlob(rgb) {
    var key = (rgb[0] >> 3) + ':' + (rgb[1] >> 3) + ':' + (rgb[2] >> 3);
    var cached = blobs[key];
    if (cached) return cached;

    /* The middle of the bucket, so rounding never drifts one way. */
    var exact = [
      ((rgb[0] >> 3) << 3) | 4,
      ((rgb[1] >> 3) << 3) | 4,
      ((rgb[2] >> 3) << 3) | 4,
    ];
    var side = GLOW_RADIUS * 2;
    var canvas = document.createElement('canvas');
    canvas.width = side;
    canvas.height = side;
    var ctx = canvas.getContext('2d');
    var gradient = ctx.createRadialGradient(
      GLOW_RADIUS, GLOW_RADIUS, 0,
      GLOW_RADIUS, GLOW_RADIUS, GLOW_RADIUS
    );
    /* Most of the falloff in the outer half, so a blob has a bright core and a
     * long soft skirt rather than being a hard disc. */
    [[0, 150], [0.45, 45], [1, 0]].forEach(function (stop) {
      gradient.addColorStop(
        stop[0],
        'rgba(' + exact[0] + ',' + exact[1] + ',' + exact[2] + ',' + stop[1] / 255 + ')'
      );
    });
    ctx.fillStyle = gradient;
    ctx.beginPath();
    ctx.arc(GLOW_RADIUS, GLOW_RADIUS, GLOW_RADIUS, 0, Math.PI * 2);
    ctx.fill();

    if (Object.keys(blobs).length >= BLOB_CACHE_LIMIT) blobs = Object.create(null);
    blobs[key] = canvas;
    return canvas;
  }

  /* -------------------------------------------------------------- the trail */

  function Trail() {
    this.blobs = [];
  }

  Trail.prototype.add = function (x, y, rgb, atMs) {
    var last = this.blobs[this.blobs.length - 1];
    if (last && atMs - last.at < TRAIL_MIN_GAP_MS) return false;
    this.blobs.push({ x: x, y: y, rgb: rgb, at: atMs });
    this.prune(atMs);
    return true;
  };

  Trail.prototype.prune = function (nowMs) {
    var cutoff = nowMs - TRAIL_LIFETIME_MS;
    if (!this.blobs.length || this.blobs[0].at >= cutoff) return;
    this.blobs = this.blobs.filter(function (blob) {
      return blob.at >= cutoff;
    });
  };

  Trail.prototype.clear = function () {
    this.blobs = [];
  };

  /* Every blob still glowing, with how much of it is left (1 -> 0), thinned to
   * `limit` of them evenly and always keeping the two ends. */
  Trail.prototype.alive = function (nowMs, limit) {
    limit = limit === undefined ? MAX_DRAWN : limit;
    var cutoff = nowMs - TRAIL_LIFETIME_MS;
    var out = [];
    this.blobs.forEach(function (blob) {
      if (blob.at < cutoff) return;
      out.push({ blob: blob, left: Math.max(0, 1 - (nowMs - blob.at) / TRAIL_LIFETIME_MS) });
    });
    if (limit > 1 && out.length > limit) {
      var last = out.length - 1;
      var picked = [];
      var seen = Object.create(null);
      for (var i = 0; i < limit; i++) {
        var index = Math.round((i * last) / (limit - 1));
        if (!seen[index]) {
          seen[index] = true;
          picked.push(index);
        }
      }
      picked.sort(function (a, b) {
        return a - b;
      });
      out = picked.map(function (index) {
        return out[index];
      });
    }
    return out;
  };

  /* ------------------------------------------------------------ the surface */

  function Stroke(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.points = [];
    this.trail = new Trail();
    this.layer = null;
    this.layerCtx = null;
    this.frozenUpto = 0;
    this.width = 0;
    this.height = 0;
    this.ratio = 1;
    this.opacity = 1;
    this.reduced = !!(
      global.matchMedia && global.matchMedia('(prefers-reduced-motion: reduce)').matches
    );
    this.resize();
  }

  Stroke.LINE_WIDTH = LINE_WIDTH;
  Stroke.GLOW_RADIUS = GLOW_RADIUS;
  Stroke.TRAIL_LIFETIME_MS = TRAIL_LIFETIME_MS;

  Stroke.prototype.resize = function () {
    var rect = this.canvas.getBoundingClientRect();
    var width = Math.max(1, Math.round(rect.width));
    var height = Math.max(1, Math.round(rect.height));
    /* DPR-aware, so it is not soft on a HiDPI screen — clamped only so a 4×
     * display does not allocate four times the pixels for a blur. */
    var ratio = Math.min(3, Math.max(1, global.devicePixelRatio || 1));
    if (width === this.width && height === this.height && ratio === this.ratio) return;
    this.width = width;
    this.height = height;
    this.ratio = ratio;
    this.canvas.width = Math.round(width * ratio);
    this.canvas.height = Math.round(height * ratio);
    this.dropLayer();
    this.paint();
  };

  Stroke.prototype.dropLayer = function () {
    this.layer = null;
    this.layerCtx = null;
    this.frozenUpto = 0;
  };

  Stroke.prototype.clear = function () {
    this.points = [];
    this.trail.clear();
    this.dropLayer();
    this.opacity = 1;
  };

  /* The colour a head at this height has: read off where it is on the thing it
   * is drawn on, exactly as the overlay reads it off the screen it covers. */
  Stroke.prototype.colourAt = function (y) {
    return CTS.glowColour(y, this.height);
  };

  /* One more head position.  The *glow* takes every move, so the light follows
   * the hand exactly; the *polyline* only takes a move that has got at least
   * three pixels away from the last point it kept, which is what stops a slow
   * hand from turning a ribbon into ten thousand collinear segments.
   * overlay.py:107, :2654-2664. */
  Stroke.prototype.push = function (x, y, atMs) {
    var last = this.points[this.points.length - 1];
    if (!last || Math.abs(x - last.x) >= MIN_STEP || Math.abs(y - last.y) >= MIN_STEP) {
      this.points.push({ x: x, y: y });
    }
    this.trail.add(x, y, this.colourAt(y), atMs);
  };

  /* Bake the settled part of the ribbon into a layer, so the per-frame cost is
   * flat however long the line gets.  overlay.py:1722. */
  Stroke.prototype.freeze = function () {
    if (this.points.length - this.frozenUpto <= STROKE_CHUNK) return;
    if (!this.layer) {
      this.layer = document.createElement('canvas');
      this.layer.width = this.canvas.width;
      this.layer.height = this.canvas.height;
      this.layerCtx = this.layer.getContext('2d');
      this.layerCtx.scale(this.ratio, this.ratio);
    }
    /* Overlap by one segment so the round caps of the frozen part and the live
     * tail meet on a shared point instead of leaving a gap. */
    var start = Math.max(0, this.frozenUpto - 1);
    var end = this.points.length - 1;
    var chunk = this.points.slice(start, end);
    this.ribbon(this.layerCtx, chunk, LINE_WIDTH + SHADOW_EXTRA, 'rgba(0,0,0,' + SHADOW_ALPHA + ')');
    this.ribbon(this.layerCtx, chunk, LINE_WIDTH, '#ffffff');
    this.frozenUpto = end;
  };

  Stroke.prototype.ribbon = function (ctx, points, width, colour) {
    if (points.length < 2) return;
    ctx.save();
    ctx.lineWidth = width;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.strokeStyle = colour;
    ctx.beginPath();
    ctx.moveTo(points[0].x, points[0].y);
    for (var i = 1; i < points.length; i++) ctx.lineTo(points[i].x, points[i].y);
    ctx.stroke();
    ctx.restore();
  };

  /* The glow, then the shadow, then the white line — in that order.  The cap
   * sits on top of its own glow, and the shadow of the live tail goes down
   * before the frozen layer so the frozen white covers it at the join. */
  Stroke.prototype.paint = function (nowMs) {
    var ctx = this.ctx;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    ctx.scale(this.ratio, this.ratio);
    if (this.opacity <= 0) return;
    ctx.globalAlpha = this.opacity;

    this.freeze();

    var alive = this.trail.alive(nowMs === undefined ? this.clock() : nowMs);
    for (var i = 0; i < alive.length; i++) {
      var item = alive[i];
      ctx.globalAlpha = item.left * this.opacity;
      ctx.drawImage(
        glowBlob(item.blob.rgb),
        item.blob.x - GLOW_RADIUS,
        item.blob.y - GLOW_RADIUS
      );
    }
    ctx.globalAlpha = this.opacity;

    var live = this.points.slice(Math.max(0, this.frozenUpto - 1));
    this.ribbon(ctx, live, LINE_WIDTH + SHADOW_EXTRA, 'rgba(0,0,0,' + SHADOW_ALPHA + ')');
    if (this.layer) {
      ctx.save();
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.globalAlpha = this.opacity;
      ctx.drawImage(this.layer, 0, 0);
      ctx.restore();
    }
    this.ribbon(ctx, live, LINE_WIDTH, '#ffffff');
    ctx.globalAlpha = 1;
  };

  Stroke.prototype.clock = function () {
    return global.performance && performance.now ? performance.now() : Date.now();
  };

  /* The last frame of a drawing, without drawing it.
   *
   * The path is replayed through the ordinary `push` with the timestamps it
   * would really have had at that speed, and then painted at the moment the
   * hand lifted — so what comes out is the same picture the animation ends on,
   * exactly: the whole white ribbon, and the glow over however much of it was
   * laid down in the last half second.  Nothing here is a special case of the
   * drawing code; it is the drawing code, run on a clock that is not a clock.
   *
   * A page whose reader has asked for no motion gets this instead of the
   * animation, and so does the link preview. */
  Stroke.prototype.paintDrawn = function (path, durationMs) {
    this.clear();
    if (!path.length) return;
    var length = 0;
    for (var i = 1; i < path.length; i++) {
      length += Math.hypot(path[i].x - path[i - 1].x, path[i].y - path[i - 1].y);
    }
    var duration = durationMs || Math.max(600, (length / 950) * 1000);
    var start = this.clock();
    var step = duration / Math.max(1, path.length - 1);
    for (var j = 0; j < path.length; j++) {
      this.push(path[j].x, path[j].y, start + step * j);
    }
    this.paint(start + duration);
  };

  CTS.Stroke = Stroke;
  CTS.Trail = Trail;
  CTS.glowBlob = glowBlob;

  /* ============================================================ the hero ==
   *
   * On arrival the stroke circles the name of the project, in one movement,
   * with the same light — and stays for a second.  That is the first thing
   * anybody sees: not a screenshot of the gesture, the gesture.  Then the
   * canvas is handed over and you can draw on it yourself, and the trail
   * follows your hand exactly the way it follows it in the program.
   */

  /* How fast the demonstration draws, in logical pixels a second.  A hand's
   * speed: fast enough that the trail is a smear rather than a dot. */
  var DEMO_SPEED = 950;
  var DEMO_MIN_MS = 1100;
  var DEMO_MAX_MS = 2300;
  var HOLD_MS = 1000;
  var FADE_MS = 400;

  function clamp(value, low, high) {
    return Math.max(low, Math.min(high, value));
  }

  /* A loop around a box: an open contour that goes a little more than once
   * round, so the two ends simply pass each other and never join.
   *
   * `bounds`, when given, is the surface it has to stay on: a loop that leaves
   * the hero is a loop drawn round nothing. */
  function loopAround(box, seed, bounds) {
    var cx = box.x + box.width / 2;
    var cy = box.y + box.height / 2;
    /* Enough room that the words are inside the loop rather than touching it,
     * and no more: with motion turned down the ribbon stays where it landed, so
     * a generous margin below is a line of somebody's text crossed out. */
    var rx = box.width / 2 + clamp(box.width * 0.18, 24, 90);
    var ry = box.height / 2 + clamp(box.height * 0.22, 18, 40);
    if (bounds) {
      /* The ends drift outwards by a few per cent, so leave room for that. */
      var margin = 10;
      rx = Math.min(rx, (Math.min(cx, bounds.width - cx) - margin) / 1.1);
      ry = Math.min(ry, (Math.min(cy, bounds.height - cy) - margin) / 1.1);
      rx = Math.max(24, rx);
      ry = Math.max(20, ry);
    }
    var start = -2.35;
    var turns = 1.14;
    var steps = 190;
    var points = [];
    for (var i = 0; i <= steps; i++) {
      var t = i / steps;
      var angle = start + t * turns * Math.PI * 2;
      /* Nobody draws a perfect ellipse.  A little wobble, from a fixed number
       * rather than a random one, so the same page draws the same loop. */
      var wobble = 1 + 0.035 * Math.sin(angle * 3 + seed) + 0.02 * Math.sin(angle * 5 - seed);
      /* The ends pass on the outside, the way they do on a real screen. */
      var drift = 1 + 0.045 * t;
      points.push({
        x: cx + Math.cos(angle) * rx * wobble * drift,
        y: cy + Math.sin(angle) * ry * wobble * drift,
      });
    }
    return points;
  }

  function pathLength(points) {
    var total = 0;
    for (var i = 1; i < points.length; i++) {
      total += Math.hypot(points[i].x - points[i - 1].x, points[i].y - points[i - 1].y);
    }
    return total;
  }

  /* Where the head is, `distance` along the path. */
  function along(points, distance) {
    var walked = 0;
    for (var i = 1; i < points.length; i++) {
      var span = Math.hypot(points[i].x - points[i - 1].x, points[i].y - points[i - 1].y);
      if (walked + span >= distance) {
        var t = span <= 0 ? 0 : (distance - walked) / span;
        return {
          x: points[i - 1].x + (points[i].x - points[i - 1].x) * t,
          y: points[i - 1].y + (points[i].y - points[i - 1].y) * t,
        };
      }
      walked += span;
    }
    return points[points.length - 1];
  }

  function Hero(canvas, target) {
    this.stroke = new Stroke(canvas);
    this.canvas = canvas;
    this.target = target;
    this.phase = 'idle';
    this.raf = 0;
    this.drawing = false;
    this.bind();
  }

  /* The box to circle, in canvas coordinates.
   *
   * The *words*, not the element: a heading is a block that fills its column
   * whatever the text in it is, and circling that draws a loop round a lot of
   * air.  A Range over its contents gives the glyphs. */
  Hero.prototype.box = function () {
    var here = this.canvas.getBoundingClientRect();
    var there = null;
    try {
      var range = document.createRange();
      range.selectNodeContents(this.target);
      var measured = range.getBoundingClientRect();
      if (measured && measured.width > 0 && measured.height > 0) there = measured;
    } catch (error) {
      /* Old browser, odd content — the element's own box will do. */
    }
    if (!there) there = this.target.getBoundingClientRect();
    return {
      x: there.left - here.left,
      y: there.top - here.top,
      width: there.width,
      height: there.height,
    };
  };

  Hero.prototype.demo = function () {
    var box = this.box();
    if (!(box.width > 0 && box.height > 0)) return;
    var path = loopAround(box, 1.7, {
      width: this.stroke.width,
      height: this.stroke.height,
    });

    if (this.stroke.reduced) {
      /* No automatic movement at all: the picture the demonstration would have
       * ended on, put up in one go. */
      this.stroke.paintDrawn(path);
      this.phase = 'held';
      return;
    }

    var length = pathLength(path);
    var duration = Math.max(DEMO_MIN_MS, Math.min(DEMO_MAX_MS, (length / DEMO_SPEED) * 1000));
    var stroke = this.stroke;
    var started = stroke.clock();
    var self = this;
    stroke.clear();
    this.phase = 'demo';

    var step = function () {
      var now = stroke.clock();
      var elapsed = now - started;
      if (self.phase !== 'demo') return;

      if (elapsed <= duration) {
        var head = along(path, (elapsed / duration) * length);
        stroke.push(head.x, head.y, now);
        stroke.paint(now);
        self.raf = requestAnimationFrame(step);
        return;
      }
      /* It stays for a second: the trail ages out on its own in half of that,
       * so what is left at the end is the plain white ribbon. */
      if (elapsed <= duration + HOLD_MS) {
        stroke.paint(now);
        self.raf = requestAnimationFrame(step);
        return;
      }
      var fading = elapsed - duration - HOLD_MS;
      if (fading <= FADE_MS) {
        stroke.opacity = 1 - fading / FADE_MS;
        stroke.paint(now);
        self.raf = requestAnimationFrame(step);
        return;
      }
      stroke.clear();
      stroke.paint(now);
      self.phase = 'idle';
    };
    this.raf = requestAnimationFrame(step);
  };

  Hero.prototype.stop = function () {
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = 0;
  };

  Hero.prototype.at = function (event) {
    var rect = this.canvas.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  };

  Hero.prototype.bind = function () {
    var self = this;
    var stroke = this.stroke;

    var tick = function () {
      /* One timer, ~60 Hz, and only while a drag is in progress: it is what
       * lets a pointer held still be seen fading rather than stopping. */
      if (!self.drawing) return;
      stroke.paint(stroke.clock());
      self.raf = requestAnimationFrame(tick);
    };

    this.canvas.addEventListener('pointerdown', function (event) {
      if (event.button !== undefined && event.button !== 0) return;
      self.stop();
      self.phase = 'user';
      self.drawing = true;
      stroke.clear();
      stroke.opacity = 1;
      var at = self.at(event);
      stroke.push(at.x, at.y, stroke.clock());
      if (self.canvas.setPointerCapture) self.canvas.setPointerCapture(event.pointerId);
      stroke.paint(stroke.clock());
      if (!stroke.reduced) self.raf = requestAnimationFrame(tick);
      event.preventDefault();
    });

    this.canvas.addEventListener('pointermove', function (event) {
      if (!self.drawing) return;
      var at = self.at(event);
      stroke.push(at.x, at.y, stroke.clock());
      /* With motion turned down there is no timer, so the picture is brought
       * up to date here instead — it only ever moves because you moved. */
      if (stroke.reduced) stroke.paint(stroke.clock());
      event.preventDefault();
    });

    var end = function (event) {
      if (!self.drawing) return;
      self.drawing = false;
      self.stop();
      /* The stroke is part of the gesture, not part of the answer, so it ends
       * with the drag — the same rule the program arrived at after the first
       * real use of it. */
      stroke.clear();
      stroke.paint(stroke.clock());
      self.phase = 'idle';
      if (event) event.preventDefault();
    };

    this.canvas.addEventListener('pointerup', end);
    this.canvas.addEventListener('pointercancel', end);
    this.canvas.addEventListener('pointerleave', function (event) {
      /* A captured pointer keeps sending moves outside the canvas, so leaving
       * is only an ending when the capture was not taken. */
      if (self.canvas.hasPointerCapture && self.canvas.hasPointerCapture(event.pointerId)) return;
      end(event);
    });

    var resize = function () {
      stroke.resize();
      if (self.phase === 'held') self.demo();
    };
    if (global.ResizeObserver) {
      new ResizeObserver(resize).observe(this.canvas);
    } else {
      global.addEventListener('resize', resize);
    }
  };

  CTS.Hero = Hero;
  CTS.loopAround = loopAround;
})(window);
