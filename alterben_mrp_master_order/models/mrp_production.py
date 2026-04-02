from odoo import api, fields, models


class MrpProduction(models.Model):
    _inherit = "mrp.production"

    ab_consumed_qty = fields.Float(
        string="Consumido",
        compute="_compute_ab_consumed_qty",
        store=True,
    )

    @api.depends(
        "move_raw_ids",
        "move_raw_ids.state",
        "move_raw_ids.quantity",
        "move_raw_ids.product_uom_qty",
    )
    def _compute_ab_consumed_qty(self):
        for production in self:
            raw_moves = production.move_raw_ids.filtered(lambda mv: mv.state != "cancel").sorted(
                key=lambda mv: ((getattr(mv, "sequence", 0) or 0), mv.id)
            )
            consumed_qty = 0.0
            if raw_moves:
                first_move = raw_moves[0]
                for field_name in ("quantity", "quantity_done", "qty_done", "product_uom_qty"):
                    if field_name in first_move._fields:
                        consumed_qty = float(getattr(first_move, field_name, 0.0) or 0.0)
                        break
            production.ab_consumed_qty = consumed_qty

    def _get_master_parameters_for_validation(self):
        self.ensure_one()
        if self.master_order_id and self.master_order_id.type_id:
            return self.master_order_id.type_id
        return self.env["mrp.master.type"].sudo().search([("active", "=", True)], limit=1)

    def _get_raw_move_lines_for_validation(self):
        self.ensure_one()
        move_lines = getattr(self, "move_raw_line_ids", False)
        if move_lines is not False:
            return move_lines
        return self.move_raw_ids.mapped("move_line_ids")

    def _validate_master_parameter_rules(self):
        for production in self:
            master_type = production._get_master_parameters_for_validation()
            if not master_type:
                continue
            move_lines = production._get_raw_move_lines_for_validation().filtered(lambda ml: ml.product_id)
            if not move_lines:
                continue
            master_type._validate_negative_stock(move_lines, "allow_negative_mrp_consumption")

    def button_mark_done(self):
        self._validate_master_parameter_rules()
        return super().button_mark_done()
