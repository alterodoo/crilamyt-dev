from odoo import models, _, http
from odoo.exceptions import UserError

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_whatsappweb_quotation(self):
        for order in self:
            partner = order.partner_id
            mobile = partner.mobile
            if not mobile:
                raise UserError(_("El cliente no tiene un número de Celular registrado, por favor actualizar la información."))

            # Obtener base URL del sistema
            base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
            # Obtener path de portal (relativo)
            portal_path = order.get_portal_url()
            # Construir link absoluto
            portal_url = f"{base_url}{portal_path}"

            # Mensaje solicitado con número de cotización y doble salto de línea
            message = (
                f"Mediante el presente mensaje hago extensa la cotización {order.name} correspondiente a los productos solicitados.\n\n"
                "Por favor, sírvase revisar y estaremos atentos para ayudarle con su pedido lo antes posible.\n\n"
                f"{portal_url}"
            )
            phone_clean = mobile.replace("+", "").replace(" ", "")
            message_encoded = message.replace(' ', '%20').replace('\n', '%0A')
            whatsapp_url = f"https://api.whatsapp.com/send?phone={phone_clean}&text={message_encoded}"
            return {
                'type': 'ir.actions.act_url',
                'url': whatsapp_url,
                'target': 'new',
            }

