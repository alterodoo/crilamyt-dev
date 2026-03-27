# -*- coding: utf-8 -*-
from lxml import etree

from odoo import api, fields, models


class L10nLatamCustomerInvoiceReport(models.Model):
    _inherit = "l10n_latam.customer.invoice.report"

    unit_cost = fields.Monetary(
        string="Costo unitario",
        readonly=True,
        compute="_compute_unit_cost",
    )
    cost_so = fields.Monetary(
        string="Costo en SO",
        readonly=True,
        compute="_compute_unit_cost",
    )
    total_cost = fields.Monetary(
        string="Costo total",
        readonly=True,
        compute="_compute_cost_metrics",
    )
    margin_percent = fields.Float(
        string="Margen %",
        readonly=True,
        compute="_compute_cost_metrics",
    )

    def _compute_unit_cost(self):
        for rec in self:
            line = self._get_source_move_line(rec)
            if line:
                product = line.product_id if "product_id" in line._fields else False
                sale_lines = line.sale_line_ids if "sale_line_ids" in line._fields else self.env["sale.order.line"]
                if not sale_lines:
                    sale_lines = self._find_sale_lines_from_invoice_context(rec, line)
            else:
                product = rec.product_id if "product_id" in rec._fields else False
                sale_lines = self._find_sale_lines_from_report_context(rec)

            so_cost = self._get_so_cost_value(sale_lines)
            svl_cost = self._get_svl_cost_value(product, sale_lines)

            rec.cost_so = so_cost
            rec.unit_cost = svl_cost or so_cost or (product.standard_price if product else 0.0)

    @api.depends("unit_cost", "price_subtotal", "invoice_id", "product_id")
    def _compute_cost_metrics(self):
        for rec in self:
            line = self._get_source_move_line(rec)
            if line:
                qty = abs(line.quantity if "quantity" in line._fields else 0.0)
                subtotal = abs(line.price_subtotal if "price_subtotal" in line._fields else 0.0)
            else:
                sale_lines = self._find_sale_lines_from_report_context(rec)
                qty = self._get_report_qty(rec, sale_lines)
                subtotal = abs(rec.price_subtotal if "price_subtotal" in rec._fields else 0.0)

            total_cost = (rec.unit_cost or 0.0) * qty
            margin_total = subtotal - total_cost
            margin_percent = (margin_total / subtotal * 100.0) if subtotal else 0.0

            rec.total_cost = total_cost
            rec.margin_percent = margin_percent

    def _find_sale_lines_from_invoice_context(self, rec, line):
        sale_line_model = self.env["sale.order.line"]

        invoice = False
        if "move_id" in line._fields:
            invoice = line.move_id
        elif "invoice_id" in rec._fields:
            invoice = rec.invoice_id

        if not invoice:
            return sale_line_model

        product = line.product_id if "product_id" in line._fields else False
        origins = [o.strip() for o in (invoice.invoice_origin or "").split(",") if o.strip()]
        if not origins:
            return sale_line_model

        domain = [("order_id.name", "in", origins)]
        if product:
            domain.append(("product_id", "=", product.id))
        return sale_line_model.search(domain, limit=10)

    def _find_sale_lines_from_report_context(self, rec):
        sale_line_model = self.env["sale.order.line"]
        invoice = rec.invoice_id if "invoice_id" in rec._fields else False
        if not invoice:
            return sale_line_model

        origins = [o.strip() for o in (invoice.invoice_origin or "").split(",") if o.strip()]
        if not origins:
            return sale_line_model

        domain = [("order_id.name", "in", origins)]
        if "product_id" in rec._fields and rec.product_id:
            domain.append(("product_id", "=", rec.product_id.id))
        return sale_line_model.search(domain, limit=10)

    def _get_report_qty(self, rec, sale_lines):
        qty_fields = ("quantity", "qty_invoiced", "product_uom_qty", "qty_delivered", "qty")
        for field_name in qty_fields:
            if field_name in rec._fields:
                qty = abs(rec[field_name] or 0.0)
                if qty:
                    return qty

        if "price_unit" in rec._fields:
            price_unit = abs(rec.price_unit or 0.0)
            subtotal = abs(rec.price_subtotal or 0.0) if "price_subtotal" in rec._fields else 0.0
            if price_unit:
                qty = subtotal / price_unit
                if qty:
                    return qty

        if sale_lines:
            qty = sum(abs(sl.qty_invoiced or 0.0) for sl in sale_lines if "qty_invoiced" in sl._fields)
            if qty:
                return qty

        return 0.0

    def _get_so_cost_value(self, sale_lines):
        costs = [sl.purchase_price for sl in sale_lines if sl.purchase_price]
        if costs:
            return sum(costs) / len(costs)
        return 0.0

    def _get_svl_cost_value(self, product, sale_lines):
        moves = sale_lines.mapped("move_ids").filtered(
            lambda m: m.state == "done" and (not product or m.product_id == product)
        )
        svls = moves.mapped("stock_valuation_layer_ids")
        svl_qty = sum(abs(v.quantity or 0.0) for v in svls)
        svl_val = sum(abs(v.value or 0.0) for v in svls)
        if svl_qty:
            return svl_val / svl_qty
        return 0.0

    def _get_source_move_line(self, rec):
        if "invoice_line_id" in rec._fields and rec.invoice_line_id:
            return rec.invoice_line_id
        if "move_line_id" in rec._fields and rec.move_line_id:
            return rec.move_line_id

        invoice = False
        for field_name in ("invoice_id", "move_id", "account_move_id"):
            if field_name in rec._fields and rec[field_name]:
                invoice = rec[field_name]
                break
        if not invoice:
            return False

        product = rec.product_id if "product_id" in rec._fields else False
        domain = [("move_id", "=", invoice.id), ("display_type", "=", False)]
        if product:
            domain.append(("product_id", "=", product.id))

        aml_candidates = self.env["account.move.line"].search(domain)
        if not aml_candidates:
            return False

        target_subtotal = abs(rec.price_subtotal or 0.0) if "price_subtotal" in rec._fields else 0.0
        if not target_subtotal:
            return aml_candidates[0]

        return min(
            aml_candidates,
            key=lambda aml: abs(abs(getattr(aml, "price_subtotal", 0.0)) - target_subtotal),
        )

    @api.model
    def _get_view(self, view_id=None, view_type="form", **options):
        arch, view = super()._get_view(view_id=view_id, view_type=view_type, **options)
        if view_type != "tree":
            return arch, view

        # Compatibilidad: elimina columnas antiguas que pudieron quedar en vistas heredadas.
        for legacy in arch.xpath("//field[@name='margin_total']"):
            parent = legacy.getparent()
            if parent is not None:
                parent.remove(legacy)

        if arch.xpath("//field[@name='cost_so']"):
            return arch, view

        anchor = arch.xpath("//field[@name='unit_cost']")
        node = etree.Element("field", name="cost_so", optional="show")
        if anchor:
            anchor[0].addnext(node)
        else:
            tree_nodes = arch.xpath("//tree")
            if tree_nodes:
                tree_nodes[0].append(node)

        return arch, view
