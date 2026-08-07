/* The text of this site, and which language it is in.
 *
 * There is one copy of every page.  None of the prose is in it: every string
 * lives in i18n/en_US.json and i18n/uk_UA.json, and this puts the chosen one
 * into the markup.  A page that shipped its own English and read Ukrainian
 * from a file would be two sources for one sentence, which is how the two
 * language trees drifted apart in the first place.
 *
 * Three attributes, and nothing else to learn:
 *
 *     data-i18n="a.b.c"                    the element's own content
 *     data-i18n-attr="alt:a.b; src:a.c"    one or more of its attributes
 *     data-switch-lang="uk_UA"             on a button, switches to it
 *
 * The choice is kept in sessionStorage, so it lasts as long as the tab and no
 * longer: a link somebody sends is the site in its own default, English, and
 * not in whatever the last reader happened to pick.
 *
 * A string may carry inline markup — <strong>, <code>, <a href> — because the
 * sentences on this site do.  It is written into innerHTML, which is safe for
 * exactly one reason: every string comes out of a file in this repository.
 * Nothing typed by a reader ever reaches here.
 */
(function (global) {
  'use strict';

  var CTS = (global.CTS = global.CTS || {});
  var root = document.documentElement;

  var STORE = 'cts-lang';
  var DEFAULT = 'en_US';
  var KNOWN = ['en_US', 'uk_UA'];

  /* Long enough that a slow file arrives before the page is revealed, short
   * enough that a file that will never arrive does not hold it hostage. */
  var REVEAL_AFTER_MS = 2000;

  var strings = null;

  /* ------------------------------------------------------------- the choice */

  function remembered() {
    try {
      var chosen = sessionStorage.getItem(STORE);
      if (KNOWN.indexOf(chosen) !== -1) return chosen;
    } catch (error) {
      /* Private browsing, a disabled store — English, then. */
    }
    return DEFAULT;
  }

  function remember(locale) {
    try {
      sessionStorage.setItem(STORE, locale);
    } catch (error) {}
  }

  /* --------------------------------------------------------------- lookup */

  function lookup(key) {
    var value = strings;
    var parts = String(key).split('.');
    for (var i = 0; i < parts.length; i++) {
      if (value === null || typeof value !== 'object') return null;
      value = value[parts[i]];
    }
    return typeof value === 'string' ? value : null;
  }

  /* ------------------------------------------------------------- applying */

  function applyContent(element) {
    var value = lookup(element.getAttribute('data-i18n'));
    if (value === null) return;
    if (element.tagName === 'TITLE') element.textContent = value;
    else element.innerHTML = value;
  }

  function applyAttributes(element) {
    var pairs = element.getAttribute('data-i18n-attr').split(';');
    for (var i = 0; i < pairs.length; i++) {
      var pair = pairs[i].trim();
      if (!pair) continue;
      var split = pair.indexOf(':');
      if (split === -1) continue;
      var value = lookup(pair.slice(split + 1).trim());
      if (value !== null) element.setAttribute(pair.slice(0, split).trim(), value);
    }
  }

  function apply(locale) {
    root.setAttribute('data-lang', locale);
    root.lang = lookup('lang.code') || 'en';

    var content = document.querySelectorAll('[data-i18n]');
    for (var i = 0; i < content.length; i++) applyContent(content[i]);

    var attributes = document.querySelectorAll('[data-i18n-attr]');
    for (var j = 0; j < attributes.length; j++) applyAttributes(attributes[j]);

    var buttons = document.querySelectorAll('[data-switch-lang]');
    for (var k = 0; k < buttons.length; k++) {
      var button = buttons[k];
      var mine = button.getAttribute('data-switch-lang') === locale;
      button.setAttribute('aria-current', mine ? 'true' : 'false');
      /* The one you are already reading is not a thing to press. */
      if (button.tagName === 'BUTTON') button.disabled = mine;
    }

    reveal();
    global.dispatchEvent(new CustomEvent('cts:language', { detail: { locale: locale } }));
  }

  function reveal() {
    root.removeAttribute('data-i18n-pending');
  }

  /* The one string that cannot live in the file, because it is what is said
   * when the file is the thing that did not arrive.  Both languages, because
   * at this point there is no telling which one the reader has. */
  function explain() {
    reveal();
    var main = document.getElementById('main') || document.body;
    var note = document.createElement('div');
    note.className = 'container section';
    note.innerHTML =
      '<p class="lede" lang="en">The text of this page is in <code>i18n/' +
      '</code> and could not be loaded. Opening the site from a file:// path' +
      ' does this; serve the <code>site/</code> directory over http instead.</p>' +
      '<p class="lede" lang="uk">Текст цієї сторінки лежить у <code>i18n/</code>' +
      ' і не завантажився. Так буває, коли сайт відкрито як file://; віддайте' +
      ' теку <code>site/</code> через http.</p>' +
      '<p class="lede"><a href="https://github.com/fand1l/cts_gl">' +
      'github.com/fand1l/cts_gl</a></p>';
    main.insertBefore(note, main.firstChild);
  }

  /* ---------------------------------------------------------------- go */

  function load(locale) {
    /* The bootstrap in <head> starts the fetch before this file is parsed, so
     * the first one is already in flight by the time we are asked for it. */
    var pending = CTS.strings;
    CTS.strings = null;
    if (pending) return pending;
    return fetch('i18n/' + locale + '.json').then(function (answer) {
      if (!answer.ok) throw new Error(answer.status + ' for ' + locale);
      return answer.json();
    });
  }

  function switchTo(locale) {
    if (KNOWN.indexOf(locale) === -1 || locale === CTS.i18n.locale) return;
    root.setAttribute('data-i18n-pending', '');
    var timer = setTimeout(reveal, REVEAL_AFTER_MS);
    load(locale).then(
      function (loaded) {
        clearTimeout(timer);
        strings = loaded;
        CTS.i18n.locale = locale;
        remember(locale);
        apply(locale);
      },
      function () {
        clearTimeout(timer);
        reveal();
      }
    );
  }

  function bindSwitch() {
    document.addEventListener('click', function (event) {
      var button = event.target.closest ? event.target.closest('[data-switch-lang]') : null;
      if (!button) return;
      event.preventDefault();
      switchTo(button.getAttribute('data-switch-lang'));
    });
  }

  var locale = remembered();
  CTS.i18n = {
    locale: locale,
    t: lookup,
    switchTo: switchTo,
    ready: null,
  };

  var guard = setTimeout(reveal, REVEAL_AFTER_MS);
  CTS.i18n.ready = load(locale).then(
    function (loaded) {
      clearTimeout(guard);
      strings = loaded;
      var start = function () {
        apply(locale);
        bindSwitch();
      };
      if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
      } else {
        start();
      }
    },
    function (error) {
      clearTimeout(guard);
      if (global.console) global.console.error('the strings did not load:', error);
      var fail = function () {
        explain();
      };
      if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', fail);
      } else {
        fail();
      }
    }
  );
})(window);
