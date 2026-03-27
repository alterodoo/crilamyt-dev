
{
    'name': 'Alterben Reportes - Ret. Informativa en ATS (FIX v3)',
    'version': '17.0.1.0.5',
    'depends': ['account', 'sale', 'mrp', 'stock_account', 'l10n_ec_gh'],
    'data': [
        # Modelos primero
        'views/l10n_ec_invoices_report_view.xml',
        'views/mrp_production_view.xml',
        'views/sale_report_view.xml',
    ],
    'auto_install': False,
    'installable': True,
    'application': False,
}
