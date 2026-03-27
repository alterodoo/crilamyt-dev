from odoo import api, fields, models
from dateutil.relativedelta import relativedelta


class L10nEcInvoicesReport(models.Model):
    _inherit = "l10n_ec.invoices.report"

    # Retencion informativa (campo Studio en account.move)
    ret_informativa = fields.Boolean(
        string="Ret. Informativa",
        readonly=True,
        related="invoice_id.x_studio_retencin_informativa",
    )

    amount_wh_ir_327 = fields.Monetary(
        string="RET_327",
        readonly=True,
        compute="_compute_amount_wh_ir_327",
    )

    amount_wh_ir_328 = fields.Monetary(
        string="RET_328",
        readonly=True,
        compute="_compute_amount_wh_ir_328",
    )

    @api.depends(
        "invoice_id.line_ids.tax_line_id",
        "invoice_id.line_ids.tax_ids",
        "invoice_id.line_ids.tax_repartition_line_id",
        "invoice_id.line_ids.balance",
        "invoice_id.move_type",
    )
    def _compute_amount_wh_ir_327(self):
        """Compute the withholding tax amount for form 327 from the related invoice."""
        for record in self:
            if record.invoice_id:
                wh_lines = record.invoice_id.line_ids.filtered(
                    lambda l: self._line_matches_ats_code(l, "327")
                )
                record.amount_wh_ir_327 = abs(sum(wh_lines.mapped("balance")))
            else:
                record.amount_wh_ir_327 = 0.0

    @api.depends(
        "invoice_id.line_ids.tax_line_id",
        "invoice_id.line_ids.tax_ids",
        "invoice_id.line_ids.tax_repartition_line_id",
        "invoice_id.line_ids.balance",
        "invoice_id.move_type",
    )
    def _compute_amount_wh_ir_328(self):
        """Compute the withholding tax amount for form 328 from the related invoice."""
        for record in self:
            if record.invoice_id:
                wh_lines = record.invoice_id.line_ids.filtered(
                    lambda l: self._line_matches_ats_code(l, "328")
                )
                record.amount_wh_ir_328 = abs(sum(wh_lines.mapped("balance")))
            else:
                record.amount_wh_ir_328 = 0.0

    @api.model
    def _is_tax_code_ats(self, tax, target_code):
        """Robust matcher for Ecuador ATS withholding code (327/328)."""
        values = [
            getattr(tax, "l10n_ec_code_ats", False),
            getattr(tax, "l10n_ec_ats_code", False),
            getattr(tax, "tax_code_ats", False),
            tax.name or "",
            tax.description or "",
        ]
        normalized = " ".join([str(v) for v in values if v]).lower()
        return target_code in normalized

    @api.model
    def _line_matches_ats_code(self, line, target_code):
        taxes_to_check = line.tax_line_id
        taxes_to_check |= line.tax_ids

        repartition = line.tax_repartition_line_id
        if repartition:
            if "tax_id" in repartition._fields:
                taxes_to_check |= repartition.tax_id
            if "invoice_tax_id" in repartition._fields:
                taxes_to_check |= repartition.invoice_tax_id
            if "refund_tax_id" in repartition._fields:
                taxes_to_check |= repartition.refund_tax_id

        return any(self._is_tax_code_ats(tax, target_code) for tax in taxes_to_check)

    # Pagos (campos calculados, no requieren columnas SQL)
    amount_paid = fields.Monetary(string="Importe Pagado", readonly=True, compute="_compute_payment_amounts")
    amount_pending = fields.Monetary(string="Importe Pendiente", readonly=True, compute="_compute_payment_amounts")
    amount_paid_cutoff = fields.Monetary(string="Importe Pagado al Corte", readonly=True, compute="_compute_payment_amounts")
    amount_debt_cutoff = fields.Monetary(string="Deuda al Corte", readonly=True, compute="_compute_payment_amounts")

    # Posicion fiscal (relacionado con el partner)
    fiscal_position = fields.Many2one(
        comodel_name="account.fiscal.position",
        string="Posicion Fiscal",
        readonly=True,
        related="invoice_id.partner_id.property_account_position_id",
    )

    @api.depends("invoice_id", "invoice_id.amount_total", "invoice_id.amount_residual")
    def _compute_payment_amounts(self):
        cutoff_date = self._get_report_cutoff_date()

        for rec in self:
            inv = rec.invoice_id
            if not inv:
                rec.amount_paid = 0.0
                rec.amount_pending = 0.0
                rec.amount_paid_cutoff = 0.0
                rec.amount_debt_cutoff = 0.0
                continue

            total = abs(inv.amount_total or 0.0)
            residual = abs(inv.amount_residual or 0.0)
            paid = max(total - residual, 0.0)

            rec.amount_paid = paid
            rec.amount_pending = residual
            rec.amount_paid_cutoff = rec._get_amount_paid_until_cutoff(inv, cutoff_date)
            rec.amount_debt_cutoff = max(total - rec.amount_paid_cutoff, 0.0)

    @api.model
    def _get_report_cutoff_date(self):
        ctx = self.env.context or {}

        # Filtros usados tipicamente por reportes/listas en Odoo.
        for key in ("date_to", "to_date", "default_date_to", "report_date_to"):
            if ctx.get(key):
                return fields.Date.to_date(ctx[key])

        # Algunos reportes pasan un diccionario 'date' en contexto.
        date_ctx = ctx.get("date")
        if isinstance(date_ctx, dict):
            for key in ("date_to", "to"):
                if date_ctx.get(key):
                    return fields.Date.to_date(date_ctx[key])

        # Fallback: maximo invoice_date visible en el lote actual.
        invoice_dates = [d for d in self.mapped("invoice_id.invoice_date") if d]
        if invoice_dates:
            return max(invoice_dates)

        # Ultimo recurso: ultimo dia del mes anterior.
        today = fields.Date.context_today(self)
        return today.replace(day=1) - relativedelta(days=1)

    def _get_amount_paid_until_cutoff(self, invoice, cutoff_date):
        self.ensure_one()

        receivable_payable_lines = invoice.line_ids.filtered(
            lambda line: line.account_id.account_type in ("asset_receivable", "liability_payable")
        )
        if not receivable_payable_lines:
            return 0.0

        partials = receivable_payable_lines.matched_debit_ids | receivable_payable_lines.matched_credit_ids
        partials = partials.filtered(lambda p: p.max_date and p.max_date <= cutoff_date)

        paid_until_cutoff = abs(sum(partials.mapped("amount")))
        total = abs(invoice.amount_total or 0.0)
        return min(paid_until_cutoff, total)
