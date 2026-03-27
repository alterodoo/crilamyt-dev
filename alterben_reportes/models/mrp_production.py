# -*- coding: utf-8 -*-
from odoo import api, fields, models


class MrpProduction(models.Model):
    _inherit = "mrp.production"

    mo_cost_currency_id = fields.Many2one(
        comodel_name="res.currency",
        string="Moneda Costo",
        related="company_id.currency_id",
        readonly=True,
    )
    mo_unit_cost = fields.Monetary(
        string="Costo unitario fabricacion",
        currency_field="mo_cost_currency_id",
        readonly=True,
        compute="_compute_mo_unit_cost",
    )

    @api.depends(
        "state",
        "product_id",
        "move_finished_ids",
        "move_finished_ids.state",
        "move_finished_ids.stock_valuation_layer_ids.value",
        "move_finished_ids.stock_valuation_layer_ids.quantity",
    )
    def _compute_mo_unit_cost(self):
        for rec in self:
            rec.mo_unit_cost = 0.0
            if rec.state not in ("to_close", "done") or not rec.product_id:
                continue

            finished_moves = rec.move_finished_ids.filtered(
                lambda m: m.state == "done"
                and m.product_id == rec.product_id
                and not getattr(m, "scrapped", False)
            )
            if not finished_moves:
                continue

            svl_qty = 0.0
            svl_val = 0.0
            if "stock_valuation_layer_ids" in self.env["stock.move"]._fields:
                svls = finished_moves.mapped("stock_valuation_layer_ids").filtered(
                    lambda v: v.product_id == rec.product_id
                )
                svl_qty = sum(abs(v.quantity or 0.0) for v in svls)
                svl_val = sum(abs(v.value or 0.0) for v in svls)

            if svl_qty:
                rec.mo_unit_cost = svl_val / svl_qty
                continue

            total_qty = 0.0
            total_val = 0.0
            for mv in finished_moves:
                qty = abs((getattr(mv, "quantity_done", 0.0) or getattr(mv, "product_uom_qty", 0.0) or 0.0))
                unit = abs(getattr(mv, "price_unit", 0.0) or 0.0)
                total_qty += qty
                total_val += unit * qty

            rec.mo_unit_cost = (total_val / total_qty) if total_qty else 0.0
