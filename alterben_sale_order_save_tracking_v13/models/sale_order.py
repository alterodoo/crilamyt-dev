from odoo import models

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def write(self, vals):
        changes = []

        for order in self:
            old_terms = order.payment_term_id.name if order.payment_term_id else ""
            if 'payment_term_id' in vals:
                new_terms_id = vals['payment_term_id']
                new_terms = self.env['account.payment.term'].browse(new_terms_id).name if new_terms_id else ""
                if old_terms != new_terms:
                    changes.append(
                        f"⏰ <b>Términos de Pago:</b> {old_terms} → {new_terms}"
                    )

            if 'order_line' in vals:
                for command in vals['order_line']:
                    if command[0] == 1:
                        line_id = command[1]
                        line_vals = command[2]
                        line = self.env['sale.order.line'].browse(line_id)
                        if 'product_uom_qty' in line_vals:
                            old_qty = line.product_uom_qty
                            new_qty = line_vals['product_uom_qty']
                            if old_qty != new_qty:
                                changes.append(
                                    f"📦 <b>Cantidad</b> en <b>[{line.product_id.display_name}]</b>: {old_qty} → {new_qty}"
                                )
                        if 'discount' in line_vals:
                            old_disc = line.discount
                            new_disc = line_vals['discount']
                            if old_disc != new_disc:
                                changes.append(
                                    f"💲 <b>Descuento</b> en <b>[{line.product_id.display_name}]</b>: {old_disc}% → {new_disc}%"
                                )

        result = super().write(vals)
        if changes:
            html = "".join(f"• {c}<br>" for c in changes)
            self.message_post(
                body=f"<b>Cambios al guardar cotización:</b><br>{html}",
                subtype_xmlid="mail.mt_note"
            )
        return result
