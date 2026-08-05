/* The vertical ramp — the one rule the whole site is built on.
 *
 * In the program the colour of the glow under the lasso is read off *where the
 * pointer is*, top to bottom of the screen: blue at the top, red at about two
 * fifths, yellow a little below that, green at the bottom.  One pure function of
 * one number.  Copied here from src/circle_to_search/stroke.py (RAMP,
 * glow_colour) rather than re-invented, because it is the same rule: on the page
 * the number is where *you* are — under the stroke, and down the scroll.
 */
(function (global) {
  'use strict';

  /* (fraction of the height, colour) — stroke.py:64 */
  var RAMP = [
    [0.0, [0x42, 0x85, 0xf4]],
    [0.4, [0xea, 0x43, 0x35]],
    [0.62, [0xfb, 0xbc, 0x05]],
    [1.0, [0x34, 0xa8, 0x53]],
  ];

  /* Python rounds a tie to even and JavaScript rounds it up, and a ramp lands
   * on .5 often enough to matter: at a tenth of the way down, green comes out
   * 116.5 and the two languages disagree about the colour.  The program is the
   * one being copied, so this is the program's rounding. */
  function pyRound(value) {
    var floor = Math.floor(value);
    var rest = value - floor;
    if (rest > 0.5) return floor + 1;
    if (rest < 0.5) return floor;
    return floor % 2 === 0 ? floor : floor + 1;
  }

  /* The colour at `fraction` of the way down, 0…1.  Clamped rather than
   * wrapped: above the top is blue, below the bottom is green. */
  function rampColour(fraction) {
    var f = Math.max(0, Math.min(1, fraction || 0));
    for (var i = 0; i < RAMP.length - 1; i++) {
      var startAt = RAMP[i][0];
      var endAt = RAMP[i + 1][0];
      if (f > endAt) continue;
      var span = endAt - startAt;
      var along = span <= 0 ? 0 : (f - startAt) / span;
      var a = RAMP[i][1];
      var b = RAMP[i + 1][1];
      return [
        pyRound(a[0] + (b[0] - a[0]) * along),
        pyRound(a[1] + (b[1] - a[1]) * along),
        pyRound(a[2] + (b[2] - a[2]) * along),
      ];
    }
    return RAMP[RAMP.length - 1][1].slice();
  }

  /* The colour at height `y` on something `height` tall — the program's own
   * signature, so the stroke code reads the same as stroke.py does. */
  function glowColour(y, height) {
    if (!(height > 0)) return RAMP[0][1].slice();
    return rampColour(y / height);
  }

  global.CTS = global.CTS || {};
  global.CTS.RAMP = RAMP;
  global.CTS.rampColour = rampColour;
  global.CTS.glowColour = glowColour;
})(window);
