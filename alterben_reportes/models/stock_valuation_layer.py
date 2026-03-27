from odoo import models
from odoo.tools.float_utils import float_is_zero


class StockValuationLayer(models.Model):
    _inherit = 'stock.valuation.layer'

    def _should_skip_native_account_entry(self):
        self.ensure_one()

        move = self.stock_move_id
        picking = move.picking_id
        if not picking:
            return False

        # Regla de negocio: solo consumo interno con centro de costo.
        # Se valida por nombre de operación y también por patrón logístico
        # interno -> inventario para tolerar variantes de etiqueta.
        if not (
            picking._is_internal_consumption_operation()
            or picking._is_internal_to_inventory_flow()
        ):
            return False

        if not picking._has_cost_center():
            return False

        if self.product_id.valuation != 'real_time':
            return False

        currency = self.currency_id or self.company_id.currency_id
        if float_is_zero(self.value, precision_rounding=currency.rounding):
            return False

        return True

    def _validate_accounting_entries(self):
        svl_to_account = self.filtered(lambda svl: not svl._should_skip_native_account_entry())
        if not svl_to_account:
            return self.env['account.move']
        return super(StockValuationLayer, svl_to_account)._validate_accounting_entries()
