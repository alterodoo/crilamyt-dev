from odoo import api, fields, models

class AlterbenBalance101SuperLine(models.Model):
    _name = 'alterben.balance101super.line'
    _description = 'Balance 101 y Supercias'
    _order = 'code'

    company_id = fields.Many2one('res.company', required=True, index=True)
    date_from = fields.Date(string='Desde', required=True, index=True)
    date_to = fields.Date(string='Hasta', required=True, index=True)

    account_id = fields.Many2one('account.account', string='Cuenta contable', required=True, index=True)
    code = fields.Char(string='Código', required=True, index=True)
    name = fields.Char(string='Descripción', required=True)

    currency_id = fields.Many2one(
        'res.currency',
        string='Moneda',
        required=True,
        default=lambda self: self.env.company.currency_id.id,
    )

    saldo_anterior = fields.Monetary(string='Saldo anterior', currency_field='currency_id')
    debitos = fields.Monetary(string='Débitos', currency_field='currency_id')
    creditos = fields.Monetary(string='Créditos', currency_field='currency_id')
    saldo_final = fields.Monetary(string='Saldo final', currency_field='currency_id')

    sri_tag = fields.Char(string='SRI')
    super_tag = fields.Char(string='SUPER')
    nivel = fields.Integer(string='Nivel')
    f101_tag = fields.Char(string='F101')
    supercias_tag = fields.Char(string='SUPERCIAS')

    tag_ids = fields.Many2many(
        'account.account.tag',
        string='Etiquetas',
        help='Etiquetas del plan de cuentas (account.account.tag)',
    )

    tag_list = fields.Char(string='Etiquetas (texto)', readonly=True)

    @api.model
    def create_from_parameters(self, date_from, date_to, company):
        """Recalcula el balance para el rango de fechas y compañía indicados."""
        domain = [
            ('company_id', '=', company.id),
            ('date_from', '=', date_from),
            ('date_to', '=', date_to),
        ]
        self.search(domain).unlink()

        Account = self.env['account.account']
        accounts = Account.search([('company_id', '=', company.id), ('deprecated', '=', False)])

        for account in accounts:
            query_prev = '''
SELECT COALESCE(SUM(debit), 0) AS debit, COALESCE(SUM(credit), 0) AS credit
FROM account_move_line l
JOIN account_move m ON l.move_id = m.id
WHERE l.account_id = %s
  AND m.company_id = %s
  AND m.state = 'posted'
  AND l.date < %s
'''
            self.env.cr.execute(query_prev, (account.id, company.id, date_from))
            prev_debit, prev_credit = self.env.cr.fetchone()

            query_period = '''
SELECT COALESCE(SUM(debit), 0) AS debit, COALESCE(SUM(credit), 0) AS credit
FROM account_move_line l
JOIN account_move m ON l.move_id = m.id
WHERE l.account_id = %s
  AND m.company_id = %s
  AND m.state = 'posted'
  AND l.date >= %s
  AND l.date <= %s
'''
            self.env.cr.execute(query_period, (account.id, company.id, date_from, date_to))
            period_debit, period_credit = self.env.cr.fetchone()

            saldo_anterior = prev_debit - prev_credit
            debitos = period_debit
            creditos = period_credit
            saldo_final = saldo_anterior + debitos - creditos

            if not (saldo_anterior or debitos or creditos or saldo_final):
                continue

            tags = account.tag_ids
            tag_names = tags.mapped('name')
            tag_list = ', '.join(tag_names) if tag_names else False

            def _find_tag(prefix):
                prefix_up = prefix.upper()
                for t in tag_names:
                    if t and t.upper().startswith(prefix_up):
                        return t
                return False

            f101_tag = _find_tag('F101')
            supercias_tag = _find_tag('SUPERCIAS')
            sri_tag = _find_tag('SRI')
            super_tag = False
            for t in tag_names:
                if not t:
                    continue
                up = t.upper()
                if up.startswith('SUPERCIAS'):
                    continue
                if up.startswith('SUPER'):
                    super_tag = t
                    break

            nivel = 0
            if account.code:
                parts = [p for p in account.code.split('.') if p]
                nivel = len(parts)

            self.create({
                'company_id': company.id,
                'date_from': date_from,
                'date_to': date_to,
                'account_id': account.id,
                'code': account.code,
                'name': account.name,
                'currency_id': company.currency_id.id,
                'saldo_anterior': saldo_anterior,
                'debitos': debitos,
                'creditos': creditos,
                'saldo_final': saldo_final,
                'sri_tag': sri_tag,
                'super_tag': super_tag,
                'nivel': nivel,
                'f101_tag': f101_tag,
                'supercias_tag': supercias_tag,
                'tag_ids': [(6, 0, tags.ids)],
                'tag_list': tag_list,
            })
        return True
