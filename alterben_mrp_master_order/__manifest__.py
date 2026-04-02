{'application': False,
    'assets': {'web.assets_backend': ['alterben_mrp_master_order/static/src/js/ct_ocr_text.js',
                                      'alterben_mrp_master_order/static/src/js/report_row_number_static.js',
                                      'alterben_mrp_master_order/static/src/js/plant_dashboard.js',
                                      'alterben_mrp_master_order/static/src/css/ct_dialog.css',
                                      'alterben_mrp_master_order/static/src/css/sale_forecast_hover.css',
                                      'alterben_mrp_master_order/static/src/css/plant_dashboard.css',
                                      'alterben_mrp_master_order/static/src/css/mrp_master_order.css',
                                      'alterben_mrp_master_order/static/src/css/ab_quality_tags.css',
                                      'alterben_mrp_master_order/static/src/js/mrp_master_order_line_footer_buttons.js',
                                      'alterben_mrp_master_order/static/src/js/novedades_hover_plain.js',
                                      'alterben_mrp_master_order/static/src/js/print_wizard_form.js',
                                      'alterben_mrp_master_order/static/src/xml/plant_dashboard.xml']},
 'author': 'Alterben S.A.',
 'category': 'Manufacturing',
    'data': [
    # 1. Security
    'security/ir.model.access.xml',
    'security/ir.model.access.csv',

    # 2. Data (non-view)
    'data/quality_reason_data.xml',
    'data/ir_cron.xml',

    # 3. All Views, Wizards, and Actions that define UI and actions
    'views/mrp_pedido_original_views.xml',
    'views/print_wizard_views.xml',
    'views/receta_pvb_cabina_move_views.xml',
    'views/control_total_label_views.xml',
    'views/res_config_settings_views.xml',
    'views/mrp_master_type_views.xml',
    'views/mrp_master_order_views.xml',
    'views/mrp_master_order_ct_views.xml',
    'views/quality_alert_views.xml',
    'views/stock_scrap_views.xml',
    'views/workorder_tree_novedades_button.xml',
    'wizard/assign_control_total_wizard_views.xml',
    'wizard/workorder_novedades_views.xml',
    'wizard/workorder_novedades_summary_views.xml',
    'wizard/scrap_confirm_wizard_views.xml',
    'wizard/pvb_cabina_inv_wizard_views.xml',
    'wizard/workorder_produce_wizard_views.xml',
    'wizard/add_open_mo_wizard_views.xml',
    'wizard/sale_forecast_detail_wizard_views.xml',
    'wizard/mrp_import_wizard_views.xml',
    'wizard/mrp_import_structural_wizard_views.xml',
    'wizard/mrp_import_result_wizard_views.xml',
    'wizard/mrp_master_confirm_wizard_views.xml',
    'wizard/opt_labels_wizard_views.xml',
    'wizard/stock_move_cancel_delivery_wizard_views.xml',
    'views/menuitems.xml',
    'views/opt_reports_views.xml',
    'views/pending_delivery_views.xml',
    'views/plant_dashboard_views.xml',
    'views/quality_scrap_report_views.xml',
    'views/sale_delivery_cancellation_views.xml',
    'views/pending_delivery_audit_views.xml',
    'views/stock_return_picking_views.xml',
    'views/sale_order_views.xml',
    'views/stock_picking_batch_views.xml',
    'actions/workorder_actions.xml',

    # 4. All Menus, loaded after the actions they depend on
    'views/recetas_pvb_views.xml',

    # 5. Reports
    'reports/report_curvado.xml',
    'reports/report_corte_pvb.xml',
    'reports/report_pvb_medidas_figura.xml',
    'reports/report_ensamblaje.xml',
    'reports/report_prevaciado.xml',
    'reports/report_inspeccion_final.xml',
    'reports/opt_reports.xml',
    'reports/report_opt_labels.xml',
    'reports/report_referencia_produccion.xml',
    'reports/report_picking_batch_custom.xml',
],
 'depends': ['account',
             'barcodes',
             'base_setup',
             'hr',
             'mail',
             'mrp',
             'mrp_workorder',
             'product',
             'quality_control',
             'sale_stock',
             'stock'],
 'description': '## 🧩 Alterben MRP Crilamyt (Master Order)\n'
                '### Módulo integral de gestión avanzada de producción, control operativo y calidad para CRILAMYT\n'
                '\n'
                'Este módulo unificado concentra en un solo paquete todas las funcionalidades desarrolladas para el '
                'MRP de CRILAMYT, integrando:\n'
                '\n'
                '1. **Orden Maestra de Producción (Master Order)**\n'
                '2. **Control Total de Despachos y Etiquetado de Productos**\n'
                '3. **Novedades del Workorder, Calidad y Desechos (Quality + Scrap)**\n'
                '\n'
                'El objetivo es centralizar en un único módulo toda la lógica operativa desarrollada por Alterben para '
                'soportar los procesos reales de manufactura de CRILAMYT, evitando dependencias distribuidas en varios '
                'módulos y mejorando la mantenibilidad del sistema.\n'
                '\n'
                '---\n'
                '\n'
                '### 1. Orden Maestra de Producción (MRP Master Order)\n'
                '\n'
                'Mecanismo central del módulo y funcionalidad principal utilizada por la planta de CRILAMYT.\n'
                '\n'
                '- Agrupa múltiples Órdenes de Fabricación en una sola **Orden Maestra**, consolidando la '
                'planificación productiva.\n'
                '- Cada línea de la Orden Maestra genera automáticamente una **Orden de Fabricación individual**.\n'
                '- Incluye una interfaz avanzada para revisar productos, cantidades, centros de trabajo, estados y '
                'prioridades.\n'
                '- Incorpora reportes especializados para centros clave (Corte, PVB, Curvado, Ensamblaje, '
                'Pre-vaciado), alineados con el flujo real de planta.\n'
                '- Se integra completamente con Inventario para reserva de materia prima y sincronización con '
                'pickings.\n'
                '- Incluye automatizaciones programadas vía `ir.cron` para tareas recurrentes.\n'
                '\n'
                '---\n'
                '\n'
                '### 2. Control Total de Despachos y Etiquetado de Productos\n'
                '\n'
                'Submódulo antes conocido como “Alterben Control Total (Despachos)”.\n'
                '\n'
                '- Gestiona **etiquetas secuenciales por unidad**, permitiendo identificar cada parabrisas/hoja de '
                'vidrio de forma única.\n'
                '- Muestra los rangos **Desde – Hasta** directamente en el picking, evitando revisar producto por '
                'producto.\n'
                '- Integra la información de etiquetas y rangos con:\n'
                '  - Órdenes de Venta\n'
                '  - Facturas de Cliente\n'
                '  - Documentos de Picking\n'
                '  - Reportes de facturación\n'
                '- Incluye wizards para asignación masiva de etiquetas, validación de rangos y prevención de '
                'solapamientos.\n'
                '- Ofrece opciones de configuración en Ajustes (Inventario/Ventas) para adaptar el comportamiento a la '
                'operación de CRILAMYT.\n'
                '\n'
                '---\n'
                '\n'
                '### 3. Novedades en Órdenes de Trabajo (Workorder + Calidad + Desechos)\n'
                '\n'
                'Submódulo antes conocido como “Alterben – Novedades en Orden de Trabajo (Calidad & Desechos)”.\n'
                '\n'
                '- Agrega botones y wizards de registro rápido de novedades directamente desde la Orden de Trabajo '
                '(workorder).\n'
                '- Permite registrar causas, incidencias, tiempos y problemas detectados en planta sin salir del flujo '
                'estándar de producción.\n'
                '- Se integra con el módulo nativo de **Calidad**, creando alertas de calidad asociadas a las órdenes '
                'involucradas.\n'
                '- Facilita el registro guiado de **Desechos (Scrap)**, enlazando causas, cantidades y productos a '
                'desechar.\n'
                '- Añade mejoras visuales (CSS/JS) para etiquetar y resaltar novedades y estados en la vista de '
                'workorders.\n'
                '- Incluye la causa adicional **"PRODUCTO CON FALLA (INSPECCION FINAL)"** para reflejar novedades '
                'detectadas en la etapa de inspección final.\n'
                '\n'
                '---\n'
                '\n'
                '### 4. Ventajas de la fusión en un solo módulo\n'
                '\n'
                '- Simplifica el mantenimiento al concentrar la funcionalidad en un único módulo técnico.\n'
                '- Evita la duplicación de vistas, modelos y reglas de seguridad.\n'
                '- Reduce dependencias internas entre módulos pequeños.\n'
                '- Asegura una operación consistente entre planificación, ejecución, calidad y logística.\n'
                '- Sirve como base estandarizada para extender el MRP de CRILAMYT con futuros desarrollos (dashboards, '
                'analítica, integraciones adicionales, etc.).\n'
                '\n'
                '---\n'
                '\n'
                '### 5. Alcance funcional global\n'
                '\n'
                'En conjunto, este módulo cubre:\n'
                '\n'
                '- Planificación avanzada de producción mediante Orden Maestra.\n'
                '- Gestión operativa detallada de Órdenes de Fabricación y Workorders.\n'
                '- Control de calidad y registro estructurado de novedades.\n'
                '- Administración completa de desechos (Scrap) con trazabilidad.\n'
                '- Etiquetado y control logístico de productos terminados.\n'
                '- Reportes operativos específicos para cada etapa del proceso productivo.\n'
                '\n'
                'Es el núcleo del sistema MRP de CRILAMYT y garantiza una operación ordenada, trazable y totalmente '
                'integrada con Inventario, Calidad y Ventas.',
 'installable': True,
 'license': 'LGPL-3',
 'name': 'Alterben MRP Crilamyt (Master Order)',
 'post_init_hook': 'post_init_hook',
 'pre_init_hook': 'pre_init_hook',
 'summary': 'Módulo unificado para CRILAMYT que extiende Orden Maestra con Control Total y Novedades de Workorder.',
 'version': '17.0.1.1.22',
 'website': 'https://alterben.ec'}
