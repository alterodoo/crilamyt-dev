# -*- coding: utf-8 -*-
from odoo import SUPERUSER_ID, api
from lxml import etree

BUTTON_XML = """<button name="action_open_novedades_wizard"
        type="object"
        string="Novedades"
        class="oe_highlight"/>
""".strip()


def pre_init_hook(env):
    # Compatibility fixes for databases where core sale columns are missing
    # even though the server code expects them during registry bootstrap.
    compatibility_alters = [
        """
        ALTER TABLE sale_order_line
        ADD COLUMN IF NOT EXISTS has_displayed_warning_upsell boolean DEFAULT false
        """,
        """
        ALTER TABLE sale_order_line
        ADD COLUMN IF NOT EXISTS analytic_distribution jsonb
        """,
        """
        ALTER TABLE sale_order_line
        ADD COLUMN IF NOT EXISTS is_downpayment boolean DEFAULT false
        """,
        """
        ALTER TABLE sale_order_line
        ADD COLUMN IF NOT EXISTS x_bucket varchar
        """,
        """
        ALTER TABLE sale_order_line
        ADD COLUMN IF NOT EXISTS x_bucket_code varchar
        """,
        """
        ALTER TABLE sale_order_line
        ADD COLUMN IF NOT EXISTS x_bucket_token varchar
        """,
        """
        ALTER TABLE sale_order_line
        ADD COLUMN IF NOT EXISTS x_bucket_option_id integer
        """,
        """
        ALTER TABLE sale_order_line
        ADD COLUMN IF NOT EXISTS x_studio_base_discount double precision DEFAULT 0
        """,
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'stock_picking'
                  AND column_name = 'x_studio_bodegas'
                  AND data_type IN ('character varying', 'text')
            ) THEN
                ALTER TABLE stock_picking
                ALTER COLUMN x_studio_bodegas TYPE integer
                USING CASE
                    WHEN trim(coalesce(x_studio_bodegas, '')) ~ '^[0-9]+$'
                        THEN trim(x_studio_bodegas)::integer
                    ELSE NULL
                END;
            END IF;
        END
        $$;
        """,
        """
        ALTER TABLE stock_move
        ADD COLUMN IF NOT EXISTS ab_sale_order_id integer
        """,
        """
        ALTER TABLE stock_move
        ADD COLUMN IF NOT EXISTS ab_customer_id integer
        """,
        """
        ALTER TABLE stock_move
        ADD COLUMN IF NOT EXISTS ab_customer_city varchar
        """,
        """
        ALTER TABLE stock_move
        ADD COLUMN IF NOT EXISTS ab_batch_id integer
        """,
        """
        ALTER TABLE stock_move
        ADD COLUMN IF NOT EXISTS ab_picking_scheduled_date timestamp without time zone
        """,
        """
        ALTER TABLE stock_move
        ADD COLUMN IF NOT EXISTS ab_sale_commitment_date timestamp without time zone
        """,
        """
        ALTER TABLE stock_move
        ADD COLUMN IF NOT EXISTS ab_delivery_date_manual timestamp without time zone
        """,
        """
        ALTER TABLE stock_move
        ADD COLUMN IF NOT EXISTS ab_delivery_date timestamp without time zone
        """,
        """
        ALTER TABLE stock_move
        ADD COLUMN IF NOT EXISTS ab_dispatch_status varchar
        """,
        """
        ALTER TABLE stock_move
        ADD COLUMN IF NOT EXISTS ab_reservation_status varchar
        """,
    ]
    for statement in compatibility_alters:
        env.cr.execute(statement)

def _insert_button_in_view(env, view):
    try:
        arch = etree.fromstring(view.arch_db.encode('utf-8'))
    except Exception:
        return False
    if arch.xpath(".//button[@name='action_open_novedades_wizard']"):
        return False
    inserted = False
    headers = arch.xpath('.//header')
    if headers:
        try:
            btn_el = etree.fromstring(BUTTON_XML)
            headers[0].insert(0, btn_el)
            inserted = True
        except Exception:
            inserted = False
    if not inserted:
        sheets = arch.xpath('.//sheet')
        if sheets:
            try:
                btn_el = etree.fromstring(BUTTON_XML)
                container = etree.Element('div'); container.attrib['class'] = 'oe_button_box'
                container.append(btn_el)
                sheets[0].insert(0, container)
                inserted = True
            except Exception:
                inserted = False
    if inserted:
        view.write({'arch_db': etree.tostring(arch, encoding='unicode')})
    return inserted

def post_init_hook(env):
    if env.uid != SUPERUSER_ID:
        env = env(user=SUPERUSER_ID)
    env.cr.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'stock_picking_x_studio_bodegas_fkey'
            ) THEN
                ALTER TABLE stock_picking
                DROP CONSTRAINT stock_picking_x_studio_bodegas_fkey;
            END IF;

            IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'stock_picking'
                  AND column_name = 'x_studio_bodegas'
            ) THEN
                UPDATE stock_picking sp
                SET x_studio_bodegas = NULL
                WHERE sp.x_studio_bodegas IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1
                      FROM alterben_bucket_option abo
                      WHERE abo.id = sp.x_studio_bodegas
                  );

                ALTER TABLE stock_picking
                ADD CONSTRAINT stock_picking_x_studio_bodegas_fkey
                FOREIGN KEY (x_studio_bodegas)
                REFERENCES alterben_bucket_option (id)
                ON DELETE SET NULL;
            END IF;
        END
        $$;
    """)
    View = env['ir.ui.view']
    views = View.search([('model', '=', 'mrp.workorder'), ('type', '=', 'form')], order='priority,id')
    for v in views:
        if _insert_button_in_view(env, v):
            break
