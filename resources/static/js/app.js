/*
 * iPXE PVE Controller — front-end behaviour.
 *
 * HTMX drives in-app navigation: sidebar and content links swap only
 * `#app-content` (see `hx-target`), giving a single-page-app feel without a
 * full page reload. This file wires up the small bits HTMX does not do for us.
 */
(function () {
    "use strict";

    // Preserve the "· App Name" suffix that layouts put in <title>.
    var titleSuffix = document.title.indexOf("·") !== -1
        ? document.title.substring(document.title.indexOf("·"))
        : "";

    function updateDocumentTitle() {
        var el = document.getElementById("page-title");
        if (el) {
            document.title = el.textContent.trim() + " " + titleSuffix;
        }
    }

    function dismissFlash(el) {
        setTimeout(function () {
            el.style.transition = "opacity 0.3s ease, transform 0.3s ease";
            el.style.opacity = "0";
            el.style.transform = "translateY(-6px)";
            setTimeout(function () { el.remove(); }, 300);
        }, 4500);
    }

    var FLASH_STYLES = {
        success: "border-emerald-500/30 bg-emerald-500/10 text-emerald-200",
        error: "border-rose-500/30 bg-rose-500/10 text-rose-200",
        warning: "border-amber-500/30 bg-amber-500/10 text-amber-200",
        info: "border-sky-500/30 bg-sky-500/10 text-sky-200"
    };
    var FLASH_ICONS = {
        success: "fa-circle-check", error: "fa-circle-exclamation",
        warning: "fa-triangle-exclamation", info: "fa-circle-info"
    };

    // Build a toast client-side. `textContent` is used deliberately so a
    // server-provided message can never inject markup (XSS-safe).
    function addFlash(category, message) {
        var container = document.getElementById("flash-container");
        if (!container || !message) {
            return;
        }
        var el = document.createElement("div");
        el.className = "pointer-events-auto flex items-start gap-3 rounded-lg border px-4 py-3 text-sm shadow-lg backdrop-blur " +
            (FLASH_STYLES[category] || FLASH_STYLES.info);
        el.setAttribute("data-flash", "");

        var icon = document.createElement("i");
        icon.className = "fa-solid " + (FLASH_ICONS[category] || FLASH_ICONS.info) + " mt-0.5";

        var span = document.createElement("span");
        span.className = "flex-1";
        span.textContent = message;

        el.appendChild(icon);
        el.appendChild(span);
        container.appendChild(el);
        dismissFlash(el);
    }

    // The sidebar lives outside #app-content, so HTMX does not re-render it.
    // Keep its active highlight in sync with the current URL ourselves.
    var NAV_ACTIVE = ["bg-brand-500/10", "text-brand-300", "font-medium"];
    var NAV_INACTIVE = ["text-slate-400", "hover:bg-slate-800/70", "hover:text-slate-100"];

    function navKeyForPath(path) {
        if (path.indexOf("/nodes") === 0) return "nodes";
        if (path.indexOf("/vms") === 0) return "vms";
        if (path.indexOf("/storage") === 0) return "storage";
        if (path.indexOf("/clusters") === 0) return "clusters";
        if (path.indexOf("/boot") === 0) return "boot";
        if (path.indexOf("/settings") === 0) return "settings";
        if (path.indexOf("/dashboard") === 0 || path === "/") return "dashboard";
        return null;
    }

    function updateSidebarActive() {
        var key = navKeyForPath(window.location.pathname);
        document.querySelectorAll("[data-nav]").forEach(function (el) {
            var isActive = el.getAttribute("data-nav") === key;
            NAV_ACTIVE.forEach(function (cls) { el.classList.toggle(cls, isActive); });
            NAV_INACTIVE.forEach(function (cls) { el.classList.toggle(cls, !isActive); });
        });
    }

    // Live boot-script preview. The renderer lives in boot-preview.js (mirrors
    // the Python BootConfig.render); the server stays authoritative on save.
    function updateBootPreview() {
        var form = document.getElementById("boot-form");
        var pre = document.getElementById("boot-preview");
        if (!form || !pre || !window.bootRenderScript) {
            return;
        }
        pre.textContent = window.bootRenderScript(form, pre.dataset.controllerDefault || "");
    }

    document.addEventListener("DOMContentLoaded", function () {
        updateDocumentTitle();
        updateSidebarActive();
        updateBootPreview();
        document.querySelectorAll("[data-flash]").forEach(dismissFlash);

        // Validation errors are returned with HTTP 422; tell HTMX to swap them
        // anyway so inline field errors show up without a page reload.
        document.body.addEventListener("htmx:beforeSwap", function (evt) {
            if (evt.detail.xhr.status === 422) {
                evt.detail.shouldSwap = true;
                evt.detail.isError = false;
            }
        });

        // If the session expired mid-navigation, send the user back to login.
        document.body.addEventListener("htmx:responseError", function (evt) {
            if (evt.detail.xhr.status === 401) {
                window.location.href = "/login";
            }
        });

        // Keep the document title in sync after every content swap.
        document.body.addEventListener("htmx:afterSwap", function (evt) {
            if (evt.detail.target && evt.detail.target.id === "app-content") {
                updateDocumentTitle();
            }
        });

        // Fade the content region out during navigation swaps.
        document.body.addEventListener("htmx:beforeSwap", function (evt) {
            if (evt.detail.target && evt.detail.target.id === "app-content") {
                evt.detail.target.classList.add("htmx-swapping");
            }
        });
        document.body.addEventListener("htmx:afterSettle", function (evt) {
            if (evt.detail.target && evt.detail.target.id === "app-content") {
                evt.detail.target.classList.remove("htmx-swapping");
            }
            updateSidebarActive();
            updateBootPreview();
        });

        // Back/forward navigation restores content from HTMX's history cache.
        document.body.addEventListener("htmx:historyRestore", function () {
            updateDocumentTitle();
            updateSidebarActive();
            updateBootPreview();
        });

        // Write actions (delete/reject/save/...) return an `app:flash` trigger
        // instead of reloading the page, so show the toasts in place.
        document.body.addEventListener("app:flash", function (evt) {
            var items = evt.detail;
            if (!items) {
                return;
            }
            if (!Array.isArray(items)) {
                items = [items];
            }
            items.forEach(function (item) {
                if (item && item.message) {
                    addFlash(item.category || "info", item.message);
                }
            });
        });
    });

    // Instant boot-script preview while typing (delegated, survives HTMX swaps).
    function isBootField(target) {
        return target && target.closest && target.closest("#boot-form");
    }
    document.addEventListener("input", function (evt) {
        if (isBootField(evt.target)) updateBootPreview();
    });
    document.addEventListener("change", function (evt) {
        if (isBootField(evt.target)) updateBootPreview();
    });

    // Copy-to-clipboard for elements marked with `data-copy-target` (works for
    // content swapped in by HTMX too, thanks to event delegation).
    document.addEventListener("click", function (evt) {
        var button = evt.target.closest("[data-copy-target]");
        if (!button) {
            return;
        }
        var source = document.querySelector(button.getAttribute("data-copy-target"));
        if (!source) {
            return;
        }
        var text = source.innerText;
        var original = button.innerHTML;
        function flash() {
            button.innerHTML = '<i class="fa-solid fa-check"></i><span class="ml-1">Copied</span>';
            setTimeout(function () { button.innerHTML = original; }, 1500);
        }
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(flash);
        } else {
            var area = document.createElement("textarea");
            area.value = text;
            document.body.appendChild(area);
            area.select();
            try { document.execCommand("copy"); flash(); } catch (e) {}
            document.body.removeChild(area);
        }
    });
})();
