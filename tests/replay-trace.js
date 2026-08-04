/*
 * Replay a recorded cursor trace through the real detector.
 *
 *   ./tools/record-trace.sh drawing.json     # on the Plasma machine
 *   node tests/replay-trace.js drawing.json  # anywhere
 *
 * It prints the per-swing diagnostics and whether the overlay would have
 * opened, so a "it fires when I draw" report becomes something that can be
 * measured, tuned against, and then kept as a regression case.
 *
 * Settings can be overridden from the command line to see what a change would
 * have done to that exact movement:
 *
 *   node tests/replay-trace.js drawing.json minSpeedPxPerSec=1000 reversals=3
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SOURCE = path.join(__dirname, "..", "kwinscript", "contents", "code", "main.js");

function parseOverrides(args) {
    const config = { debug: true };
    for (const arg of args) {
        const [key, value] = arg.split("=");
        if (value === undefined) {
            continue;
        }
        if (value === "true" || value === "false") {
            config[key] = value === "true";
        } else if (!Number.isNaN(Number(value))) {
            config[key] = Number(value);
        } else {
            config[key] = value;
        }
    }
    return config;
}

function main() {
    const [file, ...overrides] = process.argv.slice(2);
    if (!file) {
        console.error("usage: node tests/replay-trace.js TRACE.json [key=value ...]");
        process.exit(2);
    }

    const trace = JSON.parse(fs.readFileSync(file, "utf8"));
    const samples = trace.samples || [];
    if (samples.length === 0) {
        console.error("the trace contains no samples");
        process.exit(2);
    }

    const config = parseOverrides(overrides);
    const calls = [];
    const timers = [];
    const cursor = { x: samples[0].x, y: samples[0].y };
    let now = 0;

    function QTimerCtor() {
        const timer = {
            interval: 0,
            singleShot: false,
            handlers: [],
            start() {},
            stop() {},
        };
        timer.timeout = { connect: (fn) => timer.handlers.push(fn) };
        timers.push(timer);
        return timer;
    }

    const sandbox = {
        QTimer: QTimerCtor,
        workspace: {
            get cursorPos() {
                return { x: cursor.x, y: cursor.y };
            },
            screens: [{ name: "replay", geometry: { x: -100000, y: -100000, width: 200000, height: 200000 } }],
            windowList: () => [],
            windowAdded: { connect: () => {} },
            activeWindow: { fullScreen: false },
        },
        readConfig: (key, fallback) => (key in config ? config[key] : fallback),
        callDBus: (...args) => calls.push({ method: args[3], at: now, args: args.slice(4) }),
        registerShortcut: () => {},
        print: (message) => {
            const text = String(message);
            if (text.indexOf("CTS-TRACE") !== 0) {
                console.log("   " + text.replace("circle-to-search: ", ""));
            }
        },
        Date: { now: () => now },
        Math,
        String,
        Number,
        parseFloat,
        isNaN,
    };
    sandbox.global = sandbox;

    const context = vm.createContext(sandbox);
    vm.runInContext(fs.readFileSync(SOURCE, "utf8"), context, { filename: SOURCE });
    const tick = timers[0].handlers[0];

    console.log(`\nreplaying ${samples.length} samples over ` +
                `${(samples[samples.length - 1].t / 1000).toFixed(1)} s\n`);

    for (const sample of samples) {
        cursor.x = sample.x;
        cursor.y = sample.y;
        now = sample.t;
        tick();
    }

    const triggers = calls.filter((call) => call.method === "Trigger");
    console.log("");
    if (triggers.length === 0) {
        console.log("RESULT: the overlay would NOT have opened.");
    } else {
        console.log(`RESULT: the overlay would have opened ${triggers.length} time(s), at ` +
                    triggers.map((call) => `${(call.at / 1000).toFixed(2)} s`).join(", "));
    }
    if (trace.notes && trace.notes.length) {
        console.log(`\n(the recording itself logged ${trace.notes.length} diagnostic lines)`);
    }
    process.exit(0);
}

main();
