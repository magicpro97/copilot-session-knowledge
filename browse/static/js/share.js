/**
 * browse/static/js/share.js — Copy link + Screenshot toolbar.
 *
 * Self-contained module that injects a fixed-position toolbar at top-right
 * with "Copy link" and "Screenshot" buttons. No external dependencies except
 * the vendored html-to-image library.
 *
 * Loaded after palette.js. Auto-runs on DOMContentLoaded.
 */
(function () {
    'use strict';

    var TOOLBAR_ID = 'share-toolbar';
    var TOAST_ID = 'share-toast';

    /**
     * Show a temporary toast notification.
     */
    function showToast(message, duration) {
        duration = duration || 2000;
        var existing = document.getElementById(TOAST_ID);
        if (existing) {
            existing.remove();
        }
        var toast = document.createElement('div');
        toast.id = TOAST_ID;
        toast.style.cssText = [
            'position:fixed',
            'top:60px',
            'right:20px',
            'background-color:#333',
            'color:#fff',
            'padding:12px 16px',
            'border-radius:4px',
            'z-index:9999',
            'font-size:14px',
            'box-shadow:0 2px 8px rgba(0,0,0,0.2)',
            'max-width:300px',
            'word-wrap:break-word'
        ].join(';');
        toast.textContent = message;
        document.body.appendChild(toast);
        setTimeout(function () {
            if (toast.parentNode) {
                toast.remove();
            }
        }, duration);
    }

    /**
     * Copy location.href to clipboard.
     */
    function copyLink() {
        var url = location.href;

        // Try modern clipboard API first
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(url)
                .then(function () {
                    showToast('✓ Link copied!');
                })
                .catch(function (err) {
                    console.error('Clipboard API error:', err);
                    // Fallback to old API
                    fallbackCopy(url);
                });
        } else {
            fallbackCopy(url);
        }
    }

    /**
     * Fallback copy using execCommand.
     */
    function fallbackCopy(text) {
        var textarea = document.createElement('textarea');
        textarea.value = text;
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.select();
        try {
            var success = document.execCommand('copy');
            if (success) {
                showToast('✓ Link copied!');
            } else {
                showToast('Failed to copy link');
            }
        } catch (err) {
            console.error('Fallback copy error:', err);
            showToast('Failed to copy link');
        }
        document.body.removeChild(textarea);
    }

    /**
     * Download a PNG screenshot of the page.
     */
    function downloadScreenshot() {
        // Extract session ID from URL or use "unknown"
        var sessionMatch = location.pathname.match(/\/session\/([^/]+)/);
        var sessionId = sessionMatch ? sessionMatch[1] : 'unknown';

        // Extract page name (last path segment or home)
        var pathParts = location.pathname.split('/').filter(function (p) { return p; });
        var pageName = pathParts[pathParts.length - 1] || 'home';

        // Timestamp
        var now = new Date();
        var timestamp = now.getTime();

        // Filename
        var filename = 'session-' + sessionId + '-' + pageName + '-' + timestamp + '.png';

        // Check if htmlToImage is available
        if (typeof window.htmlToImage === 'undefined') {
            showToast('Screenshot library not loaded');
            console.error('htmlToImage not found in window');
            return;
        }

        showToast('Taking screenshot...');

        htmlToImage.toPng(document.body)
            .then(function (dataUrl) {
                // Create a temporary link and click it to download
                var link = document.createElement('a');
                link.href = dataUrl;
                link.download = filename;
                document.body.appendChild(link);
                link.click();
                document.body.removeChild(link);
                showToast('✓ Screenshot saved as ' + filename);
            })
            .catch(function (err) {
                console.error('Screenshot error:', err);
                showToast('Failed to take screenshot');
            });
    }

    /**
     * Inject the toolbar into the page.
     */
    function injectToolbar() {
        var existing = document.getElementById(TOOLBAR_ID);
        if (existing) {
            return; // Already injected
        }

        var toolbar = document.createElement('div');
        toolbar.id = TOOLBAR_ID;
        toolbar.style.cssText = [
            'position:fixed',
            'top:20px',
            'right:20px',
            'display:flex',
            'gap:8px',
            'z-index:9998',
            'align-items:center'
        ].join(';');

        // Copy link button
        var copyBtn = document.createElement('button');
        copyBtn.id = 'share-copy-btn';
        copyBtn.title = 'Copy link (F13)';
        copyBtn.style.cssText = [
            'background-color:#3498db',
            'color:#fff',
            'border:none',
            'padding:8px 12px',
            'border-radius:4px',
            'cursor:pointer',
            'font-size:14px',
            'display:flex',
            'align-items:center',
            'gap:6px',
            'transition:background-color 0.2s'
        ].join(';');
        copyBtn.innerHTML = '📋 Copy link';
        copyBtn.onmouseover = function () {
            this.style.backgroundColor = '#2980b9';
        };
        copyBtn.onmouseout = function () {
            this.style.backgroundColor = '#3498db';
        };
        copyBtn.onclick = function () {
            copyLink();
        };

        // Screenshot button
        var screenshotBtn = document.createElement('button');
        screenshotBtn.id = 'share-screenshot-btn';
        screenshotBtn.title = 'Take screenshot (F14)';
        screenshotBtn.style.cssText = [
            'background-color:#2ecc71',
            'color:#fff',
            'border:none',
            'padding:8px 12px',
            'border-radius:4px',
            'cursor:pointer',
            'font-size:14px',
            'display:flex',
            'align-items:center',
            'gap:6px',
            'transition:background-color 0.2s'
        ].join(';');
        screenshotBtn.innerHTML = '📸 Screenshot';
        screenshotBtn.onmouseover = function () {
            this.style.backgroundColor = '#27ae60';
        };
        screenshotBtn.onmouseout = function () {
            this.style.backgroundColor = '#2ecc71';
        };
        screenshotBtn.onclick = function () {
            downloadScreenshot();
        };

        toolbar.appendChild(copyBtn);
        toolbar.appendChild(screenshotBtn);
        document.body.appendChild(toolbar);
    }

    /**
     * Run on DOMContentLoaded.
     */
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', injectToolbar);
    } else {
        // Already loaded
        injectToolbar();
    }

    // Optional: F13 for copy link, F14 for screenshot
    document.addEventListener('keydown', function (event) {
        if (event.key === 'F13') {
            event.preventDefault();
            copyLink();
        } else if (event.key === 'F14') {
            event.preventDefault();
            downloadScreenshot();
        }
    });

})();
