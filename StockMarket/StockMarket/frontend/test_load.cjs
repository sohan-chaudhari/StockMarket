global.CanvasRenderingContext2D = { prototype: {} };
global.window = global;
global.document = {
    addEventListener: () => {},
    getElementById: () => null,
    querySelectorAll: () => []
};

try {
    require('./drawing-core.js');
    require('./drawings.js');
    console.log("Success! No loading error.");
} catch (e) {
    console.error("Loading error:", e);
}
