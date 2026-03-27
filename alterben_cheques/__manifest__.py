{
    'name': 'Alterben Cheques Posfechados',
    'version': '17.0.1.0.0',
    'summary': 'Cheques posfechados + exportación Excel para importar pagos de clientes',
    'description': '''Módulo de control de Cheques Posfechados para Odoo 17.

- Menú en Contabilidad / Clientes / Cheques Posfechados
- Estados: Borrador, Por Depositar, Depositado, Depósito Confirmado, Devuelto, Cancelado
- Botones: Confirmar, Depositar, Confirmar Depósito, Devolver Cheque, Reestablecer a Borrador, Cancelar
- Acción masiva en la vista de lista para Depositar Cheques
- Seguimiento en el chatter de quién hizo cada cambio y cuándo
- Sin asientos contables ni integración contable directa
- Exportación a Excel (XLSX) para importar Pagos de clientes (account.payment) en Odoo 17
''',
    'author': 'Alterben S.A.',
    'website': 'https://www.alterben.com',
    'license': 'LGPL-3',
    'category': 'Accounting',
    'depends': ['base', 'account', 'mail'],
    'data': [
        'security/ir.model.access.csv',
        'data/sequence.xml',
        'views/posdated_check_export_wizard_views.xml',
        'views/cheque_views.xml',
        'views/cheque_actions.xml',
        'views/cheque_menus.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}