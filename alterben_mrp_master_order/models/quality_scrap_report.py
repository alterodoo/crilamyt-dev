# -*- coding: utf-8 -*-
from datetime import datetime, time
import pytz

from odoo import api, fields, models, _
from odoo.exceptions import UserError


def _start_of_day(date_value):
    if not date_value:
        return False
    return datetime.combine(date_value, time.min)


def _end_of_day(date_value):
    if not date_value:
        return False
    return datetime.combine(date_value, time.max)


def _get_user_tz(env):
    return pytz.timezone(env.context.get("tz") or env.user.tz or "UTC")


def _local_day_bounds_to_utc(env, date_from=False, date_to=False):
    user_tz = _get_user_tz(env)
    start_utc = False
    end_utc = False
    if date_from:
        local_start = user_tz.localize(datetime.combine(date_from, time.min))
        start_utc = local_start.astimezone(pytz.UTC).replace(tzinfo=None)
    if date_to:
        local_end = user_tz.localize(datetime.combine(date_to, time.max))
        end_utc = local_end.astimezone(pytz.UTC).replace(tzinfo=None)
    return start_utc, end_utc


class MRPReportQualityScrap(models.TransientModel):
    _name = "mrp.report.quality.scrap"
    _description = "Reporte Desechos y Calidad"

    date_from = fields.Date(string="Desde", default=fields.Date.context_today, required=True)
    date_to = fields.Date(string="Hasta", default=fields.Date.context_today, required=True)
    product_ids = fields.Many2many("product.product", string="Productos")
    categ_ids = fields.Many2many("product.category", string="Categorias")
    operation_filter = fields.Char(string="Operacion")
    line_ids = fields.One2many("mrp.report.quality.scrap.entry", "wizard_id", string="Lineas")
    total_records = fields.Integer(string="Total registros", compute="_compute_totals", store=False)
    total_linked = fields.Integer(string="Vinculados", compute="_compute_totals", store=False)
    total_alert_only = fields.Integer(string="Solo alertas", compute="_compute_totals", store=False)
    total_scrap_only = fields.Integer(string="Solo desechos", compute="_compute_totals", store=False)
    total_scrap_qty = fields.Float(string="Cantidad desecho", compute="_compute_totals", store=False)

    @api.depends("line_ids.entry_type", "line_ids.scrap_qty")
    def _compute_totals(self):
        for rec in self:
            rec.total_records = len(rec.line_ids)
            rec.total_linked = len(rec.line_ids.filtered(lambda l: l.entry_type == "linked"))
            rec.total_alert_only = len(rec.line_ids.filtered(lambda l: l.entry_type == "alert_only"))
            rec.total_scrap_only = len(rec.line_ids.filtered(lambda l: l.entry_type == "scrap_only"))
            rec.total_scrap_qty = sum(rec.line_ids.mapped("scrap_qty"))

    def _normalize_key(self, value):
        if hasattr(value, "display_name"):
            value = value.display_name or value.name or ""
        return (value or "").strip().upper()

    def _extract_alert_name(self, value):
        if hasattr(value, "display_name"):
            value = value.display_name or value.name or ""
        raw = (value or "").strip()
        if not raw:
            return ""
        return raw.split(" - ")[0].strip()

    def _resolve_linked_scrap(self, alert, scrap_by_name):
        if getattr(alert, "scrap_id", False):
            return alert.scrap_id
        studio_name = getattr(alert, "x_studio_desecho_id", False)
        if studio_name:
            return scrap_by_name.get(self._normalize_key(studio_name))
        return False

    def _resolve_linked_alert(self, scrap, alert_by_name):
        if getattr(scrap, "quality_alert_id", False):
            return scrap.quality_alert_id
        studio_name = getattr(scrap, "x_studio_alerta_de_calidad_id", False)
        if studio_name:
            return alert_by_name.get(self._normalize_key(self._extract_alert_name(studio_name)))
        return False

    def _passes_filters(self, product, operation_name):
        self.ensure_one()
        if self.product_ids and product not in self.product_ids:
            return False
        if self.categ_ids and product and product.categ_id not in self.categ_ids:
            return False
        if self.operation_filter:
            return self.operation_filter.lower() in (operation_name or "").lower()
        return True

    def _pick_best_picking(self, pickings, product=False, event_dt=False, origin_ref=False, pedido_original=False):
        pickings = pickings.filtered(lambda p: p and p.exists())
        if not pickings:
            return False

        def _score(picking):
            score = 0
            move_products = picking.move_ids.mapped("product_id")
            if product and product in move_products:
                score += 100
            if origin_ref:
                origin = (picking.origin or "").strip().upper()
                if origin and origin == (origin_ref or "").strip().upper():
                    score += 40
            if pedido_original:
                origin = (picking.origin or "").strip().upper()
                if pedido_original.strip().upper() in origin:
                    score += 20
            if getattr(picking, "location_dest_id", False):
                score += 5
            scheduled = getattr(picking, "scheduled_date", False) or getattr(picking, "date_deadline", False) or picking.create_date
            if event_dt and scheduled:
                delta = abs((fields.Datetime.to_datetime(event_dt) - fields.Datetime.to_datetime(scheduled)).total_seconds())
                score -= min(delta / 86400.0, 30)
            return score

        return max(pickings, key=_score)

    def _infer_related_picking(self, alert=False, scrap=False, production=False, workorder=False, product=False, event_dt=False, origin_ref=False, pedido_original=False):
        direct_picking = (
            (scrap.picking_id if scrap and "picking_id" in scrap._fields and scrap.picking_id else False)
            or (alert.picking_id if alert and "picking_id" in alert._fields and alert.picking_id else False)
        )
        if direct_picking:
            return direct_picking

        production = production or (
            (scrap.production_id if scrap and "production_id" in scrap._fields and scrap.production_id else False)
            or (alert.production_id if alert and "production_id" in alert._fields and alert.production_id else False)
            or (workorder.production_id if workorder and getattr(workorder, "production_id", False) else False)
        )
        workorder = workorder or (
            (scrap.workorder_id if scrap and "workorder_id" in scrap._fields and scrap.workorder_id else False)
            or (alert.workorder_id if alert and "workorder_id" in alert._fields and alert.workorder_id else False)
        )

        if production and getattr(production, "master_order_id", False):
            delivery_pickings = production.master_order_id.delivery_picking_ids
            best = self._pick_best_picking(
                delivery_pickings,
                product=product,
                event_dt=event_dt,
                origin_ref=origin_ref or getattr(production, "origin", False),
                pedido_original=pedido_original,
            )
            if best:
                return best

        if production and getattr(production, "move_finished_ids", False):
            candidate_pickings = production.move_finished_ids.mapped("move_dest_ids").mapped("picking_id")
            best = self._pick_best_picking(
                candidate_pickings,
                product=product,
                event_dt=event_dt,
                origin_ref=origin_ref or getattr(production, "origin", False),
                pedido_original=pedido_original,
            )
            if best:
                return best

        if workorder and getattr(workorder, "production_id", False) and getattr(workorder.production_id, "move_finished_ids", False):
            candidate_pickings = workorder.production_id.move_finished_ids.mapped("move_dest_ids").mapped("picking_id")
            best = self._pick_best_picking(
                candidate_pickings,
                product=product,
                event_dt=event_dt,
                origin_ref=origin_ref or getattr(workorder.production_id, "origin", False),
                pedido_original=pedido_original,
            )
            if best:
                return best

        Picking = self.env["stock.picking"].sudo()
        search_domain = [("state", "!=", "cancel")]
        if origin_ref:
            search_domain = ["|", ("origin", "=", origin_ref), ("name", "=", origin_ref)] + search_domain
        candidate_pickings = Picking.search(search_domain, order="create_date desc", limit=50)
        if product:
            candidate_pickings = candidate_pickings.filtered(lambda p: product in p.move_ids.mapped("product_id"))
        best = self._pick_best_picking(
            candidate_pickings,
            product=product,
            event_dt=event_dt,
            origin_ref=origin_ref,
            pedido_original=pedido_original,
        )
        return best or False

    def _prepare_row_vals(self, alert=False, scrap=False):
        self.ensure_one()
        production = (
            (scrap.production_id if scrap and "production_id" in scrap._fields else False)
            or (alert.production_id if alert and "production_id" in alert._fields else False)
        )
        workorder = (
            (scrap.workorder_id if scrap and "workorder_id" in scrap._fields else False)
            or (alert.workorder_id if alert and "workorder_id" in alert._fields else False)
        )
        product = (
            (scrap.product_id if scrap and scrap.product_id else False)
            or (alert.product_id if alert and alert.product_id else False)
            or (production.product_id if production and production.product_id else False)
        )
        operation_name = (
            (getattr(scrap, "x_studio_operacion", False) if scrap else False)
            or (getattr(alert, "x_studio_operacion", False) if alert else False)
            or (workorder.operation_id.display_name if workorder and getattr(workorder, "operation_id", False) else False)
            or (workorder.name if workorder else False)
            or ""
        )
        pedido_original = (
            (getattr(scrap, "x_studio_pedido_original", False) if scrap else False)
            or (getattr(alert, "x_studio_pedido_original", False) if alert else False)
            or (getattr(production, "x_studio_pedido_original", False) if production else False)
            or False
        )
        origin_ref = (
            (getattr(scrap, "x_studio_origen", False) if scrap else False)
            or (getattr(alert, "x_studio_origen", False) if alert else False)
            or (scrap.origin if scrap and "origin" in scrap._fields else False)
            or (production.origin if production and "origin" in production._fields else False)
            or False
        )
        event_dt = (
            (scrap.create_date if scrap and "create_date" in scrap._fields else False)
            or (alert.create_date if alert and "create_date" in alert._fields else False)
        )
        picking = self._infer_related_picking(
            alert=alert,
            scrap=scrap,
            production=production,
            workorder=workorder,
            product=product,
            event_dt=event_dt,
            origin_ref=origin_ref,
            pedido_original=pedido_original,
        )
        dest_location = False
        if picking and getattr(picking, "location_dest_id", False):
            dest_location = picking.location_dest_id.complete_name or picking.location_dest_id.display_name or False

        destination_type = False
        if dest_location:
            dest_upper = dest_location.upper()
            for key in ("CAU", "CAE", "CAF", "PT-AAA", "AYM", "CES"):
                if "/%s" % key in dest_upper or dest_upper.endswith(key):
                    destination_type = key
                    break
        if not self._passes_filters(product, operation_name):
            return False

        entry_type = "linked" if alert and scrap else ("alert_only" if alert else "scrap_only")
        return {
            "wizard_id": self.id,
            "event_date": event_dt,
            "entry_type": entry_type,
            "quality_alert_id": alert.id if alert else False,
            "scrap_id": scrap.id if scrap else False,
            "quality_name": alert.display_name if alert else False,
            "scrap_name": scrap.display_name if scrap else False,
            "product_id": product.id if product else False,
            "production_id": production.id if production else False,
            "workorder_id": workorder.id if workorder else False,
            "master_order_id": production.master_order_id.id if production and getattr(production, "master_order_id", False) else False,
            "pedido_original": pedido_original,
            "origin_ref": origin_ref,
            "operation_name": operation_name,
            "quality_stage_name": alert.stage_id.display_name if alert and getattr(alert, "stage_id", False) else False,
            "quality_reason_name": alert.reason_id.display_name if alert and getattr(alert, "reason_id", False) else False,
            "quality_team_name": alert.team_id.display_name if alert and getattr(alert, "team_id", False) else False,
            "quality_user_name": alert.user_id.display_name if alert and getattr(alert, "user_id", False) else False,
            "quality_tag_names": ", ".join(alert.tag_ids.mapped("display_name")) if alert and getattr(alert, "tag_ids", False) else False,
            "scrap_state": scrap.state if scrap and "state" in scrap._fields else False,
            "scrap_qty": (
                (scrap.scrap_qty if scrap and "scrap_qty" in scrap._fields else False)
                or (scrap.quantity if scrap and "quantity" in scrap._fields else 0.0)
                or 0.0
            ),
            "transfer_name": picking.name if picking else False,
            "destination_location_name": dest_location,
            "destination_type": destination_type,
            "location_name": scrap.location_id.complete_name if scrap and getattr(scrap, "location_id", False) else False,
            "scrap_location_name": scrap.scrap_location_id.complete_name if scrap and getattr(scrap, "scrap_location_id", False) else False,
            "alert_title": alert.title if alert and "title" in alert._fields else False,
            "alert_name": alert.name if alert else False,
            "scrap_origin_name": scrap.origin if scrap and "origin" in scrap._fields else False,
            "quality_studio_operacion": getattr(alert, "x_studio_operacion", False) if alert else False,
            "scrap_studio_operacion": getattr(scrap, "x_studio_operacion", False) if scrap else False,
            "alert_count": 1 if alert else 0,
            "scrap_count": 1 if scrap else 0,
        }

    def action_generate(self):
        self.ensure_one()
        if self.date_from > self.date_to:
            raise UserError(_("La fecha desde no puede ser mayor que la fecha hasta."))

        self.line_ids.unlink()

        date_from_utc, date_to_utc = _local_day_bounds_to_utc(self.env, self.date_from, self.date_to)
        date_from = fields.Datetime.to_string(date_from_utc) if date_from_utc else False
        date_to = fields.Datetime.to_string(date_to_utc) if date_to_utc else False

        Alert = self.env["quality.alert"].sudo()
        Scrap = self.env["stock.scrap"].sudo()

        alerts = Alert.search([("create_date", ">=", date_from), ("create_date", "<=", date_to)], order="create_date desc")
        scraps = Scrap.search([("create_date", ">=", date_from), ("create_date", "<=", date_to)], order="create_date desc")

        scrap_by_name = {}
        for scrap in scraps:
            for key in filter(None, [scrap.name, scrap.display_name]):
                scrap_by_name[self._normalize_key(key)] = scrap

        alert_by_name = {}
        for alert in alerts:
            for key in filter(None, [alert.name, alert.display_name, self._extract_alert_name(alert.display_name)]):
                alert_by_name[self._normalize_key(key)] = alert

        matched_alert_ids = set()
        matched_scrap_ids = set()
        rows = []

        for alert in alerts:
            scrap = self._resolve_linked_scrap(alert, scrap_by_name)
            if scrap:
                matched_alert_ids.add(alert.id)
                matched_scrap_ids.add(scrap.id)
                vals = self._prepare_row_vals(alert=alert, scrap=scrap)
                if vals:
                    rows.append(vals)

        for scrap in scraps:
            if scrap.id in matched_scrap_ids:
                continue
            alert = self._resolve_linked_alert(scrap, alert_by_name)
            if alert:
                matched_alert_ids.add(alert.id)
                matched_scrap_ids.add(scrap.id)
                vals = self._prepare_row_vals(alert=alert, scrap=scrap)
                if vals:
                    rows.append(vals)

        for alert in alerts.filtered(lambda a: a.id not in matched_alert_ids):
            vals = self._prepare_row_vals(alert=alert, scrap=False)
            if vals:
                rows.append(vals)

        for scrap in scraps.filtered(lambda s: s.id not in matched_scrap_ids):
            vals = self._prepare_row_vals(alert=False, scrap=scrap)
            if vals:
                rows.append(vals)

        if not rows:
            raise UserError(_("No se encontraron datos para el rango seleccionado."))

        self.env["mrp.report.quality.scrap.entry"].create(rows)
        return {
            "type": "ir.actions.act_window",
            "name": _("Desechos y Calidad"),
            "res_model": "mrp.report.quality.scrap",
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_open_lines(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Desechos y Calidad"),
            "res_model": "mrp.report.quality.scrap.entry",
            "view_mode": "tree",
            "domain": [("wizard_id", "=", self.id)],
            "search_view_id": self.env.ref("alterben_mrp_master_order.view_report_quality_scrap_line_search").id,
        }

    def action_open_graph(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Grafica Desechos vs Calidad"),
            "res_model": "mrp.report.quality.scrap.entry",
            "view_mode": "graph,tree",
            "domain": [("wizard_id", "=", self.id)],
            "views": [
                (self.env.ref("alterben_mrp_master_order.view_report_quality_scrap_line_graph").id, "graph"),
                (self.env.ref("alterben_mrp_master_order.view_report_quality_scrap_line_tree").id, "tree"),
            ],
            "search_view_id": self.env.ref("alterben_mrp_master_order.view_report_quality_scrap_line_search").id,
        }


class MRPReportQualityScrapEntry(models.TransientModel):
    _name = "mrp.report.quality.scrap.entry"
    _description = "Linea reporte Desechos y Calidad"
    _order = "event_date desc, id desc"

    wizard_id = fields.Many2one("mrp.report.quality.scrap", required=True, ondelete="cascade")
    row_number = fields.Integer(string="#", compute="_compute_row_number", store=False)
    event_date = fields.Datetime(string="Fecha")
    entry_type = fields.Selection(
        [("linked", "Alerta + Desecho"), ("alert_only", "Solo alerta"), ("scrap_only", "Solo desecho")],
        string="Tipo",
    )
    quality_alert_id = fields.Many2one("quality.alert", string="Alerta de calidad")
    scrap_id = fields.Many2one("stock.scrap", string="Desecho")
    quality_name = fields.Char(string="Alerta")
    scrap_name = fields.Char(string="Desecho")
    product_id = fields.Many2one("product.product", string="Producto")
    categ_id = fields.Many2one("product.category", string="Categoria", related="product_id.categ_id", store=False)
    production_id = fields.Many2one("mrp.production", string="MO")
    workorder_id = fields.Many2one("mrp.workorder", string="WO")
    master_order_id = fields.Many2one("mrp.master.order", string="Orden Maestra")
    pedido_original = fields.Char(string="Pedido original")
    origin_ref = fields.Char(string="Origen")
    operation_name = fields.Char(string="Operacion")
    quality_stage_name = fields.Char(string="Etapa alerta")
    quality_reason_name = fields.Char(string="Motivo alerta")
    quality_team_name = fields.Char(string="Equipo calidad")
    quality_user_name = fields.Char(string="Responsable")
    quality_tag_names = fields.Char(string="Etiquetas")
    scrap_state = fields.Char(string="Estado desecho")
    scrap_qty = fields.Float(string="Cantidad desecho")
    transfer_name = fields.Char(string="Transferencia")
    destination_location_name = fields.Char(string="Ubicacion destino")
    destination_type = fields.Selection(
        [("CAU", "CAU"), ("CAE", "CAE"), ("CAF", "CAF"), ("PT-AAA", "PT-AAA"), ("AYM", "AYM"), ("CES", "CES")],
        string="Tipo destino",
    )
    uom_id = fields.Many2one("uom.uom", string="UdM", related="product_id.uom_id", store=False)
    location_name = fields.Char(string="Ubicacion origen")
    scrap_location_name = fields.Char(string="Ubicacion desecho")
    alert_title = fields.Char(string="Titulo alerta")
    alert_name = fields.Char(string="Codigo alerta")
    scrap_origin_name = fields.Char(string="Origen desecho")
    quality_studio_operacion = fields.Char(string="Operacion alerta")
    scrap_studio_operacion = fields.Char(string="Operacion desecho")
    alert_count = fields.Integer(string="Alertas", default=0)
    scrap_count = fields.Integer(string="Desechos", default=0)

    def _compute_row_number(self):
        for line in self:
            line.row_number = 0
        for wizard in self.mapped("wizard_id"):
            ordered = self.search([("wizard_id", "=", wizard.id)], order=self._order)
            for index, line in enumerate(ordered, start=1):
                line.row_number = index
