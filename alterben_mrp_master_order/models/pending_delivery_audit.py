from odoo import fields, models


class PendingDeliveryAuditLog(models.Model):
    _name = "stock.move.pending.delivery.audit"
    _description = "Auditoria de pedidos pendientes de entrega"
    _order = "create_date desc, id desc"

    move_id = fields.Many2one("stock.move", string="Movimiento", required=True, ondelete="cascade", index=True)
    sale_line_id = fields.Many2one("sale.order.line", string="Linea de venta", ondelete="set null", index=True)
    order_id = fields.Many2one("sale.order", string="Orden de venta", related="sale_line_id.order_id", store=True, readonly=True)
    picking_id = fields.Many2one("stock.picking", string="Transferencia", related="move_id.picking_id", store=True, readonly=True)
    product_id = fields.Many2one("product.product", string="Producto", related="move_id.product_id", store=True, readonly=True)
    action_type = fields.Selection(
        [
            ("reserve", "Reservar"),
            ("unreserve", "Liberar"),
            ("cancel", "Cancelar"),
        ],
        string="Accion",
        required=True,
        index=True,
    )
    reserved_before = fields.Float(string="Reservado antes")
    reserved_after = fields.Float(string="Reservado despues")
    pending_before = fields.Float(string="Pendiente antes")
    pending_after = fields.Float(string="Pendiente despues")
    quantity_changed = fields.Float(string="Cantidad afectada")
    reason = fields.Selection(
        [
            ("stock_shortage", "Falta de stock"),
            ("shipping_delay", "Demora en envio"),
            ("manufacturing_delay", "Demora en fabricacion"),
            ("other", "Otros"),
        ],
        string="Motivo",
    )
    note = fields.Char(string="Observacion")
    user_id = fields.Many2one("res.users", string="Usuario", default=lambda self: self.env.user, readonly=True, index=True)
