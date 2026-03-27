from odoo import _, models
from odoo.exceptions import UserError


INTERNAL_CONSUMPTION_OPERATION_NAME = 'CRILAMYT: CONSUMO INTERNO'


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def _normalized_operation_name(self):
        self.ensure_one()
        return (self.picking_type_id.name or '').strip().casefold()

    def _is_internal_consumption_operation(self):
        self.ensure_one()
        return self._normalized_operation_name() == INTERNAL_CONSUMPTION_OPERATION_NAME.casefold()

    def _has_cost_center(self):
        self.ensure_one()
        if 'x_studio_centro_de_costo' not in self._fields:
            return False
        return bool(self.x_studio_centro_de_costo)

    def _is_internal_to_inventory_flow(self):
        self.ensure_one()
        return (
            self.location_id.usage == 'internal'
            and self.location_dest_id.usage == 'inventory'
        )

    def _check_cost_center_required_for_internal_consumption(self):
        for picking in self:
            if picking._is_internal_consumption_operation() and not picking._has_cost_center():
                raise UserError(
                    _('Debe seleccionar un Centro de Costo para validar un CONSUMO INTERNO.')
                )

    def button_validate(self):
        self._check_cost_center_required_for_internal_consumption()
        return super().button_validate()
