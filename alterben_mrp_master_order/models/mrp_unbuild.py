from odoo import api, models


class MrpUnbuild(models.Model):
    _inherit = "mrp.unbuild"

    def _ab_recompute_master_order_lines(self):
        productions = self.mapped("mo_id").filtered(lambda p: p)
        if productions:
            self.env["mrp.master.order.line"]._recompute_cantidad_real_for_productions(productions)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._ab_recompute_master_order_lines()
        return records

    def write(self, vals):
        productions_before = self.mapped("mo_id").filtered(lambda p: p)
        res = super().write(vals)
        productions = productions_before | self.mapped("mo_id").filtered(lambda p: p)
        if productions:
            self.env["mrp.master.order.line"]._recompute_cantidad_real_for_productions(productions)
        return res

    def unlink(self):
        productions = self.mapped("mo_id").filtered(lambda p: p)
        res = super().unlink()
        if productions:
            self.env["mrp.master.order.line"]._recompute_cantidad_real_for_productions(productions)
        return res
