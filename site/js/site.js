/* What holds the page together.
 *
 * Three jobs, and all three are enhancements — with JavaScript off the page is
 * a finished page: the prose is prose, the commands are selectable text, the
 * screenshots are in the markup, the language switch is two ordinary links and
 * the theme still follows prefers-color-scheme.
 *
 *   1. the scroll ramp — the whole scheme regrown from where you are down the
 *      page, blue, red, yellow, green, which is the same rule the glow under
 *      the lasso uses for where you are down the screen;
 *   2. the light/dark switch, remembered;
 *   3. a copy button on the command blocks, and the two canvases started.
 */
(function (global) {
  'use strict';

  var CTS = (global.CTS = global.CTS || {});
  var root = document.documentElement;

  /* ------------------------------------------------------------- the theme */

  var STORE = 'cts-theme';

  function stored() {
    try {
      return localStorage.getItem(STORE);
    } catch (error) {
      return null;
    }
  }

  function remember(value) {
    try {
      localStorage.setItem(STORE, value);
    } catch (error) {
      /* Private browsing, a disabled store — the page works either way. */
    }
  }

  function isDark() {
    var chosen = root.getAttribute('data-theme');
    if (chosen === 'dark') return true;
    if (chosen === 'light') return false;
    return !!(global.matchMedia && global.matchMedia('(prefers-color-scheme: dark)').matches);
  }

  /* --------------------------------------------------------- the scroll ramp */

  var lastSeed = null;

  function repaintScheme() {
    if (!CTS.md3 || !CTS.rampColour) return;
    var doc = document.documentElement;
    var travel = Math.max(1, doc.scrollHeight - global.innerHeight);
    var fraction = Math.min(1, Math.max(0, (global.scrollY || global.pageYOffset || 0) / travel));
    var seed = CTS.rampColour(fraction);
    lastSeed = seed;
    CTS.md3.apply(seed, isDark());
  }

  function watchScroll() {
    var queued = false;
    var run = function () {
      queued = false;
      repaintScheme();
    };
    var ask = function () {
      if (queued) return;
      queued = true;
      requestAnimationFrame(run);
    };
    /* Passive, and the work batched into one frame: a scroll handler is not a
     * place to be reading layout on every event. */
    global.addEventListener('scroll', ask, { passive: true });
    global.addEventListener('resize', ask, { passive: true });
    repaintScheme();
  }

  function bindTheme() {
    var button = document.querySelector('[data-theme-toggle]');
    var media = global.matchMedia ? global.matchMedia('(prefers-color-scheme: dark)') : null;

    var label = function () {
      if (!button) return;
      var dark = isDark();
      button.setAttribute('aria-pressed', dark ? 'true' : 'false');
      var next = dark ? button.getAttribute('data-label-light') : button.getAttribute('data-label-dark');
      if (next) button.setAttribute('aria-label', next);
    };

    if (button) {
      button.hidden = false;
      button.addEventListener('click', function () {
        var dark = !isDark();
        root.setAttribute('data-theme', dark ? 'dark' : 'light');
        remember(dark ? 'dark' : 'light');
        label();
        if (lastSeed && CTS.md3) CTS.md3.apply(lastSeed, dark);
      });
    }

    /* Following the desktop is the default, so a desktop that changes its mind
     * while the page is open is followed too — unless the switch was used. */
    if (media && media.addEventListener) {
      media.addEventListener('change', function () {
        if (root.getAttribute('data-theme')) return;
        label();
        if (lastSeed && CTS.md3) CTS.md3.apply(lastSeed, isDark());
      });
    }
    label();
  }

  /* ------------------------------------------------------- the copy buttons */

  function bindCopy() {
    var buttons = document.querySelectorAll('[data-copy]');
    Array.prototype.forEach.call(buttons, function (button) {
      var target = document.getElementById(button.getAttribute('data-copy'));
      if (!target) return;
      button.hidden = false;

      var idle = button.querySelector('[data-copy-text]');
      var say = function (message, copied) {
        if (idle) idle.textContent = message;
        button.setAttribute('data-copied', copied ? 'true' : 'false');
        var live = document.getElementById(button.getAttribute('data-live') || '');
        if (live) live.textContent = message;
      };
      var idleText = idle ? idle.textContent : '';

      button.addEventListener('click', function () {
        var text = target.innerText.replace(/ /g, ' ');
        var done = function () {
          say(button.getAttribute('data-label-done') || idleText, true);
          setTimeout(function () {
            say(idleText, false);
          }, 2400);
        };
        /* file:// and any other non-secure context has no clipboard.  Say what
         * happened and what to do about it rather than failing silently. */
        var fallback = function () {
          var range = document.createRange();
          range.selectNodeContents(target);
          var selection = global.getSelection();
          selection.removeAllRanges();
          selection.addRange(range);
          say(button.getAttribute('data-label-select') || idleText, false);
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(done, fallback);
        } else {
          fallback();
        }
      });
    });
  }

  /* -------------------------------------------------------------- the canvases */

  function bindCanvases() {
    var hero = document.querySelector('[data-hero-canvas]');
    var title = document.getElementById('hero-title');
    if (hero && title && CTS.Hero) {
      var instance = new CTS.Hero(hero, title);
      CTS.hero = instance;
      /* Straight away, and again once the fonts have settled — the words are
       * what is being circled and they move when the typeface arrives.  Asking
       * only `fonts.ready` would be a race the drawing sometimes loses; asking
       * twice unconditionally would restart it for no reason, so the second one
       * only counts if the words really did move. */
      var drawnAround = null;
      var begin = function () {
        instance.stroke.resize();
        var box = instance.box();
        var key = [box.x, box.y, box.width, box.height].join();
        if (key === drawnAround) return;
        drawnAround = key;
        instance.demo();
      };
      begin();
      if (document.fonts && document.fonts.ready && document.fonts.ready.then) {
        document.fonts.ready.then(begin, begin);
      } else {
        setTimeout(begin, 60);
      }
      var hint = document.querySelector('[data-hero-hint]');
      if (hint) hint.hidden = false;

      /* With motion turned down the stroke is already standing on the page, so
       * there is nothing to ask for; everywhere else this is how somebody who
       * missed it — or who has no pointer to draw with — gets to see it. */
      var again = document.querySelector('[data-hero-replay]');
      if (again && !instance.stroke.reduced) {
        again.hidden = false;
        again.addEventListener('click', function () {
          instance.replay();
        });
      }
    }

    var gesture = document.querySelector('[data-gesture-canvas]');
    if (gesture && CTS.GesturePreview) CTS.gesture = new CTS.GesturePreview(gesture);
  }

  /* --------------------------------------------------------------- go */

  function start() {
    root.setAttribute('data-js', 'on');
    watchScroll();
    bindTheme();
    bindCopy();
    bindCanvases();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})(window);
