from odoo import api, fields, models


class SaleDeliveryCancellationLog(models.Model):
    _name = "sale.delivery.cancellation.log"
    _description = "Historial de cancelacion de despacho"
    _order = "create_date desc, id desc"

    move_id = fields.Many2one("stock.move", string="Movimiento", required=True, ondelete="cascade", index=True)
    sale_line_id = fields.Many2one("sale.order.line", string="Linea de venta", required=True, ondelete="cascade", index=True)
    order_id = fields.Many2one("sale.order", string="Orden de venta", related="sale_line_id.order_id", store=True, readonly=True)
    picking_id = fields.Many2one("stock.picking", string="Transferencia", related="move_id.picking_id", store=True, readonly=True)
    product_id = fields.Many2one("product.product", string="Producto", related="move_id.product_id", store=True, readonly=True)
    quantity = fields.Float(string="Cantidad cancelada", required=True)
    reason = fields.Selection(
        [
            ("stock_shortage", "Falta de stock"),
            ("shipping_delay", "Demora en envio"),
            ("manufacturing_delay", "Demora en fabricacion"),
            ("other", "Otros"),
        ],
        string="Motivo",
        required=True,
    )
    note = fields.Char(string="Observacion")
    user_id = fields.Many2one("res.users", string="Usuario", default=lambda self: self.env.user, readonly=True)


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    ab_delivery_cancellation_ids = fields.One2many(
        "sale.delivery.cancellation.log",
        "sale_line_id",
        string="Cancelaciones de despacho",
    )
    ab_cancelled_delivery_qty = fields.Float(
        string="Cant. cancelada despacho",
        compute="_compute_ab_delivery_cancellation_qty",
    )
    ab_net_delivery_qty = fields.Float(
        string="Cant. neta despacho",
        compute="_compute_ab_delivery_cancellation_qty",
    )
    ab_qty_to_deliver_net = fields.Float(
        string="Cant. pendiente neta",
        compute="_compute_ab_delivery_cancellation_qty",
    )

    @api.depends("ab_delivery_cancellation_ids.quantity", "product_uom_qty", "qty_delivered")
    def _compute_ab_delivery_cancellation_qty(self):
        for line in self:
            cancelled_qty = sum(line.ab_delivery_cancellation_ids.mapped("quantity"))
            net_qty = max(float(line.product_uom_qty or 0.0) - cancelled_qty, 0.0)
            qty_delivered = float(getattr(line, "qty_delivered", 0.0) or 0.0)
            line.ab_cancelled_delivery_qty = cancelled_qty
            line.ab_net_delivery_qty = net_qty
            line.ab_qty_to_deliver_net = max(net_qty - qty_delivered, 0.0)

    def action_view_delivery_cancellations(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Historial de cancelaciones",
            "res_model": "sale.delivery.cancellation.log",
            "view_mode": "tree,form",
            "domain": [("sale_line_id", "=", self.id)],
            "target": "current",
            "context": {
                "default_sale_line_id": self.id,
            },
        }
