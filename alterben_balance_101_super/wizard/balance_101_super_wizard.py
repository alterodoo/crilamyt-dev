from odoo import api, fields, models

class AlterbenBalance101SuperWizard(models.TransientModel):
    _name = 'alterben.balance101super.wizard'
    _description = 'Asistente Balance 101 y Supercias'

    company_id = fields.Many2one(
        'res.company',
        string='Compañía',
        required=True,
        default=lambda self: self.env.company,
    )
    date_from = fields.Date(string='Desde', required=True)
    date_to = fields.Date(string='Hasta', required=True)

    def action_generate_report(self):
        self.ensure_one()
        line_model = self.env['alterben.balance101super.line']
        line_model.create_from_parameters(self.date_from, self.date_to, self.company_id)

        action = self.env.ref('alterben_balance_101_super.action_alterben_balance_101_super_lines').read()[0]
        action['domain'] = [
            ('company_id', '=', self.company_id.id),
            ('date_from', '=', self.date_from),
            ('date_to', '=', self.date_to),
        ]
        return action
