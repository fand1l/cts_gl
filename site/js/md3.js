/* Material 3, the same way the program does it.
 *
 * A straight port of src/circle_to_search/material.py: the tonal palettes are
 * built in CIELAB, the roles are named tones off those palettes, and colours
 * only ever pair as X and on-X.  Nothing here is a hand-picked hex.
 *
 * The program grows its scheme from the KDE accent the user chose.  A web page
 * has no such thing to read, so the seed is the *ramp* — the same blue, red,
 * yellow and green the lasso's glow takes from height on screen — sampled at
 * how far down the page you are.  Same rule, different number in it.
 *
 * The CSS ships a full scheme already (site.css, seeded blue, light and dark),
 * so a page with JavaScript off is a finished page, not a grey one.  This only
 * ever moves it.
 */
(function (global) {
  'use strict';

  /* --------------------------------------------------------------- rounding */

  /* Python's round() goes to even on a tie and JavaScript's goes up; the port
   * is checked against material.py's own output, so it has to tie the same way. */
  function pyRound(value) {
    var floor = Math.floor(value);
    var rest = value - floor;
    if (rest > 0.5) return floor + 1;
    if (rest < 0.5) return floor;
    return floor % 2 === 0 ? floor : floor + 1;
  }

  /* ------------------------------------------------------- sRGB <-> CIELAB */

  var WHITE_D65 = [0.95047, 1.0, 1.08883];

  function toLinear(channel) {
    var value = channel / 255;
    return value <= 0.04045 ? value / 12.92 : Math.pow((value + 0.055) / 1.055, 2.4);
  }

  function fromLinear(value) {
    var out = value <= 0.0031308 ? 12.92 * value : 1.055 * Math.pow(value, 1 / 2.4) - 0.055;
    return out * 255;
  }

  function rgbToLab(rgb) {
    var r = toLinear(rgb[0]);
    var g = toLinear(rgb[1]);
    var b = toLinear(rgb[2]);
    var x = 0.4124564 * r + 0.3575761 * g + 0.1804375 * b;
    var y = 0.2126729 * r + 0.7151522 * g + 0.072175 * b;
    var z = 0.0193339 * r + 0.119192 * g + 0.9503041 * b;
    var limit = Math.pow(6 / 29, 3);
    function f(t) {
      return t > limit ? Math.cbrt(t) : t / (3 * Math.pow(6 / 29, 2)) + 4 / 29;
    }
    var fx = f(x / WHITE_D65[0]);
    var fy = f(y / WHITE_D65[1]);
    var fz = f(z / WHITE_D65[2]);
    return [116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)];
  }

  /* Unclamped on purpose: the caller needs to know when it left the gamut. */
  function labToRgb(lab) {
    var fy = (lab[0] + 16) / 116;
    var fx = fy + lab[1] / 500;
    var fz = fy - lab[2] / 200;
    function g(t) {
      return t > 6 / 29 ? t * t * t : 3 * Math.pow(6 / 29, 2) * (t - 4 / 29);
    }
    var x = g(fx) * WHITE_D65[0];
    var y = g(fy) * WHITE_D65[1];
    var z = g(fz) * WHITE_D65[2];
    return [
      fromLinear(3.2404542 * x - 1.5371385 * y - 0.4985314 * z),
      fromLinear(-0.969266 * x + 1.8760108 * y + 0.041556 * z),
      fromLinear(0.0556434 * x - 0.2040259 * y + 1.0572252 * z),
    ];
  }

  function inGamut(rgb) {
    return rgb.every(function (part) {
      return part >= -0.5 && part <= 255.5;
    });
  }

  function toneOf(rgb) {
    return rgbToLab(rgb)[0];
  }

  function hueChromaOf(rgb) {
    var lab = rgbToLab(rgb);
    var hue = ((Math.atan2(lab[2], lab[1]) * 180) / Math.PI + 360) % 360;
    return [hue, Math.hypot(lab[1], lab[2])];
  }

  /* One step of a tonal palette: this hue, at this lightness.  The chroma asked
   * for is the most that will be used, not a promise — near either end of the
   * tone axis there is very little room for colour, and asking for it anyway
   * would land outside sRGB, be clipped channel by channel, and come back a
   * different hue.  So it is searched down to whatever fits. */
  var toneCache = Object.create(null);

  function tonal(hue, chroma, tone) {
    var key = hue + '|' + chroma + '|' + tone;
    var cached = toneCache[key];
    if (cached) return cached;

    var t = Math.max(0, Math.min(100, tone));
    var radians = (hue * Math.PI) / 180;
    function at(c) {
      return labToRgb([t, c * Math.cos(radians), c * Math.sin(radians)]);
    }
    var wanted = at(chroma);
    if (!inGamut(wanted)) {
      var low = 0;
      var high = chroma;
      for (var i = 0; i < 20; i++) {
        var middle = (low + high) / 2;
        if (inGamut(at(middle))) low = middle;
        else high = middle;
      }
      wanted = at(low);
    }
    var out = wanted.map(function (part) {
      return Math.max(0, Math.min(255, pyRound(part)));
    });
    toneCache[key] = out;
    return out;
  }

  /* ------------------------------------------------------------- the roles */

  var CHROMA_PRIMARY_MIN = 48.0;
  var CHROMA_SECONDARY = 16.0;
  var CHROMA_TERTIARY = 24.0;
  var HUE_TERTIARY_TURN = 60.0;
  var CHROMA_NEUTRAL = 4.0;
  var CHROMA_NEUTRAL_VARIANT = 8.0;
  var HUE_ERROR = 25.0;
  var CHROMA_ERROR = 84.0;
  var CHROMA_ACHROMATIC = 2.0;

  /* Breeze blue, for when the palette has nothing to say. */
  var FALLBACK_SEED = [61, 174, 233];

  /* role -> [palette, tone in the light scheme, tone in the dark scheme].
   * Read as a table on purpose: this is the only place a colour is decided,
   * and it is decided by naming a palette and a tone. */
  var ROLES = {
    primary: ['accent', 40, 80],
    'on-primary': ['accent', 100, 20],
    'primary-container': ['accent', 90, 30],
    'on-primary-container': ['accent', 10, 90],
    secondary: ['second', 40, 80],
    'on-secondary': ['second', 100, 20],
    'secondary-container': ['second', 90, 30],
    'on-secondary-container': ['second', 10, 90],
    tertiary: ['third', 40, 80],
    'on-tertiary': ['third', 100, 20],
    error: ['wrong', 40, 80],
    'on-error': ['wrong', 100, 20],
    surface: ['neutral', 98, 6],
    'on-surface': ['neutral', 10, 90],
    'surface-variant': ['variant', 90, 30],
    'on-surface-variant': ['variant', 30, 80],
    'surface-container-lowest': ['neutral', 100, 4],
    'surface-container-low': ['neutral', 96, 10],
    'surface-container': ['neutral', 94, 12],
    'surface-container-high': ['neutral', 92, 17],
    'surface-container-highest': ['neutral', 90, 22],
    'inverse-surface': ['neutral', 20, 90],
    'inverse-on-surface': ['neutral', 95, 20],
    outline: ['variant', 50, 60],
    'outline-variant': ['variant', 80, 30],
    scrim: ['neutral', 0, 0],
  };

  var ROLE_NAMES = Object.keys(ROLES);

  function schemeFor(seed, dark) {
    var hc = hueChromaOf(seed);
    var hue = hc[0];
    var chroma = hc[1];
    if (chroma < CHROMA_ACHROMATIC) hue = hueChromaOf(FALLBACK_SEED)[0];

    var palettes = {
      accent: [hue, Math.max(CHROMA_PRIMARY_MIN, chroma)],
      second: [hue, CHROMA_SECONDARY],
      third: [(hue + HUE_TERTIARY_TURN) % 360, CHROMA_TERTIARY],
      neutral: [hue, CHROMA_NEUTRAL],
      variant: [hue, CHROMA_NEUTRAL_VARIANT],
      wrong: [HUE_ERROR, CHROMA_ERROR],
    };

    var scheme = { dark: !!dark };
    ROLE_NAMES.forEach(function (role) {
      var spec = ROLES[role];
      var palette = palettes[spec[0]];
      scheme[role] = tonal(palette[0], palette[1], dark ? spec[2] : spec[1]);
    });
    return scheme;
  }

  /* -------------------------------------------------------------- contrast */

  function relativeLuminance(rgb) {
    return (
      0.2126 * toLinear(rgb[0]) + 0.7152 * toLinear(rgb[1]) + 0.0722 * toLinear(rgb[2])
    );
  }

  /* The WCAG ratio between two colours, 1 to 21 — how the X/on-X pairs above are
   * checked to be worth their name. */
  function contrast(one, two) {
    var a = relativeLuminance(one);
    var b = relativeLuminance(two);
    var light = Math.max(a, b);
    var dark = Math.min(a, b);
    return (light + 0.05) / (dark + 0.05);
  }

  function hex(rgb) {
    return (
      '#' +
      rgb
        .map(function (part) {
          return ('0' + part.toString(16)).slice(-2);
        })
        .join('')
    );
  }

  /* ------------------------------------------------------------ onto the page */

  var applied = null;

  /* Write a scheme into the custom properties site.css already declares.  The
   * names are material.py's own role names, with underscores as hyphens. */
  function apply(seed, dark, target) {
    var key = seed.join(',') + (dark ? '|d' : '|l');
    if (key === applied) return;
    applied = key;
    var scheme = schemeFor(seed, dark);
    var style = (target || document.documentElement).style;
    ROLE_NAMES.forEach(function (role) {
      style.setProperty('--' + role, hex(scheme[role]));
    });
  }

  global.CTS = global.CTS || {};
  global.CTS.md3 = {
    ROLE_NAMES: ROLE_NAMES,
    FALLBACK_SEED: FALLBACK_SEED,
    schemeFor: schemeFor,
    tonal: tonal,
    toneOf: toneOf,
    hueChromaOf: hueChromaOf,
    contrast: contrast,
    hex: hex,
    apply: apply,
  };
})(window);
