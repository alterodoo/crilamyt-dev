from odoo import models


class ReportPickingBatchCustom(models.AbstractModel):
    _name = "report.alterben_mrp_master_order.report_picking_batch_custom"
    _description = "Reporte Batch Picking Personalizado"

    def _get_report_location(self, move, picking):
        move_lines = getattr(move, "move_line_ids", False)
        if move_lines:
            candidate_lines = move_lines.filtered(lambda ml: getattr(ml, "location_id", False))
            if candidate_lines:
                return candidate_lines.sorted(lambda ml: (ml.id, ml.location_id.complete_name or ml.location_id.name or ""))[-1].location_id
        return move.location_id or picking.location_id

    def _get_move_qty_done(self, move):
        if "quantity_done" in move._fields:
            return float(move.quantity_done or 0.0)
        if "qty_done" in move._fields:
            return float(move.qty_done or 0.0)
        if getattr(move, "move_line_ids", False):
            qty_field = "quantity" if "quantity" in move.move_line_ids._fields else "qty_done"
            return float(sum(move.move_line_ids.mapped(qty_field)) or 0.0)
        return 0.0

    def _format_measure_value(self, value):
        value = float(value or 0.0)
        if not value:
            return ""
        if value.is_integer():
            return str(int(value))
        return f"{value:.2f}".rstrip("0").rstrip(".")

    def _format_generic_value(self, field, value, record):
        if value is False or value is None or value == "":
            return ""
        if field.type == "boolean":
            return "Si" if value else "No"
        if field.type == "selection":
            selection = dict(field.selection(record.env) if callable(field.selection) else field.selection or [])
            return selection.get(value, value)
        if field.type == "many2one":
            return value.display_name or ""
        if field.type in ("float", "monetary", "integer"):
            try:
                return self._format_measure_value(value)
            except Exception:
                return str(value)
        return str(value)

    def _normalize_code(self, code):
        return (code or "").strip().upper()

    def _extract_code_suffix(self, code):
        normalized = self._normalize_code(code)
        if not normalized:
            return ""
        parts = [part.strip() for part in normalized.split("-") if part.strip()]
        return parts[-1] if parts else normalized

    def _find_recipe_for_move(self, move, recipe_by_product, recipe_by_code, recipe_by_suffix):
        product = move.product_id
        if not product:
            return self.env["receta.pvb"]

        recipe = recipe_by_product.get(product.id)
        if recipe:
            return recipe

        code = self._normalize_code(product.default_code)
        if code and code in recipe_by_code:
            return recipe_by_code[code]

        suffix = self._extract_code_suffix(code)
        if suffix and suffix in recipe_by_suffix:
            return recipe_by_suffix[suffix]

        return self.env["receta.pvb"]

    def _get_immediate_delivery_display(self, picking, move):
        immediate_display = ""
        if hasattr(picking, "_ab_format_immediate_delivery_value"):
            immediate_display = picking._ab_format_immediate_delivery_value(picking)
        if immediate_display:
            return immediate_display

        sale_line = move.sale_line_id
        if sale_line and "x_studio_entrega_inmediata" in sale_line._fields:
            field = sale_line._fields["x_studio_entrega_inmediata"]
            value = sale_line["x_studio_entrega_inmediata"]
            immediate_display = self._format_generic_value(field, value, sale_line)
            if immediate_display:
                return immediate_display

        preferred_names = [
            "x_studio_entrega_inmediata",
            "entrega_inmediata",
            "x_entrega_inmediata",
            "immediate_delivery",
            "x_immediate_delivery",
            "x_studio_immediate_delivery",
            "immediate_transfer",
        ]

        def _read_delivery_value(record):
            if not record:
                return ""
            field_name = False
            for candidate in preferred_names:
                if candidate in record._fields:
                    field_name = candidate
                    break
            if not field_name:
                for candidate, field in record._fields.items():
                    haystack = f"{candidate} {field.string or ''}".lower()
                    if "entrega inmediata" in haystack or ("entrega" in haystack and "inmediat" in haystack):
                        field_name = candidate
                        break
            if not field_name:
                return ""
            field = record._fields[field_name]
            value = record[field_name]
            return self._format_generic_value(field, value, record)

        immediate_display = _read_delivery_value(sale_line)
        if immediate_display:
            return immediate_display

        order = sale_line.order_id if sale_line else False
        return _read_delivery_value(order)

    def _get_product_measures(self, move, recipe):
        alto = getattr(recipe, "alto", 0.0) if recipe else 0.0
        ancho = getattr(recipe, "ancho", 0.0) if recipe else 0.0
        if not alto and move.sale_line_id and "x_studio_largo" in move.sale_line_id._fields:
            alto = float(move.sale_line_id.x_studio_largo or 0.0)
        if not ancho and move.sale_line_id and "x_studio_ancho" in move.sale_line_id._fields:
            ancho = float(move.sale_line_id.x_studio_ancho or 0.0)
        if not alto and not ancho:
            return ""
        return f"{self._format_measure_value(alto)} x {self._format_measure_value(ancho)}"

    def _get_batch_lines(self, batch):
        lines = []
        recipe_model = self.env["receta.pvb"].sudo()
        products = batch.picking_ids.move_ids_without_package.mapped("product_id").filtered(lambda p: p)
        product_ids = products.ids
        codes = [self._normalize_code(product.default_code) for product in products if product.default_code]
        suffixes = list({self._extract_code_suffix(code) for code in codes if self._extract_code_suffix(code)})
        domain = []
        if product_ids:
            domain.append(("product_id", "in", product_ids))
        if codes:
            if domain:
                domain = ["|"] + domain + [("product_default_code", "in", codes)]
            else:
                domain = [("product_default_code", "in", codes)]
        recipes = recipe_model.search(domain) if domain else recipe_model.browse()
        if suffixes:
            extra_recipes = recipe_model.browse()
            for suffix in suffixes:
                extra_recipes |= recipe_model.search([("product_default_code", "=ilike", f"%{suffix}")])
            recipes |= extra_recipes
        recipe_by_product = {recipe.product_id.id: recipe for recipe in recipes if recipe.product_id}
        recipe_by_code = {
            self._normalize_code(recipe.product_default_code): recipe
            for recipe in recipes
            if recipe.product_default_code
        }
        recipe_by_suffix = {
            self._extract_code_suffix(recipe.product_default_code): recipe
            for recipe in recipes
            if recipe.product_default_code
        }
        for picking in batch.picking_ids.sorted(lambda p: (p.scheduled_date or p.create_date or False, p.name or "")):
            moves = picking.move_ids_without_package.filtered(
                lambda m: m.state != "cancel"
                and m.product_id
                and (m.product_uom_qty or self._get_move_qty_done(m))
            )
            for move in moves.sorted(lambda m: ((m.location_id.complete_name if m.location_id else ""), m.product_id.display_name or "")):
                location = self._get_report_location(move, picking)
                product = move.product_id
                recipe = self._find_recipe_for_move(move, recipe_by_product, recipe_by_code, recipe_by_suffix)
                lines.append({
                    "picking": picking,
                    "move": move,
                    "partner_name": picking.partner_id.display_name if picking.partner_id else "",
                    "origin": picking.origin or "",
                    "immediate_delivery": self._get_immediate_delivery_display(picking, move),
                    "location_name": (location.name or location.display_name or location.complete_name or "") if location else "",
                    "location_barcode": getattr(location, "barcode", "") if location else "",
                    "product_name": product.display_name or "",
                    "product_barcode": product.barcode or "",
                    "product_measures": self._get_product_measures(move, recipe),
                    "qty_ordered": float(move.product_uom_qty or 0.0),
                    "qty_cancelled": float(getattr(move, "ab_cancelled_delivery_qty", 0.0) or 0.0),
                    "qty_done": self._get_move_qty_done(move),
                    "uom_name": move.product_uom.name if getattr(move, "product_uom", False) else "",
                })
        return lines

    def _get_batch_customer_names(self, batch):
        partner_names = []
        for picking in batch.picking_ids:
            if picking.partner_id and picking.partner_id.display_name:
                city = (picking.partner_id.city or "").strip()
                label = picking.partner_id.display_name
                if city:
                    label = f"{label} - {city}"
                partner_names.append(label)
        # Preserve order while removing duplicates.
        return list(dict.fromkeys(partner_names))

    def _get_batch_destination_label(self, batch):
        customer_names = self._get_batch_customer_names(batch)
        if customer_names:
            if len(customer_names) == 1:
                return customer_names[0]
            return ", ".join(customer_names)

        destinations = []
        for picking in batch.picking_ids:
            location = picking.location_dest_id
            if location:
                destinations.append(location.complete_name or location.display_name or location.name)
        destinations = list(dict.fromkeys(destinations))
        return ", ".join(destinations)

    def _get_report_values(self, docids, data=None):
        docs = self.env["stock.picking.batch"].sudo().browse(docids)
        return {
            "doc_ids": docs.ids,
            "doc_model": "stock.picking.batch",
            "docs": docs,
            "batch_lines": {batch.id: self._get_batch_lines(batch) for batch in docs},
            "batch_destination_labels": {
                batch.id: self._get_batch_destination_label(batch) for batch in docs
            },
        }
