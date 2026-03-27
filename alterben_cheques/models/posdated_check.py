from odoo import models, fields, api, _
from odoo.exceptions import UserError


class AlterbenPosdatedCheck(models.Model):
    _name = 'alterben.posdated.check'
    _description = 'Cheque Posfechado'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_register desc, id desc'

    name = fields.Char(
        string='Referencia',
        readonly=True,
        default=lambda self: _('Nuevo'),
        tracking=True,
    )
    date_register = fields.Date(
        string='Fecha de Registro',
        default=fields.Date.context_today,
        required=True,
        tracking=True,
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Cliente',
        required=True,
        domain=[('customer_rank', '>', 0)],
        tracking=True,
    )
    bank_id = fields.Many2one(
        'res.bank',
        string='Banco',
        required=True,
        tracking=True,
    )
    check_number = fields.Char(
        string='Número de Cheque',
        required=True,
        tracking=True,
    )
    amount = fields.Monetary(
        string='Monto',
        required=True,
        tracking=True,
    )
    currency_id = fields.Many2one(
        'res.currency',
        string='Moneda',
        default=lambda self: self.env.company.currency_id,
        required=True,
    )
    charge_date = fields.Date(
        string='Fecha de Cobro',
        required=True,
        tracking=True,
    )
    deposit_number = fields.Char(
        string='Número de Depósito',
        tracking=True,
    )
    return_reason = fields.Text(
        string='Motivo de Devolución',
        tracking=True,
    )
    state = fields.Selection(
        [
            ('draft', 'Borrador'),
            ('to_deposit', 'Por Depositar'),
            ('deposited', 'Depositado'),
            ('deposit_confirmed', 'Depósito Confirmado'),
            ('returned', 'Devuelto'),
            ('cancelled', 'Cancelado'),
        ],
        string='Estado',
        default='draft',
        required=True,
        tracking=True,
    )

    @api.model
    def create(self, vals):
        if vals.get('name', _('Nuevo')) == _('Nuevo'):
            seq = self.env['ir.sequence'].next_by_code('alterben.posdated.check') or _('Nuevo')
            vals['name'] = seq
        return super().create(vals)

    # --- Validaciones y flujo de estados ---

    def _check_required_fields_for_confirm(self):
        for rec in self:
            missing = []
            if not rec.date_register:
                missing.append(_('Fecha de Registro'))
            if not rec.partner_id:
                missing.append(_('Cliente'))
            if not rec.bank_id:
                missing.append(_('Banco'))
            if not rec.check_number:
                missing.append(_('Número de Cheque'))
            if not rec.amount:
                missing.append(_('Monto'))
            if not rec.charge_date:
                missing.append(_('Fecha de Cobro'))
            if missing:
                raise UserError(
                    _('No puede confirmar el cheque. Faltan datos obligatorios: %s') % ', '.join(missing)
                )

    def action_confirm(self):
        """De Borrador a Por Depositar."""
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_('Solo los cheques en estado Borrador pueden ser confirmados.'))
            rec._check_required_fields_for_confirm()
            rec.state = 'to_deposit'

    def action_set_to_draft(self):
        """Reestablecer a Borrador, limpiando datos de depósito y devolución."""
        for rec in self:
            rec.write({
                'state': 'draft',
                'deposit_number': False,
                'return_reason': False,
            })

    def action_deposit(self):
        """De Por Depositar a Depositado (aún sin número de depósito)."""
        for rec in self:
            if rec.state != 'to_deposit':
                raise UserError(_('Solo los cheques en estado Por Depositar pueden ser pasados a Depositado.'))
            rec.state = 'deposited'

    def action_confirm_deposit(self):
        """De Depositado a Depósito Confirmado, exige número de depósito."""
        for rec in self:
            if rec.state != 'deposited':
                raise UserError(_('Solo los cheques en estado Depositado pueden confirmar el depósito.'))
            if not rec.deposit_number:
                raise UserError(_('Debe ingresar el Número de Depósito antes de confirmar el depósito.'))
            rec.state = 'deposit_confirmed'

    def action_return(self):
        """De Depositado o Depósito Confirmado a Devuelto, exige motivo."""
        for rec in self:
            if rec.state not in ('deposited', 'deposit_confirmed'):
                raise UserError(_('Solo los cheques depositados pueden ser devueltos.'))
            if not rec.return_reason:
                raise UserError(_('Debe ingresar el Motivo de la Devolución antes de devolver el cheque.'))
            rec.state = 'returned'

    def action_cancel(self):
        """Cancelar solo en Borrador."""
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_('Solo los cheques en estado Borrador pueden ser cancelados.'))
            rec.state = 'cancelled'

    # --- Acciones masivas ---

    def action_multi_deposit(self):
        """Acción masiva desde la vista de lista para pasar a Depositado."""
        if not self:
            return
        invalid = self.filtered(lambda r: r.state != 'to_deposit')
        if invalid:
            raise UserError(_('Solo se pueden depositar cheques en estado Por Depositar.'))
        self.write({'state': 'deposited'})