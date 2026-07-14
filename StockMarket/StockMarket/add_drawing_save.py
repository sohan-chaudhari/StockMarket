import re

with open('frontend/drawings.js', 'r', encoding='utf-8') as f:
    js = f.read()

# We need a dictionary of all drawing classes to instantiate them safely
classes_regex = re.compile(r'class ([A-Z][a-zA-Z0-9_]+) extends BaseDrawing')
classes1 = classes_regex.findall(js)
classes_regex2 = re.compile(r'class ([A-Z][a-zA-Z0-9_]+) extends TwoPointDrawing')
classes2 = classes_regex2.findall(js)

all_classes = set(classes1 + classes2)
all_classes.add('BaseDrawing')
all_classes.add('TwoPointDrawing')

# Build a dictionary map
class_map_str = "const DrawingClasses = {\n" + ",\n".join([f"  {c}: {c}" for c in all_classes]) + "\n};\n"

# Inject the save/load methods into ToolManager
save_load_methods = """
        saveDrawings() {
            try {
                let ticker = window.currentTicker || 'default';
                let data = this.drawings.map(d => ({
                    type: d.constructor.name,
                    coords: d.coords,
                    options: d.options
                }));
                localStorage.setItem('drawings_' + ticker, JSON.stringify(data));
            } catch(e) { console.error('Save drawings error', e); }
        }

        loadDrawings() {
            try {
                let ticker = window.currentTicker || 'default';
                let saved = localStorage.getItem('drawings_' + ticker);
                if (!saved) return;
                let data = JSON.parse(saved);
                this.drawings = [];
                for (let item of data) {
                    let Cls = DrawingClasses[item.type];
                    if (Cls) {
                        let shape = Object.create(Cls.prototype);
                        shape.coords = item.coords || [];
                        shape.options = item.options || {};
                        this.drawings.push(shape);
                    }
                }
                this.draw();
            } catch(e) { console.error('Load drawings error', e); }
        }
"""

js = class_map_str + js
js = js.replace('resizeCanvas() {', save_load_methods + '\n        resizeCanvas() {')

# Hook up loadDrawings in init() or whenever ticker changes.
# In dashboard.js, we call window.toolManager.loadDrawings() when a new chart is loaded.
# And we must hook up saveDrawings() to every time a drawing is added or modified.
js = js.replace('this.drawings.push(this.currentDrawing);', 'this.drawings.push(this.currentDrawing);\n                    this.saveDrawings();')
js = js.replace('this.drawings.splice(index, 1);', 'this.drawings.splice(index, 1);\n                    this.saveDrawings();')
js = js.replace('this.drawings = [];', 'this.drawings = [];\n                    this.saveDrawings();')

with open('frontend/drawings.js', 'w', encoding='utf-8') as f:
    f.write(js)
print("Updated drawings.js with auto-save/load")
