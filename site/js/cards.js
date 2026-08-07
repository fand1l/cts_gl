/* The cards lean towards your pointer, and light comes out from behind them.
 *
 * The rest of this site moves for a reason — the stroke is the gesture, the
 * dot is the movement the thresholds ask for — so this one has to earn its
 * place too.  What it buys is that a screenshot stops being a picture pasted on
 * a page and starts behaving like a panel with a light behind it, which is what
 * the program's own screen looks like when the lasso is glowing on it.
 *
 * Nothing here is a library.  There is no framework on this site, no build step
 * and no request that leaves the domain, so Framer Motion is out (it is React),
 * and GSAP or Motion are out twice over — from npm they need a build, from a CDN
 * they are the one thing the site promises never to do.  What is left is what
 * the rest of the site already uses: requestAnimationFrame and custom
 * properties.
 *
 * **The lag is a lerp, not a spring**, and deliberately.  material.py:96 says
 * MD3's springs have no honest approximation here and the béziers are the part
 * that crosses over; inventing a stiffness and a damping to sit beside that
 * would be inventing a number the design system does not have.  What this does
 * instead is exponential smoothing towards the target, which is frame-rate
 * independent — the same distance is covered per second at 60 Hz and at 144 —
 * and has one constant with a meaning you can say out loud: how much of the
 * remaining distance is closed in a second.
 *
 * **Only transform and opacity are ever animated.**  The pointer moves a
 * custom property; the composited transform is written once in CSS from it.
 * The light is one radial gradient rasterised once and then only translated —
 * the same trick, and the same three alpha stops, as the glow blobs in
 * stroke.py.
 *
 * Off entirely for `prefers-reduced-motion`, and for a coarse pointer, where
 * there is no hover to follow and this would only fight the scroll.
 */
(function (global) {
  'use strict';

  var CTS = (global.CTS = global.CTS || {});

  /* How far it leans at the very corner, and how far it comes up.  Small on
   * purpose: this is a panel catching the light, not a card flipping over. */
  var TILT_DEG = 4;
  var LIFT_PX = 8;

  /* The lag.  How much of the distance left is closed in one second — 0.9 means
   * the card is nine tenths of the way there after a second, which reads as
   * "following" rather than "attached to" the pointer. */
  var CLOSED_PER_SECOND = 0.9995;

  /* Anything below this is close enough to stopped, and the loop can end. */
  var AT_REST = 0.0015;

  var lifts = [];
  var running = 0;
  var lastFrame = 0;

  function clamp(value) {
    return Math.max(-1, Math.min(1, value));
  }

  function Lift(node) {
    this.node = node;
    this.x = this.y = this.on = 0;
    this.toX = this.toY = this.toOn = 0;
    this.moving = false;
    node.setAttribute('data-lift', '');
    this.colour();
    this.bind();
  }

  /* The light behind a card is the colour that card's own place on the page
   * gives it, off the same ramp as everything else here: blue at the top, red
   * at two fifths, yellow a little below, green at the bottom.  Read once, and
   * again when the page changes shape — not on every frame, because it cannot
   * change while you are only moving the pointer. */
  Lift.prototype.colour = function () {
    if (!CTS.rampColour) return;
    var box = this.node.getBoundingClientRect();
    var top = global.scrollY || global.pageYOffset || 0;
    var height = Math.max(1, document.documentElement.scrollHeight);
    var rgb = CTS.rampColour((box.top + top + box.height / 2) / height);
    this.node.style.setProperty('--lift-rgb', rgb.join(', '));
  };

  Lift.prototype.wake = function () {
    if (this.moving) return;
    this.moving = true;
    this.node.style.willChange = 'transform';
    running += 1;
    if (running === 1) {
      lastFrame = 0;
      requestAnimationFrame(tick);
    }
  };

  Lift.prototype.rest = function () {
    if (!this.moving) return;
    this.moving = false;
    this.node.style.willChange = '';
    running -= 1;
  };

  Lift.prototype.aim = function (event) {
    var box = this.node.getBoundingClientRect();
    if (!box.width || !box.height) return;
    this.toX = clamp(((event.clientX - box.left) / box.width) * 2 - 1);
    this.toY = clamp(((event.clientY - box.top) / box.height) * 2 - 1);
    this.toOn = 1;
    this.wake();
  };

  Lift.prototype.step = function (seconds) {
    /* 1 - (1 - closed)^t : the same fraction of the distance per second at any
     * frame rate, so a slow frame does not make the card lag further behind. */
    var k = 1 - Math.pow(1 - CLOSED_PER_SECOND, seconds);
    this.x += (this.toX - this.x) * k;
    this.y += (this.toY - this.y) * k;
    this.on += (this.toOn - this.on) * k;

    var style = this.node.style;
    style.setProperty('--lift-x', this.x.toFixed(4));
    style.setProperty('--lift-y', this.y.toFixed(4));
    style.setProperty('--lift-on', this.on.toFixed(4));

    if (
      Math.abs(this.toX - this.x) < AT_REST &&
      Math.abs(this.toY - this.y) < AT_REST &&
      Math.abs(this.toOn - this.on) < AT_REST
    ) {
      /* Land exactly, so a card that has been left alone is not holding a
       * transform a thousandth of a degree off nothing. */
      this.x = this.toX;
      this.y = this.toY;
      this.on = this.toOn;
      style.setProperty('--lift-x', this.x.toFixed(4));
      style.setProperty('--lift-y', this.y.toFixed(4));
      style.setProperty('--lift-on', this.on.toFixed(4));
      this.rest();
    }
  };

  Lift.prototype.bind = function () {
    var self = this;
    this.node.addEventListener('pointerenter', function (event) {
      if (event.pointerType === 'touch') return;
      self.aim(event);
    });
    this.node.addEventListener('pointermove', function (event) {
      if (event.pointerType === 'touch') return;
      self.aim(event);
    });
    var away = function () {
      self.toX = self.toY = self.toOn = 0;
      self.wake();
    };
    this.node.addEventListener('pointerleave', away);
    this.node.addEventListener('pointercancel', away);
    /* Keyboard users get the same light: a card holding the focus is lit, and
     * it does not lean, because nothing is pointing at a corner of it. */
    this.node.addEventListener(
      'focusin',
      function () {
        self.toX = self.toY = 0;
        self.toOn = 1;
        self.wake();
      },
      true
    );
    this.node.addEventListener('focusout', away, true);
  };

  /* One loop for every card, not one each: a dozen timers all asking the same
   * question is a dozen chances to answer it at a different moment. */
  function tick(now) {
    var seconds = lastFrame ? Math.min(0.05, (now - lastFrame) / 1000) : 1 / 60;
    lastFrame = now;
    for (var i = 0; i < lifts.length; i++) {
      if (lifts[i].moving) lifts[i].step(seconds);
    }
    if (running > 0) requestAnimationFrame(tick);
    else lastFrame = 0;
  }

  function start() {
    var fine = !global.matchMedia || global.matchMedia('(hover: hover)').matches;
    var still =
      global.matchMedia && global.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (!fine || still) return;

    var nodes = document.querySelectorAll('.feature__figure, .card--filled');
    Array.prototype.forEach.call(nodes, function (node) {
      lifts.push(new Lift(node));
    });
    if (!lifts.length) return;

    /* The colour depends on how far down the page a card sits, and the page
     * changes length when the images land and when the window is resized. */
    var recolour = function () {
      lifts.forEach(function (lift) {
        lift.colour();
      });
    };
    global.addEventListener('load', recolour);
    global.addEventListener('resize', recolour, { passive: true });
    global.addEventListener('cts:language', recolour);
    CTS.recolourCards = recolour;
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }

  CTS.Lift = Lift;
})(window);
