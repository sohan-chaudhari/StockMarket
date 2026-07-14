/**
 * LEVERAGE - Stock Market Loader
 * Premium animated loader for home and chart pages
 */

const LeverageLoader = {
    /**
     * Creates and shows the loader
     * @param {string} type - 'candlestick' | 'market-lines' | 'spinner'
     * @param {string} message - Loading message to display
     */
    show(type = 'candlestick', message = 'Loading market data...') {
        // Remove existing loader if any
        this.hide();

        const overlay = document.createElement('div');
        overlay.className = 'leverage-loader-overlay';
        overlay.id = 'leverage-loader';

        let loaderContent = '';

        switch (type) {
            case 'candlestick':
            case 'market-lines':
            case 'spinner':
            default:
                loaderContent = `
                    <div class="leverage-loader simple-glow-theme">
                        <div class="loader-logo-wrapper">
                            <div class="logo-glow-effect"></div>
                            <img src="logo.jpeg" class="loader-main-logo" alt="LEVERAGE">
                        </div>
                        <div class="loader-text-clean">
                            <span class="loader-dot-pulse"></span>
                            <span class="loader-message-text">${message}</span>
                        </div>
                    </div>
                `;
                break;
        }

        overlay.innerHTML = loaderContent;
        document.body.appendChild(overlay);
    },

    /**
     * Hides the loader with a smooth fade out
     * @param {number} delay - Optional delay before hiding (ms)
     */
    hide(delay = 0) {
        setTimeout(() => {
            const loader = document.getElementById('leverage-loader');
            if (loader) {
                loader.classList.add('fade-out');
                setTimeout(() => {
                    loader.remove();
                }, 400); // Match CSS transition duration
            }
        }, delay);
    },

    /**
     * Updates the loader message
     * @param {string} message - New message to display
     */
    updateMessage(message) {
        const statusEl = document.querySelector('.leverage-loader-overlay .loader-message-text');
        if (statusEl) {
            statusEl.innerHTML = `<span class="loader-dot"></span>${message}`;
        }
    }
};

// Export for module usage
if (typeof module !== 'undefined' && module.exports) {
    module.exports = LeverageLoader;
}

// Make available globally
window.LeverageLoader = LeverageLoader;
