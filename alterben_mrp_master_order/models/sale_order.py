from odoo import api, models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    def _ab_avoid_negative_invoice_lines_from_returns_enabled(self):
        param_value = self.env["ir.config_parameter"].sudo().get_param(
            "alterben_mrp_master_order.ab_avoid_negative_invoice_lines_from_returns",
            default="False",
        )
        return str(param_value).lower() in ("1", "true", "yes", "on")

    def _get_invoiceable_lines(self, final=False):
        if not self._ab_avoid_negative_invoice_lines_from_returns_enabled():
            return super()._get_invoiceable_lines(final=final)
        lines = super()._get_invoiceable_lines(final=final)
        positive_lines = lines.filtered(
            lambda line: not line.display_type and float(line.qty_to_invoice or 0.0) > 0.0
        )
        if not positive_lines:
            return positive_lines

        positive_ids = set(positive_lines.ids)
        cleaned_lines = self.env["sale.order.line"]
        pending_section = self.env["sale.order.line"]
        for line in lines:
            if line.display_type:
                pending_section = line
                continue
            if line.id in positive_ids:
                cleaned_lines |= pending_section
                cleaned_lines |= line
                pending_section = self.env["sale.order.line"]
        return cleaned_lines

    @api.depends("state", "order_line.invoice_status", "order_line.qty_to_invoice", "order_line.display_type")
    def _compute_invoice_status(self):
        if not self._ab_avoid_negative_invoice_lines_from_returns_enabled():
            return super()._compute_invoice_status()
        for order in self:
            if order.state not in ("sale", "done"):
                order.invoice_status = "no"
                continue

            lines = order.order_line.filtered(lambda line: not line.display_type)
            positive_lines = lines.filtered(lambda line: float(line.qty_to_invoice or 0.0) > 0.0)
            if positive_lines:
                order.invoice_status = "to invoice"
                continue

            if lines and all(line.invoice_status == "invoiced" for line in lines):
                order.invoice_status = "invoiced"
            else:
                order.invoice_status = "no"
