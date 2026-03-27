from odoo import api, fields, models, _
from odoo.exceptions import UserError


BLOCKED_SOURCE_LOCATIONS_PARAM = "alterben_mrp_master_order.blocked_source_location_ids"


class StockPicking(models.Model):
    _inherit = "stock.picking"

    control_total_label_count = fields.Integer(compute="_compute_control_total_label_count")
    ab_entrega_inmediata_display = fields.Char(
        string="Entrega inmediata",
        compute="_compute_ab_entrega_inmediata_display",
    )
    ab_cancelled_delivery_total = fields.Float(
        string="Cant. cancelada",
        compute="_compute_ab_cancelled_delivery_total",
    )

    def _compute_control_total_label_count(self):
        for p in self:
            p.control_total_label_count = self.env["control.total.label"].search_count([("picking_id","=",p.id)])

    @api.model
    def _ab_get_immediate_delivery_field_name(self):
        preferred_names = [
            "x_studio_entrega_inmediata",
            "entrega_inmediata",
            "x_entrega_inmediata",
            "immediate_delivery",
            "x_immediate_delivery",
            "x_studio_immediate_delivery",
            "immediate_transfer",
        ]
        for field_name in preferred_names:
            if field_name in self._fields:
                return field_name

        for field_name, field in self._fields.items():
            haystack = f"{field_name} {field.string or ''}".lower()
            if "entrega inmediata" in haystack:
                return field_name
            if "entrega" in haystack and "inmediat" in haystack:
                return field_name

        return False

    @api.model
    def _ab_format_immediate_delivery_value(self, picking):
        field_name = self._ab_get_immediate_delivery_field_name()
        if not field_name or field_name not in picking._fields:
            return ""

        field = picking._fields[field_name]
        value = picking[field_name]
        if value in (False, None, ""):
            return ""
        if field.type == "boolean":
            return _("Si") if value else _("No")
        if field.type == "selection":
            selection = dict(field.selection(picking.env) if callable(field.selection) else field.selection or [])
            return selection.get(value, value)
        if field.type == "many2one":
            return value.display_name or ""
        return str(value)

    def _compute_ab_entrega_inmediata_display(self):
        for picking in self:
            picking.ab_entrega_inmediata_display = self._ab_format_immediate_delivery_value(picking)

    def _compute_ab_cancelled_delivery_total(self):
        for picking in self:
            moves = picking.move_ids_without_package.filtered(lambda mv: mv.state != "draft")
            picking.ab_cancelled_delivery_total = sum(
                float(getattr(move, "ab_cancelled_delivery_qty", 0.0) or 0.0)
                for move in moves
            )

    def action_view_control_total_labels(self):
        self.ensure_one()
        action = self.env.ref("alterben_mrp_master_order.action_control_total_label").read()[0]
        action["domain"] = [("picking_id","=", self.id)]
        action["context"] = { "default_picking_id": self.id }
        return action

    def _get_master_parameters_for_validation(self):
        self.ensure_one()
        master_types = (
            self.move_ids_without_package.mapped("raw_material_production_id.master_order_id.type_id")
            | self.move_ids_without_package.mapped("production_id.master_order_id.type_id")
        ).filtered(lambda t: t)
        if master_types:
            return master_types[:1]
        return self.env["mrp.master.type"].sudo().search([("active", "=", True)], limit=1)

    def _get_negative_stock_field_name(self):
        self.ensure_one()
        is_return = bool(self.move_ids_without_package.filtered("origin_returned_move_id"))
        if is_return:
            return "allow_negative_returns"
        if self.picking_type_id.code == "outgoing":
            return "allow_negative_customer_delivery"
        return "allow_negative_transfer"

    def _validate_master_parameter_rules(self):
        for picking in self:
            master_type = picking._get_master_parameters_for_validation()
            if not master_type:
                continue
            move_lines = picking.move_line_ids.filtered(lambda ml: ml.product_id)
            if not move_lines:
                continue
            master_type._validate_negative_stock(move_lines, picking._get_negative_stock_field_name())
            master_type._validate_restricted_consumption_locations(move_lines)
            master_type._validate_restricted_destination_locations(move_lines)

    @api.model
    def _get_blocked_source_location_ids_from_config(self):
        raw_value = self.env["ir.config_parameter"].sudo().get_param(BLOCKED_SOURCE_LOCATIONS_PARAM, default="")
        return {
            int(value)
            for value in (raw_value or "").split(",")
            if value.strip().isdigit()
        }

    @staticmethod
    def _ab_move_line_has_validation_qty(move_line):
        for field_name in ("quantity", "qty_done", "quantity_product_uom", "reserved_uom_qty"):
            if field_name in move_line._fields:
                qty = float(getattr(move_line, field_name, 0.0) or 0.0)
                if abs(qty) > 1e-9:
                    return True
        move = getattr(move_line, "move_id", False)
        if move:
            for field_name in ("quantity", "quantity_done", "product_uom_qty", "reserved_availability"):
                if field_name in move._fields:
                    qty = float(getattr(move, field_name, 0.0) or 0.0)
                    if abs(qty) > 1e-9:
                        return True
        return False

    def _get_blocked_source_validation_violations(self):
        self.ensure_one()
        blocked_location_ids = self._get_blocked_source_location_ids_from_config()
        if not blocked_location_ids:
            return []

        violations = set()
        relevant_move_lines = self.move_line_ids.filtered(
            lambda ml: (
                ml.product_id
                and ml.state != "cancel"
                and ml.location_id
                and ml.location_id.id in blocked_location_ids
                and self._ab_move_line_has_validation_qty(ml)
            )
        )
        for move_line in relevant_move_lines:
            violations.add((move_line.location_id.display_name or move_line.location_id.complete_name or "", move_line.product_id.display_name or ""))

        if not relevant_move_lines:
            fallback_moves = self.move_ids_without_package.filtered(
                lambda mv: (
                    mv.product_id
                    and mv.state not in ("draft", "cancel")
                    and mv.location_id
                    and mv.location_id.id in blocked_location_ids
                    and abs(float(getattr(mv, "quantity", 0.0) or getattr(mv, "quantity_done", 0.0) or getattr(mv, "product_uom_qty", 0.0) or 0.0)) > 1e-9
                )
            )
            for move in fallback_moves:
                violations.add((move.location_id.display_name or move.location_id.complete_name or "", move.product_id.display_name or ""))

        return sorted(violations, key=lambda item: (item[0], item[1]))

    def _validate_blocked_source_locations_on_validate(self):
        message_blocks = []
        for picking in self:
            violations = picking._get_blocked_source_validation_violations()
            if not violations:
                continue
            lines = [picking.display_name or picking.name or _("Transferencia")]
            for location_name, product_name in violations:
                lines.append(_("- %s -> %s") % (product_name, location_name))
            message_blocks.append("\n".join(lines))

        if message_blocks:
            raise UserError(
                _(
                    "No se puede validar esta transferencia porque existen productos saliendo desde una ubicación bloqueada.\n"
                    "Debes mover el stock desde una ubicación hija (percha) y no desde la bodega padre.\n\n%s"
                )
                % "\n\n".join(message_blocks)
            )

    def button_validate(self):
        self._validate_master_parameter_rules()
        self._validate_blocked_source_locations_on_validate()
        return super().button_validate()

    def action_assign_control_total_wizard(self):
        self.ensure_one()
        return {
            "name": _("Asignar Control Total"),
            "type": "ir.actions.act_window",
            "res_model": "assign.control.total.wizard",
            "view_mode": "form",
            "view_id": self.env.ref("alterben_mrp_master_order.view_assign_control_total_wizard_form").id,
            "target": "new",
            "context": {"default_picking_id": self.id, "dialog_size": "fullscreen"},
        }

   
    def action_preassign_control_total(self):
        """Pre-asigna rangos por producto en el picking, evitando repetidos y usando
        el rango configurado para Picking en Ajustes de compañía.
        Escribe ct_pre_from/ct_pre_to en cada stock.move con cantidad > 0.
        No crea registros en control.total.label; solo deja la preasignación guardada en movimientos."""
        self.ensure_one()
        Label = self.env['control.total.label']

        import re

        def split_code(code):
            m = re.match(r'^(?P<prefix>\D*?)(?P<num>\d+)$', code or '')
            if m:
                return (m.group('prefix') or ''), int(m.group('num'))
            return (code or ''), None

        company = self.company_id or self.env.company
        range_from = company.ct_picking_from or 0
        range_to = company.ct_picking_to or 0

        # Determinar prefijo y último número según etiquetas existentes
        pf_default = 'CS-'
        last_num = None
        
        # Buscar la última etiqueta sin filtrar por compañía
        last_label = Label.search([], order='create_date desc', limit=1)
        if last_label:
            pf_last, last_num = split_code(last_label.name)
            prefix = pf_last or pf_default
        else:
            prefix = pf_default

        def is_code_taken(code):
            return bool(Label.search_count([('name', '=', code)]))

        def next_free(start_n):
            n = start_n
            while True:
                code = f"{prefix}{n}"
                if not is_code_taken(code):
                    return n
                n += 1

        # Punto de partida dentro del rango configurado (si existe)
        if range_from:
            start_n = range_from
            if last_num:
                start_n = max(range_from, last_num + 1)
        else:
            start_n = (last_num + 1) if last_num else 1

        # Movimientos a pre-asignar
        moves = self.move_ids_without_package.filtered(lambda m: m.product_id and m.product_uom_qty > 0)
        cur = next_free(start_n)

        for move in moves:
            qty = int(move.product_uom_qty or 0)
            if qty <= 0:
                move.ct_pre_from = False
                move.ct_pre_to = False
                continue

            if range_to and cur + qty - 1 > range_to:
                raise UserError(_("No hay suficientes etiquetas disponibles en el rango configurado para Picking."))

            nums = []
            taken = 0
            n = cur
            while taken < qty:
                if range_to and n > range_to:
                    raise UserError(_("No hay suficientes etiquetas disponibles en el rango configurado para Picking."))
                code = f"{prefix}{n}"
                if not is_code_taken(code):
                    nums.append(n)
                    taken += 1
                n += 1

            first_n = min(nums)
            last_n = max(nums)
            width = max(5, len(str(first_n)), len(str(last_n)))
            move.ct_pre_from = f"{prefix}{str(first_n).zfill(width)}"
            move.ct_pre_to = f"{prefix}{str(last_n).zfill(width)}"
            cur = n  # Continuar desde el siguiente número disponible

        # Notificación y refresco del formulario
        try:
            self.env.user.notify_success(message=_('Pre-asignación de etiquetas completada.'))
        except Exception:
            # En contextos sin bus/notify no debe romper la acción
            pass
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'stock.picking',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }
