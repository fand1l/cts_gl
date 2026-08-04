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
if (!run("fullscreen blocks the shake", defaults, shake(4, 200, 1000, 4, 40), 0,
         { fullScreen: true })) failures += 1;

/* ...unless the user turned that guard off. */
if (!run("fullscreen allowed when configured", { disableInFullscreen: false },
         shake(4, 200, 1000, 4, 40), 1, { fullScreen: true })) failures += 1;

/* And nothing at all is reported to the daemon while a game is focused. */
{
    const quiet = lastHarness;
    run("fullscreen stays silent", defaults, shake(4, 200, 1000, 4, 40), 0, { fullScreen: true });
    const anyCall = lastHarness.calls.length;
    console.log(`${anyCall === 0 ? "PASS" : "FAIL"}  fullscreen sends no D-Bus at all: ${anyCall} call(s)`);
    if (anyCall !== 0) failures += 1;
    void quiet;
}

/* Ordinary pointer use must stay completely silent on D-Bus. */
run("straight move stays silent", defaults, straight(900, 1000, 12, 40), 0);
{
    const chatter = lastHarness.calls.length;
    console.log(`${chatter === 0 ? "PASS" : "FAIL"}  normal movement sends nothing: ${chatter}`);
    if (chatter !== 0) failures += 1;
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

console.log(failures === 0 ? "\nall good" : `\n${failures} failure(s)`);
process.exit(failures === 0 ? 0 : 1);
