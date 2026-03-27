(function () {
    "use strict";

    const TARGET_MODELS = new Set([
        "mrp.report.production.daily.line",
        "mrp.report.in_process.line",
        "mrp.report.raw_materials.line",
        "mrp.report.sales_no_stock.line",
        "mrp.report.quality.scrap.entry",
    ]);

    function renumber(root) {
        if (!root || !root.querySelectorAll) {
            return;
        }
        const tables = root.querySelectorAll(".o_list_renderer");
        tables.forEach((renderer) => {
            const model = renderer.getAttribute("data-res-model") || renderer.dataset.resModel;
            if (!TARGET_MODELS.has(model)) {
                return;
            }
            const rows = renderer.querySelectorAll("tbody tr.o_data_row");
            let index = 1;
            rows.forEach((row) => {
                const cell = row.querySelector('td[data-name="row_number"]');
                if (!cell) {
                    return;
                }
                cell.textContent = String(index);
                index += 1;
            });
        });
    }

    function ready(fn) {
        if (document.readyState !== "loading") {
            fn();
        } else {
            document.addEventListener("DOMContentLoaded", fn);
        }
    }

    ready(() => {
        renumber(document.body);
        const observer = new MutationObserver((mutations) => {
            mutations.forEach((mutation) => {
                mutation.addedNodes.forEach((node) => renumber(node));
                if (mutation.target) {
                    renumber(mutation.target);
                }
            });
        });
        observer.observe(document.body, { childList: true, subtree: true, characterData: true });
        document.addEventListener("click", () => {
            window.setTimeout(() => renumber(document.body), 0);
            window.setTimeout(() => renumber(document.body), 120);
        }, true);
    });
})();
