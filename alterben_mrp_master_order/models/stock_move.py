from datetime import datetime, time
import pytz

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class StockMove(models.Model):
    _inherit = "stock.move"

    ct_fully_labeled = fields.Boolean(string="Etiquetas asignadas", compute="_compute_ct_fully_labeled")
    ct_label_range = fields.Char(string="Rango de etiquetas", compute="_compute_ct_label_range")
    ct_pre_from = fields.Char(string="Pre-asignado desde", copy=False)
    ct_pre_to = fields.Char(string="Pre-asignado hasta", copy=False)
    ab_delivery_cancellation_ids = fields.One2many(
        "sale.delivery.cancellation.log",
        "move_id",
        string="Cancelaciones de despacho",
    )
    ab_pending_delivery_audit_ids = fields.One2many(
        "stock.move.pending.delivery.audit",
        "move_id",
        string="Auditoria pendientes entrega",
    )
    ab_pending_delivery_audit_count = fields.Integer(
        string="Auditorias",
        compute="_compute_ab_pending_delivery_audit_count",
    )

    ab_sale_order_id = fields.Many2one(
        "sale.order",
        string="Orden de venta",
        related="sale_line_id.order_id",
        readonly=True,
        store=True,
    )
    ab_customer_id = fields.Many2one(
        "res.partner",
        string="Cliente",
        related="picking_id.partner_id",
        readonly=True,
        store=True,
    )
    ab_customer_city = fields.Char(
        string="Ciudad",
        related="picking_id.partner_id.city",
        readonly=True,
        store=True,
    )
    ab_batch_id = fields.Many2one(
        "stock.picking.batch",
        string="Batch",
        related="picking_id.batch_id",
        readonly=True,
        store=True,
    )
    ab_picking_scheduled_date = fields.Datetime(
        string="Fecha programada picking",
        related="picking_id.scheduled_date",
        readonly=True,
        store=True,
    )
    ab_sale_commitment_date = fields.Datetime(
        string="Fecha compromiso OV",
        related="sale_line_id.order_id.commitment_date",
        readonly=True,
        store=True,
    )
    ab_delivery_date_manual = fields.Datetime(
        string="Fecha entrega manual",
        copy=False,
        help="Fecha de entrega ajustada manualmente por producto desde el reporte de pendientes.",
    )
    ab_delivery_date = fields.Datetime(
        string="Fecha entrega producto",
        compute="_compute_ab_delivery_date",
        inverse="_inverse_ab_delivery_date",
        store=True,
    )
    ab_qty_pending = fields.Float(
        string="Cant. pendiente",
        compute="_compute_ab_delivery_tracking",
        search="_search_ab_qty_pending",
    )
    ab_qty_reserved = fields.Float(
        string="Cant. reservada",
        compute="_compute_ab_delivery_tracking",
        search="_search_ab_qty_reserved",
    )
    ab_qty_done_display = fields.Float(
        string="Cant. hecha",
        compute="_compute_ab_delivery_tracking",
    )
    ab_cancelled_delivery_qty = fields.Float(
        string="Cant. cancelada",
        compute="_compute_ab_cancelled_delivery_qty",
    )
    ab_stock_on_hand = fields.Float(
        string="A la mano",
        compute="_compute_ab_stock_metrics",
    )
    ab_stock_reserved_total = fields.Float(
        string="Reservado prod.",
        compute="_compute_ab_stock_metrics",
    )
    ab_stock_caf = fields.Float(
        string="En CAF",
        compute="_compute_ab_stock_metrics",
    )
    ab_stock_available_net = fields.Float(
        string="Disponible",
        compute="_compute_ab_stock_metrics",
        search="_search_ab_stock_available_net",
    )
    ab_dispatch_ready = fields.Boolean(
        string="Disponible para despacho",
        compute="_compute_ab_stock_metrics",
        search="_search_ab_dispatch_ready",
    )
    ab_dispatch_status = fields.Selection(
        [
            ("ready", "Disponible"),
            ("blocked", "No disponible"),
        ],
        string="Estado despacho",
        compute="_compute_ab_stock_metrics",
        store=True,
    )
    ab_reservation_status = fields.Selection(
        [
            ("reserved", "Reservado"),
            ("partial", "Parcial"),
            ("waiting", "Sin reservar"),
            ("no_pending", "Sin pendiente"),
        ],
        string="Estado reserva",
        compute="_compute_ab_delivery_tracking",
        store=True,
    )
    ab_delivery_bucket = fields.Selection(
        [
            ("late", "Vencido"),
            ("today", "Hoy"),
            ("future", "Futuro"),
            ("undated", "Sin fecha"),
        ],
        string="Estado fecha",
        compute="_compute_ab_delivery_bucket",
        search="_search_ab_delivery_bucket",
    )
    ab_source_bucket = fields.Selection(
        [
            ("CAU", "CAU"),
            ("CAF", "CAF"),
            ("IMPORTADOS", "IMPORTADOS"),
            ("CES", "CES"),
            ("CAE", "CAE"),
        ],
        string="Ubicacion",
        compute="_compute_ab_source_bucket",
    )

    def _compute_ab_pending_delivery_audit_count(self):
        for move in self:
            move.ab_pending_delivery_audit_count = len(move.ab_pending_delivery_audit_ids)

    @api.depends("location_id", "location_id.complete_name", "location_id.name")
    def _compute_ab_source_bucket(self):
        for move in self:
            location_name = ((move.location_id.complete_name or move.location_id.name or "") if move.location_id else "").upper()
            bucket = False
            for key in ("CAU", "CAF", "IMPORTADOS", "CES", "CAE"):
                if f"/{key}" in location_name or location_name.endswith(key):
                    bucket = key
                    break
            move.ab_source_bucket = bucket

    def _ab_create_pending_delivery_audit_entry(self, action_type, reserved_before, reserved_after, pending_before, pending_after, quantity_changed=0.0, reason=False, note=False):
        self.ensure_one()
        self.env["stock.move.pending.delivery.audit"].create({
            "move_id": self.id,
            "sale_line_id": self.sale_line_id.id if self.sale_line_id else False,
            "action_type": action_type,
            "reserved_before": float(reserved_before or 0.0),
            "reserved_after": float(reserved_after or 0.0),
            "pending_before": float(pending_before or 0.0),
            "pending_after": float(pending_after or 0.0),
            "quantity_changed": float(quantity_changed or 0.0),
            "reason": reason or False,
            "note": note or False,
        })

    @api.model
    def _ab_get_blocked_source_location_ids_from_config(self):
        raw_value = self.env["ir.config_parameter"].sudo().get_param(
            "alterben_mrp_master_order.blocked_source_location_ids",
            default="",
        )
        return {
            int(value)
            for value in (raw_value or "").split(",")
            if value.strip().isdigit()
        }

    @api.model
    def _ab_get_blocked_parent_source_category_ids(self):
        return self.env["mrp.master.type"].sudo()._get_global_blocked_parent_source_category_ids()

    @api.model
    def _ab_get_putaway_target_location(self, base_location, product):
        if not base_location or not product:
            return False

        putaway_apply = getattr(base_location, "putaway_apply", None)
        if putaway_apply:
            try:
                target = putaway_apply(product)
            except TypeError:
                try:
                    target = putaway_apply(product, 1.0)
                except Exception:
                    target = False
            except Exception:
                target = False
            if target and target != base_location:
                return target

        Rule = self.env["stock.putaway.rule"].sudo()
        if not Rule:
            return False

        loc_field = "location_in_id" if "location_in_id" in Rule._fields else "location_id" if "location_id" in Rule._fields else False
        loc_out_field = "location_out_id" if "location_out_id" in Rule._fields else False
        if not loc_field or not loc_out_field:
            return False
        order = "sequence, id" if "sequence" in Rule._fields else "id"

        domain = [(loc_field, "=", base_location.id)]
        if "company_id" in Rule._fields and base_location.company_id:
            domain += ["|", ("company_id", "=", False), ("company_id", "=", base_location.company_id.id)]

        candidates = self.env["stock.putaway.rule"].sudo()
        if "product_id" in Rule._fields:
            candidates = Rule.search(domain + [("product_id", "=", product.id)], order=order, limit=1)
        if not candidates and "product_tmpl_id" in Rule._fields and product.product_tmpl_id:
            candidates = Rule.search(domain + [("product_tmpl_id", "=", product.product_tmpl_id.id)], order=order, limit=1)
        if not candidates and "product_template_id" in Rule._fields and product.product_tmpl_id:
            candidates = Rule.search(domain + [("product_template_id", "=", product.product_tmpl_id.id)], order=order, limit=1)
        if not candidates:
            cat_field = "product_category_id" if "product_category_id" in Rule._fields else "category_id" if "category_id" in Rule._fields else False
            if cat_field and product.categ_id:
                candidates = Rule.search(domain + [(cat_field, "child_of", product.categ_id.id)], order=order, limit=1)
        if not candidates:
            candidates = Rule.search(domain, order=order, limit=1)

        return candidates[loc_out_field] if candidates and candidates[loc_out_field] and candidates[loc_out_field] != base_location else False

    def _ab_force_child_source_location_for_restricted_categories(self):
        blocked_location_ids = self._ab_get_blocked_source_location_ids_from_config()
        blocked_categ_ids = self._ab_get_blocked_parent_source_category_ids()
        if not blocked_location_ids or not blocked_categ_ids:
            return

        candidate_moves = self.filtered(
            lambda mv: (
                mv.sale_line_id
                and mv.product_id
                and mv.product_id.categ_id
                and mv.product_id.categ_id.id in blocked_categ_ids
                and mv.location_id
                and mv.location_id.id in blocked_location_ids
                and mv.state not in ("done", "cancel")
                and mv.picking_type_id
                and mv.picking_type_id.code == "outgoing"
            )
        )

        for move in candidate_moves:
            target_location = self._ab_get_putaway_target_location(move.location_id, move.product_id)
            if not target_location or target_location == move.location_id:
                continue
            old_location = move.location_id
            move.write({"location_id": target_location.id})
            editable_lines = move.move_line_ids.filtered(
                lambda ml: ml.state not in ("done", "cancel") and ml.location_id == old_location
            )
            if editable_lines:
                editable_lines.write({"location_id": target_location.id})

    @api.depends("move_line_ids.quantity", "move_line_ids.control_total_label_ids")
    def _compute_ct_fully_labeled(self):
        for move in self:
            qty = int(sum(ml.quantity or 0 for ml in move.move_line_ids))
            labels = sum(len(ml.control_total_label_ids) for ml in move.move_line_ids)
            move.ct_fully_labeled = bool(qty) and labels >= qty

    def action_open_control_total_move_wizard(self):
        self.ensure_one()
        return {
            "name": _("Asignar Control Total"),
            "type": "ir.actions.act_window",
            "res_model": "assign.control.total.wizard",
            "view_mode": "form",
            "view_id": self.env.ref("alterben_mrp_master_order.view_assign_control_total_wizard_form").id,
            "target": "new",
            "context": {
                "default_picking_id": self.picking_id.id,
                "default_move_id": self.id,
            },
        }

    @api.depends("picking_id", "product_id", "ct_pre_from", "ct_pre_to")
    def _compute_ct_label_range(self):
        Label = self.env["control.total.label"]
        import re

        def split_code(code):
            match = re.match(r"^(?P<prefix>\D*?)(?P<num>\d+)$", code or "")
            if match:
                return (match.group("prefix") or ""), int(match.group("num"))
            return (code or ""), None

        for move in self:
            move.ct_label_range = False
            if not move.picking_id or not move.product_id:
                continue
            existing = Label.search([
                ("picking_id", "=", move.picking_id.id),
                ("product_id", "=", move.product_id.id),
                ("active", "=", True),
            ], order="name asc")
            if not existing:
                existing = Label.search([
                    ("picking_id", "=", move.picking_id.id),
                    ("product_id.product_tmpl_id", "=", move.product_id.product_tmpl_id.id),
                    ("active", "=", True),
                ], order="name asc")
            nums = []
            common_prefix = None
            for rec in existing:
                prefix, number = split_code(rec.name)
                if number is None:
                    continue
                if common_prefix is None:
                    common_prefix = prefix
                if prefix == common_prefix:
                    nums.append(number)
            if nums and common_prefix is not None:
                move.ct_label_range = f"{common_prefix}{min(nums)} -> {common_prefix}{max(nums)}"
            elif move.ct_pre_from and move.ct_pre_to:
                move.ct_label_range = f"{move.ct_pre_from} -> {move.ct_pre_to}"

    @api.depends("sale_line_id")
    def _compute_ab_cancelled_delivery_qty(self):
        for move in self:
            if not move.sale_line_id:
                move.ab_cancelled_delivery_qty = 0.0
                continue
            logs = move.sale_line_id.ab_delivery_cancellation_ids.filtered(lambda log: log.move_id == move)
            move.ab_cancelled_delivery_qty = sum(logs.mapped("quantity"))

    def _get_ab_qty_done(self):
        self.ensure_one()
        move_lines = getattr(self, "move_line_ids", False)
        if move_lines:
            qty_done = 0.0
            for line in move_lines.filtered(lambda ml: ml.state not in ("cancel",)):
                line_done = False
                for field_name in ("qty_done", "quantity_done"):
                    if field_name in line._fields:
                        line_done = getattr(line, field_name, 0.0)
                        if line_done:
                            break
                if line_done is False:
                    line_done = 0.0
                qty_done += float(line_done or 0.0)
            if qty_done > 0.0:
                return qty_done
        if "quantity_done" in self._fields:
            return float(self.quantity_done or 0.0)
        if "qty_done" in self._fields:
            return float(self.qty_done or 0.0)
        return 0.0

    def _get_ab_reserved_qty(self):
        self.ensure_one()
        reserved = float(getattr(self, "reserved_availability", 0.0) or 0.0)
        if reserved > 0:
            return reserved

        move_lines = getattr(self, "move_line_ids", False)
        if not move_lines:
            return 0.0

        qty_from_lines = 0.0
        for line in move_lines.filtered(lambda ml: ml.state not in ("done", "cancel")):
            qty_value = False
            for field_name in ("reserved_uom_qty", "quantity_product_uom", "product_uom_qty"):
                if field_name in line._fields:
                    qty_value = getattr(line, field_name, 0.0)
                    if qty_value:
                        break
            if qty_value is False:
                qty_value = 0.0
            qty_from_lines += float(qty_value or 0.0)
        return qty_from_lines

    def _ab_has_reserved_move_lines(self):
        self.ensure_one()
        move_lines = getattr(self, "move_line_ids", False)
        if not move_lines:
            return False

        for line in move_lines.filtered(lambda ml: ml.state not in ("done", "cancel")):
            for field_name in ("reserved_uom_qty", "quantity_product_uom", "product_uom_qty"):
                if field_name in line._fields and float(getattr(line, field_name, 0.0) or 0.0) > 0.0:
                    return True
        return False

    def _ab_prepare_for_manual_reservation(self):
        for move in self.filtered(lambda mv: mv.state in ("waiting", "confirmed")):
            vals = {}
            if getattr(move, "procure_method", False) == "make_to_order":
                vals["procure_method"] = "make_to_stock"
            if move.move_orig_ids:
                vals["move_orig_ids"] = [(5, 0, 0)]
            if vals:
                move.write(vals)
            if hasattr(move, "_recompute_state"):
                move._recompute_state()

    def _ab_get_internal_locations(self):
        self.ensure_one()
        Location = self.env["stock.location"].sudo()
        warehouse = getattr(self.picking_id, "picking_type_id", False) and getattr(self.picking_id.picking_type_id, "warehouse_id", False)
        if warehouse and warehouse.view_location_id:
            return Location.search([
                ("id", "child_of", warehouse.view_location_id.id),
                ("usage", "=", "internal"),
            ])
        order_warehouse = getattr(self.ab_sale_order_id, "warehouse_id", False)
        if order_warehouse and order_warehouse.view_location_id:
            return Location.search([
                ("id", "child_of", order_warehouse.view_location_id.id),
                ("usage", "=", "internal"),
            ])
        return Location.search([("usage", "=", "internal")])

    def _ab_get_caf_locations(self, locations):
        return locations.filtered(
            lambda loc: "CAF" in (loc.complete_name or "").upper() or "CAF" in (loc.name or "").upper()
        )

    def _ab_get_quant_metrics_by_pair(self):
        if not self:
            return {}

        pairs = {
            (move.product_id.id, move.location_id.id)
            for move in self
            if move.product_id and move.location_id
        }
        if not pairs:
            return {}

        cr = self.env.cr
        values_sql = ", ".join(["(%s, %s)"] * len(pairs))
        params = []
        for product_id, location_id in pairs:
            params.extend([product_id, location_id])

        cr.execute(f"""
            WITH pair_map(product_id, root_location_id) AS (
                VALUES {values_sql}
            ),
            scoped_quants AS (
                SELECT
                    pm.product_id,
                    pm.root_location_id,
                    q.quantity,
                    q.reserved_quantity,
                    loc.name,
                    loc.complete_name
                FROM pair_map pm
                JOIN stock_location root_loc
                    ON root_loc.id = pm.root_location_id
                JOIN stock_location loc
                    ON (
                        loc.id = root_loc.id
                        OR loc.parent_path LIKE (root_loc.parent_path || root_loc.id || '/%%')
                    )
                LEFT JOIN stock_quant q
                    ON q.product_id = pm.product_id
                   AND q.location_id = loc.id
                WHERE loc.usage = 'internal'
            )
            SELECT
                product_id,
                root_location_id,
                COALESCE(SUM(quantity), 0.0) AS on_hand_qty,
                COALESCE(SUM(reserved_quantity), 0.0) AS reserved_qty,
                COALESCE(SUM(
                    CASE
                        WHEN UPPER(COALESCE(complete_name, '')) LIKE '%%CAF%%'
                          OR UPPER(COALESCE(name, '')) LIKE '%%CAF%%'
                        THEN quantity
                        ELSE 0.0
                    END
                ), 0.0) AS caf_qty,
                COALESCE(SUM(
                    CASE
                        WHEN UPPER(COALESCE(complete_name, '')) LIKE '%%CAF%%'
                          OR UPPER(COALESCE(name, '')) LIKE '%%CAF%%'
                        THEN 0.0
                        ELSE quantity - reserved_quantity
                    END
                ), 0.0) AS free_non_caf_qty
            FROM scoped_quants
            GROUP BY product_id, root_location_id
        """, params)

        metrics = {}
        for product_id, root_location_id, on_hand_qty, reserved_qty, caf_qty, free_non_caf_qty in cr.fetchall():
            metrics[(product_id, root_location_id)] = {
                "on_hand_qty": float(on_hand_qty or 0.0),
                "reserved_qty": float(reserved_qty or 0.0),
                "caf_qty": float(caf_qty or 0.0),
                "free_non_caf_qty": float(free_non_caf_qty or 0.0),
            }
        return metrics

    def _ab_get_customer_moves(self, product, locations):
        Move = self.env["stock.move"].sudo()
        moves = Move.search([
            ("product_id", "=", product.id),
            ("state", "not in", ["done", "cancel"]),
            ("location_id", "in", locations.ids),
            "|",
            ("picking_id.picking_type_code", "=", "outgoing"),
            ("location_dest_id.usage", "=", "customer"),
        ], order="date asc, id asc", limit=80)
        return moves.filtered(
            lambda mv: (
                (mv.picking_id and getattr(mv.picking_id, "picking_type_code", False) == "outgoing")
                or (mv.location_dest_id and getattr(mv.location_dest_id, "usage", False) == "customer")
            )
        )

    def _get_ab_default_delivery_date(self):
        self.ensure_one()
        return (
            self.ab_sale_commitment_date
            or getattr(self, "date_deadline", False)
            or self.ab_picking_scheduled_date
            or self.date
            or False
        )

    @api.depends(
        "ab_delivery_date_manual",
        "sale_line_id",
        "picking_id",
        "date",
    )
    def _compute_ab_delivery_date(self):
        for move in self:
            move.ab_delivery_date = move.ab_delivery_date_manual or move._get_ab_default_delivery_date()

    def _inverse_ab_delivery_date(self):
        for move in self:
            fallback = move._get_ab_default_delivery_date()
            if move.ab_delivery_date and fallback and move.ab_delivery_date == fallback:
                move.ab_delivery_date_manual = False
            else:
                move.ab_delivery_date_manual = move.ab_delivery_date or False

    @api.depends(
        "product_uom_qty",
        "state",
        "move_line_ids",
        "move_line_ids.state",
    )
    def _compute_ab_delivery_tracking(self):
        for move in self:
            qty_done = move._get_ab_qty_done()
            qty_demand = float(move.product_uom_qty or 0.0)
            qty_pending = max(qty_demand - qty_done, 0.0)
            qty_reserved = min(move._get_ab_reserved_qty(), qty_pending)
            move.ab_qty_done_display = qty_done
            move.ab_qty_pending = qty_pending
            move.ab_qty_reserved = qty_reserved
            if qty_pending <= 0:
                move.ab_reservation_status = "no_pending"
            elif qty_reserved and qty_pending and qty_reserved >= qty_pending:
                move.ab_reservation_status = "reserved"
            elif qty_reserved:
                move.ab_reservation_status = "partial"
            else:
                move.ab_reservation_status = "waiting"

    @api.depends("product_id", "picking_id", "ab_qty_pending")
    def _compute_ab_stock_metrics(self):
        metrics_by_pair = self._ab_get_quant_metrics_by_pair()
        for move in self:
            product = move.product_id
            if not product or not move.location_id:
                move.ab_stock_on_hand = 0.0
                move.ab_stock_reserved_total = 0.0
                move.ab_stock_caf = 0.0
                move.ab_stock_available_net = 0.0
                move.ab_dispatch_ready = False
                move.ab_dispatch_status = "blocked"
                continue

            metrics = metrics_by_pair.get((product.id, move.location_id.id), {})
            on_hand_qty = max(metrics.get("on_hand_qty", 0.0), 0.0)
            caf_qty = metrics.get("caf_qty", 0.0)
            reserved_total = metrics.get("reserved_qty", 0.0)
            available_qty = max(metrics.get("free_non_caf_qty", 0.0), 0.0)

            move.ab_stock_on_hand = round(float(on_hand_qty or 0.0), 2)
            move.ab_stock_reserved_total = round(float(reserved_total or 0.0), 2)
            move.ab_stock_caf = round(float(caf_qty or 0.0), 2)
            move.ab_stock_available_net = round(float(available_qty or 0.0), 2)
            move.ab_dispatch_ready = available_qty >= float(move.ab_qty_pending or 0.0) and float(move.ab_qty_pending or 0.0) > 0.0
            move.ab_dispatch_status = "ready" if move.ab_dispatch_ready else "blocked"

    @api.depends("ab_delivery_date")
    def _compute_ab_delivery_bucket(self):
        today = fields.Date.context_today(self)
        for move in self:
            if not move.ab_delivery_date:
                move.ab_delivery_bucket = "undated"
                continue
            local_dt = fields.Datetime.context_timestamp(move, move.ab_delivery_date)
            local_date = local_dt.date() if local_dt else today
            if local_date < today:
                move.ab_delivery_bucket = "late"
            elif local_date == today:
                move.ab_delivery_bucket = "today"
            else:
                move.ab_delivery_bucket = "future"

    def _search_ab_delivery_bucket(self, operator, value):
        if operator != "=" or value not in ("late", "today", "future", "undated"):
            return []

        user_tz = pytz.timezone(self.env.context.get("tz") or self.env.user.tz or "UTC")
        today = fields.Date.context_today(self)
        local_start = user_tz.localize(datetime.combine(today, time.min))
        local_end = user_tz.localize(datetime.combine(today, time.max))
        start_today = fields.Datetime.to_string(local_start.astimezone(pytz.UTC).replace(tzinfo=None))
        end_today = fields.Datetime.to_string(local_end.astimezone(pytz.UTC).replace(tzinfo=None))
        mapping = {
            "undated": [("ab_delivery_date", "=", False)],
            "late": [("ab_delivery_date", "!=", False), ("ab_delivery_date", "<", start_today)],
            "today": [("ab_delivery_date", ">=", start_today), ("ab_delivery_date", "<=", end_today)],
            "future": [("ab_delivery_date", ">", end_today)],
        }
        return mapping[value]

    def _search_ab_qty_reserved(self, operator, value):
        if operator not in ("=", "!=", ">", ">=", "<", "<="):
            return []

        candidate_domain = [("state", "not in", ("done", "cancel"))]
        if operator in (">", ">=") and float(value or 0.0) <= 0.0:
            candidate_domain.append(("move_line_ids", "!=", False))

        candidates = self.search(candidate_domain)
        matched = candidates.filtered(lambda move: self._compare_reserved_qty(move._get_ab_reserved_qty(), operator, float(value or 0.0)))
        return [("id", "in", matched.ids)]

    def _search_ab_qty_pending(self, operator, value):
        if operator not in ("=", "!=", ">", ">=", "<", "<="):
            return []

        candidates = self.search([("state", "not in", ("done", "cancel"))])
        matched = candidates.filtered(
            lambda move: self._compare_reserved_qty(float(move.ab_qty_pending or 0.0), operator, float(value or 0.0))
        )
        return [("id", "in", matched.ids)]

    def _search_ab_stock_available_net(self, operator, value):
        if operator not in ("=", "!=", ">", ">=", "<", "<="):
            return []
        candidates = self.search([("state", "not in", ("done", "cancel"))])
        matched = candidates.filtered(
            lambda move: self._compare_reserved_qty(float(move.ab_stock_available_net or 0.0), operator, float(value or 0.0))
        )
        return [("id", "in", matched.ids)]

    def _search_ab_dispatch_ready(self, operator, value):
        if operator not in ("=", "!="):
            return []
        target = bool(value)

        self.env.cr.execute("""
            WITH candidate_moves AS (
                SELECT
                    sm.id,
                    sm.product_id,
                    sm.location_id,
                    sm.product_uom_qty AS demand_qty
                FROM stock_move sm
                JOIN stock_picking sp
                    ON sp.id = sm.picking_id
                JOIN stock_location dest
                    ON dest.id = sm.location_dest_id
                WHERE sm.state NOT IN ('done', 'cancel', 'draft')
                  AND sm.sale_line_id IS NOT NULL
                  AND dest.usage = 'customer'
                  AND sp.picking_type_id IN (
                      SELECT id
                      FROM stock_picking_type
                      WHERE code = 'outgoing'
                  )
            ),
            move_pairs AS (
                SELECT DISTINCT product_id, location_id
                FROM candidate_moves
                WHERE product_id IS NOT NULL
                  AND location_id IS NOT NULL
            ),
            availability_by_pair AS (
                SELECT
                    mp.product_id,
                    mp.location_id,
                    COALESCE(SUM(
                        CASE
                            WHEN child_loc.usage = 'internal'
                             AND UPPER(COALESCE(child_loc.complete_name, '')) NOT LIKE '%%CAF%%'
                             AND UPPER(COALESCE(child_loc.name, '')) NOT LIKE '%%CAF%%'
                            THEN COALESCE(q.quantity, 0.0) - COALESCE(q.reserved_quantity, 0.0)
                            ELSE 0.0
                        END
                    ), 0.0) AS free_non_caf_qty
                FROM move_pairs mp
                JOIN stock_location root_loc
                    ON root_loc.id = mp.location_id
                JOIN stock_location child_loc
                    ON (
                        child_loc.id = root_loc.id
                        OR child_loc.parent_path LIKE (root_loc.parent_path || root_loc.id || '/%%')
                    )
                LEFT JOIN stock_quant q
                    ON q.product_id = mp.product_id
                   AND q.location_id = child_loc.id
                GROUP BY mp.product_id, mp.location_id
            )
            SELECT cm.id
            FROM candidate_moves cm
            JOIN availability_by_pair ap
                ON ap.product_id = cm.product_id
               AND ap.location_id = cm.location_id
            WHERE (
                ap.free_non_caf_qty >= cm.demand_qty
                AND cm.demand_qty > 0
            ) = %s
        """, [target])
        matched_ids = [row[0] for row in self.env.cr.fetchall()]
        return [("id", "in", matched_ids or [0])]

    @staticmethod
    def _compare_reserved_qty(left, operator, right):
        if operator == "=":
            return left == right
        if operator == "!=":
            return left != right
        if operator == ">":
            return left > right
        if operator == ">=":
            return left >= right
        if operator == "<":
            return left < right
        if operator == "<=":
            return left <= right
        return False

    def _action_assign(self, force_qty=False):
        self._ab_force_child_source_location_for_restricted_categories()
        return super()._action_assign(force_qty=force_qty)

    def action_open_pending_delivery_sale_order(self):
        self.ensure_one()
        if not self.ab_sale_order_id:
            raise UserError(_("Este movimiento no tiene orden de venta asociada."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Orden de venta"),
            "res_model": "sale.order",
            "res_id": self.ab_sale_order_id.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_open_pending_delivery_picking(self):
        self.ensure_one()
        if not self.picking_id:
            raise UserError(_("Este movimiento no tiene transferencia asociada."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Transferencia"),
            "res_model": "stock.picking",
            "res_id": self.picking_id.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_open_pending_delivery_batch(self):
        self.ensure_one()
        if not self.ab_batch_id:
            raise UserError(_("Este movimiento no tiene batch asociado."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Batch"),
            "res_model": "stock.picking.batch",
            "res_id": self.ab_batch_id.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_open_pending_delivery_forecast_detail(self):
        self.ensure_one()
        if self.sale_line_id and hasattr(self.sale_line_id, "action_open_ab_forecast_detail"):
            return self.sale_line_id.action_open_ab_forecast_detail()
        raise UserError(_("Este movimiento no tiene una linea de venta compatible para mostrar el detalle de disponibilidad."))

    def action_open_cancel_delivery_wizard(self):
        self.ensure_one()
        if not self.sale_line_id:
            raise UserError(_("Solo se puede cancelar despacho en lineas vinculadas a ventas."))
        if self.state in ("done", "cancel"):
            raise UserError(_("No puede cancelar despacho sobre un movimiento hecho o cancelado."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Cancelar despacho"),
            "res_model": "stock.move.cancel.delivery.wizard",
            "view_mode": "form",
            "view_id": self.env.ref("alterben_mrp_master_order.view_stock_move_cancel_delivery_wizard_form").id,
            "target": "new",
            "context": {
                "default_move_id": self.id,
                "default_quantity": self.ab_qty_pending,
            },
        }

    def action_view_delivery_cancellations(self):
        self.ensure_one()
        action = {
            "type": "ir.actions.act_window",
            "name": _("Historial de cancelaciones"),
            "res_model": "sale.delivery.cancellation.log",
            "view_mode": "tree,form",
            "domain": [("move_id", "=", self.id)],
            "target": "current",
            "context": {
                "default_move_id": self.id,
                "default_sale_line_id": self.sale_line_id.id,
            },
        }
        return action

    def action_view_pending_delivery_audit(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Auditoria de acciones"),
            "res_model": "stock.move.pending.delivery.audit",
            "view_mode": "tree,form",
            "domain": [("move_id", "=", self.id)],
            "target": "current",
            "context": {
                "search_default_group_by_action_type": 0,
            },
        }

    def action_reserve_pending_delivery(self):
        moves = self.filtered(
            lambda mv: mv.state not in ("done", "cancel", "draft")
            and mv.picking_id
            and mv.picking_id.picking_type_id.code == "outgoing"
        )
        if not moves:
            raise UserError(_("Seleccione al menos una linea pendiente valida para reservar."))
        pending_moves = moves.filtered(lambda mv: float(mv.ab_qty_pending or 0.0) > 0.0)
        if not pending_moves:
            raise UserError(_("Las lineas seleccionadas ya no tienen cantidad pendiente por reservar."))

        pending_moves._ab_prepare_for_manual_reservation()
        pending_moves.invalidate_recordset()
        pending_moves = self.browse(pending_moves.ids)

        reserved_before = {move.id: move._get_ab_reserved_qty() for move in pending_moves}
        pending_before = {move.id: float(move.ab_qty_pending or 0.0) for move in pending_moves}
        pending_moves._action_assign()

        pending_moves.invalidate_recordset()
        refreshed_moves = self.browse(pending_moves.ids)

        reserved_after = {move.id: move._get_ab_reserved_qty() for move in refreshed_moves}
        pending_after = {move.id: float(move.ab_qty_pending or 0.0) for move in refreshed_moves}
        success = any(
            (reserved_after[mid] or 0.0) > (reserved_before[mid] or 0.0)
            or move._ab_has_reserved_move_lines()
            for mid, move in ((move.id, move) for move in refreshed_moves)
        )
        if not success:
            raise UserError(_(
                "No fue posible reservar stock para las lineas seleccionadas. "
                "Revise disponibilidad real, reglas de ubicacion o si el producto ya esta comprometido."
            ))
        for move in refreshed_moves:
            before_reserved = reserved_before.get(move.id, 0.0)
            after_reserved = reserved_after.get(move.id, 0.0)
            before_pending = pending_before.get(move.id, 0.0)
            after_pending = pending_after.get(move.id, 0.0)
            if (
                round(after_reserved - before_reserved, 6) > 0.0
                or round(before_pending - after_pending, 6) > 0.0
                or move._ab_has_reserved_move_lines()
            ):
                move._ab_create_pending_delivery_audit_entry(
                    "reserve",
                    before_reserved,
                    after_reserved,
                    before_pending,
                    after_pending,
                    quantity_changed=max(after_reserved - before_reserved, 0.0),
                )
        try:
            self.env.user.notify_success(message=_("La linea se reservo correctamente."))
        except Exception:
            pass
        return {"type": "ir.actions.client", "tag": "reload"}

    def action_unreserve_pending_delivery(self):
        moves = self.filtered(
            lambda mv: mv.state not in ("done", "cancel", "draft")
            and mv.picking_id
            and mv.picking_id.picking_type_id.code == "outgoing"
        )
        if not moves:
            raise UserError(_("Seleccione al menos una linea pendiente valida para liberar reserva."))
        reserved_before = {move.id: move._get_ab_reserved_qty() for move in moves}
        pending_before = {move.id: float(move.ab_qty_pending or 0.0) for move in moves}
        for move in moves:
            if hasattr(move, "_do_unreserve"):
                move._do_unreserve()
            elif move.picking_id and hasattr(move.picking_id, "do_unreserve"):
                move.picking_id.do_unreserve()
        moves.invalidate_recordset()
        refreshed_moves = self.browse(moves.ids)
        for move in refreshed_moves:
            after_reserved = move._get_ab_reserved_qty()
            after_pending = float(move.ab_qty_pending or 0.0)
            before_reserved = reserved_before.get(move.id, 0.0)
            before_pending = pending_before.get(move.id, 0.0)
            if round(before_reserved - after_reserved, 6) > 0.0 or round(after_pending - before_pending, 6) > 0.0:
                move._ab_create_pending_delivery_audit_entry(
                    "unreserve",
                    before_reserved,
                    after_reserved,
                    before_pending,
                    after_pending,
                    quantity_changed=max(before_reserved - after_reserved, 0.0),
                )
        try:
            self.env.user.notify_success(message=_("La reserva se libero correctamente."))
        except Exception:
            pass
        return {"type": "ir.actions.client", "tag": "reload"}

    def action_create_pending_delivery_batch(self):
        pickings = self.mapped("picking_id").filtered(
            lambda p: p.state not in ("done", "cancel")
            and p.picking_type_id.code == "outgoing"
        )
        if not pickings:
            raise UserError(_("Seleccione lineas con transferencias de salida pendientes para crear el batch."))
        if len(pickings.mapped("company_id")) > 1:
            raise UserError(_("No puede crear un batch con transferencias de distintas companias."))
        if len(pickings.mapped("picking_type_id")) > 1:
            raise UserError(_("No puede crear un batch con transferencias de distintos tipos de operacion."))

        already_batched = pickings.filtered(
            lambda p: "batch_id" in p._fields and p.batch_id
        )
        if already_batched:
            batch_ids = already_batched.mapped("batch_id")
            if len(batch_ids) == 1 and len(already_batched) == len(pickings):
                return {
                    "type": "ir.actions.act_window",
                    "name": _("Batch de transferencias"),
                    "res_model": "stock.picking.batch",
                    "res_id": batch_ids.id,
                    "view_mode": "form",
                    "target": "current",
                }
            raise UserError(_("Algunas transferencias ya pertenecen a un batch. Quite esas lineas o cree el batch con transferencias no agrupadas."))

        batch_model = self.env["stock.picking.batch"].sudo()
        first_picking = pickings[0]
        scheduled_dates = [dt for dt in pickings.mapped("scheduled_date") if dt]
        batch = batch_model.create({
            "company_id": first_picking.company_id.id,
            "user_id": self.env.user.id,
            "picking_type_id": first_picking.picking_type_id.id,
            "scheduled_date": min(scheduled_dates) if scheduled_dates else fields.Datetime.now(),
            "picking_ids": [(6, 0, pickings.ids)],
        })
        return {
            "type": "ir.actions.act_window",
            "name": _("Batch de transferencias"),
            "res_model": "stock.picking.batch",
            "res_id": batch.id,
            "view_mode": "form",
            "target": "current",
        }
