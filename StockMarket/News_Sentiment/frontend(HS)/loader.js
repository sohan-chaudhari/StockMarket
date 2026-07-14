// ==================== Canvas Loader Animation ====================
// Advanced candlestick chart loader with trend lines and arrows

(function () {
    const canvas = document.getElementById('loaderCanvas');
    if (!canvas) return;

    const ctx = canvas.getContext('2d');

    // Calculate responsive size based on viewport
    function getCanvasSize() {
        const isMobile = window.innerWidth < 480;
        return {
            width: isMobile ? 280 : 350,
            height: isMobile ? 210 : 260
        };
    }

    function initCanvas() {
        const size = getCanvasSize();
        canvas.width = size.width;
        canvas.height = size.height;
    }

    initCanvas();

    const padding = 40;
    let chartWidth, chartHeight;

    function updateDimensions() {
        chartWidth = canvas.width - padding * 2;
        chartHeight = canvas.height - padding * 2;
    }

    updateDimensions();

    // Animation configuration
    const numCandles = 7;
    const candleWidth = 20;
    const animationDuration = 3000; // 3 seconds total loop

    let animationStartTime = null;
    let candles = [];
    let currentTrendType = 'bullish';
    let animationId = null;

    // Generate candle data with configurable trend
    function generateCandleData(trendType) {
        const candleSpacing = (chartWidth - numCandles * candleWidth) / (numCandles + 1);
        const data = [];
        let basePrice = 100;
        const isBullishTrend = trendType === 'bullish';

        for (let i = 0; i < numCandles; i++) {
            let isBullish;
            if (isBullishTrend) {
                isBullish = Math.random() > 0.25;
            } else {
                isBullish = Math.random() > 0.75;
            }

            const open = basePrice + Math.random() * 15;
            let close;

            if (isBullish) {
                close = open + Math.random() * 35 + 20;
            } else {
                close = open - Math.random() * 30 - 18;
            }

            const high = Math.max(open, close) + Math.random() * 15;
            const low = Math.min(open, close) - Math.random() * 12;

            data.push({
                open, close, high, low, isBullish,
                x: padding + candleSpacing + i * (candleWidth + candleSpacing)
            });

            if (isBullishTrend) {
                basePrice = close + Math.random() * 10 + 5;
            } else {
                basePrice = close - Math.random() * 10 - 5;
            }
        }

        return data;
    }

    function normalizeData(data) {
        const allPrices = data.flatMap(d => [d.open, d.close, d.high, d.low]);
        const minPrice = Math.min(...allPrices);
        const maxPrice = Math.max(...allPrices);
        const priceRange = maxPrice - minPrice;

        return data.map(candle => ({
            ...candle,
            normalizedHigh: chartHeight - ((candle.high - minPrice) / priceRange) * chartHeight + padding,
            normalizedLow: chartHeight - ((candle.low - minPrice) / priceRange) * chartHeight + padding,
            normalizedOpen: chartHeight - ((candle.open - minPrice) / priceRange) * chartHeight + padding,
            normalizedClose: chartHeight - ((candle.close - minPrice) / priceRange) * chartHeight + padding
        }));
    }

    function easeOutCubic(t) {
        return 1 - Math.pow(1 - t, 3);
    }

    function easeInOutCubic(t) {
        return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
    }

    function drawCandle(candle, progress, glowIntensity = 0) {
        const centerX = candle.x + candleWidth / 2;
        const wickProgress = Math.min(progress * 2, 1);
        const bodyProgress = Math.max((progress - 0.3) / 0.7, 0);

        const currentHigh = candle.normalizedLow - (candle.normalizedLow - candle.normalizedHigh) * easeOutCubic(wickProgress);
        const bodyHeight = Math.abs(candle.normalizedClose - candle.normalizedOpen) * easeOutCubic(bodyProgress);
        const bodyTop = candle.isBullish ?
            candle.normalizedOpen - bodyHeight :
            candle.normalizedOpen;

        const color = candle.isBullish ? '#00ff88' : '#ff4444';
        const glowColor = candle.isBullish ? 'rgba(0, 255, 136, ' : 'rgba(255, 68, 68, ';

        ctx.save();

        if (glowIntensity > 0) {
            ctx.shadowBlur = 15 * glowIntensity;
            ctx.shadowColor = color;
        }

        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.globalAlpha = 0.9;
        ctx.beginPath();
        ctx.moveTo(centerX, candle.normalizedLow);
        ctx.lineTo(centerX, currentHigh);
        ctx.stroke();

        if (bodyProgress > 0) {
            ctx.fillStyle = color;
            ctx.globalAlpha = 1;
            ctx.fillRect(candle.x, bodyTop, candleWidth, bodyHeight);

            if (glowIntensity > 0) {
                ctx.fillStyle = glowColor + (0.3 * glowIntensity) + ')';
                ctx.fillRect(candle.x + 2, bodyTop + 2, candleWidth - 4, Math.max(0, bodyHeight - 4));
            }
        }

        ctx.restore();
    }

    function drawTrendLine(progress, trendType) {
        if (progress <= 0) return;

        const points = candles.map(c => ({
            x: c.x + candleWidth / 2,
            y: c.normalizedClose
        }));

        const lastPoint = points[points.length - 1];
        const secondLastPoint = points[points.length - 2];
        const slope = (lastPoint.y - secondLastPoint.y) / (lastPoint.x - secondLastPoint.x);
        const extendedX = canvas.width - padding;
        const extendedY = lastPoint.y + slope * (extendedX - lastPoint.x);

        const extendedPoints = [...points, { x: extendedX, y: extendedY }];
        const totalLength = extendedPoints.length - 1;
        const animatedProgress = easeInOutCubic(Math.min(progress, 1));
        const currentIndex = Math.min(Math.floor(animatedProgress * totalLength), totalLength - 1);
        const segmentProgress = (animatedProgress * totalLength) % 1;

        const lineColor = trendType === 'bullish' ? '#00ff88' : '#ff4444';

        ctx.save();
        ctx.strokeStyle = lineColor;
        ctx.lineWidth = 2.5;
        ctx.shadowBlur = 12;
        ctx.shadowColor = lineColor;
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';

        ctx.beginPath();
        ctx.moveTo(extendedPoints[0].x, extendedPoints[0].y);

        for (let i = 1; i <= currentIndex; i++) {
            ctx.lineTo(extendedPoints[i].x, extendedPoints[i].y);
        }

        if (currentIndex < totalLength) {
            const nextX = extendedPoints[currentIndex].x + (extendedPoints[currentIndex + 1].x - extendedPoints[currentIndex].x) * segmentProgress;
            const nextY = extendedPoints[currentIndex].y + (extendedPoints[currentIndex + 1].y - extendedPoints[currentIndex].y) * segmentProgress;
            ctx.lineTo(nextX, nextY);
        }

        ctx.stroke();
        ctx.restore();
    }

    function drawArrow(progress, trendType) {
        if (progress <= 0) return;

        const lastCandle = candles[candles.length - 1];
        const secondLastCandle = candles[candles.length - 2];
        const slope = (lastCandle.normalizedClose - secondLastCandle.normalizedClose) / (lastCandle.x - secondLastCandle.x);
        const extendedDistance = 35;
        const arrowX = lastCandle.x + candleWidth / 2 + extendedDistance;
        const arrowY = lastCandle.normalizedClose + slope * extendedDistance;
        const arrowSize = 14;

        const isUpArrow = trendType === 'bullish';
        const arrowColor = isUpArrow ? '#00ff88' : '#ff4444';

        const scale = easeOutCubic(Math.min(progress * 2, 1));
        const pulse = 1 + Math.sin(Date.now() / 200) * 0.15 * Math.min(progress, 1);

        ctx.save();
        ctx.translate(arrowX, arrowY);
        ctx.scale(scale * pulse, scale * pulse);

        if (!isUpArrow) {
            ctx.scale(1, -1);
        }

        ctx.shadowBlur = 20;
        ctx.shadowColor = arrowColor;
        ctx.fillStyle = arrowColor;
        ctx.beginPath();
        ctx.moveTo(0, -arrowSize);
        ctx.lineTo(arrowSize * 0.6, 0);
        ctx.lineTo(arrowSize * 0.25, 0);
        ctx.lineTo(arrowSize * 0.25, arrowSize);
        ctx.lineTo(-arrowSize * 0.25, arrowSize);
        ctx.lineTo(-arrowSize * 0.25, 0);
        ctx.lineTo(-arrowSize * 0.6, 0);
        ctx.closePath();
        ctx.fill();

        ctx.restore();
    }

    function animate(timestamp) {
        if (!animationStartTime) {
            animationStartTime = timestamp;
            currentTrendType = Math.random() > 0.5 ? 'bullish' : 'bearish';
            candles = normalizeData(generateCandleData(currentTrendType));
        }

        const elapsed = timestamp - animationStartTime;
        const loopProgress = (elapsed % animationDuration) / animationDuration;

        ctx.clearRect(0, 0, canvas.width, canvas.height);

        const candlePhaseEnd = 0.65;
        const trendPhaseEnd = 0.85;
        const fadeOutStart = 0.92;

        if (loopProgress < candlePhaseEnd) {
            const candleProgress = loopProgress / candlePhaseEnd;
            candles.forEach((candle, index) => {
                const candleDelay = index / numCandles;
                const candleDuration = 0.8 / numCandles;
                const individualProgress = Math.max(0, Math.min(1, (candleProgress - candleDelay) / candleDuration));

                if (individualProgress > 0) {
                    const glowIntensity = individualProgress > 0.95 ? 1 : 0;
                    drawCandle(candle, individualProgress, glowIntensity);
                }
            });
        } else if (loopProgress < trendPhaseEnd) {
            candles.forEach(candle => drawCandle(candle, 1, 0.3));
            const trendProgress = (loopProgress - candlePhaseEnd) / (trendPhaseEnd - candlePhaseEnd);
            drawTrendLine(trendProgress, currentTrendType);
        } else if (loopProgress < fadeOutStart) {
            candles.forEach(candle => drawCandle(candle, 1, 0.3));
            drawTrendLine(1, currentTrendType);
            const arrowProgress = (loopProgress - trendPhaseEnd) / (fadeOutStart - trendPhaseEnd);
            drawArrow(arrowProgress, currentTrendType);
        } else {
            const fadeProgress = (loopProgress - fadeOutStart) / (1 - fadeOutStart);
            ctx.globalAlpha = 1 - easeInOutCubic(fadeProgress);

            candles.forEach(candle => drawCandle(candle, 1, 0.3));
            drawTrendLine(1, currentTrendType);
            drawArrow(1, currentTrendType);

            ctx.globalAlpha = 1;

            if (fadeProgress > 0.95) {
                animationStartTime = timestamp;
                currentTrendType = Math.random() > 0.5 ? 'bullish' : 'bearish';
                candles = normalizeData(generateCandleData(currentTrendType));
            }
        }

        animationId = requestAnimationFrame(animate);
    }

    // Start animation
    animationId = requestAnimationFrame(animate);

    // Handle window resize
    let resizeTimeout;
    window.addEventListener('resize', () => {
        clearTimeout(resizeTimeout);
        resizeTimeout = setTimeout(() => {
            initCanvas();
            updateDimensions();
            animationStartTime = null;
        }, 250);
    });

    // Expose stop function globally to stop animation when loading completes
    window.stopLoaderAnimation = function () {
        if (animationId) {
            cancelAnimationFrame(animationId);
            animationId = null;
        }
    };
})();
