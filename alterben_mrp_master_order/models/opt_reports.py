# -*- coding: utf-8 -*-
from datetime import datetime, time, timedelta
import math
import re
import unicodedata
from odoo import api, fields, models, _
from odoo.exceptions import UserError


def _end_of_day(date_value):
    if not date_value:
        return False
    return datetime.combine(date_value, time.max)


def _get_report_sales_days(env):
    """Dias para ventas sin despacho (desde hoy hacia atras)."""
    Type = env["mrp.master.type"].sudo()
    mtype = Type.search([("active", "=", True)], limit=1)
    if mtype and getattr(mtype, "report_sales_days", False):
        return int(mtype.report_sales_days or 0)
    return 30


def _extract_suffix(code):
    if not code:
        return ""
    parts = code.split("-")
    if len(parts) >= 3 and parts[-1].startswith("T") and parts[-1][1:].isdigit():
        return "-".join(parts[-3:])
    if len(parts) >= 2:
        return "-".join(parts[-2:])
    return code


def _classify_code(code):
    if not code:
        return "pt", ""
    if code.startswith("VE-"):
        return "ignore", ""
    if code.startswith("S2-VE-"):
        return "ignore", ""
    if code.startswith("S3-"):
        return "s3", code[3:]
    if code.startswith("S2-VI-"):
        return "s2", code[len("S2-VI-") :]
    if code.startswith("VI-"):
        return "s1", code[len("VI-") :]
    return "pt", code


def _extract_ped_reference(value):
    text = (value or "").strip().upper()
    if not text:
        return ""
    match = re.search(r"PED-\d+", text)
    return match.group(0) if match else ""


def _get_production_flow_key(production):
    direct_fields = [
        getattr(production, "x_studio_pedido_original", False),
    ]
    if "pedido_original_id" in production._fields and production.pedido_original_id:
        direct_fields.append(production.pedido_original_id.name)
    for value in direct_fields:
        ped = _extract_ped_reference(value)
        if ped:
            return ped
    ped = _extract_ped_reference(getattr(production, "origin", False))
    if ped:
        return ped
    origin = (getattr(production, "origin", False) or "").strip()
    if origin:
        return origin
    return production.name or f"MO-{production.id}"


def _get_flow_key_from_values(pedido_original=False, origin_ref=False, fallback=False):
    for value in [pedido_original, origin_ref]:
        ped = _extract_ped_reference(value)
        if ped:
            return ped
    origin = (origin_ref or "").strip()
    if origin:
        return origin
    return fallback or ""


def _get_turn_capacity(turns, hours, size, env=None):
    turns = int(turns or 0)
    hours = int(hours or 0)
    per_turn = None
    if env is not None:
        mtype = env["mrp.master.type"].sudo().search([("active", "=", True)], limit=1)
        if mtype:
            if size == "small":
                per_turn = mtype.rpt_units_small_8 if hours == 8 else mtype.rpt_units_small_12
            else:
                per_turn = mtype.rpt_units_large_8 if hours == 8 else mtype.rpt_units_large_12
    if per_turn is None:
        if size == "small":
            per_turn = 88 if hours == 8 else 132
        else:
            per_turn = 24 if hours == 8 else 36
    return turns * per_turn


def _validate_turns(turns, hours):
    if int(turns or 0) == 3 and int(hours or 0) == 12:
        raise UserError(_("No se permiten 3 turnos de 12 horas."))


def _get_category_size(product):
    complete = (product.categ_id.complete_name or "").upper()
    norm = unicodedata.normalize('NFKD', complete).encode('ascii', 'ignore').decode('ascii')
    norm = norm.replace(" ", "")
    if "AUTOMOTRIZ/MGRANDES" in norm:
        return "large"
    if "AUTOMOTRIZ/MPEQUENAS" in norm:
        return "small"
    return "other"


def _get_internal_stock_excluding_caf(env, product):
    if not product:
        return 0.0
    Location = env["stock.location"].sudo()
    Quant = env["stock.quant"].sudo()
    internal_locations = Location.search([("usage", "=", "internal")])
    valid_locations = internal_locations.filtered(
        lambda loc: "CAF" not in ((loc.complete_name or loc.display_name or loc.name or "").upper())
    )
    if not valid_locations:
        return 0.0
    grouped = Quant.read_group(
        [("product_id", "=", product.id), ("location_id", "in", valid_locations.ids)],
        ["quantity:sum"],
        [],
    )
    qty = grouped[0]["quantity"] if grouped else 0.0
    return max(0.0, qty or 0.0)


def _format_qty_label(value):
    value = float(value or 0.0)
    return int(value) if value.is_integer() else round(value, 2)


def _normalize_category_text(value):
    text = unicodedata.normalize("NFKD", (value or "").strip().upper()).encode("ascii", "ignore").decode("ascii")
    return text.replace(" ", "")


def _extract_glass_sheet_area_m2(product):
    code = (product.default_code or "").upper()
    match = re.search(r"(\d{3,4})X(\d{3,4})", code)
    if not match:
        return 0.0
    width_mm = float(match.group(1))
    height_mm = float(match.group(2))
    return (width_mm * height_mm) / 1000000.0


def _extract_pvb_roll_area_m2(product):
    code = (product.default_code or "").upper()
    name = (product.name or "").upper()
    parts = [p.strip() for p in code.split("-") if p.strip()]
    width_mm = 0.0
    if len(parts) >= 3:
        width_match = re.search(r"(\d+(?:[.,]\d+)?)", parts[2])
        if width_match:
            width_mm = float(width_match.group(1).replace(",", "."))
    if not width_mm:
        width_match = re.search(r"-(\d{3,4})(?:-|$)", code)
        if width_match:
            width_mm = float(width_match.group(1))
    length_m = 0.0
    length_match = re.search(r"(\d+(?:[.,]\d+)?)\s*M\s*$", name)
    if length_match:
        length_m = float(length_match.group(1).replace(",", "."))
    if width_mm <= 0.0 or length_m <= 0.0:
        return 0.0
    return (width_mm / 1000.0) * length_m


def _compute_raw_material_plr_qty(product, stock_qty):
    category_text = _normalize_category_text(product.categ_id.complete_name if product and product.categ_id else "")
    stock_qty = float(stock_qty or 0.0)
    if not product or abs(stock_qty) <= 1e-9:
        return 0

    area_m2 = 0.0
    if "MATERIAPRIMA/VIDRIO" in category_text:
        area_m2 = _extract_glass_sheet_area_m2(product)
    elif "MATERIAPRIMA/PVB" in category_text:
        area_m2 = _extract_pvb_roll_area_m2(product)

    if area_m2 <= 0.0:
        return 0
    return int(math.floor(stock_qty / area_m2))


def _get_production_original_order_label(production):
    if not production:
        return ""
    if "pedido_original_id" in production._fields and production.pedido_original_id:
        return production.pedido_original_id.name or ""
    for field_name in ("x_studio_pedido_original",):
        if field_name in production._fields and getattr(production, field_name, False):
            return str(getattr(production, field_name))
    return ""


def _get_move_done_datetime(move):
    if not move:
        return False
    production = getattr(move, "raw_material_production_id", False) or getattr(move, "production_id", False)
    for candidate in [
        getattr(production, "date_finished", False),
        getattr(move.picking_id, "date_done", False) if getattr(move, "picking_id", False) else False,
        getattr(move, "date", False),
        getattr(move, "write_date", False),
        getattr(move, "create_date", False),
    ]:
        if candidate:
            return candidate
    return False


def _get_move_consumed_qty(move):
    if "quantity" in move._fields:
        qty = float(getattr(move, "quantity", 0.0) or 0.0)
        if abs(qty) > 1e-9:
            return qty
    qty = float(getattr(move, "quantity_done", 0.0) or 0.0)
    if abs(qty) > 1e-9:
        return qty
    total = 0.0
    for line in getattr(move, "move_line_ids", []):
        qty_line = float(getattr(line, "qty_done", 0.0) or 0.0)
        if abs(qty_line) <= 1e-9 and "quantity_product_uom" in line._fields:
            qty_line = float(getattr(line, "quantity_product_uom", 0.0) or 0.0)
        total += qty_line
    if abs(total) > 1e-9:
        return total
    if getattr(move, "state", "") == "done":
        for field_name in ("quantity", "product_uom_qty"):
            if field_name in move._fields:
                qty = float(getattr(move, field_name, 0.0) or 0.0)
                if abs(qty) > 1e-9:
                    return qty
    return 0.0


def _format_sales_no_stock_stock_detail_html(product, stock_lines):
    if not product or not stock_lines:
        return "<div><p>Sin detalle disponible.</p></div>"

    total = sum(float(item.get("qty") or 0.0) for item in stock_lines)
    rows = []
    for item in sorted(stock_lines, key=lambda x: (-float(x.get("qty") or 0.0), x.get("location") or "")):
        qty = float(item.get("qty") or 0.0)
        if abs(qty) <= 1e-6:
            continue
        rows.append(
            "<tr>"
            "<td style='padding:8px 10px;border-bottom:1px solid #e5e7eb;vertical-align:top;'><strong>%s</strong></td>"
            "<td style='padding:8px 10px;border-bottom:1px solid #e5e7eb;vertical-align:top;text-align:right;'><strong>%s</strong></td>"
            "</tr>"
            % (item.get("location") or "Sin ubicacion", _format_qty_label(qty))
        )
    if not rows:
        return "<div><p>Sin detalle disponible.</p></div>"

    return """
        <div style="padding:8px 0;">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
            <div style="font-size:14px;color:#374151;">Total existencias</div>
            <div style="font-size:20px;font-weight:700;">%s</div>
          </div>
          <table style="width:100%%;border-collapse:collapse;font-size:13px;">
            <thead>
              <tr style="background:#f8fafc;border-bottom:1px solid #d1d5db;">
                <th style="text-align:left;padding:8px 10px;">Ubicacion</th>
                <th style="text-align:right;padding:8px 10px;">Cantidad</th>
              </tr>
            </thead>
            <tbody>%s</tbody>
          </table>
        </div>
    """ % (_format_qty_label(total), "".join(rows))


def _format_sales_no_stock_sales_detail_html(product, sale_lines):
    if not product or not sale_lines:
        return "<div><p>Sin detalle disponible.</p></div>"

    total = 0.0
    rows = []
    ordered_lines = sorted(
        sale_lines,
        key=lambda item: (
            item.get("delivery_date") or item.get("order_date") or "",
            item.get("sale_order") or "",
            item.get("customer") or "",
        ),
    )
    for item in ordered_lines:
        qty = float(item.get("qty") or 0.0)
        if qty <= 0.0:
            continue
        total += qty
        rows.append(
            "<tr>"
            "<td style='padding:8px 10px;border-bottom:1px solid #e5e7eb;vertical-align:top;white-space:nowrap;'><strong>%s</strong></td>"
            "<td style='padding:8px 10px;border-bottom:1px solid #e5e7eb;vertical-align:top;'>%s</td>"
            "<td style='padding:8px 10px;border-bottom:1px solid #e5e7eb;vertical-align:top;text-align:right;'><strong>%s</strong></td>"
            "<td style='padding:8px 10px;border-bottom:1px solid #e5e7eb;vertical-align:top;white-space:nowrap;'>%s</td>"
            "</tr>"
            % (
                item.get("sale_order") or "-",
                item.get("customer") or "-",
                _format_qty_label(qty),
                item.get("delivery_date") or item.get("order_date") or "-",
            )
        )
    if not rows:
        return "<div><p>Sin detalle disponible.</p></div>"

    return """
        <div style="padding:8px 0;">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
            <div style="font-size:14px;color:#374151;">Total ventas pendientes</div>
            <div style="font-size:20px;font-weight:700;">%s</div>
          </div>
          <table style="width:100%%;border-collapse:collapse;font-size:13px;">
            <thead>
              <tr style="background:#f8fafc;border-bottom:1px solid #d1d5db;">
                <th style="text-align:left;padding:8px 10px;">Sale Order</th>
                <th style="text-align:left;padding:8px 10px;">Cliente</th>
                <th style="text-align:right;padding:8px 10px;">Cantidad</th>
                <th style="text-align:left;padding:8px 10px;">Fecha</th>
              </tr>
            </thead>
            <tbody>%s</tbody>
          </table>
        </div>
    """ % (_format_qty_label(total), "".join(rows))


def _format_raw_material_required_detail_html(product, rows_data):
    if not product or not rows_data:
        return "<div><p>Sin detalle disponible.</p></div>"

    total = 0.0
    rows = []
    ordered_rows = sorted(
        rows_data,
        key=lambda item: (-float(item.get("qty") or 0.0), item.get("finished_product_code") or "", item.get("document_label") or ""),
    )
    for item in ordered_rows:
        qty = float(item.get("qty") or 0.0)
        if qty <= 0.0:
            continue
        total += qty
        rows.append(
            "<tr>"
            "<td style='padding:8px 10px;border-bottom:1px solid #e5e7eb;vertical-align:top;'><strong>%s</strong><br/><span style='color:#6b7280;'>%s</span></td>"
            "<td style='padding:8px 10px;border-bottom:1px solid #e5e7eb;vertical-align:top;'>%s</td>"
            "<td style='padding:8px 10px;border-bottom:1px solid #e5e7eb;vertical-align:top;text-align:right;'><strong>%s</strong></td>"
            "</tr>"
            % (
                item.get("finished_product_code") or item.get("finished_product_name") or "Sin producto",
                item.get("finished_product_name") or "-",
                item.get("document_label") or "Sin documento",
                _format_qty_label(qty),
            )
        )

    if not rows:
        return "<div><p>Sin detalle disponible.</p></div>"

    return """
        <div style="padding:8px 0;">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
            <div style="font-size:14px;color:#374151;">Total requerido</div>
            <div style="font-size:20px;font-weight:700;">%s</div>
          </div>
          <table style="width:100%%;border-collapse:collapse;font-size:13px;">
            <thead>
              <tr style="background:#f8fafc;border-bottom:1px solid #d1d5db;">
                <th style="text-align:left;padding:8px 10px;">Producto final</th>
                <th style="text-align:left;padding:8px 10px;">Documento</th>
                <th style="text-align:right;padding:8px 10px;">Cantidad</th>
              </tr>
            </thead>
            <tbody>%s</tbody>
          </table>
        </div>
    """ % (_format_qty_label(total), "".join(rows))


def _format_raw_material_in_process_detail_html(product, rows_data):
    if not product or not rows_data:
        return "<div><p>Sin detalle disponible.</p></div>"

    total = 0.0
    rows = []
    ordered_rows = sorted(
        rows_data,
        key=lambda item: (-float(item.get("qty") or 0.0), item.get("finished_product_code") or "", item.get("document_label") or ""),
    )
    for item in ordered_rows:
        qty = float(item.get("qty") or 0.0)
        if qty <= 0.0:
            continue
        total += qty
        rows.append(
            "<tr>"
            "<td style='padding:8px 10px;border-bottom:1px solid #e5e7eb;vertical-align:top;'><strong>%s</strong><br/><span style='color:#6b7280;'>%s</span></td>"
            "<td style='padding:8px 10px;border-bottom:1px solid #e5e7eb;vertical-align:top;'>%s</td>"
            "<td style='padding:8px 10px;border-bottom:1px solid #e5e7eb;vertical-align:top;text-align:right;'><strong>%s</strong></td>"
            "</tr>"
            % (
                item.get("finished_product_code") or item.get("finished_product_name") or "Sin producto",
                item.get("finished_product_name") or "-",
                item.get("document_label") or "Sin documento",
                _format_qty_label(qty),
            )
        )

    if not rows:
        return "<div><p>Sin detalle disponible.</p></div>"

    return """
        <div style="padding:8px 0;">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
            <div style="font-size:14px;color:#374151;">Total consumido en produccion</div>
            <div style="font-size:20px;font-weight:700;">%s</div>
          </div>
          <table style="width:100%%;border-collapse:collapse;font-size:13px;">
            <thead>
              <tr style="background:#f8fafc;border-bottom:1px solid #d1d5db;">
                <th style="text-align:left;padding:8px 10px;">Producto</th>
                <th style="text-align:left;padding:8px 10px;">Documento</th>
                <th style="text-align:right;padding:8px 10px;">Cantidad</th>
              </tr>
            </thead>
            <tbody>%s</tbody>
          </table>
        </div>
    """ % (_format_qty_label(total), "".join(rows))


def _get_internal_stock_breakdown(env, product):
    if not product:
        return []
    Quant = env["stock.quant"].sudo()
    quants = Quant.search([
        ("product_id", "=", product.id),
        ("location_id.usage", "=", "internal"),
    ])
    stock_by_location = {}
    for quant in quants:
        qty = float(quant.quantity or 0.0)
        if abs(qty) <= 1e-6:
            continue
        location_name = quant.location_id.complete_name or quant.location_id.display_name or quant.location_id.name or "Sin ubicacion"
        stock_by_location[location_name] = stock_by_location.get(location_name, 0.0) + qty
    return [
        {"location": location, "qty": qty}
        for location, qty in stock_by_location.items()
        if abs(qty) > 1e-6
    ]


def _get_stock_in_pt_aaa(env, product):
    if not product:
        return 0.0
    Location = env["stock.location"].sudo()
    Quant = env["stock.quant"].sudo()

    root = Location.search([
        "|",
        ("complete_name", "ilike", "PT-AAA"),
        ("name", "ilike", "PT-AAA"),
    ], limit=1)
    if not root:
        root = Location.search([
            "|",
            ("complete_name", "ilike", "%/PT-AAA%"),
            ("name", "ilike", "%PT-AAA%"),
        ], limit=1)
    if not root:
        return 0.0

    locations = Location.search([
        ("id", "child_of", root.id),
        ("usage", "=", "internal"),
    ])
    if not locations:
        return 0.0

    grouped = Quant.read_group(
        [("product_id", "=", product.id), ("location_id", "in", locations.ids)],
        ["quantity:sum"],
        [],
    )
    qty = grouped[0]["quantity"] if grouped else 0.0
    return max(0.0, qty or 0.0)


def _get_orderpoint_map(env, product_ids):
    if not product_ids:
        return {}
    Orderpoint = env["stock.warehouse.orderpoint"]
    orderpoints = Orderpoint.search([("product_id", "in", product_ids)])
    result = {}
    for op in orderpoints:
        pid = op.product_id.id
        if pid not in result:
            result[pid] = {
                "min": op.product_min_qty or 0.0,
                "max": op.product_max_qty or 0.0,
            }
    return result


def _get_mold_map(env, product_ids):
    if not product_ids:
        return {}
    Receta = env["receta.pvb"]
    recs = Receta.search([("product_id", "in", product_ids)])
    return {rec.product_id.id: (rec.cant_moldes or 0.0) for rec in recs if rec.product_id}


def _get_sales_maps(env, product_ids, report_date):
    if not product_ids:
        return {}, {}
    SaleLine = env["sale.order.line"].sudo()
    SaleOrder = env["sale.order"].sudo()
    SaleReport = env["sale.report"].sudo() if "sale.report" in env else None
    Product = env["product.product"].sudo()
    # La fecha del reporte es solo informativa; no filtra ventas.
    date_start = None
    domain = [
        ("product_id", "in", product_ids),
        ("order_id.state", "in", ["sale", "done"]),
    ]
    qty_uom_field = "product_uom_qty" if "product_uom_qty" in SaleLine._fields else None
    qty_inv_field = "qty_invoiced" if "qty_invoiced" in SaleLine._fields else None
    qty_del_field = "qty_delivered" if "qty_delivered" in SaleLine._fields else None
    qty_net_del_field = "ab_qty_to_deliver_net" if "ab_qty_to_deliver_net" in SaleLine._fields else None
    qty_field = "qty_to_deliver" if "qty_to_deliver" in SaleLine._fields else None

    total_map = {}
    prio_map = {}

    if qty_net_del_field and getattr(SaleLine._fields[qty_net_del_field], "store", False):
        grouped = SaleLine.read_group(domain, ["product_id", f"{qty_net_del_field}:sum"], ["product_id"])
        total_map = {
            g["product_id"][0]: max(0.0, g.get(qty_net_del_field) or 0.0)
            for g in grouped if g.get("product_id")
        }
        prio_map = {}
        return total_map, prio_map

    # Calculo directo: Cantidad neta a entregar despues de cancelaciones.
    if qty_uom_field and qty_del_field and getattr(SaleLine._fields[qty_uom_field], "store", False) and getattr(SaleLine._fields[qty_del_field], "store", False):
        lines = SaleLine.search(domain)
        for line in lines:
            qty_to_deliver = getattr(line, "ab_qty_to_deliver_net", False)
            if qty_to_deliver is False:
                qty_to_deliver = (line.product_uom_qty or 0.0) - (line.qty_delivered or 0.0)
            if qty_to_deliver <= 0:
                continue
            pid = line.product_id.id
            total_map[pid] = total_map.get(pid, 0.0) + qty_to_deliver
        prio_map = {}
        return total_map, prio_map

    lines = SaleLine.search(domain)
    total_map = {}
    prio_map = {}
    for line in lines:
        qty_to_deliver = getattr(line, "ab_qty_to_deliver_net", False)
        if qty_to_deliver is False:
            qty_to_deliver = (line.product_uom_qty or 0.0) - (line.qty_delivered or 0.0)
        if qty_to_deliver <= 0:
            continue
        pid = line.product_id.id
        total_map[pid] = total_map.get(pid, 0.0) + qty_to_deliver
        # Prioridad ahora es interna, no depende del pedido.
    return total_map, prio_map


def _get_in_process_productions(env, report_date, include_done=False):
    Production = env["mrp.production"].sudo()
    date_end = _end_of_day(report_date)
    states = ["confirmed", "progress", "planned", "to_close"]
    if include_done:
        states.append("done")
    domain = [
        ("state", "in", states),
        ("picking_type_id.active", "=", True),
    ]
    if date_end:
        if "date_planned_start" in Production._fields:
            date_field = "date_planned_start"
        elif "date_start" in Production._fields:
            date_field = "date_start"
        else:
            date_field = "create_date"
        domain.append((date_field, "<=", fields.Datetime.to_string(date_end)))
    return Production.search(domain)


def _get_production_effective_qty(production):
    if not production:
        return 0.0
    planned_qty = float(getattr(production, "product_qty", 0.0) or 0.0)
    if getattr(production, "state", "") != "done":
        return planned_qty
    qty = float(getattr(production, "qty_produced", 0.0) or 0.0)
    for mv in getattr(production, "move_finished_ids", production.env["stock.move"]):
        qty = max(qty, float(getattr(mv, "quantity_done", 0.0) or 0.0))
    if qty <= 0.0:
        qty = planned_qty
    return max(qty, 0.0)


def _get_scrap_effective_qty(scrap):
    if not scrap:
        return 0.0
    qty = float(getattr(scrap, "scrap_qty", 0.0) or 0.0)
    if qty <= 0.0:
        qty = float(getattr(scrap, "quantity", 0.0) or 0.0)
    return max(qty, 0.0)


def _get_scrap_done_datetime(scrap):
    if not scrap:
        return False
    production = getattr(scrap, "production_id", False) or (getattr(scrap, "workorder_id", False) and scrap.workorder_id.production_id) or False
    for candidate in [
        getattr(production, "date_finished", False) if production else False,
        getattr(production, "date_start", False) if production else False,
        getattr(scrap, "date_done", False) if "date_done" in scrap._fields else False,
        getattr(scrap, "date", False) if "date" in scrap._fields else False,
        getattr(scrap, "write_date", False) if "write_date" in scrap._fields else False,
        getattr(scrap, "create_date", False) if "create_date" in scrap._fields else False,
    ]:
        value = candidate
        if value:
            return fields.Datetime.to_datetime(value)
    return False


def _get_in_process_scrap_summary(env, report_date):
    Scrap = env["stock.scrap"].sudo()
    date_end = _end_of_day(report_date)
    domain = []
    if "state" in Scrap._fields:
        domain.append(("state", "=", "done"))
    if date_end and "create_date" in Scrap._fields:
        domain.append(("create_date", "<=", fields.Datetime.to_string(date_end)))

    scraps = Scrap.search(domain)
    result = {}
    for scrap in scraps:
        product = getattr(scrap, "product_id", False)
        code = ((product and product.default_code) or "").strip()
        kind, raw_code = _classify_code(code)
        if kind == "ignore":
            continue
        suffix = _extract_suffix(raw_code)
        pedido_original = (
            getattr(scrap, "x_studio_pedido_original", False)
            or ((getattr(scrap, "production_id", False) and getattr(scrap.production_id, "x_studio_pedido_original", False)) or False)
        )
        origin_ref = (
            getattr(scrap, "x_studio_origen", False)
            or getattr(scrap, "origin", False)
            or ((getattr(scrap, "production_id", False) and getattr(scrap.production_id, "origin", False)) or False)
        )
        flow_key = _get_flow_key_from_values(pedido_original, origin_ref, fallback=(scrap.name or f"SCRAP-{scrap.id}"))
        group_key = (flow_key, suffix)
        bucket = result.setdefault(group_key, {"s1": 0.0, "s2": 0.0, "s3": 0.0, "pt": 0.0})
        bucket[kind] += _get_scrap_effective_qty(scrap)
    return result


def _build_in_process_summary(env, report_date):
    all_productions = _get_in_process_productions(env, report_date, include_done=True)
    open_productions = _get_in_process_productions(env, report_date, include_done=False)
    scrap_summary = _get_in_process_scrap_summary(env, report_date)
    payload = {}
    stage_order = ["s1", "s2", "s3", "pt"]

    for prod in all_productions:
        code = (prod.product_id.default_code or "").strip()
        kind, raw_code = _classify_code(code)
        if kind == "ignore":
            continue
        suffix = _extract_suffix(raw_code)
        flow_key = _get_production_flow_key(prod)
        group_key = (flow_key, suffix)
        bucket = payload.setdefault(
            group_key,
            {
                "flow_key": flow_key,
                "suffix": suffix,
                "stage_created": {stage: 0.0 for stage in stage_order},
                "stage_open": {stage: 0.0 for stage in stage_order},
                "stage_scrap": {stage: 0.0 for stage in stage_order},
                "product_ids": {stage: False for stage in stage_order},
            }
        )
        qty = _get_production_effective_qty(prod)
        bucket["stage_created"][kind] += qty
        if prod.product_id and not bucket["product_ids"][kind]:
            bucket["product_ids"][kind] = prod.product_id.id

    for prod in open_productions:
        code = (prod.product_id.default_code or "").strip()
        kind, raw_code = _classify_code(code)
        if kind == "ignore":
            continue
        suffix = _extract_suffix(raw_code)
        flow_key = _get_production_flow_key(prod)
        group_key = (flow_key, suffix)
        bucket = payload.setdefault(
            group_key,
            {
                "flow_key": flow_key,
                "suffix": suffix,
                "stage_created": {stage: 0.0 for stage in stage_order},
                "stage_open": {stage: 0.0 for stage in stage_order},
                "stage_scrap": {stage: 0.0 for stage in stage_order},
                "product_ids": {stage: False for stage in stage_order},
            }
        )
        bucket["stage_open"][kind] += float(prod.product_qty or 0.0)
        if prod.product_id and not bucket["product_ids"][kind]:
            bucket["product_ids"][kind] = prod.product_id.id

    for group_key, stage_scrap in scrap_summary.items():
        flow_key, suffix = group_key
        bucket = payload.setdefault(
            group_key,
            {
                "flow_key": flow_key,
                "suffix": suffix,
                "stage_created": {stage: 0.0 for stage in stage_order},
                "stage_open": {stage: 0.0 for stage in stage_order},
                "stage_scrap": {stage: 0.0 for stage in stage_order},
                "product_ids": {stage: False for stage in stage_order},
            }
        )
        for stage in stage_order:
            bucket["stage_scrap"][stage] += float(stage_scrap.get(stage) or 0.0)

    summary = {}
    for (_flow_key, suffix), data in payload.items():
        created = {stage: float(data["stage_created"][stage] or 0.0) for stage in stage_order}
        opened = {stage: float(data["stage_open"][stage] or 0.0) for stage in stage_order}
        scraps = {stage: float(data["stage_scrap"][stage] or 0.0) for stage in stage_order}

        pt_visible = opened["pt"]
        s3_visible = max(created["s3"] - created["pt"], 0.0)
        s2_visible = max(created["s2"] - max(created["s3"], created["pt"]), 0.0)
        s1_visible = max(created["s1"] - max(created["s2"], created["s3"], created["pt"]), 0.0)

        visible = {
            "s1": max(s1_visible - scraps["s1"], 0.0),
            "s2": max(s2_visible - scraps["s2"], 0.0),
            "s3": max(s3_visible - scraps["s3"], 0.0),
            "pt": pt_visible,
        }
        visible_total = visible["s1"] + visible["s2"] + visible["s3"] + visible["pt"]

        bucket = summary.setdefault(
            suffix,
            {
                "suffix": suffix,
                "pt": 0.0,
                "s1": 0.0,
                "s2": 0.0,
                "s3": 0.0,
                "product_ids": {"pt": False, "s1": False, "s2": False, "s3": False},
                "details": [],
                "scrap": {"pt": 0.0, "s1": 0.0, "s2": 0.0, "s3": 0.0},
            },
        )
        for stage in stage_order:
            bucket[stage] += visible[stage]
            bucket["scrap"][stage] += scraps[stage]
            if data["product_ids"][stage] and not bucket["product_ids"][stage]:
                bucket["product_ids"][stage] = data["product_ids"][stage]
        bucket["details"].append(
            {
                "flow_key": data["flow_key"],
                "total": visible_total,
                "stages": {stage: visible[stage] for stage in stage_order if visible[stage] > 0.0},
                "scrap": {stage: scraps[stage] for stage in stage_order if scraps[stage] > 0.0},
            }
        )
    return summary


def _format_in_process_detail_html(summary_data):
    if not summary_data:
        return "<div><p>Sin detalle disponible.</p></div>"

    details = summary_data.get("details") or []
    if not details:
        return "<div><p>Sin detalle disponible.</p></div>"

    rows = []
    total = 0.0
    for detail in sorted(details, key=lambda d: (-float(d.get("total") or 0.0), d.get("flow_key") or "")):
        detail_total = float(detail.get("total") or 0.0)
        total += detail_total
        stage_parts = []
        for stage in ("s1", "s2", "s3", "pt"):
            qty = float((detail.get("stages") or {}).get(stage) or 0.0)
            if qty <= 0.0:
                continue
            stage_parts.append("%s = %s" % (stage.upper(), int(qty) if float(qty).is_integer() else qty))
        if not stage_parts:
            stage_parts.append("Sin cantidad visible")
        scrap_parts = []
        for stage in ("s1", "s2", "s3"):
            qty = float((detail.get("scrap") or {}).get(stage) or 0.0)
            if qty <= 0.0:
                continue
            scrap_parts.append("%s = %s" % (stage.upper(), int(qty) if float(qty).is_integer() else qty))
        flow_key = detail.get("flow_key") or "Sin referencia"
        total_label = int(detail_total) if float(detail_total).is_integer() else detail_total
        rows.append(
            "<tr>"
            "<td style='padding:8px 10px;border-bottom:1px solid #e5e7eb;vertical-align:top;white-space:nowrap;'><strong>%s</strong></td>"
            "<td style='padding:8px 10px;border-bottom:1px solid #e5e7eb;vertical-align:top;white-space:nowrap;text-align:right;'><strong>%s</strong></td>"
            "<td style='padding:8px 10px;border-bottom:1px solid #e5e7eb;vertical-align:top;'>%s%s</td>"
            "</tr>"
            % (
                flow_key,
                total_label,
                ", ".join(stage_parts),
                ("<br/><span style='color:#6b7280;'>Desechos: %s</span>" % ", ".join(scrap_parts)) if scrap_parts else "",
            )
        )

    total_label = int(total) if float(total).is_integer() else total
    return """
        <div style="padding:8px 0;">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
            <div style="font-size:14px;color:#374151;">Total consolidado</div>
            <div style="font-size:20px;font-weight:700;">%s</div>
          </div>
          <table style="width:100%%;border-collapse:collapse;font-size:13px;">
            <thead>
              <tr style="background:#f8fafc;border-bottom:1px solid #d1d5db;">
                <th style="text-align:left;padding:8px 10px;">PED</th>
                <th style="text-align:right;padding:8px 10px;">Aporta</th>
                <th style="text-align:left;padding:8px 10px;">Desglose</th>
              </tr>
            </thead>
            <tbody>%s</tbody>
          </table>
        </div>
    """ % (total_label, "".join(rows))


def _format_in_process_stage_detail_html(summary_data, target_stage):
    if not summary_data:
        return "<div><p>Sin detalle disponible.</p></div>"

    stage_label = target_stage.upper()
    details = summary_data.get("details") or []
    rows = []
    total = 0.0

    for detail in sorted(details, key=lambda d: (-float((d.get("stages") or {}).get(target_stage) or 0.0), d.get("flow_key") or "")):
        stage_qty = float((detail.get("stages") or {}).get(target_stage) or 0.0)
        if stage_qty <= 0.0:
            continue
        total += stage_qty
        scrap_qty = float((detail.get("scrap") or {}).get(target_stage) or 0.0)
        flow_key = detail.get("flow_key") or "Sin referencia"
        qty_label = int(stage_qty) if float(stage_qty).is_integer() else stage_qty
        scrap_label = int(scrap_qty) if float(scrap_qty).is_integer() else scrap_qty
        rows.append(
            "<tr>"
            "<td style='padding:8px 10px;border-bottom:1px solid #e5e7eb;vertical-align:top;white-space:nowrap;'><strong>%s</strong></td>"
            "<td style='padding:8px 10px;border-bottom:1px solid #e5e7eb;vertical-align:top;white-space:nowrap;text-align:right;'><strong>%s</strong></td>"
            "<td style='padding:8px 10px;border-bottom:1px solid #e5e7eb;vertical-align:top;'>%s</td>"
            "</tr>"
            % (
                flow_key,
                qty_label,
                ("Desechos: %s" % scrap_label) if scrap_qty > 0.0 and target_stage in ("s1", "s2", "s3") else "-",
            )
        )

    if not rows:
        return "<div><p>Sin detalle disponible para %s.</p></div>" % stage_label

    total_label = int(total) if float(total).is_integer() else total
    return """
        <div style="padding:8px 0;">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
            <div style="font-size:14px;color:#374151;">Total %s</div>
            <div style="font-size:20px;font-weight:700;">%s</div>
          </div>
          <table style="width:100%%;border-collapse:collapse;font-size:13px;">
            <thead>
              <tr style="background:#f8fafc;border-bottom:1px solid #d1d5db;">
                <th style="text-align:left;padding:8px 10px;">PED</th>
                <th style="text-align:right;padding:8px 10px;">%s</th>
                <th style="text-align:left;padding:8px 10px;">Observacion</th>
              </tr>
            </thead>
            <tbody>%s</tbody>
          </table>
        </div>
    """ % (stage_label, total_label, stage_label, "".join(rows))


def _get_in_process_maps(env, report_date):
    summary = _build_in_process_summary(env, report_date)
    pt = {suffix: data["pt"] for suffix, data in summary.items() if data["pt"]}
    s1 = {suffix: data["s1"] for suffix, data in summary.items() if data["s1"]}
    s2 = {suffix: data["s2"] for suffix, data in summary.items() if data["s2"]}
    s3 = {suffix: data["s3"] for suffix, data in summary.items() if data["s3"]}
    return pt, s1, s2, s3


def _get_in_process_summary(env, report_date):
    return _build_in_process_summary(env, report_date)


def _get_automotive_products_by_suffix(env):
    Product = env["product.product"]
    Category = env["product.category"]
    small_cat = Category.search([("complete_name", "ilike", "AUTOMOTRIZ / M PEQUEÑAS")], limit=1)
    if not small_cat:
        small_cat = Category.search([("complete_name", "ilike", "AUTOMOTRIZ / M PEQUENAS")], limit=1)
    if not small_cat:
        small_cat = Category.search([("complete_name", "ilike", "AUTOMOTRIZ/M PEQUEÑAS")], limit=1)
    if not small_cat:
        small_cat = Category.search([("complete_name", "ilike", "AUTOMOTRIZ/M PEQUENAS")], limit=1)
    large_cat = Category.search([("complete_name", "ilike", "AUTOMOTRIZ / M GRANDES")], limit=1)
    if not large_cat:
        large_cat = Category.search([("complete_name", "ilike", "AUTOMOTRIZ/M GRANDES")], limit=1)
    cat_ids = []
    if small_cat:
        cat_ids.append(small_cat.id)
    if large_cat:
        cat_ids.append(large_cat.id)
    products = Product.search([("categ_id", "child_of", cat_ids)]) if cat_ids else Product.browse()
    return {_extract_suffix((product.default_code or "").strip()): product for product in products if product.default_code}


def _allocate_capacity(items, capacity, key):
    remaining = capacity
    for item in sorted(items, key=lambda i: (i.get("priority_rank", 0), i[key]), reverse=True):
        if remaining <= 0:
            break
        if (item.get("required") or 0.0) <= 0.0:
            continue
        remaining_req = max(0.0, (item.get("required") or 0.0) - (item.get("produce") or 0.0))
        need = min(item[key], remaining_req)
        if need <= 0:
            continue
        cap_left = item["cap_left"]
        alloc = min(need, remaining, cap_left)
        if alloc <= 0:
            continue
        item["produce"] += alloc
        item["cap_left"] -= alloc
        remaining -= alloc
    return remaining


class MRPReportProductionDaily(models.TransientModel):
    _name = "mrp.report.production.daily"
    _description = "Reporte Produccion Diaria"
    _rec_name = "name"

    name = fields.Char(string="Nombre", default="Reporte diario de produccion")
    report_date = fields.Date(string="Fecha", default=fields.Date.context_today, required=True)
    report_type = fields.Selection(
        [("suggested", "Sugerido"), ("general", "General")],
        string="Tipo de reporte",
        default="suggested",
        required=True,
    )
    size_filter = fields.Selection(
        [("all", "Todos"), ("small", "M pequeñas"), ("large", "M grandes")],
        string="Tamaño",
        default="all",
        required=True,
    )
    turns_small = fields.Selection([("1", "1"), ("2", "2"), ("3", "3")], string="Turnos M pequeñas", default="2", required=True)
    hours_per_turn_small = fields.Selection([("8", "8"), ("12", "12")], string="Horas por turno (M pequeñas)", default="8", required=True)
    turns_large = fields.Selection([("1", "1"), ("2", "2"), ("3", "3")], string="Turnos M grandes", default="2", required=True)
    hours_per_turn_large = fields.Selection([("8", "8"), ("12", "12")], string="Horas por turno (M grandes)", default="8", required=True)
    max_mold_changes_small = fields.Integer(string="Max. cambios moldes (peq)", default=5, required=True)
    max_mold_changes_large = fields.Integer(string="Max. cambios moldes (grandes)", default=4, required=True)
    filter_product_id = fields.Many2one("product.product", string="Buscar producto")
    line_ids = fields.One2many("mrp.report.production.daily.line", "wizard_id", string="Líneas")
    total_small_count = fields.Integer(string="Total pequeñas", compute="_compute_totals", store=False)
    total_large_count = fields.Integer(string="Total grandes", compute="_compute_totals", store=False)
    total_small_produce = fields.Integer(string="Producir pequeñas", compute="_compute_totals", store=False)
    total_large_produce = fields.Integer(string="Producir grandes", compute="_compute_totals", store=False)
    total_small_required = fields.Integer(string="Requerido pequeñas", compute="_compute_totals", store=False)
    total_large_required = fields.Integer(string="Requerido grandes", compute="_compute_totals", store=False)

    def _check_turn_rules(self):
        _validate_turns(self.turns_small, self.hours_per_turn_small)
        _validate_turns(self.turns_large, self.hours_per_turn_large)

    def action_open_lines(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Produccion diaria"),
            "res_model": "mrp.report.production.daily.line",
            "view_mode": "tree",
            "domain": [("wizard_id", "=", self.id)],
            "search_view_id": self.env.ref("alterben_mrp_master_order.view_report_production_daily_line_search").id,
        }

    def action_print(self):
        self.ensure_one()
        if not self.line_ids:
            self.action_generate()
        return self.env.ref("alterben_mrp_master_order.action_report_production_daily_pdf").report_action(self)

    def action_open_report_params(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Parametros de reportes"),
            "res_model": "mrp.report.params.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {},
        }

    def action_open_help(self):
        self.ensure_one()
        wizard = self.env["mrp.report.help.wizard"].sudo().create({"report_kind": "daily"})
        return {
            "type": "ir.actions.act_window",
            "name": _("Ayuda del reporte"),
            "res_model": "mrp.report.help.wizard",
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_generate(self):
        self.ensure_one()
        self._check_turn_rules()
        self.line_ids.unlink()

        Product = self.env["product.product"]
        Category = self.env["product.category"]
        small_cat = Category.search([("complete_name", "ilike", "AUTOMOTRIZ / M PEQUEÑAS")], limit=1)
        if not small_cat:
            small_cat = Category.search([("complete_name", "ilike", "AUTOMOTRIZ / M PEQUENAS")], limit=1)
        if not small_cat:
            small_cat = Category.search([("complete_name", "ilike", "AUTOMOTRIZ/M PEQUEÑAS")], limit=1)
        if not small_cat:
            small_cat = Category.search([("complete_name", "ilike", "AUTOMOTRIZ/M PEQUENAS")], limit=1)
        large_cat = Category.search([("complete_name", "ilike", "AUTOMOTRIZ / M GRANDES")], limit=1)
        if not large_cat:
            large_cat = Category.search([("complete_name", "ilike", "AUTOMOTRIZ/M GRANDES")], limit=1)
        cat_ids = []
        if small_cat:
            cat_ids.append(small_cat.id)
        if large_cat:
            cat_ids.append(large_cat.id)
        products = Product.search([("categ_id", "child_of", cat_ids)]) if cat_ids else Product.browse()
        if not products:
            raise UserError(_("No se encontraron productos en las categorias AUTOMOTRIZ/M PEQUEÑAS o AUTOMOTRIZ/M GRANDES."))

        product_ids = products.ids
        # La fecha solo se usa para mostrar en el PDF, no para filtrar el cálculo interno.
        sales_map, _priority_map = _get_sales_maps(self.env, product_ids, None)
        op_map = _get_orderpoint_map(self.env, product_ids)
        mold_map = _get_mold_map(self.env, product_ids)
        in_process_summary = _get_in_process_summary(self.env, None)
        pt_map, s1_map, s2_map, s3_map = _get_in_process_maps(self.env, None)

        items_small = []
        items_large = []
        lines = []

        for product in products:
            size = _get_category_size(product)
            if size not in ("small", "large"):
                continue
            if self.size_filter != "all" and size != self.size_filter:
                continue
            code = (product.default_code or "").strip()
            suffix = _extract_suffix(code)
            pt_qty = pt_map.get(suffix, 0.0)
            s1_qty = s1_map.get(suffix, 0.0)
            s2_qty = s2_map.get(suffix, 0.0)
            s3_qty = s3_map.get(suffix, 0.0)
            in_process = pt_qty + s1_qty + s2_qty + s3_qty

            stock = _get_internal_stock_excluding_caf(self.env, product)
            stock_aaa = _get_stock_in_pt_aaa(self.env, product)
            sales = sales_map.get(product.id, 0.0)
            if sales > stock:
                priority_rank = 3
            elif sales == stock and sales > 0:
                priority_rank = 2
            elif sales > 0:
                priority_rank = 1
            else:
                priority_rank = 0
            sales_to_cover = max(0.0, sales)
            prio_to_cover = sales if priority_rank == 3 else 0.0

            op = op_map.get(product.id, {})
            min_qty = op.get("min", 0.0)
            max_qty = op.get("max", 0.0)
            if min_qty > 0 and (stock + in_process - sales) >= min_qty:
                required_qty = 0.0
            else:
                if max_qty > 0:
                    required_qty = max(0.0, max_qty + sales - stock - in_process)
                else:
                    required_qty = max(0.0, sales - stock - in_process)

            molds = mold_map.get(product.id, 0.0)
            cap = molds * 8 if molds else 0.0
            cap_left = cap if cap > 0 else 999999.0

            item = {
                "product": product,
                "size": size,
                "stock": stock,
                "aaa": stock_aaa,
                "sales": sales_to_cover,
                "prio_sales": prio_to_cover,
                "priority_rank": priority_rank,
                "in_process": in_process,
                "min": min_qty,
                "max": max_qty,
                "required": required_qty,
                "molds": molds,
                "cap_left": cap_left,
                "produce": 0.0,
                "in_process_detail_html": _format_in_process_detail_html(in_process_summary.get(suffix)),
            }
            if size == "small":
                items_small.append(item)
            else:
                items_large.append(item)

        def _select_top_items(items, max_items):
            if max_items <= 0:
                return []
            ordered = sorted(
                items,
                key=lambda i: (i.get("priority_rank", 0), i.get("required", 0.0), i.get("sales", 0.0)),
                reverse=True,
            )
            selected = []
            for item in ordered:
                if (item.get("required") or 0.0) <= 0.0:
                    continue
                selected.append(item)
                if len(selected) >= max_items:
                    break
            return selected

        def _fill(items, capacity):
            remaining = capacity
            remaining = _allocate_capacity(items, remaining, "prio_sales")
            remaining = _allocate_capacity(items, remaining, "sales")
            need_min_items = []
            for item in items:
                if item["min"] <= 0 and item["max"] <= 0:
                    continue
                need_min = max(0.0, item["min"] - (item["stock"] + item["in_process"] - item["sales"]))
                item["need_min"] = need_min
                need_min_items.append(item)
            remaining = _allocate_capacity(need_min_items, remaining, "need_min")
            need_max_items = []
            for item in items:
                if item["max"] <= 0:
                    continue
                need_max = max(0.0, item["max"] + item["sales"] - item["stock"] - item["in_process"])
                item["need_max"] = need_max
                need_max_items.append(item)
            remaining = _allocate_capacity(need_max_items, remaining, "need_max")
            return remaining

        cap_small = _get_turn_capacity(self.turns_small, self.hours_per_turn_small, "small", env=self.env)
        cap_large = _get_turn_capacity(self.turns_large, self.hours_per_turn_large, "large", env=self.env)

        items_small_sel = items_small
        items_large_sel = items_large
        if self.report_type == "suggested":
            max_small = 22 + int(self.max_mold_changes_small or 0)
            max_large = 6 + int(self.max_mold_changes_large or 0)
            items_small_sel = _select_top_items(items_small, max_small)
            items_large_sel = _select_top_items(items_large, max_large)

        _fill(items_small_sel, cap_small)
        _fill(items_large_sel, cap_large)

        def _ensure_needs(items):
            for item in items:
                if "need_min" not in item:
                    item["need_min"] = max(0.0, item["min"] - (item["stock"] + item["in_process"] - item["sales"]))
                if "need_max" not in item:
                    item["need_max"] = max(0.0, item["max"] + item["sales"] - item["stock"])

        def _needs_production(item):
            return (
                (item.get("required") or 0.0) > 0.0
                or (item.get("aaa") or 0.0) > 0.0
                or (item.get("in_process") or 0.0) > 0.0
            )

        def _build_lines(items, is_excess, only_produce):
            for item in items:
                if only_produce and item["produce"] <= 0:
                    continue
                if not only_produce and not _needs_production(item):
                    continue
                lines.append({
                    "wizard_id": self.id,
                    "product_id": item["product"].id,
                    "product_code": item["product"].default_code or "",
                    "product_name": item["product"].name or "",
                    "max_qty": item["max"],
                    "min_qty": item["min"],
                    "stock_qty": item["stock"],
                    "aaa_qty": item.get("aaa", 0.0),
                    "sales_qty": item["sales"],
                    "priority_sales_qty": item["priority_rank"],
                    "in_process_qty": item["in_process"],
                    "in_process_detail_html": item.get("in_process_detail_html"),
                    "molds_qty": item["molds"],
                    "produce_qty": item["produce"],
                    "required_qty": item["required"],
                    "size_category": item["size"],
                    "is_excess": is_excess,
                })

        _ensure_needs(items_small_sel)
        _ensure_needs(items_large_sel)

        if self.report_type == "general":
            _build_lines(items_small, False, False)
            _build_lines(items_large, False, False)
        else:
            _build_lines(items_small_sel, False, True)
            _build_lines(items_large_sel, False, True)

        def _build_excess(items, capacity):
            extra_cap = capacity * 0.15
            remaining = extra_cap
            candidates = []
            for item in items:
                if item["min"] <= 0 and item["max"] <= 0:
                    continue
                if item["max"] > 0:
                    need = max(0.0, item["max"] + item["sales"] - item["stock"] - item["produce"])
                else:
                    need = 0.0
                if need <= 0:
                    continue
                candidates.append({
                    "ref": item,
                    "need_ex": need,
                    "cap_left": item["cap_left"],
                    "extra": 0.0,
                })
            for c in sorted(candidates, key=lambda x: x["need_ex"], reverse=True):
                if remaining <= 0:
                    break
                alloc = min(c["need_ex"], remaining, c["cap_left"])
                if alloc <= 0:
                    continue
                c["extra"] = alloc
                remaining -= alloc
            for c in candidates:
                if c["extra"] <= 0:
                    continue
                item = c["ref"]
                lines.append({
                    "wizard_id": self.id,
                    "product_id": item["product"].id,
                    "product_code": item["product"].default_code or "",
                    "product_name": item["product"].name or "",
                    "max_qty": item["max"],
                    "min_qty": item["min"],
                    "stock_qty": item["stock"],
                    "aaa_qty": item.get("aaa", 0.0),
                    "sales_qty": item["sales"],
                    "priority_sales_qty": item["prio_sales"],
                    "in_process_qty": item["in_process"],
                    "in_process_detail_html": item.get("in_process_detail_html"),
                    "molds_qty": item["molds"],
                    "produce_qty": c["extra"],
                    "required_qty": item["required"],
                    "size_category": item["size"],
                    "is_excess": True,
                })

        if self.report_type != "general":
            _build_excess(items_small_sel, cap_small)
            _build_excess(items_large_sel, cap_large)

        if not lines:
            def _build_fallback(items):
                for item in items:
                    if (
                        (item["sales"] or 0.0) <= 0.0
                        and (item["prio_sales"] or 0.0) <= 0.0
                        and (item["min"] or 0.0) <= 0.0
                        and (item["max"] or 0.0) <= 0.0
                        and (item["in_process"] or 0.0) <= 0.0
                        and (item["molds"] or 0.0) <= 0.0
                        and (item["stock"] or 0.0) <= 0.0
                    ):
                        continue
                    lines.append({
                        "wizard_id": self.id,
                        "product_id": item["product"].id,
                        "product_code": item["product"].default_code or "",
                        "product_name": item["product"].name or "",
                        "max_qty": item["max"],
                        "min_qty": item["min"],
                        "stock_qty": item["stock"],
                        "aaa_qty": item.get("aaa", 0.0),
                        "sales_qty": item["sales"],
                        "priority_sales_qty": item["prio_sales"],
                        "in_process_qty": item["in_process"],
                        "in_process_detail_html": item.get("in_process_detail_html"),
                        "molds_qty": item["molds"],
                        "produce_qty": 0.0,
                        "required_qty": item["required"],
                        "size_category": item["size"],
                        "is_excess": False,
                    })
            _build_fallback(items_small)
            _build_fallback(items_large)

        if lines:
            self.env["mrp.report.production.daily.line"].create(lines)
        return {
            "type": "ir.actions.act_window",
            "name": _("Produccion diaria"),
            "res_model": "mrp.report.production.daily",
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
        }

    @api.depends(
        "line_ids",
        "line_ids.size_category",
        "line_ids.produce_qty",
        "line_ids.required_qty",
    )
    def _compute_totals(self):
        for rec in self:
            small = rec.line_ids.filtered(lambda l: l.size_category == "small")
            large = rec.line_ids.filtered(lambda l: l.size_category == "large")
            rec.total_small_count = len(small)
            rec.total_large_count = len(large)
            rec.total_small_produce = int(sum(small.mapped("produce_qty")) or 0)
            rec.total_large_produce = int(sum(large.mapped("produce_qty")) or 0)
            rec.total_small_required = int(sum(small.mapped("required_qty")) or 0)
            rec.total_large_required = int(sum(large.mapped("required_qty")) or 0)


class MRPReportProductionDailyLine(models.TransientModel):
    _name = "mrp.report.production.daily.line"
    _description = "Linea Produccion Diaria"
    _order = "is_excess, priority_sales_qty desc, produce_qty desc"

    wizard_id = fields.Many2one("mrp.report.production.daily", required=True, ondelete="cascade")
    row_number = fields.Integer(string="#", compute="_compute_row_number", store=False)
    product_id = fields.Many2one("product.product", string="Producto", required=True)
    product_code = fields.Char("Referencia", compute="_compute_product_info", store=True)
    product_name = fields.Char("Nombre", compute="_compute_product_info", store=True)
    size_category = fields.Selection([("small", "M pequeñas"), ("large", "M grandes")], string="Tamaño", compute="_compute_product_info", store=False)
    max_qty = fields.Float("Max", digits=(16, 0))
    min_qty = fields.Float("Min", digits=(16, 0))
    stock_qty = fields.Float("Existencia", digits=(16, 0))
    aaa_qty = fields.Float("AAA", digits=(16, 0))
    sales_qty = fields.Float("Pedido (ventas)", digits=(16, 0))
    priority_sales_qty = fields.Float("Ventas prioridad", digits=(16, 0))
    in_process_qty = fields.Float("En proceso", digits=(16, 0))
    in_process_detail_html = fields.Html("Detalle en proceso", sanitize=False)
    molds_qty = fields.Float("Cant. moldes", digits=(16, 0))
    produce_qty = fields.Float("Producir", digits=(16, 0))
    required_qty = fields.Float("Requerido", digits=(16, 0))
    is_excess = fields.Boolean("Excedente", default=False)
    available_product_ids = fields.Many2many(
        "product.product", string="Productos disponibles", compute="_compute_available_products", store=False
    )

    def _compute_row_number(self):
        for line in self:
            line.row_number = 0
        for wiz in self.mapped("wizard_id"):
            ordered = self.search([("wizard_id", "=", wiz.id)], order="id asc")
            idx = 1
            for line in ordered:
                line.row_number = idx
                idx += 1

    @api.depends("product_id")
    def _compute_product_info(self):
        for line in self:
            prod = line.product_id
            line.product_code = prod.default_code or ""
            line.product_name = prod.name or ""
            line.size_category = _get_category_size(prod) if prod else False

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if "priority_sales_qty" in vals:
                val = int(vals.get("priority_sales_qty") or 0)
                if val < 0:
                    val = 0
                if val > 3:
                    val = 3
                vals["priority_sales_qty"] = val
        return super().create(vals_list)

    def write(self, vals):
        if "priority_sales_qty" in vals:
            val = int(vals.get("priority_sales_qty") or 0)
            if val < 0:
                val = 0
            if val > 3:
                val = 3
            vals["priority_sales_qty"] = val
        return super().write(vals)

    def _compute_available_products(self):
        Category = self.env["product.category"]
        small_cat = Category.search([("complete_name", "ilike", "AUTOMOTRIZ / M PEQUEÑAS")], limit=1)
        if not small_cat:
            small_cat = Category.search([("complete_name", "ilike", "AUTOMOTRIZ / M PEQUENAS")], limit=1)
        if not small_cat:
            small_cat = Category.search([("complete_name", "ilike", "AUTOMOTRIZ/M PEQUEÑAS")], limit=1)
        if not small_cat:
            small_cat = Category.search([("complete_name", "ilike", "AUTOMOTRIZ/M PEQUENAS")], limit=1)
        large_cat = Category.search([("complete_name", "ilike", "AUTOMOTRIZ / M GRANDES")], limit=1)
        if not large_cat:
            large_cat = Category.search([("complete_name", "ilike", "AUTOMOTRIZ/M GRANDES")], limit=1)
        cat_ids = []
        if small_cat:
            cat_ids.append(small_cat.id)
        if large_cat:
            cat_ids.append(large_cat.id)
        products = self.env["product.product"].search([("categ_id", "child_of", cat_ids)]) if cat_ids else self.env["product.product"].browse()
        for line in self:
            line.available_product_ids = products

    def action_open_in_process_detail(self):
        self.ensure_one()
        wizard = self.env["mrp.report.in_process.detail.wizard"].sudo().create({
            "title": _("Detalle de En proceso - %s") % (self.product_code or self.product_name or self.product_id.display_name),
            "detail_html": self.in_process_detail_html or "<div><p>Sin detalle disponible.</p></div>",
        })
        return {
            "type": "ir.actions.act_window",
            "name": _("Detalle de En proceso"),
            "res_model": "mrp.report.in_process.detail.wizard",
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
        }


class MRPReportParamsWizard(models.TransientModel):
    _name = "mrp.report.params.wizard"
    _description = "Parametros de reportes"

    report_sales_days = fields.Integer(string="Dias de busqueda de ventas sin despacho")
    rpt_units_small_8 = fields.Integer(string="Unidades por turno (pequeñas, 8h)")
    rpt_units_small_12 = fields.Integer(string="Unidades por turno (pequeñas, 12h)")
    rpt_units_large_8 = fields.Integer(string="Unidades por turno (grandes, 8h)")
    rpt_units_large_12 = fields.Integer(string="Unidades por turno (grandes, 12h)")

    def _get_master_type(self):
        return self.env["mrp.master.type"].sudo().search([("active", "=", True)], limit=1)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        mtype = self._get_master_type()
        if not mtype:
            return res
        res.update({
            "report_sales_days": mtype.report_sales_days or 0,
            "rpt_units_small_8": mtype.rpt_units_small_8 or 0,
            "rpt_units_small_12": mtype.rpt_units_small_12 or 0,
            "rpt_units_large_8": mtype.rpt_units_large_8 or 0,
            "rpt_units_large_12": mtype.rpt_units_large_12 or 0,
        })
        return res

    def action_apply(self):
        self.ensure_one()
        mtype = self._get_master_type()
        if not mtype:
            return {"type": "ir.actions.act_window_close"}
        mtype.write({
            "report_sales_days": self.report_sales_days,
            "rpt_units_small_8": self.rpt_units_small_8,
            "rpt_units_small_12": self.rpt_units_small_12,
            "rpt_units_large_8": self.rpt_units_large_8,
            "rpt_units_large_12": self.rpt_units_large_12,
        })
        return {"type": "ir.actions.act_window_close"}


class MRPReportInProcess(models.TransientModel):
    _name = "mrp.report.in_process"
    _description = "Reporte Productos en Proceso"
    _rec_name = "name"

    name = fields.Char(string="Nombre", default="Productos en proceso")
    report_date = fields.Date(string="Fecha", default=fields.Date.context_today, required=True)
    show_valued = fields.Boolean(string="Valorado", default=False)
    size_filter = fields.Selection(
        [("all", "Todos"), ("small", "M pequeñas"), ("large", "M grandes")],
        string="Tamaño",
        default="all",
        required=True,
    )
    turns_small = fields.Selection([("1", "1"), ("2", "2"), ("3", "3")], string="Turnos M pequeñas", default="2", required=True)
    hours_per_turn_small = fields.Selection([("8", "8"), ("12", "12")], string="Horas por turno (M pequeñas)", default="8", required=True)
    turns_large = fields.Selection([("1", "1"), ("2", "2"), ("3", "3")], string="Turnos M grandes", default="2", required=True)
    hours_per_turn_large = fields.Selection([("8", "8"), ("12", "12")], string="Horas por turno (M grandes)", default="8", required=True)
    line_ids = fields.One2many("mrp.report.in_process.line", "wizard_id", string="Líneas")

    def _check_turn_rules(self):
        _validate_turns(self.turns_small, self.hours_per_turn_small)
        _validate_turns(self.turns_large, self.hours_per_turn_large)

    def action_open_lines(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Productos en proceso"),
            "res_model": "mrp.report.in_process.line",
            "view_mode": "tree",
            "domain": [("wizard_id", "=", self.id)],
            "context": {"show_valued": bool(self.show_valued)},
            "search_view_id": self.env.ref("alterben_mrp_master_order.view_report_in_process_line_search").id,
        }

    def action_print(self):
        self.ensure_one()
        if not self.line_ids:
            self.action_generate()
        return self.env.ref("alterben_mrp_master_order.action_report_in_process_pdf").report_action(self)

    def action_open_help(self):
        self.ensure_one()
        wizard = self.env["mrp.report.help.wizard"].sudo().create({"report_kind": "in_process"})
        return {
            "type": "ir.actions.act_window",
            "name": _("Ayuda del reporte"),
            "res_model": "mrp.report.help.wizard",
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_generate(self):
        self.ensure_one()
        self._check_turn_rules()
        self.line_ids.unlink()

        suffix_products = _get_automotive_products_by_suffix(self.env)
        summary = _get_in_process_summary(self.env, None)
        lines = []
        product_model = self.env["product.product"]
        for suffix, data in summary.items():
            product = (
                suffix_products.get(suffix)
                or product_model.browse(data["product_ids"].get("pt"))
                or product_model.browse(data["product_ids"].get("s3"))
                or product_model.browse(data["product_ids"].get("s2"))
                or product_model.browse(data["product_ids"].get("s1"))
            )
            if not product:
                continue
            size = _get_category_size(product)
            if size not in ("small", "large"):
                continue
            if self.size_filter != "all" and size != self.size_filter:
                continue
            pt_qty = data["pt"]
            s1_qty = data["s1"]
            s2_qty = data["s2"]
            s3_qty = data["s3"]
            total = pt_qty + s1_qty + s2_qty + s3_qty
            if total <= 0:
                continue
            lines.append({
                "wizard_id": self.id,
                "product_id": product.id,
                "product_code": product.default_code or "",
                "product_name": product.name or "",
                "stock_qty": _get_internal_stock_excluding_caf(self.env, product),
                "qty_pt": pt_qty,
                "qty_s1": s1_qty,
                "qty_s2": s2_qty,
                "qty_s3": s3_qty,
                "qty_total": total,
                "in_process_detail_html": _format_in_process_detail_html(data),
                "in_process_s1_detail_html": _format_in_process_stage_detail_html(data, "s1"),
                "in_process_s2_detail_html": _format_in_process_stage_detail_html(data, "s2"),
                "in_process_s3_detail_html": _format_in_process_stage_detail_html(data, "s3"),
                "in_process_pt_detail_html": _format_in_process_stage_detail_html(data, "pt"),
            })
        if lines:
            self.env["mrp.report.in_process.line"].create(lines)
        else:
            raise UserError(_("No hay datos para la fecha seleccionada."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Productos en proceso"),
            "res_model": "mrp.report.in_process",
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
        }


class MRPReportInProcessLine(models.TransientModel):
    _name = "mrp.report.in_process.line"
    _description = "Linea Productos en Proceso"
    _order = "qty_total desc"

    wizard_id = fields.Many2one("mrp.report.in_process", required=True, ondelete="cascade")
    row_number = fields.Integer(string="#", compute="_compute_row_number", store=False)
    show_valued = fields.Boolean(related="wizard_id.show_valued", store=True)
    product_id = fields.Many2one("product.product", string="Producto", required=True)
    product_code = fields.Char("Referencia")
    product_name = fields.Char("Nombre")
    size_category = fields.Selection(
        [("small", "M pequeñas"), ("large", "M grandes")],
        string="Tamaño",
        compute="_compute_size_category",
        store=False,
    )
    stock_qty = fields.Float("Existencia", digits=(16, 0))
    qty_s1 = fields.Float("En proceso S1", digits=(16, 0))
    qty_s2 = fields.Float("En proceso S2", digits=(16, 0))
    qty_s3 = fields.Float("En proceso S3", digits=(16, 0))
    qty_pt = fields.Float("En proceso PT", digits=(16, 0))
    qty_total = fields.Float("En proceso total", digits=(16, 0))
    in_process_detail_html = fields.Html("Detalle en proceso", sanitize=False)
    in_process_s1_detail_html = fields.Html("Detalle S1", sanitize=False)
    in_process_s2_detail_html = fields.Html("Detalle S2", sanitize=False)
    in_process_s3_detail_html = fields.Html("Detalle S3", sanitize=False)
    in_process_pt_detail_html = fields.Html("Detalle PT", sanitize=False)
    cost_unit = fields.Float("Costo unit", digits=(16, 2), compute="_compute_costs", store=False)
    cost_s1_total = fields.Float("Costo S1", digits=(16, 2), compute="_compute_costs", store=False)
    cost_s2_total = fields.Float("Costo S2", digits=(16, 2), compute="_compute_costs", store=False)
    cost_s3_total = fields.Float("Costo S3", digits=(16, 2), compute="_compute_costs", store=False)
    cost_pt_total = fields.Float("Costo PT", digits=(16, 2), compute="_compute_costs", store=False)
    cost_total = fields.Float("Costo total", digits=(16, 2), compute="_compute_costs", store=False)

    def _compute_row_number(self):
        for line in self:
            line.row_number = 0
        for wiz in self.mapped("wizard_id"):
            ordered = self.search([("wizard_id", "=", wiz.id)], order="id asc")
            idx = 1
            for line in ordered:
                line.row_number = idx
                idx += 1

    @api.depends("product_id")
    def _compute_size_category(self):
        for line in self:
            value = _get_category_size(line.product_id) if line.product_id else False
            line.size_category = value if value in ("small", "large") else False

    @api.depends("product_id", "qty_s1", "qty_s2", "qty_s3", "qty_pt", "qty_total")
    def _compute_costs(self):
        for line in self:
            unit = line.product_id.standard_price if line.product_id else 0.0
            line.cost_unit = unit
            line.cost_s1_total = (line.qty_s1 or 0.0) * unit
            line.cost_s2_total = (line.qty_s2 or 0.0) * unit
            line.cost_s3_total = (line.qty_s3 or 0.0) * unit
            line.cost_pt_total = (line.qty_pt or 0.0) * unit
            line.cost_total = (line.qty_total or 0.0) * unit

    def action_open_in_process_detail(self):
        self.ensure_one()
        wizard = self.env["mrp.report.in_process.detail.wizard"].sudo().create({
            "title": _("Detalle de En proceso - %s") % (self.product_code or self.product_name or self.product_id.display_name),
            "detail_html": self.in_process_detail_html or "<div><p>Sin detalle disponible.</p></div>",
        })
        return {
            "type": "ir.actions.act_window",
            "name": _("Detalle de En proceso"),
            "res_model": "mrp.report.in_process.detail.wizard",
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
        }

    def _open_stage_detail(self, stage_key, stage_label):
        self.ensure_one()
        detail_field = "in_process_%s_detail_html" % stage_key
        wizard = self.env["mrp.report.in_process.detail.wizard"].sudo().create({
            "title": _("Detalle %s - %s") % (stage_label, (self.product_code or self.product_name or self.product_id.display_name)),
            "detail_html": getattr(self, detail_field) or "<div><p>Sin detalle disponible.</p></div>",
        })
        return {
            "type": "ir.actions.act_window",
            "name": _("Detalle %s") % stage_label,
            "res_model": "mrp.report.in_process.detail.wizard",
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_open_s1_detail(self):
        return self._open_stage_detail("s1", "S1")

    def action_open_s2_detail(self):
        return self._open_stage_detail("s2", "S2")

    def action_open_s3_detail(self):
        return self._open_stage_detail("s3", "S3")

    def action_open_pt_detail(self):
        return self._open_stage_detail("pt", "PT")


class MRPReportHelpWizard(models.TransientModel):
    _name = "mrp.report.help.wizard"
    _description = "Ayuda de reportes de produccion"

    report_kind = fields.Selection(
        [("daily", "Reporte diario"), ("in_process", "Productos en proceso")],
        required=True,
        default="daily",
    )
    title = fields.Char(string="Titulo", compute="_compute_help_content")
    help_html = fields.Html(string="Guia", sanitize=False, compute="_compute_help_content")

    @api.depends("report_kind")
    def _compute_help_content(self):
        for wizard in self:
            if wizard.report_kind == "in_process":
                wizard.title = _("Como se calcula Productos en proceso")
                wizard.help_html = """
                    <div>
                      <h3>Logica general</h3>
                      <p>El reporte agrupa primero por la referencia madre del flujo. Busca un codigo PED- en Pedido Original y, si no lo encuentra, lo busca en Origen. Despues separa por la referencia base o sufijo del producto.</p>
                      <p>Dentro de cada flujo no suma etapas de forma ciega. Usa produccion acumulada por etapa para no perder piezas ya hechas ni duplicar piezas que ya avanzaron a la etapa siguiente.</p>
                      <h3>Regla por etapas</h3>
                      <ul>
                        <li>PT visible: solo lo que esta abierto en PT.</li>
                        <li>S3 visible: produccion acumulada en S3 menos produccion acumulada que ya paso a PT.</li>
                        <li>S2 visible: produccion acumulada en S2 menos la cantidad acumulada que ya paso a S3 o PT.</li>
                        <li>S1 visible: produccion acumulada en S1 menos la cantidad acumulada que ya paso a S2, S3 o PT.</li>
                      </ul>
                      <h3>Done y ocultos</h3>
                      <p>Las MOs en done no se ignoran por completo. Su cantidad se usa como produccion acumulada de la etapa para no perder material ya hecho que todavia no ha sido absorbido por la siguiente etapa.</p>
                      <p>Si luego entran nuevos PED del mismo producto, cada flujo se limpia por separado y al final se suman todos en la fila del producto.</p>
                      <p>Los desechos validados tambien se descuentan por etapa dentro del mismo flujo, pero solo en S1, S2 y S3. El scrap de PT no baja En proceso. El descuento se aplica con tope a cero dentro del mismo PED, para que un desecho viejo no reduzca un lote nuevo.</p>
                      <h3>Columnas</h3>
                      <ul>
                        <li>Existencia: stock interno del producto excluyendo CAF.</li>
                        <li>En proceso S1, S2, S3 y PT: cantidades visibles por etapa despues de depurar el avance acumulado del flujo.</li>
                        <li>En proceso total: suma depurada de etapas, evitando sobrecontar la misma corrida y evitando ocultar cantidades ya hechas.</li>
                        <li>Icono de detalle: abre el desglose por PED para ver de donde sale el total consolidado de la fila.</li>
                      </ul>
                    </div>
                """
            else:
                wizard.title = _("Como se calcula Reporte diario de produccion")
                wizard.help_html = """
                    <div>
                      <h3>Logica general</h3>
                      <p>La columna En proceso usa la misma regla del reporte Productos en proceso. Primero limpia cada flujo por PED y referencia, y luego consolida todos los PED del mismo producto en una sola fila.</p>
                      <h3>Columnas principales</h3>
                      <ul>
                        <li>Existencia: stock interno del producto excluyendo CAF.</li>
                        <li>AAA: stock del producto en PT-AAA y sububicaciones internas.</li>
                        <li>Pedido (ventas): pendiente neto de entrega del producto.</li>
                        <li>En proceso: cantidad visible del flujo productivo despues de aplicar la logica acumulada por etapas.</li>
                        <li>Producir: sugerencia resultante segun ventas, minimos, maximos, moldes y capacidad diaria.</li>
                        <li>Requerido: necesidad teorica antes de aplicar la restriccion de capacidad.</li>
                      </ul>
                      <h3>Regla de En proceso</h3>
                      <ul>
                        <li>PT se muestra solo por lo que esta abierto.</li>
                        <li>S3 se muestra por el acumulado visible de la etapa.</li>
                        <li>S2 se reduce por lo que ya fue desprendido hacia S3 y PT dentro del mismo PED.</li>
                        <li>S1 se reduce por lo que ya fue absorbido por S2, S3 o PT dentro del mismo PED.</li>
                        <li>Los desechos validados se restan solo en S1, S2 y S3 dentro del mismo PED, con tope a cero. El scrap de PT no se descuenta de En proceso.</li>
                        <li>El icono de detalle de la linea abre el desglose por PED para explicar de donde sale la cifra final.</li>
                      </ul>
                    </div>
                """


class MRPReportInProcessDetailWizard(models.TransientModel):
    _name = "mrp.report.in_process.detail.wizard"
    _description = "Detalle de En proceso"

    title = fields.Char(string="Titulo", required=True)
    detail_html = fields.Html(string="Detalle", sanitize=False, required=True)


class MRPReportRawMaterials(models.TransientModel):
    _name = "mrp.report.raw_materials"
    _description = "Reporte Materias Primas"

    report_date = fields.Date(string="Fecha", default=fields.Date.context_today, required=True)
    size_filter = fields.Selection(
        [("all", "Todos"), ("small", "M pequeñas"), ("large", "M grandes")],
        string="Tamaño",
        default="all",
        required=True,
    )
    turns_small = fields.Selection([("1", "1"), ("2", "2"), ("3", "3")], string="Turnos M pequeñas", default="2", required=True)
    hours_per_turn_small = fields.Selection([("8", "8"), ("12", "12")], string="Horas por turno (M pequeñas)", default="8", required=True)
    turns_large = fields.Selection([("1", "1"), ("2", "2"), ("3", "3")], string="Turnos M grandes", default="2", required=True)
    hours_per_turn_large = fields.Selection([("8", "8"), ("12", "12")], string="Horas por turno (M grandes)", default="8", required=True)
    product_ids = fields.Many2many("product.product", string="Productos")
    categ_ids = fields.Many2many("product.category", string="Categorias de producto")
    line_ids = fields.One2many("mrp.report.raw_materials.line", "wizard_id", string="Líneas")

    def _check_turn_rules(self):
        _validate_turns(self.turns_small, self.hours_per_turn_small)
        _validate_turns(self.turns_large, self.hours_per_turn_large)

    def action_open_lines(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Materias primas"),
            "res_model": "mrp.report.raw_materials.line",
            "view_mode": "tree",
            "domain": [("wizard_id", "=", self.id)],
            "search_view_id": self.env.ref("alterben_mrp_master_order.view_report_raw_materials_line_search").id,
        }

    def action_print(self):
        self.ensure_one()
        if not self.line_ids:
            self.action_generate()
        return self.env.ref("alterben_mrp_master_order.action_report_raw_materials_pdf").report_action(self)

    def action_open_consumption_report(self):
        self.ensure_one()
        action = self.env.ref(
            "alterben_mrp_master_order.action_report_raw_material_consumption",
            raise_if_not_found=False,
        )
        if not action:
            raise UserError(_("No se encontró la acción del reporte Consumos de Materia prima."))
        action_vals = action.read()[0]
        action_vals["context"] = dict(self.env.context or {})
        return action_vals

    def action_generate(self):
        self.ensure_one()
        self._check_turn_rules()
        self.line_ids.unlink()
        selected_products = self.product_ids
        selected_categories = self.categ_ids
        allowed_category_ids = set()
        if selected_categories:
            allowed_category_ids = set(
                self.env["product.category"].search([("id", "child_of", selected_categories.ids)]).ids
            )
            allowed_category_ids.update(selected_categories.ids)

        plan_wizard = self.env["mrp.report.production.daily"].create({
            "report_date": self.report_date,
            "turns_small": self.turns_small,
            "hours_per_turn_small": self.hours_per_turn_small,
            "turns_large": self.turns_large,
            "hours_per_turn_large": self.hours_per_turn_large,
        })
        plan_wizard.action_generate()
        plan_lines = plan_wizard.line_ids.filtered(lambda l: not l.is_excess and l.produce_qty > 0)
        if self.size_filter != "all":
            plan_lines = plan_lines.filtered(lambda l: l.size_category == self.size_filter)

        Bom = self.env["mrp.bom"]
        def _find_bom(product):
            if not product:
                return False
            if hasattr(Bom, "_bom_find"):
                try:
                    return Bom._bom_find(product=product)
                except TypeError:
                    try:
                        return Bom._bom_find(product_tmpl=product.product_tmpl_id)
                    except TypeError:
                        pass
            if getattr(product, "product_tmpl_id", False):
                return Bom.search([("product_tmpl_id", "=", product.product_tmpl_id.id)], limit=1)
            return Bom.search([("product_id", "=", product.id)], limit=1)
        components = {}
        component_required_details = {}
        sale_line_model = self.env["sale.order.line"].sudo()
        for line in plan_lines:
            product = line.product_id
            bom = _find_bom(product)
            if not bom:
                continue
            document_names = []
            if product:
                sale_domain = [
                    ("product_id", "=", product.id),
                    ("order_id.state", "in", ["sale", "done"]),
                ]
                for sale_line in sale_line_model.search(sale_domain):
                    qty_to_deliver = getattr(sale_line, "ab_qty_to_deliver_net", False)
                    if qty_to_deliver is False:
                        qty_to_deliver = (sale_line.product_uom_qty or 0.0) - (sale_line.qty_delivered or 0.0)
                    if qty_to_deliver <= 0.0:
                        continue
                    if sale_line.order_id and sale_line.order_id.name and sale_line.order_id.name not in document_names:
                        document_names.append(sale_line.order_id.name)
            document_label = ", ".join(document_names[:5]) if document_names else "Reporte diario"
            factor = (line.produce_qty or 0.0) / (bom.product_qty or 1.0)
            for bl in bom.bom_line_ids:
                comp = bl.product_id
                qty = (bl.product_qty or 0.0) * factor
                if not comp:
                    continue
                components[comp.id] = components.get(comp.id, 0.0) + qty
                component_required_details.setdefault(comp.id, []).append({
                    "finished_product_code": product.default_code or "",
                    "finished_product_name": product.name or "",
                    "document_label": document_label,
                    "qty": qty,
                })

        Production = self.env["mrp.production"]
        date_end = _end_of_day(self.report_date)
        domain = [("state", "in", ["confirmed", "progress"]) ]
        if date_end:
            if "date_planned_start" in Production._fields:
                date_field = "date_planned_start"
            elif "date_start" in Production._fields:
                date_field = "date_start"
            else:
                date_field = "create_date"
            domain.append((date_field, "<=", fields.Datetime.to_string(date_end)))
        productions = Production.search(domain)
        comp_in_process = {}
        component_in_process_details = {}
        for prod in productions:
            bom = _find_bom(prod.product_id)
            if not bom:
                continue
            document_label = _get_production_flow_key(prod) or prod.name or "Sin referencia"
            factor = (prod.product_qty or 0.0) / (bom.product_qty or 1.0)
            for bl in bom.bom_line_ids:
                comp = bl.product_id
                qty = (bl.product_qty or 0.0) * factor
                if not comp:
                    continue
                comp_in_process[comp.id] = comp_in_process.get(comp.id, 0.0) + qty
                component_in_process_details.setdefault(comp.id, []).append({
                    "finished_product_code": prod.product_id.default_code or "",
                    "finished_product_name": prod.product_id.name or "",
                    "document_label": document_label,
                    "qty": qty,
                })

        Location = self.env["stock.location"]
        loc_mp = Location.search([("complete_name", "=", "WH/Existencias/MP")], limit=1)
        if not loc_mp:
            loc_mp = Location.search([("complete_name", "ilike", "/MP")], limit=1)
        loc_pre = Location.search([("complete_name", "=", "WH/Existencias/PREPRODUCCION")], limit=1)
        if not loc_pre:
            loc_pre = Location.search([("complete_name", "ilike", "PREPRODUCCION")], limit=1)
        Quant = self.env["stock.quant"]
        stock_mp = {}
        stock_pre = {}
        if loc_mp:
            mp_loc_ids = Location.search([("id", "child_of", loc_mp.id)]).ids
            grouped = Quant.read_group([("location_id", "in", mp_loc_ids)], ["product_id", "quantity:sum"], ["product_id"])
            stock_mp = {g["product_id"][0]: g["quantity"] for g in grouped if g.get("product_id")}
        if loc_pre:
            pre_loc_ids = Location.search([("id", "child_of", loc_pre.id)]).ids
            grouped = Quant.read_group([("location_id", "in", pre_loc_ids)], ["product_id", "quantity:sum"], ["product_id"])
            stock_pre = {g["product_id"][0]: g["quantity"] for g in grouped if g.get("product_id")}

        lines = []
        product_ids = set(components.keys())
        op_map = _get_orderpoint_map(self.env, list(product_ids))
        for pid, qty in components.items():
            product = self.env["product.product"].browse(pid)
            if selected_products and product not in selected_products:
                continue
            if allowed_category_ids and product.categ_id.id not in allowed_category_ids:
                continue
            op = op_map.get(pid, {})
            lines.append({
                "wizard_id": self.id,
                "product_id": pid,
                "product_code": product.default_code or "",
                "product_name": product.name or "",
                "min_qty": op.get("min", 0.0),
                "max_qty": op.get("max", 0.0),
                "required_qty": qty,
                "stock_mp_qty": stock_mp.get(pid, 0.0),
                "plr_mp_qty": _compute_raw_material_plr_qty(product, stock_mp.get(pid, 0.0)),
                "plr_mp_display": str(_compute_raw_material_plr_qty(product, stock_mp.get(pid, 0.0))),
                "stock_pre_qty": stock_pre.get(pid, 0.0),
                "plr_pre_qty": _compute_raw_material_plr_qty(product, stock_pre.get(pid, 0.0)),
                "plr_pre_display": str(_compute_raw_material_plr_qty(product, stock_pre.get(pid, 0.0))),
                "in_process_qty": comp_in_process.get(pid, 0.0),
                "required_detail_html": _format_raw_material_required_detail_html(product, component_required_details.get(pid, [])),
                "in_process_detail_html": _format_raw_material_in_process_detail_html(product, component_in_process_details.get(pid, [])),
                "is_extra": False,
            })

        extra_ids = set(stock_mp.keys()) | set(stock_pre.keys())
        extra_ids = extra_ids - product_ids
        for pid in extra_ids:
            product = self.env["product.product"].browse(pid)
            if not product:
                continue
            if selected_products and product not in selected_products:
                continue
            if allowed_category_ids and product.categ_id.id not in allowed_category_ids:
                continue
            op = op_map.get(pid, {})
            lines.append({
                "wizard_id": self.id,
                "product_id": pid,
                "product_code": product.default_code or "",
                "product_name": product.name or "",
                "min_qty": op.get("min", 0.0),
                "max_qty": op.get("max", 0.0),
                "required_qty": 0.0,
                "stock_mp_qty": stock_mp.get(pid, 0.0),
                "plr_mp_qty": _compute_raw_material_plr_qty(product, stock_mp.get(pid, 0.0)),
                "plr_mp_display": str(_compute_raw_material_plr_qty(product, stock_mp.get(pid, 0.0))),
                "stock_pre_qty": stock_pre.get(pid, 0.0),
                "plr_pre_qty": _compute_raw_material_plr_qty(product, stock_pre.get(pid, 0.0)),
                "plr_pre_display": str(_compute_raw_material_plr_qty(product, stock_pre.get(pid, 0.0))),
                "in_process_qty": comp_in_process.get(pid, 0.0),
                "required_detail_html": _format_raw_material_required_detail_html(product, component_required_details.get(pid, [])),
                "in_process_detail_html": _format_raw_material_in_process_detail_html(product, component_in_process_details.get(pid, [])),
                "is_extra": True,
            })

        if lines:
            self.env["mrp.report.raw_materials.line"].create(lines)
        else:
            raise UserError(_("No hay datos para la fecha seleccionada."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Materias primas"),
            "res_model": "mrp.report.raw_materials",
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
        }


class MRPReportRawMaterialsLine(models.TransientModel):
    _name = "mrp.report.raw_materials.line"
    _description = "Linea Materias Primas"
    _order = "is_extra, required_qty desc"

    wizard_id = fields.Many2one("mrp.report.raw_materials", required=True, ondelete="cascade")
    row_number = fields.Integer(string="#", compute="_compute_row_number", store=False)
    product_id = fields.Many2one("product.product", string="Producto", required=True)
    categ_id = fields.Many2one("product.category", string="Categoria", related="product_id.categ_id", store=False)
    product_code = fields.Char("Referencia")
    product_name = fields.Char("Nombre")
    min_qty = fields.Float("Min", digits=(16, 3))
    max_qty = fields.Float("Max", digits=(16, 3))
    uom_id = fields.Many2one("uom.uom", string="UdM", related="product_id.uom_id", store=False)
    size_category = fields.Selection(
        [("small", "M pequeñas"), ("large", "M grandes")],
        string="Tamaño",
        compute="_compute_size_category",
        store=False,
    )
    required_qty = fields.Float("Requerido", digits=(16, 3))
    stock_mp_qty = fields.Float("Stock MP", digits=(16, 3))
    plr_mp_qty = fields.Integer("PL/R MP")
    plr_mp_display = fields.Char("PL/R MP")
    stock_pre_qty = fields.Float("Stock Preproduccion", digits=(16, 3))
    plr_pre_qty = fields.Integer("PL/R Preproduccion")
    plr_pre_display = fields.Char("PL/R Preproduccion")
    in_process_qty = fields.Float("En proceso", digits=(16, 3))
    required_detail_html = fields.Html("Detalle requerido", sanitize=False)
    in_process_detail_html = fields.Html("Detalle stock en produccion", sanitize=False)
    is_extra = fields.Boolean("No considerado", default=False)

    def _compute_row_number(self):
        for line in self:
            line.row_number = 0
        wizards = self.mapped("wizard_id")
        for wiz in wizards:
            ordered = self.search([("wizard_id", "=", wiz.id)], order="id asc")
            idx = 1
            for line in ordered:
                line.row_number = idx
                idx += 1

    def _open_detail_wizard(self, title, detail_html):
        self.ensure_one()
        wizard = self.env["mrp.report.in_process.detail.wizard"].sudo().create({
            "title": title,
            "detail_html": detail_html or "<div><p>Sin detalle disponible.</p></div>",
        })
        return {
            "type": "ir.actions.act_window",
            "name": title,
            "res_model": "mrp.report.in_process.detail.wizard",
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_open_required_detail(self):
        return self._open_detail_wizard(
            _("Detalle de Requerido - %s") % (self.product_code or self.product_name or self.product_id.display_name),
            self.required_detail_html,
        )

    def action_open_raw_in_process_detail(self):
        return self._open_detail_wizard(
            _("Detalle de En proceso - %s") % (self.product_code or self.product_name or self.product_id.display_name),
            self.in_process_detail_html,
        )

    @api.depends("product_id")
    def _compute_size_category(self):
        for line in self:
            value = _get_category_size(line.product_id) if line.product_id else False
            line.size_category = value if value in ("small", "large") else False


class MRPReportRawMaterialConsumption(models.TransientModel):
    _name = "mrp.report.raw_material_consumption"
    _description = "Reporte Consumos de Materia Prima"

    date_from = fields.Date(string="Desde", required=True, default=lambda self: fields.Date.context_today(self) - timedelta(days=7))
    date_to = fields.Date(string="Hasta", required=True, default=fields.Date.context_today)
    raw_product_ids = fields.Many2many(
        "product.product",
        "mrp_report_raw_material_consumption_product_rel",
        "wizard_id",
        "product_id",
        string="Materias primas",
    )
    raw_categ_ids = fields.Many2many(
        "product.category",
        "mrp_report_raw_material_consumption_raw_categ_rel",
        "wizard_id",
        "categ_id",
        string="Categorías de materia prima",
    )
    finished_categ_ids = fields.Many2many(
        "product.category",
        "mrp_report_raw_material_consumption_finished_categ_rel",
        "wizard_id",
        "categ_id",
        string="Categorías de producto final",
    )
    line_ids = fields.One2many("mrp.report.raw_material_consumption.line", "wizard_id", string="Líneas")

    def action_open_lines(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Consumos de Materia prima"),
            "res_model": "mrp.report.raw_material_consumption.line",
            "view_mode": "tree",
            "domain": [("wizard_id", "=", self.id)],
            "search_view_id": self.env.ref("alterben_mrp_master_order.view_report_raw_material_consumption_line_search").id,
        }

    def action_generate(self):
        self.ensure_one()

        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise UserError(_("La fecha Desde no puede ser mayor que la fecha Hasta."))

        date_from_dt = fields.Datetime.to_datetime(datetime.combine(self.date_from, time.min)) if self.date_from else False
        date_to_dt = fields.Datetime.to_datetime(datetime.combine(self.date_to, time.max)) if self.date_to else False

        raw_allowed_categ_ids = set()
        if self.raw_categ_ids:
            raw_allowed_categ_ids = set(self.env["product.category"].search([("id", "child_of", self.raw_categ_ids.ids)]).ids)
            raw_allowed_categ_ids.update(self.raw_categ_ids.ids)

        finished_allowed_categ_ids = set()
        if self.finished_categ_ids:
            finished_allowed_categ_ids = set(self.env["product.category"].search([("id", "child_of", self.finished_categ_ids.ids)]).ids)
            finished_allowed_categ_ids.update(self.finished_categ_ids.ids)

        Move = self.env["stock.move"].sudo()
        Scrap = self.env["stock.scrap"].sudo()
        domain = [
            ("state", "=", "done"),
            ("raw_material_production_id", "!=", False),
        ]
        if self.raw_product_ids:
            domain.append(("product_id", "in", self.raw_product_ids.ids))

        scrap_domain = []
        if "state" in Scrap._fields:
            scrap_domain.append(("state", "=", "done"))
        if "production_id" in Scrap._fields:
            scrap_domain.append(("production_id", "!=", False))

        scraps = Scrap.search(scrap_domain)
        scrap_move_ids = set()
        if "move_id" in Scrap._fields:
            scrap_move_ids = set(scraps.mapped("move_id").ids)

        moves = Move.search(domain, order="raw_material_production_id, product_id, id")
        grouped = {}
        scrap_grouped = {}

        for move in moves:
            if move.id in scrap_move_ids:
                continue
            if "scrapped" in move._fields and move.scrapped:
                continue
            if move.location_dest_id and getattr(move.location_dest_id, "scrap_location", False):
                continue

            production = move.raw_material_production_id
            raw_product = move.product_id
            finished_product = production.product_id if production else False

            if not production or not raw_product:
                continue
            if raw_allowed_categ_ids and raw_product.categ_id.id not in raw_allowed_categ_ids:
                continue
            if finished_allowed_categ_ids and finished_product and finished_product.categ_id.id not in finished_allowed_categ_ids:
                continue

            done_dt = _get_move_done_datetime(move)
            if date_from_dt and done_dt and done_dt < date_from_dt:
                continue
            if date_to_dt and done_dt and done_dt > date_to_dt:
                continue
            if (date_from_dt or date_to_dt) and not done_dt:
                continue

            qty = _get_move_consumed_qty(move)
            if abs(qty) <= 1e-9:
                continue

            key = (production.id, raw_product.id, move.product_uom.id if move.product_uom else raw_product.uom_id.id)
            bucket = grouped.setdefault(key, {
                "wizard_id": self.id,
                "date_done": fields.Datetime.to_string(done_dt) if done_dt else False,
                "production_id": production.id,
                "mo_name": production.name or "",
                "pedido_original": _get_production_original_order_label(production),
                "origin_ref": getattr(production, "origin", False) or "",
                "source_location_name": move.location_id.complete_name or "" if move.location_id else "",
                "finished_product_id": finished_product.id if finished_product else False,
                "finished_product_code": finished_product.default_code or "" if finished_product else "",
                "finished_product_name": finished_product.name or "" if finished_product else "",
                "finished_categ_id": finished_product.categ_id.id if finished_product and finished_product.categ_id else False,
                "raw_product_id": raw_product.id,
                "raw_product_code": raw_product.default_code or "",
                "raw_product_name": raw_product.name or "",
                "raw_categ_id": raw_product.categ_id.id if raw_product.categ_id else False,
                "uom_id": move.product_uom.id if move.product_uom else raw_product.uom_id.id,
                "qty_consumed": 0.0,
                "qty_scrap": 0.0,
            })
            bucket["qty_consumed"] += qty

            if done_dt:
                current_dt = fields.Datetime.to_datetime(bucket["date_done"]) if bucket["date_done"] else False
                if not current_dt or done_dt > current_dt:
                    bucket["date_done"] = fields.Datetime.to_string(done_dt)

        for scrap in scraps:
            production = getattr(scrap, "production_id", False) or (getattr(scrap, "workorder_id", False) and scrap.workorder_id.production_id) or False
            raw_product = getattr(scrap, "product_id", False)
            finished_product = production.product_id if production else False

            if not production or not raw_product:
                continue
            if raw_allowed_categ_ids and raw_product.categ_id.id not in raw_allowed_categ_ids:
                continue
            if finished_allowed_categ_ids and finished_product and finished_product.categ_id.id not in finished_allowed_categ_ids:
                continue

            scrap_dt = _get_scrap_done_datetime(scrap)
            if date_from_dt and scrap_dt and scrap_dt < date_from_dt:
                continue
            if date_to_dt and scrap_dt and scrap_dt > date_to_dt:
                continue
            if (date_from_dt or date_to_dt) and not scrap_dt:
                continue

            qty = _get_scrap_effective_qty(scrap)
            if abs(qty) <= 1e-9:
                continue

            scrap_uom = False
            for field_name in ("product_uom_id", "uom_id"):
                if field_name in scrap._fields:
                    scrap_uom = getattr(scrap, field_name, False)
                    if scrap_uom:
                        break
            key = (production.id, raw_product.id, scrap_uom.id if scrap_uom else raw_product.uom_id.id)
            scrap_grouped[key] = float(scrap_grouped.get(key, 0.0) or 0.0) + qty

            bucket = grouped.setdefault(key, {
                "wizard_id": self.id,
                "date_done": fields.Datetime.to_string(scrap_dt) if scrap_dt else False,
                "production_id": production.id,
                "mo_name": production.name or "",
                "pedido_original": _get_production_original_order_label(production),
                "origin_ref": getattr(production, "origin", False) or "",
                "source_location_name": scrap.location_id.complete_name or "" if getattr(scrap, "location_id", False) else "",
                "finished_product_id": finished_product.id if finished_product else False,
                "finished_product_code": finished_product.default_code or "" if finished_product else "",
                "finished_product_name": finished_product.name or "" if finished_product else "",
                "finished_categ_id": finished_product.categ_id.id if finished_product and finished_product.categ_id else False,
                "raw_product_id": raw_product.id,
                "raw_product_code": raw_product.default_code or "",
                "raw_product_name": raw_product.name or "",
                "raw_categ_id": raw_product.categ_id.id if raw_product.categ_id else False,
                "uom_id": scrap_uom.id if scrap_uom else raw_product.uom_id.id,
                "qty_consumed": 0.0,
                "qty_scrap": 0.0,
            })
            if scrap_dt:
                current_dt = fields.Datetime.to_datetime(bucket["date_done"]) if bucket["date_done"] else False
                if not current_dt or scrap_dt > current_dt:
                    bucket["date_done"] = fields.Datetime.to_string(scrap_dt)

        if not grouped:
            self.line_ids.unlink()
            raise UserError(_("No hay consumos de materia prima para los filtros seleccionados."))

        for key, bucket in grouped.items():
            bucket["qty_scrap"] = float(scrap_grouped.get(key, 0.0) or 0.0)

        self.line_ids.unlink()
        self.env["mrp.report.raw_material_consumption.line"].create(list(grouped.values()))
        return {
            "type": "ir.actions.act_window",
            "name": _("Consumos de Materia prima"),
            "res_model": "mrp.report.raw_material_consumption",
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
        }


class MRPReportRawMaterialConsumptionLine(models.TransientModel):
    _name = "mrp.report.raw_material_consumption.line"
    _description = "Linea Consumo de Materia Prima"
    _order = "date_done desc, mo_name, raw_product_code"

    wizard_id = fields.Many2one("mrp.report.raw_material_consumption", required=True, ondelete="cascade")
    row_number = fields.Integer(string="#", compute="_compute_row_number", store=False)
    date_done = fields.Datetime("Fecha")
    production_id = fields.Many2one("mrp.production", string="MO", readonly=True)
    mo_name = fields.Char("MO")
    pedido_original = fields.Char("Pedido original")
    origin_ref = fields.Char("Origen")
    source_location_name = fields.Char("Desde")
    finished_product_id = fields.Many2one("product.product", string="Producto final", readonly=True)
    finished_product_code = fields.Char("Cod. final")
    finished_product_name = fields.Char("Producto final")
    finished_categ_id = fields.Many2one("product.category", string="Categoría final", readonly=True)
    raw_product_id = fields.Many2one("product.product", string="Materia prima", readonly=True)
    raw_product_code = fields.Char("Cod. MP")
    raw_product_name = fields.Char("Materia prima")
    raw_categ_id = fields.Many2one("product.category", string="Categoría MP", readonly=True)
    uom_id = fields.Many2one("uom.uom", string="UdM", readonly=True)
    qty_consumed = fields.Float("Consumido", digits=(16, 3))
    qty_scrap = fields.Float("Desecho", digits=(16, 3))
    qty_total = fields.Float("Total", digits=(16, 3), compute="_compute_qty_total", store=False)

    @api.depends("qty_consumed", "qty_scrap")
    def _compute_qty_total(self):
        for line in self:
            line.qty_total = float(line.qty_consumed or 0.0) + float(line.qty_scrap or 0.0)

    def _compute_row_number(self):
        for line in self:
            line.row_number = 0
        wizards = self.mapped("wizard_id")
        for wiz in wizards:
            ordered = self.search([("wizard_id", "=", wiz.id)], order=self._order)
            idx = 1
            for line in ordered:
                line.row_number = idx
                idx += 1

class MRPReportSalesNoStock(models.TransientModel):
    _name = "mrp.report.sales_no_stock"
    _description = "Reporte Ventas sin Stock"

    report_date = fields.Date(string="Fecha", default=fields.Date.context_today, required=True)
    size_filter = fields.Selection(
        [("all", "Todos"), ("small", "M pequeñas"), ("large", "M grandes")],
        string="Tamaño",
        default="all",
        required=True,
    )
    turns_small = fields.Selection([("1", "1"), ("2", "2"), ("3", "3")], string="Turnos M pequeñas", default="2", required=True)
    hours_per_turn_small = fields.Selection([("8", "8"), ("12", "12")], string="Horas por turno (M pequeñas)", default="8", required=True)
    turns_large = fields.Selection([("1", "1"), ("2", "2"), ("3", "3")], string="Turnos M grandes", default="2", required=True)
    hours_per_turn_large = fields.Selection([("8", "8"), ("12", "12")], string="Horas por turno (M grandes)", default="8", required=True)
    line_ids = fields.One2many("mrp.report.sales_no_stock.line", "wizard_id", string="Líneas")
    categ_ids = fields.Many2many("product.category", string="Categorías", required=False)

    def _check_turn_rules(self):
        _validate_turns(self.turns_small, self.hours_per_turn_small)
        _validate_turns(self.turns_large, self.hours_per_turn_large)

    def action_open_lines(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Ventas sin stock"),
            "res_model": "mrp.report.sales_no_stock.line",
            "view_mode": "tree",
            "domain": [("wizard_id", "=", self.id)],
        }

    def action_print(self):
        self.ensure_one()
        if not self.line_ids:
            self.action_generate()
        return self.env.ref("alterben_mrp_master_order.action_report_sales_no_stock_pdf").report_action(self)

    def action_generate(self):
        self.ensure_one()
        self._check_turn_rules()
        self.line_ids.unlink()

        Product = self.env["product.product"]
        SaleLine = self.env["sale.order.line"]
        date_end = _end_of_day(self.report_date)
        domain = [("order_id.state", "in", ["sale", "done"])]
        if date_end:
            domain.append(("order_id.date_order", "<=", fields.Datetime.to_string(date_end)))
        qty_net_field = "ab_qty_to_deliver_net" if "ab_qty_to_deliver_net" in SaleLine._fields else None
        qty_field = "qty_to_deliver" if "qty_to_deliver" in SaleLine._fields else None
        sales_detail_map = {}
        sale_lines = SaleLine.search(domain)

        if qty_net_field and getattr(SaleLine._fields[qty_net_field], "store", False):
            sales_map = {}
            for line in sale_lines:
                qty_to_deliver = float(getattr(line, qty_net_field, 0.0) or 0.0)
                if qty_to_deliver <= 0.0:
                    continue
                pid = line.product_id.id
                sales_map[pid] = sales_map.get(pid, 0.0) + qty_to_deliver
                sales_detail_map.setdefault(pid, []).append({
                    "sale_order": line.order_id.name,
                    "customer": line.order_id.partner_id.display_name if line.order_id.partner_id else "",
                    "qty": qty_to_deliver,
                    "order_date": fields.Datetime.to_string(line.order_id.date_order) if getattr(line.order_id, "date_order", False) else "",
                    "delivery_date": fields.Datetime.to_string(getattr(line.order_id, "commitment_date", False) or False) if getattr(line.order_id, "commitment_date", False) else "",
                })
            prio_map = {}
        elif qty_field and getattr(SaleLine._fields[qty_field], "store", False):
            sales_map = {}
            for line in sale_lines:
                qty_to_deliver = float(getattr(line, qty_field, 0.0) or 0.0)
                if qty_to_deliver <= 0.0:
                    continue
                pid = line.product_id.id
                sales_map[pid] = sales_map.get(pid, 0.0) + qty_to_deliver
                sales_detail_map.setdefault(pid, []).append({
                    "sale_order": line.order_id.name,
                    "customer": line.order_id.partner_id.display_name if line.order_id.partner_id else "",
                    "qty": qty_to_deliver,
                    "order_date": fields.Datetime.to_string(line.order_id.date_order) if getattr(line.order_id, "date_order", False) else "",
                    "delivery_date": fields.Datetime.to_string(getattr(line.order_id, "commitment_date", False) or False) if getattr(line.order_id, "commitment_date", False) else "",
                })
            prio_map = {}
        else:
            sales_map = {}
            prio_map = {}
            for line in sale_lines:
                qty_to_deliver = getattr(line, "ab_qty_to_deliver_net", False)
                if qty_to_deliver is False:
                    qty_to_deliver = (line.product_uom_qty or 0.0) - (line.qty_delivered or 0.0)
                if qty_to_deliver <= 0:
                    continue
                pid = line.product_id.id
                sales_map[pid] = sales_map.get(pid, 0.0) + qty_to_deliver
                sales_detail_map.setdefault(pid, []).append({
                    "sale_order": line.order_id.name,
                    "customer": line.order_id.partner_id.display_name if line.order_id.partner_id else "",
                    "qty": qty_to_deliver,
                    "order_date": fields.Datetime.to_string(line.order_id.date_order) if getattr(line.order_id, "date_order", False) else "",
                    "delivery_date": fields.Datetime.to_string(getattr(line.order_id, "commitment_date", False) or False) if getattr(line.order_id, "commitment_date", False) else "",
                })
            prio_map = {}

        lines = []
        in_process_summary = _get_in_process_summary(self.env, None)
        allowed_categ_ids = set()
        if self.categ_ids:
            allowed_categ_ids = set(self.env["product.category"].search([("id", "child_of", self.categ_ids.ids)]).ids)
            allowed_categ_ids.update(self.categ_ids.ids)
        for pid, qty in sales_map.items():
            product = Product.browse(pid)
            size = _get_category_size(product) if product else "other"
            if self.size_filter != "all" and size != self.size_filter:
                continue
            if allowed_categ_ids and product.categ_id:
                if product.categ_id.id not in allowed_categ_ids:
                    continue
            stock = product.qty_available or 0.0
            if qty <= stock:
                continue
            if qty > stock:
                priority_rank = 3
            elif qty == stock and qty > 0:
                priority_rank = 2
            elif qty > 0:
                priority_rank = 1
            else:
                priority_rank = 0
            code = (product.default_code or "").strip()
            suffix = _extract_suffix(code)
            in_process_data = in_process_summary.get(suffix) or {}
            in_process = (
                float(in_process_data.get("pt") or 0.0)
                + float(in_process_data.get("s1") or 0.0)
                + float(in_process_data.get("s2") or 0.0)
                + float(in_process_data.get("s3") or 0.0)
            )
            stock_breakdown = _get_internal_stock_breakdown(self.env, product)
            lines.append({
                "wizard_id": self.id,
                "product_id": pid,
                "product_code": product.default_code or "",
                "product_name": product.name or "",
                "stock_qty": stock,
                "sales_qty": qty,
                "priority_sales_qty": priority_rank,
                "shortfall_qty": max(0.0, qty - stock),
                "in_process_qty": in_process,
                "stock_detail_html": _format_sales_no_stock_stock_detail_html(product, stock_breakdown),
                "sales_detail_html": _format_sales_no_stock_sales_detail_html(product, sales_detail_map.get(pid, [])),
                "in_process_detail_html": _format_in_process_detail_html(in_process_data),
            })
        if lines:
            self.env["mrp.report.sales_no_stock.line"].create(lines)
        else:
            raise UserError(_("No hay datos para la fecha seleccionada."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Ventas sin stock"),
            "res_model": "mrp.report.sales_no_stock",
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
        }


class MRPReportSalesNoStockLine(models.TransientModel):
    _name = "mrp.report.sales_no_stock.line"
    _description = "Linea Ventas sin Stock"
    _order = "shortfall_qty desc"

    wizard_id = fields.Many2one("mrp.report.sales_no_stock", required=True, ondelete="cascade")
    row_number = fields.Integer(string="#", compute="_compute_row_number", store=False)
    product_id = fields.Many2one("product.product", string="Producto", required=True)
    product_code = fields.Char("Referencia")
    product_name = fields.Char("Nombre")
    uom_id = fields.Many2one("uom.uom", string="UdM", related="product_id.uom_id", store=False)
    size_category = fields.Selection(
        [("small", "M pequeñas"), ("large", "M grandes"), ("other", "Otros")],
        string="Tamaño",
        compute="_compute_size_category",
        store=False,
    )
    stock_qty = fields.Float("Existencia", digits=(16, 0))
    sales_qty = fields.Float("Ventas", digits=(16, 0))
    priority_sales_qty = fields.Float("Ventas prioridad", digits=(16, 0))
    shortfall_qty = fields.Float("Faltante", digits=(16, 0))
    in_process_qty = fields.Float("En proceso", digits=(16, 0))
    stock_detail_html = fields.Html("Detalle existencias", sanitize=False)
    sales_detail_html = fields.Html("Detalle ventas", sanitize=False)
    in_process_detail_html = fields.Html("Detalle en proceso", sanitize=False)

    @api.depends("product_id")
    def _compute_size_category(self):
        for line in self:
            line.size_category = _get_category_size(line.product_id) if line.product_id else False

    def _compute_row_number(self):
        for line in self:
            line.row_number = 0
        wizards = self.mapped("wizard_id")
        for wiz in wizards:
            ordered = self.search([("wizard_id", "=", wiz.id)], order=self._order)
            idx = 1
            for line in ordered:
                line.row_number = idx
                idx += 1

    def _open_detail_wizard(self, title, detail_html):
        self.ensure_one()
        wizard = self.env["mrp.report.in_process.detail.wizard"].sudo().create({
            "title": title,
            "detail_html": detail_html or "<div><p>Sin detalle disponible.</p></div>",
        })
        return {
            "type": "ir.actions.act_window",
            "name": title,
            "res_model": "mrp.report.in_process.detail.wizard",
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_open_stock_detail(self):
        return self._open_detail_wizard(
            _("Detalle de Existencias - %s") % (self.product_code or self.product_name or self.product_id.display_name),
            self.stock_detail_html,
        )

    def action_open_sales_detail(self):
        return self._open_detail_wizard(
            _("Detalle de Ventas - %s") % (self.product_code or self.product_name or self.product_id.display_name),
            self.sales_detail_html,
        )

    def action_open_in_process_detail(self):
        return self._open_detail_wizard(
            _("Detalle de En proceso - %s") % (self.product_code or self.product_name or self.product_id.display_name),
            self.in_process_detail_html,
        )

    def _compute_row_number(self):
        for line in self:
            line.row_number = 0
        wizards = self.mapped("wizard_id")
        for wiz in wizards:
            ordered = self.search([("wizard_id", "=", wiz.id)], order="id asc")
            idx = 1
            for line in ordered:
                line.row_number = idx
                idx += 1
