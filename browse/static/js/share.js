/**
 * browse/static/js/share.js — Copy link toolbar.
 *
 * Self-contained module that injects a fixed-position toolbar at top-right
 * with a "Copy link" button. The copied URL strips any ?token= query param
 * to avoid leaking auth tokens in shared links.
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
     * Copy a clean URL to clipboard — strips only the token param so other
     * query params (session=, q=, a=, b=) stay intact.
     */
    function copyLink() {
        var u = new URL(location.href);
        u.searchParams.delete('token');
        var clean = u.toString();

        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(clean)
                .then(function () {
                    showToast('✓ Link copied!');
                })
                .catch(function (err) {
                    console.error('Clipboard API error:', err);
                    fallbackCopy(clean);
                });
        } else {
            fallbackCopy(clean);
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
     * Inject the toolbar into the page.
     */
    function injectToolbar() {
        var existing = document.getElementById(TOOLBAR_ID);
        if (existing) {
            return;
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
        copyBtn.addEventListener('mouseover', function () {
            this.style.backgroundColor = '#2980b9';
        });
        copyBtn.addEventListener('mouseout', function () {
            this.style.backgroundColor = '#3498db';
        });
        copyBtn.addEventListener('click', copyLink);

        toolbar.appendChild(copyBtn);
        document.body.appendChild(toolbar);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', injectToolbar);
    } else {
        injectToolbar();
    }

    document.addEventListener('keydown', function (event) {
        if (event.key === 'F13') {
            event.preventDefault();
            copyLink();
        }
    });

}());

