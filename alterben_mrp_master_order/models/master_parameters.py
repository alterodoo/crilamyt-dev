from odoo import fields, models, _
from odoo.exceptions import UserError


class MrpMasterType(models.Model):
    _inherit = "mrp.master.type"

    allow_negative_transfer = fields.Boolean(
        string="Permitir stock negativo en transferencias",
        default=False,
        tracking=True,
    )
    allow_negative_mrp_consumption = fields.Boolean(
        string="Permitir stock negativo en consumo MRP",
        default=False,
        tracking=True,
    )
    allow_negative_customer_delivery = fields.Boolean(
        string="Permitir stock negativo en entregas a cliente",
        default=False,
        tracking=True,
    )
    allow_negative_returns = fields.Boolean(
        string="Permitir stock negativo en devoluciones",
        default=False,
        tracking=True,
    )

    allow_consume_cau = fields.Boolean(
        string="Permitir consumir desde CAU",
        default=True,
        tracking=True,
    )
    allow_consume_caf = fields.Boolean(
        string="Permitir consumir desde CAF",
        default=True,
        tracking=True,
    )
    allow_consume_importados = fields.Boolean(
        string="Permitir consumir desde IMPORTADOS",
        default=True,
        tracking=True,
    )

    allow_put_cau = fields.Boolean(
        string="Permitir recibir en CAU",
        default=True,
        tracking=True,
    )
    allow_put_caf = fields.Boolean(
        string="Permitir recibir en CAF",
        default=True,
        tracking=True,
    )
    allow_put_importados = fields.Boolean(
        string="Permitir recibir en IMPORTADOS",
        default=True,
        tracking=True,
    )

    def _get_parameter_locations(self):
        self.ensure_one()
        Location = self.env["stock.location"].sudo().with_context(active_test=False)
        location_names = {
            "cau": "WH/Existencias/CAU",
            "caf": "WH/Existencias/CAF",
            "importados": "WH/Existencias/IMPORTADOS",
        }
        return {
            key: Location.search([("complete_name", "=", complete_name)], limit=1)
            for key, complete_name in location_names.items()
        }

    def _get_available_quantity(self, product, location):
        self.ensure_one()
        if not product or not location:
            return 0.0
        quants = self.env["stock.quant"].sudo().search([
            ("product_id", "=", product.id),
            ("location_id", "=", location.id),
        ])
        return sum(quants.mapped("quantity"))

    def _validate_restricted_consumption_locations(self, move_lines):
        self.ensure_one()
        locations = self._get_parameter_locations()
        rules = (
            ("cau", "allow_consume_cau", _("Consumption from location CAU is not allowed by system parameters")),
            ("caf", "allow_consume_caf", _("Consumption from location CAF is not allowed by system parameters")),
            (
                "importados",
                "allow_consume_importados",
                _("Consumption from location IMPORTADOS is not allowed by system parameters"),
            ),
        )
        for move_line in move_lines:
            for key, field_name, message in rules:
                location = locations.get(key)
                if not getattr(self, field_name) and location and move_line.location_id == location:
                    raise UserError(message)

    def _validate_restricted_destination_locations(self, move_lines):
        self.ensure_one()
        locations = self._get_parameter_locations()
        rules = (
            ("cau", "allow_put_cau", _("Receiving products in location CAU is restricted")),
            ("caf", "allow_put_caf", _("Receiving products in location CAF is restricted")),
            ("importados", "allow_put_importados", _("Receiving products in location IMPORTADOS is restricted")),
        )
        for move_line in move_lines:
            for key, field_name, message in rules:
                location = locations.get(key)
                if not getattr(self, field_name) and location and move_line.location_dest_id == location:
                    raise UserError(message)

    def _validate_negative_stock(self, move_lines, allow_negative_field):
        self.ensure_one()
        if getattr(self, allow_negative_field, False):
            return

        grouped_qty = {}
        for move_line in move_lines.filtered(lambda ml: ml.product_id and ml.location_id):
            qty_done = getattr(move_line, "qty_done", False)
            if qty_done is False:
                qty_done = getattr(move_line, "quantity", 0.0)
            qty_done = qty_done or 0.0
            if qty_done <= 0 or move_line.location_id.usage not in ("internal", "transit"):
                continue
            key = (move_line.product_id.id, move_line.location_id.id)
            grouped_qty[key] = grouped_qty.get(key, 0.0) + qty_done

        Product = self.env["product.product"]
        Location = self.env["stock.location"]
        for (product_id, location_id), qty_done in grouped_qty.items():
            product = Product.browse(product_id)
            location = Location.browse(location_id)
            available_qty = self._get_available_quantity(product, location)
            if qty_done > available_qty:
                raise UserError(
                    _("No se puede validar la transferencia porque provocaría stock negativo para el producto %(product)s en la ubicación %(location)s.") % {
                        "product": product.display_name,
                        "location": location.complete_name or location.display_name,
                    }
                )
