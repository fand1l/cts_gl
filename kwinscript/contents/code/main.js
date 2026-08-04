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

var DBUS_SERVICE = "io.github.fand1l.CircleToSearch";
var DBUS_PATH = "/io/github/fand1l/CircleToSearch";
var DBUS_INTERFACE = "io.github.fand1l.CircleToSearch";

/* Must match OVERLAY_WINDOW_TITLE / GLOW_WINDOW_TITLE in the Python package. */
var OVERLAY_CAPTION = "Circle to Search Overlay";
var GLOW_CAPTION = "Circle to Search Glow";

/* How long to keep looking for the overlay window after a trigger, in ms. */
var OVERLAY_WATCH_MS = 5000;
var OVERLAY_WATCH_INTERVAL = 200;

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
    glow: true,
    disableInFullscreen: true,
    shortcut: "Meta+Shift+L"
};

var state = {
    lastX: -1,
    lastY: -1,
    idleTicks: 0,
    slow: false,
    stroke: null,          /* {x0, y0, sx, sy, amp} */
    offDiagonal: 0,
    reversalTimes: [],
    lastTriggerAt: 0,
    watchUntil: 0
};

/*
 * Cursor glow.  The daemon cannot know where the pointer is, so once a swing
 * has been recognised the script starts feeding it the position — but only
 * then: while nothing is happening there is still not a single D-Bus call per
 * tick, which is the whole point of the polling design.  A shake lasts well
 * under a second, so this costs a couple of dozen calls per gesture.
 */
var glow = {
    active: false,
    lastX: -1,
    lastY: -1,
    lastCount: 0,
    lastSentAt: 0
};

/* Re-reading the active window on every tick is wasteful; half a second of
 * staleness is invisible for "did the user just alt-tab into a game". */
var fullScreenCache = {
    value: false,
    at: 0
};

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
    cfg.glow = readBoolean("glow", true);
    cfg.disableInFullscreen = readBoolean("disableInFullscreen", true);
    cfg.shortcut = String(readConfig("shortcut", "Meta+Shift+L"));

    print("circle-to-search: config enabled=" + cfg.enabled
        + " reversals=" + cfg.reversals
        + " windowMs=" + cfg.windowMs
        + " minAmplitudePx=" + cfg.minAmplitudePx
        + " angleTolerance=" + cfg.angleTolerance
        + " pollMs=" + cfg.pollMs
        + " cooldownMs=" + cfg.cooldownMs
        + " minStepPx=" + cfg.minStepPx
        + " glow=" + cfg.glow
        + " disableInFullscreen=" + cfg.disableInFullscreen);
}

/* --------------------------------------------------- fullscreen and glow */

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

function reportGlow(x, y, count) {
    if (!cfg.glow) {
        return;
    }
    var now = Date.now();
    if (count === glow.lastCount
        && now - glow.lastSentAt < 40
        && Math.abs(x - glow.lastX) < 3
        && Math.abs(y - glow.lastY) < 3) {
        return;
    }
    glow.active = true;
    glow.lastCount = count;
    glow.lastX = x;
    glow.lastY = y;
    glow.lastSentAt = now;
    callDBus(DBUS_SERVICE, DBUS_PATH, DBUS_INTERFACE, "GestureProgress",
             Math.round(x) | 0, Math.round(y) | 0, count | 0, cfg.reversals | 0,
             screenNameAt(x, y));
}

function endGlow() {
    if (!glow.active) {
        return;
    }
    glow.active = false;
    glow.lastCount = 0;
    if (cfg.glow) {
        callDBus(DBUS_SERVICE, DBUS_PATH, DBUS_INTERFACE, "GestureEnded");
    }
}

/* --------------------------------------------------------------- detection */

function sign(value) {
    if (value > 0) {
        return 1;
    }
    return value < 0 ? -1 : 0;
}

/*
 * A movement counts as "diagonal" when both axes move more than the noise floor
 * and the direction is within `angleTolerance` degrees of 45°.  Everything else
 * (straight lines, tiny jitter, moving to a menu) is ignored, which is what
 * keeps normal mouse use from triggering the overlay.
 */
function isDiagonal(dx, dy) {
    var ax = Math.abs(dx);
    var ay = Math.abs(dy);
    if (ax < cfg.minStepPx || ay < cfg.minStepPx) {
        return false;
    }
    var angle = Math.atan2(ay, ax) * 180 / Math.PI;   /* 0..90 */
    return Math.abs(angle - 45) <= cfg.angleTolerance;
}

function resetDetector() {
    state.stroke = null;
    state.offDiagonal = 0;
    state.reversalTimes.length = 0;
    endGlow();
}

function pruneReversals(now) {
    while (state.reversalTimes.length > 0 && now - state.reversalTimes[0] > cfg.windowMs) {
        state.reversalTimes.shift();
    }
}

function beginStroke(x, y, sx, sy) {
    state.stroke = { x0: x, y0: y, sx: sx, sy: sy, amp: 0 };
}

function distance(x0, y0, x1, y1) {
    var dx = x1 - x0;
    var dy = y1 - y0;
    return Math.sqrt(dx * dx + dy * dy);
}

function poll() {
    if (!cfg.enabled) {
        return;
    }
    if (cfg.disableInFullscreen && activeIsFullScreen()) {
        /* A game has the focus: stop looking at the cursor entirely.  The
         * global shortcut still works, because pressing it is deliberate. */
        if (state.stroke !== null || state.reversalTimes.length > 0) {
            resetDetector();
        }
        endGlow();
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

    if (!isDiagonal(dx, dy)) {
        state.offDiagonal += 1;
        if (state.offDiagonal > 3) {
            /* Normal pointer travel: forget any half-finished shake. */
            resetDetector();
        }
        return;
    }
    state.offDiagonal = 0;

    var sx = sign(dx);
    var sy = sign(dy);
    var now = Date.now();

    if (state.stroke === null) {
        beginStroke(previousX, previousY, sx, sy);
        refreshGlow(x, y, now);
        return;
    }

    if (sx === state.stroke.sx && sy === state.stroke.sy) {
        var travelled = distance(state.stroke.x0, state.stroke.y0, x, y);
        if (travelled > state.stroke.amp) {
            state.stroke.amp = travelled;
        }
        refreshGlow(x, y, now);
        return;
    }

    if (sx === -state.stroke.sx && sy === -state.stroke.sy) {
        /* Both axes flipped at once: this is the turning point of a swing. */
        var amplitude = Math.max(
            state.stroke.amp,
            distance(state.stroke.x0, state.stroke.y0, previousX, previousY)
        );
        if (amplitude >= cfg.minAmplitudePx) {
            state.reversalTimes.push(now);
            pruneReversals(now);
            if (state.reversalTimes.length >= cfg.reversals) {
                fire(x, y, now);
                return;
            }
        }
        beginStroke(previousX, previousY, sx, sy);
        refreshGlow(x, y, now);
        return;
    }

    /* Only one axis changed sign — treat it as a new swing, not a reversal. */
    beginStroke(previousX, previousY, sx, sy);
    refreshGlow(x, y, now);
}

/*
 * Light the cursor up once at least one swing has been recognised, so the
 * gesture is discoverable: keep moving and the ring closes.
 */
function refreshGlow(x, y, now) {
    pruneReversals(now);
    if (state.reversalTimes.length > 0) {
        reportGlow(x, y, state.reversalTimes.length);
    } else {
        endGlow();
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
    endGlow();
    trigger(x, y);
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

function trigger(x, y) {
    var screenName = screenNameAt(x, y);
    print("circle-to-search: trigger at " + x + "," + y + " on '" + screenName + "'");
    /* `| 0` forces a 32-bit integer so the call matches the "iis" signature. */
    callDBus(DBUS_SERVICE, DBUS_PATH, DBUS_INTERFACE, "Trigger",
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

function promote(window) {
    try {
        window.keepAbove = true;
        window.noBorder = true;
        window.skipTaskbar = true;
        window.skipPager = true;
        window.skipSwitcher = true;
        if (!window.fullScreen) {
            window.fullScreen = true;
        }
        workspace.activeWindow = window;
        print("circle-to-search: promoted the overlay window");
    } catch (error) {
        print("circle-to-search: cannot promote the overlay: " + error);
    }
}

function checkForOverlay() {
    var windows = allWindows();
    for (var i = 0; i < windows.length; i += 1) {
        if (isGlow(windows[i])) {
            placeGlow(windows[i]);
        }
        if (isOverlay(windows[i])) {
            promote(windows[i]);
            stopOverlayWatch();
            return;
        }
    }
    if (Date.now() > state.watchUntil) {
        stopOverlayWatch();
    }
}

function startOverlayWatch() {
    state.watchUntil = Date.now() + OVERLAY_WATCH_MS;
    if (watchTimer !== null) {
        watchTimer.start();
    }
}

function stopOverlayWatch() {
    if (watchTimer !== null) {
        watchTimer.stop();
    }
}

function isGlow(window) {
    try {
        return !!window && window.caption === GLOW_CAPTION;
    } catch (error) {
        return false;
    }
}

/*
 * The glow is click-through and must not steal focus, so it is only placed and
 * raised — never full-screened, which would make KWin hide the panels for it.
 */
function placeGlow(window) {
    try {
        window.keepAbove = true;
        window.noBorder = true;
        window.skipTaskbar = true;
        window.skipPager = true;
        window.skipSwitcher = true;
        var output = screenAt(window.frameGeometry.x, window.frameGeometry.y);
        var geometry = output ? output.geometry : null;
        if (geometry) {
            window.frameGeometry = {
                x: geometry.x,
                y: geometry.y,
                width: geometry.width,
                height: geometry.height
            };
        }
    } catch (error) {
        print("circle-to-search: cannot place the glow: " + error);
    }
}

function onWindowAdded(window) {
    if (isOverlay(window)) {
        promote(window);
    } else if (isGlow(window)) {
        placeGlow(window);
    }
}

/* -------------------------------------------------------------- life cycle */

function init() {
    loadConfig();

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

    print("circle-to-search: KWin script started");
}

init();
