# -*- coding: utf-8 -*-
from odoo import fields, models


class ControlTotalConfirmWizard(models.TransientModel):
    _name = "assign.control.total.confirm.wizard"
    _description = "Confirmacion de asignacion Control Total"

    message = fields.Text(readonly=True)

    def action_continue(self):
        wizard_id = self.env.context.get("active_assign_wizard_id")
        if wizard_id:
            wizard = self.env["assign.control.total.wizard"].browse(wizard_id)
            return wizard.with_context(skip_ct_qty_warning=True).action_assign()
        return {"type": "ir.actions.act_window_close"}

    def action_back(self):
        return {"type": "ir.actions.act_window_close"}
