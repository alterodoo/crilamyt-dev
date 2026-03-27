from odoo import fields, models


class SaleForecastDetailWizard(models.TransientModel):
    _name = "sale.forecast.detail.wizard"
    _description = "Detalle de Disponibilidad de Venta"

    sale_line_id = fields.Many2one("sale.order.line", string="Linea de venta", readonly=True)
    detail_html = fields.Html(string="Detalle", sanitize=False, readonly=True)
