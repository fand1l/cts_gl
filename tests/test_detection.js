/*
 * Runs the real kwinscript/contents/code/main.js against a fake KWin API and
 * feeds it synthetic cursor paths, so the shake detection can be checked
 * without a Plasma session.
 *
 * Run with:  node tests/test_detection.js
 *
 * The same paths can be replayed inside KWin itself: paste main.js into
 * System Settings -> Window Management -> KWin Scripts -> Script Console.
 */
const fs = require("fs");
const vm = require("vm");

const path = require("path");

const SOURCE = path.join(__dirname, "..", "kwinscript", "contents", "code", "main.js");

function makeSandbox(config, options) {
    const timers = [];
    const calls = [];
    const cursor = { x: 0, y: 0 };
    const active = { fullScreen: (options && options.fullScreen) || false };
    let now = 0;

    function QTimerCtor() {
        const timer = {
            interval: 0,
            singleShot: false,
            running: false,
            handlers: [],
            start() { this.running = true; },
            stop() { this.running = false; },
        };
        timer.timeout = { connect: (fn) => timer.handlers.push(fn) };
        timers.push(timer);
        return timer;
    }

    const sandbox = {
        QTimer: QTimerCtor,
        workspace: {
            get cursorPos() { return { x: cursor.x, y: cursor.y }; },
            screens: [
                { name: "eDP-1", geometry: { x: 0, y: 0, width: 2560, height: 1440 } },
                { name: "HDMI-A-1", geometry: { x: 2560, y: 0, width: 1920, height: 1080 } },
            ],
            windowList: () => [],
            windowAdded: { connect: () => {} },
            activeWindow: active,
        },
        readConfig: (key, fallback) => (key in config ? config[key] : fallback),
        /* `config` is the same object the test holds, so a test can change a
         * setting while the script is running — which is exactly what the tray
         * checkbox does through kwriteconfig6 + reconfigure. */
        callDBus: (...args) => calls.push(args),
        registerShortcut: () => {},
        print: () => {},
        Date: {
            now: () => now,
        },
        Math: Math,
        String: String,
        Number: Number,
        parseFloat: parseFloat,
        isNaN: isNaN,
    };
    sandbox.global = sandbox;

    return {
        sandbox,
        calls,
        timers,
        config,
        setNow: (value) => { now = value; },
        getNow: () => now,
        setFullScreen: (value) => { active.fullScreen = value; },
        moveTo: (x, y) => { cursor.x = x; cursor.y = y; },
    };
}

function run(name, config, path, expectTriggers, options) {
    const harness = makeSandbox(config, options);
    const context = vm.createContext(harness.sandbox);
    vm.runInContext(fs.readFileSync(SOURCE, "utf8"), context, { filename: SOURCE });

    const pollTimer = harness.timers[0];
    if (!pollTimer || pollTimer.handlers.length === 0) {
        console.log(`FAIL  ${name}: the script never created a poll timer`);
        return false;
    }
    const tick = pollTimer.handlers[0];

    for (const step of path) {
        harness.moveTo(step.x, step.y);
        harness.setNow(step.t);
        tick();
    }

        /* A gesture arrives as TriggerShake so the daemon can honour the "detect
     * cursor shake" setting itself; a shortcut press stays a plain Trigger. */
    const triggers = harness.calls.filter((c) => c[3] === "TriggerShake");
    harness.triggers = triggers;
    const ok = triggers.length === expectTriggers;
    console.log(
        `${ok ? "PASS" : "FAIL"}  ${name}: ${triggers.length} trigger(s), expected ${expectTriggers}` +
        (triggers.length ? ` -> ${JSON.stringify(triggers[0].slice(3))}` : "")
    );
    if (triggers.length) {
        const args = triggers[0];
        if (args[0] !== "io.github.fand1l.CircleToSearch"
            || args[3] !== "TriggerShake"
            || !Number.isInteger(args[4])
            || !Number.isInteger(args[5])
            || typeof args[6] !== "string") {
            console.log(`FAIL  ${name}: bad D-Bus call ${JSON.stringify(args)}`);
            return false;
        }
    }
    lastHarness = harness;
    return ok;
}

let lastHarness = null;

/* ---- synthetic paths ---------------------------------------------------- */

/* One diagonal swing sampled every 50 ms. */
function swing(from, to, t0, steps, dt) {
    const path = [];
    for (let i = 1; i <= steps; i += 1) {
        path.push({
            x: Math.round(from.x + (to.x - from.x) * (i / steps)),
            y: Math.round(from.y + (to.y - from.y) * (i / steps)),
            t: t0 + i * dt,
        });
    }
    return path;
}

function shake(swings, amplitude, t0, stepsPerSwing, dt) {
    /* Down-right then up-left, repeatedly, at 45 degrees. */
    let path = [{ x: 1000, y: 500, t: t0 }];
    let point = { x: 1000, y: 500 };
    let time = t0;
    for (let i = 0; i < swings; i += 1) {
        const sign = i % 2 === 0 ? 1 : -1;
        const next = { x: point.x + sign * amplitude, y: point.y + sign * amplitude };
        const leg = swing(point, next, time, stepsPerSwing, dt);
        path = path.concat(leg);
        point = next;
        time = leg[leg.length - 1].t;
    }
    return path;
}

function straight(distance, t0, steps, dt) {
    return swing({ x: 200, y: 700 }, { x: 200 + distance, y: 700 }, t0, steps, dt);
}

function slowDrift(t0, steps, dt) {
    const path = [];
    for (let i = 1; i <= steps; i += 1) {
        path.push({ x: 400 + i * 2, y: 400 + i * 2, t: t0 + i * dt });
    }
    return path;
}

/* ---- movements that must NOT be mistaken for a shake --------------------- */

/* Drawing: a wavy, slow, curved line. */
function drawing(t0, points, dt) {
    const path = [];
    for (let i = 1; i <= points; i += 1) {
        path.push({
            x: Math.round(600 + i * 6 + 40 * Math.sin(i / 2)),
            y: Math.round(500 + 40 * Math.cos(i / 2)),
            t: t0 + i * dt,
        });
    }
    return path;
}

/* Scribbling: fast but curly and going nowhere in particular. */
function scribble(t0, points, dt) {
    const path = [];
    let seed = 7;
    const random = () => {
        seed = (seed * 1103515245 + 12345) % 2147483648;
        return seed / 2147483648;
    };
    let x = 900;
    let y = 500;
    for (let i = 1; i <= points; i += 1) {
        x += (random() - 0.5) * 120;
        y += (random() - 0.5) * 120;
        path.push({ x: Math.round(x), y: Math.round(y), t: t0 + i * dt });
    }
    return path;
}

/* Dragging a window diagonally down and back up, at a human drag speed. */
function slowDrag(t0, swings, amplitude, dt, stepsPerSwing) {
    return shake(swings, amplitude, t0, stepsPerSwing, dt);
}

/* Drawing a circle: constant reversals on each axis, never antiparallel. */
function circle(t0, points, radius, dt) {
    const path = [];
    for (let i = 1; i <= points; i += 1) {
        const angle = (i / points) * Math.PI * 2 * 2;
        path.push({
            x: Math.round(800 + radius * Math.cos(angle)),
            y: Math.round(500 + radius * Math.sin(angle)),
            t: t0 + i * dt,
        });
    }
    return path;
}

/* Resizing from a corner: diagonal, back and forth, but slow and short. */
function resizeCorner(t0, swings, dt) {
    return shake(swings, 90, t0, 6, dt);
}

/* A right angle, as in drawing a box corner: 90° turns, not reversals. */
function boxCorners(t0, dt) {
    let path = [{ x: 500, y: 500, t: t0 }];
    const corners = [
        { x: 800, y: 500 },
        { x: 800, y: 800 },
        { x: 500, y: 800 },
        { x: 500, y: 500 },
    ];
    let from = { x: 500, y: 500 };
    let time = t0;
    for (const corner of corners) {
        const leg = swing(from, corner, time, 5, dt);
        path = path.concat(leg);
        from = corner;
        time = leg[leg.length - 1].t;
    }
    return path;
}

/* Flicking the pointer to a far corner and back — two swings, but travelling. */
function travelAndBack(t0, dt) {
    let path = swing({ x: 100, y: 100 }, { x: 1400, y: 1400 }, t0, 6, dt);
    const last = path[path.length - 1];
    path = path.concat(swing({ x: 1400, y: 1400 }, { x: 200, y: 200 }, last.t, 6, dt));
    return path;
}

const defaults = {};
let failures = 0;

/* 3 swings = 2 reversals -> fires. */
if (!run("diagonal shake (3 swings)", defaults, shake(3, 200, 1000, 4, 40), 1)) failures += 1;

/* 2 swings = 1 reversal -> below the threshold. */
if (!run("one reversal only", defaults, shake(2, 200, 1000, 4, 40), 0)) failures += 1;

/* Big diagonal swings but far too slow: outside the 600 ms window. */
if (!run("too slow", defaults, shake(5, 200, 1000, 4, 400), 0)) failures += 1;

/* Horizontal jiggle must never fire. */
if (!run("horizontal move", defaults, straight(900, 1000, 12, 40), 0)) failures += 1;

/* Tiny diagonal wiggles below minAmplitudePx. */
if (!run("small wiggle", defaults, shake(6, 40, 1000, 2, 40), 0)) failures += 1;

/* Ordinary slow diagonal drift towards a menu. */
if (!run("slow drift", defaults, slowDrift(1000, 20, 50), 0)) failures += 1;

/* Cooldown: a long shake must still fire only once. */
if (!run("cooldown holds", defaults, shake(8, 200, 1000, 4, 40), 1)) failures += 1;

/* Disabled by configuration. */
if (!run("disabled", { enabled: false }, shake(4, 200, 1000, 4, 40), 0)) failures += 1;

/* Stricter configuration: 3 reversals needed. */
if (!run("reversals=3, 3 swings", { reversals: 3 }, shake(3, 200, 1000, 4, 40), 0)) failures += 1;
if (!run("reversals=3, 4 swings", { reversals: 3 }, shake(4, 200, 1000, 4, 40), 1)) failures += 1;

/* Screen resolution: a shake on the second output reports its name. */
const harness2 = makeSandbox({});
const ctx2 = vm.createContext(harness2.sandbox);
vm.runInContext(fs.readFileSync(SOURCE, "utf8"), ctx2, { filename: SOURCE });
const tick2 = harness2.timers[0].handlers[0];
for (const step of shake(4, 200, 1000, 4, 40).map((p) => ({ x: p.x + 2600, y: p.y, t: p.t }))) {
    harness2.moveTo(step.x, step.y);
    harness2.setNow(step.t);
    tick2();
}
const triggers2 = harness2.calls.filter((c) => c[3] === "TriggerShake");
const screenOk = triggers2.length === 1 && triggers2[0][6] === "HDMI-A-1";
console.log(`${screenOk ? "PASS" : "FAIL"}  screen name: ${JSON.stringify(triggers2.map((c) => c[6]))}`);
if (!screenOk) failures += 1;

/* A full screen window has the focus: the shake must be ignored entirely. */
/*
 * Everything the *pointer* caused.  ScriptReady is the version handshake: it
 * goes out once when the script loads and again only when the configuration
 * changes, never from the poll tick, so it is not what the "stays completely
 * silent" assertions are about.  Filtering it here rather than counting raw
 * calls keeps those assertions saying what they mean.
 */
function fromThePointer(harness) {
    return harness.calls.filter((c) => c[3] !== "ScriptReady");
}

if (!run("fullscreen blocks the shake", defaults, shake(4, 200, 1000, 4, 40), 0,
         { fullScreen: true })) failures += 1;

/* ...unless the user turned that guard off. */
if (!run("fullscreen allowed when configured", { disableInFullscreen: false },
         shake(4, 200, 1000, 4, 40), 1, { fullScreen: true })) failures += 1;

/* And nothing at all is reported to the daemon while a game is focused. */
{
    const quiet = lastHarness;
    run("fullscreen stays silent", defaults, shake(4, 200, 1000, 4, 40), 0, { fullScreen: true });
    const anyCall = fromThePointer(lastHarness).length;
    console.log(`${anyCall === 0 ? "PASS" : "FAIL"}  fullscreen sends no D-Bus at all: ${anyCall} call(s)`);
    if (anyCall !== 0) failures += 1;
    void quiet;
}

/* Ordinary pointer use must stay completely silent on D-Bus. */
run("straight move stays silent", defaults, straight(900, 1000, 12, 40), 0);
{
    const chatter = fromThePointer(lastHarness).length;
    console.log(`${chatter === 0 ? "PASS" : "FAIL"}  normal movement sends nothing: ${chatter}`);
    if (chatter !== 0) failures += 1;
}

/* The version handshake is not pointer traffic: it goes out once when the
 * script loads, and the whole point of the CPU budget is that nothing else
 * does.  Both halves are worth pinning down — that it happens at all, and that
 * moving the pointer for a second does not repeat it. */
{
    const ready = lastHarness.calls.filter((c) => c[3] === "ScriptReady");
    const once = ready.length === 1 && typeof ready[0][4] === "string" && ready[0][4].length > 0;
    console.log(`${once ? "PASS" : "FAIL"}  the script announces its version once at load: ` +
                JSON.stringify(ready.map((c) => c[4])));
    if (!once) failures += 1;
}

/* ---- false positives: none of these may open the overlay ---------------- */

if (!run("drawing a wavy line", defaults, drawing(1000, 40, 40), 0)) failures += 1;
if (!run("scribbling fast", defaults, scribble(1000, 40, 30), 0)) failures += 1;
if (!run("drawing a circle", defaults, circle(1000, 40, 200, 30), 0)) failures += 1;
if (!run("drawing box corners", defaults, boxCorners(1000, 30), 0)) failures += 1;
if (!run("resizing from a corner", defaults, resizeCorner(1000, 6, 40), 0)) failures += 1;

/* The classic one: dragging a window diagonally back and forth.  Same shape as
 * a shake, but at a human drag speed (≈300 px/s) instead of a flick. */
if (!run("dragging a window", defaults, slowDrag(1000, 5, 200, 130, 6), 0)) failures += 1;

/* Travelling across the screen and back is antiparallel and fast, but it does
 * not stay in one place. */
if (!run("flick across the screen", defaults, travelAndBack(1000, 25), 0)) failures += 1;

/* Half a shake followed by a drawn stroke must not add up to a gesture. */
{
    const half = shake(2, 200, 1000, 4, 30);
    const tail = drawing(half[half.length - 1].t, 20, 40);
    if (!run("half a shake then drawing", defaults, half.concat(tail), 0)) failures += 1;
}

/* ---- true positives: these must still work ------------------------------ */

/* A deliberate shake: ~200 px swings in ~120 ms each (≈1600 px/s). */
if (!run("deliberate shake", defaults, shake(3, 200, 1000, 4, 30), 1)) failures += 1;
if (!run("faster shake", defaults, shake(3, 250, 1000, 3, 25), 1)) failures += 1;
if (!run("bigger shake", defaults, shake(4, 320, 1000, 5, 25), 1)) failures += 1;

/* A shake that is not perfectly straight still counts. */
{
    const wobbly = shake(3, 220, 1000, 4, 30).map((p, i) => ({
        x: p.x + (i % 2 === 0 ? 6 : -6),
        y: p.y + (i % 3 === 0 ? 5 : -5),
        t: p.t,
    }));
    if (!run("shake with a wobble", defaults, wobbly, 1)) failures += 1;
}

/* The knobs really do loosen it: a user with a slow pointer lowers the speed
 * floor *and* widens the window, because the swings then take longer than the
 * default 600 ms window allows. */
if (!run("slow shake accepted when configured",
         { minSpeedPxPerSec: 200, windowMs: 2500 },
         slowDrag(1000, 4, 200, 130, 6), 1)) failures += 1;

/* Lowering only one of the two is not enough — the window still governs. */
if (!run("slow shake still rejected on speed alone", { minSpeedPxPerSec: 200 },
         slowDrag(1000, 4, 200, 130, 6), 0)) failures += 1;

/* ---- the live settings reload ------------------------------------------- */
/*
 * KWin does not re-run a script when its settings change, so the tray checkbox
 * used to have no effect at all on a running session.  Turn detection off
 * mid-flight and the script must stop firing without being restarted.
 */
{
    const harness = makeSandbox({});
    const context = vm.createContext(harness.sandbox);
    vm.runInContext(fs.readFileSync(SOURCE, "utf8"), context, { filename: SOURCE });
    const tick = harness.timers[0].handlers[0];

    const play = (path) => {
        for (const step of path) {
            harness.moveTo(step.x, step.y);
            harness.setNow(step.t);
            tick();
        }
    };

    play(shake(3, 200, 1000, 4, 30));
    const before = harness.calls.filter((c) => c[3] === "TriggerShake").length;

    /* The user unticks "Detect cursor shake". */
    harness.config.enabled = false;
    /* Nothing else happens for a few seconds; the script re-reads by itself. */
    harness.setNow(20000);
    tick();

    play(shake(3, 200, 30000, 4, 30));
    const after = harness.calls.filter((c) => c[3] === "TriggerShake").length;

    const ok = before === 1 && after === 1;
    console.log(`${ok ? "PASS" : "FAIL"}  disabling at runtime stops it: ` +
                `${before} before, ${after} after`);
    if (!ok) failures += 1;

    /* And switching it back on works without a restart either. */
    harness.config.enabled = true;
    harness.setNow(60000);
    tick();
    play(shake(3, 200, 70000, 4, 30));
    const again = harness.calls.filter((c) => c[3] === "TriggerShake").length;
    console.log(`${again === 2 ? "PASS" : "FAIL"}  re-enabling at runtime works: ${again}`);
    if (again !== 2) failures += 1;
}

/* The shake and the shortcut must not use the same D-Bus method: the daemon
 * gates one on the setting and never the other. */
{
    run("method used by a gesture", defaults, shake(3, 200, 1000, 4, 30), 1);
    const methods = lastHarness.calls.map((c) => c[3]);
    const usesShake = methods.indexOf("TriggerShake") !== -1;
    const plain = methods.indexOf("Trigger") !== -1;
    console.log(`${usesShake && !plain ? "PASS" : "FAIL"}  a gesture calls TriggerShake, ` +
                `not Trigger: ${JSON.stringify(Array.from(new Set(methods)))}`);
    if (!usesShake || plain) failures += 1;
}

/* ---- promoting the overlay window --------------------------------------- */
/*
 * A Wayland client cannot ask for its own position, and KWin sometimes hands the
 * overlay the work area instead of the whole output — the screenshot is then
 * painted from the window's corner and appears shifted down by the panel's
 * height, which looks like two panels.  The script has to keep correcting it and
 * keep the daemon informed.
 */
{
    const harness = makeSandbox({});
    /* A window that the compositor placed below a 35 px panel and that ignores
     * the fullScreen request, which is the case that used to go unnoticed. */
    const overlay = {
        caption: "Circle to Search Overlay",
        resourceName: "circle-to-search",
        fullScreen: false,
        keepAbove: false,
        skipTaskbar: false,
        skipPager: false,
        onAllDesktops: false,
    };
    /* The properties that are not writable for every window type: setting them
     * has to fail without taking the rest of the promotion down with it, and
     * a compositor that ignores the geometry assignment must still end up with
     * the daemon knowing where the window really is. */
    Object.defineProperty(overlay, "noBorder", {
        get: () => false,
        set: () => { throw new Error("noBorder is read-only"); },
    });
    Object.defineProperty(overlay, "skipSwitcher", {
        get: () => false,
        set: () => { throw new Error("skipSwitcher is read-only"); },
    });
    let placed = { x: 0, y: 35, width: 2560, height: 1405 };
    Object.defineProperty(overlay, "frameGeometry", {
        get: () => placed,
        set: () => { /* the compositor keeps the window in the work area */ },
    });
    harness.sandbox.workspace.windowList = () => [overlay];

    const context = vm.createContext(harness.sandbox);
    vm.runInContext(fs.readFileSync(SOURCE, "utf8"), context, { filename: SOURCE });
    const tick = harness.timers[0].handlers[0];
    const watch = harness.timers[1].handlers[0];

    for (const step of shake(3, 200, 1000, 4, 30)) {
        harness.moveTo(step.x, step.y);
        harness.setNow(step.t);
        tick();
    }
    harness.setNow(2000);
    watch();

    console.log(`${overlay.fullScreen ? "PASS" : "FAIL"}  the overlay is asked to go full screen`);
    if (!overlay.fullScreen) failures += 1;
    console.log(`${overlay.keepAbove ? "PASS" : "FAIL"}  the overlay is kept above the panel`);
    if (!overlay.keepAbove) failures += 1;

    /* The window did not move, so its real position has to reach the daemon. */
    const reports = harness.calls.filter((c) => c[3] === "OverlayGeometry");
    const ok = reports.length > 0
        && reports[reports.length - 1][4] === 0
        && reports[reports.length - 1][5] === 35;
    console.log(`${ok ? "PASS" : "FAIL"}  the real geometry is reported: ` +
                JSON.stringify(reports.map((c) => c.slice(4))));
    if (!ok) failures += 1;

    /* Repeats must not spam the bus while nothing changes. */
    const before = harness.calls.length;
    harness.setNow(2300); watch();
    harness.setNow(2600); watch();
    const quiet = harness.calls.length === before;
    console.log(`${quiet ? "PASS" : "FAIL"}  an unchanged geometry is not re-sent`);
    if (!quiet) failures += 1;

    /* When the compositor finally moves it, the daemon hears about that too. */
    placed = { x: 0, y: 0, width: 2560, height: 1440 };
    harness.setNow(2900); watch();
    const last = harness.calls.filter((c) => c[3] === "OverlayGeometry").pop();
    const corrected = last[4] === 0 && last[5] === 0 && last[7] === 1440;
    console.log(`${corrected ? "PASS" : "FAIL"}  a later move is reported: ` +
                JSON.stringify(last.slice(4)));
    if (!corrected) failures += 1;
}

/* ---- calibration mode ---------------------------------------------------- */
/*
 * While the calibration window is open the script has to measure everything and
 * decide nothing — including when detection is switched off, because a user
 * whose shake is currently rejected is exactly the one who needs to calibrate.
 */
{
    run("calibrating never fires", { calibrating: true }, shake(4, 250, 1000, 4, 30), 0);
    const samples = lastHarness.calls.filter((c) => c[3] === "CalibrationSample");
    console.log(`${samples.length >= 3 ? "PASS" : "FAIL"}  swings are measured: ${samples.length}`);
    if (samples.length < 3) failures += 1;

    /* CalibrationSample(length, speed, curvature%, diagonal°, turn°, ms) */
    const first = samples[0].slice(4);
    const shaped = first.length === 6 && first.every((value) => Number.isInteger(value));
    console.log(`${shaped ? "PASS" : "FAIL"}  sample shape: ${JSON.stringify(first)}`);
    if (!shaped) failures += 1;

    /* 250 px on each axis is a 354 px swing in 120 ms — a real measurement, not
     * a rounded-off placeholder. */
    const length = first[0];
    const speed = first[1];
    const plausible = length > 300 && length < 400 && speed > 2000 && speed < 4000;
    console.log(`${plausible ? "PASS" : "FAIL"}  measurements look real: ` +
                `${length} px at ${speed} px/s`);
    if (!plausible) failures += 1;

    /* The turn against the previous swing is what sets the turn-back
     * tolerance; the first swing has nothing to compare against. */
    const turns = samples.map((c) => c[8]);
    const turnsOk = turns[0] === -1 && turns.slice(1).some((value) => value > 150);
    console.log(`${turnsOk ? "PASS" : "FAIL"}  turns are reported: ${JSON.stringify(turns)}`);
    if (!turnsOk) failures += 1;
}

/* Detection off plus calibrating: still measures. */
{
    run("calibrating works with detection off", { calibrating: true, enabled: false },
        shake(4, 250, 1000, 4, 30), 0);
    const samples = lastHarness.calls.filter((c) => c[3] === "CalibrationSample").length;
    console.log(`${samples >= 3 ? "PASS" : "FAIL"}  measured with detection off: ${samples}`);
    if (samples < 3) failures += 1;
}

/* And in a full screen window, where detection is otherwise suppressed. */
{
    run("calibrating works in fullscreen", { calibrating: true },
        shake(4, 250, 1000, 4, 30), 0, { fullScreen: true });
    const samples = lastHarness.calls.filter((c) => c[3] === "CalibrationSample").length;
    console.log(`${samples >= 3 ? "PASS" : "FAIL"}  measured in fullscreen: ${samples}`);
    if (samples < 3) failures += 1;
}

/* Nothing is measured when the dialog is not open. */
{
    run("no samples outside calibration", defaults, shake(3, 200, 1000, 4, 30), 1);
    const samples = lastHarness.calls.filter((c) => c[3] === "CalibrationSample").length;
    console.log(`${samples === 0 ? "PASS" : "FAIL"}  no samples normally: ${samples}`);
    if (samples !== 0) failures += 1;
}

/* ---- giving the keyboard back -------------------------------------------- */
/*
 * A Wayland client cannot hand the focus to another client's window, so the
 * compositor has to remember who had it and give it back once the overlay is
 * gone.  Only when the keyboard ended up nowhere: a browser tab opened by a
 * search is entitled to the focus it took.
 */
function focusHarness(config) {
    const harness = makeSandbox(config || {});
    const editor = { caption: "Kate", resourceName: "kate", fullScreen: false };
    const overlay = {
        caption: "Circle to Search Overlay",
        resourceName: "circle-to-search",
        fullScreen: false,
        keepAbove: false,
        skipTaskbar: false,
        skipPager: false,
        skipSwitcher: false,
        noBorder: false,
        onAllDesktops: false,
        frameGeometry: { x: 0, y: 0, width: 2560, height: 1440 },
    };
    let windows = [editor];
    harness.sandbox.workspace.windowList = () => windows;
    harness.sandbox.workspace.activeWindow = editor;

    const context = vm.createContext(harness.sandbox);
    vm.runInContext(fs.readFileSync(SOURCE, "utf8"), context, { filename: SOURCE });
    const tick = harness.timers[0].handlers[0];
    const watch = harness.timers[1].handlers[0];

    for (const step of shake(3, 200, 1000, 4, 30)) {
        harness.moveTo(step.x, step.y);
        harness.setNow(step.t);
        tick();
    }
    return {
        harness,
        editor,
        overlay,
        watch,
        open: () => { windows = [editor, overlay]; },
        close: () => { windows = [editor]; },
        active: () => harness.sandbox.workspace.activeWindow,
        setActive: (w) => { harness.sandbox.workspace.activeWindow = w; },
    };
}

{
    const scene = focusHarness();
    scene.open();
    scene.harness.setNow(2000); scene.watch();
    console.log(`${scene.active() === scene.overlay ? "PASS" : "FAIL"}  the overlay takes the focus`);
    if (scene.active() !== scene.overlay) failures += 1;

    /* The overlay is gone and the keyboard went nowhere. */
    scene.close();
    scene.setActive(null);
    scene.harness.setNow(2300); scene.watch();
    const back = scene.active() === scene.editor;
    console.log(`${back ? "PASS" : "FAIL"}  the keyboard goes back to the previous window`);
    if (!back) failures += 1;
}

{
    /* Something else already has the focus: leave it alone. */
    const scene = focusHarness();
    scene.open();
    scene.harness.setNow(2000); scene.watch();
    const browser = { caption: "Firefox", resourceName: "firefox" };
    scene.close();
    scene.setActive(browser);
    scene.harness.setNow(2300); scene.watch();
    const kept = scene.active() === browser;
    console.log(`${kept ? "PASS" : "FAIL"}  focus taken by something else is not stolen back`);
    if (!kept) failures += 1;
}

{
    /* Switched off: the script must not touch the focus at all. */
    const scene = focusHarness({ restoreFocus: false });
    scene.open();
    scene.harness.setNow(2000); scene.watch();
    scene.close();
    scene.setActive(null);
    scene.harness.setNow(2300); scene.watch();
    const untouched = scene.active() === null;
    console.log(`${untouched ? "PASS" : "FAIL"}  restoreFocus=false leaves the focus alone`);
    if (!untouched) failures += 1;
}

{
    /* The previous window closed while the overlay was up. */
    const scene = focusHarness();
    scene.open();
    scene.harness.setNow(2000); scene.watch();
    scene.harness.sandbox.workspace.windowList = () => [];
    scene.setActive(null);
    scene.harness.setNow(2300); scene.watch();
    console.log(`${scene.active() === null ? "PASS" : "FAIL"}  a window that closed is not resurrected`);
    if (scene.active() !== null) failures += 1;
}

/* ---- the movement trace behind a trigger --------------------------------- */
/*
 * After a trigger the daemon sometimes asks "did you mean this?", and a "no"
 * is only useful if the movement that caused it can be replayed.  The script
 * keeps a few seconds of samples for that — but only while it has been asked
 * to, because the resting state of an idle desktop has to stay silent.
 */
{
    run("trace is sent with the trigger", { collectTraces: true },
        shake(3, 200, 1000, 4, 30), 1);
    const traces = lastHarness.calls.filter((c) => c[3] === "GestureTrace");
    console.log(`${traces.length === 1 ? "PASS" : "FAIL"}  one trace per trigger: ${traces.length}`);
    if (traces.length !== 1) failures += 1;

    /* It has to arrive before the trigger it belongs to, or the daemon would
     * have nothing in hand when it decides whether to ask. */
    const methods = lastHarness.calls.map((c) => c[3]);
    const ordered = methods.indexOf("GestureTrace") < methods.indexOf("TriggerShake");
    console.log(`${ordered ? "PASS" : "FAIL"}  the trace precedes the trigger: ` +
                JSON.stringify(methods));
    if (!ordered) failures += 1;

    /* "x,y,t;x,y,t;…", timestamps relative to the first sample. */
    const encoded = traces.length ? traces[0][4] : "";
    const points = String(encoded).split(";").filter((p) => p.length > 0)
        .map((p) => p.split(",").map(Number));
    const wellFormed = points.length > 5
        && points.every((p) => p.length === 3 && p.every((n) => Number.isInteger(n)))
        && points[0][2] === 0;
    console.log(`${wellFormed ? "PASS" : "FAIL"}  the trace parses: ${points.length} point(s)`);
    if (!wellFormed) failures += 1;

    /* And it really is the movement that just happened, not a stub. */
    const rising = points.every((p, i) => i === 0 || p[2] >= points[i - 1][2]);
    const moved = points.some((p) => p[0] !== points[0][0]);
    console.log(`${rising && moved ? "PASS" : "FAIL"}  the trace is the real movement`);
    if (!rising || !moved) failures += 1;
}

/* Off by default: an installation that has stopped asking costs nothing. */
{
    run("no trace unless asked for", defaults, shake(3, 200, 1000, 4, 30), 1);
    const traces = lastHarness.calls.filter((c) => c[3] === "GestureTrace").length;
    console.log(`${traces === 0 ? "PASS" : "FAIL"}  no trace when not collecting: ${traces}`);
    if (traces !== 0) failures += 1;
}

/* Collecting must not make ordinary movement chatty either. */
{
    run("collecting stays silent without a trigger", { collectTraces: true },
        drawing(1000, 40, 40), 0);
    const chatter = fromThePointer(lastHarness).length;
    console.log(`${chatter === 0 ? "PASS" : "FAIL"}  drawing sends nothing while collecting: ` +
                `${chatter}`);
    if (chatter !== 0) failures += 1;
}

/* ---- the recorded corpus ------------------------------------------------- */
/*
 * Every trace in tests/traces/ is replayed through the detector and has to end
 * the way it says it should.  Files land there from tools/record-trace.sh and
 * from the "no, that was accidental" answer, so a misfire reported once cannot
 * come back unnoticed.
 */
{
    const traceDir = path.join(__dirname, "traces");
    let files = [];
    try {
        files = fs.readdirSync(traceDir).filter((name) => name.endsWith(".json")).sort();
    } catch (error) {
        console.log(`FAIL  the trace corpus is missing: ${error.message}`);
        failures += 1;
    }
    console.log(`\n(replaying ${files.length} recorded trace(s))`);
    for (const name of files) {
        let trace;
        try {
            trace = JSON.parse(fs.readFileSync(path.join(traceDir, name), "utf8"));
        } catch (error) {
            console.log(`FAIL  ${name}: not valid JSON — ${error.message}`);
            failures += 1;
            continue;
        }
        const samples = trace.samples || [];
        if (samples.length < 2) {
            console.log(`FAIL  ${name}: no samples`);
            failures += 1;
            continue;
        }
        if (trace.expect !== "fire" && trace.expect !== "no-fire") {
            console.log(`FAIL  ${name}: expect must be "fire" or "no-fire"`);
            failures += 1;
            continue;
        }
        const settings = Object.assign({}, trace.settings || {});
        if (!run(`trace ${name}`, settings, samples, trace.expect === "fire" ? 1 : 0)) {
            failures += 1;
        }
    }
}

console.log(failures === 0 ? "\nall good" : `\n${failures} failure(s)`);
process.exit(failures === 0 ? 0 : 1);
