# -*- coding: utf-8 -*-
from collections import defaultdict
from datetime import timedelta
import unicodedata

from odoo import api, fields, models
from .opt_reports import _extract_suffix, _get_in_process_summary


class MrpPlantDashboard(models.TransientModel):
    _name = "mrp.plant.dashboard"
    _description = "Dashboard de Planta"

    def _format_user_datetime(self, dt_value):
        local_dt = fields.Datetime.context_timestamp(self, dt_value)
        return local_dt.strftime("%Y-%m-%d %H:%M:%S") if local_dt else ""

    def _normalize_text(self, value):
        text = (value or "").strip()
        text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
        return " ".join(text.lower().split())

    def _get_stage_config(self):
        return [
            {
                "key": "corte_vidrio",
                "label": "Corte de Vidrio",
                "short": "Corte",
                "color": "#0f766e",
                "keywords": ("cortar vidrio", "corte de vidrio", "corte vidrio"),
            },
            {
                "key": "lijado",
                "label": "Lijado",
                "short": "Lijado",
                "color": "#1d4ed8",
                "keywords": ("lijar", "lijado"),
            },
            {
                "key": "pulido",
                "label": "Pulido",
                "short": "Pulido",
                "color": "#7c3aed",
                "keywords": ("pulir", "pulido", "pulido estructural", "perforado antibala"),
            },
            {
                "key": "pintado",
                "label": "Pintado",
                "short": "Pintado",
                "color": "#b45309",
                "keywords": (
                    "pintar",
                    "pintura",
                    "ficha sr",
                    "lavar y espolvorear",
                    "lavar y espolvorear externo",
                    "lavar y espolvorear interno",
                    "lavar y espolvorear",
                    "lavar y espolvorear externo",
                    "lavar y espolvorear interno",
                    "lavar interno",
                    "lavado",
                ),
            },
            {
                "key": "curvado",
                "label": "Curvado",
                "short": "Curvado",
                "color": "#be123c",
                "keywords": ("curvado", "horno grande", "horno pequeno", "horno pequeño"),
            },
            {
                "key": "ensamblado",
                "label": "Ensamblado",
                "short": "Ensamblado",
                "color": "#166534",
                "keywords": (
                    "ensamblado",
                    "instalacion de pvb",
                    "corte de pvb",
                    "instalacion de antena",
                ),
            },
            {
                "key": "prevaciado_autoclave",
                "label": "Prevaciado y Autoclave",
                "short": "Prevaciado",
                "color": "#9333ea",
                "keywords": (
                    "prevaciado",
                    "prevacado",
                    "autoclave",
                    "laminado",
                    "instalacion de ficha sr",
                ),
            },
            {
                "key": "inspeccion",
                "label": "Inspeccion",
                "short": "Inspeccion",
                "color": "#0f172a",
                "keywords": (
                    "inspeccion final",
                    "inspection final",
                ),
            },
        ]

    def _get_family_config(self):
        return {
            "automotriz": {
                "key": "automotriz_progreso",
                "label": "Automotriz - En Progreso",
                "color": "#102a43",
            },
            "estructural": {
                "key": "estructural_progreso",
                "label": "Estructural - En Progreso",
                "color": "#7c2d12",
            },
            "other": {
                "key": "otros_progreso",
                "label": "Otros",
                "color": "#475569",
            },
        }

    def _match_stage(self, workorder, family=None):
        operation_name = ""
        if "operation_id" in workorder._fields and workorder.operation_id:
            operation_name = workorder.operation_id.name or ""
        workcenter_name = workorder.workcenter_id.name or "" if workorder.workcenter_id else ""
        normalized = self._normalize_text(" ".join(filter(None, [operation_name, workcenter_name])))

        if family == "estructural":
            if any(keyword in normalized for keyword in ("calandra", "salida de calandra", "prevaciado estructural", "laminado estructural")):
                return next((stage for stage in self._get_stage_config() if stage["key"] == "prevaciado_autoclave"), False)
            if "inspeccion final estructural" in normalized:
                return next((stage for stage in self._get_stage_config() if stage["key"] == "inspeccion"), False)

        for stage in self._get_stage_config():
            if any(keyword in normalized for keyword in stage["keywords"]):
                return stage
        return False

    def _get_product_family(self, product):
        complete_name = self._normalize_text(product.categ_id.complete_name if product and product.categ_id else "")
        if "automotriz" in complete_name:
            return "automotriz"
        if "estructural" in complete_name:
            return "estructural"
        return "other"

    def _get_workorder_product(self, workorder):
        if "product_id" in workorder._fields and workorder.product_id:
            return workorder.product_id
        production = workorder.production_id
        return production.product_id if production else False

    def _get_current_workorder_by_production(self, workorders):
        """Return the single open/current workorder that represents each MO in the dashboard.

        A production should only count once in "units in process" and only in its current
        stage, even if it still has several open future workorders.
        """
        production_map = {}
        for wo in workorders:
            production = wo.production_id
            if not production or (wo.state or "").strip().lower() in ("done", "cancel"):
                continue
            bucket = production_map.setdefault(production.id, [])
            bucket.append(wo)

        result = {}
        for production_id, production_workorders in production_map.items():
            ordered = sorted(
                production_workorders,
                key=lambda wo: (
                    0 if (wo.state or "").strip().lower() in ("progress", "ready") else 1,
                    0 if bool(getattr(wo, "date_start", False)) else 1,
                    0 if float(getattr(wo, "qty_produced", 0.0) or 0.0) > 0 else 1,
                    getattr(wo, "sequence", 0) if "sequence" in wo._fields else 0,
                    wo.id,
                ),
            )
            result[production_id] = ordered[0]
        return result

    def _empty_stage_metrics(self, stage):
        hours = []
        for step in range(11, -1, -1):
            hours.append({
                "label": f"-{step}h" if step else "Ahora",
                "started": 0,
                "finished": 0,
            })
        return {
            "key": stage["key"],
            "label": stage["label"],
            "short": stage["short"],
            "color": stage["color"],
            "total": 0,
            "active": 0,
            "queued": 0,
            "done": 0,
            "blocked": 0,
            "overdue": 0,
            "qty_in_progress": 0.0,
            "qty_finished": 0.0,
            "mo_total": 0,
            "mo_open": 0,
            "mo_running": 0,
            "wo_running": 0,
            "wo_paused": 0,
            "wo_stopped": 0,
            "avg_duration": 0.0,
            "avg_expected": 0.0,
            "efficiency_ratio": 0.0,
            "duration_status": "neutral",
            "state_counts": {
                "pending": 0,
                "ready": 0,
                "waiting": 0,
                "done": 0,
                "other": 0,
            },
            "state_products": {
                "pending": defaultdict(lambda: {"product_name": "", "qty": 0.0, "wo_count": 0}),
                "ready": defaultdict(lambda: {"product_name": "", "qty": 0.0, "wo_count": 0}),
                "waiting": defaultdict(lambda: {"product_name": "", "qty": 0.0, "wo_count": 0}),
                "done": defaultdict(lambda: {"product_name": "", "qty": 0.0, "wo_count": 0}),
                "other": defaultdict(lambda: {"product_name": "", "qty": 0.0, "wo_count": 0}),
            },
            "trend": hours,
            "running_tasks": [],
            "state_product_overflow": {
                "pending": 0,
                "ready": 0,
                "waiting": 0,
                "done": 0,
                "other": 0,
            },
            "material_summary": {
                "reserved": 0,
                "available": 0,
                "partial": 0,
                "blocked": 0,
                "missing_components": 0,
                "ready_percent": 0.0,
                "segments": [],
                "top_missing_materials": [],
            },
            "_done_count": 0,
            "_done_qty": 0.0,
            "_duration_total": 0.0,
            "_expected_total": 0.0,
            "_production_ids": set(),
            "_open_production_ids": set(),
            "_running_production_ids": set(),
        }

    def _empty_summary(self, label, color):
        return {
            "label": label,
            "color": color,
            "total_workorders": 0,
            "active_workorders": 0,
            "queued_workorders": 0,
            "blocked_workorders": 0,
            "finished_workorders": 0,
            "overdue_workorders": 0,
            "qty_in_progress": 0.0,
            "qty_finished": 0.0,
            "last_refresh": "",
            "_open_production_qty": {},
            "_finished_today_qty": {},
        }

    def _is_final_finished_product(self, product):
        code = (product.default_code or "").strip().upper() if product else ""
        return code.startswith("VLA-")

    def _is_today_in_user_tz(self, dt_value, today=None):
        if not dt_value:
            return False
        today = today or fields.Date.context_today(self)
        return fields.Datetime.context_timestamp(self, dt_value).date() == today

    def _empty_product_metrics(self, product, family):
        return {
            "product_id": product.id,
            "default_code": product.default_code or "",
            "product_name": product.display_name,
            "category_name": product.categ_id.display_name if product.categ_id else "",
            "family": family,
            "production_count": 0,
            "planned_qty": 0.0,
            "produced_qty": 0.0,
            "pending_mo_qty": 0.0,
            "in_process_qty": 0.0,
            "sales_pending_qty": 0.0,
            "active_workorders": 0,
            "queued_workorders": 0,
            "blocked_workorders": 0,
            "done_workorders": 0,
            "overdue_workorders": 0,
            "stage_totals": defaultdict(float),
            "stage_open_totals": defaultdict(float),
            "open_stage_keys": set(),
            "open_mos": set(),
            "in_process_stages": "",
            "_production_ids": set(),
        }

    def _get_elapsed_minutes(self, workorder, now):
        date_start = getattr(workorder, "date_start", False)
        if not date_start:
            return 0.0
        return max((now - date_start).total_seconds() / 60.0, 0.0)

    def _is_overdue(self, workorder, now):
        if (workorder.state or "").strip().lower() == "done":
            return False
        duration_expected = float(getattr(workorder, "duration_expected", 0.0) or 0.0)
        if duration_expected <= 0:
            return False
        elapsed_minutes = self._get_elapsed_minutes(workorder, now)
        return elapsed_minutes > (duration_expected * 1.15)

    def _get_sales_pending_map(self, product_ids):
        if not product_ids:
            return {}

        SaleLine = self.env["sale.order.line"].sudo()
        domain = [
            ("product_id", "in", list(product_ids)),
            ("order_id.state", "in", ["sale", "done"]),
        ]
        lines = SaleLine.search(domain)
        result = defaultdict(float)
        qty_delivered_field = "qty_delivered" if "qty_delivered" in SaleLine._fields else False
        qty_net_field = "ab_qty_to_deliver_net" if "ab_qty_to_deliver_net" in SaleLine._fields else False

        for line in lines:
            if qty_net_field:
                qty_pending = float(getattr(line, qty_net_field, 0.0) or 0.0)
            else:
                qty_pending = float(line.product_uom_qty or 0.0)
                if qty_delivered_field:
                    qty_pending -= float(line.qty_delivered or 0.0)
            if qty_pending > 0:
                result[line.product_id.id] += qty_pending
        return result

    def _serialize_products(self, products_map, sales_map):
        rows = []
        for data in products_map.values():
            data["sales_pending_qty"] = round(sales_map.get(data["product_id"], 0.0), 2)
            open_stage_parts = []
            for stage_label, count in sorted(data["stage_open_totals"].items()):
                if count:
                    open_stage_parts.append(f"{stage_label} ({round(count, 2)})")

            stage_parts = []
            for stage_label, count in sorted(data["stage_totals"].items()):
                if count:
                    stage_parts.append(f"{stage_label} ({round(count, 2)})")

            coverage = 0.0
            if data["sales_pending_qty"] > 0:
                coverage = round((data["in_process_qty"] / data["sales_pending_qty"]) * 100, 1)

            risk_level = "ok"
            if data["sales_pending_qty"] > data["in_process_qty"]:
                risk_level = "risk"
            elif data["active_workorders"] == 0 and data["sales_pending_qty"] > 0:
                risk_level = "watch"

            rows.append({
                "product_id": data["product_id"],
                "default_code": data["default_code"],
                "product_name": data["product_name"],
                "category_name": data["category_name"],
                "family": data["family"],
                "production_count": data["production_count"],
                "planned_qty": round(data["planned_qty"], 2),
                "produced_qty": round(data["produced_qty"], 2),
                "pending_mo_qty": round(data["pending_mo_qty"], 2),
                "in_process_qty": round(data["in_process_qty"], 2),
                "sales_pending_qty": data["sales_pending_qty"],
                "active_workorders": data["active_workorders"],
                "queued_workorders": data["queued_workorders"],
                "blocked_workorders": data["blocked_workorders"],
                "done_workorders": data["done_workorders"],
                "overdue_workorders": data["overdue_workorders"],
                "open_mo_count": len(data["open_mos"]),
                "open_stages": ", ".join(open_stage_parts) or "-",
                "all_stages": data["in_process_stages"] or (", ".join(stage_parts) or "-"),
                "open_stage_keys": sorted(data["open_stage_keys"]),
                "coverage_percent": coverage,
                "risk_level": risk_level,
            })

        rows.sort(
            key=lambda item: (
                item["family"] != "automotriz",
                item["risk_level"] == "ok",
                -item["sales_pending_qty"],
                -item["active_workorders"],
                -item["pending_mo_qty"],
                item["default_code"],
            )
        )
        return rows

    def _add_state_product_detail(self, metric, state_key, product, qty):
        if not product or state_key not in metric["state_products"]:
            return
        product_bucket = metric["state_products"][state_key][product.id]
        product_bucket["product_name"] = product.display_name
        product_bucket["qty"] += qty
        product_bucket["wo_count"] += 1

    def _classify_material_status(self, production):
        raw_moves = production.move_raw_ids.filtered(lambda m: m.state not in ("done", "cancel"))
        if not raw_moves:
            return {
                "status": "available",
                "missing_components": 0,
                "missing_materials": [],
            }

        all_reserved = True
        all_forecast = True
        any_forecast = False
        missing_components = 0
        missing_materials = []

        for move in raw_moves:
            required = float(move.product_uom_qty or 0.0)
            available = float(getattr(move, "availability", 0.0) or 0.0)
            forecast = float(getattr(move, "forecast_availability", 0.0) or 0.0)
            is_reserved = move.state == "assigned" or available >= required
            has_forecast = forecast >= required

            all_reserved = all_reserved and is_reserved
            all_forecast = all_forecast and has_forecast
            any_forecast = any_forecast or has_forecast

            if not has_forecast:
                missing_components += 1
                missing_materials.append({
                    "product_name": move.product_id.display_name if move.product_id else "",
                    "short_qty": round(max(required - forecast, 0.0), 2),
                    "uom": move.product_uom.name if move.product_uom else "",
                })

        if all_reserved:
            status = "reserved"
        elif all_forecast:
            status = "available"
        elif any_forecast:
            status = "partial"
        else:
            status = "blocked"

        return {
            "status": status,
            "missing_components": missing_components,
            "missing_materials": missing_materials,
        }

    def _compute_material_summary(self, production_ids):
        summary = {
            "reserved": 0,
            "available": 0,
            "partial": 0,
            "blocked": 0,
            "missing_components": 0,
            "ready_percent": 0.0,
            "segments": [],
            "top_missing_materials": [],
        }
        if not production_ids:
            return summary

        Production = self.env["mrp.production"].sudo().browse(list(production_ids))
        missing_totals = defaultdict(lambda: {"product_name": "", "short_qty": 0.0, "uom": ""})

        for production in Production:
            status_data = self._classify_material_status(production)
            summary[status_data["status"]] += 1
            summary["missing_components"] += status_data["missing_components"]
            for item in status_data["missing_materials"]:
                key = (item["product_name"], item["uom"])
                missing_totals[key]["product_name"] = item["product_name"]
                missing_totals[key]["uom"] = item["uom"]
                missing_totals[key]["short_qty"] += item["short_qty"]

        total = len(production_ids)
        ready_total = summary["reserved"] + summary["available"]
        summary["ready_percent"] = round((ready_total / total) * 100, 1) if total else 0.0
        segment_defs = [
            ("reserved", "Reservada", "#16a34a"),
            ("available", "Disponible", "#0f766e"),
            ("partial", "Parcial", "#d97706"),
            ("blocked", "Bloqueada", "#b91c1c"),
        ]
        summary["segments"] = [
            {"key": key, "label": label, "value": summary[key], "color": color}
            for key, label, color in segment_defs
            if summary[key] > 0
        ]
        top_missing = []
        for values in missing_totals.values():
            top_missing.append({
                "product_name": values["product_name"],
                "short_qty": round(values["short_qty"], 2),
                "uom": values["uom"],
            })
        top_missing.sort(key=lambda item: (-item["short_qty"], item["product_name"]))
        summary["top_missing_materials"] = top_missing[:5]
        return summary

    @api.model
    def get_progress_dashboard_data(self):
        now = fields.Datetime.now()
        today = fields.Date.context_today(self)
        recent_limit = now - timedelta(days=7)
        trend_limit = now - timedelta(hours=12)

        Workorder = self.env["mrp.workorder"].sudo()
        domain = []
        if "date_start" in Workorder._fields and "date_finished" in Workorder._fields:
            domain = [
                "|",
                "|",
                ("state", "not in", ["done", "cancel"]),
                ("date_start", ">=", fields.Datetime.to_string(recent_limit)),
                ("date_finished", ">=", fields.Datetime.to_string(recent_limit)),
            ]
        workorders = Workorder.search(domain, order="id desc")
        current_workorder_by_production = self._get_current_workorder_by_production(workorders)

        stage_configs = self._get_stage_config()
        family_config = self._get_family_config()
        metrics_by_family = {
            family_key: {
                "cards": {
                    stage["key"]: self._empty_stage_metrics(stage)
                    for stage in stage_configs
                },
                "summary": self._empty_summary(data["label"], data["color"]),
                "unmapped_samples": set(),
            }
            for family_key, data in family_config.items()
        }

        products_map = {}
        product_ids = set()
        in_process_summary = _get_in_process_summary(self.env, None)

        for wo in workorders:
            production = wo.production_id
            product = self._get_workorder_product(wo)
            family = self._get_product_family(product) if product else "other"
            family_bucket = metrics_by_family[family]

            stage = self._match_stage(wo, family=family)
            if not stage:
                sample = ""
                if "operation_id" in wo._fields and wo.operation_id:
                    sample = wo.operation_id.name or ""
                if not sample and wo.workcenter_id:
                    sample = wo.workcenter_id.name or ""
                if sample:
                    family_bucket["unmapped_samples"].add(sample)
                continue

            metric = family_bucket["cards"][stage["key"]]
            state = (wo.state or "").strip().lower()
            working_state = ((wo.working_state or "").strip().lower() if "working_state" in wo._fields else "")
            is_overdue = self._is_overdue(wo, now)
            duration = float(getattr(wo, "duration", 0.0) or 0.0)
            duration_expected = float(getattr(wo, "duration_expected", 0.0) or 0.0)

            metric["total"] += 1
            qty_producing = float(getattr(wo, "qty_producing", 0.0) or 0.0)
            qty_produced = float(getattr(wo, "qty_produced", 0.0) or 0.0)
            is_current_stage = bool(production and current_workorder_by_production.get(production.id) == wo)
            production_pending_qty = max(
                float(getattr(production, "product_qty", 0.0) or 0.0) - float(getattr(production, "qty_produced", 0.0) or 0.0),
                0.0,
            ) if production else 0.0
            stage_qty = production_pending_qty if state != "done" else (qty_produced or float(getattr(production, "product_qty", 0.0) or 0.0))
            if is_current_stage:
                metric["qty_in_progress"] += stage_qty
                if state != "done" and production:
                    family_bucket["summary"]["_open_production_qty"][production.id] = stage_qty
            if state == "done":
                metric["qty_finished"] += qty_produced
                if (
                    production
                    and self._is_final_finished_product(product)
                    and self._is_today_in_user_tz(getattr(production, "date_finished", False), today=today)
                ):
                    family_bucket["summary"]["_finished_today_qty"][production.id] = float(getattr(production, "product_qty", 0.0) or qty_produced or 0.0)
            if production:
                metric["_production_ids"].add(production.id)
                if state != "done":
                    metric["_open_production_ids"].add(production.id)

            if state == "done":
                metric["done"] += 1
                metric["state_counts"]["done"] += 1
                self._add_state_product_detail(metric, "done", product, qty_produced or qty_producing or 0.0)
            elif state in ("ready", "progress"):
                metric["active"] += 1
                metric["state_counts"]["ready"] += 1
                self._add_state_product_detail(metric, "ready", product, qty_producing or qty_produced or 0.0)
            elif state == "pending":
                metric["queued"] += 1
                metric["state_counts"]["pending"] += 1
                self._add_state_product_detail(metric, "pending", product, qty_producing or qty_produced or 0.0)
            elif state == "waiting":
                metric["blocked"] += 1
                metric["state_counts"]["waiting"] += 1
                self._add_state_product_detail(metric, "waiting", product, qty_producing or qty_produced or 0.0)
            else:
                if state:
                    metric["state_counts"]["other"] += 1
                self._add_state_product_detail(metric, "other", product, qty_producing or qty_produced or 0.0)
                if working_state and working_state != "normal":
                    metric["blocked"] += 1

            if is_overdue:
                metric["overdue"] += 1

            is_running = state in ("ready", "progress") and qty_producing > 0
            if is_running:
                metric["wo_running"] += 1
                if production:
                    metric["_running_production_ids"].add(production.id)
                elapsed_minutes = round(self._get_elapsed_minutes(wo, now), 1)
                metric["running_tasks"].append({
                    "workorder_name": wo.display_name,
                    "product_name": product.display_name if product else "",
                    "production_name": production.name if production else "",
                    "elapsed_minutes": elapsed_minutes,
                    "expected_minutes": round(duration_expected, 1),
                    "is_overdue": bool(duration_expected and elapsed_minutes > duration_expected),
                })
            elif state in ("waiting",):
                metric["wo_paused"] += 1
            else:
                metric["wo_stopped"] += 1

            if state == "done" and duration_expected > 0:
                done_qty = qty_produced or 0.0
                if done_qty > 0:
                    metric["_duration_total"] += duration
                    metric["_expected_total"] += duration_expected
                    metric["_done_qty"] += done_qty
                metric["_done_count"] += 1

            date_start = getattr(wo, "date_start", False)
            if date_start and date_start >= trend_limit:
                diff_hours = int((now - date_start).total_seconds() // 3600)
                if 0 <= diff_hours <= 11:
                    metric["trend"][11 - diff_hours]["started"] += 1

            date_finished = getattr(wo, "date_finished", False)
            if date_finished and date_finished >= trend_limit:
                diff_hours = int((now - date_finished).total_seconds() // 3600)
                if 0 <= diff_hours <= 11:
                    metric["trend"][11 - diff_hours]["finished"] += 1

            if product:
                product_ids.add(product.id)
                if product.id not in products_map:
                    products_map[product.id] = self._empty_product_metrics(product, family)
                product_data = products_map[product.id]
                if is_current_stage:
                    product_data["stage_totals"][stage["label"]] += stage_qty
                if state != "done" and is_current_stage:
                    product_data["stage_open_totals"][stage["label"]] += stage_qty
                    product_data["open_stage_keys"].add(stage["key"])
                if state in ("ready", "progress"):
                    product_data["active_workorders"] += 1
                elif state == "pending":
                    product_data["queued_workorders"] += 1
                elif state == "waiting":
                    product_data["blocked_workorders"] += 1
                elif state == "done":
                    product_data["done_workorders"] += 1
                if is_overdue:
                    product_data["overdue_workorders"] += 1

                if production:
                    if state != "done":
                        product_data["open_mos"].add(production.id)
                    if production.id not in product_data["_production_ids"]:
                        product_data["_production_ids"].add(production.id)
                        product_data["production_count"] += 1
                        planned_qty = float(getattr(production, "product_qty", 0.0) or 0.0)
                        produced_qty = float(getattr(production, "qty_produced", 0.0) or 0.0)
                        product_data["planned_qty"] += planned_qty
                        product_data["produced_qty"] += produced_qty
                        product_data["pending_mo_qty"] += max(planned_qty - produced_qty, 0.0)

        tabs = []
        for family in ("automotriz", "estructural"):
            family_data = metrics_by_family[family]
            cards = []
            summary = family_data["summary"]
            for stage in stage_configs:
                metric = family_data["cards"][stage["key"]]
                done_count = metric.pop("_done_count", 0)
                production_ids = metric.pop("_production_ids", set())
                open_production_ids = metric.pop("_open_production_ids", set())
                running_production_ids = metric.pop("_running_production_ids", set())
                metric["mo_total"] = len(production_ids)
                metric["mo_open"] = len(open_production_ids)
                metric["mo_running"] = len(running_production_ids)
                serialized_state_products = {}
                state_product_overflow = {}
                for state_key, products in metric.pop("state_products").items():
                    rows = []
                    for product_id, values in products.items():
                        rows.append({
                            "product_id": product_id,
                            "product_name": values["product_name"],
                            "qty": round(values["qty"], 2),
                        })
                    rows.sort(key=lambda item: (-item["qty"], item["product_name"]))
                    serialized_state_products[state_key] = rows
                    state_product_overflow[state_key] = 0
                metric["state_products"] = serialized_state_products
                metric["state_product_overflow"] = state_product_overflow
                metric["material_summary"] = self._compute_material_summary(open_production_ids)
                metric["running_tasks"] = sorted(
                    metric["running_tasks"],
                    key=lambda item: (-item["elapsed_minutes"], item["workorder_name"]),
                )[:5]
                done_qty = metric.pop("_done_qty", 0.0)
                duration_total = metric.pop("_duration_total", 0.0)
                expected_total = metric.pop("_expected_total", 0.0)
                if done_count and done_qty > 0:
                    metric["avg_duration"] = round(duration_total / done_qty, 2)
                    metric["avg_expected"] = round(expected_total / done_qty, 2)
                    if metric["avg_duration"] > 0:
                        metric["efficiency_ratio"] = round((metric["avg_expected"] / metric["avg_duration"]) * 100, 1)
                    if metric["avg_duration"] <= (metric["avg_expected"] * 1.2):
                        metric["duration_status"] = "good"
                    else:
                        metric["duration_status"] = "bad"
                metric["qty_in_progress"] = round(metric["qty_in_progress"], 2)
                metric["qty_finished"] = round(metric["qty_finished"], 2)
                cards.append(metric)

            summary["total_workorders"] = sum(card["total"] for card in cards)
            summary["active_workorders"] = sum(card["active"] for card in cards)
            summary["queued_workorders"] = sum(card["queued"] for card in cards)
            summary["blocked_workorders"] = sum(card["blocked"] for card in cards)
            summary["finished_workorders"] = sum(card["done"] for card in cards)
            summary["overdue_workorders"] = sum(card["overdue"] for card in cards)
            summary["qty_in_progress"] = round(sum(summary.pop("_open_production_qty", {}).values()), 2)
            summary["qty_finished"] = round(sum(summary.pop("_finished_today_qty", {}).values()), 2)
            summary["last_refresh"] = self._format_user_datetime(now)

            tabs.append({
                "key": family_config[family]["key"],
                "label": family_config[family]["label"],
                "type": "stages",
                "summary": summary,
                "cards": cards,
                "unmapped_samples": sorted(family_data["unmapped_samples"])[:20],
            })

        sales_map = self._get_sales_pending_map(product_ids)
        for product_data in products_map.values():
            suffix = _extract_suffix(product_data["default_code"])
            summary = in_process_summary.get(suffix) or {}
            product_data["in_process_qty"] = round(
                float(summary.get("s1", 0.0) or 0.0)
                + float(summary.get("s2", 0.0) or 0.0)
                + float(summary.get("s3", 0.0) or 0.0)
                + float(summary.get("pt", 0.0) or 0.0),
                2,
            )
            stage_parts = []
            for stage_key, label in (("s1", "S1"), ("s2", "S2"), ("s3", "S3"), ("pt", "PT")):
                qty = float(summary.get(stage_key, 0.0) or 0.0)
                if qty > 0.0:
                    stage_parts.append("%s (%s)" % (label, round(qty, 2)))
            product_data["in_process_stages"] = ", ".join(stage_parts)
        product_rows = self._serialize_products(products_map, sales_map)
        product_summary = self._empty_summary("Productos", "#1e293b")
        product_summary.update({
            "products_count": len(product_rows),
            "active_products": sum(1 for row in product_rows if row["active_workorders"]),
            "total_workorders": sum(
                row["active_workorders"] + row["queued_workorders"] + row["blocked_workorders"] + row["done_workorders"]
                for row in product_rows
            ),
            "active_workorders": sum(row["active_workorders"] for row in product_rows),
            "queued_workorders": sum(row["queued_workorders"] for row in product_rows),
            "blocked_workorders": sum(row["blocked_workorders"] for row in product_rows),
            "sales_pending_qty": round(sum(row["sales_pending_qty"] for row in product_rows), 2),
            "pending_mo_qty": round(sum(row["pending_mo_qty"] for row in product_rows), 2),
            "in_process_qty": round(sum(row["in_process_qty"] for row in product_rows), 2),
            "overdue_workorders": sum(row["overdue_workorders"] for row in product_rows),
            "last_refresh": self._format_user_datetime(now),
        })
        tabs.append({
            "key": "productos",
            "label": "Productos",
            "type": "products",
            "summary": product_summary,
            "stage_options": [
                {"key": stage["key"], "label": stage["label"]}
                for stage in stage_configs
            ],
            "rows": product_rows,
        })

        overall_summary = self._empty_summary("Planta", "#111827")
        overall_summary.update({
            "total_workorders": sum(tab["summary"].get("total_workorders", 0) for tab in tabs if tab["type"] == "stages"),
            "active_workorders": sum(tab["summary"].get("active_workorders", 0) for tab in tabs if tab["type"] == "stages"),
            "queued_workorders": sum(tab["summary"].get("queued_workorders", 0) for tab in tabs if tab["type"] == "stages"),
            "blocked_workorders": sum(tab["summary"].get("blocked_workorders", 0) for tab in tabs if tab["type"] == "stages"),
            "finished_workorders": sum(tab["summary"].get("finished_workorders", 0) for tab in tabs if tab["type"] == "stages"),
            "overdue_workorders": sum(tab["summary"].get("overdue_workorders", 0) for tab in tabs if tab["type"] == "stages"),
            "qty_in_progress": round(sum(tab["summary"].get("qty_in_progress", 0) for tab in tabs if tab["type"] == "stages"), 2),
            "qty_finished": round(sum(tab["summary"].get("qty_finished", 0) for tab in tabs if tab["type"] == "stages"), 2),
            "last_refresh": self._format_user_datetime(now),
        })

        return {
            "summary": overall_summary,
            "tabs": tabs,
        }
