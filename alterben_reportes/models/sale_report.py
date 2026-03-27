# -*- coding: utf-8 -*-
from odoo import api, fields, models

import re


class SaleReport(models.Model):
    _inherit = "sale.report"

    x_date_inv = fields.Date(string="Fecha Factura", readonly=True)
    qty_on_hand = fields.Float(
        string="Cant. a la mano",
        readonly=True,
        compute="_compute_stock_qty",
    )
    qty_available = fields.Float(
        string="Cant. disponible",
        readonly=True,
        compute="_compute_stock_qty",
    )
    qty_on_hand_detail = fields.Char(
        string="Ubicacion de Stock",
        readonly=True,
        compute="_compute_stock_qty",
    )
    qty_available_detail = fields.Char(
        string="Clientes con Reserva",
        readonly=True,
        compute="_compute_stock_qty",
    )

    @api.depends("product_id", "company_id")
    def _compute_stock_qty(self):
        products = self.mapped("product_id")
        if not products:
            for rec in self:
                rec.qty_on_hand = 0.0
                rec.qty_available = 0.0
                rec.qty_on_hand_detail = False
                rec.qty_available_detail = False
            return

        company_ids = list({c.id for c in self.mapped("company_id") if c})
        quants_domain = [
            ("product_id", "in", products.ids),
            ("location_id.usage", "=", "internal"),
        ]
        if company_ids:
            quants_domain.append(("company_id", "in", company_ids))

        quant_data = self.env["stock.quant"].read_group(
            quants_domain,
            ["product_id", "quantity:sum", "reserved_quantity:sum"],
            ["product_id", "company_id"],
            lazy=False,
        )
        quant_by_product_company = {
            (
                item["product_id"][0],
                item["company_id"][0] if item.get("company_id") else False,
            ): (
                item.get("quantity", 0.0) or 0.0,
                item.get("reserved_quantity", 0.0) or 0.0,
            )
            for item in quant_data
            if item.get("product_id")
        }

        quant_location_data = self.env["stock.quant"].read_group(
            quants_domain,
            ["product_id", "company_id", "location_id", "quantity:sum"],
            ["product_id", "company_id", "location_id"],
            lazy=False,
        )
        on_hand_detail_map = {}
        for item in quant_location_data:
            product = item.get("product_id")
            if not product:
                continue
            company = item.get("company_id")
            location = item.get("location_id")
            key = (product[0], company[0] if company else False)
            location_name = self._short_location_name(location[1] if location else "Sin ubicacion")
            qty = item.get("quantity", 0.0) or 0.0
            on_hand_detail_map.setdefault(key, []).append(f"{location_name}: {qty:.2f}")

        move_domain = [
            ("product_id", "in", products.ids),
            ("company_id", "in", company_ids or [self.env.company.id]),
            ("state", "not in", ["done", "cancel"]),
            ("sale_line_id", "!=", False),
            ("location_id.usage", "=", "internal"),
        ]
        reserved_moves = self.env["stock.move"].search(move_domain)
        move_line_model = self.env["stock.move.line"]
        if "reserved_uom_qty" in move_line_model._fields:
            qty_field = "reserved_uom_qty"
        elif "quantity" in move_line_model._fields:
            qty_field = "quantity"
        else:
            qty_field = "product_uom_qty"

        reserved_detail_map = {}
        for move in reserved_moves:
            if not move.product_id:
                continue
            reserved_qty = sum(move.move_line_ids.mapped(qty_field))
            if reserved_qty <= 0:
                continue
            partner = move.sale_line_id.order_id.partner_id
            partner_name = partner.display_name if partner else "Sin cliente"
            key = (move.product_id.id, move.company_id.id if move.company_id else False)
            partner_qty = reserved_detail_map.setdefault(key, {})
            partner_qty[partner_name] = partner_qty.get(partner_name, 0.0) + reserved_qty

        for rec in self:
            if not rec.product_id:
                rec.qty_on_hand = 0.0
                rec.qty_available = 0.0
                rec.qty_on_hand_detail = False
                rec.qty_available_detail = False
                continue
            key = (rec.product_id.id, rec.company_id.id if rec.company_id else False)
            qty, reserved = quant_by_product_company.get(key, (0.0, 0.0))
            rec.qty_on_hand = qty
            rec.qty_available = qty - reserved
            on_hand_lines = on_hand_detail_map.get(key, [])
            rec.qty_on_hand_detail = " | ".join(on_hand_lines) if on_hand_lines else "Sin stock interno"
            reserved_by_partner = reserved_detail_map.get(key, {})
            if reserved_by_partner:
                lines = [f"{name}: {value:.2f}" for name, value in sorted(reserved_by_partner.items())]
                rec.qty_available_detail = " | ".join(lines)
            else:
                rec.qty_available_detail = "Sin reservas"

    @api.model
    def _short_location_name(self, full_name):
        name = (full_name or "").strip()
        lower_name = name.lower()
        prefixes = ("wh/existencias/", "wh/preproduccion/", "wh/preproducción/")
        for prefix in prefixes:
            if lower_name.startswith(prefix):
                return name[len(prefix):]
        return name

    def _query(self):
        """
        Extiende sale.report sin multiplicar filas del reporte.

        La implementacion anterior hacia JOIN directo contra
        sale_order_line_invoice_rel / account_move_line / account_move,
        lo que puede duplicar cantidades cuando una linea de venta tiene
        varias lineas de factura.

        Aqui se usa una subconsulta agregada por order_line_id para que
        cada linea de venta siga aportando una sola fila logica al reporte.
        """
        query = super()._query()

        if " AS x_date_inv" in query or "x_date_inv" in query:
            return query

        m = re.search(r"\bFROM\s+sale_order_line\s+(\w+)\b", query, flags=re.I)
        sol_alias = m.group(1) if m else "l"

        from_match = re.search(r"\bFROM\b", query, flags=re.I)
        if not from_match:
            return query

        select_part = query[:from_match.start()].rstrip()
        rest = query[from_match.start():]

        # MAX evita problemas de GROUP BY en la vista agregada
        select_part += "\n    , MAX(inv.x_date_inv) AS x_date_inv\n"

        join_sql = f"""
    LEFT JOIN (
        SELECT
            solir.order_line_id,
            MAX(am.invoice_date) AS x_date_inv
        FROM sale_order_line_invoice_rel solir
        JOIN account_move_line aml
            ON aml.id = solir.invoice_line_id
        JOIN account_move am
            ON am.id = aml.move_id
        WHERE am.move_type IN ('out_invoice', 'out_refund')
          AND am.state = 'posted'
        GROUP BY solir.order_line_id
    ) inv ON inv.order_line_id = {sol_alias}.id
"""

        if "sale_order_line_invoice_rel" not in rest and ") inv ON inv.order_line_id" not in rest:
            where_match = re.search(r"\bWHERE\b", rest, flags=re.I)
            if where_match:
                rest = rest[:where_match.start()] + join_sql + rest[where_match.start():]
            else:
                group_match = re.search(r"\bGROUP\s+BY\b", rest, flags=re.I)
                if group_match:
                    rest = rest[:group_match.start()] + join_sql + rest[group_match.start():]
                else:
                    rest = rest + join_sql

        return select_part + rest
