/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, onMounted, onWillStart, onWillUnmount, useState } from "@odoo/owl";

class PlantDashboard extends Component {
    static template = "alterben_mrp_master_order.PlantDashboard";

    setup() {
        this.orm = useService("orm");
        this.state = useState({
            loading: true,
            activeTab: "automotriz_progreso",
            productQuery: "",
            productStage: "",
            productSegment: "",
            helpOpen: false,
            payload: { summary: {}, tabs: [], unmapped_samples: [] },
        });
        this._intervalId = null;

        onWillStart(async () => {
            await this.loadDashboard();
        });

        onMounted(() => {
            this._intervalId = setInterval(() => this.loadDashboard(), 45000);
        });

        onWillUnmount(() => {
            if (this._intervalId) {
                clearInterval(this._intervalId);
            }
        });
    }

    async loadDashboard() {
        const payload = await this.orm.call("mrp.plant.dashboard", "get_progress_dashboard_data", []);
        this.state.payload = payload;
        if (!payload.tabs.find((tab) => tab.key === this.state.activeTab) && payload.tabs.length) {
            this.state.activeTab = payload.tabs[0].key;
        }
        this.state.loading = false;
    }

    setTab(tabKey) {
        this.state.activeTab = tabKey;
    }

    toggleHelp() {
        this.state.helpOpen = !this.state.helpOpen;
    }

    get activeTab() {
        return this.state.payload.tabs.find((tab) => tab.key === this.state.activeTab) || this.state.payload.tabs[0];
    }

    get activeSummary() {
        return this.activeTab?.summary || this.state.payload.summary || {};
    }

    get visibleStageCards() {
        if (!this.activeTab || this.activeTab.type !== "stages") {
            return [];
        }
        return (this.activeTab.cards || []).filter((card) =>
            (card.total || 0) > 0 ||
            (card.mo_total || 0) > 0 ||
            (card.qty_in_progress || 0) > 0 ||
            (card.qty_finished || 0) > 0
        );
    }

    setProductQuery(value) {
        this.state.productQuery = value || "";
    }

    setProductStage(value) {
        this.state.productStage = value || "";
    }

    setProductSegment(value) {
        this.state.productSegment = value || "";
    }

    get filteredProductRows() {
        if (!this.activeTab || this.activeTab.type !== "products") {
            return [];
        }
        const query = this.state.productQuery.trim().toLowerCase();
        const stage = this.state.productStage;
        const segment = this.state.productSegment;
        return (this.activeTab.rows || []).filter((row) => {
            const matchesQuery = !query || [
                row.default_code || "",
                row.product_name || "",
                row.category_name || "",
            ].some((value) => value.toLowerCase().includes(query));
            const matchesStage = !stage || (row.open_stage_keys || []).includes(stage);
            let matchesSegment = true;
            const category = (row.category_name || "").toUpperCase();
            if (segment === "m_grandes") {
                matchesSegment = category.includes("M GRANDES");
            } else if (segment === "m_pequenas") {
                matchesSegment = category.includes("M PEQUE") || category.includes("M PEQUENAS");
            } else if (segment === "estructural") {
                matchesSegment = (row.family || "") === "estructural" || category.includes("ESTRUCTURAL");
            }

            let matchesStageProductRule = true;
            const code = (row.default_code || "").toUpperCase();
            const isStructural = (row.family || "") === "estructural" || category.includes("ESTRUCTURAL");
            if (stage === "corte_vidrio") {
                // En corte automotriz mostrar piezas VI/VE; para estructural se permite la referencia estructural.
                matchesStageProductRule = code.startsWith("VI-") || code.startsWith("VE-") || isStructural;
            } else if (stage === "lijado") {
                matchesStageProductRule = code.startsWith("S2-") || isStructural;
            } else if (stage === "pintado") {
                matchesStageProductRule = code.startsWith("S2-");
            } else if (stage === "pulido") {
                matchesStageProductRule = code.startsWith("S2-") || isStructural;
            }

            return matchesQuery && matchesStage && matchesSegment && matchesStageProductRule;
        });
    }

    getCardBarStyle(card, key) {
        const total = card.total || 1;
        const value = card.state_counts[key] || 0;
        const percent = Math.max(6, Math.round((value / total) * 100));
        return `width:${percent}%;`;
    }

    getTrendMax(card) {
        const allValues = card.trend.flatMap((point) => [point.started, point.finished]);
        return Math.max(1, ...allValues);
    }

    getTrendHeight(value, maxValue) {
        return Math.max(6, Math.round((value / Math.max(1, maxValue)) * 64));
    }

    getTrendPairHeight(point, maxValue) {
        return Math.max(8, Math.round((Math.max(point.started, point.finished) / Math.max(1, maxValue)) * 72));
    }

    getTrendPairStyle(point, maxValue) {
        return `height:${this.getTrendPairHeight(point, maxValue)}px;`;
    }

    getTrendStartedStyle(point, maxValue) {
        const ratio = point.started / Math.max(1, maxValue);
        const width = Math.max(18, Math.round(ratio * 100));
        return `width:${Math.min(width, 100)}%;`;
    }

    getTrendFinishedStyle(point, maxValue) {
        const ratio = point.finished / Math.max(1, maxValue);
        const width = Math.max(18, Math.round(ratio * 100));
        return `width:${Math.min(width, 100)}%;`;
    }

    getDonutSegments(card) {
        const segments = card.material_summary?.segments || [];
        const total = segments.reduce((sum, segment) => sum + segment.value, 0);
        if (!total) {
            return [];
        }

        let currentAngle = -90;
        return segments
            .filter((segment) => segment.value > 0)
            .map((segment) => {
                const angle = (segment.value / total) * 360;
                const startAngle = currentAngle;
                const endAngle = currentAngle + angle;
                currentAngle = endAngle;
                return {
                    ...segment,
                    path: this.describeArc(50, 50, 34, startAngle, endAngle),
                };
            });
    }

    polarToCartesian(centerX, centerY, radius, angleInDegrees) {
        const angleInRadians = ((angleInDegrees - 90) * Math.PI) / 180.0;
        return {
            x: centerX + radius * Math.cos(angleInRadians),
            y: centerY + radius * Math.sin(angleInRadians),
        };
    }

    describeArc(x, y, radius, startAngle, endAngle) {
        const start = this.polarToCartesian(x, y, radius, endAngle);
        const end = this.polarToCartesian(x, y, radius, startAngle);
        const largeArcFlag = endAngle - startAngle <= 180 ? "0" : "1";
        return [
            "M", start.x, start.y,
            "A", radius, radius, 0, largeArcFlag, 0, end.x, end.y,
        ].join(" ");
    }

    getCardTooltip(card) {
        return [
            `${card.label}`,
            `WO totales: ${card.total || 0}`,
            `WO activas: ${card.active || 0}`,
            `En cola: ${card.queued || 0}`,
            `Bloqueadas: ${card.blocked || 0}`,
            `Atrasadas: ${card.overdue || 0}`,
            `Terminadas: ${card.done || 0}`,
            `Unidades en proceso: ${card.qty_in_progress || 0}`,
            `Unidades terminadas: ${card.qty_finished || 0}`,
            `Duracion promedio: ${card.avg_duration || 0} min`,
            `Duracion esperada: ${card.avg_expected || 0} min`,
        ].join("\n");
    }

    getStateTooltip(card, key, label) {
        const value = card.state_counts[key] || 0;
        const details = card.state_products?.[key] || [];
        const lines = [`${card.label}`, `${label}: ${value} de ${card.total || 0} workorders`];
        if (details.length) {
            lines.push("Productos:");
            details.forEach((item) => {
                lines.push(`${item.product_name} | Cant. ${item.qty} | WOs ${item.wo_count}`);
            });
        } else {
            lines.push("Sin productos registrados en este estado");
        }
        return lines.join("\n");
    }

    getTrendTooltip(point, card) {
        return `${card.label}\n${point.label}\nIniciadas: ${point.started}\nTerminadas: ${point.finished}`;
    }

    getProductRowTooltip(row) {
        return [
            `${row.default_code || "-"} - ${row.product_name}`,
            `Categoria: ${row.category_name || "-"}`,
            `Familia: ${row.family || "-"}`,
            `MO planificadas: ${row.planned_qty || 0}`,
            `MO producidas: ${row.produced_qty || 0}`,
            `En proceso real: ${row.in_process_qty || 0}`,
            `Pendiente ventas: ${row.sales_pending_qty || 0}`,
            `Cobertura vs ventas: ${row.coverage_percent || 0}%`,
            `Etapas abiertas: ${row.open_stages || "-"}`,
            `Desglose en proceso: ${row.all_stages || "-"}`,
        ].join("\n");
    }

    getRunningTasksTooltip(card) {
        if (!card.running_tasks?.length) {
            return `${card.label}\nSin tareas corriendo en este momento`;
        }
        return [
            `${card.label}`,
            ...card.running_tasks.map((task) =>
                `${task.workorder_name} | ${task.product_name} | ${task.elapsed_minutes} min de ${task.expected_minutes} min${task.is_overdue ? " | atrasada" : ""}`
            ),
        ].join("\n");
    }
}

registry.category("actions").add("alterben_mrp_master_order.plant_dashboard", PlantDashboard);
