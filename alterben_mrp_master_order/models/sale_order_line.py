import uuid

from markupsafe import escape

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    # Compatibility shim for databases where the core sale column is missing
    # but the running server code still expects it during reads.
    has_displayed_warning_upsell = fields.Boolean(
        string="Upsell warning displayed",
        default=False,
        copy=False,
    )
    ab_hover_line_id = fields.Char(string="AB Hover Line ID", compute="_compute_ab_hover_line_id")
    x_bucket = fields.Selection(
        [("CAF", "CAF"), ("CAU", "CAU"), ("CES", "CES"), ("OTROS", "OTROS"), ("AAA", "AAA")],
        string="Origen (legacy)",
        copy=False,
    )
    x_bucket_code = fields.Selection(
        [("CAF", "CAF"), ("CAU", "CAU"), ("CES", "CES"), ("OTROS", "OTROS"), ("AAA", "AAA")],
        string="Bodega",
        copy=False,
    )
    x_bucket_token = fields.Char(string="Token opciones", default=lambda self: str(uuid.uuid4()), copy=False)
    x_bucket_option_id = fields.Many2one(
        "alterben.bucket.option",
        string="Bucket option",
        domain="[('token', '=', x_bucket_token)]",
    )
    x_studio_base_discount = fields.Float(
        string="Base discount",
        help="Campo de compatibilidad para automatizaciones antiguas de descuento.",
    )
    x_stock_info = fields.Char(string="Stock Info", compute="_compute_stock_info")

    @api.model
    def _ab_get_bucket_code_from_option_id(self, option_id):
        if not option_id:
            return False
        option = self.env["alterben.bucket.option"].browse(option_id).exists()
        return option.code if option else False

    @api.model
    def _ab_sync_bucket_vals(self, vals):
        vals = dict(vals)
        if "x_bucket_option_id" not in vals:
            return vals
        option_id = vals.get("x_bucket_option_id")
        if not option_id:
            vals["x_bucket_code"] = False
            vals["x_bucket"] = False
            if "route_id" not in vals:
                vals["route_id"] = False
            return vals

        code = self._ab_get_bucket_code_from_option_id(option_id)
        vals["x_bucket_code"] = code or False
        vals["x_bucket"] = code or False
        return vals

    def _sync_qty_from_requested_qty(self, vals):
        if "x_studio_pedido" not in self._fields:
            return vals

        requested_qty = vals.get("x_studio_pedido")
        qty_in_vals = "product_uom_qty" in vals
        if requested_qty is False or requested_qty is None or qty_in_vals:
            return vals

        vals["product_uom_qty"] = requested_qty
        return vals

    @api.onchange("x_studio_pedido")
    def _onchange_x_studio_pedido_set_qty(self):
        if "x_studio_pedido" not in self._fields:
            return

        for line in self:
            requested_qty = float(getattr(line, "x_studio_pedido", 0.0) or 0.0)
            line.product_uom_qty = requested_qty

    @api.onchange("product_id")
    def _onchange_product_id_default_requested_qty(self):
        if "x_studio_pedido" not in self._fields:
            return

        for line in self:
            if not line.product_id:
                continue
            if not getattr(line, "x_studio_pedido", 0.0):
                line.x_studio_pedido = 1.0
            if not line.product_uom_qty:
                line.product_uom_qty = float(getattr(line, "x_studio_pedido", 1.0) or 1.0)

    def _compute_ab_hover_line_id(self):
        for line in self:
            line.ab_hover_line_id = str(line.id or "")

    def _get_root_locations(self):
        Location = self.env["stock.location"].sudo()
        caf = Location.search([("complete_name", "=", "WH/Existencias/CAF")], limit=1)
        cau = Location.search([("complete_name", "=", "WH/Existencias/CAU")], limit=1)
        aaa = Location.search([("complete_name", "=", "WH/PREPRODUCCION/PT-AAA")], limit=1)
        return caf, cau, aaa

    def _is_caf_location(self, location):
        name = (getattr(location, "complete_name", False) or "").upper()
        return "CAF" in name or "FALLAS" in name

    def _is_aaa_location(self, location):
        name = (getattr(location, "complete_name", False) or "").upper()
        return "AAA" in name

    def _get_available(self, root):
        self.ensure_one()
        Quant = self.env["stock.quant"].sudo()
        if not root or not self.product_id:
            return 0
        quants = Quant.search([
            ("location_id", "child_of", root.id),
            ("product_id", "=", self.product_id.id),
        ])
        qty = 0.0
        for quant in quants:
            qty += (quant.quantity - quant.reserved_quantity)
        return round(qty, 2)

    @api.depends("product_id")
    def _compute_stock_info(self):
        for line in self:
            if not line.product_id:
                line.x_stock_info = ""
                continue
            caf, cau, aaa = line._get_root_locations()
            caf_qty = line._get_available(caf)
            cau_qty = line._get_available(cau)
            aaa_qty = line._get_available(aaa)
            line.x_stock_info = f"CAU:{cau_qty} | CAF:{caf_qty} | AAA:{aaa_qty}"

    @api.onchange("product_id", "order_id.warehouse_id")
    def _onchange_rebuild_bucket_options(self):
        Option = self.env["alterben.bucket.option"]
        Quant = self.env["stock.quant"]
        Location = self.env["stock.location"]

        for line in self:
            if line.x_bucket_token:
                Option.search([("token", "=", line.x_bucket_token)]).unlink()
            else:
                line.x_bucket_token = str(uuid.uuid4())

            if not line.product_id:
                line.x_bucket_option_id = False
                line.x_bucket_code = False
                continue

            groups = Quant.read_group([
                ("product_id", "=", line.product_id.id),
                ("location_id.usage", "=", "internal"),
                ("available_quantity", ">", 0),
            ], ["location_id", "available_quantity:sum"], ["location_id"])

            caf_qty = 0.0
            aaa_qty = 0.0
            total_internal = 0.0
            for group in groups:
                if not group.get("location_id"):
                    continue
                qty = group.get("available_quantity", 0.0) or 0.0
                total_internal += qty
                loc = Location.browse(group["location_id"][0])
                if line._is_caf_location(loc):
                    caf_qty += qty
                elif line._is_aaa_location(loc):
                    aaa_qty += qty

            selectable_qty = max(total_internal - caf_qty - aaa_qty, 0.0)
            categ = (line.product_id.categ_id.display_name or line.product_id.categ_id.name or "").lower()

            options = []
            sequence = 1

            def create_option(name, code):
                nonlocal sequence
                option = Option.create({
                    "name": name,
                    "code": code,
                    "token": line.x_bucket_token,
                    "sequence": sequence,
                })
                sequence += 1
                options.append(option)
                return option

            if "estructural" in categ:
                create_option("CES", "CES")
            elif "automotriz" in categ or "automotr\xedz" in categ:
                create_option(f"CAU: {int(selectable_qty)}", "CAU")
            else:
                create_option(f"OTROS: {int(selectable_qty)}", "OTROS")

            create_option(f"CAF: {int(caf_qty)}", "CAF")
            create_option(f"AAA: {int(aaa_qty)} (Info)", "AAA")

            default_option = next((opt for opt in options if opt.code != "AAA"), False)
            line.x_bucket_option_id = default_option.id if default_option else False
            line.x_bucket_code = default_option.code if default_option else False

    def _get_structural_qty_tolerance(self):
        params = self.env["mrp.master.type"].search([("active", "=", True)], order="id", limit=1)
        return float(getattr(params, "structural_import_quote_qty_tolerance", 0.0) or 0.0)

    def _get_structural_sale_type(self):
        self.ensure_one()
        if "x_studio_venta_de_producto" in self._fields:
            return (getattr(self, "x_studio_venta_de_producto", False) or "").strip().lower()
        order = self.order_id
        if order and "x_studio_venta_de_producto" in order._fields:
            return (getattr(order, "x_studio_venta_de_producto", False) or "").strip().lower()
        return ""

    def _compute_structural_m2(self):
        self.ensure_one()
        largo = float(getattr(self, "x_studio_largo", 0.0) or 0.0)
        ancho = float(getattr(self, "x_studio_ancho", 0.0) or 0.0)
        piezas = float(getattr(self, "x_studio_piezas", 0.0) or 0.0)
        if largo <= 0 or ancho <= 0 or piezas <= 0:
            return 0.0
        return (largo * ancho * piezas) / 1000000.0

    @api.onchange("x_studio_largo", "x_studio_ancho", "x_studio_piezas", "order_id")
    def _onchange_structural_m2(self):
        for line in self:
            if line._get_structural_sale_type() != "estructural":
                continue
            m2 = line._compute_structural_m2()
            if m2 > 0:
                line.product_uom_qty = round(m2, 2)

    @api.constrains("x_studio_largo", "x_studio_ancho", "x_studio_piezas", "product_uom_qty", "order_id")
    def _check_m2_for_estructural(self):
        tolerance = self._get_structural_qty_tolerance()
        for line in self:
            if line._get_structural_sale_type() != "estructural":
                continue
            m2_calculado = line._compute_structural_m2()
            cantidad = float(line.product_uom_qty or 0.0)
            if m2_calculado and abs(m2_calculado - cantidad) > tolerance:
                raise ValidationError(
                    _(
                        "ERROR en linea de cotizacion:\n\n"
                        "Largo x Ancho x Piezas (en m2) debe ser igual a la Cantidad (m2).\n"
                        "Calculado: %.3f m2, Cantidad ingresada: %.3f m2\n"
                        "Tolerancia permitida: %.3f m2\n\n"
                        "Por favor, corrija los valores para continuar."
                    ) % (m2_calculado, cantidad, tolerance)
                )

    def _ab_forecast_round(self, qty):
        return round(float(qty or 0.0), 2)

    def _ab_format_datetime(self, value):
        if not value:
            return False
        return fields.Datetime.to_string(value)

    def _ab_get_internal_locations(self):
        self.ensure_one()
        Location = self.env["stock.location"].sudo()
        warehouse = getattr(self.order_id, "warehouse_id", False)
        if warehouse and warehouse.view_location_id:
            return Location.search([
                ("id", "child_of", warehouse.view_location_id.id),
                ("usage", "=", "internal"),
            ])
        return Location.search([("usage", "=", "internal")])

    def _ab_get_caf_locations(self, locations):
        return locations.filtered(
            lambda loc: "CAF" in (loc.complete_name or "").upper() or "CAF" in (loc.name or "").upper()
        )

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

    def _ab_get_move_reserved_qty(self, move):
        self.ensure_one()
        reserved = float(getattr(move, "reserved_availability", 0.0) or 0.0)
        if reserved > 0:
            return reserved

        move_lines = getattr(move, "move_line_ids", False)
        if move_lines:
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
            if qty_from_lines > 0:
                return qty_from_lines
        return 0.0

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [
            self._ab_sync_bucket_vals(self._sync_qty_from_requested_qty(dict(vals)))
            for vals in vals_list
        ]
        lines = super().create(vals_list)
        if not self.env.context.get("skip_apply_route_logic"):
            lines._apply_route_logic()
        return lines

    def write(self, vals):
        vals = self._ab_sync_bucket_vals(dict(vals))
        if (
            "x_studio_pedido" in self._fields
            and "x_studio_pedido" in vals
            and "product_uom_qty" not in vals
        ):
            requested_qty = float(vals.get("x_studio_pedido") or 0.0)
            for line in self:
                super(SaleOrderLine, line).write({
                    **vals,
                    "product_uom_qty": requested_qty,
                })
            if not self.env.context.get("skip_apply_route_logic"):
                self._apply_route_logic()
            return True
        res = super().write(vals)
        if not self.env.context.get("skip_apply_route_logic"):
            self._apply_route_logic()
        return res

    @api.onchange("x_bucket_option_id")
    def _onchange_bucket_option_sync_code_and_route(self):
        for line in self:
            option = line.x_bucket_option_id
            if option and option.code == "AAA":
                line.x_bucket_code = False
                line.route_id = False
                return {
                    "warning": {
                        "title": _("AAA es informativo"),
                        "message": _("La opcion AAA solo es informativa y no debe usarse para tomar producto."),
                    }
                }
            line.x_bucket_code = option.code if option else False
            if not line.product_id:
                line.route_id = False
                continue
            if option and option.code == "CAF":
                line.route_id = line._find_caf_route()
            else:
                line.route_id = False

    def _apply_route_logic(self):
        for line in self.filtered(lambda ln: not getattr(ln, "display_type", False)):
            effective_bucket_code = line.x_bucket_code or (line.x_bucket_option_id.code if line.x_bucket_option_id else False)
            sync_vals = {}
            if line.x_bucket_code != effective_bucket_code:
                sync_vals["x_bucket_code"] = effective_bucket_code or False
            if line.x_bucket != effective_bucket_code:
                sync_vals["x_bucket"] = effective_bucket_code or False
            target_route_id = False
            caf_route = False
            if effective_bucket_code == "CAF" and caf_route:
                target_route_id = caf_route.id
            elif effective_bucket_code == "CAF":
                caf_route = line._find_caf_route()
                target_route_id = caf_route.id if caf_route else False
            current_route_id = line.route_id.id if line.route_id else False
            if current_route_id != target_route_id:
                sync_vals["route_id"] = target_route_id
            if sync_vals:
                super(SaleOrderLine, line.with_context(skip_apply_route_logic=True)).write(sync_vals)

    def _find_caf_route(self):
        self.ensure_one()
        Route = self.env["stock.route"].sudo()
        Rule = self.env["stock.rule"].sudo()
        routes = Route.search([], order="sale_selectable desc, id")
        for route in routes:
            rules = Rule.search([("route_id", "=", route.id), ("action", "in", ("pull", "pull_push"))])
            for rule in rules:
                src = rule.location_src_id
                src_name = (src.complete_name or "").upper() if src else ""
                if src and ("CAF" in src_name or "FALLAS" in src_name):
                    return route
        return False

    def _ab_render_forecast_detail_html(self, payload):
        summary = payload.get("summary", {})
        reservations = payload.get("reservations", [])
        stock_locations = payload.get("stock_locations", [])
        productions = payload.get("productions", [])

        def qty(value):
            return f"{self._ab_forecast_round(value):,.2f}"

        def text(value):
            return escape(value or "-")

        def reservation_block(item):
            return f"""
                <div class="ab-fd-card{' is-current' if item.get('is_current_line') else ''}">
                    <div class="ab-fd-card-title">{text(item.get('sale_order'))} <span>{text(item.get('partner'))}</span></div>
                    <div class="ab-fd-card-line">Reservado: <strong>{qty(item.get('reserved_qty'))}</strong> | Fecha pedido: {text(item.get('order_date'))}</div>
                    <div class="ab-fd-card-line">Fecha entrega: {text(item.get('delivery_date'))}</div>
                    <div class="ab-fd-card-line">Ubicacion: {text(item.get('location_leaf') or item.get('location'))} | Picking: {text(item.get('picking'))}</div>
                </div>
            """

        def stock_block(item):
            return f"""
                <div class="ab-fd-card">
                    <div class="ab-fd-card-title">{text(item.get('location_leaf') or item.get('location'))}</div>
                    <div class="ab-fd-card-line">Cantidad: <strong>{qty(item.get('qty'))}</strong></div>
                    <div class="ab-fd-card-line">Ubicacion completa: {text(item.get('location'))}</div>
                </div>
            """

        def production_block(item):
            commitments = item.get("commitments") or []
            commitment_html = "".join(
                f"<div class='ab-fd-subline'>{text(commitment.get('sale_order'))} | {text(commitment.get('partner'))} | {qty(commitment.get('qty'))}</div>"
                for commitment in commitments
            ) or "<div class='ab-fd-subline is-empty'>Sin compromisos detectados.</div>"
            return f"""
                <div class="ab-fd-card">
                    <div class="ab-fd-card-title">{text(item.get('name'))} <span>{text(item.get('state_label'))}</span></div>
                    <div class="ab-fd-card-line">Cantidad OF: <strong>{qty(item.get('qty'))}</strong> | Salida estimada: {text(item.get('planned_out'))}</div>
                    <div class="ab-fd-card-line">Comprometido: {qty(item.get('committed_qty'))} | Disponible: <strong>{qty(item.get('available_qty'))}</strong></div>
                    <div class="ab-fd-sublist">{commitment_html}</div>
                </div>
            """

        reservations_html = "".join(reservation_block(item) for item in reservations) or (
            "<div class='ab-fd-empty'>Sin reservas o despachos abiertos para este producto.</div>"
        )
        stock_html = "".join(stock_block(item) for item in stock_locations) or (
            "<div class='ab-fd-empty'>Sin stock disponible del producto en ubicaciones internas.</div>"
        )
        productions_html = "".join(production_block(item) for item in productions) or (
            "<div class='ab-fd-empty'>Sin ordenes de fabricacion abiertas para este producto.</div>"
        )

        return f"""
            <div class="ab-forecast-detail">
                <div class="ab-fd-header">
                    <div class="ab-fd-title">{text(payload.get('default_code'))} {text(payload.get('product_name'))}</div>
                    <div class="ab-fd-subtitle">{text(payload.get('warehouse'))}</div>
                </div>
                <div class="ab-fd-grid">
                    <div class="ab-fd-kpi"><span>A la mano</span><strong>{qty(summary.get('qty_on_hand'))}</strong></div>
                    <div class="ab-fd-kpi"><span>Reservado</span><strong>{qty(summary.get('qty_reserved'))}</strong></div>
                    <div class="ab-fd-kpi"><span>En CAF</span><strong>{qty(summary.get('qty_caf'))}</strong></div>
                    <div class="ab-fd-kpi is-highlight"><span>Disponible</span><strong>{qty(summary.get('qty_available_net'))}</strong></div>
                </div>
                <div class="ab-fd-note">Libre hoy: <strong>{qty(summary.get('free_qty_today'))}</strong> | Fecha pronosticada: <strong>{text(summary.get('forecast_expected_date'))}</strong></div>
                <div class="ab-fd-section">
                    <div class="ab-fd-section-title">Reservas y despachos</div>
                    {reservations_html}
                </div>
                <div class="ab-fd-section">
                    <div class="ab-fd-section-title">Stock por ubicacion</div>
                    {stock_html}
                </div>
                <div class="ab-fd-section">
                    <div class="ab-fd-section-title">Fabricacion relacionada</div>
                    {productions_html}
                </div>
            </div>
        """

    def get_ab_forecast_hover_data(self):
        self.ensure_one()

        product = self.product_id
        if not product:
            return {}

        Quant = self.env["stock.quant"].sudo()
        Production = self.env["mrp.production"].sudo()

        locations = self._ab_get_internal_locations()
        caf_locations = self._ab_get_caf_locations(locations)

        quants = Quant.search([
            ("product_id", "=", product.id),
            ("location_id", "in", locations.ids),
        ])
        on_hand_qty = sum(quants.mapped("quantity"))
        caf_qty = sum(quants.filtered(lambda q: q.location_id in caf_locations).mapped("quantity"))

        customer_moves = self._ab_get_customer_moves(product, locations)
        reserved_qty = sum(self._ab_get_move_reserved_qty(mv) for mv in customer_moves)
        available_qty = on_hand_qty - reserved_qty - caf_qty

        reservation_lines = []
        for mv in customer_moves:
            reserved = self._ab_get_move_reserved_qty(mv)
            so = mv.sale_line_id.order_id if mv.sale_line_id else False
            partner = so.partner_id if so else mv.picking_id.partner_id
            location_name = mv.location_id.complete_name if mv.location_id else False
            reservation_lines.append({
                "move_id": mv.id,
                "sale_order": so.name if so else (mv.origin or getattr(mv, "reference", False) or mv.picking_id.name or False),
                "partner": partner.display_name if partner else False,
                "reserved_qty": self._ab_forecast_round(reserved),
                "demand_qty": self._ab_forecast_round(
                    getattr(mv, "product_uom_qty", 0.0) or getattr(mv, "quantity", 0.0) or 0.0
                ),
                "date": self._ab_format_datetime(
                    getattr(mv, "date_deadline", False) or getattr(mv.picking_id, "scheduled_date", False) or mv.date
                ),
                "order_date": self._ab_format_datetime(so.date_order) if so and getattr(so, "date_order", False) else False,
                "delivery_date": self._ab_format_datetime(
                    getattr(mv, "date_deadline", False) or getattr(mv.picking_id, "scheduled_date", False) or False
                ) or "No programada",
                "location": location_name,
                "location_leaf": location_name.split("/")[-1].strip() if location_name else False,
                "picking": mv.picking_id.name if mv.picking_id else False,
                "is_current_line": mv.sale_line_id.id == self.id if mv.sale_line_id else False,
                "state": mv.state,
            })

        state_labels = {
            "draft": "Borrador",
            "confirmed": "Confirmada",
            "progress": "En proceso",
            "to_close": "Por cerrar",
            "done": "Hecha",
            "cancel": "Cancelada",
        }
        productions = Production.search([
            ("product_id", "=", product.id),
            ("state", "not in", ["done", "cancel"]),
        ], order="id desc", limit=25)

        production_lines = []
        for mo in productions:
            commitment_lines = []
            commitment_qty = 0.0
            for finished_mv in mo.move_finished_ids.filtered(lambda mv: mv.state not in ("done", "cancel")):
                destinations = finished_mv.move_dest_ids.filtered(lambda dest: dest.state not in ("done", "cancel"))
                if destinations:
                    for dest in destinations:
                        qty = float(
                            getattr(dest, "reserved_availability", 0.0)
                            or getattr(dest, "product_uom_qty", 0.0)
                            or 0.0
                        )
                        commitment_qty += qty
                        so = dest.sale_line_id.order_id if dest.sale_line_id else False
                        partner = so.partner_id if so else dest.picking_id.partner_id
                        commitment_lines.append({
                            "move_id": dest.id,
                            "sale_order": so.name if so else (dest.origin or dest.picking_id.name or False),
                            "partner": partner.display_name if partner else False,
                            "qty": self._ab_forecast_round(qty),
                            "picking": dest.picking_id.name if dest.picking_id else False,
                            "state": dest.state,
                        })

            planned_out = (
                getattr(mo, "date_deadline", False)
                or getattr(mo, "date_planned_finished", False)
                or getattr(mo, "date_finished", False)
                or getattr(mo, "date_start", False)
            )
            qty_in_mo = float(mo.product_qty or 0.0)
            production_lines.append({
                "mo_id": mo.id,
                "name": mo.name,
                "origin": mo.origin,
                "state": mo.state,
                "state_label": state_labels.get(mo.state, mo.state),
                "qty": self._ab_forecast_round(qty_in_mo),
                "planned_out": self._ab_format_datetime(planned_out),
                "committed_qty": self._ab_forecast_round(commitment_qty),
                "available_qty": self._ab_forecast_round(qty_in_mo - commitment_qty),
                "commitments": commitment_lines,
            })

        stock_by_location = {}
        for quant in quants.filtered(lambda q: q.quantity):
            location = quant.location_id
            if not location:
                continue
            key = location.id
            if key not in stock_by_location:
                stock_by_location[key] = {
                    "location": location.complete_name or location.display_name or location.name,
                    "location_leaf": (
                        (location.complete_name or location.display_name or location.name or "").split("/")[-1].strip()
                    ),
                    "qty": 0.0,
                }
            stock_by_location[key]["qty"] += float(quant.quantity or 0.0)

        stock_breakdown = [
            {
                "location": values["location"],
                "location_leaf": values["location_leaf"],
                "qty": self._ab_forecast_round(values["qty"]),
            }
            for _, values in sorted(stock_by_location.items(), key=lambda item: item[1]["location"])
            if abs(values["qty"]) > 1e-6
        ]

        return {
            "line_id": self.id,
            "product_id": product.id,
            "product_name": product.display_name,
            "default_code": product.default_code,
            "warehouse": self.order_id.warehouse_id.display_name if getattr(self.order_id, "warehouse_id", False) else False,
            "summary": {
                "qty_on_hand": self._ab_forecast_round(on_hand_qty),
                "qty_reserved": self._ab_forecast_round(reserved_qty),
                "qty_caf": self._ab_forecast_round(caf_qty),
                "qty_available_net": self._ab_forecast_round(available_qty),
                "free_qty_today": self._ab_forecast_round(getattr(self, "free_qty_today", 0.0) or 0.0),
                "forecast_expected_date": self._ab_format_datetime(getattr(self, "forecast_expected_date", False)),
            },
            "reservations": reservation_lines,
            "stock_locations": stock_breakdown,
            "caf_locations": [item for item in stock_breakdown if "CAF" in (item.get("location") or "").upper()],
            "productions": production_lines,
        }

    def action_open_ab_forecast_detail(self):
        self.ensure_one()
        payload = self.get_ab_forecast_hover_data()
        wizard = self.env["sale.forecast.detail.wizard"].create({
            "sale_line_id": self.id,
            "detail_html": self._ab_render_forecast_detail_html(payload),
        })
        return {
            "type": "ir.actions.act_window",
            "name": _("Detalle de disponibilidad"),
            "res_model": "sale.forecast.detail.wizard",
            "view_mode": "form",
            "res_id": wizard.id,
            "target": "new",
            "context": {"dialog_size": "extra-large"},
        }
