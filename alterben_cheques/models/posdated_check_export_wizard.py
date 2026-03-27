from odoo import models, fields, api, _
from odoo.exceptions import UserError

import base64
import io


class AlterbenPosdatedCheckExportWizard(models.TransientModel):
    _name = 'alterben.posdated.check.export.wizard'
    _description = 'Exportar cheques posfechados a Excel para importación de pagos'

    check_ids = fields.Many2many(
        'alterben.posdated.check',
        'ab_pdc_export_rel',
        'wizard_id',
        'check_id',
        string='Cheques',
        required=True,
        default=lambda self: self._default_check_ids(),
    )

    mark_as_deposited = fields.Boolean(
        string='Marcar como Depositado',
        default=True,
        help='Si está activado, los cheques seleccionados pasarán a estado "Depositado" al generar el Excel.',
    )

    journal_id = fields.Many2one(
        'account.journal',
        string='Diario de Banco',
        required=True,
        domain=[('type', 'in', ('bank', 'cash'))],
        help='Este diario se usará en el archivo para importar los pagos (account.payment).',
    )

    payment_method_line_id = fields.Many2one(
        'account.payment.method.line',
        string='Método de Pago (Línea)',
        required=True,
        domain="[('journal_id', '=', journal_id), ('payment_type', '=', 'inbound')]",
        help='Debe ser una línea de método de pago Inbound del diario seleccionado (por ejemplo: Depósitos, Cheques, etc.).',
    )

    payment_date = fields.Date(
        string='Fecha de Pago',
        default=fields.Date.context_today,
        required=True,
        help='Fecha que se colocará en el archivo de importación.',
    )

    file_data = fields.Binary(string='Archivo', readonly=True)
    file_name = fields.Char(string='Nombre de archivo', readonly=True)

    def _default_check_ids(self):
        active_ids = self.env.context.get('active_ids') or []
        return [(6, 0, active_ids)]

    def _validate_checks(self):
        self.ensure_one()
        if not self.check_ids:
            raise UserError(_('No hay cheques seleccionados.'))
        invalid = self.check_ids.filtered(lambda r: r.state != 'to_deposit')
        if invalid:
            raise UserError(
                _('Solo se pueden generar pagos desde cheques en estado "Por Depositar".\n\nCheque(s) inválidos: %s')
                % ', '.join(invalid.mapped('name'))
            )

    def action_generate_excel(self):
        """Genera un XLSX con columnas listas para importación a account.payment."""
        self.ensure_one()
        self._validate_checks()

        # Marcar como depositado si corresponde
        if self.mark_as_deposited:
            self.check_ids.write({'state': 'deposited'})

        # Construcción del XLSX
        try:
            import xlsxwriter  # Odoo suele traerlo; si no, fallará con ImportError
        except Exception as e:
            raise UserError(_("No se pudo generar el XLSX (xlsxwriter no disponible).\nDetalle: %s") % str(e))

        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        worksheet = workbook.add_worksheet('Pagos')

        # Usamos nombres técnicos de campos para que la importación sea más robusta.
        headers = [
            'partner_id',
            'amount',
            'date',
            'journal_id',
            'payment_method_line_id',
            'payment_type',
            'partner_type',
        ]
        for col, h in enumerate(headers):
            worksheet.write(0, col, h)

        row = 1
        for chk in self.check_ids.sorted(key=lambda r: (r.partner_id.id, r.charge_date or r.date_register, r.id)):
            worksheet.write(row, 0, chk.partner_id.display_name or '')
            worksheet.write_number(row, 1, float(chk.amount or 0.0))
            worksheet.write(row, 2, fields.Date.to_string(self.payment_date))
            worksheet.write(row, 3, self.journal_id.display_name or '')
            worksheet.write(row, 4, self.payment_method_line_id.display_name or '')
            worksheet.write(row, 5, 'inbound')
            worksheet.write(row, 6, 'customer')
            row += 1

        workbook.close()
        output.seek(0)
        data = output.read()

        filename = 'import_pagos_cheques_posfechados.xlsx'
        self.write({
            'file_data': base64.b64encode(data),
            'file_name': filename,
        })

        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content?model=%s&id=%s&field=file_data&download=true&filename=%s' % (
                self._name,
                self.id,
                filename,
            ),
            'target': 'self',
        }
