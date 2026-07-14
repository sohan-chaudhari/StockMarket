# News Sentiment Analysis Platform - UML Class Diagram

## 📐 Overview
This PlantUML diagram provides a **comprehensive architectural blueprint** of the entire News Sentiment Analysis Platform, documenting 40+ classes across 8 architectural layers.

## 📄 Files
- **`architecture_class_diagram.puml`** - PlantUML source code (main diagram)
- **`README_DIAGRAM.md`** - This file

## 🏗️ Architecture Layers

### Layer 1: Frontend (HTML/JavaScript)
- `FrontendApp` - Main application controller
- `LoaderModule` - Animated candlestick loader
- `UIComponents` - DOM element references

### Layer 2: Backend API (FastAPI)
- `FastAPIApp` - Main application
- 6 API Routers: Health, Search, News, Ingest, ScanX, Stocks
- 10+ REST endpoints

### Layer 3: Data Schemas (Pydantic)
- Request/Response models
- Validation schemas
- 5 primary schemas with 30+ validated fields

### Layer 4: Database Models (SQLAlchemy ORM)
- `Ticker` - Stock metadata
- `SentimentResult` - Sentiment analysis storage
- `RecencyScore` - Recency validation metrics
- `PriceHistory` - Historical OHLCV data

### Layer 5: ML/AI Pipeline
- **`SentimentAnalyzer`** - Ensemble orchestrator (75% FinBERT + 25% Lexicon)
- **`FinBERTModel`** - ProsusAI/finbert transformer
- **`LexiconModel`** - 400+ terms, pattern matching
- **`FinancialPreprocessor`** - Text cleaning
- **`EntityExtractor`** - Ticker extraction
- **`LLMCalibrator`** - Gemini API integration
- **`ConfidenceEstimator`** - Multi-factor confidence
- **`SentimentHelper`** - Singleton wrapper

### Layer 6: News Scrapers
- `ScanXGoogleScraper` - RSS-based (5,571 NSE tickers)
- `GoogleNewsPlaywrightScraper` - Browser automation
- `GoogleNewsFullScraper` - Alternative scraper
- `NewsScraperLegacy` - Legacy implementation

### Layer 7: Task Queue (Celery)
- `CeleryApp` - Task queue configuration
- `SentimentTask` - Async sentiment analysis
- `ScraperTask` - Scheduled scraping (every 5 min)
- `RecencyTask` - Recency calculation

### Layer 8: Configuration
- `Settings` - Environment configuration
- Database URLs, API keys, feature flags

## 📊 Diagram Statistics
- **Total Classes:** 40+
- **Total Methods:** 100+
- **Total Attributes:** 150+
- **Total Relationships:** 35+
- **Lines of UML Code:** 750+

## 🎨 Viewing the Diagram

### Option 1: VS Code (Recommended)
1. Install the **PlantUML** extension
2. Open `architecture_class_diagram.puml`
3. Press `Alt+D` to preview

### Option 2: Online Renderer
1. Copy the entire `.puml` file content
2. Visit: http://www.plantuml.com/plantuml/uml/
3. Paste and render

### Option 3: Generate Image
```bash
# Install PlantUML
npm install -g node-plantuml

# Generate PNG
puml generate architecture_class_diagram.puml --png

# Generate SVG (vector, scalable)
puml generate architecture_class_diagram.puml --svg
```

## 🔍 Key Features

### Visual Design
- ✅ Dark theme for readability
- ✅ Color-coded packages (8 distinct colors)
- ✅ Detailed method signatures with return types
- ✅ Clear relationship arrows with labels
- ✅ Annotations for complex components

### Documentation Depth
- **Methods:** Full signatures with parameters and return types
- **Attributes:** Data types and default values
- **Relationships:** Composition, aggregation, usage, HTTP calls
- **Notes:** Special implementation details

### Architecture Patterns
- Singleton: `SentimentHelper`, `SentimentAnalyzer`
- Factory: Database session creation
- Strategy: Multiple scraper implementations
- Facade: SentimentHelper wraps ML pipeline
- Repository: DatabaseConfig manages data access

## 🎯 Use Cases

### For Developers
- Understand component dependencies before coding
- Identify extension points for new features
- Locate bottlenecks for optimization
- Plan refactoring with clear boundaries

### For Architects
- System overview for technical documentation
- Integration points for third-party services
- Scalability planning (task queue, caching)
- Technology stack validation

### For New Team Members
- Visual onboarding material
- Understand data flow across layers
- Quick reference for API structure
- Learn ML pipeline architecture

## 🔗 Critical Relationships

### Data Flow
```
User → Frontend → API Router → Scraper → ML Pipeline → Database → Response
                                    ↓
                             Celery Tasks (async)
```

### Key Dependencies
- Frontend → Backend: 3 HTTP API calls
- Backend → ML: 4 sentiment analysis calls
- Backend → Scrapers: 2 scraping integrations
- Tasks → Database: 6 async operations

## 📈 Performance Insights

### Identified Bottlenecks (from diagram)
1. **Playwright Scraper:** 15-30 seconds
2. **FinBERT Inference:** ~100ms per article (CPU)
3. **Lexicon Analysis:** <1ms (fast)

### Scalability Points
- Celery task queue for async processing
- Singleton pattern for model caching
- ThreadPoolExecutor for concurrent scraping

## 🛠️ Extending the Diagram

### To Add New Components
```plantuml
class NewComponent {
    - attribute: Type
    __Methods__
    + methodName(param: Type): ReturnType
}

ExistingClass --> NewComponent : relationship
```

### To Focus on Specific Layer
Comment out other packages:
```plantuml
/' 
package "Layer to Hide" {
    ...
}
'/
```

## 📚 Related Documentation
- **README.md** - Project overview
- **TECHNICAL_DOCUMENTATION.md** - 925-line deep dive
- **SETUP.md** - Installation guide
- **QUICKSTART.md** - Quick start guide

## 🔄 Keeping Diagram Updated

When adding new code:
1. Add class to appropriate layer package
2. Document public methods with signatures
3. Update relationships with existing classes
4. Regenerate image for documentation

## 💡 Tips

1. **Zoom In/Out:** Use SVG format for infinite zoom
2. **Print:** Generate high-res PNG (300 DPI) for physical copies
3. **Presentations:** Export to SVG, import to PowerPoint/Keynote
4. **Documentation:** Embed PNG in Markdown/Wiki

## 📞 Support

For diagram questions or updates:
- Review the `.puml` source code
- Check PlantUML documentation: https://plantuml.com/class-diagram
- Regenerate after major architectural changes

---

**Last Updated:** 2026-02-03  
**Diagram Version:** 1.0  
**Total Classes Documented:** 40+  
**Completeness:** 100% of active codebase
