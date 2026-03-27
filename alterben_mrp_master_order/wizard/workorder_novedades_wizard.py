# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class WorkorderNovedadesWizard(models.TransientModel):
    _name = 'alterben.workorder.novedades.wizard'
    _description = 'Registrar Novedades de Calidad y Desechos (WO)'

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        res['producto_desecho_tipo'] = 'mp'
        m2o_fields = (
            'workorder_id',
            'production_id',
            'workcenter_id',
            'quality_team_id',
            'product_to_scrap_id',
            'location_id',
            'scrap_location_id',
        )
        for fname in m2o_fields:
            if fname not in res:
                continue
            val = res.get(fname)
            if isinstance(val, models.BaseModel):
                res[fname] = val.id or False
            elif val and not isinstance(val, int):
                res[fname] = False
        production = False
        if res.get('production_id'):
            production = self.env['mrp.production'].browse(res['production_id']).exists()
        elif self.env.context.get('default_workorder_id'):
            workorder = self.env['mrp.workorder'].browse(self.env.context['default_workorder_id']).exists()
            production = workorder.production_id.exists() if workorder else False
            if production and not res.get('production_id'):
                res['production_id'] = production.id
        if res.get('producto_desecho_tipo') == 'mp' and production:
            res['scrap_line_ids'] = self._prepare_default_scrap_lines_from_mo(production)
        return res

    def _get_raw_component_rows(self, mo):
        rows = []
        seen = set()
        mo_qty_total = float(getattr(mo, 'product_qty', 0.0) or 0.0) or 1.0
        raw_moves = mo.move_raw_ids.filtered(lambda m: m.product_id and m.state != 'cancel')
        for move in raw_moves:
            product = move.product_id.exists()
            if not product or product.id in seen:
                continue
            seen.add(product.id)
            mo_qty = float(move.product_uom_qty or 0.0)
            unit_qty = mo_qty / mo_qty_total if mo_qty_total else 0.0
            rows.append((product, mo_qty, unit_qty))
        if rows:
            return rows

        bom = mo.bom_id
        if not bom and hasattr(self.env['mrp.production'], '_bom_find'):
            bom = self.env['mrp.production']._bom_find(product=mo.product_id, company_id=mo.company_id.id)
        if not bom:
            return rows

        factor = 1.0
        bom_qty = bom.product_qty or 1.0
        if bom_qty:
            factor = (mo.product_qty or 0.0) / bom_qty if (mo.product_qty or 0.0) else 1.0
        for bom_line in bom.bom_line_ids.filtered(lambda l: l.product_id):
            product = bom_line.product_id.exists()
            if not product or product.id in seen:
                continue
            seen.add(product.id)
            unit_qty = float(bom_line.product_qty or 0.0)
            rows.append((product, unit_qty * factor, unit_qty))
        return rows

    def _prepare_default_scrap_lines_from_mo(self, mo):
        commands = [(5, 0, 0)]
        base_qty = 1.0
        for product, mo_qty, unit_qty in self._get_raw_component_rows(mo):
            commands.append((0, 0, {
                'product_id': product.id,
                'unit_qty': unit_qty,
                'qty_scrap': unit_qty * base_qty,
                'mo_qty': mo_qty,
            }))
        return commands

    def _sync_scrap_lines_from_mo(self):
        for wizard in self:
            if wizard.producto_desecho_tipo != 'mp':
                continue
            mo = wizard.production_id.exists() if wizard.production_id else False
            if not mo and wizard.workorder_id:
                mo = wizard.workorder_id.production_id.exists()
            if not mo:
                wizard.scrap_line_ids = [(5, 0, 0)]
                continue
            existing_qty = {
                line.product_id.id: (line.qty_scrap or 0.0)
                for line in wizard.scrap_line_ids.filtered(lambda l: l.product_id)
            }
            commands = [(5, 0, 0)]
            base_qty = float(wizard.qty_scrap or 0.0)
            for product, mo_qty, unit_qty in wizard._get_raw_component_rows(mo):
                commands.append((0, 0, {
                    'product_id': product.id,
                    'unit_qty': unit_qty,
                    'qty_scrap': existing_qty.get(product.id, unit_qty * base_qty),
                    'mo_qty': mo_qty,
                }))
            wizard.scrap_line_ids = commands

    @api.onchange('qty_scrap')
    def _onchange_base_qty_scrap(self):
        for wizard in self:
            if wizard.producto_desecho_tipo != 'mp':
                continue
            base_qty = float(wizard.qty_scrap or 0.0)
            for line in wizard.scrap_line_ids:
                line.qty_scrap = (line.unit_qty or 0.0) * base_qty

    def _sanitize_m2o(self):
        for wizard in self:
            for fname in (
                'workorder_id',
                'production_id',
                'product_finished_id',
                'workcenter_id',
                'quality_team_id',
                'product_to_scrap_id',
                'uom_id',
                'location_id',
                'scrap_location_id',
            ):
                val = wizard[fname]
                if isinstance(val, models.BaseModel):
                    if not val.exists():
                        wizard[fname] = False
                    continue
                if val is not False and val is not None:
                    wizard[fname] = False

    def read(self, fields=None, load='_classic_read'):
        self._sanitize_m2o()
        return super().read(fields=fields, load=load)

    workorder_id = fields.Many2one('mrp.workorder', string='Orden de trabajo', required=True)
    production_id = fields.Many2one('mrp.production', string='Orden de fabricacion', required=True)
    product_finished_id = fields.Many2one('product.product', string='Producto final', compute='_compute_product_finished', store=False)

    workcenter_id = fields.Many2one('mrp.workcenter', string='Centro de trabajo', required=True)

    create_alert = fields.Boolean(string='Crear alerta de calidad', default=True)
    alert_name = fields.Char(string='Titulo de alerta', default='Novedad en operacion')
    quality_team_id = fields.Many2one(
        'quality.alert.team',
        string='Equipo de calidad',
        default=lambda self: self.env['quality.alert.team'].search([('name', '=', 'Produccion')], limit=1).id,
    )
    quality_main_cause_id = fields.Many2one('quality.reason', string='Causa principal')
    quality_tag_ids = fields.Many2many('quality.tag', string='Etiquetas')
    description = fields.Text(string='Descripcion / Observaciones')

    producto_desecho_tipo = fields.Selection([
        ('mp', 'Materia prima (insumo de la MO)'),
        ('pf', 'Producto terminado'),
    ], string='Que desea desechar', default='pf')
    create_scrap = fields.Boolean(string='Crear desecho (scrap)', default=False)

    product_to_scrap_id = fields.Many2one('product.product', string='Producto a desechar')
    allowed_product_ids = fields.Many2many('product.product', string='Productos permitidos', compute='_compute_allowed_products', store=False)
    scrap_line_ids = fields.One2many(
        'alterben.workorder.novedades.scrap.line',
        'wizard_id',
        string='Lineas de desecho',
    )
    qty_scrap = fields.Float(string='Cantidad base a desechar', default=1.0)
    uom_id = fields.Many2one('uom.uom', string='UdM', compute='_compute_uom', store=False)
    location_id = fields.Many2one(
        'stock.location',
        string='Ubicacion de origen',
        default=lambda self: self.env['stock.location'].search([('complete_name', '=', 'WH/PREPRODUCCION')], limit=1).id,
    )
    scrap_location_id = fields.Many2one(
        'stock.location',
        string='Ubicacion de desecho',
        domain=[('scrap_location', '=', True)],
        default=lambda self: self.env['stock.location'].search([('complete_name', '=', 'Virtual Locations/Desecho Producción')], limit=1).id,
    )

    @api.depends('production_id', 'producto_desecho_tipo', 'workorder_id')
    def _compute_allowed_products(self):
        for wizard in self:
            products = self.env['product.product'].browse()
            mo = wizard.production_id
            if isinstance(mo, models.BaseModel):
                mo = mo.exists()
            else:
                mo = False
            if not mo:
                wo = wizard.workorder_id
                if isinstance(wo, models.BaseModel):
                    wo = wo.exists()
                else:
                    wo = False
                mo = wo.production_id.exists() if wo else False
            if mo:
                if wizard.producto_desecho_tipo == 'pf':
                    if isinstance(mo.product_id, models.BaseModel):
                        products |= mo.product_id.exists()
                else:
                    raw_moves = mo.move_raw_ids.filtered(lambda m: m.product_id and m.state != 'cancel')
                    products |= raw_moves.mapped('product_id').exists()
                    if not products and mo.bom_id:
                        products |= mo.bom_id.bom_line_ids.mapped('product_id').exists()
            wizard.allowed_product_ids = products

    @api.depends('production_id')
    def _compute_product_finished(self):
        for wizard in self:
            production = wizard.production_id
            if not isinstance(production, models.BaseModel):
                wizard.product_finished_id = False
                continue
            production = production.exists()
            wizard.product_finished_id = production.product_id.exists() if production and production.product_id else False

    @api.onchange('producto_desecho_tipo', 'production_id')
    def _onchange_product_to_scrap_domain(self):
        self._sanitize_m2o()
        if self.producto_desecho_tipo == 'pf':
            self.scrap_line_ids = [(5, 0, 0)]
        else:
            self._sync_scrap_lines_from_mo()
        return {'domain': {'product_to_scrap_id': [('id', 'in', self.allowed_product_ids.ids)]}}

    @api.onchange('workorder_id', 'production_id', 'product_to_scrap_id')
    def _onchange_sanitize(self):
        self._sanitize_m2o()
        if self.producto_desecho_tipo == 'mp':
            self._sync_scrap_lines_from_mo()

    @api.onchange('quality_tag_ids')
    def _onchange_quality_tag_ids_autoscrap(self):
        if self._should_auto_enable_scrap():
            self.create_scrap = True

    @api.onchange('quality_main_cause_id')
    def _onchange_quality_main_cause_autoscrap(self):
        if self._should_auto_enable_scrap():
            self.create_scrap = True

    @api.depends('product_to_scrap_id', 'production_id', 'producto_desecho_tipo')
    def _compute_uom(self):
        for wizard in self:
            prod = wizard.product_to_scrap_id
            if isinstance(prod, models.BaseModel):
                prod = prod.exists()
            else:
                prod = False
            if not prod and wizard.producto_desecho_tipo == 'pf':
                production = wizard.production_id.exists() if wizard.production_id else False
                prod = production.product_id.exists() if production and production.product_id else False
            wizard.uom_id = prod.uom_id.exists() if prod and prod.uom_id else False

    def _normalize_text_for_scrap_trigger(self, value):
        text = (value or '').strip().lower()
        replacements = {
            'á': 'a',
            'é': 'e',
            'í': 'i',
            'ó': 'o',
            'ú': 'u',
        }
        for source, target in replacements.items():
            text = text.replace(source, target)
        return text

    def _should_auto_enable_scrap(self):
        self.ensure_one()
        tags = self.quality_tag_ids or self.env['quality.tag']
        if any(getattr(tag, 'x_studio_perdida_total', False) for tag in tags):
            return True
        for tag in tags:
            normalized_name = self._normalize_text_for_scrap_trigger(tag.name)
            if 'roto' in normalized_name or 'rotura' in normalized_name:
                return True
        cause_name = self._normalize_text_for_scrap_trigger(self.quality_main_cause_id.name)
        return 'roto' in cause_name or 'rotura' in cause_name

    def _get_scrap_entries(self, mo):
        self.ensure_one()
        entries = []
        if self.producto_desecho_tipo == 'mp':
            for line in self.scrap_line_ids.filtered(lambda l: l.product_id and l.qty_scrap > 0):
                entries.append((line.product_id, line.qty_scrap))
            if not entries and self.product_to_scrap_id and self.qty_scrap > 0:
                entries.append((self.product_to_scrap_id, self.qty_scrap))
            return entries
        product = self.product_to_scrap_id or (mo and mo.product_id) or False
        if product and self.qty_scrap > 0:
            entries.append((product, self.qty_scrap))
        return entries

    def _build_scrap_vals(self, product, qty, mo, wo):
        vals = {
            'product_id': product.id,
            'scrap_qty': qty,
            'product_uom_id': product.uom_id.id,
            'origin': '%s - %s' % ((mo.name or '') if mo else '', (wo.name or '') if wo else ''),
            'location_id': self.location_id.id if self.location_id else self.env['stock.location'].search([('complete_name', '=', 'WH/PREPRODUCCION')], limit=1).id,
            'scrap_location_id': self.scrap_location_id.id if self.scrap_location_id else self.env['stock.location'].search([('complete_name', '=', 'Virtual Locations/Desecho Producción')], limit=1).id,
        }
        for fname, value in [('production_id', mo.id if mo else False), ('workorder_id', wo.id if wo else False)]:
            if fname in self.env['stock.scrap']._fields and value:
                vals[fname] = value
        if 'x_studio_origen' in self.env['stock.scrap']._fields and mo:
            vals['x_studio_origen'] = mo.origin or ''
        if 'x_studio_pedido_original' in self.env['stock.scrap']._fields and mo and hasattr(mo, 'x_studio_pedido_original'):
            vals['x_studio_pedido_original'] = mo.x_studio_pedido_original
        return vals

    def _action_confirm_internal(self, force_create_scrap=None):
        if self.create_alert and not self.quality_tag_ids:
            raise UserError(_('Debe seleccionar al menos una etiqueta de calidad para crear la alerta.'))

        self.ensure_one()
        mo = self.production_id
        wo = self.workorder_id
        create_scrap_flag = self.create_scrap if force_create_scrap is None else bool(force_create_scrap)
        scrap_entries = self._get_scrap_entries(mo)

        if create_scrap_flag:
            if not self.quality_tag_ids:
                raise UserError(_('Debe seleccionar al menos una etiqueta.'))
            if not scrap_entries:
                raise UserError(_('Seleccione el producto a desechar.'))
            if any(qty <= 0 for _, qty in scrap_entries):
                raise UserError(_('La cantidad a desechar debe ser mayor que cero.'))
            if not self.location_id or not self.scrap_location_id:
                raise UserError(_('Debe indicar ubicaciones de origen y desecho.'))

        if self.create_alert:
            vals = {
                'team_id': self.quality_team_id.id if self.quality_team_id else False,
                'description': self.description or '',
            }
            if 'product_id' in self.env['quality.alert']._fields and mo and mo.product_id:
                vals['product_id'] = mo.product_id.id
            if 'workorder_id' in self.env['quality.alert']._fields and wo:
                vals['workorder_id'] = wo.id
            if 'production_id' in self.env['quality.alert']._fields and mo:
                vals['production_id'] = mo.id
            if 'root_cause_id' in self.env['quality.alert']._fields and self.quality_main_cause_id:
                vals['root_cause_id'] = self.quality_main_cause_id.id
            alert = self.env['quality.alert'].with_context(
                from_wo_novedades=True,
                mo_id=(mo.id if mo else False),
                wo_id=(wo.id if wo else False),
            ).create(vals)
            if self.quality_tag_ids and 'tag_ids' in self.env['quality.alert']._fields:
                alert.write({'tag_ids': [(6, 0, self.quality_tag_ids.ids)]})

        flag_ok = True
        perdida_field = 'x_studio_perdida_total'
        if wo and perdida_field in wo._fields:
            flag_ok = bool(wo[perdida_field])
        elif mo and perdida_field in mo._fields:
            flag_ok = bool(mo[perdida_field])

        if create_scrap_flag:
            if not flag_ok:
                raise UserError(_('No esta habilitado el desecho para esta orden (active la opcion de perdida total).'))
            for product, qty in scrap_entries:
                scrap = self.env['stock.scrap'].create(self._build_scrap_vals(product, qty, mo, wo))
                if getattr(scrap, 'state', '') == 'draft':
                    scrap.action_validate()

        if create_scrap_flag:
            if not self.quality_tag_ids:
                raise UserError(_('Debe seleccionar al menos una etiqueta.'))
            if not scrap_entries:
                raise UserError(_('Seleccione el producto a desechar.'))
            if any(qty <= 0 for _, qty in scrap_entries):
                raise UserError(_('La cantidad a desechar debe ser mayor que cero.'))
        return {'type': 'ir.actions.act_window_close'}

    def action_confirm(self):
        self.ensure_one()
        scrap_entries = self._get_scrap_entries(self.production_id)
        if scrap_entries and not self.create_scrap:
            if len(scrap_entries) == 1:
                msg = _('¿Desea desechar el producto %s?\nUd ha seleccionado un producto a desechar pero no activó la opción de desechar.') % (scrap_entries[0][0].display_name,)
            else:
                msg = _('¿Desea crear los desechos seleccionados?\nUd ha registrado productos a desechar pero no activó la opción de desechar.')
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'alterben.workorder.scrap.confirm.wizard',
                'view_mode': 'form',
                'target': 'new',
                'context': {
                    'default_message': msg,
                    'active_novedades_id': self.id,
                }
            }
        return self._action_confirm_internal()


class WorkorderNovedadesScrapLine(models.TransientModel):
    _name = 'alterben.workorder.novedades.scrap.line'
    _description = 'Linea de desecho en novedades'

    wizard_id = fields.Many2one('alterben.workorder.novedades.wizard', required=True, ondelete='cascade')
    allowed_product_ids = fields.Many2many(
        'product.product',
        string='Productos permitidos',
        compute='_compute_allowed_product_ids',
        store=False,
    )
    product_id = fields.Many2one('product.product', string='Producto', required=True)
    unit_qty = fields.Float(string='Cant. unitaria', readonly=True)
    mo_qty = fields.Float(string='Cant. MO', readonly=True)
    qty_scrap = fields.Float(string='Cantidad', default=1.0, required=True)
    uom_id = fields.Many2one('uom.uom', string='UdM', related='product_id.uom_id', store=False)

    @api.depends('wizard_id.allowed_product_ids')
    def _compute_allowed_product_ids(self):
        for line in self:
            line.allowed_product_ids = line.wizard_id.allowed_product_ids
