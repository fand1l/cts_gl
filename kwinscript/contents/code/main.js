/*
 * Circle to Search — KWin 6 script.
 *
 * Why this exists: on Wayland an ordinary client cannot ask for the global
 * pointer position, so the shake gesture has to be recognised inside the
 * compositor.  KWin can read `workspace.cursorPos` for free, and this script
 * does nothing else — as soon as a shake is recognised it hands the position
 * over to the Python daemon through D-Bus and gets out of the way.
 *
 * CPU budget: the KDE `plasma-cursor-eyes` widget became infamous for burning a
 * core by polling the cursor.  The rules followed here:
 *   * one QTimer, one property read per tick, no allocation in the hot path;
 *   * *never* any D-Bus traffic inside the tick — only when a shake fires;
 *   * when the cursor stops moving the timer slows down to `idlePollMs`, and
 *     the (optional) `cursorPosChanged` signal only flips a flag to wake it up.
 *
 * Debugging:
 *   journalctl --user -u plasma-kwin_wayland -f | grep -i circle
 *   or paste this file into System Settings → Window Management → KWin Scripts
 *   → "KWin Script Console" (kwin-script-console) and watch it live.
 */

"use strict";

/* Bumped whenever the script changes.  KWin keeps a loaded script running until
 * it is explicitly unloaded, so this is how you tell whether the version on
 * disk is the version in memory:
 *   journalctl --user -u plasma-kwin_wayland | grep "script started"
 */
var SCRIPT_VERSION = "1.3.0";

var DBUS_SERVICE = "io.github.fand1l.CircleToSearch";
var DBUS_PATH = "/io/github/fand1l/CircleToSearch";
var DBUS_INTERFACE = "io.github.fand1l.CircleToSearch";

/* Must match OVERLAY_WINDOW_TITLE in the Python package. */
var OVERLAY_CAPTION = "Circle to Search Overlay";

/* How long to keep looking for the overlay window after a trigger, in ms. */
var OVERLAY_WATCH_MS = 6000;
var OVERLAY_WATCH_INTERVAL = 250;

/* Re-read the configuration at least this often (ms), so a settings change
 * takes effect without logging out even if the change signal never arrives. */
var CONFIG_RELOAD_MS = 4000;

/* Slow the poll timer down after this many idle ticks (~1 s at 50 ms). */
var IDLE_TICKS = 20;
var IDLE_POLL_MS = 250;

var cfg = {
    enabled: true,
    reversals: 2,
    windowMs: 600,
    minAmplitudePx: 150,
    angleTolerance: 30,
    pollMs: 50,
    cooldownMs: 1500,
    minStepPx: 6,
    minSpeedPxPerSec: 700,
    maxCurvaturePct: 140,
    reversalTolerance: 40,
    maxAmplitudeRatio: 3,
    containmentFactor: 3,
    debug: false,
    trace: false,
    disableInFullscreen: true,
    shortcut: "Meta+Shift+L"
};

var state = {
    lastX: -1,
    lastY: -1,
    idleTicks: 0,
    slow: false,
    stroke: null,          /* the movement since the last direction change */
    lastSwing: null,       /* the previous accepted swing, for the turn test */
    reversalTimes: [],
    boxValid: false,       /* bounding box of the gesture so far */
    boxMinX: 0,
    boxMinY: 0,
    boxMaxX: 0,
    boxMaxY: 0,
    lastTriggerAt: 0,
    watchUntil: 0,
    overlayPromoted: false,
    reportedX: -1,
    reportedY: -1,
    reportedW: -1,
    reportedH: -1
};

/* Re-reading the active window on every tick is wasteful; half a second of
 * staleness is invisible for "did the user just alt-tab into a game". */
var fullScreenCache = {
    value: false,
    at: 0
};

var lastConfigSummary = "";
var lastConfigAt = 0;

var pollTimer = null;      /* kept in a global on purpose: a QTimer that goes  */
var watchTimer = null;     /* out of scope would be garbage collected          */

/* ------------------------------------------------------------------ config */

function readNumber(key, fallback) {
    var value = readConfig(key, fallback);
    var number = parseFloat(value);
    return isNaN(number) ? fallback : number;
}

function readBoolean(key, fallback) {
    var value = readConfig(key, fallback);
    if (typeof value === "boolean") {
        return value;
    }
    return String(value).toLowerCase() === "true";
}

function loadConfig() {
    cfg.enabled = readBoolean("enabled", true);
    cfg.reversals = Math.max(1, Math.round(readNumber("reversals", 2)));
    cfg.windowMs = Math.max(150, Math.round(readNumber("windowMs", 600)));
    cfg.minAmplitudePx = Math.max(10, Math.round(readNumber("minAmplitudePx", 150)));
    cfg.angleTolerance = Math.max(1, Math.min(44, Math.round(readNumber("angleTolerance", 30))));
    cfg.pollMs = Math.max(20, Math.min(200, Math.round(readNumber("pollMs", 50))));
    cfg.cooldownMs = Math.max(0, Math.round(readNumber("cooldownMs", 1500)));
    cfg.minStepPx = Math.max(1, Math.round(readNumber("minStepPx", 6)));
    cfg.minSpeedPxPerSec = Math.max(0, Math.round(readNumber("minSpeedPxPerSec", 700)));
    cfg.maxCurvaturePct = Math.max(100, Math.round(readNumber("maxCurvaturePct", 140)));
    cfg.reversalTolerance =
        Math.max(5, Math.min(90, Math.round(readNumber("reversalTolerance", 40))));
    cfg.maxAmplitudeRatio = Math.max(1.2, readNumber("maxAmplitudeRatio", 3));
    cfg.containmentFactor = Math.max(1.2, readNumber("containmentFactor", 3));
    cfg.debug = readBoolean("debug", false);
    cfg.trace = readBoolean("trace", false);
    cfg.disableInFullscreen = readBoolean("disableInFullscreen", true);
    cfg.shortcut = String(readConfig("shortcut", "Meta+Shift+L"));

    var summary = "enabled=" + cfg.enabled
        + " reversals=" + cfg.reversals
        + " windowMs=" + cfg.windowMs
        + " minAmplitudePx=" + cfg.minAmplitudePx
        + " minSpeedPxPerSec=" + cfg.minSpeedPxPerSec
        + " maxCurvaturePct=" + cfg.maxCurvaturePct
        + " angleTolerance=" + cfg.angleTolerance
        + " reversalTolerance=" + cfg.reversalTolerance
        + " pollMs=" + cfg.pollMs
        + " cooldownMs=" + cfg.cooldownMs
        + " minStepPx=" + cfg.minStepPx
        + " debug=" + cfg.debug
        + " trace=" + cfg.trace
        + " disableInFullscreen=" + cfg.disableInFullscreen;

    lastConfigAt = Date.now();
    /* The config is also re-read on a timer, so only speak up when something
     * actually changed — otherwise the journal fills with identical lines. */
    if (summary !== lastConfigSummary) {
        lastConfigSummary = summary;
        print("circle-to-search: config v" + SCRIPT_VERSION + " " + summary);
        resetDetector();
    }
}

/*
 * KWin does not re-run a script when its settings change, so without this the
 * "Detect cursor shake" checkbox had no effect on a running session: the script
 * kept using whatever it read at start-up.  `options.configChanged` is the
 * documented signal; the timed re-read is the safety net for the versions where
 * it is not emitted for script settings.
 */
function watchConfig() {
    try {
        if (typeof options !== "undefined" && options.configChanged) {
            options.configChanged.connect(function () {
                loadConfig();
            });
            print("circle-to-search: watching options.configChanged");
        }
    } catch (error) {
        print("circle-to-search: cannot watch the config: " + error);
    }
}

function maybeReloadConfig(now) {
    if (now - lastConfigAt >= CONFIG_RELOAD_MS) {
        loadConfig();
    }
}

/* ---------------------------------------------- the full screen guard */

function activeWindow() {
    try {
        if (typeof workspace.activeWindow !== "undefined") {
            return workspace.activeWindow;
        }
        if (typeof workspace.activeClient !== "undefined") {
            return workspace.activeClient;
        }
    } catch (error) {
        /* not available in this KWin version */
    }
    return null;
}

/*
 * True when the focused window is full screen — a game, a video, a
 * presentation.  Shaking the mouse in a shooter is normal, and having the
 * overlay jump in front of it is the worst possible moment for it.
 */
function activeIsFullScreen() {
    var now = Date.now();
    if (now - fullScreenCache.at < 500) {
        return fullScreenCache.value;
    }
    fullScreenCache.at = now;
    var window = activeWindow();
    fullScreenCache.value = !!(window && window.fullScreen);
    return fullScreenCache.value;
}

/* --------------------------------------------------------------- detection */
/*
 * What counts as "the user wants this" versus "the user is drawing".
 *
 * Counting direction reversals is not enough: dragging a window back and forth,
 * scribbling in a canvas, resizing from a corner and hunting through a menu all
 * reverse direction constantly, and the first version fired on all of them.
 * A deliberate shake is a much more specific thing, and every one of these is
 * cheap to measure on the samples we already collect:
 *
 *   speed        a shake is fast — drawing and dragging are not
 *   straightness a shake swing is a straight line; a drawn stroke curves
 *   antiparallel consecutive swings must come back along the same line,
 *                not merely "both axes changed sign", which allows 90°
 *   symmetry     the swings are of comparable length
 *   containment  the whole thing happens in one place instead of travelling
 *
 * Any single one of them rejects most accidental motion, and together they are
 * hard to hit by accident while still being trivial to do on purpose.  Turn on
 * `debug` to see, per swing, which test said no.
 */

function sign(value) {
    if (value > 0) {
        return 1;
    }
    return value < 0 ? -1 : 0;
}

function distance(x0, y0, x1, y1) {
    var dx = x1 - x0;
    var dy = y1 - y0;
    return Math.sqrt(dx * dx + dy * dy);
}

/* Angle of a vector against the nearest 45° diagonal, in degrees. */
function diagonalError(vx, vy) {
    var ax = Math.abs(vx);
    var ay = Math.abs(vy);
    if (ax < 1 && ay < 1) {
        return 90;
    }
    return Math.abs(Math.atan2(ay, ax) * 180 / Math.PI - 45);
}

/* Angle between two vectors, 0..180 degrees. */
function angleBetween(ax, ay, bx, by) {
    var la = Math.sqrt(ax * ax + ay * ay);
    var lb = Math.sqrt(bx * bx + by * by);
    if (la < 1 || lb < 1) {
        return 180;
    }
    var cosine = (ax * bx + ay * by) / (la * lb);
    if (cosine > 1) {
        cosine = 1;
    } else if (cosine < -1) {
        cosine = -1;
    }
    return Math.acos(cosine) * 180 / Math.PI;
}

function resetDetector() {
    state.stroke = null;
    state.lastSwing = null;
    state.reversalTimes.length = 0;
    state.boxMinX = 0;
    state.boxMinY = 0;
    state.boxMaxX = 0;
    state.boxMaxY = 0;
    state.boxValid = false;
}

function pruneReversals(now) {
    while (state.reversalTimes.length > 0 && now - state.reversalTimes[0] > cfg.windowMs) {
        state.reversalTimes.shift();
    }
    if (state.reversalTimes.length === 0) {
        state.boxValid = false;
    }
}

function strokePauseMs() {
    return Math.max(150, Math.round(cfg.windowMs / 2));
}

function beginStroke(x, y, now) {
    state.stroke = {
        x0: x,
        y0: y,
        x1: x,
        y1: y,
        t0: now,
        tLast: now,
        pathLength: 0,
        counted: false     /* already counted as a reversal while in progress */
    };
}

function extendStroke(x, y, now) {
    var stroke = state.stroke;
    stroke.pathLength += distance(stroke.x1, stroke.y1, x, y);
    stroke.x1 = x;
    stroke.y1 = y;
    stroke.tLast = now;
}

/*
 * Turn the stroke that just ended into a swing, or explain why it is not one.
 * Returns an object with `ok` plus the measurements, so `debug` can print them.
 */
function judgeStroke(stroke, now) {
    var vx = stroke.x1 - stroke.x0;
    var vy = stroke.y1 - stroke.y0;
    var displacement = Math.sqrt(vx * vx + vy * vy);
    var duration = Math.max(1, now - stroke.t0);
    var speed = displacement * 1000 / duration;
    var curvature = displacement < 1 ? 99 : stroke.pathLength / displacement;
    var diagonal = diagonalError(vx, vy);

    var reason = "";
    if (displacement < cfg.minAmplitudePx) {
        reason = "short";
    } else if (speed < cfg.minSpeedPxPerSec) {
        reason = "slow";
    } else if (curvature * 100 > cfg.maxCurvaturePct) {
        reason = "curved";
    } else if (diagonal > cfg.angleTolerance) {
        reason = "off-diagonal";
    }

    return {
        ok: reason === "",
        reason: reason,
        vx: vx,
        vy: vy,
        length: displacement,
        speed: speed,
        curvature: curvature,
        diagonal: diagonal,
        endX: stroke.x1,
        endY: stroke.y1
    };
}

function describeSwing(swing) {
    return "len=" + Math.round(swing.length)
        + " speed=" + Math.round(swing.speed)
        + " curve=" + (Math.round(swing.curvature * 100) / 100)
        + " diag=" + Math.round(swing.diagonal)
        + (swing.ok ? " ok" : " rejected:" + swing.reason);
}

/* The gesture has to stay in one place: a shake does not travel. */
function trackContainment(x, y) {
    if (!state.boxValid) {
        state.boxValid = true;
        state.boxMinX = x;
        state.boxMaxX = x;
        state.boxMinY = y;
        state.boxMaxY = y;
        return true;
    }
    state.boxMinX = Math.min(state.boxMinX, x);
    state.boxMaxX = Math.max(state.boxMaxX, x);
    state.boxMinY = Math.min(state.boxMinY, y);
    state.boxMaxY = Math.max(state.boxMaxY, y);
    var span = Math.max(state.boxMaxX - state.boxMinX, state.boxMaxY - state.boxMinY);
    return span <= cfg.minAmplitudePx * cfg.containmentFactor;
}

function forgetChain() {
    if (state.reversalTimes.length > 0) {
        state.reversalTimes.length = 0;
        }
    state.boxValid = false;
}

/*
 * Does `swing` complete a reversal against the swing before it?  Returns true
 * when that was the last one needed and the overlay should open.
 */
function considerReversal(swing, now) {
    var previous = state.lastSwing;
    if (previous === null) {
        return false;
    }

    var turn = angleBetween(previous.vx, previous.vy, swing.vx, swing.vy);
    var ratio = swing.length / Math.max(1, previous.length);
    var symmetric = ratio <= cfg.maxAmplitudeRatio && ratio >= 1 / cfg.maxAmplitudeRatio;
    var reversed = turn >= 180 - cfg.reversalTolerance;

    if (cfg.debug) {
        print("circle-to-search: turn=" + Math.round(turn)
            + " ratio=" + (Math.round(ratio * 100) / 100)
            + (reversed && symmetric ? " -> reversal" : " -> ignored"));
    }

    if (!reversed || !symmetric) {
        return false;
    }
    if (!trackContainment(swing.endX, swing.endY)) {
        /* Antiparallel, but the gesture is travelling across the screen
         * instead of happening in one place. */
        forgetChain();
        return false;
    }

    state.reversalTimes.push(now);
    pruneReversals(now);
    return state.reversalTimes.length >= cfg.reversals;
}

/*
 * A swing counts as soon as it has gone far enough, not only once it turns
 * back: the overlay should open on the last swing of the shake, not after the
 * user has already started moving somewhere else.
 */
function tryCountInProgress(now) {
    var stroke = state.stroke;
    if (stroke === null || stroke.counted || state.lastSwing === null) {
        return false;
    }
    var swing = judgeStroke(stroke, now);
    if (!swing.ok) {
        return false;
    }
    stroke.counted = true;
    if (cfg.debug) {
        print("circle-to-search: swing (in progress) " + describeSwing(swing));
    }
    return considerReversal(swing, now);
}

function finishStroke(now) {
    var swing = judgeStroke(state.stroke, now);
    var counted = state.stroke.counted;

    if (cfg.debug) {
        print("circle-to-search: swing " + describeSwing(swing));
    }

    if (!swing.ok) {
        /* A bad swing breaks the chain: half a shake plus a drawn stroke is
         * not a shake. */
        state.lastSwing = null;
        forgetChain();
        return false;
    }

    var fired = counted ? false : considerReversal(swing, now);
    state.lastSwing = swing;
    return fired;
}
/* -------------------------------------------------------------- the loop */

function poll() {
    var now = Date.now();
    maybeReloadConfig(now);

    if (!cfg.enabled) {
        if (state.stroke !== null || state.reversalTimes.length > 0) {
            resetDetector();
        }
        return;
    }
    if (cfg.disableInFullscreen && activeIsFullScreen()) {
        /* A game has the focus: stop looking at the cursor entirely.  The
         * global shortcut still works, because pressing it is deliberate. */
        if (state.stroke !== null || state.reversalTimes.length > 0) {
            resetDetector();
        }
        return;
    }

    var pos = workspace.cursorPos;
    var x = pos.x;
    var y = pos.y;

    if (x === state.lastX && y === state.lastY) {
        state.idleTicks += 1;
        if (!state.slow && state.idleTicks >= IDLE_TICKS) {
            /* Nothing is happening: back off to a quarter-second heartbeat. */
            state.slow = true;
            pollTimer.interval = IDLE_POLL_MS;
            resetDetector();
        }
        return;
    }

    var dx = x - state.lastX;
    var dy = y - state.lastY;
    var hadPrevious = state.lastX !== -1 || state.lastY !== -1;
    var previousX = state.lastX;
    var previousY = state.lastY;

    state.lastX = x;
    state.lastY = y;
    state.idleTicks = 0;
    if (state.slow) {
        state.slow = false;
        pollTimer.interval = cfg.pollMs;
        /* The jump out of idle is not a real movement sample. */
        return;
    }
    if (!hadPrevious) {
        return;
    }

    /*
     * Recording mode: print every sample so a movement that behaved wrongly on
     * a real desktop can be replayed through the same code offline.  See
     * tools/record-trace.sh and tests/replay-trace.js.
     */
    if (cfg.trace) {
        print("CTS-TRACE " + x + " " + y + " " + now);
    }

    /* Sub-pixel jitter is not movement. */
    if (Math.abs(dx) < cfg.minStepPx && Math.abs(dy) < cfg.minStepPx) {
        return;
    }

    /* The pointer stopped for a moment: whatever comes next is a new movement,
     * not a continuation.  This is a *pause* test, deliberately not a limit on
     * how long a stroke may last — a slow drag is one long swing that the speed
     * test then rejects, and cutting it in half here would hide it from that
     * test by turning it into two short ones. */
    if (state.stroke !== null && now - state.stroke.tLast > strokePauseMs()) {
        finishStroke(now);
        state.stroke = null;
    }

    if (state.stroke === null) {
        beginStroke(previousX, previousY, now);
        extendStroke(x, y, now);
        return;
    }

    var vx = state.stroke.x1 - state.stroke.x0;
    var vy = state.stroke.y1 - state.stroke.y0;

    /* Direction change = the new step points back against the stroke so far.
     * The dot product decides that without caring which axis flipped, which is
     * what keeps a 90° corner (drawing a box) from looking like a reversal. */
    if (vx * vx + vy * vy > 4 && dx * vx + dy * vy < 0) {
        var fired = finishStroke(now);
        beginStroke(previousX, previousY, now);
        extendStroke(x, y, now);
        if (fired || tryCountInProgress(now)) {
            fire(x, y, now);
            return;
        }
        return;
    }

    extendStroke(x, y, now);
    if (tryCountInProgress(now)) {
        fire(x, y, now);
        return;
    }
}

function fire(x, y, now) {
    /* lastTriggerAt === 0 means "never fired yet" — without that check the
     * cooldown would swallow the first trigger whenever the clock happens to
     * read less than cooldownMs. */
    if (state.lastTriggerAt !== 0 && now - state.lastTriggerAt < cfg.cooldownMs) {
        return;
    }
    state.lastTriggerAt = now;
    resetDetector();
    trigger(x, y, "TriggerShake");
}

/* ----------------------------------------------------------------- outputs */

function screenAt(x, y) {
    try {
        if (typeof workspace.screenAt === "function") {
            var output = workspace.screenAt({ x: x, y: y });
            if (output) {
                return output;
            }
        }
    } catch (error) {
        /* fall through to the manual search */
    }
    var screens = workspace.screens;
    if (screens) {
        for (var i = 0; i < screens.length; i += 1) {
            var geometry = screens[i].geometry;
            if (x >= geometry.x && x < geometry.x + geometry.width
                && y >= geometry.y && y < geometry.y + geometry.height) {
                return screens[i];
            }
        }
        if (screens.length > 0) {
            return screens[0];
        }
    }
    return null;
}

function screenNameAt(x, y) {
    /* KWin 6 exposes screenAt(); older versions only have the screens list. */
    try {
        if (typeof workspace.screenAt === "function") {
            var output = workspace.screenAt({ x: x, y: y });
            if (output && output.name) {
                return output.name;
            }
        }
    } catch (error) {
        /* fall through to the manual search */
    }

    var screens = workspace.screens;
    if (screens) {
        for (var i = 0; i < screens.length; i += 1) {
            var geometry = screens[i].geometry;
            if (x >= geometry.x && x < geometry.x + geometry.width
                && y >= geometry.y && y < geometry.y + geometry.height) {
                return screens[i].name;
            }
        }
        if (screens.length > 0) {
            return screens[0].name;
        }
    }
    return "";
}

/*
 * `method` is "TriggerShake" for the gesture and "Trigger" for the shortcut:
 * the daemon honours the "detect cursor shake" setting for the first and never
 * for the second, so pressing the key always works and a stale script cannot
 * defeat the checkbox.
 */
function trigger(x, y, method) {
    var screenName = screenNameAt(x, y);
    print("circle-to-search: " + (method || "Trigger") + " at " + x + "," + y
        + " on '" + screenName + "'");
    /* `| 0` forces a 32-bit integer so the call matches the "iis" signature. */
    callDBus(DBUS_SERVICE, DBUS_PATH, DBUS_INTERFACE, method || "Trigger",
             Math.round(x) | 0, Math.round(y) | 0, screenName);
    startOverlayWatch();
}

/* ------------------------------------------------------- overlay promotion */

/*
 * Insurance for the overlay: there are no Python bindings for layer-shell-qt,
 * so the daemon maps an ordinary full-screen window.  KWin puts it above the
 * panels here, which is the part a plain client cannot do for itself.
 */
function allWindows() {
    if (typeof workspace.windowList === "function") {
        return workspace.windowList();
    }
    if (typeof workspace.stackingOrder !== "undefined" && workspace.stackingOrder) {
        return workspace.stackingOrder;
    }
    if (typeof workspace.clientList === "function") {
        return workspace.clientList();
    }
    return [];
}

function isOverlay(window) {
    if (!window) {
        return false;
    }
    try {
        if (window.caption === OVERLAY_CAPTION) {
            return true;
        }
        var name = String(window.resourceName || "").toLowerCase();
        var klass = String(window.resourceClass || "").toLowerCase();
        var caption = String(window.caption || "");
        var ours = name.indexOf("circletosearch") !== -1
            || name.indexOf("circle-to-search") !== -1
            || klass.indexOf("circletosearch") !== -1
            || klass.indexOf("circle-to-search") !== -1;
        return ours && caption.indexOf("Overlay") !== -1;
    } catch (error) {
        return false;
    }
}

/*
 * Set one property, and survive it being read-only.
 *
 * This used to be a single try block around the whole promotion, which meant
 * that one unsettable property (`noBorder` and `skipSwitcher` are not writable
 * for every window type) skipped everything after it — including the fullScreen
 * assignment that actually matters.  The overlay was then left in the work
 * area: the panel stayed visible above it and the screenshot inside it was
 * drawn shifted down by the panel's height, which looks like two panels.
 */
function setProperty(window, name, value) {
    try {
        window[name] = value;
        return true;
    } catch (error) {
        if (cfg.debug) {
            print("circle-to-search: cannot set " + name + ": " + error);
        }
        return false;
    }
}

function outputFor(window) {
    try {
        var geometry = window.frameGeometry;
        var output = screenAt(geometry.x + geometry.width / 2, geometry.y + geometry.height / 2);
        return output ? output.geometry : null;
    } catch (error) {
        return null;
    }
}

function coversOutput(window, geometry) {
    try {
        var frame = window.frameGeometry;
        return frame.x <= geometry.x
            && frame.y <= geometry.y
            && frame.width >= geometry.width
            && frame.height >= geometry.height;
    } catch (error) {
        return false;
    }
}

function describeGeometry(window) {
    try {
        var frame = window.frameGeometry;
        return frame.width + "x" + frame.height + "+" + frame.x + "+" + frame.y;
    } catch (error) {
        return "?";
    }
}

function promote(window, verbose) {
    var geometry = outputFor(window);

    /* The one that matters goes first, and none of these can stop the others. */
    var full = setProperty(window, "fullScreen", true);
    setProperty(window, "keepAbove", true);
    setProperty(window, "noBorder", true);
    setProperty(window, "skipTaskbar", true);
    setProperty(window, "skipPager", true);
    setProperty(window, "skipSwitcher", true);
    setProperty(window, "onAllDesktops", true);

    /* Belt and braces: whatever the placement policy decided, the overlay has
     * to cover the whole output, panel included.  Only when it does not — a
     * window that is already full screen must not be poked, or KWin takes the
     * geometry assignment as a request to leave that state. */
    if (geometry !== null && !coversOutput(window, geometry)) {
        setProperty(window, "frameGeometry", {
            x: geometry.x,
            y: geometry.y,
            width: geometry.width,
            height: geometry.height
        });
    }

    setProperty(workspace, "activeWindow", window);
    if (verbose || cfg.debug) {
        print("circle-to-search: promoted the overlay (fullScreen=" + full
            + " geometry=" + describeGeometry(window) + ")");
    }

    reportOverlayGeometry(window);
}

/*
 * Tell the daemon where the window actually ended up.
 *
 * A Wayland client cannot ask for its own position, so if KWin leaves the
 * overlay in the work area — below the panel — it has no way of knowing, and it
 * paints the screenshot from its own top-left corner.  The picture then appears
 * shifted down by the panel's height and the live panel shows above it, which
 * looks like two panels.  With the real geometry in hand the overlay can draw
 * (and crop) at the right offset whatever the window manager decided.
 */
function reportOverlayGeometry(window) {
    var frame;
    try {
        frame = window.frameGeometry;
    } catch (error) {
        return;
    }
    var output = outputFor(window);
    var originX = output ? output.x : 0;
    var originY = output ? output.y : 0;
    var x = Math.round(frame.x - originX) | 0;
    var y = Math.round(frame.y - originY) | 0;
    var width = Math.round(frame.width) | 0;
    var height = Math.round(frame.height) | 0;

    /* The watch runs several times a second; only speak when something moved. */
    if (x === state.reportedX && y === state.reportedY
        && width === state.reportedW && height === state.reportedH) {
        return;
    }
    state.reportedX = x;
    state.reportedY = y;
    state.reportedW = width;
    state.reportedH = height;

    callDBus(DBUS_SERVICE, DBUS_PATH, DBUS_INTERFACE, "OverlayGeometry", x, y, width, height);
}

/*
 * Keep an eye on the overlay for a few seconds instead of promoting it once.
 *
 * `windowAdded` fires before the client has committed its final size, so a
 * single pass reports a geometry that is already out of date, and a compositor
 * that hands the window the work area rather than the output would never be
 * corrected.  Re-promoting is a no-op once everything is right, and the repeated
 * geometry report is what lets the daemon line its drawing up if it is not.
 */
function checkForOverlay() {
    var windows = allWindows();
    var found = false;
    for (var i = 0; i < windows.length; i += 1) {
        if (isOverlay(windows[i])) {
            found = true;
            promote(windows[i], !state.overlayPromoted);
            state.overlayPromoted = true;
            break;
        }
    }
    if (!found && state.overlayPromoted) {
        /* The overlay closed. */
        stopOverlayWatch();
        return;
    }
    if (Date.now() > state.watchUntil) {
        stopOverlayWatch();
    }
}

function startOverlayWatch() {
    state.watchUntil = Date.now() + OVERLAY_WATCH_MS;
    state.overlayPromoted = false;
    state.reportedX = -1;
    state.reportedY = -1;
    state.reportedW = -1;
    state.reportedH = -1;
    if (watchTimer !== null) {
        watchTimer.start();
    }
}

function stopOverlayWatch() {
    if (watchTimer !== null) {
        watchTimer.stop();
    }
}

function onWindowAdded(window) {
    if (isOverlay(window)) {
        promote(window, true);
        state.overlayPromoted = true;
    }
}

/* -------------------------------------------------------------- life cycle */

function init() {
    loadConfig();
    watchConfig();

    pollTimer = new QTimer();
    pollTimer.interval = cfg.pollMs;
    pollTimer.singleShot = false;
    pollTimer.timeout.connect(poll);
    pollTimer.start();

    watchTimer = new QTimer();
    watchTimer.interval = OVERLAY_WATCH_INTERVAL;
    watchTimer.singleShot = false;
    watchTimer.timeout.connect(checkForOverlay);

    /*
     * Optional: when KWin exposes cursorPosChanged we use it *only* to leave
     * idle mode.  The handler is a single assignment — deliberately not the
     * place where detection happens, because the signal can fire at the mouse's
     * full report rate (1000 Hz on a gaming mouse).
     */
    try {
        if (workspace.cursorPosChanged && typeof workspace.cursorPosChanged.connect === "function") {
            workspace.cursorPosChanged.connect(function () {
                if (state.slow) {
                    state.slow = false;
                    state.idleTicks = 0;
                    pollTimer.interval = cfg.pollMs;
                }
            });
            print("circle-to-search: using cursorPosChanged to wake up from idle");
        }
    } catch (error) {
        /* Not available in this KWin version — plain polling is enough. */
    }

    try {
        if (typeof workspace.windowAdded !== "undefined") {
            workspace.windowAdded.connect(onWindowAdded);
        } else if (typeof workspace.clientAdded !== "undefined") {
            workspace.clientAdded.connect(onWindowAdded);
        }
    } catch (error) {
        print("circle-to-search: cannot watch for new windows: " + error);
    }

    /* Fallback trigger, in case shaking is not your thing. */
    registerShortcut(
        "CircleToSearch",
        "Circle to Search: select a region",
        cfg.shortcut,
        function () {
            var pos = workspace.cursorPos;
            print("circle-to-search: shortcut pressed");
            state.lastTriggerAt = Date.now();
            trigger(pos.x, pos.y);
        }
    );

    print("circle-to-search: KWin script started (v" + SCRIPT_VERSION + ")");
}

init();
