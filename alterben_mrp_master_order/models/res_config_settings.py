from odoo import api, fields, models


BLOCKED_SOURCE_LOCATIONS_PARAM = "alterben_mrp_master_order.blocked_source_location_ids"
BLOCKED_SOURCE_LOCATION_DEFAULTS = [
    "WH/Existencias/CAU",
    "WH/Existencias/CAF",
]


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    ab_avoid_negative_invoice_lines_from_returns = fields.Boolean(
        string="Evitar negativos en facturas por devoluciones",
        config_parameter="alterben_mrp_master_order.ab_avoid_negative_invoice_lines_from_returns",
    )
    blocked_source_location_ids = fields.Many2many(
        "stock.location",
        string="Bloquear salida de productos desde estas bodegas",
        help="Impide validar pickings cuando la ubicación origen sea una bodega padre configurada aquí. Los movimientos deben salir desde ubicaciones hijas/perchas.",
    )

    @api.model
    def _get_default_blocked_source_locations(self):
        return self.env["stock.location"].sudo().search([
            ("complete_name", "in", BLOCKED_SOURCE_LOCATION_DEFAULTS),
        ])

    @api.model
    def get_values(self):
        res = super().get_values()
        params = self.env["ir.config_parameter"].sudo()
        raw_value = params.get_param(BLOCKED_SOURCE_LOCATIONS_PARAM, default="__missing__")
        if raw_value == "__missing__":
            locations = self._get_default_blocked_source_locations()
        else:
            ids = [int(x) for x in (raw_value or "").split(",") if x.strip().isdigit()]
            locations = self.env["stock.location"].sudo().browse(ids).exists()
        res.update(
            blocked_source_location_ids=[(6, 0, locations.ids)],
        )
        return res

    def set_values(self):
        super().set_values()
        params = self.env["ir.config_parameter"].sudo()
        params.set_param(
            BLOCKED_SOURCE_LOCATIONS_PARAM,
            ",".join(str(location_id) for location_id in self.blocked_source_location_ids.ids),
        )

