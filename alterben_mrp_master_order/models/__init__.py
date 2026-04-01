# merged __init__ from: /mnt/data/merge_master_order_round2/alterben_control_total/alterben_control_total/models/__init__.py, /mnt/data/merge_master_order_round2/alterben_mrp_master_order/alterben_mrp_master_order/models/__init__.py, /mnt/data/merge_master_order_round2/alterben_mrp_workorder_novedades/alterben_mrp_workorder_novedades/models/__init__.py
from . import res_company
from . import res_config_settings
from . import control_total_label
from . import control_total_draft
from . import stock_move_line
from . import stock_move
from . import stock_picking

from . import ct_completion
from . import mrp_master_type
from . import master_parameters
from . import mrp_production
from . import mrp_master_order
from . import mrp_master_order_optA
from . import mrp_master_order_ct
from . import receta_pvb
from . import print_wizard
from . import report_curvado
from . import report_corte_pvb
from . import report_pvb_medidas_figura
from . import report_ensamblaje
from . import report_inspeccion_final
from . import report_opt_labels
from . import report_referencia_produccion
from . import report_picking_batch_custom
# -*- coding: utf-8 -*-
from . import mrp_workorder
from . import workorder_produce_wizard
from . import quality_alert_patch
from . import stock_scrap
from . import quality_tag_patch
from . import stock_return_picking
from . import sale_order
from . import sale_order_line
from . import sale_delivery_cancellation
from . import pending_delivery_audit
from . import sale_bucket_option

# Asegurar que los modelos se carguen correctamente
from .mrp_master_order_ct import MrpMasterOrderLineCT

from . import opt_reports
from . import quality_scrap_report
from . import plant_dashboard

