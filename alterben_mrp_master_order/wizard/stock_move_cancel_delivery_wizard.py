from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class StockMoveCancelDeliveryWizard(models.TransientModel):
    _name = "stock.move.cancel.delivery.wizard"
    _description = "Cancelar despacho de producto"

    move_id = fields.Many2one("stock.move", string="Movimiento", required=True, readonly=True)
    sale_order_id = fields.Many2one("sale.order", string="Orden de venta", readonly=True)
    picking_id = fields.Many2one("stock.picking", string="Transferencia", readonly=True)
    product_id = fields.Many2one("product.product", string="Producto", readonly=True)
    max_qty = fields.Float(string="Cantidad maxima cancelable", readonly=True)
    quantity = fields.Float(string="Cantidad a cancelar", required=True)
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

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        move_id = vals.get("move_id") or self.env.context.get("default_move_id")
        if move_id:
            move = self.env["stock.move"].browse(move_id)
            vals.update(self._prepare_move_display_vals(move))
        return vals

    def _prepare_move_display_vals(self, move):
        return {
            "sale_order_id": move.ab_sale_order_id.id if move.ab_sale_order_id else False,
            "picking_id": move.picking_id.id if move.picking_id else False,
            "product_id": move.product_id.id if move.product_id else False,
            "max_qty": self._get_move_pending_qty(move) if move else 0.0,
        }

    @api.onchange("move_id")
    def _onchange_move_id(self):
        for wizard in self:
            if wizard.move_id:
                wizard.update(wizard._prepare_move_display_vals(wizard.move_id))
            else:
                wizard.sale_order_id = False
                wizard.picking_id = False
                wizard.product_id = False
                wizard.max_qty = 0.0

    def _get_move_pending_qty(self, move):
        qty_done = move._get_ab_qty_done()
        return max(float(move.product_uom_qty or 0.0) - qty_done, 0.0)

    def action_confirm(self):
        self.ensure_one()
        move = self.move_id
        if not move:
            raise UserError(_("No se encontro el movimiento a cancelar."))
        if not move.sale_line_id:
            raise UserError(_("Solo se pueden cancelar lineas vinculadas a una orden de venta."))

        max_qty = self._get_move_pending_qty(move)
        qty = float(self.quantity or 0.0)
        if qty <= 0:
            raise ValidationError(_("La cantidad a cancelar debe ser mayor que cero."))
        if qty > max_qty:
            raise ValidationError(_("La cantidad a cancelar no puede ser mayor a la pendiente por despachar."))

        sale_line = move.sale_line_id
        reserved_before = move._get_ab_reserved_qty()
        pending_before = self._get_move_pending_qty(move)
        move.env["sale.delivery.cancellation.log"].create({
            "move_id": move.id,
            "sale_line_id": sale_line.id,
            "quantity": qty,
            "reason": self.reason,
            "note": self.note or False,
        })

        qty_done = move._get_ab_qty_done()
        new_demand = max(float(move.product_uom_qty or 0.0) - qty, qty_done)
        pending_after = max(new_demand - qty_done, 0.0)

        if hasattr(move, "_do_unreserve"):
            move._do_unreserve()
        elif move.picking_id and hasattr(move.picking_id, "do_unreserve"):
            move.picking_id.do_unreserve()

        if new_demand <= 0 and qty_done <= 0:
            try:
                move._action_cancel()
            except Exception:
                # Some external automation chains fail on full cancel; keep the move with zero pending
                # so it disappears from the pending-delivery console without blocking the user action.
                move.write({"product_uom_qty": 0.0})
        else:
            move.write({"product_uom_qty": new_demand})
            if pending_after > 0 and move.state not in ("done", "cancel", "draft"):
                move._action_assign()

        move.invalidate_recordset()
        refreshed_move = move.browse(move.id)
        if hasattr(refreshed_move, "_ab_create_pending_delivery_audit_entry"):
            refreshed_move._ab_create_pending_delivery_audit_entry(
                "cancel",
                reserved_before,
                refreshed_move._get_ab_reserved_qty(),
                pending_before,
                float(refreshed_move.ab_qty_pending or 0.0),
                quantity_changed=qty,
                reason=self.reason,
                note=self.note or False,
            )

        return {"type": "ir.actions.client", "tag": "reload"}
