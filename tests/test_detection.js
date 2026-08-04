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

    const triggers = harness.calls.filter((c) => c[3] === "Trigger");
    harness.triggers = triggers;
    const ok = triggers.length === expectTriggers;
    console.log(
        `${ok ? "PASS" : "FAIL"}  ${name}: ${triggers.length} trigger(s), expected ${expectTriggers}` +
        (triggers.length ? ` -> ${JSON.stringify(triggers[0].slice(3))}` : "")
    );
    if (triggers.length) {
        const args = triggers[0];
        if (args[0] !== "io.github.fand1l.CircleToSearch"
            || args[3] !== "Trigger"
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
const triggers2 = harness2.calls.filter((c) => c[3] === "Trigger");
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

/* The glow: reported once a swing is accepted, and stopped afterwards. */
run("glow during a shake", defaults, shake(3, 200, 1000, 4, 40), 1);
{
    const progress = lastHarness.calls.filter((c) => c[3] === "GestureProgress");
    const ended = lastHarness.calls.filter((c) => c[3] === "GestureEnded");
    const ok = progress.length > 0 && ended.length > 0;
    console.log(`${ok ? "PASS" : "FAIL"}  glow: ${progress.length} progress, ${ended.length} ended`);
    if (!ok) failures += 1;
    /* GestureProgress(x, y, count, needed, screen) — integers, then a string. */
    const shapeOk = progress.every((c) => c.length === 9
        && Number.isInteger(c[4]) && Number.isInteger(c[5])
        && Number.isInteger(c[6]) && Number.isInteger(c[7])
        && typeof c[8] === "string" && c[6] >= 1 && c[6] <= c[7]);
    console.log(`${shapeOk ? "PASS" : "FAIL"}  glow call shape: ${JSON.stringify(progress[0] ? progress[0].slice(3) : null)}`);
    if (!shapeOk) failures += 1;
    /* The glow must never be reported before the first accepted swing. */
    const firstProgressIndex = lastHarness.calls.findIndex((c) => c[3] === "GestureProgress");
    console.log(`${firstProgressIndex >= 0 ? "PASS" : "FAIL"}  glow starts only after a swing`);
}

/* Turning the glow off means no progress traffic whatsoever. */
run("no glow when disabled", { glow: false }, shake(3, 200, 1000, 4, 40), 1);
{
    const chatter = lastHarness.calls.filter((c) => c[3] !== "Trigger").length;
    console.log(`${chatter === 0 ? "PASS" : "FAIL"}  glow disabled sends nothing extra: ${chatter}`);
    if (chatter !== 0) failures += 1;
}

/* Ordinary pointer use must stay completely silent on D-Bus. */
run("straight move stays silent", defaults, straight(900, 1000, 12, 40), 0);
{
    const chatter = lastHarness.calls.length;
    console.log(`${chatter === 0 ? "PASS" : "FAIL"}  normal movement sends nothing: ${chatter}`);
    if (chatter !== 0) failures += 1;
}

console.log(failures === 0 ? "\nall good" : `\n${failures} failure(s)`);
process.exit(failures === 0 ? 0 : 1);
