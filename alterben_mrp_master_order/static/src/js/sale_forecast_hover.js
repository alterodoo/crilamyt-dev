(function () {
    "use strict";

    const cache = new Map();
    let active = null;
    let hideTimer = null;
    let showTimer = null;

    function toNumber(value) {
        const n = parseInt(value, 10);
        return Number.isFinite(n) ? n : null;
    }

    function formatQty(value) {
        const number = Number(value || 0);
        return new Intl.NumberFormat("es-EC", {
            minimumFractionDigits: 0,
            maximumFractionDigits: 2,
        }).format(number);
    }

    function escapeHtml(value) {
        return String(value || "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function formatDate(value) {
        if (!value) {
            return "-";
        }
        const parsed = new Date(String(value).replace(" ", "T"));
        if (Number.isNaN(parsed.getTime())) {
            return escapeHtml(value);
        }
        return new Intl.DateTimeFormat("es-EC", {
            year: "numeric",
            month: "2-digit",
            day: "2-digit",
            hour: "2-digit",
            minute: "2-digit",
        }).format(parsed);
    }

    function getRowIdFromElement(element) {
        const tr = element && element.closest ? element.closest("tr") : null;
        if (!tr) {
            return null;
        }
        const idCell = tr.querySelector('td[data-name="ab_hover_line_id"]');
        if (idCell) {
            const numeric = toNumber((idCell.textContent || "").trim());
            if (numeric !== null) {
                return numeric;
            }
        }
        const keys = ["data-res-id", "data-id", "data-oe-id"];
        for (const key of keys) {
            const value = tr.getAttribute(key);
            const numeric = toNumber(value);
            if (numeric !== null) {
                return numeric;
            }
        }
        if (tr.dataset) {
            const datasetKeys = ["resId", "id", "oeId"];
            for (const key of datasetKeys) {
                const numeric = toNumber(tr.dataset[key]);
                if (numeric !== null) {
                    return numeric;
                }
            }
        }
        const checkbox = tr.querySelector('input.o_list_record_selector, input[type="checkbox"][name="ids"]');
        if (checkbox) {
            const numeric = toNumber(checkbox.value);
            if (numeric !== null) {
                return numeric;
            }
        }
        const link = tr.querySelector('a[href*="#id="]');
        if (link) {
            const href = link.getAttribute("href") || "";
            const match = href.match(/[#&]id=(\d+)/);
            if (match) {
                const numeric = toNumber(match[1]);
                if (numeric !== null) {
                    return numeric;
                }
            }
        }
        const fieldWidgets = tr.querySelectorAll("[name]");
        for (const widget of fieldWidgets) {
            const attrs = ["data-context", "context", "data-oe-context"];
            for (const attr of attrs) {
                const raw = widget.getAttribute && widget.getAttribute(attr);
                if (!raw) {
                    continue;
                }
                const patterns = [/"id"\s*:\s*(\d+)/, /'id'\s*:\s*(\d+)/, /"res_id"\s*:\s*(\d+)/, /'res_id'\s*:\s*(\d+)/];
                for (const pattern of patterns) {
                    const match = raw.match(pattern);
                    if (match) {
                        const numeric = toNumber(match[1]);
                        if (numeric !== null) {
                            return numeric;
                        }
                    }
                }
            }
        }
        return null;
    }

    function isInsideSaleOrderLine(anchor) {
        if (!anchor || !anchor.closest) {
            return false;
        }
        const row = anchor.closest("tr");
        if (!row) {
            return false;
        }
        if (row.closest('.o_field_x2many[name="order_line"]')) {
            return true;
        }
        const form = row.closest(".o_form_view");
        if (!form) {
            return false;
        }
        const resModel = form.getAttribute("data-res-model") || form.dataset.resModel;
        if (resModel && resModel !== "sale.order") {
            return false;
        }
        return Boolean(
            row.querySelector('td[data-name="product_template_id"], td[data-name="product_id"], td[data-name="x_studio_cantidad_a_la_mano"], td[data-name="free_qty_today"], td[data-name="forecast_expected_date"]')
            || row.querySelector('.o_field_widget[name="product_template_id"], .o_field_widget[name="product_id"], .o_field_widget[name="x_studio_cantidad_a_la_mano"], .o_field_widget[name="free_qty_today"], .o_field_widget[name="forecast_expected_date"]')
        );
    }

    async function fetchPayload(lineId) {
        if (cache.has(lineId)) {
            return cache.get(lineId);
        }
        const payload = {
            jsonrpc: "2.0",
            method: "call",
            params: {
                model: "sale.order.line",
                method: "get_ab_forecast_hover_data",
                args: [[lineId]],
                kwargs: {},
            },
            id: Date.now(),
        };
        const response = await fetch("/web/dataset/call_kw", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            credentials: "same-origin",
            body: JSON.stringify(payload),
        });
        const data = await response.json();
        if (!data || typeof data.result === "undefined") {
            throw new Error("Sin resultado");
        }
        cache.set(lineId, data.result);
        return data.result;
    }

    function renderReservations(items) {
        if (!items.length) {
            return '<div class="ab-forecast-empty">Sin reservas o despachos abiertos para este producto.</div>';
        }
        return items.slice(0, 8).map((item) => `
            <div class="ab-forecast-item ${item.is_current_line ? "is-current" : ""}">
                <div class="ab-forecast-item-title">${escapeHtml(item.sale_order || "Sin SO")} <span>${escapeHtml(item.partner || "Sin cliente")}</span></div>
                <div class="ab-forecast-item-meta">Reservado: <strong>${formatQty(item.reserved_qty)}</strong> | Fecha: ${formatDate(item.date)}</div>
                <div class="ab-forecast-item-meta">Ubicacion: ${escapeHtml(item.location_leaf || item.location || "-")} | Picking: ${escapeHtml(item.picking || "-")}</div>
            </div>
        `).join("");
    }

    function renderCafLocations(items) {
        if (!items.length) {
            return '<div class="ab-forecast-empty">Sin stock del producto en ubicaciones CAF.</div>';
        }
        return items.map((item) => `
            <div class="ab-forecast-item">
                <div class="ab-forecast-item-title">${escapeHtml(item.location_leaf || item.location || "CAF")}</div>
                <div class="ab-forecast-item-meta">Cantidad: <strong>${formatQty(item.qty)}</strong></div>
            </div>
        `).join("");
    }

    function renderProductions(items) {
        if (!items.length) {
            return '<div class="ab-forecast-empty">Sin ordenes de fabricacion abiertas para este producto.</div>';
        }
        return items.slice(0, 8).map((item) => {
            const commitments = item.commitments && item.commitments.length
                ? item.commitments.slice(0, 4).map((commitment) => `
                    <div class="ab-forecast-subitem">
                        ${escapeHtml(commitment.sale_order || "Sin SO")} | ${escapeHtml(commitment.partner || "Sin cliente")} | ${formatQty(commitment.qty)}
                    </div>
                `).join("")
                : '<div class="ab-forecast-subitem is-empty">Sin compromisos detectados.</div>';
            return `
                <div class="ab-forecast-item">
                    <div class="ab-forecast-item-title">${escapeHtml(item.name)} <span>${escapeHtml(item.state_label || item.state || "-")}</span></div>
                    <div class="ab-forecast-item-meta">Cantidad OF: <strong>${formatQty(item.qty)}</strong> | Salida estimada: ${formatDate(item.planned_out)}</div>
                    <div class="ab-forecast-item-meta">Comprometido: ${formatQty(item.committed_qty)} | Disponible: <strong>${formatQty(item.available_qty)}</strong></div>
                    <div class="ab-forecast-sublist">${commitments}</div>
                </div>
            `;
        }).join("");
    }

    function buildPopover(payload) {
        const summary = payload.summary || {};
        const wrapper = document.createElement("div");
        wrapper.className = "ab-sale-forecast-popover";
        wrapper.innerHTML = `
            <div class="ab-forecast-header">
                <div class="ab-forecast-title">${escapeHtml(payload.default_code || "")} ${escapeHtml(payload.product_name || "")}</div>
                <div class="ab-forecast-subtitle">${escapeHtml(payload.warehouse || "Sin bodega definida")}</div>
            </div>
            <div class="ab-forecast-grid">
                <div class="ab-forecast-kpi">
                    <span>A la mano</span>
                    <strong>${formatQty(summary.qty_on_hand)}</strong>
                </div>
                <div class="ab-forecast-kpi">
                    <span>Reservado</span>
                    <strong>${formatQty(summary.qty_reserved)}</strong>
                </div>
                <div class="ab-forecast-kpi">
                    <span>En CAF</span>
                    <strong>${formatQty(summary.qty_caf)}</strong>
                </div>
                <div class="ab-forecast-kpi is-highlight">
                    <span>Disponible</span>
                    <strong>${formatQty(summary.qty_available_net)}</strong>
                </div>
            </div>
            <div class="ab-forecast-legend">Libre hoy: <strong>${formatQty(summary.free_qty_today)}</strong> | Fecha pronosticada: <strong>${formatDate(summary.forecast_expected_date)}</strong></div>
            <div class="ab-forecast-section">
                <div class="ab-forecast-section-title">Reservas y despachos</div>
                ${renderReservations(payload.reservations || [])}
            </div>
            <div class="ab-forecast-section">
                <div class="ab-forecast-section-title">Stock en CAF</div>
                ${renderCafLocations(payload.caf_locations || [])}
            </div>
            <div class="ab-forecast-section">
                <div class="ab-forecast-section-title">Fabricacion relacionada</div>
                ${renderProductions(payload.productions || [])}
            </div>
        `;
        wrapper.addEventListener("mouseenter", () => {
            if (hideTimer) {
                window.clearTimeout(hideTimer);
                hideTimer = null;
            }
        });
        wrapper.addEventListener("mouseleave", scheduleHide);
        return wrapper;
    }

    function getAnchorRect(anchor) {
        const cell = anchor.closest ? anchor.closest("td, .o_data_cell") : null;
        return (cell || anchor).getBoundingClientRect();
    }

    function positionPopover(anchor, popover) {
        const rect = getAnchorRect(anchor);
        const width = Math.min(520, window.innerWidth - 24);
        popover.style.maxWidth = `${width}px`;
        popover.style.left = `${Math.max(12, Math.min(window.innerWidth - width - 12, rect.right + 8))}px`;
        const desiredTop = rect.top + window.scrollY - 8;
        const maxTop = window.scrollY + window.innerHeight - popover.offsetHeight - 12;
        popover.style.top = `${Math.max(window.scrollY + 12, Math.min(desiredTop, maxTop))}px`;
    }

    function clearActive() {
        if (active && active.popover && active.popover.parentNode) {
            active.popover.parentNode.removeChild(active.popover);
        }
        active = null;
    }

    function scheduleHide() {
        if (hideTimer) {
            window.clearTimeout(hideTimer);
        }
        hideTimer = window.setTimeout(clearActive, 180);
    }

    async function showPopover(anchor) {
        const lineId = getRowIdFromElement(anchor);
        if (!lineId) {
            return;
        }
        if (active && active.lineId === lineId) {
            if (hideTimer) {
                window.clearTimeout(hideTimer);
                hideTimer = null;
            }
            return;
        }
        clearActive();

        const placeholder = document.createElement("div");
        placeholder.className = "ab-sale-forecast-popover is-loading";
        placeholder.innerHTML = '<div class="ab-forecast-loading">Cargando detalle del pronostico...</div>';
        document.body.appendChild(placeholder);
        positionPopover(anchor, placeholder);
        active = { lineId, anchor, popover: placeholder };

        try {
            const payload = await fetchPayload(lineId);
            if (!active || active.lineId !== lineId) {
                return;
            }
            const popover = buildPopover(payload || {});
            document.body.replaceChild(popover, placeholder);
            positionPopover(anchor, popover);
            active.popover = popover;
        } catch (error) {
            if (!active || active.lineId !== lineId) {
                return;
            }
            placeholder.innerHTML = '<div class="ab-forecast-loading is-error">No se pudo cargar el detalle del pronostico.</div>';
            positionPopover(anchor, placeholder);
        }
    }

    function scheduleShow(anchor) {
        if (!anchor || !isInsideSaleOrderLine(anchor)) {
            return;
        }
        anchor.classList.add("ab-sale-forecast-anchor");
        if (showTimer) {
            window.clearTimeout(showTimer);
        }
        if (hideTimer) {
            window.clearTimeout(hideTimer);
            hideTimer = null;
        }
        showTimer = window.setTimeout(() => showPopover(anchor), 180);
    }

    function handleAnchorLeave(event, anchor) {
        const related = event.relatedTarget;
        if (related && (anchor.contains(related) || (active && active.popover && active.popover.contains(related)))) {
            return;
        }
        if (showTimer) {
            window.clearTimeout(showTimer);
            showTimer = null;
        }
        scheduleHide();
    }

    function bindAnchor(anchor) {
        if (!anchor || anchor.dataset.abForecastBound) {
            return;
        }
        anchor.dataset.abForecastBound = "1";
        anchor.classList.add("ab-sale-forecast-anchor");
        anchor.addEventListener("mouseenter", () => scheduleShow(anchor));
        anchor.addEventListener("mouseleave", (event) => handleAnchorLeave(event, anchor));
    }

    function install(root) {
        if (!root || !root.querySelectorAll) {
            return;
        }
        const selectors = [
            'td[data-name="x_studio_cantidad_a_la_mano"]',
            'td[data-name="forecast_expected_date"]',
            'td[data-name="free_qty_today"]',
            '.o_widget_qty_at_date_widget a.fa-area-chart',
            '.o_widget_qty_at_date_widget a.fa-chart-area',
            '.o_widget_qty_at_date_widget a.fa-line-chart',
            '.o_widget_qty_at_date_widget a.fa-bar-chart',
            '.o_widget_qty_at_date_widget',
        ];
        root.querySelectorAll(selectors.join(",")).forEach((anchor) => bindAnchor(anchor));
    }

    function ready(fn) {
        if (document.readyState !== "loading") {
            fn();
        } else {
            document.addEventListener("DOMContentLoaded", fn);
        }
    }

    ready(() => {
        install(document.body);
        const observer = new MutationObserver((mutations) => {
            mutations.forEach((mutation) => {
                mutation.addedNodes.forEach((node) => install(node));
            });
        });
        observer.observe(document.body, { childList: true, subtree: true });
        window.addEventListener("scroll", () => {
            if (active && active.anchor && active.popover) {
                positionPopover(active.anchor, active.popover);
            }
        }, true);
        window.addEventListener("resize", () => {
            if (active && active.anchor && active.popover) {
                positionPopover(active.anchor, active.popover);
            }
        });
    });
})();
