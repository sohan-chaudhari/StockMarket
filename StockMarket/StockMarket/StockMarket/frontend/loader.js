/**
 * LEVERAGE - Stock Market Loader
 * Premium animated loader for home and chart pages
 */

const LeverageLoader = {
    show(type = 'candlestick', message = 'Loading market data...') {
        // Trigger the slim top progress bar from page-cache.js instead of blocking screen
        if (window._lvNavBarStart) {
            window._lvNavBarStart();
        }
    },

    hide(delay = 0) {
        setTimeout(() => {
            if (window._lvNavBarFinish) {
                window._lvNavBarFinish();
            }
            // Clear any hardcoded loader from the HTML if it exists
            const loader = document.getElementById('leverage-loader');
            if (loader) {
                loader.classList.add('fade-out');
                setTimeout(() => loader.remove(), 300);
            }
        }, delay);
    },

    updateMessage(message) {
        // No-op for top progress bar
    }
};

// Export for module usage
if (typeof module !== 'undefined' && module.exports) {
    module.exports = LeverageLoader;
}

// Make available globally
window.LeverageLoader = LeverageLoader;
