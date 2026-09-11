globalThis.CanvasRenderingContext2D = { prototype: {} };
globalThis.window = globalThis;
globalThis.document = {
    addEventListener: () => {},
    getElementById: () => null,
    querySelectorAll: () => []
};

import './drawings.js';
console.log("Success! No loading error.");
