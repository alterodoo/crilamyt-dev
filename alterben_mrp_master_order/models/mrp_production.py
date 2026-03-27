from odoo import models


class MrpProduction(models.Model):
    _inherit = "mrp.production"

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
